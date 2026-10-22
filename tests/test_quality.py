"""Regression tests for the first real-data pilot's failure modes."""
import json
from datetime import date
from pathlib import Path
import tempfile
import unittest
from prospect import core, providers, signals

ROOT = Path(__file__).resolve().parents[1]
TODAY = date(2026, 10, 15)
CONFIG = {'regions': [], 'trades': {
    'painting': ['painting'], 'landscaping': ['landscaping'],
    'fencing_decks': ['fence_and_gate_sales_service', 'deck_and_railing_sales_service'],
    'residential_remodeling': ['altering_and_remodeling_contractor', 'bathroom_remodeling', 'kitchen_remodeling'],
    'name_screened_contractor': ['contractor', 'building_contractor', 'carpenter']}}


def business(**kwargs):
    return {'id': 1, 'name': 'Example House Painting', 'country': 'US', 'state': 'OR',
            'city': 'Example City', 'address': '1 Example Lane', 'postcode': '',
            'websites': [], 'socials': [], 'phones': ['5035550111'], 'brands': [],
            'categories': ['painting'], 'activity': 'unknown', 'observed_at': '',
            'review': {}, 'observations': [], **kwargs}


class QualityTests(unittest.TestCase):
    def evaluate(self, **kwargs):
        return core.evaluate(business(**kwargs), CONFIG, set(), TODAY)

    def test_social_and_contact_hosts_are_not_standalone(self):
        for url in ['https://linktr.ee/example', 'https://wa.me/15035550111',
                    'https://api.whatsapp.com/send?phone=15035550111', 'https://beacons.ai/example',
                    'https://www.instagram.com/example', 'mailto:example@example.com', 'tel:+15035550111']:
            with self.subTest(url=url):
                b = self.evaluate(websites=[url])
                self.assertEqual(b['website_signal'], 'social_only')
                self.assertNotIn('website_present', b['excluded_reasons'])

    def test_directory_is_distinct_from_social(self):
        b = self.evaluate(websites=['https://www.localsearch.com/profile/example'])
        self.assertEqual(b['website_signal'], 'directory_only')
        self.assertLess(b['score_breakdown']['website_gap'], self.evaluate(websites=['https://linktr.ee/example'])['score_breakdown']['website_gap'])
        self.assertEqual(self.evaluate(websites=['https://yelp.com/biz/example'],socials=['https://facebook.com/example'])['website_signal'], 'social_and_directory')
        self.assertEqual(signals.classify_link('https://www.google.com/maps/place/example')['kind'],'directory')
        self.assertEqual(signals.classify_link('https://example.business.site')['kind'],'uncertain_domain')

    def test_lookalike_host_is_not_treated_as_social(self):
        self.assertEqual(signals.classify_link('https://facebook.com.example.org')['kind'], 'standalone_website')

    def test_custom_domain_is_only_a_site_signal(self):
        b = self.evaluate(websites=['https://example.org'])
        self.assertIn('website_present', b['excluded_reasons'])
        self.assertEqual(b['website_assessment']['links'][0]['reason'], 'custom_domain_signal_unverified')

    def test_shorteners_and_malformed_domains_stay_uncertain(self):
        for url in ['https://bit.ly/example', 'https://t.co/example', 'not a URL',
                    'https://127.0.0.1', 'ftp://example.org', 'https://example.org:bad', 'https://[invalid']:
            with self.subTest(url=url):
                b = self.evaluate(websites=[url])
                self.assertEqual(b['website_signal'], 'uncertain_domain')
                self.assertIn('website_needs_verification', b['review_flags'])
                self.assertNotIn('website_present', b['excluded_reasons'])

    def test_missing_fields_never_mean_verified_absence(self):
        b = self.evaluate()
        self.assertEqual(b['website_signal'], 'no_website_listed')
        self.assertFalse(b['website_assessment']['absence_verified'])
        self.assertEqual(b['verification_status'], 'unreviewed')
        self.assertFalse(b['qualified'])

    def test_manual_unresolved_domain_lowers_priority(self):
        unresolved = self.evaluate(review={'website_status': 'uncertain', 'candidate_website': 'https://example.org'})
        self.assertEqual(unresolved['website_signal'], 'uncertain_domain')
        self.assertLess(unresolved['score'], self.evaluate()['score'])

    def test_later_review_cannot_erase_previous_manual_site(self):
        b = self.evaluate(review={'website_status': 'no_site_found'}, reviews=[
            {'website_status': 'site_found', 'verified_website': 'https://example.org'},
            {'website_status': 'no_site_found'}])
        self.assertIn('website_present', b['excluded_reasons'])

    def test_observation_update_does_not_create_activity_points(self):
        old = self.evaluate(observed_at='2017-01-01', activity='unknown')
        new = self.evaluate(observed_at='2026-10-15', activity='unknown')
        self.assertEqual(old['score'], new['score'])
        self.assertEqual(new['score_breakdown']['activity_evidence'], 0)

    def test_computed_overture_sources_are_not_identity_corroboration(self):
        obs = [{**business(), 'source':'synthetic', 'source_id':'x1', 'upstream_sources': [{'dataset': 'Overture', 'record_id': 'a', 'property': '/properties/confidence'},
                                    {'dataset': 'Overture-signals', 'record_id': 'b', 'property': '/properties/operating_status'}]}]
        self.assertEqual(self.evaluate(observations=obs)['score'], self.evaluate()['score'])

    def test_provider_status_is_weak_evidence_and_not_manual_verification(self):
        b = self.evaluate(activity='open')
        self.assertEqual(b['score_breakdown']['activity_evidence'], 2)
        self.assertFalse(b['activity_assessment']['current_operation_verified'])

    def test_stale_manual_activity_cannot_fully_qualify(self):
        r = {'website_status': 'no_site_found', 'activity': 'active', 'residential': 'yes',
             'ownership': 'independent', 'evidence_url': 'https://example.com/check', 'notes': 'Observed',
             'created_at': '2020-01-01T00:00:00Z'}
        b = self.evaluate(review=r)
        self.assertFalse(b['qualified'])
        r['created_at'] = '2026-10-15T00:00:00Z'
        self.assertFalse(self.evaluate(review=r)['qualified'])
        r['evidence_observed_at'] = '2026-10-15'
        self.assertTrue(self.evaluate(review=r)['qualified'])

    def test_four_dimensions_are_bounded_and_sum_to_priority(self):
        b = self.evaluate()
        self.assertEqual(set(b['score_breakdown']), {'website_gap','identity_contact','trade_relevance','activity_evidence'})
        self.assertEqual(b['score'], sum(b['score_breakdown'].values()))
        self.assertLessEqual(b['score'], 100)
        self.assertIn('not a probability', b['ranking_interpretation'])

    def test_known_unrelated_pilot_names_are_excluded_with_reasons(self):
        for name, category in [('Example Parking Marking Inc','painting'),
                               ('Example Lock Safe & Access Control','altering_and_remodeling_contractor')]:
            b = self.evaluate(name=name, categories=[category])
            self.assertIn('unrelated_trade_signal', b['excluded_reasons'])
            self.assertTrue(b['trade_assessment']['negative_signals'])

    def test_conflicting_trade_signal_is_retained_for_review(self):
        b = self.evaluate(name='Example Deck and Access Control',categories=['fence_and_gate_sales_service'])
        self.assertEqual(b['trade_assessment']['status'], 'uncertain')
        self.assertIn('trade_needs_verification', b['review_flags'])
        self.assertNotIn('outside_trade', b['excluded_reasons'])

    def test_broad_contractor_and_name_only_matches_are_uncertain(self):
        for categories in (['contractor'], ['unmapped_specialty'], []):
            b = self.evaluate(name='Example Home Renovations',categories=categories)
            self.assertEqual(b['trade_assessment']['status'],'uncertain')
            self.assertNotIn('outside_trade',b['excluded_reasons'])

    def test_actual_overture_categories_are_explained(self):
        for category in ['painting','landscaping','fence_and_gate_sales_service','deck_and_railing_sales_service',
                         'altering_and_remodeling_contractor','bathroom_remodeling','kitchen_remodeling']:
            b = self.evaluate(name='Example Property Services',categories=[category])
            self.assertEqual(b['trade_assessment']['status'],'target')
            self.assertIn(category, sum(b['trade_assessment']['matched_categories'].values(), []))
            self.assertIn('residential_scope_unverified', b['review_flags'])

    def test_blank_array_values_normalize_to_empty(self):
        self.assertEqual(providers.many(['', ' ', None]), [])


