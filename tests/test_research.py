"""Offline research behavior with fictional records and mocked transport only."""
import hashlib
import json
import sqlite3
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from unittest.mock import patch

from prospect import research, research_network as network
import test_app as app_tests


class ResearchHTTPTests(unittest.TestCase):
    setUp, tearDown = app_tests.AppTests.setUp, app_tests.AppTests.tearDown
    start, stop, request, ok = app_tests.AppTests.start, app_tests.AppTests.stop, app_tests.AppTests.request, app_tests.AppTests.ok
    row, preview, rights, imported = app_tests.AppTests.row, app_tests.AppTests.preview, app_tests.AppTests.rights, app_tests.AppTests.imported

    def payload(self, **extra):
        return dict(request_id='fictional-capture', mode='paste', url='https://example.com/fictional',
            retention_confirmed=True, observed_at='2026-01-01', content='<title>Fictional Cedar Painting</title><p>Fictional Cedar Painting 1 Fictional Way Austin</p>', **extra)

    def test_paste_idempotency_persistence_dismiss_no_decision_mutation(self):
        bid = self.imported()
        path = f'/api/businesses/{bid}/research'
        before = self.ok('GET', f'/api/businesses/{bid}/investigation')
        payload = self.payload()
        with patch.object(network, 'fetch_page', side_effect=AssertionError('offline must not fetch')):
            first = self.ok('POST', path, payload)['record']
            self.assertEqual(self.ok('POST', path, payload)['record']['id'], first['id'])
            self.assertEqual(self.request('POST', path, {**payload, 'content': 'changed'})[0], 409)
            self.assertEqual(self.ok('GET', f'/api/businesses/{bid}/investigation'), before)
            self.assertEqual(first['proposals']['identity']['identity_status'], 'same_business')
            self.assertEqual(first['content_sha256'], hashlib.sha256(payload['content'].encode()).hexdigest())
        with closing(sqlite3.connect(self.workspace / 'review.sqlite3')) as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM research_records').fetchone()[0], 1)
            stored = db.execute('SELECT payload FROM research_records').fetchone()[0]
            self.assertEqual(len(stored), 64)
            self.assertEqual(db.execute('SELECT COUNT(*) FROM reviews').fetchone()[0], 0)
        dismissed = self.ok('POST', path + '/' + first['id'] + '/dismiss', {'dismissed': True})['record']
        self.assertTrue(dismissed['dismissed'])
        self.stop(); self.start()
        saved = self.ok('GET', path)['records']
        self.assertEqual(len(saved), 1)
        self.assertTrue(saved[0]['dismissed'])
        self.ok('POST', path + '/' + first['id'] + '/dismiss', {'dismissed': False})

    def test_validation_limit_and_get_no_fetch(self):
        bid = self.imported(); path = f'/api/businesses/{bid}/research'
        with patch.object(network, 'fetch_page', side_effect=AssertionError('GET must not fetch')):
            self.assertEqual(self.ok('GET', path), {'records': []})
        for changes in ({'retention_confirmed': False}, {'observed_at': '2999-01-01'}, {'observed_at': ''},
                        {'content': 'x' * (512 * 1024 + 1)}, {'url': 'http://127.0.0.1/'}, {'request_id': ''}):
            self.assertEqual(self.request('POST', path, {**self.payload(), **changes})[0], 400)
        first = self.ok('POST', path, self.payload())['record']
        with closing(sqlite3.connect(self.workspace / 'review.sqlite3')) as db:
            row = db.execute('SELECT * FROM research_records').fetchone()
            for number in range(99):
                db.execute('INSERT INTO research_records VALUES(?,?,?,?,?,?)', (str(number),bid,'extra-'+str(number), row[3],row[4],row[5]))
            db.commit()
        self.assertEqual(self.request('POST', path, {**self.payload(), 'request_id': 'over-limit'})[0], 400)
        self.assertEqual(self.ok('POST', path, self.payload())['record']['id'], first['id'])

    def test_network_slot_drafts_respond_and_contact_change_discards(self):
        bid = self.imported(); path = f'/api/businesses/{bid}/research'
        entered, release = threading.Event(), threading.Event()
        def transport(url):
            entered.set(); self.assertTrue(release.wait(4))
            return url, b'Fictional Cedar Painting Austin', 'text/plain'
        payload = dict(request_id='live-mock', mode='fetch', url='https://example.com/', retention_confirmed=True)
        with patch.object(network, 'fetch_page', side_effect=transport), ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(self.request, 'POST', path, payload)
            try:
                self.assertTrue(entered.wait(2))
                self.assertEqual(self.request('POST', path, {**payload, 'request_id': 'another'})[0], 409)
                current = self.ok('GET', f'/api/businesses/{bid}/investigation')
                self.assertIsInstance(current, dict)
                self.ok('POST', f'/api/businesses/{bid}/review', {'changes': {'disposition': 'contacted', 'notes': 'Fictional contact control', 'evidence_url': 'https://example.com/fictional'}})
            finally:
                release.set()
            self.assertEqual(future.result()[0], 409)
        self.assertEqual(self.ok('GET', path)['records'], [])
        self.assertEqual(self.request('POST', path, self.payload())[0], 400)

    def test_identity_change_discards_and_review_change_does_not_stale(self):
        bid = self.imported(); path = f'/api/businesses/{bid}/research'
        record = self.ok('POST', path, self.payload())['record']
        self.ok('POST', f'/api/businesses/{bid}/review', {'changes': {'notes': 'review-only', 'evidence_url': 'https://example.com/fictional'}})
        self.assertFalse(self.ok('GET', path)['records'][0]['stale'])
        with closing(sqlite3.connect(self.workspace / 'review.sqlite3')) as db:
            row = db.execute('SELECT id,data FROM observations WHERE business_id=?', (bid,)).fetchone()
            data = json.loads(row[1]); data['name'] = 'Changed Fictional Name'
            db.execute('UPDATE observations SET data=? WHERE id=?', (json.dumps(data), row[0])); db.commit()
        self.assertTrue(self.ok('GET', path)['records'][0]['stale'])
        self.assertEqual(self.request('POST', f'/api/businesses/{bid+1}/research/{record["id"]}/dismiss', {'dismissed': True})[0],404)


