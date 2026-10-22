"""End-to-end synthetic change inbox and source-context preservation checks."""
import json
import sqlite3
import tempfile
import threading
from contextlib import closing
from pathlib import Path
from unittest import TestCase
from unittest.mock import patch

from prospect import core, app, investigation, changes
import test_app as app_tests


class ChangeAppTests(TestCase):
    setUp, tearDown = app_tests.AppTests.setUp, app_tests.AppTests.tearDown
    start, stop, request, ok = (app_tests.AppTests.start, app_tests.AppTests.stop,
                               app_tests.AppTests.request, app_tests.AppTests.ok)
    row, preview, rights, imported = (app_tests.AppTests.row, app_tests.AppTests.preview,
                                    app_tests.AppTests.rights, app_tests.AppTests.imported)

    def reimport(self, **fields):
        row = self.row()
        row.update(fields)
        preview = self.preview([row])
        compared = self.ok('POST', '/api/import/compare', dict(preview_id=preview['preview_id'], source='fictional'))
        return self.ok('POST', '/api/import/commit', dict(preview_id=preview['preview_id'],
                       rights=self.rights(), comparison_id=compared['comparison_id']))

    def review(self, bid):
        return self.ok('POST', f'/api/businesses/{bid}/review', {'changes': dict(
            website_status='no_site_found', activity='active', evidence_observed_at='2026-01-01',
            evidence_url='https://example.com/fictional', notes='Synthetic saved evidence')})

    def test_preview_import_triage_and_repeated_import_preserve_review(self):
        bid = self.imported()
        review = self.review(bid)['review']
        preview = self.preview([self.row(websites=['https://cedar.example'])])
        before = self.ok('GET', f'/api/businesses/{bid}')
        comparison = self.ok('POST', '/api/import/compare', dict(preview_id=preview['preview_id'], source='fictional'))
        self.assertEqual(comparison['summary']['changed_businesses'], 1)
        self.assertEqual(self.ok('GET', f'/api/businesses/{bid}'), before)
        self.ok('POST', '/api/import/commit', dict(preview_id=preview['preview_id'],
                comparison_id=comparison['comparison_id'], rights=self.rights()))
        item = self.ok('GET', '/api/changes')['items'][0]
        listing = [r for r in item['reasons'] if r['id'].startswith('change:')]
        self.assertEqual(len(listing), 1)
        self.assertEqual(listing[0]['fields'][0]['field'], 'websites')
        self.ok('POST', '/api/changes/seen', dict(reason_ids=[listing[0]['id']], seen=True))
        self.reimport(websites=['https://cedar.example/'])
        self.stop()
        self.start()
        listing2 = [r for r in self.ok('GET', '/api/changes')['items'][0]['reasons'] if r['id'].startswith('change:')]
        self.assertEqual([r['id'] for r in listing2], [listing[0]['id']])
        self.assertTrue(listing2[0]['seen'])
        after = self.ok('GET', f'/api/businesses/{bid}')
        self.assertEqual(after['review_history_count'], 1)
        for key, value in review.items():
            self.assertEqual(after['review'][key], value)
        self.ok('POST', '/api/changes/seen', dict(reason_ids=[listing[0]['id']], seen=False))
        self.assertFalse([r for r in self.ok('GET', '/api/changes')['items'][0]['reasons'] if r['id'] == listing[0]['id']][0]['seen'])

    def test_comparison_rejects_changed_source_or_newer_review(self):
        bid = self.imported()
        preview = self.preview([self.row(websites=['https://cedar.example'])])
        compared = self.ok('POST', '/api/import/compare', dict(preview_id=preview['preview_id'], source='fictional'))
        body = dict(preview_id=preview['preview_id'], comparison_id=compared['comparison_id'], rights=self.rights())
        body['rights']['source'] = 'other'
        self.assertEqual(self.request('POST', '/api/import/commit', body)[0], 409)
        body['rights']['source'] = 'fictional'
        self.review(bid)
        self.assertEqual(self.request('POST', '/api/import/commit', body)[0], 409)
        compared = self.ok('POST', '/api/import/compare', dict(preview_id=preview['preview_id'], source='fictional'))
        body['comparison_id'] = compared['comparison_id']
        self.ok('POST', '/api/import/commit', body)
        self.assertEqual(self.ok('GET', f'/api/businesses/{bid}')['review_history_count'], 1)

    def test_draft_context_survives_reimport_and_requires_explicit_refresh(self):
        bid = self.imported()
        base = f'/api/businesses/{bid}'
        state = self.ok('GET', base + '/investigation')
        draft = dict(step=1, mode='workspace', questions={'website': dict(website_status='uncertain',
            evidence_observed_at='2026-01-01', evidence_url='https://example.com/fictional', notes='Keep my unfinished work')})
        saved = self.ok('POST', base + '/draft', dict(review_revision=state['review_revision'],
            draft_revision=state['draft_revision'], source_revision=state['source_revision'], draft=draft))
        self.reimport(websites=['https://cedar.example'])
        changed = self.ok('GET', base + '/investigation')
        self.assertTrue(changed['source_changed'])
        self.assertEqual(changed['draft'], draft)
        body = dict(review_revision=changed['review_revision'], draft_revision=changed['draft_revision'],
                    source_revision=state['source_revision'], request_id='source-check', questions=draft['questions'])
        self.assertEqual(self.request('POST', base + '/investigation', body)[0], 409)
        # Autosave may persist old-context input; only the decision save is blocked.
        saved = self.ok('POST', base + '/draft', dict(review_revision=changed['review_revision'],
            draft_revision=changed['draft_revision'], source_revision=state['source_revision'], draft=draft))
        body.update(draft_revision=saved['draft_revision'], source_revision=changed['source_revision'])
        committed = self.ok('POST', base + '/investigation', body)
        self.reimport(websites=['https://second.example'])
        self.ok('POST', base + '/investigation/undo', dict(session_id=committed['session_id'],
                review_revision=committed['review_revision']))
        restored = self.ok('GET', base + '/investigation')
        self.assertTrue(restored['source_changed'])
        self.assertEqual(restored['draft']['questions'], draft['questions'])

    def test_material_reversion_does_not_revive_old_review_context(self):
        bid = self.imported()
        base = f'/api/businesses/{bid}'
        initial = self.ok('GET', base + '/investigation')['source_revision']
        self.reimport(websites=['https://cedar.example'])
        self.reimport(websites=[])
        self.assertNotEqual(self.ok('GET', base + '/investigation')['source_revision'], initial)
        self.assertEqual(self.request('POST', base + '/review', {'source_revision': initial, 'changes': dict(
            website_status='uncertain', evidence_url='https://example.com/fictional', notes='Synthetic')})[0], 409)

    def test_undo_cannot_certify_old_answers_from_a_newer_draft(self):
        bid = self.imported()
        base = f'/api/businesses/{bid}'
        initial = self.ok('GET', base + '/investigation')
        website = dict(website_status='no_site_found', evidence_url='https://example.com/old',
                       evidence_observed_at='2026-01-01', notes='Original synthetic website check')
        committed = self.ok('POST', base + '/investigation', dict(review_revision=initial['review_revision'],
            draft_revision=initial['draft_revision'], source_revision=initial['source_revision'],
            request_id='old-website', questions={'website': website}))
        # Also exercise migration of a v0.4 session with no recorded source basis.
        self.stop()
        with closing(sqlite3.connect(self.workspace / 'review.sqlite3')) as db:
            db.execute('UPDATE investigation_sessions SET source_revision=NULL')
            db.commit()
        self.start()
        self.reimport(websites=['https://cedar.example'])
        current = self.ok('GET', base + '/investigation')
        self.ok('POST', base + '/draft', dict(review_revision=current['review_revision'],
            draft_revision=current['draft_revision'], source_revision=current['source_revision'],
            draft=dict(step=0, mode='workspace', questions={'identity': {'notes': 'New-context draft'}})))
        self.ok('POST', base + '/investigation/undo', dict(session_id=committed['session_id'],
                review_revision=committed['review_revision']))
        restored = self.ok('GET', base + '/investigation')
        self.assertTrue(restored['source_changed'])
        self.assertEqual(restored['draft']['questions']['website'], website)
        self.assertEqual(restored['draft']['questions']['identity']['notes'], 'New-context draft')

    def test_external_review_during_staging_is_never_overwritten(self):
        bid = self.imported()
        preview = self.preview([self.row(websites=['https://cedar.example'])])
        original = core.import_data

        def concurrent_review(staged, *args):
            metrics = original(staged, *args)
            with closing(core.connect(self.workspace / 'review.sqlite3')) as writer:
                core.review(writer, bid, dict(website_status='uncertain', notes='Newer external work',
                                             evidence_url='https://example.com/external'))
            return metrics

        with patch('prospect.app.core.import_data', side_effect=concurrent_review):
            self.assertEqual(self.request('POST', '/api/import/commit',
                dict(preview_id=preview['preview_id'], rights=self.rights()))[0], 409)
        business = self.ok('GET', f'/api/businesses/{bid}')
        self.assertEqual(business['review']['notes'], 'Newer external work')
        self.assertEqual(len(business['observations']), 1)
        self.assertFalse(business['websites'])

    def test_combined_desk_snapshot_keeps_display_and_tokens_together(self):
        bid = self.imported()
        base = f'/api/businesses/{bid}'
        before = self.ok('GET', base + '/investigation')
        original = investigation.read

        def import_during_read(snapshot, business_id, business):
            with closing(core.connect(self.workspace / 'review.sqlite3')) as writer:
                core.import_rows(writer, [core.normalize(self.row(websites=['https://cedar.example/new']))],
                                 {'source': 'fictional'}, 'synthetic-concurrent-import')
                core.review(writer, bid, dict(website_status='uncertain', notes='Concurrent CLI review',
                                             evidence_url='https://example.com/cli'))
            return original(snapshot, business_id, business)

        with patch('prospect.app.investigation.read', side_effect=import_during_read):
            displayed = self.ok('GET', base + '/investigation')
        self.assertEqual(displayed['business']['websites'], [])
        self.assertEqual(displayed['source_revision'], before['source_revision'])
        self.assertEqual(displayed['review_revision'], before['review_revision'])
        self.assertEqual(displayed['business']['review_history_count'], 0)
        current = self.ok('GET', base + '/investigation')
        self.assertEqual(current['business']['websites'], ['https://cedar.example/new'])
        self.assertNotEqual(current['source_revision'], displayed['source_revision'])
        self.assertNotEqual(current['review_revision'], displayed['review_revision'])
        self.assertEqual(current['business']['review_history_count'], 1)
        self.assertEqual(self.request('POST', base + '/review', dict(source_revision=displayed['source_revision'],
            changes=dict(website_status='no_site_found', evidence_url='https://example.com/check', notes='Old display')))[0], 409)

    def test_v04_draft_schema_upgrade_preserves_content(self):
        bid = self.imported()
        self.review(bid)
        self.stop()
        draft = dict(step=2, mode='guided', questions={'activity': {'notes': 'Existing v0.4 draft'}})
        with closing(sqlite3.connect(self.workspace / 'review.sqlite3')) as db:
            db.execute('DROP TABLE investigation_state')
            db.execute('DROP TABLE investigation_sessions')
            db.execute('CREATE TABLE investigation_state(business_id INTEGER PRIMARY KEY,draft_revision INTEGER NOT NULL,draft TEXT,questions TEXT NOT NULL)')
            db.execute('INSERT INTO investigation_state VALUES(?,?,?,?)', (bid, 7, json.dumps(draft), '{}'))
            db.commit()
        self.start()
        state = self.ok('GET', f'/api/businesses/{bid}/investigation')
        self.assertEqual(state['draft'], draft)
        self.assertEqual(state['draft_revision'], 7)
        self.assertFalse(state['source_changed'])
        self.assertEqual(self.ok('GET', f'/api/businesses/{bid}')['review_history_count'], 1)
        self.reimport(phones=['2025550199'])
        self.assertTrue(self.ok('GET', f'/api/businesses/{bid}/investigation')['source_changed'])

    def test_rejected_request_bodies_return_denial_without_writing(self):
        for size in (1, 4096, 32768):
            status, response = self.request('POST', '/api/demo', {'untrusted': 'x' * size},
                                            headers={'Origin': 'https://foreign.example'})
            self.assertEqual(status, 403)
            self.assertIn('Cross-origin', response['error'])
        self.assertEqual(self.ok('GET', '/api/state')['counts']['total'], 0)

    def test_advanced_review_rejects_a_newer_review_after_draft_flush(self):
        bid = self.imported()
        base = f'/api/businesses/{bid}'
        snapshot = self.ok('GET', base + '/investigation')
        self.ok('POST', base + '/draft', dict(review_revision=snapshot['review_revision'],
            draft_revision=snapshot['draft_revision'], source_revision=snapshot['source_revision'],
            draft=dict(step=3, mode='workspace', questions={})))
        with closing(core.connect(self.workspace / 'review.sqlite3')) as writer:
            core.review(writer, bid, dict(ownership='chain', evidence_url='https://example.com/newer', notes='Newer CLI finding'))
        body = dict(review_revision=snapshot['review_revision'], source_revision=snapshot['source_revision'],
            changes=dict(ownership='independent', evidence_url='https://example.com/older', notes='Older advanced form'))
        self.assertEqual(self.request('POST', base + '/review', body)[0], 409)
        current = self.ok('GET', base + '/investigation')
        self.assertEqual(current['business']['review']['ownership'], 'chain')
        for invalid in (True, -1, '1', None):
            body['review_revision'] = invalid
            self.assertEqual(self.request('POST', base + '/review', body)[0], 400)
        body['review_revision'] = current['review_revision']
        self.ok('POST', base + '/review', body)
        self.assertEqual(self.ok('GET', base)['review']['ownership'], 'independent')


