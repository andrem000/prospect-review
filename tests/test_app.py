"""End-to-end loopback HTTP tests using only fictional local records."""
import http.client
import io
import json
import sqlite3
import tempfile
import threading
import unittest
import zipfile
from contextlib import closing
from pathlib import Path

from prospect import app


class AppTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.workspace = Path(self.temporary.name)
        self.start()

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

    def request(self, method, path, data=None, headers=None, raw=None):
        conn = http.client.HTTPConnection('127.0.0.1', self.server.server_port, timeout=5)
        h = {'X-Prospect-Token': self.server.app.token}
        if data is not None:
            h['Content-Type'] = 'application/json'
            raw = json.dumps(data).encode()
        if headers:
            h.update(headers)
        conn.request(method, path, body=raw, headers=h)
        response = conn.getresponse()
        body = response.read()
        result = json.loads(body) if 'application/json' in response.getheader('Content-Type', '') else body
        status = response.status
        conn.close()
        return status, result

    def ok(self, method, path, data=None):
        status, value = self.request(method, path, data)
        self.assertEqual(status, 200, value)
        return value

    def row(self, source_id='fictional-1', **changes):
        return dict(source_id=source_id, name='Fictional Cedar Painting', country='US', state='TX', city='Austin',
                    address='1 Fictional Way', categories=['painter'], phones=['2025550101'], **changes)

    def preview(self, rows=None, **extra):
        return self.ok('POST', '/api/import/preview', dict(filename='fictional.json',
            content=json.dumps(rows if rows is not None else [self.row()]), adapter='canonical', **extra))

    def rights(self):
        return dict(source='fictional', license='synthetic', terms_url='https://example.com/terms',
            attribution='Fictional tests', reviewed_at='2026-01-01', retention_permitted=True,
            export_permitted=True, lead_discovery_permitted=True)

    def imported(self):
        preview = self.preview()
        self.ok('POST', '/api/import/commit', dict(preview_id=preview['preview_id'], rights=self.rights()))
        return self.ok('GET', '/api/businesses')['matching_ids'][0]

    def test_workflow_persistence_and_read_only_get(self):
        self.assertEqual(self.ok('GET', '/api/state')['counts']['total'], 0)
        bid = self.imported()
        with closing(sqlite3.connect(self.workspace / 'review.sqlite3')) as db:
            count = db.execute('SELECT COUNT(*) FROM runs').fetchone()[0]
        for path in ('/api/state', '/api/businesses', f'/api/businesses/{bid}'):
            self.ok('GET', path)
        with closing(sqlite3.connect(self.workspace / 'review.sqlite3')) as db:
            self.assertEqual(count, db.execute('SELECT COUNT(*) FROM runs').fetchone()[0])
        changes = dict(activity='active', website_status='no_site_found', ownership='independent', residential='yes',
            evidence_url='https://example.com/fictional-first', evidence_observed_at='2026-01-01', notes='Fictional first check')
        first = self.ok('POST', f'/api/businesses/{bid}/review', dict(changes=changes))['review']
        changes = dict(website_status='uncertain', evidence_url='https://example.com/fictional-second',
                       evidence_observed_at='2026-01-02', notes='Fictional second check')
        second = self.ok('POST', f'/api/businesses/{bid}/review', dict(changes=changes))['review']
        self.assertEqual(second['activity_evidence_url'], first['activity_evidence_url'])
        self.assertEqual(second['activity_evidence_observed_at'], '2026-01-01')
        export = self.ok('POST', '/api/exports', {'ids': [bid], 'format': 'json'})
        binary = self.ok('GET', export['download_url'])
        with zipfile.ZipFile(io.BytesIO(binary)) as bundle:
            self.assertEqual(len(bundle.namelist()), 2)
            provenance = json.loads(bundle.read('businesses.json.provenance.json'))
            self.assertIn('original_sha256', provenance['sources'][0]['app_provenance'])
        config = self.ok('GET', '/api/state')['config']
        config['freshness_days'] = 30
        self.ok('POST', '/api/config', {'config': config})
        old_token = self.server.app.token
        self.stop()
        self.start()
        self.assertNotEqual(old_token, self.server.app.token)
        state = self.ok('GET', '/api/state')
        self.assertEqual(state['counts']['total'], 1)
        self.assertEqual(state['counts']['reviewed'], 1)
        self.assertEqual(state['config']['freshness_days'], 30)
        self.assertEqual(state['exports'][0]['id'], export['id'])
        self.assertEqual(self.ok('GET', export['download_url']), binary)

    def test_security_and_fixed_paths(self):
        for headers in ({'Host': 'evil.example'}, {'Origin': 'https://evil.example'}, {'X-Prospect-Token': ''},
                        {'Sec-Fetch-Site': 'cross-site'}):
            self.assertEqual(self.request('GET', '/api/state', headers=headers)[0], 403)
        self.assertEqual(self.request('POST', '/api/demo', raw=b'{}', headers={'Content-Type': 'text/plain'})[0], 415)
        self.assertEqual(self.request('POST', '/api/demo', raw=b'{}', headers={'Content-Length': str(app.MAX_BODY + 1)})[0], 413)
        for path in ('/../config.json', '/%2e%2e/config.json', '/api/exports/../../config.json/download', '/data/businesses.csv'):
            self.assertEqual(self.request('GET', path)[0], 404)
        for raw in (b'[]', b'{"x": NaN}', b'\xff', b'{'):
            self.assertEqual(self.request('POST', '/api/demo', raw=raw, headers={'Content-Type': 'application/json'})[0], 400)
        self.assertEqual(self.server.server_address[0], '127.0.0.1')

    def test_invalid_imports_rights_and_atomicity(self):
        preview = self.preview([self.row(), {'name': 'Invalid fictional record'}])
        self.assertFalse(preview['can_import'])
        self.assertEqual(len(preview['errors']), 1)
        self.assertEqual(self.request('POST', '/api/import/commit', {'preview_id': preview['preview_id'], 'rights': self.rights()})[0], 400)
        self.assertEqual(self.ok('GET', '/api/state')['counts']['total'], 0)
        valid = self.preview()
        rights = self.rights()
        rights.update(license='fictional-test-license', notice_files=['../../secret'], notices=[{'text': 'not accepted'}])
        self.assertEqual(self.request('POST', '/api/import/commit', {'preview_id': valid['preview_id'], 'rights': rights})[0], 400)
        rights['terms_url'] = 'file:///secret'
        self.assertEqual(self.request('POST', '/api/import/commit', {'preview_id': valid['preview_id'], 'rights': rights,
            'notices': [{'name': '../../secret', 'text': 'Fictional notice'}]})[0], 400)
        rights['terms_url'] = 'https://example.com/terms'
        self.ok('POST', '/api/import/commit', {'preview_id': valid['preview_id'], 'rights': rights,
            'notices': [{'name': '../../secret', 'text': 'Fictional notice'}]})
        self.assertEqual(self.request('POST', '/api/import/commit', {'preview_id': valid['preview_id'], 'rights': rights})[0], 400)
        self.assertEqual(self.ok('GET', '/api/state')['counts']['total'], 1)
        notices = list((self.workspace / 'imports').glob('*/notice-0.txt'))
        self.assertEqual(notices[0].read_text(), 'Fictional notice')
        self.assertFalse((self.workspace / 'secret').exists())

    def test_mapping_filters_pagination_exclusions(self):
        source = 'identifier,title,country,state,city,kind\na,Fictional Alpha,US,TX,Austin,painter\nb,Fictional Beta,US,NC,Raleigh,plumber\n'
        preview = self.ok('POST', '/api/import/preview', {'filename': 'fictional.csv', 'content': source, 'adapter': 'canonical',
            'mapping': {'source_id': 'identifier', 'name': 'title', 'country': 'country', 'state': 'state', 'city': 'city', 'categories': 'kind'}})
        self.assertEqual(preview['valid_rows'], 2)
        self.ok('POST', '/api/import/commit', {'preview_id': preview['preview_id'], 'rights': self.rights()})
        rows = self.ok('GET', '/api/businesses?queue=all&sort=name&limit=1')
        self.assertEqual(len(rows['records']), 1)
        self.assertEqual(len(rows['matching_ids']), 2)
        self.assertEqual(self.ok('GET', '/api/businesses?q=Raleigh&state=NC')['total'], 1)
        self.ok('POST', '/api/exclusions', {'filename': 'fictional.json', 'content': json.dumps([{'source': 'fictional', 'source_id': 'a'}])})
        self.assertEqual(self.ok('GET', '/api/businesses?queue=excluded')['total'], 1)
        self.assertEqual(self.ok('GET', '/api/businesses')['total'], 1)
        self.assertEqual(self.request('POST', '/api/exclusions', {'filename': 'bad.json', 'content': json.dumps([
            {'source': 'fictional', 'source_id': 'b'}, {}])})[0], 400)
        self.assertEqual(self.ok('GET', '/api/businesses')['total'], 1)

    def test_review_export_config_validation(self):
        bid = self.imported()
        for changes in ({'activity_evidence_url': 'https://example.com'}, {'snapshot_action_id': 'fake'}, {'queue_hold': 'yes'}, {'activity': 1}):
            self.assertEqual(self.request('POST', f'/api/businesses/{bid}/review', {'changes': changes})[0], 400)
        for ids in ([], [bid, bid], [999], ['1'], [True]):
            self.assertEqual(self.request('POST', '/api/exports', {'ids': ids, 'format': 'json'})[0], 400)
        for config in (None, {'trades': {}}, {'trades': {'x': ['painter']}, 'regions': [{'state': 1}]}):
            self.assertEqual(self.request('POST', '/api/config', {'config': config})[0], 400)
        self.assertEqual(self.ok('GET', '/api/state')['config'], self.server.app.default)
        for query in ('limit=101', 'offset=-1', 'sort=evil', 'queue=unknown', 'limit=no'):
            self.assertEqual(self.request('GET', '/api/businesses?' + query)[0], 400)
        self.assertEqual(self.request('GET', '/api/businesses/999')[0], 404)

    def test_demo_guard_and_durable_contact_control(self):
        self.ok('POST', '/api/demo', {})
        state = self.ok('GET', '/api/state')
        self.assertEqual(state['counts']['total'], 8)
        self.assertEqual(state['counts']['reviewed'], 0)
        self.assertEqual(self.request('POST', '/api/demo', {})[0], 400)
        bid = self.ok('GET', '/api/businesses')['matching_ids'][0]
        for disposition in ('contacted', 'pending'):
            self.ok('POST', f'/api/businesses/{bid}/review', {'changes': {'disposition': disposition,
                'evidence_url': 'https://example.com/fictional', 'notes': 'Fictional control test'}})
        self.assertIn('suppressed_or_contacted', self.ok('GET', f'/api/businesses/{bid}')['excluded_reasons'])

    def test_limits_and_malformed_adapters(self):
        for filename, content, adapter in (('x.csv', 'a,a\n1,2', 'canonical'), ('x.json', '[1]', 'canonical'),
            ('x.json', '{}', 'canonical'), ('x.json', '[{}]', 'osm'), ('x.exe', '[]', 'canonical')):
            status, result = self.request('POST', '/api/import/preview', dict(filename=filename, content=content, adapter=adapter))
            self.assertTrue(status == 400 or not result['can_import'])
        self.assertEqual(self.request('POST', '/api/import/preview', dict(filename='x.json',
            content=json.dumps([self.row()] * 5001), adapter='canonical'))[0], 400)
        old = app.MAX_IDENTITIES
        try:
            app.MAX_IDENTITIES = 0
            preview = self.preview()
            self.assertEqual(self.request('POST', '/api/import/commit', {'preview_id': preview['preview_id'], 'rights': self.rights()})[0], 400)
            self.assertEqual(self.ok('GET', '/api/state')['counts']['total'], 0)
            self.assertFalse(list((self.workspace / 'imports').iterdir()))
        finally:
            app.MAX_IDENTITIES = old

    def test_adapter_imports_and_geography_skip(self):
        cases = [('osm', {'type': 'node', 'id': 123, 'tags': {'name': 'Fictional OSM Painter',
            'addr:country': 'US', 'addr:state': 'TX', 'addr:city': 'Austin', 'craft': 'painter'}}),
            ('overture', {'id': 'fictional-overture', 'names': {'primary': 'Fictional Overture Plumber'},
             'addresses': [{'country': 'US', 'region': 'NC', 'locality': 'Raleigh'}], 'categories': {'primary': 'plumber'}})]
        for adapter, row in cases:
            preview = self.ok('POST', '/api/import/preview', {'filename': 'fictional.jsonl',
                'content': json.dumps(row), 'adapter': adapter})
            self.assertTrue(preview['can_import'])
            rights = self.rights()
            rights['source'] = adapter
            self.ok('POST', '/api/import/commit', {'preview_id': preview['preview_id'], 'rights': rights})
        outside = self.row('fictional-outside')
        outside['country'] = 'CA'
        inside = self.row('fictional-inside')
        preview = self.preview([outside, inside])
        self.assertEqual(preview['skipped_rows'], 1)
        self.assertEqual(preview['valid_rows'], 1)
        metrics = self.ok('POST', '/api/import/commit', {'preview_id': preview['preview_id'], 'rights': self.rights()})['metrics']
        self.assertEqual(metrics['outside_us'], 1)
        self.assertEqual(self.ok('GET', '/api/state')['counts']['total'], 3)

    def test_canonical_brand_mapping_preserves_chain_exclusion(self):
        row = self.row(brand='Fictional National Brand')
        preview = self.preview([row])
        self.assertEqual(preview['mapping']['brand'], 'brand')
        self.assertEqual(preview['sample'][0]['brand'], row['brand'])
        self.ok('POST', '/api/import/commit', {'preview_id': preview['preview_id'], 'rights': self.rights()})
        self.assertEqual(self.ok('GET', '/api/businesses')['total'], 0)
        excluded = self.ok('GET', '/api/businesses?queue=excluded')
        self.assertEqual(excluded['total'], 1)
        self.assertIn('chain_signal', excluded['records'][0]['excluded_reasons'])
        detail = self.ok('GET', f"/api/businesses/{excluded['matching_ids'][0]}")
        self.assertEqual(detail['brands'], ['Fictional National Brand'])
        # Explicit remapping must preserve the same safety field too.
        renamed = self.row('fictional-other', brand_label='Fictional Second Brand')
        mapping = {key: key for key in app.FIELDS if key in renamed}
        mapping['brand'] = 'brand_label'
        preview = self.preview([renamed], mapping=mapping)
        self.assertEqual(preview['sample'][0]['brand'], 'Fictional Second Brand')

    def test_rights_dates_are_strict_and_cannot_be_future(self):
        preview = self.preview()
        for invalid in ('20260101', '2026-W01-1', '2026-02-30', '9999-01-01', '2026-01-01T00:00:00Z'):
            rights = self.rights()
            rights['reviewed_at'] = invalid
            status, result = self.request('POST', '/api/import/commit', {'preview_id': preview['preview_id'], 'rights': rights})
            self.assertEqual(status, 400, result)
            self.assertEqual(self.ok('GET', '/api/state')['counts']['total'], 0)
            self.assertFalse(list((self.workspace / 'imports').iterdir()))
        self.ok('POST', '/api/import/commit', {'preview_id': preview['preview_id'], 'rights': self.rights()})

    def test_static_assets_token_and_headers(self):
        status, html = self.request('GET', '/')
        self.assertEqual(status, 200)
        self.assertIn(self.server.app.token.encode(), html)
        self.assertNotIn(b'__SESSION_TOKEN__', html)
        status, css = self.request('GET', '/app.css')
        self.assertEqual(status, 200)
        self.assertTrue(css)
        status, javascript = self.request('GET', '/app.js')
        self.assertEqual(status, 200)
        self.assertTrue(javascript)
        # Browser assets are bundled resources, never user-requested paths.
        self.assertEqual(self.request('GET', '/web/app.css')[0], 404)
        conn = http.client.HTTPConnection('127.0.0.1', self.server.server_port)
        conn.request('GET', '/')
        response = conn.getresponse()
        self.assertIn("script-src 'self'", response.getheader('Content-Security-Policy'))
        self.assertIsNone(response.getheader('Access-Control-Allow-Origin'))
        self.assertEqual(response.getheader('X-Content-Type-Options'), 'nosniff')
        response.read()
        conn.close()


if __name__ == '__main__':
    unittest.main()
