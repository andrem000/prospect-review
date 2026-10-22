"""Independent adversarial research regressions; transport is entirely offline."""
import json
import socket
import subprocess
import threading
import tempfile
import time
import unittest
from contextlib import closing
from types import SimpleNamespace
from unittest.mock import Mock, patch

from prospect import research_network as net
from prospect import research
from prospect import app, core, investigation


class PublicTransportSecurityTests(unittest.TestCase):
    def test_rejects_nonpublic_and_transition_addresses(self):
        for value in ('0.0.0.0', '127.0.0.1', '10.1.2.3', '172.16.0.1',
                      '192.168.1.1', '169.254.169.254', '100.64.0.1',
                      '192.0.2.1', '224.0.0.1', '255.255.255.255', '::',
                      '::1', 'fc00::1', 'fe80::1', 'ff02::1',
                      '::ffff:8.8.8.8', '2002:0808:0808::1',
                      '2001:0000:4136:e378:8000:63bf:3fff:fdd2'):
            with self.subTest(value=value), self.assertRaises(ValueError):
                net.public_address(value)
        self.assertEqual(net.public_address('8.8.8.8'), '8.8.8.8')

    def test_rejects_parser_normalization_credentials_and_alternate_hosts(self):
        for value in (' https://example.com', 'https://exam\nple.com',
                      'https://example.com/\r\nx', 'https://example.com/\x00',
                      'https://example.com/\\x', 'file:///etc/passwd',
                      'http://localhost/', 'http://localhost.localdomain:8080/',
                      'http://127.1/', 'http://2130706433/', 'http://0x7f000001/',
                      'https://u:p@example.com/', 'http://example.com./',
                      'http://[fe80::1%25eth0]/', 'http://[::ffff:127.0.0.1]/',
                      'https://example.com:444/'):
            with self.subTest(value=value), self.assertRaises(ValueError):
                net.validate_url(value)

    def test_mixed_dns_answers_fail_closed_before_connection(self):
        for addresses in (['8.8.8.8', '127.0.0.1'], ['8.8.8.8', '::ffff:8.8.8.8']):
            completed = SimpleNamespace(stdout=json.dumps(addresses).encode())
            with self.subTest(addresses=addresses), patch.object(net.subprocess, 'run', return_value=completed), \
                    patch.object(net, 'PinnedHTTPConnection') as connection, self.assertRaises(ValueError):
                net._request('https://example.com/', time.monotonic() + 2)
            connection.assert_not_called()

    def test_resolver_timeout_fails_closed(self):
        with patch.object(net.subprocess, 'run', side_effect=subprocess.TimeoutExpired('dns', .01)), \
                self.assertRaisesRegex(ValueError, 'lookup'):
            net.resolve('example.com', 443, .01)

    def test_connection_pins_numeric_address_and_uses_verified_hostname_tls(self):
        sock, wrapped, context = Mock(), Mock(), Mock()
        context.wrap_socket.return_value = wrapped
        with patch.object(net.socket, 'socket', return_value=sock) as factory, \
                patch.object(net.ssl, 'create_default_context', return_value=context) as default, \
                patch.object(net.socket, 'getaddrinfo', side_effect=AssertionError('unexpected second resolution')):
            connection = net.PinnedHTTPConnection('example.com', 443, '8.8.8.8', 2, True)
            connection.connect()
        factory.assert_called_once_with(socket.AF_INET, socket.SOCK_STREAM)
        sock.connect.assert_called_once_with(('8.8.8.8', 443))
        default.assert_called_once_with()
        self.assertEqual(context.wrap_socket.call_count, 1)
        self.assertEqual(context.wrap_socket.call_args.args, (sock,))
        self.assertEqual(context.wrap_socket.call_args.kwargs['server_hostname'], 'example.com')
        wrapped.do_handshake.assert_called_once_with()
        self.assertIs(connection.sock, wrapped)

    def test_cross_origin_redirect_checks_destination_robots(self):
        calls = []
        def request(url, deadline, max_bytes=net.MAX_CONTENT):
            calls.append(url)
            return {
                'https://one.example/robots.txt': (404, {}, b''),
                'https://one.example/start': (302, {'location': 'https://two.example/private'}, b''),
                'https://two.example/robots.txt': (200, {}, b'User-agent: *\nDisallow: /private\n'),
            }[url]
        with patch.object(net, '_request', side_effect=request), self.assertRaisesRegex(ValueError, 'disallows'):
            net.fetch_page('https://one.example/start')
        self.assertEqual(calls[-1], 'https://two.example/robots.txt')

    def test_redirect_cannot_target_private_or_control_url(self):
        for location in ('http://127.0.0.1/admin', 'http://[::1]/', 'http://example.com/\nadmin',
                         'https://example.com/\nadmin', '/\nadmin', '/\tadmin'):
            calls = []
            def request(url, deadline, max_bytes=net.MAX_CONTENT):
                calls.append(url)
                return (404, {}, b'') if url.endswith('/robots.txt') else (302, {'location': location}, b'')
            with self.subTest(location=location), patch.object(net, '_request', side_effect=request), self.assertRaises(ValueError):
                net.fetch_page('https://one.example/start')
            self.assertEqual(calls, ['https://one.example/robots.txt', 'https://one.example/start'])

    def test_robots_redirect_rejects_raw_controls_before_join_normalizes_them(self):
        for location in ('https://two.example/\nrobots.txt', '/\trobots.txt'):
            with self.subTest(location=location), patch.object(net, '_request',
                    return_value=(302, {'location': location}, b'')) as request, self.assertRaises(ValueError):
                net.fetch_page('https://one.example/start')
            self.assertEqual(request.call_count, 1)

    def test_robots_unavailable_does_not_fetch_page(self):
        with patch.object(net, '_request', return_value=(503, {}, b'')) as request, self.assertRaisesRegex(ValueError, 'Robots'):
            net.fetch_page('https://one.example/start')
        self.assertEqual(request.call_count, 1)

    def test_request_has_no_credentials_cookie_proxy_or_compression(self):
        response = Mock(status=200)
        response.getheaders.return_value = [('Content-Type', 'text/plain')]
        response.read1.side_effect = [b'safe text', b'']
        connection = Mock(sock=Mock(), aborted=threading.Event())
        connection.getresponse.return_value = response
        with patch.object(net, 'resolve', return_value=['8.8.8.8']), \
                patch.object(net, 'PinnedHTTPConnection', return_value=connection), \
                patch.dict('os.environ', {'HTTPS_PROXY': 'http://127.0.0.1:9999'}):
            self.assertEqual(net._request('https://example.com/path?q=test', time.monotonic() + 2)[2], b'safe text')
        args, kwargs = connection.request.call_args
        self.assertEqual(args, ('GET', '/path?q=test'))
        self.assertEqual(kwargs['headers']['Accept-Encoding'], 'identity')
        self.assertFalse({'Cookie', 'Authorization', 'Proxy-Authorization'} & set(kwargs['headers']))
        connection.close.assert_called_once()

    def test_content_limit_does_not_depend_on_declared_length(self):
        response = Mock(status=200)
        response.getheaders.return_value = []
        response.read1.return_value = b'x' * 9
        connection = Mock(sock=Mock(), aborted=threading.Event())
        connection.getresponse.return_value = response
        with patch.object(net, 'resolve', return_value=['8.8.8.8']), \
                patch.object(net, 'PinnedHTTPConnection', return_value=connection), self.assertRaisesRegex(ValueError, 'size'):
            net._request('https://example.com/', time.monotonic() + 2, max_bytes=8)
        connection.close.assert_called_once()

    def test_total_deadline_interrupts_blocked_header_phase(self):
        interrupted = threading.Event()
        connection = Mock(sock=Mock(), aborted=interrupted)
        connection.abort.side_effect = interrupted.set
        def blocked_headers():
            if not interrupted.wait(1):
                raise AssertionError('Header phase escaped total deadline')
            raise OSError('Deadline closed transport')
        connection.getresponse.side_effect = blocked_headers
        started = time.monotonic()
        with patch.object(net, 'resolve', return_value=['8.8.8.8']), \
                patch.object(net, 'PinnedHTTPConnection', return_value=connection), self.assertRaises(ValueError):
            net._request('https://example.com/', started + .05)
        self.assertLess(time.monotonic() - started, .8)
        connection.abort.assert_called_once()
        connection.close.assert_called_once()


