"""Exercise the installed browser app through real HTTP, using fictional data only."""
import http.client
import io
import json
import re
import sqlite3
from contextlib import closing
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
            assert set(investigation['evidence']) == {'source_groups', 'change_reasons', 'retained_signals'}
            assert investigation['evidence']['source_groups']
            assert investigation['saved_answers']['website']['answers']['website_status'] == 'uncertain'
            assert not investigation['saved_answers']['website']['basis_known']
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
            # Saved-page assistance must work fully offline in the installed wheel.
            before_research = request(base + '/investigation')
            business = request(base)
            page = ('<html><title>' + business['name'] + '</title><body><h1>' +
                    business['name'] + '</h1><p>' + business['address'] + ', ' +
                    business['city'] + ', ' + business['state'] +
                    '</p><p>RESEARCH_ONLY_SENTINEL</p></body></html>')
            research_payload = {'request_id': 'installed-research', 'mode': 'paste',
                                'url': 'https://example.com/installed-research',
                                'retention_confirmed': True, 'content': page,
                                'observed_at': '2026-10-15'}
            captured = request(base + '/research', research_payload)['record']
            assert captured['mode'] == 'paste' and captured['observed_at'] == '2026-10-15'
            assert captured['content_sha256'] and 'RESEARCH_ONLY_SENTINEL' in captured['excerpt']
            assert request(base + '/research', research_payload)['record']['id'] == captured['id']
            assert len(request(base + '/research')['records']) == 1
            assert request(base + '/investigation') == before_research
            assert request(base)['review_history_count'] == business['review_history_count']
            request(base + '/research/' + captured['id'] + '/dismiss', {'dismissed': True})
            assert request(base + '/research')['records'][0]['dismissed'] is True
            exported = request('/api/exports', {'ids': queue['matching_ids'], 'format': 'csv'})
            with zipfile.ZipFile(io.BytesIO(request(exported['download_url']))) as bundle:
                assert set(bundle.namelist()) == {'businesses.csv', 'businesses.csv.provenance.json'}
                assert json.loads(bundle.read('businesses.csv.provenance.json'))['sources']
                assert b'RESEARCH_ONLY_SENTINEL' not in bundle.read('businesses.csv')
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
            restored_research = request(base + '/research')['records']
            assert len(restored_research) == 1 and restored_research[0]['id'] == captured['id']
            assert restored_research[0]['dismissed'] is True
            # The installed change inbox must share core reimport semantics and
            # retain the existing saved review/draft after adding new evidence.
            prior = request(base)
            observation = dict(prior['observations'][0])
            source_name = observation.pop('source')
            observation.pop('batch_id', None)
            observation['websites'] = ['https://installed-change.example']
            preview = request('/api/import/preview', {'filename': 'installed-change.json',
                'content': json.dumps([observation]), 'adapter': 'canonical'})
            compared = request('/api/import/compare', {'preview_id': preview['preview_id'], 'source': source_name})
            assert compared['summary']['changed_businesses'] == 1
            rights = {'source': source_name, 'license': 'synthetic', 'terms_url': 'https://example.com/synthetic',
                'attribution': 'Installed synthetic check', 'reviewed_at': '2026-01-01',
                'retention_permitted': True, 'export_permitted': True, 'lead_discovery_permitted': True}
            request('/api/import/commit', {'preview_id': preview['preview_id'],
                'comparison_id': compared['comparison_id'], 'rights': rights})
            assert request(base)['review'] == prior['review']
            assert request(base + '/investigation')['source_changed']
            item = next(item for item in request('/api/changes')['items'] if item['business_id'] == record['id'])
            reason = next(reason for reason in item['reasons'] if reason['id'].startswith('change:'))
            request('/api/changes/seen', {'reason_ids': [reason['id']], 'seen': True})
            item = next(item for item in request('/api/changes')['items'] if item['business_id'] == record['id'])
            assert next(r for r in item['reasons'] if r['id'] == reason['id'])['seen']
            # A later omission retains the earlier website signal. Last imported
            # is explicit even when the supplied observation date is older.
            observation['websites'] = []
            observation['observed_at'] = '2026-01-01'
            omitted = request('/api/import/preview', {'filename': 'installed-omission.json',
                'content': json.dumps([observation]), 'adapter': 'canonical'})
            omitted_comparison = request('/api/import/compare', {
                'preview_id': omitted['preview_id'], 'source': source_name})
            request('/api/import/commit', {'preview_id': omitted['preview_id'],
                'comparison_id': omitted_comparison['comparison_id'], 'rights': rights})

            def persisted_review_state():
                # Observe the actual installed workspace, including nonempty
                # review/draft/triage records, without creating another app.
                with closing(sqlite3.connect(server.app.dbpath)) as db:
                    return {table: db.execute('SELECT * FROM ' + table + ' ORDER BY rowid').fetchall()
                            for table in ('reviews', 'investigation_state',
                                          'investigation_sessions', 'inbox_acknowledgements')}

            before_read = persisted_review_state()
            desk = request(base + '/investigation')
            assert request(base + '/investigation') == desk
            current_item = next(item for item in request('/api/changes')['items']
                                if item['business_id'] == record['id'])
            assert desk['saved_answers'] == current_item['saved_answers']
            assert desk['evidence']['change_reasons'] == current_item['reasons']
            group = next(group for group in desk['evidence']['source_groups']
                         if group['source'] == source_name and group['source_id'] == observation['source_id'])
            assert group['latest']['websites'] == []
            assert group['latest']['observed_at'] == '2026-01-01'
            assert group['observation_count'] == len(group['history']) + 1
            assert group['history'][0]['websites'] == ['https://installed-change.example']
            assert any(signal['kind'] == 'website' and signal['value'] == 'https://installed-change.example'
                       for signal in desk['evidence']['retained_signals'])
            assert desk['draft'] is not None and desk['source_changed']
            assert desk['business']['review'] == prior['review']
            assert 'website_present' in desk['business']['excluded_reasons']
            assert persisted_review_state() == before_read
            # Unfinished work is discoverable without changing persisted state.
            work = request('/api/drafts?limit=1')
            assert work['total'] >= 1
            listed = next(item for item in request('/api/drafts')['items'] if item['id'] == record['id'])
            assert 'website' in listed['questions'] and listed['source_changed']
            assert persisted_review_state() == before_read
            fit = {'ownership': 'unknown', 'residential': 'unknown', 'service_fit': 'unknown',
                   'delivery': 'unknown', 'notes': 'SIMULATED retained unfinished fit sentinel',
                   'evidence_url': 'https://example.com/unfinished-fit', 'evidence_observed_at': '2026-10-15'}
            edited = dict(desk['draft'], questions={**desk['draft']['questions'], 'fit': fit})
            saved = request(base + '/draft', {'review_revision': desk['review_revision'],
                'draft_revision': desk['draft_revision'], 'source_revision': desk['draft_source_revision'], 'draft': edited})
            request(base + '/review', {'changes': {'ownership': 'independent',
                'evidence_url': 'https://example.com/later-correction', 'notes': 'SIMULATED later saved correction'},
                'review_revision': saved['review_revision'], 'source_revision': desk['source_revision']})
            changed = request(base + '/investigation')
            assert changed['draft_conflicts']['website']['changed']
            assert changed['draft_conflicts']['fit']['changed']
            old_fit_revision = changed['draft_review_revisions']['fit']
            before_resolution_reviews = request(base)['review_history_count']
            resolved = request(base + '/draft', {'review_revision': changed['review_revision'],
                'draft_revision': changed['draft_revision'], 'source_revision': changed['draft_source_revision'],
                'draft': changed['draft'], 'resolve_review_revision': changed['review_revision'],
                'resolve_questions': {'website': 'keep'}})
            assert not resolved['draft_conflicts']['website']['changed']
            assert resolved['draft_conflicts']['fit']['changed'] and resolved['source_changed']
            assert request(base)['review_history_count'] == before_resolution_reviews
            # Accepting source context is separate and cannot resolve fit's review baseline.
            accepted_source = request(base + '/draft', {'review_revision': resolved['review_revision'],
                'draft_revision': resolved['draft_revision'], 'source_revision': resolved['source_revision'],
                'draft': resolved['draft']})
            assert not accepted_source['source_changed'] and accepted_source['draft_conflicts']['fit']['changed']
            partial_payload = {'review_revision': accepted_source['review_revision'],
                'draft_revision': accepted_source['draft_revision'], 'source_revision': accepted_source['source_revision'],
                'request_id': 'installed-partial-resume', 'questions': {'website': accepted_source['draft']['questions']['website']}}
            partial = request(base + '/investigation', partial_payload)
            assert request(base + '/investigation', partial_payload) == partial
            remaining = request(base + '/investigation')
            assert remaining['draft']['questions'] == {'fit': fit}
            assert remaining['draft_review_revisions']['fit'] == old_fit_revision
            assert remaining['draft_conflicts']['fit']['changed']
            server.shutdown(); server.server_close(); thread.join(timeout=10)
            server = create_server(workspace, port=0)
            thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
            token = server.app.token
            resumed = request(base + '/investigation')
            assert resumed['draft']['questions'] == {'fit': fit} and resumed['review_changed']
            assert next(item for item in request('/api/drafts')['items'] if item['id'] == record['id'])['questions'] == ['fit']
            return {'installed_app_http': True, 'bundled_web_assets': True,
                    'app_demo_review_filtered_export': True,
                    'installed_investigation_draft_restart': True,
                    'installed_guided_review_and_undo': True,
                    'installed_review_retry_idempotency': True,
                    'installed_research_capture_and_retry': True,
                    'installed_research_separate_from_reviews_exports': True,
                    'installed_research_dismissal_restart': True,
                    'installed_change_comparison_and_triage': True,
                    'installed_reimport_preserves_review_and_draft': True,
                    'installed_evidence_desk_read_model': True,
                    'installed_evidence_get_preserves_reviews_drafts_triage': True,
                    'installed_latest_import_and_retained_signal_explanation': True,
                    'installed_unfinished_worklist_read_only': True,
                    'installed_question_resolution_keeps_other_contexts': True,
                    'installed_partial_draft_restart': True}
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=10)


if __name__ == '__main__':
    print(json.dumps(verify()))
