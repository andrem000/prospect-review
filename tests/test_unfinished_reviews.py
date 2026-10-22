"""Synthetic worklist, per-question comparison and partial-save regressions."""
import json
import sqlite3
from contextlib import closing
from unittest import TestCase
from unittest.mock import patch

from prospect import core
import test_investigation as fixtures
import test_draft_review_baseline as baseline_fixtures


class UnfinishedReviewTests(TestCase):
    setUp, tearDown = fixtures.InvestigationTests.setUp, fixtures.InvestigationTests.tearDown
    start, stop = fixtures.InvestigationTests.start, fixtures.InvestigationTests.stop
    request, ok = fixtures.InvestigationTests.request, fixtures.InvestigationTests.ok
    state, entry = fixtures.InvestigationTests.state, fixtures.InvestigationTests.entry
    payload, save = fixtures.InvestigationTests.payload, fixtures.InvestigationTests.save
    draft, counts = fixtures.InvestigationTests.draft, fixtures.InvestigationTests.counts
    change_source = baseline_fixtures.DraftReviewBaselineTests.change_source

    def mixed(self):
        return dict(step=2, mode='guided', questions={
            'website': dict(self.entry(website_status='no_site_found'), notes='Retain website draft'),
            'activity': dict(self.entry(activity='active'), notes='Retain activity draft')})

    def correction(self):
        return self.ok('POST', self.base + '/review', dict(changes=self.entry(website_status='uncertain')))

    def resolve(self, actions, draft=None):
        current = self.state()
        return self.ok('POST', self.base + '/draft', dict(
            review_revision=current['review_revision'], draft_revision=current['draft_revision'],
            resolve_review_revision=current['review_revision'], resolve_questions=actions,
            draft=current['draft'] if draft is None else draft))

    def database_rows(self):
        with closing(core.connect(self.workspace / 'review.sqlite3')) as db:
            return {r[0]: [tuple(row) for row in db.execute('SELECT * FROM "' + r[0] + '" ORDER BY rowid')]
                    for r in db.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")}

    def extra_business(self, name, draft):
        with closing(core.connect(self.workspace / 'review.sqlite3')) as db:
            with db:
                bid = db.execute('INSERT INTO businesses(created_at) VALUES(?)', (core.now(),)).lastrowid
                original = db.execute('SELECT * FROM observations LIMIT 1').fetchone()
                data = json.loads(original['data'])
                data.update(name=name, phones=[], city='Dallas')
                db.execute('INSERT INTO observations(business_id,batch_id,source,source_id,data) VALUES(?,?,?,?,?)',
                           (bid, original['batch_id'], original['source'], str(bid), json.dumps(data)))
        self.ok('POST', f'/api/businesses/{bid}/draft', dict(review_revision=0, draft_revision=0, draft=draft))
        return bid

    def test_worklist_search_pagination_navigation_and_contact_hold_are_read_only(self):
        self.draft(self.mixed())
        self.extra_business('Navigation Only', dict(step=3, mode='workspace', questions={}))
        self.extra_business('Empty Entry', dict(step=0, mode='guided', questions={'website': {}}))
        self.extra_business('Blank Entry', dict(step=0, mode='guided', questions={'website': {'notes': '  ', 'website_status': ''}}))
        second = self.extra_business('A Fictional Painter', dict(step=0, mode='workspace', questions={'fit': {'notes': 'Unfinished'}}))
        self.ok('POST', '/api/exclusions', dict(filename='fictional.json', content=json.dumps([
            {'phone': '2025550101', 'reason': 'fictional existing contact'}])))
        before = self.database_rows()
        first = self.ok('GET', '/api/drafts?limit=1')
        self.assertEqual(first['total'], 2)
        self.assertEqual(first['items'][0]['id'], second)
        page = self.ok('GET', '/api/drafts?limit=1&offset=1')
        held = page['items'][0]
        self.assertEqual(held['id'], self.bid)
        self.assertFalse(held['can_review'])
        self.assertEqual(held['questions'], ['website', 'activity'])
        self.assertEqual(held['question_labels'], ['Website', 'Activity'])
        self.assertEqual((held['step'], held['mode']), (2, 'guided'))
        self.assertFalse(held['review_changed'])
        self.assertFalse(held['review_context_unknown'])
        self.assertEqual(self.ok('GET', '/api/drafts?q=DALLAS')['total'], 1)
        self.assertEqual(self.ok('GET', '/api/drafts?q=TX')['total'], 2)
        self.assertEqual(self.ok('GET', '/api/drafts?offset=200')['items'], [])
        for query in ('limit=0', 'limit=101', 'offset=-1', 'offset=bad'):
            self.assertEqual(self.request('GET', '/api/drafts?' + query)[0], 400)
        self.assertEqual(self.database_rows(), before)
        self.assertEqual(self.state()['draft'], self.mixed())
        self.assertEqual(self.request('POST', self.base + '/investigation', self.payload(
            {'website': self.mixed()['questions']['website']}))[0], 400)

    def test_worklist_uses_one_pinned_snapshot(self):
        self.draft(self.mixed())
        original = self.server.app.evaluated
        changed = False
        def concurrent_correction(snapshot):
            nonlocal changed
            if not changed:
                changed = True
                with closing(core.connect(self.workspace / 'review.sqlite3')) as writer:
                    core.review(writer, self.bid, self.entry(website_status='uncertain'))
            return original(snapshot)
        with patch.object(self.server.app, 'evaluated', side_effect=concurrent_correction):
            pinned = self.ok('GET', '/api/drafts')['items'][0]
        self.assertFalse(pinned['review_changed'])
        self.assertTrue(self.ok('GET', '/api/drafts')['items'][0]['review_changed'])

    def test_explicit_unknown_answer_counts_as_unfinished_work(self):
        self.draft(dict(step=2, mode='workspace', questions={'activity': {'activity': 'unknown'}}))
        work = self.ok('GET', '/api/drafts')
        self.assertEqual(work['total'], 1)
        self.assertEqual(work['items'][0]['questions'], ['activity'])

    def test_comparison_shows_proven_historical_basis_and_current_answer(self):
        original = self.entry(website_status='no_site_found')
        first = self.save({'website': original})
        self.draft(self.mixed())
        self.correction()
        state = self.state()
        conflict = state['draft_conflicts']['website']
        self.assertEqual(conflict['baseline_revision'], first['review_revision'])
        self.assertEqual(conflict['current_revision'], state['review_revision'])
        self.assertEqual(conflict['baseline_saved_answer']['answers']['website_status'], 'no_site_found')
        self.assertEqual(conflict['baseline_saved_answer']['notes'], original['notes'])
        self.assertTrue(conflict['baseline_saved_answer']['basis_known'])
        self.assertEqual(conflict['current_saved_answer']['answers']['website_status'], 'uncertain')
        self.assertFalse(conflict['current_saved_answer']['basis_known'])
        self.assertTrue(conflict['changed'])
        self.assertFalse(conflict['unknown'])
        self.assertIsNone(state['draft_conflicts']['activity']['baseline_saved_answer'])

    def test_same_value_later_review_still_requires_deliberate_resolution(self):
        self.save({'website': self.entry(website_status='no_site_found')})
        self.draft(self.mixed())
        self.ok('POST', self.base + '/review', dict(changes=self.entry(website_status='no_site_found')))
        conflict = self.state()['draft_conflicts']['website']
        self.assertEqual(conflict['baseline_saved_answer']['answers'], conflict['current_saved_answer']['answers'])
        self.assertTrue(conflict['changed'])
        self.assertEqual(self.request('POST', self.base + '/investigation',
            self.payload({'website': self.mixed()['questions']['website']}))[0], 409)

    def test_keep_rebases_only_selected_question_current_removes_only_selected_edit(self):
        original = self.mixed()
        self.draft(original)
        self.correction()
        before = self.state()
        resolved = self.resolve({'website': 'keep'})
        self.assertEqual(resolved['draft'], original)
        self.assertEqual(resolved['draft_review_revisions']['activity'], before['draft_review_revisions']['activity'])
        self.assertEqual(resolved['draft_review_revisions']['website'], before['review_revision'])
        self.assertFalse(resolved['draft_conflicts']['website']['changed'])
        self.assertTrue(resolved['draft_conflicts']['activity']['changed'])
        current = self.resolve({'website': 'current'})
        self.assertEqual(current['draft']['questions'], {'activity': original['questions']['activity']})
        self.assertTrue(current['draft_conflicts']['activity']['changed'])
        self.assertEqual(self.counts()[0], 1)
        self.assertEqual(self.ok('GET', self.base)['review']['website_status'], 'uncertain')

    def test_partial_commit_persists_unsubmitted_text_baselines_source_and_retry_result(self):
        original = self.mixed()
        self.draft(original)
        old = self.state()
        self.correction()
        self.resolve({'website': 'keep'})
        payload = self.payload({'website': original['questions']['website']}, 'partial')
        saved = self.ok('POST', self.base + '/investigation', payload)
        self.assertEqual(saved['draft']['questions'], {'activity': original['questions']['activity']})
        self.assertEqual(saved['draft_review_revisions'], {'activity': old['draft_review_revisions']['activity']})
        self.assertEqual(saved['draft_source_revision'], old['draft_source_revision'])
        self.assertTrue(saved['review_changed'])
        self.stop()
        self.start()
        restored = self.state()
        self.assertEqual(restored['draft'], saved['draft'])
        self.assertEqual(restored['draft_review_revisions'], saved['draft_review_revisions'])
        self.assertEqual(self.ok('POST', self.base + '/investigation', payload), saved)
        self.assertEqual(self.request('POST', self.base + '/investigation',
            self.payload(restored['draft']['questions'], 'unresolved-activity'))[0], 409)
        self.resolve({'activity': 'keep'})
        self.save(restored['draft']['questions'], 'resolved-activity')
        self.assertIsNone(self.state()['draft'])

    def test_source_resolution_and_question_resolution_do_not_certify_each_other(self):
        self.draft(self.mixed())
        before = self.state()
        self.correction()
        self.change_source()
        self.resolve({'website': 'keep'})
        resolved = self.state()
        self.assertTrue(resolved['source_changed'])
        self.assertFalse(resolved['draft_conflicts']['website']['changed'])
        self.assertTrue(resolved['draft_conflicts']['activity']['changed'])
        self.assertEqual(self.request('POST', self.base + '/investigation', self.payload(
            {'website': self.mixed()['questions']['website']}))[0], 409)
        current = self.state()
        self.ok('POST', self.base + '/draft', dict(review_revision=current['review_revision'], draft_revision=current['draft_revision'],
            source_revision=current['source_revision'], draft=current['draft']))
        self.assertTrue(self.state()['draft_conflicts']['activity']['changed'])
        # A commit can explicitly compare current source while preserving the
        # older source context for the unsubmitted activity draft.
        current = self.state()
        self.ok('POST', self.base + '/draft', dict(review_revision=current['review_revision'], draft_revision=current['draft_revision'],
            source_revision=before['source_revision'], draft=current['draft']))
        body = self.payload({'website': self.mixed()['questions']['website']}, 'source-partial')
        body['source_revision'] = current['source_revision']
        saved = self.ok('POST', self.base + '/investigation', body)
        self.assertEqual(saved['draft_source_revision'], before['source_revision'])
        self.assertTrue(saved['source_changed'])

    def test_migration_carries_proven_scalar_and_keeps_legacy_unknown(self):
        self.draft(self.mixed())
        known = self.state()['review_revision']
        other = self.extra_business('Unknown Context', self.mixed())
        self.stop()
        with closing(sqlite3.connect(self.workspace / 'review.sqlite3')) as db:
            db.execute('UPDATE investigation_state SET draft_review_revision=NULL WHERE business_id=?', (other,))
            db.execute('ALTER TABLE investigation_state DROP COLUMN draft_review_revisions')
            db.execute('ALTER TABLE investigation_sessions DROP COLUMN before_draft_review_revisions')
            db.commit()
        self.start()
        self.assertEqual(self.state()['draft_review_revisions'], {'website': known, 'activity': known})
        unknown = self.ok('GET', f'/api/businesses/{other}/investigation')
        self.assertEqual(unknown['draft_review_revisions'], {'website': None, 'activity': None})
        self.assertTrue(unknown['review_context_unknown'])
        self.assertTrue(all(c['unknown'] and c['changed'] for c in unknown['draft_conflicts'].values()))
        self.assertIsNone(unknown['draft_conflicts']['website']['baseline_saved_answer'])
        self.assertTrue(next(i for i in self.ok('GET', '/api/drafts')['items'] if i['id'] == other)['review_context_unknown'])

    def test_partial_commit_undo_preserves_mixed_context_and_all_text(self):
        original = self.mixed()
        self.draft(original)
        self.correction()
        self.resolve({'website': 'keep'})
        before = self.state()
        saved = self.save({'website': original['questions']['website']}, 'partial-undo')
        self.ok('POST', self.base + '/investigation/undo', dict(session_id=saved['session_id'], review_revision=saved['review_revision']))
        restored = self.state()
        self.assertEqual(restored['draft']['questions'], original['questions'])
        self.assertEqual(restored['draft_review_revisions']['activity'], before['draft_review_revisions']['activity'])
        self.assertEqual(restored['draft_review_revisions']['website'], restored['review_revision'])
        self.assertTrue(restored['draft_conflicts']['activity']['changed'])
        self.assertFalse(restored['draft_conflicts']['website']['changed'])
        self.assertEqual(self.ok('GET', self.base)['review']['website_status'], 'uncertain')

    def test_resolution_validation_races_and_failed_commit_preserve_draft_context(self):
        self.draft(self.mixed())
        old = self.state()
        self.correction()
        current = self.state()
        base = dict(review_revision=current['review_revision'], draft_revision=current['draft_revision'],
                    draft=current['draft'], resolve_review_revision=current['review_revision'])
        for actions in ({}, {'website': 'defer'}, {'other': 'keep'}, [], {'fit': 'keep'}):
            self.assertEqual(self.request('POST', self.base + '/draft', dict(base, resolve_questions=actions))[0], 400)
        body = dict(base, resolve_questions={'website': 'keep'}, resolve_review_revision=old['review_revision'])
        self.assertEqual(self.request('POST', self.base + '/draft', body)[0], 409)
        self.assertEqual(self.state(), current)
        self.resolve({'website': 'keep'})
        before = self.state()
        invalid = dict(self.mixed()['questions']['website'], notes=' ')
        self.assertEqual(self.request('POST', self.base + '/investigation', self.payload({'website': invalid}))[0], 400)
        self.assertEqual(self.state(), before)
