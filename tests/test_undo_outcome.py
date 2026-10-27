"""Read-only operation recovery via actual local HTTP, independent of latest save."""
import json
import unittest
from contextlib import closing
from unittest.mock import patch
from urllib.parse import quote

from prospect import core, investigation
import test_undo_preview as fixtures


class UndoOutcomeTests(unittest.TestCase):
    setUp, tearDown = fixtures.UndoPreviewTests.setUp, fixtures.UndoPreviewTests.tearDown
    start, stop = fixtures.UndoPreviewTests.start, fixtures.UndoPreviewTests.stop
    request, ok = fixtures.UndoPreviewTests.request, fixtures.UndoPreviewTests.ok
    state, entry = fixtures.UndoPreviewTests.state, fixtures.UndoPreviewTests.entry
    payload, save = fixtures.UndoPreviewTests.payload, fixtures.UndoPreviewTests.save
    draft = fixtures.UndoPreviewTests.draft
    database_rows = fixtures.UndoPreviewTests.database_rows
    extra_business = fixtures.UndoPreviewTests.extra_business
    preview, confirm = fixtures.UndoPreviewTests.preview, fixtures.UndoPreviewTests.confirm
    confirm_data = fixtures.UndoPreviewTests.confirm_data
    website, correction = fixtures.UndoPreviewTests.website, fixtures.UndoPreviewTests.correction

    def outcome(self, session_id, base=None):
        return self.ok('GET', (base or self.base) + '/investigation?undo_session_id=' + quote(session_id, safe=''))

    def hold(self):
        self.ok('POST', '/api/exclusions', dict(filename='fictional.json', content=json.dumps([
            dict(phone='2025550101', reason='Fictional existing contact')])))

    def assert_current(self, snapshot, current):
        self.assertEqual({k: v for k, v in snapshot.items() if k != 'undo_outcome'}, current)

    def test_fresh_preview_targets_newer_session_and_recovers_exact_operation(self):
        older = self.website()['session_id']
        stale_desk = self.state()
        newer = self.save({'activity': self.entry(activity='active')}, 'second')['session_id']
        preview = self.preview()
        self.assertEqual(stale_desk['latest_session']['session_id'], older)
        self.assertEqual(preview['session_id'], newer)
        self.assertTrue(self.outcome(newer)['undo_outcome']['can_undo'])
        self.assertFalse(self.outcome(older)['undo_outcome']['can_undo'])
        receipt = self.confirm(preview)
        before = self.database_rows()
        recovered = self.outcome(newer)
        self.assertEqual(recovered['undo_outcome'], dict(session_id=newer, found=True, undone=True,
                         undo_result=receipt['undo_result'], can_undo=False))
        self.assert_current(recovered, self.state())
        self.assertFalse(self.outcome(older)['undo_outcome']['undone'])
        self.assertEqual(self.database_rows(), before)

    def test_old_completed_outcome_preserves_new_session_draft_and_hold_after_restart(self):
        self.website()
        preview = self.preview()
        receipt = self.confirm(preview)  # Recovery also works if this response was lost.
        newer = self.save({'website': self.entry(website_status='uncertain')}, 'later')
        draft = dict(step=3, mode='workspace', questions={'fit': {'notes': 'Newer unfinished text'}})
        self.draft(draft)
        self.hold()
        current = self.state()
        self.assertEqual(current['latest_session']['session_id'], newer['session_id'])
        self.assertEqual(current['draft'], draft)
        self.assertFalse(current['can_review'])
        self.stop()
        self.start()
        before = self.database_rows()
        for _ in range(2):
            recovered = self.outcome(preview['session_id'])
            self.assertEqual(recovered['undo_outcome']['undo_result'], receipt['undo_result'])
            self.assertTrue(recovered['undo_outcome']['undone'])
            self.assert_current(recovered, current)
        self.assertFalse(self.outcome(newer['session_id'])['undo_outcome']['can_undo'])
        self.assertEqual(self.confirm(preview), receipt)  # Existing exact POST replay remains idempotent.
        self.assertEqual(self.database_rows(), before)

    def test_missing_and_other_business_sessions_reveal_no_facts(self):
        session = self.website()['session_id']
        other = self.extra_business('Other fictional painter', None)
        before = self.database_rows()
        for sid, base in [('unknown', self.base), (session, f'/api/businesses/{other}')]:
            result = self.outcome(sid, base)
            self.assertEqual(result['undo_outcome'], dict(session_id=sid, found=False,
                             undone=None, undo_result=None, can_undo=False))
            self.assert_current(result, self.ok('GET', base + '/investigation'))
        self.assertEqual(self.database_rows(), before)

    def test_bad_query_is_400_without_mutations_and_opaque_boundaries_work(self):
        self.website()
        before = self.database_rows()
        queries = ['undo_session_id=', 'undo_session_id', 'undo_session_id=%20',
                   'undo_session_id=' + 'x' * 129, 'undo_session_id=%00',
                   'undo_session_id=a%00b', 'undo_session_id=a&undo_session_id=b',
                   'undo_session_id=a&undo_session_id=', 'undo_session_id=&undo_session_id=a']
        for query in queries:
            with self.subTest(query=query):
                self.assertEqual(self.request('GET', self.base + '/investigation?' + query)[0], 400)
        self.assertFalse(self.outcome('x' * 128)['undo_outcome']['found'])
        self.assertFalse(self.outcome('opaque ?/&= value')['undo_outcome']['found'])
        self.assertNotIn('undo_outcome', self.state())
        self.assertEqual(self.database_rows(), before)

    def test_legacy_undone_without_receipt_remains_proven_after_restart(self):
        sid = self.website()['session_id']
        self.confirm(self.preview())
        with closing(core.connect(self.workspace / 'review.sqlite3')) as db:
            with db:
                db.execute('UPDATE investigation_sessions SET undo_result=NULL,undo_payload=NULL WHERE session_id=?', (sid,))
        self.stop()
        self.start()
        before = self.database_rows()
        self.assertEqual(self.outcome(sid)['undo_outcome'], dict(session_id=sid, found=True,
                         undone=True, undo_result=None, can_undo=False))
        self.assertEqual(self.database_rows(), before)

    def test_outcome_and_all_current_details_share_one_snapshot_during_concurrent_write(self):
        sid = self.website()['session_id']
        expected = self.outcome(sid)
        original = investigation.read
        def concurrent_correction(snapshot, bid, business, **kwargs):
            with closing(core.connect(self.workspace / 'review.sqlite3')) as live:
                core.review(live, self.bid, self.entry(website_status='uncertain', disposition='contacted'))
            return original(snapshot, bid, business, **kwargs)
        with patch('prospect.investigation.read', side_effect=concurrent_correction):
            raced = self.outcome(sid)
        self.assertEqual(raced, expected)
        self.assertTrue(raced['undo_outcome']['can_undo'])
        current = self.outcome(sid)
        self.assertFalse(current['undo_outcome']['can_undo'])
        self.assertGreater(current['review_revision'], raced['review_revision'])
        self.assertFalse(current['can_review'])

    def test_failed_undo_is_not_claimed_completed_and_untouched_guard_survives(self):
        sid = self.website()['session_id']
        preview = self.preview()
        self.correction(website_status='uncertain')
        before = self.database_rows()
        self.assertEqual(self.request('POST', self.base + '/investigation/undo', self.confirm_data(preview))[0], 409)
        result = self.outcome(sid)['undo_outcome']
        self.assertTrue(result['found'])
        self.assertFalse(result['undone'])
        self.assertIsNone(result['undo_result'])
        self.assertFalse(result['can_undo'])
        self.assertEqual(self.database_rows(), before)


if __name__ == '__main__':
    unittest.main()
