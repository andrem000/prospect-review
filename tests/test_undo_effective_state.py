"""Effective checkedness is restored by Undo; historical assertions remain history."""
import json
import unittest
from contextlib import closing

from prospect import core, review_state
import test_investigation as fixtures


class UndoEffectiveStateTests(unittest.TestCase):
    setUp, tearDown = fixtures.InvestigationTests.setUp, fixtures.InvestigationTests.tearDown
    start, stop = fixtures.InvestigationTests.start, fixtures.InvestigationTests.stop
    request, ok = fixtures.InvestigationTests.request, fixtures.InvestigationTests.ok
    state, entry = fixtures.InvestigationTests.state, fixtures.InvestigationTests.entry
    payload, save = fixtures.InvestigationTests.payload, fixtures.InvestigationTests.save
    draft, counts = fixtures.InvestigationTests.draft, fixtures.InvestigationTests.counts

    def undo(self, result):
        return self.ok('POST', self.base + '/investigation/undo', dict(
            session_id=result['session_id'], review_revision=result['review_revision']))

    def correction(self, **fields):
        with closing(core.connect(self.workspace / 'review.sqlite3')) as db:
            return core.review(db, self.bid, self.entry(**fields))

    def test_first_website_undo_removes_answer_and_reminder_preserving_draft_history(self):
        submitted = {'website': self.entry(website_status='no_site_found')}
        self.assertEqual(self.state()['saved_answers'], {})
        result = self.save(submitted)
        original = self.ok('GET', self.base)['reviews']
        self.undo(result)
        state = self.state()
        self.assertEqual(state['saved_answers'], {})
        self.assertEqual(state['questions'], {})
        self.assertEqual(state['draft']['questions'], submitted)
        history = self.ok('GET', self.base)['reviews']
        self.assertEqual(history[:-1], original)
        self.assertEqual(history[-1]['investigation_undo'], result['session_id'])
        changes = self.ok('GET', '/api/changes')
        self.assertFalse([r for item in changes['items'] if item['business_id'] == self.bid
                          for r in item['reasons'] if r['question'] == 'website'])
        self.stop()
        self.start()
        self.assertEqual(self.state(), state)

    def test_prior_guided_answers_and_exact_basis_are_restored(self):
        self.save({'website': self.entry(website_status='uncertain')}, 'prior')
        before = self.state()['saved_answers']
        result = self.save({'website': dict(self.entry(website_status='no_site_found'),
                                           evidence_observed_at='2026-02-03')}, 'latest')
        self.undo(result)
        self.assertEqual(self.state()['saved_answers'], before)

    def test_cli_correction_prior_to_session_retains_unknown_basis(self):
        self.save({'website': self.entry(website_status='no_site_found')}, 'prior')
        self.correction(website_status='uncertain', candidate_website='https://candidate.example.com')
        before = self.state()['saved_answers']
        self.assertFalse(before['website']['basis_known'])
        self.undo(self.save({'website': self.entry(website_status='no_site_found')}, 'latest'))
        self.assertEqual(self.state()['saved_answers'], before)

    def test_explicit_unknown_activity_before_session_keeps_marker_and_basis(self):
        self.correction(activity='unknown')
        before = self.state()['saved_answers']
        self.assertTrue(before['activity']['basis_known'])
        self.undo(self.save({'website': self.entry(website_status='uncertain')}))
        self.assertEqual(self.state()['saved_answers'], before)
        self.assertTrue(self.ok('GET', self.base)['review']['activity_asserted'])

    def test_first_explicit_unknown_activity_undo_returns_to_unchecked(self):
        result = self.save({'activity': self.entry(activity='unknown')})
        self.assertIn('activity', self.state()['saved_answers'])
        self.undo(result)
        self.assertNotIn('activity', self.state()['saved_answers'])

    def test_partial_save_undo_preserves_unrelated_draft_and_prior_answer(self):
        self.correction(activity='active')
        before = self.state()['saved_answers']
        fit = {'notes': 'Unresolved fit draft'}
        website = self.entry(website_status='no_site_found')
        self.draft(dict(step=1, mode='workspace', questions=dict(website=website, fit=fit)))
        result = self.save({'website': website})
        self.undo(result)
        state = self.state()
        self.assertEqual(state['saved_answers'], before)
        self.assertEqual(state['draft']['questions'], dict(website=website, fit=fit))
        self.assertEqual(set(state['draft_review_revisions']), {'website', 'fit'})

    def test_later_cli_correction_after_undo_is_still_a_saved_check(self):
        self.undo(self.save({'website': self.entry(website_status='no_site_found')}))
        self.correction(website_status='uncertain')
        answer = self.state()['saved_answers']['website']
        self.assertEqual(answer['answers']['website_status'], 'uncertain')
        self.assertFalse(answer['basis_known'])
        self.assertIsNone(answer['evidence_observed_at'])

    def test_legacy_active_before_session_is_preserved_without_invented_basis(self):
        legacy = dict(core.REVIEW_DEFAULTS, activity='active', evidence_url='https://example.com/legacy',
                      notes='Unattributed legacy evidence')
        raw = json.dumps(legacy)
        with closing(core.connect(self.workspace / 'review.sqlite3')) as db:
            with db:
                db.execute('INSERT INTO reviews(business_id,created_at,data) VALUES(?,?,?)',
                           (self.bid, '2026-01-01', raw))
        self.undo(self.save({'activity': self.entry(activity='closed')}))
        saved = self.state()['saved_answers']['activity']
        self.assertEqual(saved['answers'], dict(activity='active'))
        self.assertFalse(saved['basis_known'])
        self.assertIsNone(saved['evidence_observed_at'])
        with closing(core.connect(self.workspace / 'review.sqlite3')) as db:
            self.assertEqual(db.execute('SELECT data FROM reviews ORDER BY id LIMIT 1').fetchone()[0], raw)

    def test_unknown_legacy_activity_is_not_inferred_from_generic_evidence(self):
        legacy = dict(core.REVIEW_DEFAULTS, evidence_url='https://example.com/legacy', notes='Legacy unknown',
                      activity_evidence_observed_at='2026-01-01', activity_evidence_url='https://example.com/legacy',
                      activity_evidence_notes='No assertion marker')
        first = dict(core.REVIEW_DEFAULTS, activity='active', investigation_session='session',
                     investigation_question='activity')
        undone = dict(legacy, investigation_undo='session')
        self.assertEqual(review_state.saved_answers(dict(reviews=[legacy, first, undone], review=undone)), {})

    def test_missing_legacy_session_boundary_retains_check_with_unknown_basis(self):
        first = dict(core.REVIEW_DEFAULTS, website_status='uncertain')
        undone = dict(core.REVIEW_DEFAULTS, investigation_undo='missing')
        saved = review_state.saved_answers(dict(reviews=[first, undone], review=undone))
        self.assertIn('website', saved)
        self.assertFalse(saved['website']['basis_known'])


if __name__ == '__main__':
    unittest.main()