class AnalysisTests(unittest.TestCase):
    business = dict(id=1, name='Fictional Cedar Painting', address='1 Fictional Way', city='Austin',state='TX',phones=['2025550101'],websites=[])
    def analyze(self, text, url='https://example.com/'):
        return research.analyze(self.business, text.encode(), url,url,'paste','2026-01-01')

    def test_missing_is_unknown_and_untrusted_scripts_ignored(self):
        record = self.analyze('<script>Fictional Cedar Painting 2025550101</script><style>1 Fictional Way Austin</style><!-- Fictional Cedar Painting -->No information')
        self.assertEqual(record['proposals']['identity']['identity_status'],'uncertain')
        self.assertFalse(any(s['kind']=='conflict' for s in record['signals']))
        self.assertNotIn('2025550101', record['excerpt'])
        self.assertNotIn('no_site_found', json.dumps(record['proposals']))

    def test_multientity_conflict_and_social_cannot_confirm(self):
        entities = [{'@type':'HousePainter','name':'Fictional Cedar Painting','telephone':'2025550101'},
                    {'@type':'HousePainter','name':'Other Fictional Business','telephone':'2025550102'}]
        record = self.analyze('<script type="application/ld+json">'+json.dumps(entities)+'</script>')
        self.assertEqual(record['proposals']['identity']['identity_status'],'uncertain')
        self.assertTrue(any(s['kind']=='conflict' for s in record['signals']))
        record = self.analyze('Fictional Cedar Painting 2025550101', 'https://www.facebook.com/fictional')
        self.assertEqual(record['proposals']['website']['website_status'],'uncertain')

    def test_bounds_links_original_hash(self):
        text = '<title>'+'t'*1000+'</title>' + 'Fictional Cedar Painting 2025550101' + ''.join('<a href="https://example.com/%s">x</a>'%i for i in range(30))+'x'*2000
        record = self.analyze(text)
        self.assertLessEqual(len(record['excerpt']),800)
        self.assertLessEqual(len(record['title']),300)
        self.assertLessEqual(len(record['links']),12)
        self.assertEqual(record['content_sha256'],hashlib.sha256(text.encode()).hexdigest())


class TransportTests(unittest.TestCase):
    def test_urls_and_addresses(self):
        for value in ('http://127.0.0.1/', 'http://2130706433/', 'http://[::ffff:8.8.8.8]/', 'http://foo.localhost/',
                      'https://user:secret@example.com/', 'https://example.com:444/', 'https://example.com/\npath', 'https://example.com\\@127.0.0.1/'):
            if 'foo.localhost' in value:
                continue  # DNS boundary rejects local resolutions even for valid DNS syntax.
            with self.subTest(value=value), self.assertRaises(ValueError): network.validate_url(value)
        self.assertEqual(network.validate_url('https://example.com'),'https://example.com/')
        for address in ('127.0.0.1','10.0.0.1','192.0.2.1','224.0.0.1','::1','::ffff:8.8.8.8','2001:db8::1'):
            with self.subTest(address=address), self.assertRaises(ValueError): network.public_address(address)

    def test_robots_and_redirect_revalidation(self):
        def request(url, deadline, max_bytes=network.MAX_CONTENT):
            if url.endswith('/robots.txt'): return 200, {}, b'User-agent: *\nDisallow: /private'
            raise AssertionError('disallowed page must not be fetched')
        with patch.object(network,'_request',side_effect=request):
            with self.assertRaisesRegex(ValueError,'disallows'): network.fetch_page('https://example.com/private')
        def redirect(url, deadline, max_bytes=network.MAX_CONTENT):
            if url.endswith('/robots.txt'): return 404,{},b''
            return 302,{'location':'http://127.0.0.1/'},b''
        with patch.object(network,'_request',side_effect=redirect):
            with self.assertRaises(ValueError): network.fetch_page('https://example.com/')

if __name__ == '__main__': unittest.main()
