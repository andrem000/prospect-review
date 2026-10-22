"""Public-release behavior: safe exports, bundled data and CLI versioning."""
from contextlib import redirect_stdout
from importlib import resources
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from prospect import __version__, core
from prospect.demo import run

ROOT = Path(__file__).resolve().parents[1]


class ExportSafetyTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name)
        self.database = self.path / 'database.json'
        self.db = core.connect(self.database)
        self.addCleanup(self.db.close)
        core.import_data(self.db, ROOT / 'fixtures/businesses.csv', 'canonical', ROOT / 'fixtures/rights.synthetic.json')
        self.rows, _ = core.search(self.db, json.loads((ROOT / 'config.example.json').read_text()))

    def test_existing_export_is_unchanged_without_creating_sidecar(self):
        for extension in ('.csv', '.json'):
            target = self.path / ('existing' + extension)
            target.write_bytes(b'preserve the previous export')
            with self.assertRaisesRegex(ValueError, 'fresh'):
                core.export(self.db, self.rows, target)
            self.assertEqual(target.read_bytes(), b'preserve the previous export')
            self.assertFalse(target.with_suffix(extension + '.provenance.json').exists())

    def test_existing_provenance_preserved_without_creating_export(self):
        target = self.path / 'new.json'
        notice = target.with_suffix('.json.provenance.json')
        notice.write_bytes(b'preserve source rights')
        with self.assertRaisesRegex(ValueError, 'fresh'):
            core.export(self.db, self.rows, target)
        self.assertEqual(notice.read_bytes(), b'preserve source rights')
        self.assertFalse(target.exists())

    def test_database_named_json_cannot_be_export_destination(self):
        before = self.database.read_bytes()
        with self.assertRaisesRegex(ValueError, 'protected'):
            core.export(self.db, self.rows, self.database)
        self.assertEqual(self.database.read_bytes(), before)
        self.assertEqual(self.db.execute('PRAGMA integrity_check').fetchone()[0], 'ok')

    def test_cli_rejects_input_collision_before_mutating_database(self):
        config = self.path / 'config.json'
        config.write_bytes((ROOT / 'config.example.json').read_bytes())
        before = {path: path.read_bytes() for path in (self.database, config)}
        result = subprocess.run([sys.executable, '-m', 'prospect', '--db', str(self.database),
                                 'export', '--config', str(config), '--out', str(config)],
                                cwd=ROOT, capture_output=True, text=True)
        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertIn('protected', result.stderr)
        for path, content in before.items():
            self.assertEqual(path.read_bytes(), content)

    def test_new_destination_and_sidecar_are_complete(self):
        target = self.path / 'nested' / 'fresh.json'
        result = core.export(self.db, self.rows, target)
        self.assertEqual(result['exported'], len(self.rows))
        self.assertEqual(len(json.loads(target.read_text())), len(self.rows))
        self.assertEqual(json.loads(target.with_suffix('.json.provenance.json').read_text())['sources'][0]['license'], 'synthetic')

    def test_racing_sidecar_is_preserved_and_reserved_output_removed(self):
        target = self.path / 'race.json'
        notice = target.with_suffix('.json.provenance.json')
        original_open = Path.open

        def racing_open(path, mode='r', *args, **kwargs):
            if path == notice and mode == 'x':
                with original_open(path, 'w') as stream:
                    stream.write('created by another writer')
            return original_open(path, mode, *args, **kwargs)

        with patch.object(Path, 'open', racing_open):
            with self.assertRaises(FileExistsError):
                core.export(self.db, self.rows, target)
        self.assertFalse(target.exists())
        self.assertEqual(notice.read_text(), 'created by another writer')

    def test_serialization_failure_creates_no_partial_files(self):
        target = self.path / 'invalid.json'
        with self.assertRaises(TypeError):
            core.export(self.db, [{'observations': [], 'excluded_reasons': [], 'invalid': object()}], target)
        self.assertFalse(target.exists())
        self.assertFalse(target.with_suffix('.json.provenance.json').exists())

    def test_write_failure_removes_both_new_files(self):
        target = self.path / 'failed.json'
        notice = target.with_suffix('.json.provenance.json')
        original_open = Path.open

        class FailingWriter:
            def __enter__(self):
                self.stream = original_open(notice, 'x', encoding='utf-8')
                return self

            def write(self, value):
                raise OSError('simulated full disk')

            def __exit__(self, *args):
                self.stream.close()

        def failing_open(path, mode='r', *args, **kwargs):
            return FailingWriter() if path == notice and mode == 'x' else original_open(path, mode, *args, **kwargs)

        with patch.object(Path, 'open', failing_open):
            with self.assertRaisesRegex(OSError, 'full disk'):
                core.export(self.db, self.rows, target)
        self.assertFalse(target.exists())
        self.assertFalse(notice.exists())


class DistributionBehaviorTests(unittest.TestCase):
    def test_bundled_examples_match_checkout_examples(self):
        data = resources.files('prospect').joinpath('data')
        for name in ('businesses.csv', 'rights.synthetic.json', 'suppression.csv', 'config.example.json'):
            source = ROOT / name if name == 'config.example.json' else ROOT / 'fixtures' / name
            self.assertEqual(data.joinpath(name).read_bytes(), source.read_bytes(), name)

    def test_demo_preserves_fixed_checks_and_rejects_existing_output(self):
        with tempfile.TemporaryDirectory() as directory, redirect_stdout(io.StringIO()):
            target = Path(directory) / 'demo'
            report = run(target)
            self.assertEqual(len(report['checks']), 12)
            self.assertTrue(all(report['checks'].values()))
            with self.assertRaises(FileExistsError):
                run(target)

    def test_module_version_is_public_release_version(self):
        result = subprocess.run([sys.executable, '-m', 'prospect', '--version'], cwd=ROOT, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), 'Prospect Review ' + __version__)
        self.assertEqual(__version__, '0.2.0')
