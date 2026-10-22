"""Synthetic source comparisons: preserve evidence and human work."""
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

from prospect import changes, core, providers


ROOT = Path(__file__).resolve().parents[1]


class SourceChangesTests(unittest.TestCase):
    def setUp(self):
        self.db = core.connect(':memory:')
        self.addCleanup(self.db.close)
        self.manifest = core.load_rights(ROOT / 'fixtures/rights.synthetic.json', 'canonical')

    def row(self, **overrides):
        return core.normalize(providers.canonical({
            'source_id': 'fictional-1', 'name': 'Example Painting', 'country': 'US',
            'state': 'OR', 'city': 'Example City', 'address': '1 Example Lane',
            'phone': '5035550100', 'categories': ['painting'], 'activity': 'open',
            'observed_at': '2026-01-02', 'source_url': 'https://example.org/listing', **overrides}))

    def load(self, *rows, source=None):
        manifest = {**self.manifest, **({'source': source} if source else {})}
        return core.import_rows(self.db, rows, manifest, 'synthetic-input-hash')

    def events(self):
        return [dict(id=r['id'], business_id=r['business_id'], **json.loads(r['data']))
                for r in self.db.execute('SELECT * FROM change_events ORDER BY id')]

    def revision(self):
        return changes.source_revision(self.db, 1)

    def test_first_import_and_upgrade_do_not_backfill_events(self):
        result = self.load(self.row())
        self.assertEqual(result['comparison']['new_businesses'], 1)
        self.assertEqual(self.events(), [])
        changes.initialize(self.db)
        self.assertEqual(self.events(), [])
        self.assertEqual(changes.batch_summary(self.db, result['batch_id']), result['comparison'])

    def test_material_change_revert_and_repeats(self):
        self.load(self.row())
        initial = self.revision()
        changed = self.load(self.row(website='https://example.org/painting'))
        event = self.events()[0]
        self.assertEqual(event['kind'], 'source_change')
        self.assertEqual(event['questions'], ['website'])
        self.assertEqual((event['before_batch_id'], event['after_batch_id']), (1, 2))
        self.assertEqual(event['fields'][0]['before'], [])
        self.assertNotEqual(initial, self.revision())
        self.assertEqual(changed['comparison']['changed_businesses'], 1)
        repeated = self.load(self.row(website='https://example.org/painting'))
        self.assertEqual(repeated['comparison']['unchanged_rows'], 1)
        self.assertEqual(len(self.events()), 1)
        self.load(self.row())
        self.assertEqual(len(self.events()), 2)
        self.assertNotEqual(initial, self.revision())  # Retained intervening evidence still matters.
        reverted = self.revision()
        self.load(self.row())
        self.assertEqual(reverted, self.revision())
        # The merged display deliberately retains the original website signal.
        self.assertEqual(core.all_businesses(self.db)[0]['websites'], ['https://example.org/painting'])

    def test_formatting_order_and_provenance_only_are_quiet(self):
        self.load(self.row(websites=['https://EXAMPLE.org', 'https://example.org/Services'],
                           categories=['Painting', 'Decorator']))
        revision = self.revision()
        result = self.load(self.row(name='Example  Painting!', address='1 Example Lane.',
            phones=['+1 (503) 555-0100', '5035550100'],
            websites=['https://example.org/Services', 'https://example.org/'],
            categories=['decorator', 'painting', 'painting'],
            observed_at='2026-02-03', source_url='https://example.org/new-provenance'))
        self.assertEqual(self.events(), [])
        self.assertEqual(self.revision(), revision)
        self.assertEqual(result['comparison']['unchanged_rows'], 1)
        self.load(self.row(websites=['https://example.org/services']))
        self.assertTrue(self.events())  # Path casing/profile identity is meaningful.

    def test_additional_identity_no_missing_field_retraction(self):
        self.load(self.row(website='https://example.org/site'))
        result = self.load(self.row(source_id='another-alias'), source='other-source')
        self.assertEqual(result['comparison']['additional_sources'], 1)
        self.assertEqual(result['inserted'], 0)
        self.assertEqual(self.events(), [])
        self.load(self.row(source_id='third-alias', activity='closed'), source='third-source')
        event = self.events()[0]
        self.assertEqual(event['kind'], 'source_conflict')
        self.assertEqual(event['fields'][0]['field'], 'activity')
        self.assertTrue(any('Additional source' in w for w in event['warnings']))

    def test_missing_entire_rows_and_skipped_rows_do_not_delete(self):
        self.load(self.row())
        result = self.load(self.row(source_id='unsupported', country='CA', state='BC'))
        self.assertEqual(result['comparison']['skipped_rows'], 1)
        self.assertEqual(self.events(), [])
        self.assertEqual(len(core.all_businesses(self.db)), 1)

    def test_additional_source_added_website_social_and_brand_alert_once(self):
        self.load(self.row())
        for field, value, question in (
                ('websites', ['https://example.org/new-site'], 'website'),
                ('socials', ['https://instagram.com/fictional_painting'], 'website'),
                ('brand', 'Fictional National Brand', 'fit')):
            with self.subTest(field=field):
                count = len(self.events())
                row = self.row(source_id='new-' + field, **{field: value})
                source = 'source-' + field
                result = self.load(row, source=source)
                self.assertEqual(result['comparison']['additional_sources'], 1)
                self.assertEqual(result['comparison']['changed_businesses'], 1)
                self.assertEqual(result['inserted'], 0)
                event = self.events()[-1]
                self.assertEqual(len(self.events()), count + 1)
                self.assertEqual(event['kind'], 'source_change')
                self.assertEqual(event['label'], 'Additional source evidence')
                self.assertEqual(event['questions'], [question])
                self.assertEqual(event['fields'][0]['field'], field)
                self.assertEqual(event['fields'][0]['after'], value)
                self.assertFalse(any('disagrees' in warning for warning in event['warnings']))
                self.assertTrue(any('missing fields do not establish absence' in warning for warning in event['warnings']))
                repeated = self.load(row, source=source)
                self.assertEqual(repeated['comparison']['changed_businesses'], 0)
                self.assertEqual(len(self.events()), count + 1)
        count = len(self.events())
        result = self.load(self.row(source_id='omitted'), source='omission-source')
        self.assertEqual(result['comparison']['additional_sources'], 1)
        self.assertEqual(result['comparison']['changed_businesses'], 0)
        self.assertEqual(len(self.events()), count)

    def test_additional_source_nonempty_disagreement_stays_conflict(self):
        self.load(self.row(websites=['https://example.org/original']))
        self.load(self.row(source_id='new-alias', websites=['https://example.org/different'],
                           brand='Fictional New Brand'), source='new-source')
        event = self.events()[0]
        self.assertEqual(event['kind'], 'source_conflict')
        self.assertNotEqual(event.get('label'), 'Additional source evidence')
        self.assertEqual({field['field'] for field in event['fields']}, {'websites', 'brand'})
        self.assertTrue(any('disagrees' in warning for warning in event['warnings']))

    def test_duplicate_alias_last_row_baseline_and_repeat_is_quiet(self):
        self.load(self.row())
        rows = [self.row(activity='closed'), self.row(activity='open')]
        result = self.load(*rows)
        self.assertEqual(result['comparison']['identity_warnings'], 1)
        self.assertEqual(self.events()[0]['kind'], 'identity_warning')
        self.assertTrue(any('Conflicting rows' in w for w in self.events()[0]['warnings']))
        self.load(*rows)
        self.assertEqual(len(self.events()), 1)
        self.assertEqual(len(core.all_businesses(self.db)[0]['observations']), 5)
        self.load(self.row(activity='closed'))
        self.assertEqual(len(self.events()), 2)

    def test_reused_alias_retains_binding_with_prominent_warning(self):
        self.load(self.row())
        result = self.load(self.row(name='Different Business', phones=['5035550199'], address='99 Other Road'))
        self.assertEqual(result['inserted'], 0)
        self.assertEqual(result['comparison']['identity_warnings'], 1)
        self.assertTrue(any('reused source identity' in w for w in self.events()[0]['warnings']))

    def test_alias_matching_other_business_warns_without_merging(self):
        self.load(self.row(), self.row(source_id='second', name='Second Painting', phones=['5035550199'], address='99 Other Road'))
        self.load(self.row(name='Second Painting', phones=['5035550199'], address='99 Other Road'))
        self.assertEqual(len(core.all_businesses(self.db)), 2)
        self.assertTrue(any('another business' in w for w in self.events()[0]['warnings']))
        self.load(self.row(name='Second Painting', phones=['5035550199'], address='99 Other Road'))
        self.assertEqual(len(self.events()), 1)

    def test_older_unknown_and_future_dates_labeled_without_refresh(self):
        self.load(self.row())
        self.load(self.row(activity='closed', observed_at='2026-01-01'))
        self.assertTrue(any('predates' in w for w in self.events()[-1]['warnings']))
        self.load(self.row(activity='open', observed_at=''))
        self.assertTrue(any('unknown' in w for w in self.events()[-1]['warnings']))
        self.load(self.row(activity='closed', observed_at='2999-01-01'))
        self.assertTrue(any('future' in w for w in self.events()[-1]['warnings']))

    def test_human_review_history_and_suppressions_survive(self):
        self.load(self.row())
        core.review(self.db, 1, dict(website_status='no_site_found', activity='active',
            evidence_url='https://example.org/check', notes='Fictional human observation',
            evidence_observed_at='2026-01-02', disposition='contacted'))
        reviews = [tuple(r) for r in self.db.execute('SELECT * FROM reviews')]
        suppressions = [tuple(r) for r in self.db.execute('SELECT * FROM suppressions')]
        self.load(self.row(website='https://example.org/site', activity='closed'))
        self.assertEqual(reviews, [tuple(r) for r in self.db.execute('SELECT * FROM reviews')])
        self.assertEqual(suppressions, [tuple(r) for r in self.db.execute('SELECT * FROM suppressions')])

    def test_failed_iteration_rolls_back_observations_batches_and_events(self):
        self.load(self.row())

        def failing_rows():
            yield self.row(activity='closed')
            raise ValueError('Fictional malformed row')

        before = {table: [tuple(r) for r in self.db.execute('SELECT * FROM ' + table)]
                  for table in ('observations', 'batches', 'change_events', 'runs')}
        with self.assertRaisesRegex(ValueError, 'malformed'):
            core.import_rows(self.db, failing_rows(), self.manifest, 'bad-file')
        for table, rows in before.items():
            self.assertEqual(rows, [tuple(r) for r in self.db.execute('SELECT * FROM ' + table)])

    def test_identity_answer_survives_unrelated_review_and_replace_resets_it(self):
        self.load(self.row())
        evidence = dict(evidence_url='https://example.org/check', notes='Fictional identity check',
                        evidence_observed_at='2026-01-02')
        first = core.review(self.db, 1, {**evidence, 'identity_status': 'same_business'})
        second = core.review(self.db, 1, {**evidence, 'website_status': 'uncertain'})
        self.assertEqual(second['identity_status'], first['identity_status'])
        self.assertEqual(core.review(self.db, 1, evidence, replace=True)['identity_status'], 'unknown')
        with self.assertRaisesRegex(ValueError, 'identity_status'):
            core.review(self.db, 1, {**evidence, 'identity_status': 'invented'})

    def test_legacy_missing_identity_is_unknown_without_restoring_old_assertions(self):
        self.load(self.row())
        self.db.execute('INSERT INTO reviews(business_id,created_at,data) VALUES(?,?,?)',
                        (1, '2026-01-02', json.dumps({'identity_status': 'same_business'})))
        self.db.execute('INSERT INTO reviews(business_id,created_at,data) VALUES(?,?,?)',
                        (1, '2026-01-03', json.dumps({'website_status': 'uncertain'})))
        self.db.commit()
        latest = core.review(self.db, 1, dict(evidence_url='https://example.org/check', notes='Fictional patch'))
        self.assertEqual(latest['identity_status'], 'unknown')

    def test_file_entry_point_shares_comparison_integration(self):
        source = ROOT / 'fixtures/businesses.csv'
        rights = ROOT / 'fixtures/rights.synthetic.json'
        core.import_data(self.db, source, 'canonical', rights)
        second = core.import_data(self.db, source, 'canonical', rights)
        self.assertEqual(second['comparison']['new_businesses'], 0)
        self.assertIn('comparison', second)

    def test_concurrent_import_reads_identity_after_writer_lock(self):
        scratch = ROOT / '.test-temp-changes'
        scratch.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=scratch) as folder:
            path = Path(folder) / 'concurrent.sqlite3'
            ready = threading.Barrier(3)
            first_go, second_go = threading.Event(), threading.Event()
            first_read, second_attempt, release = threading.Event(), threading.Event(), threading.Event()
            errors, results = [], []
            original = core.all_businesses

            def checked_baseline(db):
                result = original(db)
                if threading.current_thread().name == 'first-import':
                    self.assertTrue(db.in_transaction, 'Identity baseline must be read under writer lock')
                    first_read.set()
                    if not release.wait(3):
                        raise RuntimeError('Test failed to release first import')
                return result

            def worker(first):
                db = core.connect(path)
                try:
                    if not first:
                        db.set_trace_callback(lambda sql: second_attempt.set() if sql == 'BEGIN IMMEDIATE' else None)
                    ready.wait(3)
                    (first_go if first else second_go).wait(3)
                    results.append(core.import_rows(db, [self.row()], self.manifest, 'same-input'))
                except BaseException as exc:
                    errors.append(exc)
                finally:
                    db.close()

            with patch('prospect.core.all_businesses', side_effect=checked_baseline):
                first = threading.Thread(target=worker, args=(True,), name='first-import')
                second = threading.Thread(target=worker, args=(False,), name='second-import')
                first.start()
                second.start()
                try:
                    ready.wait(3)
                    first_go.set()
                    self.assertTrue(first_read.wait(3))
                    second_go.set()
                    self.assertTrue(second_attempt.wait(3))
                finally:
                    first_go.set()
                    second_go.set()
                    release.set()
                    first.join(5)
                    second.join(5)
            self.assertEqual(errors, [])
            self.assertEqual(sorted(r['inserted'] for r in results), [0, 1])
            db = core.connect(path)
            try:
                self.assertEqual(db.execute('SELECT COUNT(*) FROM businesses').fetchone()[0], 1)
                self.assertEqual(db.execute('SELECT COUNT(*) FROM observations').fetchone()[0], 2)
            finally:
                db.close()


if __name__ == '__main__':
    unittest.main()
