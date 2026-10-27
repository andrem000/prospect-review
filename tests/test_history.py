"""Synthetic HTTP history checks and frozen v0.8.1 read-model parity."""
import json
import unittest
from contextlib import closing
from unittest.mock import patch
from urllib.parse import quote

from prospect import core, history, review_state
import test_undo_outcome as fixtures


def legacy_saved(rows):
    """Frozen pre-extraction semantics from 2a9b1d6, including final activity basis."""
    bases, reviewed, previous, before_sessions = {}, set(), {}, {}
    for row in rows:
        undo = row.get('investigation_undo')
        session = None if undo else row.get('investigation_session')
        question = None if undo else row.get('investigation_question')
        if session and question in review_state.FIELDS and session not in before_sessions:
            before_sessions[session] = (dict(bases), set(reviewed))
        for key in review_state.FIELDS:
            current = review_state.answers(row, key)
            if any(v != core.REVIEW_DEFAULTS.get(f) and v is not None for f, v in current.items()):
                reviewed.add(key)
            if current != review_state.answers(previous, key):
                bases.pop(key, None)
        if undo:
            before = before_sessions.get(undo)
            prior_bases, prior_reviewed = before if before is not None else ({}, reviewed)
            bases = {q: entry for q, entry in prior_bases.items()
                     if review_state.answers(entry, q) == review_state.answers(row, q)}
            reviewed = set(prior_reviewed)
        if row.get('activity_asserted') is True:
            reviewed.add('activity')
        if question in review_state.FIELDS and session:
            bases[question] = {key: row.get(key, core.REVIEW_DEFAULTS.get(key))
                               for key in (*review_state.FIELDS[question], *review_state.EVIDENCE)}
            reviewed.add(question)
        previous = row
    effective = rows[-1] if rows else {}
    if effective.get('activity_asserted') is True:
        reviewed.add('activity')
    if 'activity' in reviewed and all(f in effective for f in core.ACTIVITY_EVIDENCE_FIELDS):
        bases['activity'] = dict(activity=effective.get('activity'),
            evidence_observed_at=effective['activity_evidence_observed_at'],
            evidence_url=effective['activity_evidence_url'], notes=effective['activity_evidence_notes'])
    return {q: dict(answers=review_state.answers(effective, q),
                    **{key: (bases.get(q) or {}).get(key) for key in review_state.EVIDENCE}, basis_known=q in bases)
            for q in review_state.FIELDS if q in reviewed}


