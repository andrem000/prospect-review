"""Queue/scope contracts and hash-pinned reconciliation, using invented records."""
from contextlib import closing
from datetime import date, datetime, timezone
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from prospect import core, reconcile

ROOT = Path(__file__).resolve().parents[1]


class UpgradeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name)
        self.db_path = self.path / 'source.sqlite3'
        self.db = core.connect(self.db_path)
        self.addCleanup(self.db.close)
        self.rows = [
            {'source_id': 'p1', 'name': 'Synthetic Pressure Washing', 'country': 'US', 'state': 'NC', 'city': 'Charlotte', 'categories': ['pressure_washing'], 'phone': '7045550101'},
            {'source_id': 'p2', 'name': 'Synthetic Mobile Detailing', 'country': 'US', 'state': 'TX', 'city': 'San Antonio', 'categories': ['auto_detailing'], 'phone': '2105550102'},
            {'source_id': 'p3', 'name': 'Synthetic Fixed Car Wash', 'country': 'US', 'state': 'TX', 'city': 'San Antonio', 'categories': ['car_wash'], 'phone': '2105550103'},
        ]
        self.input = self.path / 'rows.json'
        self.input.write_text(json.dumps(self.rows), encoding='utf-8')
        core.import_data(self.db, self.input, 'canonical', ROOT / 'fixtures/rights.synthetic.json')
        self.pressure = json.loads((ROOT / 'profiles/pressure-washing.json').read_text(encoding='utf-8'))
        self.detailing = json.loads((ROOT / 'profiles/mobile-detailing.json').read_text(encoding='utf-8'))

    def review(self, bid=1, **values):
        core.review(self.db, bid, {'website_status': 'no_site_found', 'activity': 'unknown', 'ownership': 'unknown',
                                  'residential': 'unknown', 'disposition': 'pending', 'evidence_url': 'https://example.org/evidence',
                                  'notes': 'Invented evidence for testing only.',
                                  **({'evidence_observed_at': datetime.now(timezone.utc).date().isoformat()} if values.get('activity') == 'active' else {}), **values})

    def manifest(self, cases=None, controls=None, decided='2026-10-18T12:00:00Z'):
        bank = self.path / 'bank.json'
        bank.write_text(json.dumps(core.all_businesses(self.db)), encoding='utf-8')
        source = self.path / 'cases.json'
        source.write_text(json.dumps({'checked_utc': decided, 'cases': cases or [
            {'id': 1, 'name': self.rows[0]['name'], 'evidence_observed_at': '2026-01-01',
             'queue_review': {'website_status': 'site_found', 'verified_website': 'https://synthetic.example.org',
                              'evidence_url': 'https://synthetic.example.org', 'disposition': 'pending', 'queue_hold': False}}
        ]}), encoding='utf-8')
        manifest = self.path / 'manifest.json'
        manifest.write_text(json.dumps({'bank': str(bank), 'sources': [{'absolute_path': str(source), 'sha256': reconcile.file_hash(source)}]}), encoding='utf-8')
        ctl = self.path / 'controls.json'
        ctl.write_text(json.dumps({'created_utc': '2026-10-20T10:00:00Z', 'records': controls or []}), encoding='utf-8')
        return manifest, ctl

    def preview(self, **kwargs):
        manifest, ctl = self.manifest(**kwargs)
        with closing(reconcile.open_readonly(self.db_path)) as db:
            return reconcile.preview(db, manifest, ctl)

    def test_service_scope_can_fit_commercial_nonresidential_pressure_business(self):
        self.review(residential='no', service_fit='yes', activity='active', ownership='independent')
        rows, _ = core.search(self.db, self.pressure, qualified_only=True)
        self.assertEqual([b['id'] for b in rows], [1])
        self.assertNotIn('residential_scope_unverified', rows[0]['review_flags'])

    def test_service_fit_never_substitutes_for_activity_or_ownership(self):
        self.review(service_fit='yes')
        self.assertEqual(core.search(self.db, self.pressure, qualified_only=True)[0], [])

    def test_residential_yes_does_not_implicitly_verify_service_profile(self):
        self.review(residential='yes', activity='active', ownership='independent')
        self.assertEqual(core.search(self.db, self.pressure, qualified_only=True)[0], [])

    def test_mobile_name_hint_does_not_verify_delivery(self):
        self.review(2, service_fit='yes', activity='active', ownership='independent')
        rows, _ = core.search(self.db, self.detailing)
        b = next(b for b in rows if b['id'] == 2)
        self.assertTrue(b['scope_assessment']['mobile_name_hint'])
        self.assertIn('delivery_scope_unverified', b['review_flags'])
        self.assertFalse(b['qualified'])
        self.review(2, service_fit='yes', delivery='mobile', activity='active', ownership='independent')
        self.assertEqual([b['id'] for b in core.search(self.db, self.detailing, qualified_only=True)[0]], [2])

    def test_fixed_only_detailer_is_outside_mobile_scope(self):
        self.review(2, service_fit='yes', delivery='fixed')
        b = next(b for b in core.search(self.db, self.detailing, include_excluded=True)[0] if b['id'] == 2)
        self.assertIn('outside_delivery_scope', b['excluded_reasons'])

    def test_broad_car_wash_category_stays_uncertain(self):
        b = next(b for b in core.search(self.db, self.detailing)[0] if b['id'] == 3)
        self.assertEqual(b['trade_assessment']['status'], 'uncertain')
        self.assertFalse(b['qualified'])

    def test_niche_category_conflict_is_explainable_not_silent(self):
        b = core.all_businesses(self.db)[1]
        b['categories'] = ['auto_detailing', 'automatic_car_wash']
        evaluated = core.evaluate(b, self.detailing, set(), date(2026, 10, 20))
        self.assertEqual(evaluated['trade_assessment']['status'], 'uncertain')
        self.assertIn('automatic_car_wash', evaluated['trade_assessment']['negative_signals'])

    def test_raw_and_actual_review_states_are_distinct(self):
        b = core.search(self.db, self.pressure)[0][0]
        self.assertEqual((b['review_state'], b['review_history_count']), ('raw_import_only', 0))
        self.review()
        b = core.search(self.db, self.pressure)[0][0]
        self.assertEqual((b['review_state'], b['review_history_count']), ('saved_human_review', 1))
        self.assertIsNone(b['latest_evidence_observed_at'])

    def test_default_queue_has_no_known_site_or_contact_leakage(self):
        self.review(website_status='site_found', verified_website='https://synthetic.example.org')
        self.assertEqual(core.search(self.db, self.pressure)[0], [])
        audit, metrics = core.search(self.db, self.pressure, include_excluded=True)
        self.assertEqual(metrics['output_scope'], 'all_records_audit')
        self.assertIn('website_present', next(b for b in audit if b['id'] == 1)['excluded_reasons'])

    def test_residential_default_still_rejects_explicit_nonresidential(self):
        self.review(residential='no', service_fit='yes')
        config = {**self.pressure, 'service_scope': 'residential'}
        b = next(b for b in core.search(self.db, config, include_excluded=True)[0] if b['id'] == 1)
        self.assertIn('manually_rejected', b['excluded_reasons'])

    def test_preview_is_readonly_and_date_is_not_refreshed(self):
        before = reconcile.file_hash(self.db_path)
        plan = self.preview()
        self.assertEqual(reconcile.file_hash(self.db_path), before)
        self.assertEqual(plan['conflicts'], [])
        action = plan['actions'][0]['data']
        self.assertEqual(action['evidence_observed_at'], '2026-01-01')
        self.assertTrue(all(action[k] == 'unknown' for k in ('activity', 'ownership', 'residential', 'service_fit', 'delivery')))

    def test_apply_only_new_copy_and_repeat_skips_original_action(self):
        plan = self.preview()
        before = reconcile.file_hash(self.db_path)
        output = self.path / 'new.sqlite3'
        reconcile.apply_copy(self.db_path, plan, output, plan['plan_id'])
        self.assertEqual(reconcile.file_hash(self.db_path), before)
        with closing(reconcile.open_readonly(output)) as db:
            rows = core.all_businesses(db)
            self.assertEqual(rows[0]['review']['activity'], 'unknown')
            manifest, ctl = self.path / 'manifest.json', self.path / 'controls.json'
            repeat = reconcile.preview(db, manifest, ctl)
            self.assertEqual(repeat['actions'], [])
            self.assertEqual(repeat['conflicts'], [])
            self.assertEqual(len(repeat['skipped']), 1)
        with self.assertRaisesRegex(ValueError, 'fresh'):
            reconcile.apply_copy(self.db_path, plan, output, plan['plan_id'])

    def test_wrong_approval_or_edited_plan_cannot_apply(self):
        plan = self.preview()
        output = self.path / 'denied.sqlite3'
        with self.assertRaises(ValueError):
            reconcile.apply_copy(self.db_path, plan, output, 'incorrect')
        plan['actions'][0]['data']['notes'] = 'edited'
        with self.assertRaises(ValueError):
            reconcile.apply_copy(self.db_path, plan, output, plan['plan_id'])
        self.assertFalse(output.exists())

    def test_changed_database_requires_new_preview(self):
        plan = self.preview()
        self.review()
        with self.assertRaisesRegex(ValueError, 'Database changed'):
            reconcile.apply_copy(self.db_path, plan, self.path / 'stale.sqlite3', plan['plan_id'])

    def test_changed_source_snapshot_hash_is_rejected(self):
        manifest, ctl = self.manifest()
        (self.path / 'cases.json').write_text('{}', encoding='utf-8')
        with self.assertRaisesRegex(ValueError, 'hash mismatch'):
            reconcile.preview(self.db, manifest, ctl)

    def test_changed_input_after_preview_cannot_apply(self):
        plan = self.preview()
        (self.path / 'controls.json').write_text('{}', encoding='utf-8')
        with self.assertRaisesRegex(ValueError, 'Input changed'):
            reconcile.apply_copy(self.db_path, plan, self.path / 'stale.sqlite3', plan['plan_id'])

    def test_case_identity_mismatch_is_not_imported(self):
        plan = self.preview(cases=[{'id': 1, 'name': 'Different Business', 'decision': 'hold_identity_route'}])
        self.assertFalse(plan['actions'])
        self.assertTrue(any('name differs' in c['reason'] for c in plan['conflicts']))

    def test_wrong_bank_identity_is_not_imported(self):
        manifest, ctl = self.manifest()
        bank = json.loads((self.path / 'bank.json').read_text(encoding='utf-8'))
        bank[0]['observations'][0]['source_id'] = 'different-source-identity'
        (self.path / 'bank.json').write_text(json.dumps(bank), encoding='utf-8')
        plan = reconcile.preview(self.db, manifest, ctl)
        self.assertTrue(any('identity differs' in c['reason'] for c in plan['conflicts']))

    def test_newer_review_and_date_only_ambiguity_are_protected(self):
        self.review()
        for when in ('2020-01-01', core.now()[:10]):
            with self.subTest(when=when):
                plan = self.preview(decided=when)
                self.assertEqual(plan['actions'], [])
                self.assertTrue(plan['conflicts'])

    def test_new_contact_date_cannot_smuggle_older_outcome(self):
        self.review()
        plan = self.preview(controls=[{'engineering_id': 1, 'business': self.rows[0]['name'], 'status': 'contacted_exclude_repeat_initial_followup'}], decided='2020-01-01')
        self.assertEqual(plan['actions'], [])
        self.assertTrue(any('piggyback' in c['reason'] for c in plan['conflicts']))

    def test_name_only_contact_and_wrong_explicit_id_are_conflicts(self):
        for control in ({'business': self.rows[0]['name'], 'status': 'contacted_exclude_repeat_initial_followup'},
                        {'engineering_id': 1, 'business': 'Different Business', 'status': 'contacted_exclude_repeat_initial_followup'}):
            with self.subTest(control=control):
                plan = self.preview(controls=[control])
                self.assertTrue(plan['conflicts'])
                with self.assertRaisesRegex(ValueError, 'conflicts'):
                    reconcile.apply_copy(self.db_path, plan, self.path / 'blocked.sqlite3', plan['plan_id'])

    def test_explicit_contact_precedence_and_durable_keys(self):
        plan = self.preview(controls=[{'engineering_id': 1, 'business': self.rows[0]['name'], 'status': 'contacted_exclude_repeat_initial_followup'}])
        self.assertEqual(plan['conflicts'], [])
        self.assertEqual(plan['actions'][0]['data']['disposition'], 'contacted')
        target = self.path / 'contacted.sqlite3'
        reconcile.apply_copy(self.db_path, plan, target, plan['plan_id'])
        with closing(core.connect(target)) as db:
            self.assertEqual(core.search(db, self.pressure)[0], [])
            core.review(db, 1, {'website_status': 'uncertain', 'evidence_url': 'https://example.org/check', 'notes': 'Later pending review', 'disposition': 'pending'})
            self.assertEqual(core.search(db, self.pressure)[0], [])

    def test_hosted_confirmation_and_unknown_domain_remain_distinct(self):
        plan = self.preview(cases=[{'id': 1, 'name': self.rows[0]['name'], 'queue_review': {
            'website_status': 'site_found', 'verified_website': 'https://synthetic.lovable.app', 'disposition': 'pending', 'queue_hold': False}}])
        output = self.path / 'hosted.sqlite3'
        reconcile.apply_copy(self.db_path, plan, output, plan['plan_id'])
        with closing(core.connect(output)) as db:
            b = next(b for b in core.search(db, self.pressure, include_excluded=True)[0] if b['id'] == 1)
            self.assertEqual(b['website_signal'], 'provider_hosted_website')
            self.assertEqual(b['review_state'], 'carried_research_review')
        invalid = self.preview(cases=[{'id': 1, 'name': self.rows[0]['name'], 'queue_review': {
            'website_status': 'site_found', 'verified_website': 'https://bit.ly/unknown', 'queue_hold': False}}])
        self.assertTrue(invalid['conflicts'])

    def test_cli_preview_does_not_create_missing_database(self):
        manifest, ctl = self.manifest()
        missing = self.path / 'missing.sqlite3'
        result = subprocess.run([sys.executable, '-m', 'prospect', '--db', str(missing), 'reconcile-preview', '--manifest', str(manifest),
                                 '--controls', str(ctl), '--out', str(self.path / 'preview.json')], cwd=ROOT, capture_output=True, text=True)
        self.assertEqual(result.returncode, 2)
        self.assertFalse(missing.exists())

    def test_manual_hold_is_audit_only_not_a_fresh_candidate(self):
        plan = self.preview(cases=[{'id': 1, 'name': self.rows[0]['name'], 'decision': 'hold_identity_route', 'sources': [{'url': 'https://example.org/evidence'}]}])
        target = self.path / 'held.sqlite3'
        reconcile.apply_copy(self.db_path, plan, target, plan['plan_id'])
        with closing(core.connect(target)) as db:
            self.assertEqual(core.search(db, self.pressure)[0], [])
            b = next(b for b in core.search(db, self.pressure, include_excluded=True)[0] if b['id'] == 1)
            self.assertEqual((b['queue_status'], b['review_state']), ('held', 'carried_research_review'))
            self.assertFalse(b['website_assessment']['absence_verified'])

    def test_even_signed_plan_cannot_recertify_activity(self):
        plan = self.preview()
        plan['actions'][0]['data']['activity'] = 'active'
        plan['plan_id'] = reconcile.digest({k: v for k, v in plan.items() if k != 'plan_id'})
        with self.assertRaisesRegex(ValueError, 're-certify'):
            reconcile.apply_copy(self.db_path, plan, self.path / 'denied.sqlite3', plan['plan_id'])

    def test_derived_control_parent_change_blocks_application(self):
        manifest, ctl = self.manifest()
        parent = self.path / 'approved-parent.json'
        parent.write_text('{}', encoding='utf-8')
        ctl.write_text(json.dumps({'records': [], 'derived_from': [{'path': str(parent), 'sha256': reconcile.file_hash(parent)}]}), encoding='utf-8')
        plan = reconcile.preview(self.db, manifest, ctl)
        parent.write_text('{"changed":true}', encoding='utf-8')
        with self.assertRaisesRegex(ValueError, 'Input changed'):
            reconcile.apply_copy(self.db_path, plan, self.path / 'denied.sqlite3', plan['plan_id'])

    def test_cli_preview_and_apply_round_trip(self):
        manifest, ctl = self.manifest()
        plan_file = self.path / 'cli-plan.json'
        def run(*args):
            result = subprocess.run([sys.executable, '-m', 'prospect', '--db', str(self.db_path), *args], cwd=ROOT, capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            return json.loads(result.stdout)
        preview = run('reconcile-preview', '--manifest', str(manifest), '--controls', str(ctl), '--out', str(plan_file))
        self.assertEqual((preview['mode'], preview['actions_proposed']), ('dry_run', 1))
        applied = run('reconcile-apply', '--plan', str(plan_file), '--approve-plan', preview['plan_id'], '--out-db', str(self.path / 'cli-copy.sqlite3'))
        self.assertEqual(applied['actions_applied'], 1)

    def test_conflicting_same_date_snapshots_block_application(self):
        manifest, ctl = self.manifest()
        extra = self.path / 'conflicting-cases.json'
        extra.write_text(json.dumps({'checked_utc': '2026-10-18T12:00:00Z', 'cases': [
            {'id': 1, 'name': self.rows[0]['name'], 'decision': 'hold_identity_route'}]}), encoding='utf-8')
        data = json.loads(manifest.read_text(encoding='utf-8'))
        data['sources'].append({'absolute_path': str(extra), 'sha256': reconcile.file_hash(extra)})
        manifest.write_text(json.dumps(data), encoding='utf-8')
        plan = reconcile.preview(self.db, manifest, ctl)
        self.assertTrue(any('same-date' in c['reason'] for c in plan['conflicts']))
        with self.assertRaisesRegex(ValueError, 'conflicts'):
            reconcile.apply_copy(self.db_path, plan, self.path / 'blocked.sqlite3', plan['plan_id'])

    def test_cli_all_record_audit_does_not_label_excluded_rows_candidates(self):
        config = self.path / 'profile.json'
        config.write_text(json.dumps(self.pressure), encoding='utf-8')
        result = subprocess.run([sys.executable, '-m', 'prospect', '--db', str(self.db_path), 'search', '--config', str(config), '--include-excluded'],
                                cwd=ROOT, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        data = json.loads(result.stdout)
        self.assertEqual(data['output_scope'], 'all_records_audit')
        self.assertIn('records', data)
        self.assertNotIn('candidates', data)
        self.assertTrue(any(row['excluded_reasons'] for row in data['records']))


if __name__ == '__main__':
    unittest.main()
