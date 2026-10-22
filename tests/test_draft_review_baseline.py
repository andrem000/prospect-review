"""Synthetic regressions for saved draft review context, separate from source context."""
import json
import sqlite3
from contextlib import closing
from unittest import TestCase

from prospect import core, investigation
import test_investigation as fixtures


class DraftReviewBaselineTests(TestCase):
    setUp, tearDown = fixtures.InvestigationTests.setUp, fixtures.InvestigationTests.tearDown
    start, stop = fixtures.InvestigationTests.start, fixtures.InvestigationTests.stop
    request, ok = fixtures.InvestigationTests.request, fixtures.InvestigationTests.ok
    state, entry = fixtures.InvestigationTests.state, fixtures.InvestigationTests.entry
    payload, save = fixtures.InvestigationTests.payload, fixtures.InvestigationTests.save
    draft, counts = fixtures.InvestigationTests.draft, fixtures.InvestigationTests.counts

    def unfinished(self):
        return dict(step=1, mode='guided', questions={'website': dict(self.entry(
            website_status='no_site_found'), notes='Preserved unfinished website check')})

    def correction(self):
        with closing(core.connect(self.workspace / 'review.sqlite3')) as db:
            return core.review(db, self.bid, self.entry(website_status='uncertain'))

    def resolve(self, **fields):
        current = self.state()
        return self.request('POST', self.base + '/draft', dict(
            review_revision=current['review_revision'], draft_revision=current['draft_revision'],
            draft=current['draft'], resolve_review_revision=current['review_revision'], **fields))

    def test_reopened_draft_keeps_baseline_and_cannot_overwrite_correction(self):
        self.save({'website': self.entry(website_status='no_site_found')})
        draft = self.unfinished()
        baseline = self.draft(draft)['draft_review_revision']
        self.correction()
        self.stop()
        self.start()
        before = self.state()
        self.assertEqual(before['draft_review_revision'], baseline)
        self.assertTrue(before['review_changed'])
        self.assertFalse(before['source_changed'])
        self.assertEqual(before['draft'], draft)
        counts = self.counts()
        self.assertEqual(self.request('POST', self.base + '/investigation',
            self.payload(draft['questions'], 'reopened'))[0], 409)
        self.assertEqual(self.counts(), counts)
        self.assertEqual(self.state(), before)
        self.assertEqual(self.ok('GET', self.base)['review']['website_status'], 'uncertain')

    def test_autosave_and_read_do_not_resolve_but_explicit_resolution_does(self):
        draft = self.unfinished()
        baseline = self.draft(draft)['draft_review_revision']
        self.correction()
        draft['questions']['website']['notes'] += ' plus newer text'
        saved = self.draft(draft)
        self.assertEqual(saved['draft_review_revision'], baseline)
        self.assertTrue(saved['review_changed'])
        self.assertTrue(self.state()['review_changed'])
        status, resolved = self.resolve()
        self.assertEqual(status, 200, resolved)
        self.assertEqual(resolved['draft_review_revision'], resolved['review_revision'])
        self.assertFalse(resolved['review_changed'])
        self.assertEqual(resolved['draft'], draft)
        committed = self.save(draft['questions'])
        self.assertEqual(committed['review']['website_status'], 'no_site_found')
        self.assertIsNone(self.state()['draft_review_revision'])

    def test_resolution_requires_exact_current_revision_and_preserves_write_tokens(self):
        self.draft(self.unfinished())
        old = self.state()
        self.correction()
        current = self.state()
        for value, status in ((old['review_revision'], 409), (None, 400), (True, 400), ('1', 400), (-1, 400)):
            body = dict(review_revision=current['review_revision'], draft_revision=current['draft_revision'],
                        resolve_review_revision=value, draft=current['draft'])
            self.assertEqual(self.request('POST', self.base + '/draft', body)[0], status)
            self.assertEqual(self.state(), current)
        stale = dict(review_revision=old['review_revision'], draft_revision=old['draft_revision'],
                     resolve_review_revision=current['review_revision'], draft=old['draft'])
        self.assertEqual(self.request('POST', self.base + '/draft', stale)[0], 409)
        stale['review_revision'] = current['review_revision']
        self.assertEqual(self.resolve()[0], 200)
        self.assertEqual(self.request('POST', self.base + '/draft', stale)[0], 409)

    def test_clear_and_fresh_draft_establish_current_context(self):
        self.draft(self.unfinished())
        self.correction()
        cleared = self.draft(None)
        self.assertIsNone(cleared['draft_review_revision'])
        self.assertFalse(cleared['review_changed'])
        fresh = self.draft(self.unfinished())
        self.assertEqual(fresh['draft_review_revision'], fresh['review_revision'])
        self.assertFalse(fresh['review_changed'])

    def test_legacy_draft_remains_unknown_after_migration_restart_and_autosave(self):
        draft = self.unfinished()
        self.draft(draft)
        self.stop()
        with closing(sqlite3.connect(self.workspace / 'review.sqlite3')) as db:
            db.execute('ALTER TABLE investigation_state DROP COLUMN draft_review_revision')
            db.execute('ALTER TABLE investigation_state DROP COLUMN draft_review_revisions')
            db.execute('ALTER TABLE investigation_sessions DROP COLUMN before_draft_review_revisions')
            for field in ('before_draft_review_revision', 'before_review_revision'):
                db.execute('ALTER TABLE investigation_sessions DROP COLUMN ' + field)
            db.commit()
        self.start()
        self.assertIsNone(self.state()['draft_review_revision'])
        self.assertTrue(self.state()['review_changed'])
        self.assertEqual(self.request('POST', self.base + '/investigation',
            self.payload(draft['questions']))[0], 409)
        saved = self.draft(draft)
        self.assertIsNone(saved['draft_review_revision'])
        self.stop()
        self.start()
        self.assertIsNone(self.state()['draft_review_revision'])
        self.assertTrue(self.state()['review_changed'])
        self.assertEqual(self.resolve()[0], 200)
        self.save(draft['questions'])

    def change_source(self):
        with closing(core.connect(self.workspace / 'review.sqlite3')) as db:
            with db:
                row = db.execute('SELECT * FROM observations WHERE business_id=?', (self.bid,)).fetchone()
                data = json.loads(row['data'])
                data['websites'] = ['https://fictional-new.example.com']
                db.execute('INSERT INTO observations(business_id,batch_id,source,source_id,data) VALUES(?,?,?,?,?)',
                           (self.bid, row['batch_id'], row['source'], row['source_id'], json.dumps(data)))

    def test_source_and_review_resolution_are_independent(self):
        original = self.state()
        self.draft(self.unfinished())
        self.correction()
        self.change_source()
        current = self.state()
        self.assertTrue(current['source_changed'])
        self.assertTrue(current['review_changed'])
        # Source refresh alone must not rebase a saved-review conflict.
        source_only = self.ok('POST', self.base + '/draft', dict(
            review_revision=current['review_revision'], draft_revision=current['draft_revision'],
            source_revision=current['source_revision'], draft=current['draft']))
        self.assertFalse(source_only['source_changed'])
        self.assertTrue(source_only['review_changed'])
        self.assertEqual(self.request('POST', self.base + '/investigation',
            self.payload(current['draft']['questions']))[0], 409)
        # Restore old source context, then resolve only the saved-review conflict.
        self.assertEqual(self.resolve(source_revision=original['source_revision'])[0], 200)
        changed = self.state()
        self.assertFalse(changed['review_changed'])
        self.assertTrue(changed['source_changed'])
        self.assertEqual(self.request('POST', self.base + '/investigation',
            self.payload(current['draft']['questions']))[0], 409)

    def test_undo_preserves_newer_draft_context_and_restores_submitted_text(self):
        original = self.unfinished()
        self.draft(original)
        saved = self.save(original['questions'])
        newer = dict(step=2, mode='workspace', questions={'activity': {'notes': 'Newer partial work'}})
        baseline = self.draft(newer)['draft_review_revision']
        self.ok('POST', self.base + '/investigation/undo', dict(
            session_id=saved['session_id'], review_revision=saved['review_revision']))
        restored = self.state()
        self.assertIsNone(restored['draft_review_revision'])
        self.assertEqual(restored['draft_review_revisions']['activity'], baseline)
        self.assertEqual(restored['draft_review_revisions']['website'], restored['review_revision'])
        self.assertTrue(restored['review_changed'])
        self.assertEqual(restored['draft']['questions']['activity'], newer['questions']['activity'])
        self.assertEqual(restored['draft']['questions']['website'], original['questions']['website'])
        self.assertEqual(self.request('POST', self.base + '/investigation',
            self.payload({'activity': self.entry(activity='active')}, 'after-undo'))[0], 409)

    def test_pristine_undo_tracks_proven_restored_snapshot_and_legacy_undo_is_unknown(self):
        draft = self.unfinished()
        self.draft(draft)
        saved = self.save(draft['questions'])
        self.ok('POST', self.base + '/investigation/undo', dict(
            session_id=saved['session_id'], review_revision=saved['review_revision']))
        restored = self.state()
        self.assertFalse(restored['review_changed'])
        self.assertEqual(restored['draft_review_revision'], restored['review_revision'])
        saved = self.save(draft['questions'], 'second')
        self.stop()
        with closing(sqlite3.connect(self.workspace / 'review.sqlite3')) as db:
            db.execute('UPDATE investigation_sessions SET before_draft_review_revision=NULL,before_review_revision=NULL,before_draft_review_revisions=NULL')
            db.commit()
        self.start()
        self.ok('POST', self.base + '/investigation/undo', dict(
            session_id=saved['session_id'], review_revision=saved['review_revision']))
        self.assertIsNone(self.state()['draft_review_revision'])
        self.assertTrue(self.state()['review_changed'])

    def test_migration_failure_rolls_back_new_columns(self):
        self.stop()
        with closing(core.connect(self.workspace / 'review.sqlite3')) as db:
            for table, fields in (('investigation_state', ('draft_review_revision',)),
                                  ('investigation_sessions', ('before_draft_review_revision', 'before_review_revision'))):
                for field in fields:
                    db.execute('ALTER TABLE ' + table + ' DROP COLUMN ' + field)
            db.commit()
            def reject_final_column(action, table, field, database, trigger):
                if action == sqlite3.SQLITE_ALTER_TABLE and field == 'investigation_sessions':
                    return sqlite3.SQLITE_DENY
                return sqlite3.SQLITE_OK
            db.set_authorizer(reject_final_column)
            with self.assertRaises(sqlite3.DatabaseError):
                investigation.initialize(db)
            db.set_authorizer(None)
            self.assertNotIn('draft_review_revision', {r['name'] for r in db.execute('PRAGMA table_info(investigation_state)')})
        self.start()
