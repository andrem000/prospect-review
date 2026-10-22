"""Fictional HTTP regressions for durable, atomic investigation sessions."""
import concurrent.futures
import http.client
import json
import sqlite3
import tempfile
import threading
import unittest
from contextlib import closing
from datetime import date, timedelta
from pathlib import Path
from unittest.mock import patch

from prospect import app, core


class InvestigationTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.workspace = Path(self.temporary.name)
        self.start()
        preview = self.ok('POST', '/api/import/preview', dict(filename='fictional.json', adapter='canonical',
            content=json.dumps([dict(source_id='fixture-1', name='Fictional Cedar Painting', country='US',
                                    state='TX', city='Austin', categories=['painter'], phones=['2025550101'])])))
        self.ok('POST', '/api/import/commit', dict(preview_id=preview['preview_id'], rights=dict(
            source='fictional', license='synthetic', terms_url='https://example.com/terms', attribution='Fictional',
            reviewed_at='2026-01-01', retention_permitted=True, export_permitted=True, lead_discovery_permitted=True)))
        self.bid = self.ok('GET', '/api/businesses')['matching_ids'][0]
        self.base = f'/api/businesses/{self.bid}'

    def start(self):
        self.server = app.create_server(self.workspace)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def stop(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()

    def tearDown(self):
        self.stop()
        self.temporary.cleanup()

    def request(self, method, path, data=None, headers=None):
        conn = http.client.HTTPConnection('127.0.0.1', self.server.server_port, timeout=5)
        options = {'X-Prospect-Token': self.server.app.token, 'Content-Type': 'application/json'}
        options.update(headers or {})
        conn.request(method, path, body=json.dumps(data) if data is not None else None, headers=options)
        response = conn.getresponse()
        result = response.status, json.loads(response.read())
        conn.close()
        return result

    def ok(self, method, path, data=None):
        status, body = self.request(method, path, data)
        self.assertEqual(status, 200, body)
        return body

    def state(self):
        return self.ok('GET', self.base + '/investigation')

    def entry(self, **answers):
        return dict(evidence_url='https://example.com/fictional-check', notes='Fictional observed evidence',
                    evidence_observed_at='2026-01-02', **answers)

    def payload(self, questions, request_id='first'):
        state = self.state()
        return dict(review_revision=state['review_revision'], draft_revision=state['draft_revision'],
                    request_id=request_id, questions=questions)

    def save(self, questions, request_id='first'):
        return self.ok('POST', self.base + '/investigation', self.payload(questions, request_id))

    def draft(self, value):
        state = self.state()
        return self.ok('POST', self.base + '/draft', dict(review_revision=state['review_revision'],
                       draft_revision=state['draft_revision'], draft=value))

    def counts(self):
        with closing(sqlite3.connect(self.workspace / 'review.sqlite3')) as db:
            return tuple(db.execute('SELECT COUNT(*) FROM ' + table).fetchone()[0]
                         for table in ('reviews', 'observations', 'suppressions', 'investigation_sessions'))

    def test_draft_restart_and_read_only_get(self):
        initial = self.state()
        self.assertEqual(initial['questions'], {})
        self.assertTrue(initial['can_review'])
        draft = dict(step=2, mode='guided', questions={'activity': {'notes': 'Unfinished fictional finding'}})
        saved = self.draft(draft)
        self.assertEqual(saved['draft_revision'], 1)
        self.assertEqual(self.counts()[0], 0)
        self.stop()
        self.start()
        self.assertEqual(self.state()['draft'], draft)
        self.assertEqual(self.state()['review_revision'], 0)
        self.assertEqual(self.state()['draft_revision'], 1)
        self.draft(None)
        self.assertIsNone(self.state()['draft'])
        self.assertEqual(self.counts()[0], 0)

    def test_atomic_question_evidence_and_website_does_not_refresh_activity(self):
        active = self.entry(activity='active')
        saved = self.save({'activity': active})
        website = self.entry(website_status='no_site_found')
        website.update(evidence_url='https://example.com/later-site-check', evidence_observed_at='2026-02-03')
        second = self.save({'website': website}, 'second')
        self.assertEqual(second['review']['activity_evidence_observed_at'], '2026-01-02')
        self.assertEqual(second['review']['activity_evidence_url'], active['evidence_url'])
        state = self.state()
        self.assertEqual(state['questions'], {'activity': active, 'website': website})
        history = self.ok('GET', self.base)['reviews']
        self.assertEqual(len(history), 2)
        self.assertEqual(history[0]['investigation_question'], 'activity')
        self.assertEqual(history[0]['investigation_session'], saved['session_id'])
        self.assertEqual(history[1]['investigation_question'], 'website')

    def test_failure_in_second_core_review_is_atomic(self):
        self.draft(dict(step=0, mode='guided', questions={'identity': {'notes': 'Keep this draft'}}))
        before, counts = self.state(), self.counts()
        body = self.payload({'identity': self.entry(identity_status='different_businesses'),
                             'website': self.entry(website_status='site_found', verified_website='https://facebook.com/fictional')})
        self.assertEqual(self.request('POST', self.base + '/investigation', body)[0], 400)
        self.assertEqual(self.state(), before)
        self.assertEqual(self.counts(), counts)
        self.assertFalse(self.ok('GET', self.base)['review'].get('queue_hold', False))

    def test_identical_retry_after_restart_and_later_save(self):
        body = self.payload({'activity': self.entry(activity='active')})
        original = self.ok('POST', self.base + '/investigation', body)
        self.stop()
        self.start()
        self.save({'website': self.entry(website_status='uncertain')}, 'second')
        self.draft(dict(step=0, mode='workspace', questions={}))
        state, counts = self.state(), self.counts()
        self.assertEqual(self.ok('POST', self.base + '/investigation', body), original)
        self.assertEqual(self.state(), state)
        self.assertEqual(self.counts(), counts)
        changed = {**body, 'questions': {'activity': self.entry(activity='closed')}}
        self.assertEqual(self.request('POST', self.base + '/investigation', changed)[0], 409)

    def test_concurrent_sessions_and_stale_drafts_conflict(self):
        first = self.payload({'activity': self.entry(activity='active')}, 'one')
        second = {**first, 'request_id': 'two'}
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as workers:
            results = list(workers.map(lambda body: self.request('POST', self.base + '/investigation', body), [first, second]))
        self.assertEqual(sorted(result[0] for result in results), [200, 409])
        self.assertEqual(self.counts()[0], 1)
        state = self.state()
        body = dict(review_revision=state['review_revision'], draft_revision=state['draft_revision'],
                    draft=dict(step=0, mode='guided', questions={}))
        self.ok('POST', self.base + '/draft', body)
        self.assertEqual(self.request('POST', self.base + '/draft', body)[0], 409)
        stale = self.payload({'website': self.entry(website_status='uncertain')}, 'three')
        self.ok('POST', self.base + '/review', {'changes': self.entry(activity='unknown')})
        self.assertEqual(self.request('POST', self.base + '/investigation', stale)[0], 409)

    def test_external_writer_during_staging_cannot_be_overwritten(self):
        original_review = core.review

        def review_and_external_suppression(staged, bid, changes):
            result = original_review(staged, bid, changes)
            with closing(core.connect(self.workspace / 'review.sqlite3')) as external:
                with external:
                    external.execute('INSERT INTO suppressions VALUES(?,?,?)', ('phone', '2025550101', 'fictional_late_contact'))
            return result

        body = self.payload({'activity': self.entry(activity='active')})
        with patch('prospect.investigation.core.review', side_effect=review_and_external_suppression):
            self.assertEqual(self.request('POST', self.base + '/investigation', body)[0], 409)
        self.assertEqual(self.counts(), (0, 1, 1, 0))
        self.assertFalse(self.state()['can_review'])

    def test_concurrent_identical_retries_across_two_servers(self):
        second_server = app.create_server(self.workspace)
        second_thread = threading.Thread(target=second_server.serve_forever, daemon=True)
        second_thread.start()
        barrier = threading.Barrier(2)
        original_review = core.review

        def simultaneous_review(staged, bid, changes):
            result = original_review(staged, bid, changes)
            barrier.wait(timeout=5)
            return result

        body = self.payload({'activity': self.entry(activity='active')})

        def other_request():
            conn = http.client.HTTPConnection('127.0.0.1', second_server.server_port, timeout=10)
            conn.request('POST', self.base + '/investigation', body=json.dumps(body), headers={
                'X-Prospect-Token': second_server.app.token, 'Content-Type': 'application/json'})
            response = conn.getresponse()
            value = response.status, json.loads(response.read())
            conn.close()
            return value

        try:
            with patch('prospect.investigation.core.review', side_effect=simultaneous_review):
                with concurrent.futures.ThreadPoolExecutor(max_workers=2) as workers:
                    future = workers.submit(other_request)
                    first = self.request('POST', self.base + '/investigation', body)
                    second = future.result()
            self.assertEqual(first[0], 200, first)
            self.assertEqual(first, second)
            self.assertEqual(self.counts()[0], 1)
        finally:
            second_server.shutdown()
            second_server.server_close()
            second_thread.join()

    def test_four_question_session_and_cross_business_retry_rejected(self):
        questions = dict(identity=self.entry(identity_status='same_business'),
                         website=self.entry(website_status='no_site_found'), activity=self.entry(activity='active'),
                         fit=self.entry(ownership='independent', residential='yes', service_fit='yes', delivery='mobile'))
        body = self.payload(questions)
        result = self.ok('POST', self.base + '/investigation', body)
        self.assertEqual(self.counts()[0], 4)
        self.assertEqual(self.state()['questions'], questions)
        self.assertEqual(self.ok('GET', self.base)['review']['ownership'], 'independent')
        self.assertEqual(result['review']['activity_evidence_observed_at'], '2026-01-02')
        with closing(core.connect(self.workspace / 'review.sqlite3')) as db:
            with db:
                other = db.execute('INSERT INTO businesses(created_at) VALUES(?)', (core.now(),)).lastrowid
                observation = db.execute('SELECT batch_id,source,data FROM observations LIMIT 1').fetchone()
                db.execute('INSERT INTO observations(business_id,batch_id,source,source_id,data) VALUES(?,?,?,?,?)',
                           (other, observation['batch_id'], observation['source'], 'fictional-other', observation['data']))
        self.assertEqual(self.request('POST', f'/api/businesses/{other}/investigation', body)[0], 409)
        self.assertEqual(self.counts()[0], 4)
        self.assertEqual(result, self.ok('POST', self.base + '/investigation', body))

    def test_undo_preserves_history_activity_and_confirmed_site(self):
        baseline = self.ok('POST', self.base + '/review', {'changes': self.entry(activity='active')})['review']
        submitted = {'website': self.entry(website_status='site_found', verified_website='https://fictional.example.com')}
        saved = self.save(submitted)
        result = self.ok('POST', self.base + '/investigation/undo', dict(session_id=saved['session_id'], review_revision=saved['review_revision']))
        for field in core.ACTIVITY_EVIDENCE_FIELDS:
            self.assertEqual(result['review'][field], baseline[field])
        business = self.ok('GET', self.base)
        self.assertEqual(len(business['reviews']), 3)
        self.assertIn('website_present', business['excluded_reasons'])
        state = self.state()
        self.assertEqual(state['draft']['questions'], submitted)
        self.assertEqual(state['questions'], {})
        self.assertTrue(state['latest_session']['undone'])
        self.assertFalse(state['latest_session']['can_undo'])
        self.assertEqual(self.request('POST', self.base + '/investigation/undo', dict(session_id=saved['session_id'], review_revision=result['review_revision']))[0], 409)

    def test_undo_undated_activity_and_later_review_rejection(self):
        legacy = self.entry(activity='active')
        legacy.pop('evidence_observed_at')
        self.ok('POST', self.base + '/review', {'changes': legacy})
        saved = self.save({'activity': self.entry(activity='closed')})
        undone = self.ok('POST', self.base + '/investigation/undo', dict(session_id=saved['session_id'], review_revision=saved['review_revision']))
        self.assertIsNone(undone['review']['activity_evidence_observed_at'])
        second = self.save({'activity': self.entry(activity='active')}, 'second')
        later = self.ok('POST', self.base + '/review', {'changes': self.entry(website_status='uncertain')})
        state = self.state()
        self.assertFalse(state['latest_session']['can_undo'])
        self.assertEqual(self.request('POST', self.base + '/investigation/undo', dict(session_id=second['session_id'], review_revision=state['review_revision']))[0], 409)

    def test_identity_hold_is_not_cleared_and_contact_exclusions_are_durable(self):
        self.save({'identity': self.entry(identity_status='different_businesses')})
        self.assertIn('manual_hold', self.ok('GET', self.base)['excluded_reasons'])
        self.save({'identity': self.entry(identity_status='same_business')}, 'second')
        self.assertIn('manual_hold', self.ok('GET', self.base)['excluded_reasons'])
        draft = dict(step=0, mode='guided', questions={'activity': {'activity': 'active'}})
        self.draft(draft)
        payload = self.payload({'activity': self.entry(activity='active')}, 'excluded')
        self.ok('POST', '/api/exclusions', dict(filename='fictional.json', content=json.dumps([{'phone': '2025550101', 'reason': 'fictional_existing_contact'}])))
        self.assertFalse(self.state()['can_review'])
        counts = self.counts()
        self.assertEqual(self.request('POST', self.base + '/investigation', payload)[0], 400)
        state = self.state()
        self.assertEqual(self.request('POST', self.base + '/draft', dict(review_revision=state['review_revision'], draft_revision=state['draft_revision'], draft=draft))[0], 400)
        self.assertEqual(self.counts(), counts)
        self.assertIn('suppressed_or_contacted', self.ok('GET', self.base)['excluded_reasons'])

    def test_bounded_validation_and_route_protections(self):
        valid = self.entry(activity='active')
        cases = [dict(valid, activity=True), dict(valid, extra='unsupported'), dict(valid, notes=' '),
                 dict(valid, notes='x' * 8001), dict(valid, evidence_url='javascript:alert(1)'),
                 dict(valid, evidence_url='https://user:pass@example.com'),
                 dict(valid, evidence_url='https://example.com:bad'), dict(valid, evidence_url='https://exa mple.com'),
                 dict(valid, evidence_observed_at='2026-02-30'),
                 dict(valid, evidence_observed_at=(date.today() + timedelta(days=1)).isoformat())]
        for entry in cases:
            with self.subTest(entry=str(entry)[:90]):
                self.assertEqual(self.request('POST', self.base + '/investigation', self.payload({'activity': entry}))[0], 400)
        for draft in (dict(step=True, mode='guided', questions={}), dict(step=4, mode='guided', questions={}),
                      dict(step=0, mode='guided', questions={'other': {}}),
                      dict(step=0, mode='guided', questions={'activity': {'notes': []}})):
            self.assertEqual(self.request('POST', self.base + '/draft', dict(review_revision=0, draft_revision=0, draft=draft))[0], 400)
        self.assertEqual(self.counts()[0], 0)
        self.assertEqual(self.request('GET', self.base + '/investigation', headers={'X-Prospect-Token': ''})[0], 403)
        self.assertEqual(self.request('POST', self.base + '/investigation', self.payload({'activity': valid}), headers={'Origin': 'https://evil.example'})[0], 403)
        self.assertEqual(self.request('GET', '/api/businesses/99999/investigation')[0], 404)


if __name__ == '__main__':
    unittest.main()
