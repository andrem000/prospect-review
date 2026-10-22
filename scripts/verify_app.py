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
            # Exercise the new module from the wheel, not the source checkout.
            base = '/api/businesses/' + str(record['id'])
            investigation = request(base + '/investigation')
            questions = {
                'identity': {'identity_status': 'same_business',
                             'evidence_url': 'https://example.com/fictional-identity',
                             'evidence_observed_at': '2026-10-14',
                             'notes': 'SIMULATED: matching fictional name and address.'},
                'activity': {'activity': 'active',
                             'evidence_url': 'https://example.com/fictional-activity',
                             'evidence_observed_at': '2026-10-15',
                             'notes': 'SIMULATED: fictional service update.'}}
            draft = {'mode': 'guided', 'step': 2, 'questions': questions}
            saved_draft = request(base + '/draft', {
                'review_revision': investigation['review_revision'],
                'draft_revision': investigation['draft_revision'], 'draft': draft})
            assert request(base + '/investigation')['draft'] == draft
            payload = {'review_revision': saved_draft['review_revision'],
                       'draft_revision': saved_draft['draft_revision'],
                       'request_id': 'installed-guided-review', 'questions': questions}
            committed = request(base + '/investigation', payload)
            assert request(base + '/investigation', payload) == committed
            assert request(base)['review_history_count'] == 3
            assert request(base + '/investigation')['draft'] is None
            correction = request(base + '/investigation', {
                'review_revision': committed['review_revision'],
                'draft_revision': committed['draft_revision'],
                'request_id': 'installed-website-correction',
                'questions': {'website': {
                    'website_status': 'uncertain',
                    'evidence_url': 'https://example.com/fictional-website',
                    'evidence_observed_at': '2026-10-16',
                    'notes': 'SIMULATED: website-only correction.'}}})
            assert request(base)['review']['activity_evidence_observed_at'] == '2026-10-15'
            request(base + '/investigation/undo', {
                'session_id': correction['session_id'],
                'review_revision': correction['review_revision']})
            assert request(base)['review']['activity_evidence_observed_at'] == '2026-10-15'
            assert request(base + '/investigation')['draft'] is not None
            exported = request('/api/exports', {'ids': queue['matching_ids'], 'format': 'csv'})
            with zipfile.ZipFile(io.BytesIO(request(exported['download_url']))) as bundle:
                assert set(bundle.namelist()) == {'businesses.csv', 'businesses.csv.provenance.json'}
                assert json.loads(bundle.read('businesses.csv.provenance.json'))['sources']
            assert request('/api/state')['exports'][0]['id'] == exported['id']
            server.shutdown()
            server.server_close()
            thread.join(timeout=10)
            server = create_server(workspace, port=0)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            token = server.app.token
            assert request(base + '/investigation')['draft'] is not None
            assert request(base)['review']['activity_evidence_observed_at'] == '2026-10-15'
            return {'installed_app_http': True, 'bundled_web_assets': True,
                    'app_demo_review_filtered_export': True,
                    'installed_investigation_draft_restart': True,
                    'installed_guided_review_and_undo': True,
                    'installed_review_retry_idempotency': True}
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=10)


if __name__ == '__main__':
    print(json.dumps(verify()))