class MetricsTests(unittest.TestCase):
    def test_target_yield_geography_unknown_and_suppression(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)
            db=core.connect(':memory:')
            self.addCleanup(db.close)
            rows=[]
            for i,changes in enumerate([
                {}, {'website':'https://example.org'}, {'categories':['contractor'],'name':'Example Home Remodeling'},
                {'categories':['unmapped_specialty'],'name':'Example Generic Service'},
                {'categories':[],'name':'Example Generic Service'}, {'state':''}, {'country':''},
            ]):
                rows.append({'source_id':str(i),'name':'Example Painting '+str(i),'country':'US','state':'OR',
                             'city':'Example City','categories':['painting'],'phone':f'50355501{i:02d}',**changes})
            file=path/'rows.json'; file.write_text(json.dumps(rows))
            imported=core.import_data(db,file,'canonical',ROOT/'fixtures/rights.synthetic.json')
            self.assertEqual(imported['missing_geography'],2)
            self.assertEqual(imported['missing_categories'],1)
            candidates, metrics=core.search(db,CONFIG)
            self.assertEqual(metrics['target_trade_businesses'],2)
            self.assertEqual(metrics['target_trade_candidates'],1)
            self.assertEqual(metrics['target_trade_yield'],.5)
            self.assertEqual(metrics['target_trade_record_yield'],.5)
            self.assertEqual(metrics['import_rows_missing_geography'],2)
            self.assertEqual(metrics['missing_category_count'],1)
            self.assertEqual(metrics['unknown_category_count'],1)
            self.assertEqual(metrics['uncertain_trade_candidates'],1)
            self.assertEqual(db.execute('SELECT COUNT(*) FROM suppressions').fetchone()[0],0)
            suppression=path/'suppression.json'; suppression.write_text(json.dumps([{'source':'synthetic','source_id':'0'}]))
            core.suppress(db,suppression)
            _,metrics=core.search(db,CONFIG)
            self.assertEqual(metrics['target_trade_candidates'],0)
            self.assertEqual(metrics['exclusions']['suppressed_or_contacted'],1)

    def test_review_rejects_social_as_confirmed_standalone(self):
        with tempfile.TemporaryDirectory() as directory:
            db=core.connect(':memory:')
            self.addCleanup(db.close)
            db.execute('INSERT INTO businesses(created_at) VALUES(?)',(core.now(),)); db.commit()
            with self.assertRaises(ValueError):
                core.review(db,1,{'website_status':'site_found','verified_website':'https://linktr.ee/example',
                                  'evidence_url':'https://example.com/check','notes':'checked'})


if __name__=='__main__':
    unittest.main()
