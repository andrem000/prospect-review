"""Synthetic, read-only evidence ordering and retained screening explanations."""
import copy
from datetime import date
import unittest
from unittest.mock import patch

from prospect import core, evidence, inbox, providers, review_state, changes


class EvidenceTests(unittest.TestCase):
    def setUp(self):
        self.db = core.connect(':memory:')
        self.addCleanup(self.db.close)
        inbox.initialize(self.db)
        self.config = dict(as_of='2026-10-22', trades={'painting': ['painting']})

    def row(self, **fields):
        return core.normalize(providers.canonical({'source_id': 'one', 'name': 'Fictional Painting',
            'country': 'US', 'state': 'OR', 'city': 'Example', 'address': '1 Example Lane',
            'phones': ['5035550100'], 'categories': ['painting'], 'observed_at': '2026-10-01', **fields}))

    def load(self, *rows, source='fictional'):
        return core.import_rows(self.db, rows, dict(source=source), 'synthetic')

    def business(self):
        suppression = {(r['kind'], r['value']) for r in self.db.execute('SELECT * FROM suppressions')}
        return core.evaluate(core.all_businesses(self.db)[0], self.config, suppression, date(2026, 10, 22))

    def read(self):
        return evidence.read(self.db, self.business(), self.config)

    def test_import_order_reversion_and_older_dates_preserve_exact_observations(self):
        a = self.row(websites=[])
        self.load(a)
        original_revision = changes.source_revision(self.db, 1)
        self.load(self.row(websites=['https://fictional.example'], observed_at='2026-10-10'))
        self.load(self.row(websites=[], observed_at='2025-01-01'))
        business = self.business()
        before = copy.deepcopy(business)
        group = evidence.read(self.db, business, self.config)['source_groups'][0]
        self.assertEqual(group['latest'], business['observations'][2])
        self.assertEqual(group['latest']['observed_at'], '2025-01-01')
        self.assertEqual(group['history'], list(reversed(business['observations'][:2])))
        self.assertEqual(group['observation_count'], 3)
        self.assertEqual(business, before)
        self.assertNotEqual(changes.source_revision(self.db, 1), original_revision)
        self.assertIn('website_present', business['excluded_reasons'])
        signal = self.read()['retained_signals'][0]
        self.assertEqual((signal['kind'], signal['value'], signal['batch_id']), ('website', 'https://fictional.example', 2))

    def test_duplicate_file_order_and_two_sources_stay_separate(self):
        self.load(self.row(websites=['https://first.example']), self.row(websites=['https://second.example']))
        self.load(self.row(source_id='other', websites=['https://third.example']), source='other-source')
        groups = self.read()['source_groups']
        self.assertEqual(len(groups), 2)
        self.assertEqual(groups[0]['latest']['websites'], ['https://second.example'])
        self.assertEqual(groups[0]['history'][0]['websites'], ['https://first.example'])
        self.assertEqual(groups[1]['latest']['websites'], ['https://third.example'])
        self.assertEqual(groups[1]['history'], [])
        self.load(self.row(source_id='other', websites=[]), source='other-source')
        # A missing row never removes the other source's group.
        self.assertEqual([g['observation_count'] for g in self.read()['source_groups']], [2, 2])

    def test_historical_phone_is_not_misrepresented_as_a_contact_exclusion(self):
        self.load(self.row())
        self.load(self.row(phones=['5035550199']))
        signals = self.read()['retained_signals']
        contact = next(s for s in signals if s['kind'] == 'historical_contact')
        self.assertIsNone(contact['suppression_kind'])
        self.assertIn('does not match', contact['explanation'])
        self.assertNotIn('suppressed_or_contacted', self.business()['excluded_reasons'])
        self.db.execute('INSERT INTO suppressions VALUES(?,?,?)', ('phone', '5035550100', 'synthetic'))
        self.db.commit()
        signals = self.read()['retained_signals']
        contact = next(s for s in signals if s['kind'] == 'historical_contact')
        self.assertEqual(contact['suppression_kind'], 'phone')
        self.assertIn('suppressed_or_contacted', self.business()['excluded_reasons'])
        self.assertEqual([(s['suppression_kind'], s['value']) for s in signals if s['kind'] == 'contact_suppression'],
                         [('phone', '5035550100')])

    def test_unrelated_suppression_does_not_attribute_history_to_contact_exclusion(self):
        self.load(self.row())
        self.load(self.row(phones=['5035550199']))
        self.db.execute('INSERT INTO suppressions VALUES(?,?,?)', ('business_id', '1', 'synthetic'))
        self.db.commit()
        contact = next(s for s in self.read()['retained_signals'] if s['kind'] == 'historical_contact')
        self.assertIsNone(contact['suppression_kind'])
        self.assertIn('does not match', contact['explanation'])
        self.assertIn('suppressed_or_contacted', self.business()['excluded_reasons'])

    def test_prior_manual_hosted_site_confirmation_survives_later_correction(self):
        self.load(self.row())
        core.review(self.db, 1, dict(website_status='site_found', verified_website='https://fictional.wixsite.com/painting',
            evidence_url='https://example.com/check', notes='Synthetic confirmation', evidence_observed_at='2026-10-01'))
        core.review(self.db, 1, dict(website_status='uncertain', verified_website='', candidate_website='https://possible.example',
            evidence_url='https://example.com/correction', notes='Synthetic correction'))
        business = self.business()
        self.assertIn('website_present', business['excluded_reasons'])
        manual = next(s for s in self.read()['retained_signals'] if s['kind'] == 'manual_website_confirmation')
        self.assertEqual(manual['value'], 'https://fictional.wixsite.com/painting')
        # Generic legacy review evidence may describe another carried field;
        # the retained confirmation does not invent a trustworthy dated basis.
        self.assertIsNone(manual['observed_at'])
        self.assertIn('not been reverified', manual['explanation'])
        self.assertEqual(review_state.saved_answers(business)['website']['answers']['website_status'], 'uncertain')

    def test_exact_inbox_reasons_seen_state_and_no_database_mutation(self):
        self.load(self.row())
        self.load(self.row(websites=['https://fictional.example']))
        core.review(self.db, 1, dict(website_status='uncertain', evidence_url='https://example.com/check', notes='Synthetic'))
        expected = inbox.list_items(self.db, self.config)['items'][0]
        inbox.set_seen(self.db, self.config, [expected['reasons'][0]['id']], True)
        expected = inbox.list_items(self.db, self.config)['items'][0]
        before = list(self.db.iterdump())
        business = self.business()
        with patch('prospect.inbox.core.all_businesses', side_effect=AssertionError('Do not reload workspace businesses')), \
             patch('prospect.inbox.core.evaluate', side_effect=AssertionError('Do not reevaluate the supplied business')):
            result = evidence.read(self.db, business, self.config)
        self.assertEqual(result['change_reasons'], expected['reasons'])
        self.assertEqual(review_state.saved_answers(business), expected['saved_answers'])
        self.assertTrue(result['change_reasons'][0]['seen'])
        self.assertEqual(list(self.db.iterdump()), before)

    def test_empty_initial_attention_and_bounded_default_group(self):
        row = self.row()
        for _ in range(20):
            self.load(row)
        result = self.read()
        self.assertEqual(result['change_reasons'], [])
        self.assertEqual(result['retained_signals'], [])
        self.assertEqual(len(result['source_groups']), 1)
        self.assertEqual(result['source_groups'][0]['observation_count'], 20)
        self.assertEqual(len(result['source_groups'][0]['history']), 19)

    def test_repeated_history_across_sources_deduplicates_signal_explanations(self):
        for source in ('first', 'second'):
            for _ in range(10):
                self.load(self.row(websites=['https://retained.example']), source=source)
            self.load(self.row(websites=[]), source=source)
        result = self.read()
        self.assertEqual(len(result['source_groups']), 2)
        self.assertEqual([g['observation_count'] for g in result['source_groups']], [11, 11])
        self.assertEqual(len(result['retained_signals']), 1)
        self.assertEqual(result['retained_signals'][0]['value'], 'https://retained.example')
