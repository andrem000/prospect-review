"""Synthetic regressions for explicit activity assertion provenance (F1/F4)."""
import json
from datetime import date
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from prospect import app, core, inbox, investigation, review_state


ROOT = Path(__file__).resolve().parents[1]
URL = 'https://example.org/activity-check'
DATE = '2026-10-01'


class ReviewAssertionTests(unittest.TestCase):
    def setUp(self):
        scratch = ROOT / '.test-temp/v0.6.1-core'
        scratch.mkdir(parents=True, exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=scratch)
        self.addCleanup(self.temp.cleanup)
        self.application = app.Application(self.temp.name)
        self.db = core.connect(self.application.dbpath)
        self.addCleanup(self.db.close)
        core.import_data(self.db, ROOT / 'fixtures/businesses.csv', 'canonical', ROOT / 'fixtures/rights.synthetic.json')
        self.config = json.loads((ROOT / 'config.example.json').read_text())
        self.config.update(as_of='2026-10-22', freshness_days=30)

    def save(self, replace=False, **fields):
        return core.review(self.db, 1, dict(evidence_url=URL, notes='Synthetic activity check', **fields), replace)

    def business(self):
        return next(b for b in core.all_businesses(self.db) if b['id'] == 1)

    def evaluated(self):
        return core.evaluate(self.business(), self.config, set(), date(2026, 10, 22))

    def saved(self):
        return review_state.saved_answers(self.business())

    def reasons(self):
        return [r for item in inbox.list_items(self.db, self.config)['items']
                if item['business_id'] == 1 for r in item['reasons'] if r['question'] == 'activity']

    def prepared(self):
        self.save(activity='active', website_status='no_site_found', ownership='independent',
                  residential='yes', evidence_observed_at='2020-01-01')
        self.save(website_status='no_site_found', evidence_observed_at=DATE)
        self.assertFalse(self.evaluated()['qualified'])

    def assert_undated(self, notes='Synthetic activity check'):
        effective = self.business()['review']
        self.assertEqual(effective['evidence_observed_at'], DATE)
        self.assertIsNone(effective['activity_evidence_observed_at'])
        saved = self.saved()['activity']
        self.assertTrue(saved['basis_known'])
        self.assertIsNone(saved['evidence_observed_at'])
        self.assertEqual(saved['notes'], notes)
        self.assertFalse(self.evaluated()['qualified'])
        self.assertEqual(self.reasons()[0]['kind'], 'evidence_unknown')

    def test_core_same_value_undated_assertion_never_borrows_date(self):
        self.prepared()
        history = list(self.db.execute('SELECT data FROM reviews ORDER BY id'))
        self.save(activity='active')
        self.assert_undated()
        self.assertEqual(history, list(self.db.execute('SELECT data FROM reviews ORDER BY id LIMIT 2')))

    def test_real_cli_same_value_undated_assertion_never_borrows_date(self):
        self.prepared()
        result = subprocess.run([sys.executable, '-m', 'prospect', '--db', str(self.application.dbpath),
            'review', '1', '--activity', 'active', '--evidence-url', URL, '--notes', 'CLI assertion'],
            cwd=ROOT, text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIsNone(json.loads(result.stdout)['review']['activity_evidence_observed_at'])
        self.assert_undated('CLI assertion')

    def test_advanced_review_undated_assertion(self):
        self.prepared()
        result = self.application.dispatch('POST', '/api/businesses/1/review', {}, dict(changes=dict(
            activity='active', evidence_url=URL, notes='Advanced assertion')), self.db)
        self.assertIsNone(result['review']['activity_evidence_observed_at'])
        self.assert_undated('Advanced assertion')

    def test_replace_explicit_unknown_has_only_new_basis(self):
        self.prepared()
        self.save(replace=True, activity='unknown')
        self.assertIsNone(self.business()['review']['activity_evidence_observed_at'])
        self.assertEqual(self.saved()['activity']['answers'], dict(activity='unknown'))
        self.assertEqual(self.reasons()[0]['kind'], 'evidence_unknown')

    def test_omitted_activity_retains_assertion_and_original_basis_after_restart(self):
        first = self.save(activity='unknown', evidence_observed_at='2020-01-01')
        self.save(website_status='uncertain', evidence_observed_at=DATE)
        self.db.close()
        self.db = core.connect(self.application.dbpath)
        self.addCleanup(self.db.close)
        self.assertTrue(self.business()['review']['activity_asserted'])
        for key in core.ACTIVITY_EVIDENCE_FIELDS:
            self.assertEqual(self.business()['review'][key], first[key])
        self.assertEqual(self.saved()['activity']['evidence_observed_at'], '2020-01-01')
        self.assertEqual(self.reasons()[0]['kind'], 'evidence_old')

    def test_dated_guided_save_and_undo_preserve_assertion_metadata(self):
        self.save(activity='unknown', evidence_observed_at='2020-01-01')
        business = self.evaluated()
        state = investigation.read(self.db, 1, business)
        result = investigation.commit(self.db, 1, dict(review_revision=state['review_revision'],
            draft_revision=state['draft_revision'], request_id='synthetic-guided', questions=dict(activity=dict(
                activity='active', evidence_url=URL, notes='Dated guided assertion', evidence_observed_at=DATE))), business)
        self.assertEqual(self.saved()['activity']['evidence_observed_at'], DATE)
        self.assertTrue(self.evaluated()['activity_assessment']['current_operation_verified'])
        investigation.undo(self.db, 1, dict(session_id=result['session_id'], review_revision=result['review_revision']), self.evaluated())
        self.assertEqual(self.saved()['activity']['answers'], dict(activity='unknown'))
        self.assertEqual(self.saved()['activity']['evidence_observed_at'], '2020-01-01')

    def test_first_explicit_unknown_is_saved_and_ages_but_default_is_unchecked(self):
        self.assertNotIn('activity', self.saved())
        self.save(website_status='uncertain', evidence_observed_at=DATE)
        self.assertNotIn('activity', self.saved())
        self.assertEqual(self.reasons(), [])
        self.save(activity='unknown', evidence_observed_at=DATE)
        self.assertEqual(self.saved()['activity']['answers'], dict(activity='unknown'))
        self.assertEqual(self.saved()['activity']['evidence_url'], URL)
        self.assertEqual(self.reasons(), [])
        self.config['as_of'] = '2026-11-01'
        self.assertEqual(self.reasons()[0]['kind'], 'evidence_old')

    def test_unknown_legacy_fields_do_not_invent_explicitness_or_rewrite_history(self):
        legacy = dict(core.REVIEW_DEFAULTS, evidence_url=URL, notes='Legacy unknown',
            activity_evidence_observed_at=DATE, activity_evidence_url=URL, activity_evidence_notes='Unprovable')
        raw = json.dumps(legacy)
        with self.db:
            self.db.execute('INSERT INTO reviews(business_id,created_at,data) VALUES(?,?,?)', (1, DATE, raw))
        self.assertNotIn('activity', self.saved())
        self.save(website_status='uncertain', evidence_observed_at=DATE)
        self.assertNotIn('activity', self.saved())
        self.assertEqual(self.db.execute('SELECT data FROM reviews ORDER BY id LIMIT 1').fetchone()[0], raw)

    def test_first_cli_unknown_is_durably_saved_with_its_actual_basis(self):
        result = subprocess.run([sys.executable, '-m', 'prospect', '--db', str(self.application.dbpath),
            'review', '1', '--activity', 'unknown', '--evidence-url', URL, '--notes', 'Inconclusive CLI check',
            '--evidence-observed-at', '2020-01-01'], cwd=ROOT, text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(json.loads(result.stdout)['review']['activity_asserted'])
        self.assertEqual(self.saved()['activity'], dict(answers=dict(activity='unknown'),
            evidence_url=URL, notes='Inconclusive CLI check', evidence_observed_at='2020-01-01', basis_known=True))
        self.assertEqual(self.reasons()[0]['kind'], 'evidence_old')

    def test_undated_legacy_active_remains_unknown_after_unrelated_correction(self):
        legacy = dict(core.REVIEW_DEFAULTS, activity='active', evidence_url=URL, notes='Legacy active')
        with self.db:
            self.db.execute('INSERT INTO reviews(business_id,created_at,data) VALUES(?,?,?)', (1, DATE, json.dumps(legacy)))
        self.save(website_status='uncertain', evidence_observed_at=DATE)
        self.assertIsNone(self.saved()['activity']['evidence_observed_at'])
        self.assertFalse(self.evaluated()['activity_assessment']['current_operation_verified'])
        self.assertEqual(self.reasons()[0]['kind'], 'evidence_unknown')
