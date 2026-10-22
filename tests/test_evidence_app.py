"""Loopback desk evidence integration uses one immutable source snapshot."""
from contextlib import closing
from pathlib import Path
import tempfile
from unittest import TestCase
from unittest.mock import patch

from prospect import core, evidence
import test_app as app_tests


class EvidenceAppTests(TestCase):
    start, stop, request, ok = (app_tests.AppTests.start, app_tests.AppTests.stop,
                               app_tests.AppTests.request, app_tests.AppTests.ok)
    row, preview, rights, imported = (app_tests.AppTests.row, app_tests.AppTests.preview,
                                    app_tests.AppTests.rights, app_tests.AppTests.imported)
    tearDown = app_tests.AppTests.tearDown

    def setUp(self):
        scratch = Path(__file__).resolve().parents[1] / '.test-temp/v0.6-evidence-backend'
        scratch.mkdir(parents=True, exist_ok=True)
        self.temporary = tempfile.TemporaryDirectory(dir=scratch)
        self.workspace = Path(self.temporary.name)
        self.start()

    def test_desk_contains_same_changes_answers_and_seen_state_without_writes(self):
        bid = self.imported()
        path = self.workspace / 'review.sqlite3'
        with closing(core.connect(path)) as db:
            core.import_rows(db, [core.normalize(self.row(websites=['https://fictional.example']))],
                             dict(source='fictional'), 'synthetic-change')
            core.review(db, bid, dict(website_status='uncertain', evidence_url='https://example.com/check', notes='Synthetic'))
        changes = self.ok('GET', '/api/changes')['items'][0]
        self.ok('POST', '/api/changes/seen', dict(reason_ids=[changes['reasons'][0]['id']], seen=True))
        changes = self.ok('GET', '/api/changes')['items'][0]
        with closing(core.connect(path)) as db:
            before = list(db.iterdump())
        desk = self.ok('GET', f'/api/businesses/{bid}/investigation')
        self.assertEqual(desk['evidence']['change_reasons'], changes['reasons'])
        self.assertEqual(desk['saved_answers'], changes['saved_answers'])
        self.assertEqual(desk['evidence']['source_groups'][0]['latest'], desk['business']['observations'][-1])
        with closing(core.connect(path)) as db:
            self.assertEqual(list(db.iterdump()), before)
        self.stop()
        self.start()
        self.assertEqual(self.ok('GET', f'/api/businesses/{bid}/investigation')['evidence'], desk['evidence'])

    def test_concurrent_import_after_snapshot_cannot_change_evidence_or_reason_tokens(self):
        bid = self.imported()
        base = f'/api/businesses/{bid}/investigation'
        before = self.ok('GET', base)
        original = evidence.read

        def write_during_evidence(snapshot, business, config):
            with closing(core.connect(self.workspace / 'review.sqlite3')) as writer:
                core.import_rows(writer, [core.normalize(self.row(websites=['https://new.example']))],
                                 dict(source='fictional'), 'concurrent-synthetic')
                core.review(writer, bid, dict(website_status='uncertain', evidence_url='https://example.com/new', notes='Concurrent'))
            return original(snapshot, business, config)

        with patch('prospect.app.evidence.read', side_effect=write_during_evidence):
            during = self.ok('GET', base)
        self.assertEqual(during['evidence'], before['evidence'])
        self.assertEqual(during['saved_answers'], before['saved_answers'])
        self.assertEqual(during['source_revision'], before['source_revision'])
        self.assertEqual(during['review_revision'], before['review_revision'])
        current = self.ok('GET', base)
        self.assertEqual(current['evidence']['source_groups'][0]['latest']['websites'], ['https://new.example'])
        self.assertTrue(current['evidence']['change_reasons'])
        self.assertNotEqual(current['source_revision'], during['source_revision'])
        self.assertNotEqual(current['review_revision'], during['review_revision'])

    def test_historical_website_context_explains_current_exclusion(self):
        bid = self.imported()
        with closing(core.connect(self.workspace / 'review.sqlite3')) as db:
            core.import_rows(db, [core.normalize(self.row(websites=['https://retained.example']))], dict(source='fictional'), 'has-site')
            core.import_rows(db, [core.normalize(self.row())], dict(source='fictional'), 'omitted-site')
        desk = self.ok('GET', f'/api/businesses/{bid}/investigation')
        self.assertEqual(desk['evidence']['source_groups'][0]['latest']['websites'], [])
        self.assertIn('website_present', desk['business']['excluded_reasons'])
        retained = desk['evidence']['retained_signals'][0]
        self.assertEqual(retained['kind'], 'website')
        self.assertEqual(retained['value'], 'https://retained.example')
        self.assertIn('does not establish removal', retained['explanation'])
