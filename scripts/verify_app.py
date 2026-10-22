"""Exercise the installed browser app through real HTTP, using fictional data only."""
import http.client
import io
import json
import re
import tempfile
import threading
import zipfile

from prospect.app import create_server


def verify():
    with tempfile.TemporaryDirectory(prefix='prospect-review-app-') as workspace:
        server = create_server(workspace, port=0)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        token = ''

        def request(path, body=None):
            connection = http.client.HTTPConnection('127.0.0.1', server.server_port, timeout=10)
            headers = {'X-Prospect-Token': token}
            if body is not None:
                headers['Content-Type'] = 'application/json'
            connection.request('GET' if body is None else 'POST', path,
                               body=None if body is None else json.dumps(body).encode(), headers=headers)
            response = connection.getresponse()
            data = response.read()
            connection.close()
            assert response.status == 200, (path, response.status, data)
            return json.loads(data) if 'application/json' in response.getheader('Content-Type', '') else data

        try:
            html = request('/').decode()
            token = re.search(r'name="session-token" content="([^"]+)"', html).group(1)
            assert token != '__SESSION_TOKEN__'
            assert request('/app.css') and request('/app.js')
            assert request('/api/state')['counts']['total'] == 0
            request('/api/demo', {})
            queue = request('/api/businesses?limit=1')
            assert len(queue['records']) == 1 and len(queue['matching_ids']) > 1
            record = queue['records'][0]
            request('/api/businesses/' + str(record['id']) + '/review', {'changes': {
                'website_status': 'uncertain', 'evidence_observed_at': '2026-10-15',
                'evidence_url': 'https://example.com/installed-app-test',
                'notes': 'SIMULATED installed app check; no real business reviewed.'}})
            assert request('/api/businesses/' + str(record['id']))['review_history_count'] == 1
            exported = request('/api/exports', {'ids': queue['matching_ids'], 'format': 'csv'})
            with zipfile.ZipFile(io.BytesIO(request(exported['download_url']))) as bundle:
                assert set(bundle.namelist()) == {'businesses.csv', 'businesses.csv.provenance.json'}
                assert json.loads(bundle.read('businesses.csv.provenance.json'))['sources']
            assert request('/api/state')['exports'][0]['id'] == exported['id']
            return {'installed_app_http': True, 'bundled_web_assets': True,
                    'app_demo_review_filtered_export': True}
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=10)


if __name__ == '__main__':
    print(json.dumps(verify()))
