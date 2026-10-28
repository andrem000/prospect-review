"""Trusted insertion context, conservative history, and unchanged replay semantics."""
import io
import json
import unittest
from contextlib import closing, redirect_stdout

from prospect import core, history, review_state
from prospect.__main__ import main
import test_history as fixtures


class CorrectionProvenanceTests(unittest.TestCase):
    for _name in ('setUp', 'tearDown', 'start', 'stop', 'request', 'ok', 'state', 'entry',
                  'payload', 'save', 'draft', 'database_rows', 'extra_business', 'preview',
                  'confirm', 'confirm_data', 'website', 'hold', 'read', 'lookup', 'rows'):
        locals()[_name] = getattr(fixtures.HistoryTests, _name)

    def correct(self, route=None, replace=False, **fields):
        with closing(core.connect(self.workspace / 'review.sqlite3')) as db:
            return core.review(db, self.bid, self.entry(**fields), replace, recording_route=route)

    def latest(self):
        return self.read()['items'][0]

    def test_core_strips_forged_and_never_inherits_envelopes(self):
        fake = dict(version=1, route='advanced', mode='merge', supplied_fields=['website_status'])
        result = self.correct(correction_provenance=fake, website_status='uncertain')
        self.assertNotIn('correction_provenance', result)
        self.assertIsNone(self.latest()['correction_provenance'])
        result = self.correct('cli', correction_provenance=fake, ownership='unknown')
        self.assertEqual(result['correction_provenance'], dict(version=1, route='cli', mode='merge',
            supplied_fields=['evidence_observed_at', 'evidence_url', 'notes', 'ownership']))
        self.correct()
        self.assertIsNone(self.latest()['correction_provenance'])
        before = self.database_rows()
        with self.assertRaises(ValueError):
            self.correct('forged')
        self.assertEqual(self.database_rows(), before)

    def test_cli_records_explicit_defaults_and_omissions_before_merge(self):
        self.correct(website_status='uncertain', ownership='independent')
        args = ['--db', str(self.workspace / 'review.sqlite3'), 'review', str(self.bid),
                '--activity', 'unknown', '--evidence-url', 'https://example.com/cli', '--notes', 'CLI check']
        with redirect_stdout(io.StringIO()):
            self.assertEqual(main(args), 0)
        event = self.latest()
        self.assertEqual(event['origin'], 'cli')
        self.assertEqual(event['correction_provenance']['supplied_fields'], ['activity', 'evidence_url', 'notes'])
        self.assertEqual(event['after']['website']['answers']['website_status'], 'uncertain')
        self.assertEqual(event['asserted_questions'], [])
        self.assertEqual(self.read('question=identity')['total'], 1)  # Earlier unknown record only.
        self.assertEqual(self.read('question=activity')['total'], 2)

    def test_http_trusts_server_route_and_rejects_metadata_inside_changes(self):
        changes = self.entry(website_status='unreviewed', ownership='unknown')
        fake = dict(version=1, route='cli', mode='replace', supplied_fields=['activity'])
        result = self.ok('POST', self.base + '/review', dict(changes=changes, origin='cli',
                         recording_route='cli', correction_provenance=fake))
        self.assertEqual(result['review']['correction_provenance'], dict(version=1, route='advanced',
                         mode='merge', supplied_fields=sorted(changes)))
        event = self.latest()
        self.assertEqual(event['origin'], 'advanced')
        self.assertFalse(event['association_unknown'])
        self.assertEqual(event['asserted_questions'], [])
        self.assertFalse(event['record_evidence'][0]['association_known'])
        self.assertIsNone(event['record_evidence'][0]['question'])
        before = self.database_rows()
        self.assertEqual(self.request('POST', self.base + '/review',
            dict(changes=dict(changes, correction_provenance=fake)))[0], 400)
        self.assertEqual(self.database_rows(), before)
        self.assertEqual(self.read('question=website')['total'], 1)
        self.assertEqual(self.read('question=fit')['total'], 1)
        self.assertEqual(self.read('question=identity')['total'], 0)

    def test_notes_only_and_same_value_mixed_fields_filter_precisely(self):
        self.correct('advanced')
        self.assertEqual(self.read()['total'], 1)
        for question in review_state.FIELDS:
            self.assertEqual(self.read('question=' + question)['total'], 0)
        self.correct('advanced', website_status='unreviewed', delivery='unknown')
        event = self.latest()
        self.assertEqual(event['changed_questions'], [])
        self.assertEqual(self.read('question=website')['total'], 1)
        self.assertEqual(self.read('question=fit')['total'], 1)
        self.assertEqual(self.read('question=activity')['total'], 0)

    def test_replace_resets_omitted_decisions_and_keeps_supplied_distinct(self):
        self.correct('cli', website_status='uncertain', ownership='independent', activity='active')
        self.correct('advanced', replace=True, identity_status='unknown')
        event = self.latest()
        self.assertEqual(event['correction_provenance']['mode'], 'replace')
        self.assertNotIn('website_status', event['correction_provenance']['supplied_fields'])
        self.assertEqual(set(event['changed_questions']), {'website', 'activity', 'fit'})
        for question in review_state.FIELDS:
            self.assertEqual(self.read('question=' + question)['items'][0]['id'], event['id'])
        self.assertEqual(event['after'], fixtures.legacy_saved(self.rows()))
        self.assertFalse(self.rows()[-1]['activity_asserted'])

    def test_strict_validator_fails_closed_without_crashing(self):
        valid = dict(version=1, route='cli', mode='merge', supplied_fields=['notes'])
        cases = [None, [], 'cli', {}, dict(valid, version=True), dict(valid, version=2),
                 dict(valid, route=[]), dict(valid, route='guided'), dict(valid, mode={}),
                 dict(valid, supplied_fields='notes'), dict(valid, supplied_fields=[{}]),
                 dict(valid, supplied_fields=['notes', 'notes']), dict(valid, supplied_fields=['notes', 'activity']),
                 dict(valid, supplied_fields=['investigation_question']), dict(valid, extra=True)]
        self.correct()
        with closing(core.connect(self.workspace / 'review.sqlite3')) as db:
            row = db.execute('SELECT id,data FROM reviews ORDER BY id DESC LIMIT 1').fetchone()
            data = json.loads(row['data'])
            for value in cases:
                with self.subTest(value=value), db:
                    db.execute('UPDATE reviews SET data=? WHERE id=?',
                               (json.dumps(dict(data, correction_provenance=value)), row['id']))
                event = self.latest()
                self.assertIsNone(event['correction_provenance'])
                self.assertEqual(event['origin'], 'unknown')
                self.assertTrue(event['association_unknown'])
                self.assertEqual(self.read('question=identity')['items'][0]['matches_filter'], 'unknown')
        self.assertEqual(history.correction_provenance(valid), valid)

    def test_guided_and_undo_override_inherited_envelope(self):
        self.correct('cli', website_status='uncertain')
        first = self.website()
        # Simulate a historical inherited envelope without altering guided proof.
        with closing(core.connect(self.workspace / 'review.sqlite3')) as db, db:
            row = db.execute('SELECT id,data FROM reviews ORDER BY id DESC LIMIT 1').fetchone()
            data = json.loads(row['data'])
            data['correction_provenance'] = dict(version=1, route='cli', mode='merge', supplied_fields=['ownership'])
            db.execute('UPDATE reviews SET data=? WHERE id=?', (json.dumps(data), row['id']))
        event = self.latest()
        self.assertEqual(event['origin'], 'guided')
        self.assertIsNone(event['correction_provenance'])
        self.confirm(self.preview())
        with closing(core.connect(self.workspace / 'review.sqlite3')) as db, db:
            row = db.execute('SELECT id,data FROM reviews ORDER BY id DESC LIMIT 1').fetchone()
            data = json.loads(row['data'])
            data['correction_provenance'] = dict(version=1, route='advanced', mode='replace', supplied_fields=['ownership'])
            db.execute('UPDATE reviews SET data=? WHERE id=?', (json.dumps(data), row['id']))
        event = self.latest()
        self.assertEqual(event['kind'], 'undo')
        self.assertEqual(event['origin'], 'guided')
        self.assertIsNone(event['correction_provenance'])
        self.assertEqual(event['original_event_id'], 'session:' + first['session_id'])
        with closing(core.connect(self.workspace / 'review.sqlite3')) as db, db:
            data['investigation_undo'] = 'unproven-original'
            db.execute('UPDATE reviews SET data=? WHERE id=?', (json.dumps(data), row['id']))
        event = self.latest()
        self.assertEqual(event['kind'], 'undo')
        self.assertEqual(event['origin'], 'unknown')
        self.assertIsNone(event['correction_provenance'])
        self.assertTrue(event['association_unknown'])

    def test_prefix_parity_drafts_restart_read_only_and_generic_retries(self):
        self.website()
        self.correct('cli', activity='active')
        changes = dict(self.entry(ownership='independent'), notes='Repeat generic request')
        for _ in range(2):
            self.ok('POST', self.base + '/review', dict(changes=changes))
        self.assertNotEqual(self.read()['items'][0]['id'], self.read()['items'][1]['id'])
        self.draft(dict(step=3, mode='workspace', questions={'fit': {'notes': 'Unfinished newer text'}}))
        current, before = self.state(), self.database_rows()
        rows = self.rows()
        for event in self.read()['items']:
            self.assertEqual(event['before'], fixtures.legacy_saved(rows[:event['first_revision'] - 1]))
            self.assertEqual(event['after'], fixtures.legacy_saved(rows[:event['last_revision']]))
        for question in ('all', *review_state.FIELDS):
            self.read('question=' + question)
        self.assertEqual(self.database_rows(), before)
        self.stop()
        self.start()
        self.assertEqual(self.state(), current)
        self.assertEqual(self.database_rows(), before)


if __name__ == '__main__':
    unittest.main()