class UntrustedEvidenceSecurityTests(unittest.TestCase):
    def setUp(self):
        self.business = dict(id=1, name='Fictional Cedar Painting', address='1 Fictional Way',
                             city='Austin', state='TX', phones=['2025550101'], websites=[])

    def analyze(self, text, url='https://cedar.example/'):
        return research.analyze(self.business, text.encode(), url, url, 'paste', '2026-01-01')

    def test_hidden_content_and_instructions_cannot_supply_matching_clues(self):
        record = self.analyze('''<title>Welcome</title><p>No published business details here.</p>
          <script>Fictional Cedar Painting 2025550101</script>
          <style>Fictional Cedar Painting 2025550101</style>
          <!-- Fictional Cedar Painting 2025550101 -->
          <div hidden>Fictional Cedar Painting 2025550101</div>
          <div style="display:none">Fictional Cedar Painting 2025550101</div>''')
        self.assertEqual(record['proposals']['identity']['identity_status'], 'uncertain')
        self.assertNotIn('2025550101', record['excerpt'])
        self.assertFalse(any(s['kind'] == 'match' for s in record['signals']))

    def test_disagreeing_entities_do_not_produce_confirmed_proposals(self):
        entities = [dict(**{'@type': 'HousePainter'}, name=self.business['name'], telephone=phone,
                         address='1 Fictional Way Austin TX') for phone in ('2025550101', '2025550199')]
        record = self.analyze('<script type="application/ld+json">' + json.dumps(entities) + '</script>')
        self.assertEqual(record['proposals']['identity']['identity_status'], 'uncertain')
        self.assertEqual(record['proposals']['website']['website_status'], 'uncertain')
        self.assertTrue(any(s['kind'] == 'conflict' for s in record['signals']))

    def test_partial_structured_location_is_missing_evidence_not_conflict(self):
        entity = {'@type': 'HousePainter', 'address': {'addressRegion': 'TX'}}
        record = self.analyze('<script type="application/ld+json">' + json.dumps(entity) + '</script>')
        self.assertFalse(any(s['kind'] == 'conflict' for s in record['signals']))

    def test_same_street_and_city_in_wrong_state_cannot_confirm_identity(self):
        entity = {'@type': 'Plumber', 'name': self.business['name'], 'telephone': '2025550101',
                  'address': {'streetAddress': '1 Fictional Way', 'addressLocality': 'Austin',
                              'addressRegion': 'MA'}}
        record = self.analyze('<script type="application/ld+json">' + json.dumps(entity) + '</script>')
        self.assertEqual(record['proposals']['identity']['identity_status'], 'uncertain')
        self.assertTrue(any(s['field'] == 'address' and s['kind'] == 'conflict' for s in record['signals']))

    def test_directory_cannot_be_suggested_as_confirmed_website(self):
        record = self.analyze('<p>Fictional Cedar Painting 202-555-0101</p>',
                              'https://www.yelp.com/biz/fictional-cedar')
        self.assertEqual(record['proposals']['website']['website_status'], 'uncertain')
        self.assertNotIn('verified_website', record['proposals']['website'])

    def test_no_absence_activity_or_qualification_proposals(self):
        record = self.analyze('<p>No useful data.</p><a href="javascript:alert(1)">bad</a>')
        self.assertEqual(set(record['proposals']), {'identity', 'website'})
        self.assertEqual(record['proposals']['website']['website_status'], 'uncertain')
        self.assertEqual(record['links'], [])
        self.assertFalse(any(s['kind'] == 'conflict' for s in record['signals']))


class CaptureRaceSecurityTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.application = app.Application(self.temporary.name)
        with closing(core.connect(self.application.dbpath)) as db:
            self.application.dispatch('POST', '/api/demo', {}, {}, db)
            self.bid = next(b['id'] for b in self.application.evaluated(db)
                            if 'suppressed_or_contacted' not in b['excluded_reasons'])
        self.payload = dict(request_id='security-capture', mode='fetch',
                            url='https://example.com/', retention_confirmed=True)

    def tearDown(self):
        self.temporary.cleanup()

    def test_contact_added_during_network_discards_result_without_locking_drafts(self):
        entered, release = threading.Event(), threading.Event()
        errors = []
        def fetch(url):
            entered.set()
            if not release.wait(3):
                raise AssertionError('Test release did not arrive')
            return url, b'<p>Fictional test</p>', 'text/html'
        def capture():
            try:
                self.application.capture_research(self.bid, self.payload)
            except BaseException as exc:
                errors.append(exc)
        with patch.object(research.network, 'fetch_page', side_effect=fetch):
            worker = threading.Thread(target=capture)
            worker.start()
            try:
                self.assertTrue(entered.wait(2))
                with self.assertRaises(investigation.Conflict):
                    self.application.capture_research(self.bid, {**self.payload, 'request_id': 'parallel'})
                self.assertTrue(self.application.lock.acquire(timeout=.3), 'Network holds global mutation lock')
                try:
                    with closing(core.connect(self.application.dbpath)) as db:
                        core.review(db, self.bid, {'disposition': 'contacted',
                                    'evidence_url': 'https://example.com/fictional-contact',
                                    'notes': 'Synthetic contact exclusion added during check.'})
                finally:
                    self.application.lock.release()
            finally:
                release.set()
                worker.join(3)
        self.assertFalse(worker.is_alive())
        self.assertEqual(len(errors), 1)
        self.assertIsInstance(errors[0], investigation.Conflict)
        with closing(core.connect(self.application.dbpath)) as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM research_records').fetchone()[0], 0)

    def test_identity_changed_during_network_discards_result(self):
        def fetch(url):
            with self.application.lock, closing(core.connect(self.application.dbpath)) as db:
                row = db.execute('SELECT id,data FROM observations WHERE business_id=? LIMIT 1', (self.bid,)).fetchone()
                data = json.loads(row['data'])
                data['name'] = 'Changed Fictional Identity'
                with db:
                    db.execute('UPDATE observations SET data=? WHERE id=?', (json.dumps(data), row['id']))
            return url, b'<p>Fictional test</p>', 'text/html'
        with patch.object(research.network, 'fetch_page', side_effect=fetch), self.assertRaisesRegex(investigation.Conflict, 'identity'):
            self.application.capture_research(self.bid, self.payload)
        with closing(core.connect(self.application.dbpath)) as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM research_records').fetchone()[0], 0)

    def test_full_saved_html_is_not_retained_for_idempotency(self):
        marker = 'UNRETAINED_SOURCE_' + 'q' * 1000
        payload = dict(request_id='private-source', mode='paste', url='https://example.com/',
                       retention_confirmed=True, observed_at='2026-01-01',
                       content='<p>Fictional evidence.</p><!--' + marker + '-->')
        first = self.application.capture_research(self.bid, payload)
        second = self.application.capture_research(self.bid, payload)
        self.assertEqual(first['record']['id'], second['record']['id'])
        with closing(core.connect(self.application.dbpath)) as db:
            stored = db.execute('SELECT payload,data FROM research_records').fetchone()
            self.assertNotIn(marker, stored['payload'] + stored['data'])


if __name__ == '__main__':
    unittest.main()
