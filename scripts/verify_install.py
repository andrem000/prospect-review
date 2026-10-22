"""Install a built wheel into a fresh venv and verify it outside the checkout."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import venv
import zipfile


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--wheel-dir', type=Path, required=True)
    args = parser.parse_args()
    wheels = sorted(args.wheel_dir.resolve().glob('prospect_review-*.whl'))
    if len(wheels) != 1:
        parser.error('Expected exactly one prospect_review wheel in --wheel-dir')
    wheel = wheels[0]
    with zipfile.ZipFile(wheel) as archive:
        metadata_names = [name for name in archive.namelist() if name.endswith('.dist-info/METADATA')]
        if len(metadata_names) != 1:
            raise RuntimeError('Wheel must contain one metadata record')
        metadata = archive.read(metadata_names[0]).decode('utf-8')
        version = next(line.split(': ', 1)[1] for line in metadata.splitlines() if line.startswith('Version: '))
        requirements = [line for line in metadata.splitlines() if line.startswith('Requires-Dist:')]
        if requirements:
            raise RuntimeError('Expected the runtime package to have no third-party dependencies')
    with tempfile.TemporaryDirectory(prefix='prospect-review-install-') as directory:
        root = Path(directory).resolve()
        environment = root / 'venv'
        outside = root / 'outside-checkout'
        outside.mkdir()
        venv.EnvBuilder(with_pip=True).create(environment)
        binaries = environment / ('Scripts' if os.name == 'nt' else 'bin')
        python = binaries / ('python.exe' if os.name == 'nt' else 'python')
        cli = binaries / ('prospect-review.exe' if os.name == 'nt' else 'prospect-review')
        alias = binaries / ('prospect.exe' if os.name == 'nt' else 'prospect')
        env = dict(os.environ, PYTHONDONTWRITEBYTECODE='1', PYTHONNOUSERSITE='1', PIP_DISABLE_PIP_VERSION_CHECK='1')
        env.pop('PYTHONPATH', None)
        env.pop('PYTHONHOME', None)

        def run(*command, expected=0):
            result = subprocess.run(list(map(str, command)), cwd=outside, env=env, text=True, capture_output=True)
            if result.returncode != expected:
                raise RuntimeError(f'Command failed ({result.returncode}): {result.stdout}\n{result.stderr}')
            return result.stdout

        run(python, '-m', 'pip', 'install', '--no-index', '--no-deps', wheel)
        location_check = "import importlib.metadata,json,pathlib,prospect,site,sys; p=pathlib.Path(prospect.__file__).resolve(); r=pathlib.Path(sys.prefix).resolve(); assert sys.prefix != sys.base_prefix; assert p.is_relative_to(r); assert any(p.is_relative_to(pathlib.Path(s).resolve()) for s in site.getsitepackages()); print(json.dumps({'version':importlib.metadata.version('prospect-review'),'import_in_venv_site_packages':True,'python':sys.version.split()[0]}))"
        location = json.loads(run(python, '-I', '-c', location_check))
        assert location['version'] == version
        expected_version = 'Prospect Review ' + version
        assert run(cli, '--version').strip() == expected_version
        assert run(alias, '--version').strip() == expected_version
        assert 'demo' in run(cli, '--help')
        summary = json.loads(run(cli, 'demo', '--out', 'installed-demo'))
        report_path = outside / 'installed-demo' / 'report.json'
        report = json.loads(report_path.read_text(encoding='utf-8'))
        assert summary['checks_passed'] == 12 and all(report['checks'].values())
        assert report['synthetic_only'] and report['network_requests'] == 0
        before = report_path.read_bytes()
        run(cli, 'demo', '--out', 'installed-demo', expected=2)
        assert report_path.read_bytes() == before
        module_demo = json.loads(run(python, '-I', '-m', 'prospect', 'demo', '--out', 'module-demo'))
        alias_demo = json.loads(run(alias, 'demo', '--out', 'alias-demo'))
        assert module_demo == summary == alias_demo

        # Exercise the documented review/correction workflow through the installed
        # command, with bundled fictional inputs and an intentional historical clock.
        extract = "from importlib import resources; from pathlib import Path; d=resources.files('prospect').joinpath('data'); p=Path('fixtures'); p.mkdir(); [(p/n).write_bytes(d.joinpath(n).read_bytes()) for n in ('businesses.csv','rights.synthetic.json','suppression.csv','config.example.json')]"
        run(python, '-I', '-c', extract)
        example = outside / 'review-example'
        example.mkdir()
        database = example / 'review.sqlite3'
        run(cli, '--db', database, 'import', 'fixtures/businesses.csv', '--rights', 'fixtures/rights.synthetic.json')
        run(cli, '--db', database, 'suppress', 'fixtures/suppression.csv')
        initial = json.loads(run(cli, '--db', database, 'show', '1'))
        assert initial['name'] == 'Cedar Example Plumbing'
        run(cli, '--db', database, 'review', '1', '--website-status', 'no_site_found',
            '--activity', 'active', '--ownership', 'independent', '--residential', 'yes',
            '--evidence-observed-at', '2026-10-15', '--evidence-url', 'https://example.com/fictional-check',
            '--notes', 'SIMULATED: fictional website, activity and scope check; no real business reviewed.')
        config = json.loads((outside / 'fixtures/config.example.json').read_text(encoding='utf-8'))
        config['as_of'] = '2026-10-22'
        for days, expected_qualified in ((180, True), (1, False)):
            config['freshness_days'] = days
            config_path = example / ('freshness-' + str(days) + '.json')
            config_path.write_text(json.dumps(config), encoding='utf-8')
            result = json.loads(run(cli, '--db', database, 'search', '--config', config_path))
            cedar = next(row for row in result['candidates'] if row['id'] == 1)
            assert cedar['qualified'] is expected_qualified
        run(cli, '--db', database, 'review', '1', '--website-status', 'uncertain',
            '--candidate-website', 'https://possible.example.com', '--evidence-observed-at', '2026-10-16',
            '--evidence-url', 'https://example.com/fictional-correction',
            '--notes', 'SIMULATED: possible website needs checking; no new activity check.')
        corrected = json.loads(run(cli, '--db', database, 'show', '1'))
        saved = corrected['review']
        assert len(corrected['reviews']) == 2
        assert saved['ownership'] == 'independent' and saved['residential'] == 'yes'
        assert saved['activity'] == 'active' and saved['evidence_observed_at'] == '2026-10-16'
        assert saved['activity_evidence_observed_at'] == '2026-10-15'
        run(cli, '--db', database, 'export', '--config', example / 'freshness-180.json', '--out', example / 'queue.json')
        assert (example / 'queue.json.provenance.json').exists()
        print(json.dumps({'wheel': wheel.name, 'version': version, 'python': location['python'],
                          'import_in_isolated_venv': True, 'outside_checkout': True,
                          'installed_cli': True, 'module_cli': True, 'compatibility_alias': True,
                          'demo_checks': summary['checks_passed'], 'existing_demo_preserved': True,
                          'documented_review_example': True, 'configured_freshness_changes_qualification': True,
                          'partial_correction_preserves_decisions_and_activity_date': True,
                          'runtime_dependencies': 0}, indent=2))


if __name__ == '__main__':
    main()
