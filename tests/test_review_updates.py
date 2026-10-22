"""Fictional regression cases for review patching and evidence freshness."""
from contextlib import closing, redirect_stdout, redirect_stderr
from datetime import date, datetime, timedelta, timezone
import io
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from prospect import core
from prospect.__main__ import main

ROOT = Path(__file__).resolve().parents[1]
TODAY = datetime.now(timezone.utc).date()
OLD = (TODAY - timedelta(days=7)).isoformat()
URL = 'https://example.org/fictional-evidence'


class ReviewUpdateTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / 'legacy.sqlite3'
        # Existing v0.1.0 schema and JSON rows need no migration or rewriting.
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute('CREATE TABLE reviews(id INTEGER PRIMARY KEY, business_id INTEGER, created_at TEXT, data TEXT NOT NULL)')
        self.db = core.connect(self.path)
        self.addCleanup(self.db.close)
        core.import_data(self.db, ROOT / 'fixtures/businesses.csv', 'canonical', ROOT / 'fixtures/rights.synthetic.json')
        self.config = json.loads((ROOT / 'config.example.json').read_text())
        self.config['as_of'] = TODAY.isoformat()

    def save(self, **fields):
        return core.review(self.db, 1, {'evidence_url': URL, 'notes': 'Fictional evidence.', **fields})

    def initial(self, **fields):
        return self.save(**{'website_status': 'no_site_found', 'activity': 'active',
                           'ownership': 'independent', 'residential': 'yes',
                           'evidence_observed_at': OLD, **fields})

    def evaluated(self, **config):
        rows, _ = core.search(self.db, {**self.config, **config}, include_excluded=True)
        return next(b for b in rows if b['id'] == 1)

    def cli(self, *args):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = main(['--db', str(self.path), *args])
        return code, json.loads(out.getvalue()) if out.getvalue() else None, err.getvalue()

    def correction(self, *args):
        return self.cli('review', '1', '--evidence-url', URL, '--notes', 'Fictional correction.', *args)

    def test_configured_window_and_inclusive_zero_boundary(self):
        self.initial()
        for days, expected in [(0, False), (1, False), (6, False), (7, True), (180, True)]:
            with self.subTest(days=days):
                self.assertEqual(self.evaluated(freshness_days=days)['qualified'], expected)
        self.save(activity='active', evidence_observed_at=TODAY.isoformat())
        self.assertTrue(self.evaluated(freshness_days=0)['qualified'])
        self.assertFalse(self.evaluated(freshness_days=0, as_of=(TODAY - timedelta(days=1)).isoformat())['qualified'])

    def test_invalid_new_dates_do_not_append(self):
        self.initial()
        before = self.db.execute('SELECT * FROM reviews').fetchall()
        for value in ['', '2026-2-01', '2026-02-30', 'nonsense', '2026-01-01T12:00:00Z',
                      (TODAY + timedelta(days=1)).isoformat()]:
            with self.subTest(value=value):
                code, _, _ = self.correction('--evidence-observed-at', value)
                self.assertEqual(code, 2)
        self.assertEqual(before, self.db.execute('SELECT * FROM reviews').fetchall())

    def test_initial_undated_active_is_not_qualified(self):
        code, result, err = self.correction('--website-status', 'no_site_found', '--activity', 'active',
                                             '--ownership', 'independent', '--residential', 'yes')
        self.assertEqual(code, 0, err)
        self.assertIsNone(result['review']['evidence_observed_at'])
        self.assertFalse(self.evaluated()['qualified'])

    def test_patch_preserves_decisions_and_explicit_resets_work(self):
        first = self.initial(disposition='rejected', delivery='mobile', service_fit='yes',
                             candidate_website='https://example.org/possible')
        raw = self.db.execute('SELECT data FROM reviews').fetchone()[0]
        code, result, err = self.correction('--website-status', 'uncertain')
        self.assertEqual(code, 0, err)
        saved = result['review']
        for field in ('activity', 'ownership', 'residential', 'disposition', 'delivery', 'service_fit',
                      'candidate_website', 'evidence_observed_at'):
            self.assertEqual(saved[field], first[field], field)
        self.assertIn('manually_rejected', self.evaluated()['excluded_reasons'])
        self.assertEqual(self.db.execute('SELECT data FROM reviews ORDER BY id LIMIT 1').fetchone()[0], raw)
        code, result, err = self.correction('--residential', 'unknown', '--disposition', 'pending', '--candidate-website', '')
        self.assertEqual(code, 0, err)
        self.assertEqual((result['review']['residential'], result['review']['disposition'], result['review']['candidate_website']),
                         ('unknown', 'pending', ''))
        self.assertEqual(self.evaluated()['review_history_count'], 3)

    def test_new_website_date_does_not_refresh_activity_but_reassertion_does(self):
        first = self.initial()
        code, result, err = self.correction('--website-status', 'no_site_found', '--evidence-observed-at', TODAY.isoformat())
        self.assertEqual(code, 0, err)
        self.assertEqual(result['review']['evidence_observed_at'], TODAY.isoformat())
        self.assertEqual(result['review']['activity_evidence_observed_at'], OLD)
        self.assertEqual(result['review']['activity_evidence_notes'], first['notes'])
        self.assertFalse(self.evaluated(freshness_days=1)['qualified'])
        code, _, err = self.correction('--activity', 'active', '--evidence-observed-at', TODAY.isoformat())
        self.assertEqual(code, 0, err)
        self.assertTrue(self.evaluated(freshness_days=1)['qualified'])

    def test_legacy_bad_dates_remain_unverified_after_dated_patch(self):
        for value in ['__missing__', None, '', 'bad', 12, {}, '2026-01-01T12:00:00Z', (TODAY + timedelta(days=1)).isoformat()]:
            with self.subTest(value=value):
                self.db.execute('DELETE FROM reviews')
                legacy = {'website_status': 'no_site_found', 'activity': 'active', 'ownership': 'independent',
                          'residential': 'yes', 'evidence_url': URL, 'notes': 'Fictional legacy.',
                          'created_at': '1900-01-01', 'evidence_observed_at': value}
                if value == '__missing__':
                    legacy.pop('evidence_observed_at')
                self.db.execute('INSERT INTO reviews(business_id,created_at,data) VALUES(?,?,?)',
                                (1, TODAY.isoformat(), json.dumps(legacy)))
                self.db.commit()
                self.assertFalse(self.evaluated()['qualified'])
                # Database recording time wins over historical JSON metadata.
                self.assertEqual(self.evaluated()['latest_review_recorded_at'], TODAY.isoformat())
                with patch('prospect.core.now', return_value=TODAY.isoformat() + 'T12:00:00+00:00'):
                    saved = self.save(website_status='no_site_found', evidence_observed_at=TODAY.isoformat())
                self.assertEqual(saved['created_at'], TODAY.isoformat() + 'T12:00:00+00:00')
                self.assertFalse(self.evaluated()['qualified'])
                self.assertEqual(self.evaluated()['activity_assessment']['evidence_observed_at'],
                                 None if value == '__missing__' else value)

    def test_replacement_retains_site_contact_history_and_real_timestamp(self):
        self.initial(website_status='site_found', verified_website='https://fictional.example.org', disposition='contacted')
        saved = core.review(self.db, 1, {'evidence_url': URL, 'notes': 'Fictional replacement.',
                                       'created_at': '1900-01-01'}, replace=True)
        self.assertEqual(saved['website_status'], 'unreviewed')
        self.assertEqual(saved['activity'], 'unknown')
        self.assertIsNone(saved['evidence_observed_at'])
        self.assertNotEqual(saved['created_at'], '1900-01-01')
        result = self.evaluated()
        self.assertIn('suppressed_or_contacted', result['excluded_reasons'])
        self.assertIn('website_present', result['excluded_reasons'])
        self.assertEqual(result['review_history_count'], 2)

    def test_cli_replace_and_manual_origin(self):
        self.initial(snapshot_action_id='fictional', research_record_key='fictional', queue_hold=True)
        code, _, err = self.correction('--website-status', 'uncertain')
        self.assertEqual(code, 0, err)
        result = self.evaluated()
        self.assertEqual(result['review_state'], 'saved_human_review')
        self.assertIn('manual_hold', result['excluded_reasons'])
        code, result, err = self.correction('--replace')
        self.assertEqual(code, 0, err)
        self.assertFalse(result['review']['queue_hold'])
        self.assertEqual(result['review']['ownership'], 'unknown')


if __name__ == '__main__':
    unittest.main()
