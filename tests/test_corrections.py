"""Bounded E1/E2 regressions; all database/contact records here are synthetic."""
import json
from datetime import date
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from prospect import core, signals

ROOT = Path(__file__).resolve().parents[1]
HOSTED = 'https://synthetic-example.localsearch.com/'
CONFIG = {'regions': [], 'trades': {'painting': ['painting']}, 'as_of': '2026-10-15'}


class CorrectionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.db = core.connect(self.root / 'synthetic.sqlite3')
        self.addCleanup(self.db.close)

    def import_rows(self, *rows):
        defaults = {'name': 'Synthetic Painting A', 'source_id': 'a', 'country': 'US',
                    'state': 'OR', 'city': 'Example City', 'address': '', 'categories': ['painting']}
        path = self.root / 'input.json'
        path.write_text(json.dumps([{**defaults, **row} for row in rows]))
        return core.import_data(self.db, path, 'canonical', ROOT / 'fixtures/rights.synthetic.json')

    def review(self, bid=1, **changes):
        data = {'website_status': 'uncertain', 'evidence_url': 'https://example.org/synthetic-evidence',
                'notes': 'Synthetic check: business identity and hosted site contents match.',
                'disposition': 'pending', **changes}
        core.review(self.db, bid, data)

    def results(self):
        return core.search(self.db, CONFIG, include_excluded=True)[0]

    def suppress(self, *rows):
        path = self.root / 'suppression.json'
        path.write_text(json.dumps(rows))
        return core.suppress(self.db, path)

    def test_audited_hosted_url_is_uncertain_until_confirmed(self):
        # Regression URL from saved audit only: this test does not assert it is live.
        self.import_rows({'websites': [HOSTED]})
        result = self.results()[0]
        self.assertEqual(signals.classify_link(HOSTED)['reason'], 'hosted_business_page_requires_review')
        self.assertEqual(result['website_signal'], 'uncertain_domain')
        self.assertIn('website_needs_verification', result['review_flags'])
        self.assertEqual(result['score_breakdown']['website_gap'], 4)
        self.assertNotIn('website_present', result['excluded_reasons'])
        self.assertFalse(result['website_assessment']['absence_verified'])
        self.assertFalse(result['qualified'])

    def test_evidence_backed_hosted_confirmation_excludes_and_is_sticky(self):
        self.import_rows({'websites': [HOSTED]})
        self.review(website_status='site_found', verified_website=HOSTED)
        self.review(website_status='no_site_found')
        result = self.results()[0]
        self.assertEqual(result['website_signal'], 'provider_hosted_website')
        self.assertIn('website_present', result['excluded_reasons'])
        self.assertEqual(result['score_breakdown']['website_gap'], 0)
        self.assertEqual(result['website_assessment']['links'][-1]['origin'], 'manual_site_found')
        self.assertEqual(result['reviews'][0]['verified_website'], HOSTED)
        self.assertFalse(result['qualified'])

    def test_hosted_confirmation_requires_evidence_and_notes(self):
        self.import_rows({})
        for changes in ({'evidence_url': ''}, {'notes': '  '}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                self.review(website_status='site_found', verified_website=HOSTED, **changes)
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM reviews').fetchone()[0], 0)

    def test_directory_social_contact_and_unknown_links_still_cannot_be_confirmed(self):
        self.import_rows({})
        for url in ('https://www.localsearch.com/profile/example', 'https://yelp.com/biz/example',
                    'https://facebook.com/example', 'https://linktr.ee/example', 'https://wa.me/15035550111',
                    'https://bit.ly/example', 'https://business.site', 'https://wordpress.com'):
            with self.subTest(url=url), self.assertRaises(ValueError):
                self.review(website_status='site_found', verified_website=url)
        self.assertEqual(signals.classify_link('https://www.localsearch.com/profile/example')['kind'], 'directory')

    def test_unresolved_hosted_candidate_remains_uncertain(self):
        self.import_rows({})
        self.review(candidate_website=HOSTED)
        result = self.results()[0]
        self.assertEqual(result['website_signal'], 'uncertain_domain')
        self.assertNotIn('website_present', result['excluded_reasons'])

    def test_provider_host_and_lookalike_boundaries(self):
        for url in ('https://synthetic.business.site', 'https://synthetic.weebly.com',
                    'https://synthetic.wordpress.com', 'https://synthetic.wixsite.com/site'):
            with self.subTest(url=url):
                self.assertEqual(signals.classify_link(url)['kind'], 'uncertain_domain')
                self.assertTrue(signals.confirmable_site(url))
        self.assertEqual(signals.classify_link('https://localsearch.com.example.org')['kind'], 'standalone_website')
        self.assertFalse(signals.confirmable_site('https://support.wordpress.com'))

    def test_distinct_shared_profiles_are_not_cross_suppressed(self):
        for platform in ('facebook.com', 'linktr.ee', 'wa.me', 'yelp.com/biz', 'localsearch.com/profile'):
            with self.subTest(platform=platform):
                first = core.suppression_link_key('https://' + platform + '/synthetic-a')
                second = core.suppression_link_key('https://' + platform + '/synthetic-b')
                self.assertEqual(first[0], 'profile_url')
                self.assertNotEqual(first, second)
        self.import_rows({'websites': ['https://facebook.com/synthetic-a']},
                         {'source_id': 'b', 'name': 'Synthetic Painting B', 'websites': ['https://facebook.com/synthetic-b']})
        self.review(disposition='contacted')
        a, b = sorted(self.results(), key=lambda x: x['id'])
        self.assertIn('suppressed_or_contacted', a['excluded_reasons'])
        self.assertNotIn('suppressed_or_contacted', b['excluded_reasons'])
        stored = {(r['kind'], r['value']) for r in self.db.execute('SELECT * FROM suppressions')}
        self.assertNotIn(('domain', 'facebook.com'), stored)
        self.assertIn(('profile_url', 'facebook.com/synthetic-a'), stored)

    def test_profile_import_before_discovery_suppresses_only_exact_business(self):
        self.suppress({'website': 'http://www.facebook.com/synthetic-a/?utm_source=test'})
        self.import_rows({'socials': ['https://facebook.com/synthetic-a']},
                         {'source_id': 'b', 'name': 'Synthetic Painting B', 'socials': ['https://facebook.com/synthetic-b']})
        a, b = sorted(self.results(), key=lambda x: x['id'])
        self.assertIn('suppressed_or_contacted', a['excluded_reasons'])
        self.assertNotIn('suppressed_or_contacted', b['excluded_reasons'])

    def test_profile_query_identity_is_preserved(self):
        first = core.suppression_link_key('https://facebook.com/profile.php?id=111&utm_source=test')
        second = core.suppression_link_key('https://facebook.com/profile.php?id=222')
        self.assertEqual(first, ('profile_url', 'facebook.com/profile.php?id=111'))
        self.assertNotEqual(first, second)
        self.assertNotEqual(core.suppression_link_key('https://linktr.ee/Case'), core.suppression_link_key('https://linktr.ee/case'))

    def test_bare_shared_domain_import_fails_atomically(self):
        for host in ('facebook.com', 'www.yelp.com', 'wa.me', 'linktr.ee', 'localsearch.com', 'weebly.com'):
            with self.subTest(host=host), self.assertRaisesRegex(ValueError, 'bare shared hosts'):
                self.suppress({'phone': '5035550111'}, {'domain': host})
            self.assertEqual(self.db.execute('SELECT COUNT(*) FROM suppressions').fetchone()[0], 0)

    def test_legacy_shared_domain_does_not_cross_suppress_but_contact_identity_survives(self):
        self.import_rows({'websites': ['https://facebook.com/synthetic-a']},
                         {'source_id': 'b', 'name': 'Synthetic Painting B', 'websites': ['https://facebook.com/synthetic-b']})
        # Reproduce an old contacted record with the old broad key and durable keys.
        self.db.executemany('INSERT INTO suppressions VALUES(?,?,?)',
                            [('domain', 'facebook.com', 'old synthetic'), ('business_id', '1', 'old synthetic'),
                             ('source_id', 'synthetic:a', 'old synthetic')])
        self.db.commit()
        self.review(website_status='no_site_found')
        self.import_rows({'websites': []})
        rows, metrics = core.search(self.db, CONFIG, include_excluded=True)
        a, b = sorted(rows, key=lambda x: x['id'])
        self.assertIn('suppressed_or_contacted', a['excluded_reasons'])
        self.assertNotIn('suppressed_or_contacted', b['excluded_reasons'])
        self.assertEqual(metrics['legacy_shared_domain_suppressions_ignored'], 1)
        self.assertEqual(self.db.execute("SELECT COUNT(*) FROM suppressions WHERE kind='domain'").fetchone()[0], 1)

    def test_contact_suppression_survives_changed_profile_and_later_review(self):
        self.import_rows({'phone': '5035550111', 'address': '1 Synthetic Lane', 'websites': ['https://facebook.com/synthetic-a']})
        self.review(disposition='contacted')
        self.review(website_status='no_site_found')
        self.import_rows({'websites': ['https://facebook.com/new-profile'], 'phone': '', 'address': ''})
        self.assertIn('suppressed_or_contacted', self.results()[0]['excluded_reasons'])
        # Each durable alias works independently of business_id and profile URL.
        b = core.all_businesses(self.db)[0]
        keys = core.suppression_keys(b)
        for key in (('source_id', 'synthetic:a'), ('phone', '5035550111'),
                    ('identity', 'syntheticpaintinga|1syntheticlane|examplecity|or')):
            with self.subTest(key=key):
                self.assertIn(key, keys)
                self.assertIn('suppressed_or_contacted', core.evaluate(b, CONFIG, {key}, date(2026, 10, 15))['excluded_reasons'])

    def test_dedicated_domain_suppression_still_spans_pages(self):
        self.suppress({'domain': 'www.synthetic.example.org'})
        self.import_rows({'websites': ['https://synthetic.example.org/services/painting']})
        self.assertIn('suppressed_or_contacted', self.results()[0]['excluded_reasons'])
        self.assertEqual(core.suppression_link_key('https://synthetic.example.org/other'), ('domain', 'synthetic.example.org'))

    def test_hosted_tenants_do_not_share_suppression(self):
        self.suppress({'website': 'https://synthetic-a.localsearch.com/'})
        self.import_rows({'websites': ['https://synthetic-a.localsearch.com/']},
                         {'source_id': 'b', 'name': 'Synthetic Painting B', 'websites': ['https://synthetic-b.localsearch.com/']})
        a, b = sorted(self.results(), key=lambda x: x['id'])
        self.assertIn('suppressed_or_contacted', a['excluded_reasons'])
        self.assertNotIn('suppressed_or_contacted', b['excluded_reasons'])

    def test_cli_can_record_hosted_confirmation(self):
        self.import_rows({})
        result = subprocess.run([sys.executable, '-m', 'prospect', '--db', str(self.root / 'synthetic.sqlite3'),
                                 'review', '1', '--website-status', 'site_found', '--verified-website', HOSTED,
                                 '--evidence-url', 'https://example.org/synthetic-evidence', '--notes', 'Synthetic matching hosted site'],
                                cwd=ROOT, text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)['reviewed'], 1)
        self.assertEqual(json.loads(result.stdout)['review']['website_status'], 'site_found')
        self.assertEqual(self.results()[0]['website_signal'], 'provider_hosted_website')

    def test_legacy_dedicated_tenant_domain_is_still_suppressed(self):
        self.db.execute('INSERT INTO suppressions VALUES(?,?,?)', ('domain', 'synthetic.localsearch.com', 'old synthetic'))
        self.db.commit()
        self.import_rows({'websites': ['https://synthetic.localsearch.com/services']})
        rows, metrics = core.search(self.db, CONFIG, include_excluded=True)
        self.assertIn('suppressed_or_contacted', rows[0]['excluded_reasons'])
        self.assertEqual(metrics['legacy_shared_domain_suppressions_ignored'], 0)

    def test_valid_contact_key_survives_unsafe_root_in_same_import_row(self):
        result = self.suppress({'phone': '5035550111', 'website': 'https://facebook.com/'})
        self.assertEqual(result, {'suppression_keys_loaded': 1, 'unsafe_link_values_ignored': 1})
        self.import_rows({'phone': '5035550111'})
        self.assertIn('suppressed_or_contacted', self.results()[0]['excluded_reasons'])

    def test_prior_verified_domain_remains_a_contact_alias(self):
        self.import_rows({})
        self.review(website_status='site_found', verified_website='https://synthetic.example.org')
        self.review(disposition='contacted')
        keys = {(r['kind'], r['value']) for r in self.db.execute('SELECT * FROM suppressions')}
        self.assertIn(('domain', 'synthetic.example.org'), keys)
        self.import_rows({'source_id': 'b', 'name': 'Synthetic Painting B', 'websites': ['https://synthetic.example.org/services']})
        self.assertTrue(all('suppressed_or_contacted' in b['excluded_reasons'] for b in self.results()))


if __name__ == '__main__':
    unittest.main()
