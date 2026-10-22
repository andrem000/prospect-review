import csv
import json
import tempfile
import unittest
import subprocess
import sys
from pathlib import Path
from prospect import core, providers

ROOT = Path(__file__).resolve().parents[1]


class PipelineTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.db = core.connect(self.root / 'test.sqlite3')
        self.addCleanup(self.db.close)
        self.rights = ROOT / 'fixtures/rights.synthetic.json'
        self.config = json.loads((ROOT / 'config.example.json').read_text())

    def load(self):
        return core.import_data(self.db, ROOT / 'fixtures/businesses.csv', 'canonical', self.rights)

    def import_rows(self, rows):
        path = self.root / 'input.json'
        path.write_text(json.dumps(rows))
        return core.import_data(self.db, path, 'canonical', self.rights)

    def row(self, **kwargs):
        return {'source_id': 'x1', 'name': 'Test Plumbing', 'country': 'US', 'state': 'TX',
                'city': 'Austin', 'address': '1 Example Lane', 'trade': 'plumber',
                'phone': '5125550111', **kwargs}

    def test_end_to_end_yield_dedupe_and_suppression(self):
        m = self.load()
        self.assertEqual((m['records_read'], m['inserted'], m['duplicates'], m['outside_us']), (10, 8, 1, 1))
        core.suppress(self.db, ROOT / 'fixtures/suppression.csv')
        rows, metrics = core.search(self.db, self.config)
        self.assertEqual({r['name'] for r in rows}, {'Cedar Example Plumbing', 'Elm Example Electric'})
        self.assertEqual(metrics['candidate_yield'], .25)
        self.assertEqual(next(b for b in rows if b['name']=='Elm Example Electric')['website_signal'], 'social_only')
        self.assertFalse(any(r['qualified'] for r in rows))

    def test_reimport_is_idempotent_and_review_survives(self):
        self.load()
        core.review(self.db, 1, self.review_data())
        self.assertEqual(self.load()['inserted'], 0)
        self.assertEqual(len(core.all_businesses(self.db)), 8)
        self.assertEqual(core.all_businesses(self.db)[0]['review']['ownership'], 'independent')

    def review_data(self, **kwargs):
        return dict(website_status='no_site_found', activity='active', ownership='independent',
                    residential='yes', disposition='pending', evidence_url='https://example.com/evidence',
                    notes='Synthetic human check of identity, activity, ownership, service and website search.',
                    verified_website='', **kwargs)

    def test_qualification_requires_manual_signals(self):
        self.load()
        core.review(self.db, 1, self.review_data())
        rows, _ = core.search(self.db, self.config, qualified_only=True)
        self.assertEqual([b['id'] for b in rows], [1])
        self.assertEqual(rows[0]['verification_status'], 'no_site_found')

    def test_manual_site_excludes_and_history_retained(self):
        self.load()
        data = self.review_data()
        core.review(self.db, 1, data)
        data.update(website_status='site_found', verified_website='https://cedar.example.com')
        core.review(self.db, 1, data)
        rows, _ = core.search(self.db, self.config)
        self.assertNotIn(1, [b['id'] for b in rows])
        self.assertEqual(len(core.all_businesses(self.db)[0]['reviews']), 2)

    def test_old_website_signal_cannot_be_erased_by_empty_import(self):
        self.import_rows([self.row(website='https://test.example.com')])
        self.import_rows([self.row()])
        rows, _ = core.search(self.db, self.config)
        self.assertEqual(rows, [])

    def test_repeated_source_id_in_one_batch_preserves_website_evidence(self):
        self.import_rows([self.row(website='https://test.example.com'), self.row()])
        self.assertEqual(len(core.all_businesses(self.db)), 1)
        self.assertEqual(len(core.all_businesses(self.db)[0]['observations']), 2)
        self.assertEqual(core.search(self.db, self.config)[0], [])

    def test_invalid_region_cannot_silently_return_empty_queue(self):
        config = {**self.config, 'regions': [{'state': 'Texas'}]}
        with self.assertRaises(ValueError):
            core.search(self.db, config)

    def test_suppression_added_before_discovery_and_phone_normalization(self):
        path = self.root / 's.csv'
        path.write_text('phone\n+1 (512) 555-0111\n')
        core.suppress(self.db, path)
        self.import_rows([self.row()])
        self.assertEqual(core.search(self.db, self.config)[0], [])

    def test_domain_suppression(self):
        self.import_rows([self.row(website='https://www.test.example.com/path')])
        path = self.root / 's.json'
        path.write_text(json.dumps([{'domain': 'test.example.com'}]))
        core.suppress(self.db, path)
        rows, _ = core.search(self.db, self.config, include_excluded=True)
        self.assertIn('suppressed_or_contacted', rows[0]['excluded_reasons'])

    def test_names_alone_do_not_merge(self):
        self.import_rows([self.row(phone='', address=''), self.row(source_id='x2', phone='', address='')])
        self.assertEqual(len(core.all_businesses(self.db)), 2)

    def test_failed_batch_rolls_back(self):
        with self.assertRaises(ValueError):
            self.import_rows([self.row(), self.row(source_id='')])
        self.assertEqual(len(core.all_businesses(self.db)), 0)
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM batches').fetchone()[0], 0)

    def test_rights_gate(self):
        m = json.loads(self.rights.read_text())
        p = self.root / 'rights.json'
        m['export_permitted'] = False
        p.write_text(json.dumps(m))
        with self.assertRaises(ValueError):
            core.load_rights(p, 'canonical')
        m.update(export_permitted=True, source='Google Places')
        p.write_text(json.dumps(m))
        with self.assertRaises(ValueError):
            core.load_rights(p, 'canonical')

    def test_non_synthetic_license_notices_required(self):
        m = json.loads(self.rights.read_text())
        m['license'] = 'CDLA-Permissive-2.0'
        p = self.root / 'rights.json'
        p.write_text(json.dumps(m))
        with self.assertRaises(ValueError):
            core.load_rights(p, 'canonical')
        (self.root / 'LICENSE.txt').write_text('Example notice for test only')
        m['notice_files'] = ['LICENSE.txt']
        p.write_text(json.dumps(m))
        self.assertIn('Example notice', core.load_rights(p, 'canonical')['notices'][0]['text'])

    def test_evidence_required(self):
        self.load()
        data = self.review_data()
        data['evidence_url'] = 'javascript:alert(1)'
        with self.assertRaises(ValueError):
            core.review(self.db, 1, data)

    def test_export_roundtrip_and_formula_protection(self):
        self.import_rows([self.row(name='=FORMULA()')])
        rows, _ = core.search(self.db, self.config)
        p = self.root / 'review.csv'
        core.export(self.db, rows, p)
        with p.open(encoding='utf-8-sig', newline='') as f:
            self.assertEqual(next(csv.DictReader(f))['name'], "'=FORMULA()")
        self.assertEqual(json.loads(p.with_suffix('.csv.provenance.json').read_text())['sources'][0]['source'], 'synthetic')
        p = self.root / 'review.json'
        core.export(self.db, rows, p)
        self.assertEqual(json.loads(p.read_text())[0]['name'], '=FORMULA()')

    def test_adapters_current_and_legacy_overture(self):
        row = {'properties': {'id': 'o1', 'names': {'primary': 'Sample'}, 'basic_category': 'plumber',
                'taxonomy': {'primary': 'plumber'}, 'addresses': [{'country': 'US', 'region': 'US-TX', 'locality': 'Austin'}],
                'websites': [], 'phones': ['+15125550111'], 'operating_status': 'open',
                'sources': [{'dataset': 'meta', 'update_time': '2026-01-01T00:00:00Z'}]}}
        normalized = core.normalize(providers.overture(row))
        self.assertEqual(normalized['state'], 'TX')
        self.assertIn('plumber', normalized['categories'])
        row['properties']['categories'] = {'primary': 'electrician'}
        row['properties'].pop('taxonomy')
        self.assertIn('electrician', providers.overture(row)['categories'])

    def test_osm_adapter_never_assumes_us_or_active(self):
        row = {'type': 'node', 'id': 10, 'tags': {'name': 'Sample', 'craft': 'plumber', 'contact:website': 'https://sample.example.com'}}
        b = providers.osm(row)
        self.assertIsNone(b['country'])
        self.assertEqual(b['activity'], 'unknown')
        self.assertEqual(b['websites'], ['https://sample.example.com'])

    def test_overpass_error_rejected(self):
        p = self.root / 'overpass.json'
        p.write_text(json.dumps({'remark': 'runtime error: timeout', 'elements': []}))
        with self.assertRaises(ValueError):
            list(providers.records(p))

    def test_contacted_status_cannot_be_cleared_by_later_review(self):
        self.load()
        data = self.review_data()
        data['disposition'] = 'contacted'
        core.review(self.db, 1, data)
        core.review(self.db, 1, self.review_data())
        rows, _ = core.search(self.db, self.config)
        self.assertNotIn(1, [b['id'] for b in rows])

    def test_current_overture_adapter_enters_pipeline(self):
        m = json.loads(self.rights.read_text())
        m['source'] = 'overture'
        p = self.root / 'rights.json'
        p.write_text(json.dumps(m))
        data = {'type': 'FeatureCollection', 'features': [{'type': 'Feature', 'properties': {
            'id': 'o1', 'names': {'primary': 'Synthetic Overture Plumbing'},
            'basic_category': 'plumber', 'addresses': [{'country': 'US', 'region': 'TX', 'locality': 'Austin'}],
            'operating_status': 'open'}, 'geometry': {'type': 'Point', 'coordinates': [-97.7, 30.3]}}]}
        file = self.root / 'overture.geojson'
        file.write_text(json.dumps(data))
        self.assertEqual(core.import_data(self.db, file, 'overture', p)['inserted'], 1)
        self.assertEqual(len(core.search(self.db, self.config)[0]), 1)

    def test_cli_integration(self):
        dbpath = str(self.root / 'cli.sqlite3')

        def run(*args):
            result = subprocess.run([sys.executable, '-m', 'prospect', '--db', dbpath, *args],
                                    cwd=ROOT, text=True, capture_output=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            return json.loads(result.stdout)

        run('init')
        run('import', str(ROOT / 'fixtures/businesses.csv'), '--rights', str(self.rights))
        run('suppress', str(ROOT / 'fixtures/suppression.csv'))
        result = run('search', '--config', str(ROOT / 'config.example.json'))
        self.assertEqual(len(result['candidates']), 2)
        run('review', '1', '--website-status', 'no_site_found', '--activity', 'active',
            '--ownership', 'independent', '--residential', 'yes',
            '--evidence-url', 'https://example.com/check', '--notes', 'Synthetic test review')
        result = run('export', '--config', str(ROOT / 'config.example.json'), '--qualified-only',
                     '--out', str(self.root / 'qualified.json'))
        self.assertEqual(result['exported'], 1)
        stats = run('stats')
        self.assertEqual((stats['import_count'], stats['search_count'], stats['api_requests']), (1, 2, 0))


if __name__ == '__main__':
    unittest.main()
