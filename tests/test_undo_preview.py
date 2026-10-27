"""Synthetic preview/confirm Undo tests; no external services or real contacts."""
import json
import sqlite3
import unittest
from contextlib import closing
from unittest.mock import patch

from prospect import app, core, investigation
import test_investigation as fixtures
import test_draft_review_baseline as baseline_fixtures
import test_unfinished_reviews as unfinished_fixtures


class UndoPreviewTests(unittest.TestCase):
    setUp, tearDown = fixtures.InvestigationTests.setUp, fixtures.InvestigationTests.tearDown
    start, stop = fixtures.InvestigationTests.start, fixtures.InvestigationTests.stop
    request, ok = fixtures.InvestigationTests.request, fixtures.InvestigationTests.ok
    state, entry = fixtures.InvestigationTests.state, fixtures.InvestigationTests.entry
    payload, save = fixtures.InvestigationTests.payload, fixtures.InvestigationTests.save
    draft, counts = fixtures.InvestigationTests.draft, fixtures.InvestigationTests.counts
    change_source = baseline_fixtures.DraftReviewBaselineTests.change_source
    database_rows = unfinished_fixtures.UnfinishedReviewTests.database_rows
    extra_business = unfinished_fixtures.UnfinishedReviewTests.extra_business

    def preview(self):
        return self.ok('GET', self.base + '/investigation/undo-preview')

    def confirm_data(self, preview):
        return {key: preview[key] for key in ('session_id', 'review_revision', 'draft_revision',
                                              'source_revision', 'preview_token')} | dict(
                                                  keep_questions=preview['overlap_questions'])

    def confirm(self, preview):
        return self.ok('POST', self.base + '/investigation/undo', self.confirm_data(preview))

    def website(self):
        return self.save({'website': self.entry(website_status='no_site_found')})

    def correction(self, **fields):
        with closing(core.connect(self.workspace / 'review.sqlite3')) as db:
            return core.review(db, self.bid, self.entry(**fields))

    def assert_stale_unchanged(self, preview):
        before = self.database_rows()
        self.assertEqual(self.request('POST', self.base + '/investigation/undo', self.confirm_data(preview))[0], 409)
        self.assertEqual(self.database_rows(), before)

    def test_preview_cancel_repeated_read_write_nothing_and_no_session_is_ineligible(self):
        before = self.database_rows()
        empty = self.preview()
        self.assertFalse(empty['eligible'])
        self.assertIsNone(empty['preview_token'])
        self.assertIsNone(empty['session_id'])
        self.assertEqual(self.database_rows(), before)
        self.website()
        before = self.database_rows()
        first = self.preview()
        self.assertTrue(first['eligible'])
        self.assertEqual(first, self.preview())
        self.assertEqual(first['before_saved_answers'], {})
        self.assertFalse(first['before_context_unknown'])
        self.assertEqual(first['submitted_questions'], first['restored_draft_questions'])
        self.assertEqual(first['overlap_questions'], [])
        self.assertEqual(first['retained_draft_questions'], {})
        self.assertEqual(self.database_rows(), before)  # Closing/cancelling has no mutation endpoint.

    def test_confirm_first_answer_restores_unchecked_and_persists_receipt(self):
        self.website()
        preview = self.preview()
        result = self.confirm(preview)
        summary = result['undo_result']
        self.assertEqual(summary['restored_decisions'], ['website'])
        self.assertEqual(summary['session_restored_questions'], ['website'])
        self.assertEqual(summary['retained_questions'], [])
        state = self.state()
        self.assertEqual(state['saved_answers'], {})
        self.assertEqual(state['draft']['questions'], preview['submitted_questions'])
        self.assertEqual(state['latest_session']['undo_result'], summary)
        self.assertEqual(len(self.ok('GET', self.base)['reviews']), 2)
        self.assertIsNone(self.preview()['preview_token'])
        self.stop()
        self.start()
        self.assertEqual(self.state(), state)
        before = self.database_rows()
        self.assertEqual(self.confirm(preview), result)
        self.assertEqual(self.database_rows(), before)

    def test_partial_save_unrelated_and_overlapping_drafts_survive_restart(self):
        website = self.entry(website_status='no_site_found')
        fit = {'notes': 'Earlier unresolved fit work'}
        self.draft(dict(step=3, mode='guided', questions=dict(website=website, fit=fit)))
        self.save({'website': website})
        newer = dict(step=2, mode='workspace', questions=dict(
            website=dict(website, notes='Newer overlapping website note'),
            fit=fit, activity={'notes': 'Newer unrelated activity work'}))
        self.draft(newer)
        before = self.state()
        preview = self.preview()
        self.assertEqual(preview['overlap_questions'], ['website'])
        self.assertEqual(preview['unrelated_questions'], ['activity', 'fit'])
        self.assertEqual(preview['restored_draft_questions'], {})
        self.assertEqual(preview['retained_draft_questions'], newer['questions'])
        self.assertEqual(preview['current_draft_context']['draft_review_revisions'], before['draft_review_revisions'])
        result = self.confirm(preview)
        summary = result['undo_result']
        self.assertEqual(summary['overlap_questions'], ['website'])
        self.assertEqual(summary['session_restored_questions'], [])
        self.assertEqual(summary['retained_questions'], ['website', 'activity', 'fit'])
        state = self.state()
        self.assertEqual(state['draft'], newer)
        self.assertEqual(state['draft_review_revisions']['website'], before['draft_review_revisions']['website'])
        self.assertEqual(state['draft_review_revisions']['activity'], before['draft_review_revisions']['activity'])
        self.assertEqual(state['draft_review_revisions']['fit'], state['review_revision'])
        self.assertTrue(state['draft_conflicts']['website']['changed'])
        self.assertFalse(state['draft_conflicts']['fit']['changed'])
        self.stop()
        self.start()
        self.assertEqual(self.state(), state)

    def test_unrelated_earlier_draft_and_session_restored_text_keep_context(self):
        website = self.entry(website_status='no_site_found')
        fit = {'notes': 'Older fit draft'}
        self.draft(dict(step=3, mode='workspace', questions=dict(website=website, fit=fit)))
        self.save({'website': website})
        preview = self.preview()
        self.assertEqual(preview['unrelated_questions'], ['fit'])
        self.assertEqual(preview['overlap_questions'], [])
        result = self.confirm(preview)
        self.assertEqual(result['undo_result']['session_restored_questions'], ['website'])
        self.assertEqual(result['undo_result']['retained_questions'], ['fit'])
        self.assertEqual(self.state()['draft']['questions'], dict(website=website, fit=fit))
        self.assertFalse(self.state()['review_changed'])

    def test_overlap_requires_exact_explicit_keep_set_and_bounded_fields(self):
        self.website()
        self.draft(dict(step=1, mode='workspace', questions=dict(website={'notes': 'Keep this'})))
        preview = self.preview()
        valid = self.confirm_data(preview)
        cases = [dict(valid, keep_questions=[]), dict(valid, keep_questions=['website', 'fit']),
                 dict(valid, keep_questions=['website', 'website']), dict(valid, keep_questions='website'),
                 dict(valid, keep_questions=[{}]), dict(valid, keep_questions=['other']),
                 dict(valid, draft_revision=True), dict(valid, source_revision='bad'),
                 dict(valid, preview_token='bad')]
        cases += [{k: v for k, v in valid.items() if k != field} for field in
                  ('keep_questions', 'draft_revision', 'source_revision')]
        for body in cases:
            with self.subTest(body=body):
                before = self.database_rows()
                self.assertEqual(self.request('POST', self.base + '/investigation/undo', body)[0], 400)
                self.assertEqual(self.database_rows(), before)
        self.assertEqual(self.confirm(preview)['undo_result']['overlap_questions'], ['website'])

    def test_stale_draft_preview_rejects_without_writes(self):
        self.website()
        preview = self.preview()
        self.draft(dict(step=2, mode='workspace', questions=dict(activity={'notes': 'New draft'})))
        self.assert_stale_unchanged(preview)
        self.assertNotEqual(self.preview()['preview_token'], preview['preview_token'])

    def test_stale_source_preview_rejects_and_fresh_undo_does_not_accept_source(self):
        self.website()
        preview = self.preview()
        self.change_source()
        self.assert_stale_unchanged(preview)
        fresh = self.preview()
        self.assertTrue(fresh['eligible'])
        result = self.confirm(fresh)
        self.assertTrue(result['undo_result']['source_changed'])
        self.assertEqual(result['undo_result']['draft_source_revision'], preview['source_revision'])
        self.assertNotEqual(result['undo_result']['source_revision'], preview['source_revision'])

    def test_later_review_makes_preview_and_latest_session_ineligible(self):
        self.website()
        preview = self.preview()
        self.correction(website_status='uncertain')
        self.assert_stale_unchanged(preview)
        latest = self.preview()
        self.assertFalse(latest['eligible'])
        self.assertIsNone(latest['preview_token'])

    def test_new_contact_exclusion_invalidates_preview_without_erasing_hold(self):
        self.website()
        preview = self.preview()
        self.ok('POST', '/api/exclusions', dict(filename='fictional.json', content=json.dumps([
            dict(phone='2025550101', reason='Fictional existing contact')])))
        self.assert_stale_unchanged(preview)
        fresh = self.preview()
        self.assertFalse(fresh['eligible'])
        self.assertFalse(fresh['can_review'])
        self.assertIn('suppressed_or_contacted', fresh['excluded_reasons'])
        self.assertIsNone(fresh['preview_token'])

    def test_cross_business_token_and_wrong_session_are_rejected(self):
        self.website()
        preview = self.preview()
        other = self.extra_business('Other fictional painter', dict(step=0, mode='workspace', questions={}))
        state = self.ok('GET', f'/api/businesses/{other}/investigation')
        result = self.ok('POST', f'/api/businesses/{other}/investigation', dict(
            review_revision=state['review_revision'], draft_revision=state['draft_revision'], request_id='other',
            questions={'website': self.entry(website_status='no_site_found')}))
        other_preview = self.ok('GET', f'/api/businesses/{other}/investigation/undo-preview')
        body = dict(self.confirm_data(other_preview), preview_token=preview['preview_token'])
        before = self.database_rows()
        self.assertEqual(self.request('POST', f'/api/businesses/{other}/investigation/undo', body)[0], 409)
        self.assertEqual(self.database_rows(), before)
        body = dict(self.confirm_data(preview), session_id=result['session_id'])
        self.assertEqual(self.request('POST', self.base + '/investigation/undo', body)[0], 409)
        self.assertEqual(self.database_rows(), before)

    def test_legacy_pre_session_context_remains_unknown_across_restart(self):
        self.correction(activity='unknown')
        self.website()
        self.stop()
        with closing(core.connect(self.workspace / 'review.sqlite3')) as db:
            with db:
                db.execute('UPDATE investigation_sessions SET before_review_revision=NULL, before_draft_review_revision=NULL, before_draft_review_revisions=NULL')
        self.start()
        preview = self.preview()
        self.assertTrue(preview['before_context_unknown'])
        self.assertIsNone(preview['before_saved_answers'])
        self.assertIsNone(preview['before_review_revision'])
        result = self.confirm(preview)
        self.assertTrue(result['undo_result']['before_context_unknown'])
        self.assertTrue(result['undo_result']['review_context_unknown'])
        self.assertIsNone(self.state()['draft_review_revisions']['website'])
        self.assertIn('activity', self.state()['saved_answers'])

    def test_prior_explicit_unknown_activity_and_cli_unknown_website_basis_preview(self):
        self.correction(activity='unknown')
        self.website()
        self.correction(website_status='uncertain')
        before = self.state()['saved_answers']
        self.save({'website': self.entry(website_status='no_site_found')}, 'second')
        preview = self.preview()
        self.assertEqual(preview['before_saved_answers'], before)
        self.assertFalse(preview['before_saved_answers']['website']['basis_known'])
        self.assertTrue(preview['before_saved_answers']['activity']['basis_known'])
        self.confirm(preview)
        self.assertEqual(self.state()['saved_answers'], before)

    def test_changed_basis_with_same_value_is_a_restored_decision(self):
        self.website()
        self.save({'website': dict(self.entry(website_status='no_site_found'), notes='Second check')}, 'second')
        preview = self.preview()
        self.assertNotEqual(preview['before_saved_answers'], preview['current_saved_answers'])
        self.assertEqual(self.confirm(preview)['undo_result']['restored_decisions'], ['website'])

    def test_exact_retry_after_newer_draft_review_and_session_never_reapplies(self):
        self.website()
        preview = self.preview()
        original = self.confirm(preview)
        self.draft(None)
        self.correction(activity='unknown')
        self.save({'website': self.entry(website_status='uncertain')}, 'new-session')
        self.draft(dict(step=3, mode='workspace', questions=dict(fit={'notes': 'Most recent input'})))
        before = self.database_rows()
        self.assertEqual(self.confirm(preview), original)
        self.assertEqual(self.database_rows(), before)
        changed = dict(self.confirm_data(preview), keep_questions=['website'])
        self.assertEqual(self.request('POST', self.base + '/investigation/undo', changed)[0], 409)
        self.assertEqual(self.database_rows(), before)

    def test_transaction_failure_after_draft_write_rolls_back_everything(self):
        self.website()
        preview = self.preview()
        before = self.database_rows()
        original = investigation.write_state
        def fail_after_write(*args, **kwargs):
            original(*args, **kwargs)
            raise RuntimeError('Synthetic transaction failure')
        with closing(core.connect(self.workspace / 'review.sqlite3')) as db:
            with patch('prospect.investigation.write_state', side_effect=fail_after_write):
                with self.assertRaisesRegex(RuntimeError, 'Synthetic transaction failure'):
                    investigation.undo(db, self.bid, self.confirm_data(preview), {})
        self.assertEqual(self.database_rows(), before)
        self.assertEqual(self.preview(), preview)
        self.confirm(preview)

    def test_preview_reads_single_snapshot_when_another_writer_corrects_review(self):
        self.website()
        original = investigation.undo_preview
        def concurrent_correction(snapshot, bid, business):
            self.correction(website_status='uncertain')
            return original(snapshot, bid, business)
        expected = self.preview()
        with patch('prospect.investigation.undo_preview', side_effect=concurrent_correction):
            stale = self.preview()
        self.assertEqual(stale, expected)
        self.assert_stale_unchanged(stale)

    def test_confirmation_recomputes_token_under_sqlite_writer_lock(self):
        self.website()
        preview = self.preview()
        original = investigation._undo_snapshot
        def check_lock(db, bid):
            self.assertTrue(db.in_transaction)
            with closing(sqlite3.connect(self.workspace / 'review.sqlite3', timeout=0)) as external:
                with self.assertRaises(sqlite3.OperationalError):
                    external.execute('BEGIN IMMEDIATE')
            return original(db, bid)
        with patch('prospect.investigation._undo_snapshot', side_effect=check_lock):
            self.confirm(preview)

    def test_old_api_remains_compatible_and_non_idempotent(self):
        result = self.website()
        body = dict(session_id=result['session_id'], review_revision=result['review_revision'])
        undone = self.ok('POST', self.base + '/investigation/undo', body)
        self.assertIn('undo_result', undone)
        self.assertEqual(self.request('POST', self.base + '/investigation/undo', body)[0], 409)
        self.assertEqual(self.state()['latest_session']['undo_result'], undone['undo_result'])

    def test_additive_receipt_migration_does_not_rewrite_existing_reviews(self):
        self.website()
        reviews = self.ok('GET', self.base)['reviews']
        self.stop()
        with closing(core.connect(self.workspace / 'review.sqlite3')) as db:
            with db:
                db.execute('ALTER TABLE investigation_sessions DROP COLUMN undo_payload')
                db.execute('ALTER TABLE investigation_sessions DROP COLUMN undo_result')
        self.start()
        self.assertEqual(self.ok('GET', self.base)['reviews'], reviews)
        self.assertIsNone(self.state()['latest_session']['undo_result'])
        self.confirm(self.preview())


if __name__ == '__main__':
    unittest.main()