class InvestigationMigrationTests(TestCase):
    def setUp(self):
        scratch = Path(__file__).resolve().parents[1] / '.test-temp'
        scratch.mkdir(exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=scratch)
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / 'legacy.sqlite3'
        with closing(core.connect(self.path)) as db:
            row = core.normalize(dict(source_id='legacy', name='Fictional Painting', country='US', state='TX', categories=['painter']))
            core.import_rows(db, [row], {'source': 'fictional'}, 'legacy-input')
            core.review(db, 1, dict(website_status='uncertain', notes='Existing synthetic review', evidence_url='https://example.com'))
            db.executescript('''CREATE TABLE investigation_state(
                business_id INTEGER PRIMARY KEY, draft_revision INTEGER NOT NULL, draft TEXT, questions TEXT NOT NULL);
                CREATE TABLE investigation_sessions(session_id TEXT PRIMARY KEY,business_id INTEGER NOT NULL,
                request_id TEXT UNIQUE NOT NULL,payload TEXT NOT NULL,result TEXT NOT NULL,before_review TEXT NOT NULL,
                before_questions TEXT NOT NULL,submitted TEXT NOT NULL,before_draft TEXT,final_revision INTEGER NOT NULL,
                undone INTEGER NOT NULL DEFAULT 0);''')
            with db:
                db.execute('INSERT INTO investigation_state VALUES(?,?,?,?)',
                           (1, 4, json.dumps(dict(step=1, mode='workspace', questions={'website': {'notes': 'Keep unfinished work'}})), '{}'))
                db.execute('INSERT INTO investigation_sessions VALUES(?,?,?,?,?,?,?,?,?,?,?)',
                           ('legacy-session', 1, 'legacy-request', '{}', '{}', '{}', '{}', '{}', 'null', 1, 0))
            self.before = {table: [tuple(r) for r in db.execute('SELECT * FROM ' + table)]
                           for table in ('investigation_state', 'investigation_sessions', 'reviews')}

    def test_concurrent_upgrade_serializes_schema_inspection_and_preserves_work(self):
        first_inspected, second_attempt, release = threading.Event(), threading.Event(), threading.Event()
        errors = []

        class MigrationConnection(sqlite3.Connection):
            def execute(connection, sql, *args):
                cursor = super().execute(sql, *args)
                if sql == 'PRAGMA table_info(investigation_state)':
                    if not connection.in_transaction:
                        raise AssertionError('Schema inspection must hold the writer transaction')
                    if threading.current_thread().name == 'first-upgrade':
                        rows = cursor.fetchall()
                        first_inspected.set()
                        if not release.wait(3):
                            raise RuntimeError('Test failed to release migration')
                        return rows
                return cursor

        def worker(first):
            with closing(sqlite3.connect(self.path, factory=MigrationConnection)) as db:
                db.row_factory = sqlite3.Row
                if not first:
                    db.set_trace_callback(lambda sql: second_attempt.set() if sql == 'BEGIN IMMEDIATE' else None)
                try:
                    investigation.initialize(db)
                except BaseException as error:
                    errors.append(error)

        first = threading.Thread(target=worker, args=(True,), name='first-upgrade')
        second = threading.Thread(target=worker, args=(False,), name='second-upgrade')
        first.start()
        try:
            self.assertTrue(first_inspected.wait(3))
            second.start()
            self.assertTrue(second_attempt.wait(3))
        finally:
            release.set()
            first.join(5)
            if second.ident is not None:
                second.join(5)
        self.assertEqual(errors, [])
        with closing(core.connect(self.path)) as db:
            revision = changes.source_revision(db, 1)
            for table in ('investigation_state', 'investigation_sessions'):
                legacy_columns = ('business_id,draft_revision,draft,questions' if table == 'investigation_state'
                                  else 'session_id,business_id,request_id,payload,result,before_review,before_questions,submitted,before_draft,final_revision,undone')
                self.assertEqual([tuple(r) for r in db.execute('SELECT ' + legacy_columns + ' FROM ' + table)], self.before[table])
                source_column = 'draft_source_revision' if table == 'investigation_state' else 'source_revision'
                self.assertTrue(all(r[source_column] == revision for r in db.execute('SELECT * FROM ' + table)))
                baseline_columns = ('draft_review_revision' if table == 'investigation_state'
                                    else 'before_draft_review_revision,before_review_revision')
                self.assertTrue(all(value is None for row in db.execute('SELECT ' + baseline_columns + ' FROM ' + table) for value in row))
            self.assertEqual([tuple(r) for r in db.execute('SELECT * FROM reviews')], self.before['reviews'])

    def test_failed_backfill_rolls_back_schema_and_preserves_legacy_rows(self):
        with closing(core.connect(self.path)) as db:
            with patch('prospect.investigation.changes.source_revision', side_effect=RuntimeError('Synthetic backfill failure')):
                with self.assertRaisesRegex(RuntimeError, 'backfill failure'):
                    investigation.initialize(db)
            self.assertNotIn('draft_source_revision', {r['name'] for r in db.execute('PRAGMA table_info(investigation_state)')})
            self.assertNotIn('source_revision', {r['name'] for r in db.execute('PRAGMA table_info(investigation_sessions)')})
            for table, rows in self.before.items():
                self.assertEqual([tuple(r) for r in db.execute('SELECT * FROM ' + table)], rows)
            investigation.initialize(db)
            self.assertEqual(db.execute('SELECT draft_revision FROM investigation_state').fetchone()[0], 4)


if __name__ == '__main__':
    import unittest
    unittest.main()