class HistoryTests(unittest.TestCase):
    setUp, tearDown = fixtures.UndoOutcomeTests.setUp, fixtures.UndoOutcomeTests.tearDown
    start, stop = fixtures.UndoOutcomeTests.start, fixtures.UndoOutcomeTests.stop
    request, ok = fixtures.UndoOutcomeTests.request, fixtures.UndoOutcomeTests.ok
    state, entry = fixtures.UndoOutcomeTests.state, fixtures.UndoOutcomeTests.entry
    payload, save = fixtures.UndoOutcomeTests.payload, fixtures.UndoOutcomeTests.save
    draft, database_rows = fixtures.UndoOutcomeTests.draft, fixtures.UndoOutcomeTests.database_rows
    extra_business = fixtures.UndoOutcomeTests.extra_business
    preview, confirm = fixtures.UndoOutcomeTests.preview, fixtures.UndoOutcomeTests.confirm
    confirm_data = fixtures.UndoOutcomeTests.confirm_data
    website = fixtures.UndoOutcomeTests.website
    hold = fixtures.UndoOutcomeTests.hold

    def read(self, query='', base=None):
        return self.ok('GET', (base or self.base) + '/history' + ('?' + query if query else ''))

    def correction(self, **fields):
        with closing(core.connect(self.workspace / 'review.sqlite3')) as db:
            return core.review(db, self.bid, dict(self.entry(), **fields))

    def lookup(self, event_id, base=None):
        return self.read('event_id=' + quote(event_id, safe=''), base)

    def rows(self):
        with closing(core.connect(self.workspace / 'review.sqlite3')) as db:
            return [{**json.loads(row['data']), 'created_at': row['created_at']}
                    for row in db.execute('SELECT * FROM reviews WHERE business_id=? ORDER BY id', (self.bid,))]

    def test_empty_history_and_queries_validate_without_writing(self):
        before = self.database_rows()
        self.assertEqual(self.read(), dict(business_id=self.bid, history_revision=0, question='all',
                         offset=0, limit=20, total=0, has_more=False, items=[]))
        queries = ['question=', 'question=other', 'question=all&question=fit', 'offset=', 'offset=-1',
                   'offset=1.2', 'offset=1e2', 'limit=0', 'limit=51', 'limit=', 'limit=2&limit=3',
                   'event_id=', 'event_id=review:0', 'event_id=session:bad', 'event_id=review:1%00',
                   'event_id=review:1&question=all', 'event_id=review:1&offset=0',
                   'event_id=review:1&limit=1', 'event_id=review:1&event_id=review:2', 'unknown=1']
        for query in queries:
            with self.subTest(query=query):
                self.assertEqual(self.request('GET', self.base + '/history?' + query)[0], 400)
        self.assertEqual(self.request('GET', '/api/businesses/99999/history')[0], 404)
        self.assertEqual(self.request('POST', self.base + '/history', {})[0], 404)
        self.assertEqual(self.database_rows(), before)

    def test_multi_question_save_proven_grouping_tied_times_and_recorded_vs_observed(self):
        with patch('prospect.core.now', return_value='2026-10-01T01:02:03+00:00'):
            first = self.save({'website': self.entry(website_status='no_site_found'),
                               'activity': self.entry(activity='unknown')})
            second = self.save({'identity': self.entry(identity_status='same_business')}, 'second')
        result = self.read()
        self.assertEqual(result['total'], 2)
        self.assertEqual([e['session_id'] for e in result['items']], [second['session_id'], first['session_id']])
        event = result['items'][1]
        self.assertEqual(event['kind'], 'investigation_save')
        self.assertEqual(event['origin'], 'guided')
        self.assertEqual(event['asserted_questions'], ['website', 'activity'])
        self.assertEqual(event['before'], {})
        self.assertEqual(event['after']['activity']['answers'], {'activity': 'unknown'})
        self.assertTrue(event['after']['activity']['basis_known'])
        self.assertEqual(len(event['review_ids']), 2)
        self.assertEqual(event['recorded_at'], '2026-10-01T01:02:03+00:00')
        self.assertEqual(event['record_evidence'][0]['evidence_observed_at'], '2026-01-02')
        self.assertTrue(event['record_evidence'][0]['association_known'])
        self.assertNotIn('draft', event)

    def test_partial_save_same_value_new_basis_and_generic_unknown_filter_survive(self):
        self.draft(dict(step=3, mode='workspace', questions={'website': self.entry(website_status='no_site_found'),
                                                           'fit': {'notes': 'Unfinished fit text'}}))
        first = self.website()
        second = self.save({'website': dict(self.entry(website_status='no_site_found'),
                                            evidence_url='https://example.com/new', evidence_observed_at='2026-01-03')}, 'second')
        self.correction(notes='Generic same-value record', evidence_observed_at='2026-01-04')
        before = self.database_rows()
        current = self.state()
        result = self.read('question=activity')
        self.assertEqual(result['total'], 1)
        generic = result['items'][0]
        self.assertEqual(generic['kind'], 'saved_review')
        self.assertEqual(generic['origin'], 'unknown')
        self.assertEqual(generic['asserted_questions'], [])
        self.assertTrue(generic['association_unknown'])
        self.assertEqual(generic['matches_filter'], 'unknown')
        self.assertIsNone(generic['record_evidence'][0]['question'])
        self.assertFalse(generic['record_evidence'][0]['association_known'])
        event = self.lookup('session:' + second['session_id'])['items'][0]
        self.assertEqual(event['changed_questions'], [])
        self.assertEqual(event['asserted_questions'], ['website'])
        self.assertEqual(event['before']['website']['evidence_observed_at'], '2026-01-02')
        self.assertEqual(event['after']['website']['evidence_observed_at'], '2026-01-03')
        self.assertEqual(self.lookup('session:' + first['session_id'])['total'], 1)
        self.assertEqual(self.state(), current)
        self.assertEqual(self.database_rows(), before)

    def test_undo_classified_before_inherited_metadata_links_original_after_new_session_restart(self):
        older = self.website()
        self.save({'activity': self.entry(activity='active')}, 'second')
        receipt = self.confirm(self.preview())
        self.save({'website': self.entry(website_status='uncertain')}, 'later')
        self.draft(dict(step=3, mode='workspace', questions={'fit': {'notes': 'Private newer draft text'}}))
        self.hold()
        current = self.state()
        self.stop()
        self.start()
        before = self.database_rows()
        result = self.read('limit=1&offset=1')
        undo = result['items'][0]
        self.assertEqual(undo['kind'], 'undo')
        self.assertEqual(undo['id'], 'review:' + str(receipt['review_revision']))
        self.assertEqual(undo['original_event_id'], 'session:' + receipt['session_id'])
        self.assertNotEqual(undo['original_event_id'], 'session:' + older['session_id'])
        original = self.lookup(undo['original_event_id'])
        self.assertEqual(original['selected_event_id'], undo['original_event_id'])
        self.assertEqual(original['total'], 1)
        self.assertEqual(original['limit'], 1)
        self.assertFalse(original['has_more'])
        self.assertEqual(original['items'][0]['asserted_questions'], ['activity'])
        self.assertEqual(undo['undo_result']['restored_decisions'], receipt['undo_result']['restored_decisions'])
        self.assertNotIn('draft', undo['undo_result'])
        self.assertNotIn('draft_conflicts', undo['undo_result'])
        self.assertNotIn('Private newer draft text', json.dumps(self.read()))
        self.assertEqual(self.state(), current)
        self.assertEqual(self.database_rows(), before)

    def test_first_save_undo_restores_unchecked_and_legacy_receipt_unknown(self):
        saved = self.website()
        receipt = self.confirm(self.preview())
        event = self.read()['items'][0]
        self.assertEqual(event['before']['website']['answers']['website_status'], 'no_site_found')
        self.assertEqual(event['after'], {})
        self.assertEqual(event['changed_questions'], ['website'])
        self.assertEqual(event['original_event_id'], 'session:' + saved['session_id'])
        with closing(core.connect(self.workspace / 'review.sqlite3')) as db:
            with db:
                db.execute('UPDATE investigation_sessions SET undo_result=NULL WHERE session_id=?', (saved['session_id'],))
        self.stop()
        self.start()
        legacy = self.lookup('review:' + str(receipt['review_revision']))['items'][0]
        self.assertEqual(legacy['kind'], 'undo')
        self.assertIsNone(legacy['undo_result'])
        self.assertEqual(legacy['after'], {})

    def test_scoped_direct_lookup_and_long_history_pages_are_read_only(self):
        first = self.website()
        for n in range(24):
            self.correction(notes='Synthetic generic save ' + str(n))
        other = self.extra_business('Other fictional painter', None)
        before = self.database_rows()
        page = self.read()
        self.assertEqual(page['total'], 25)
        self.assertEqual(len(page['items']), 20)
        self.assertTrue(page['has_more'])
        end = self.read('offset=20&limit=20')
        self.assertEqual(len(end['items']), 5)
        self.assertFalse(end['has_more'])
        ids = [e['last_revision'] for e in page['items'] + end['items']]
        self.assertEqual(ids, sorted(ids, reverse=True))
        self.assertEqual(self.read('offset=999')['items'], [])
        for event_id, base in [('review:999999', self.base), ('session:' + first['session_id'], f'/api/businesses/{other}')]:
            result = self.lookup(event_id, base)
            self.assertEqual(result['total'], 0)
            self.assertEqual(result['items'], [])
            self.assertFalse(result['has_more'])
        self.assertEqual(self.database_rows(), before)

    def test_missing_session_row_metadata_and_noncontiguous_session_remain_ungrouped(self):
        first = self.save({'website': self.entry(website_status='no_site_found'),
                           'activity': self.entry(activity='active')})
        with closing(core.connect(self.workspace / 'review.sqlite3')) as db:
            with db:
                # A duplicate proven-looking row means grouping can no longer be certified.
                row = db.execute('SELECT * FROM reviews ORDER BY id LIMIT 1').fetchone()
                db.execute('INSERT INTO reviews(business_id,created_at,data) VALUES(?,?,?)',
                           (self.bid, row['created_at'], row['data']))
        result = self.read()
        self.assertEqual(result['total'], 3)
        self.assertTrue(all(e['kind'] == 'saved_review' and e['association_unknown'] for e in result['items']))
        self.assertEqual(self.lookup('session:' + first['session_id'])['total'], 0)
        with closing(core.connect(self.workspace / 'review.sqlite3')) as db:
            with db:
                db.execute('DELETE FROM investigation_sessions WHERE session_id=?', (first['session_id'],))
        self.assertTrue(all(e['origin'] == 'unknown' for e in self.read()['items']))

    def test_generic_activity_flag_is_not_proof_of_new_assertion_and_other_changes_visible(self):
        self.save({'activity': self.entry(activity='unknown')})
        self.correction(notes='Only generic note changed', queue_hold=True, disposition='rejected')
        event = self.read()['items'][0]
        self.assertEqual(event['asserted_questions'], [])
        self.assertEqual(event['changed_questions'], [])
        self.assertEqual(event['other_changes'], [dict(field='disposition', before='pending', after='rejected'),
                                                 dict(field='queue_hold', before=False, after=True)])
        self.assertTrue(event['after']['activity']['basis_known'])
        self.assertEqual(event['after']['activity']['evidence_observed_at'], '2026-01-02')

    def test_interrupted_session_rows_never_group_even_with_matching_count_and_final_revision(self):
        saved = self.save({'website': self.entry(website_status='no_site_found'),
                           'activity': self.entry(activity='active')})
        self.correction(notes='Intervening generic record')
        with closing(core.connect(self.workspace / 'review.sqlite3')) as db:
            with db:
                db.execute('UPDATE reviews SET id=4 WHERE id=2')
                db.execute('UPDATE investigation_sessions SET final_revision=4 WHERE session_id=?', (saved['session_id'],))
        events = self.read()['items']
        self.assertEqual(len(events), 3)
        self.assertTrue(all(e['kind'] == 'saved_review' for e in events))
        self.assertEqual([e['last_revision'] for e in events], [4, 3, 1])
        self.assertEqual(self.lookup('session:' + saved['session_id'])['total'], 0)

    def test_receipt_must_match_undo_revision_and_lookup_cannot_read_another_business_review(self):
        saved = self.website()
        receipt = self.confirm(self.preview())
        event_id = 'review:' + str(receipt['review_revision'])
        other = self.extra_business('Other fictional painter', None)
        self.assertEqual(self.lookup(event_id, f'/api/businesses/{other}')['items'], [])
        with closing(core.connect(self.workspace / 'review.sqlite3')) as db:
            with db:
                changed = dict(receipt, review_revision=999999)
                db.execute('UPDATE investigation_sessions SET undo_result=? WHERE session_id=?',
                           (json.dumps(changed), saved['session_id']))
        event = self.lookup(event_id)['items'][0]
        self.assertEqual(event['kind'], 'undo')
        self.assertIsNone(event['undo_result'])
        self.assertEqual(event['original_event_id'], 'session:' + saved['session_id'])

    def test_legacy_generic_unknown_basis_and_missing_original_stay_explicit(self):
        self.correction(website_status='uncertain')
        with closing(core.connect(self.workspace / 'review.sqlite3')) as db:
            with db:
                raw = json.loads(db.execute('SELECT data FROM reviews LIMIT 1').fetchone()[0])
                raw.pop('activity_asserted', None)
                for key in core.ACTIVITY_EVIDENCE_FIELDS:
                    raw.pop(key, None)
                db.execute('UPDATE reviews SET data=?', (json.dumps(raw),))
                # Legacy compensating record with no provable original session.
                db.execute('INSERT INTO reviews(business_id,created_at,data) VALUES(?,?,?)',
                           (self.bid, core.now(), json.dumps(dict(raw, investigation_undo='missing-original'))))
        events = self.read()['items']
        self.assertEqual(events[0]['kind'], 'undo')
        self.assertIsNone(events[0]['original_event_id'])
        self.assertIsNone(events[0]['undo_result'])
        self.assertTrue(events[0]['association_unknown'])
        self.assertEqual(events[1]['origin'], 'unknown')
        self.assertFalse(events[1]['after']['website']['basis_known'])
        self.assertIsNone(events[1]['after']['website']['evidence_observed_at'])
        self.assertEqual(events[1]['record_evidence'][0]['evidence_observed_at'], '2026-01-02')

    def test_snapshot_race_keeps_revision_page_and_receipts_together(self):
        self.website()
        expected = self.read()
        original = history.read
        def concurrent_correction(snapshot, bid, options):
            self.correction(website_status='uncertain')
            return original(snapshot, bid, options)
        with patch('prospect.app.history.read', side_effect=concurrent_correction):
            raced = self.read()
        self.assertEqual(raced, expected)
        current = self.read()
        self.assertGreater(current['history_revision'], raced['history_revision'])
        self.assertEqual(current['total'], 2)

    def test_incremental_replay_matches_frozen_pre_extraction_semantics_every_prefix(self):
        self.save({'website': self.entry(website_status='no_site_found'),
                   'activity': self.entry(activity='unknown')})
        self.correction(website_status='uncertain')
        self.save({'identity': self.entry(identity_status='same_business'),
                   'fit': self.entry(ownership='independent', residential='yes', service_fit='yes', delivery='mobile')}, 'second')
        self.confirm(self.preview())
        self.save({'website': self.entry(website_status='no_site_found')}, 'third')
        self.correction(activity='closed')
        rows = self.rows()
        # Legacy Undo with an unavailable session boundary must keep conservative unknown basis.
        rows.append(dict(rows[-1], investigation_undo='missing-session', website_status='uncertain'))
        replay = review_state.Replay()
        self.assertEqual(replay.saved(), legacy_saved([]))
        for index, row in enumerate(rows, 1):
            replay.advance(row)
            expected = legacy_saved(rows[:index])
            self.assertEqual(replay.saved(), expected, index)
            self.assertEqual(review_state.saved_answers(dict(reviews=rows[:index], review=row)), expected, index)
            # Retrieving snapshots must not alter pre-session basis state.
            self.assertEqual(replay.saved(), expected, index)


if __name__ == '__main__':
    unittest.main()
