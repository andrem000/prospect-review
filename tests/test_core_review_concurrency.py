"""Synthetic review serialization and caller transaction ownership regressions."""
from contextlib import closing
from pathlib import Path
import sqlite3
import tempfile
import unittest

from prospect import core


def evidence(**fields):
    return dict(evidence_url='https://example.org/synthetic-evidence',
                notes='Synthetic review evidence.', **fields)


class CoreReviewConcurrencyTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / 'synthetic.sqlite3'
        self.db = core.connect(self.path)
        self.addCleanup(self.db.close)
        core.import_rows(self.db, [core.normalize(dict(
            source_id='synthetic-one', name='Synthetic Painting', country='US',
            state='TX', city='Example City', address='1 Example Road',
            categories=['painter'], phones=['2025550101']))],
            {'source': 'synthetic'}, 'synthetic-hash')
        self.first = core.review(self.db, 1, evidence(
            activity='active', ownership='unknown', evidence_observed_at='2026-01-01'))

    def latest(self, db=None):
        return core.all_businesses(db or self.db)[0]

    def test_overlapping_partial_reviews_serialize_then_retry_without_lost_fields(self):
        # Exact audit interleaving, no threads or sleeps: attempt B from the trace
        # hook just before A's INSERT. A must already hold the writer reservation.
        attempts = []
        with closing(core.connect(self.path)) as other:
            other.execute('PRAGMA busy_timeout=0')

            def attempt_other_writer(sql):
                if sql.startswith('INSERT INTO reviews') and not attempts:
                    try:
                        core.review(other, 1, evidence(ownership='independent', disposition='contacted'))
                    except sqlite3.OperationalError as exc:
                        attempts.append(str(exc))
                    else:
                        attempts.append('unexpected success')

            self.db.set_trace_callback(attempt_other_writer)
            try:
                website = core.review(self.db, 1, evidence(website_status='uncertain'))
            finally:
                self.db.set_trace_callback(None)
            self.assertEqual(attempts, ['database is locked'])
            self.assertFalse(other.in_transaction)
            self.assertEqual(len(self.latest()['reviews']), 2)
            self.assertEqual(website['activity_evidence_observed_at'], '2026-01-01')
            retried = core.review(other, 1, evidence(ownership='independent', disposition='contacted'))
            self.assertEqual(retried['website_status'], 'uncertain')
            self.assertEqual(retried['ownership'], 'independent')
            self.assertEqual(retried['activity_evidence_notes'], self.first['notes'])
            self.assertEqual(len(self.latest()['reviews']), 3)
            suppressed = set(map(tuple, other.execute('SELECT kind,value FROM suppressions')))
            self.assertIn(('business_id', '1'), suppressed)
            self.assertIn(('phone', '2025550101'), suppressed)
            # Replacement semantics still reset effective fields, but cannot
            # erase the durable contact exclusion or prior decision history.
            reset = core.review(other, 1, evidence(), replace=True)
            self.assertEqual(reset['ownership'], 'unknown')
            self.assertEqual(reset['website_status'], 'unreviewed')
            self.assertEqual(suppressed, set(map(tuple, other.execute('SELECT kind,value FROM suppressions'))))
            self.assertEqual(len(self.latest()['reviews']), 4)

    def test_caller_owned_transaction_can_rollback_review_and_contact_keys(self):
        before = self.db.execute('SELECT COUNT(*) FROM runs').fetchone()[0]
        self.db.execute('BEGIN IMMEDIATE')
        self.db.execute('INSERT INTO runs(kind,metrics) VALUES(?,?)', ('synthetic-caller', '{}'))
        saved = core.review(self.db, 1, evidence(disposition='contacted'))
        self.assertEqual(saved['disposition'], 'contacted')
        self.assertTrue(self.db.in_transaction)
        self.assertGreater(self.db.execute('SELECT COUNT(*) FROM suppressions').fetchone()[0], 0)
        # Another connection sees neither the pending review nor contact keys.
        with closing(core.connect(self.path)) as reader:
            self.assertEqual(len(self.latest(reader)['reviews']), 1)
            self.assertEqual(reader.execute('SELECT COUNT(*) FROM suppressions').fetchone()[0], 0)
        self.db.rollback()
        self.assertEqual(len(self.latest()['reviews']), 1)
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM suppressions').fetchone()[0], 0)
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM runs').fetchone()[0], before)

    def test_failed_contact_key_write_rolls_back_only_review_savepoint(self):
        self.db.execute("CREATE TRIGGER synthetic_fail BEFORE INSERT ON suppressions BEGIN SELECT RAISE(ABORT, 'synthetic failure'); END")
        self.db.execute('BEGIN')
        self.db.execute('INSERT INTO runs(kind,metrics) VALUES(?,?)', ('synthetic-caller', '{}'))
        with self.assertRaisesRegex(sqlite3.IntegrityError, 'synthetic failure'):
            core.review(self.db, 1, evidence(disposition='contacted'))
        self.assertTrue(self.db.in_transaction)
        self.assertEqual(len(self.latest()['reviews']), 1)
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM suppressions').fetchone()[0], 0)
        self.assertEqual(self.db.execute("SELECT COUNT(*) FROM runs WHERE kind='synthetic-caller'").fetchone()[0], 1)
        self.db.commit()
        self.assertEqual(self.db.execute("SELECT COUNT(*) FROM runs WHERE kind='synthetic-caller'").fetchone()[0], 1)

    def test_owned_transaction_failure_leaves_no_partial_contact_or_lock(self):
        self.db.execute("CREATE TRIGGER synthetic_fail BEFORE INSERT ON suppressions BEGIN SELECT RAISE(ABORT, 'synthetic failure'); END")
        with self.assertRaisesRegex(sqlite3.IntegrityError, 'synthetic failure'):
            core.review(self.db, 1, evidence(disposition='contacted'))
        self.assertFalse(self.db.in_transaction)
        self.assertEqual(len(self.latest()['reviews']), 1)
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM suppressions').fetchone()[0], 0)
        with closing(core.connect(self.path)) as other:
            other.execute('PRAGMA busy_timeout=0')
            core.review(other, 1, evidence(ownership='independent'))
        self.assertEqual(self.latest()['review']['ownership'], 'independent')

    def test_caller_savepoint_retains_control_and_its_uncommitted_merge_basis(self):
        self.db.execute('SAVEPOINT synthetic_outer')
        first = core.review(self.db, 1, evidence(ownership='independent'))
        second = core.review(self.db, 1, evidence(website_status='uncertain'))
        self.assertEqual(first['ownership'], second['ownership'])
        self.assertTrue(self.db.in_transaction)
        self.db.execute('ROLLBACK TO SAVEPOINT synthetic_outer')
        self.db.execute('RELEASE SAVEPOINT synthetic_outer')
        self.assertEqual(len(self.latest()['reviews']), 1)
        self.assertEqual(self.latest()['review']['ownership'], 'unknown')

    def test_stale_caller_wal_snapshot_conflicts_before_any_review_write(self):
        self.assertEqual(self.db.execute('PRAGMA journal_mode=WAL').fetchone()[0], 'wal')
        with closing(core.connect(self.path)) as other:
            self.db.execute('BEGIN')
            self.assertEqual(self.latest()['review']['ownership'], 'unknown')
            core.review(other, 1, evidence(ownership='independent'))
            self.db.execute('PRAGMA busy_timeout=0')
            with self.assertRaisesRegex(sqlite3.OperationalError, 'locked'):
                core.review(self.db, 1, evidence(website_status='uncertain'))
            # The failed savepoint preserves the caller's transaction/snapshot;
            # the caller can explicitly roll it back and retry against current data.
            self.assertTrue(self.db.in_transaction)
            self.assertEqual(len(self.latest(other)['reviews']), 2)
            self.db.rollback()
            saved = core.review(self.db, 1, evidence(website_status='uncertain'))
            self.assertEqual(saved['ownership'], 'independent')
            self.assertEqual(saved['website_status'], 'uncertain')
            self.assertEqual(len(self.latest()['reviews']), 3)


if __name__ == '__main__':
    unittest.main()
