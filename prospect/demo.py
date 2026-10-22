"""Offline fictional demonstration, with reproducible assertions and fresh output."""
import argparse
import hashlib
import json
from contextlib import closing, ExitStack
from importlib import resources
from pathlib import Path
from unittest.mock import patch

from . import core

FIXED_TIME = '2026-10-15T12:00:00+00:00'


def run(output):
    with ExitStack() as stack:
        data = resources.files('prospect').joinpath('data')
        inputs = [stack.enter_context(resources.as_file(data.joinpath(name))) for name in
                  ('businesses.csv', 'rights.synthetic.json', 'suppression.csv', 'config.example.json')]
        return _run(output, inputs)


def _run(output, inputs):
    output = Path(output).resolve()
    output.mkdir(parents=True, exist_ok=False)
    hashes = {path.name: hashlib.sha256(path.read_bytes()).hexdigest() for path in inputs}
    config = json.loads(inputs[-1].read_text(encoding='utf-8'))
    config['as_of'] = '2026-10-15'
    checks = {}

    def require(name, condition):
        if not condition:
            raise AssertionError(name)
        checks[name] = True

    # Demo-only clock: scoring, simulated review time and emitted artifacts repeat.
    # This never replaces evidence dates during ordinary CLI operation.
    with patch.object(core, 'now', return_value=FIXED_TIME), closing(core.connect(output / 'demo.sqlite3')) as db:
        imported = core.import_data(db, inputs[0], 'canonical', inputs[1])
        first_ids = [row['id'] for row in core.all_businesses(db)]
        repeated = core.import_data(db, inputs[0], 'canonical', inputs[1])
        require('reimport_preserves_business_ids', repeated['inserted'] == 0 and
                [row['id'] for row in core.all_businesses(db)] == first_ids)
        require('ten_rows_become_eight_us_businesses', imported['records_read'] == 10 and len(first_ids) == 8)
        core.suppress(db, inputs[2])
        before, before_metrics = core.search(db, config)
        require('two_candidates_before_manual_review', len(before) == 2)
        require('raw_candidates_are_unreviewed_and_unqualified', all(
            row['review_state'] == 'raw_import_only' and row['verification_status'] == 'unreviewed'
            and not row['qualified'] for row in before))
        cedar = next(row for row in before if row['name'] == 'Cedar Example Plumbing')
        require('missing_listing_field_is_only_a_signal', cedar['website_signal'] == 'no_website_listed')
        audit, _ = core.search(db, config, include_excluded=True)
        by_name = {row['name']: row for row in audit}
        require('contacted_fixture_excluded_with_reason',
                'suppressed_or_contacted' in by_name['Contacted Example Cleaning']['excluded_reasons'])
        require('website_fixture_excluded_with_reason',
                'website_present' in by_name['Maple Example HVAC']['excluded_reasons'])
        require('scores_have_explanations', all(row['score'] == sum(row['score_breakdown'].values()) for row in audit))
        core.review(db, cedar['id'], {
            'website_status': 'no_site_found', 'activity': 'active', 'ownership': 'independent',
            'residential': 'yes', 'disposition': 'pending',
            'evidence_url': 'https://example.com/simulated-review',
            'evidence_observed_at': '2026-10-15',
            'notes': 'SIMULATED: invented evidence for this demonstration. No real business was checked or contacted.'})
        after, after_metrics = core.search(db, config)
        require('only_simulated_review_can_qualify_fixture', sum(row['qualified'] for row in after) == 1)
        require('unreviewed_fixture_stays_unknown', next(row for row in after if row['name'] ==
                'Elm Example Electric')['verification_status'] == 'unreviewed')
        core.export(db, after, output / 'candidates.json')
        core.export(db, after, output / 'candidates.csv')
        core.export(db, audit, output / 'audit-before-review.json')
        provenance = json.loads((output / 'candidates.json.provenance.json').read_text(encoding='utf-8'))
        require('export_has_synthetic_provenance', provenance['sources'] and
                all(source['license'] == 'synthetic' for source in provenance['sources']))
    require('inputs_unchanged', hashes == {path.name: hashlib.sha256(path.read_bytes()).hexdigest() for path in inputs})
    report = {
        'synthetic_only': True, 'scoring_date': '2026-10-15', 'simulated_clock': FIXED_TIME,
        'checks': checks, 'first_import': imported, 'repeat_import': repeated,
        'repeat_import_semantics': 'Business identities are stable. Every import appends a batch and observations.',
        'before_simulated_review': before_metrics, 'after_simulated_review': after_metrics,
        'input_sha256': hashes, 'network_requests': 0,
        'meaning': 'A fictional offline software demonstration. No absence, current business facts, recipient eligibility or market demand is verified.'}
    (output / 'report.json').write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
    print(json.dumps({'checks_passed': len(checks), 'businesses': 8, 'candidates': 2,
        'qualified_after_simulated_review': 1, 'synthetic_only': True, 'network_requests': 0}, indent=2))
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out', default='demo-output', help='A new directory; existing directories are never overwritten')
    args = parser.parse_args()
    try:
        run(args.out)
    except (OSError, ValueError) as error:
        parser.error(str(error))


if __name__ == '__main__':
    main()
