"""Synthetic evidence provenance and independent triage regressions."""
import json
import hashlib
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path

from prospect import core, inbox, investigation, research


class InboxTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / 'review.sqlite3'
        self.db = core.connect(self.path)
        investigation.initialize(self.db)
        research.initialize(self.db)
        inbox.initialize(self.db)
        with self.db:
            self.db.execute("INSERT INTO businesses VALUES(1,'2026-01-01')")
            self.db.execute("INSERT INTO batches VALUES(1,'2026-01-01','fictional','hash','{}')")
            data = core.normalize(dict(source_id='one', name='Fictional Painting', country='US', state='TX', city='Austin', categories=['painter']))
            self.db.execute('INSERT INTO observations VALUES(1,1,1,?,?,?)', ('fictional', 'one', json.dumps(data)))
        self.config = dict(as_of='2026-10-01', trades={'painting': ['painter']})

    def tearDown(self):
        self.db.close()
        self.temp.cleanup()

    def entry(self, observed='2026-01-01', **values):
        return dict(evidence_url='https://example.com/evidence', notes='Synthetic check', evidence_observed_at=observed, **values)

    def tagged(self, question, observed='2026-01-01', **values):
        return core.review(self.db, 1, self.entry(observed, investigation_question=question, investigation_session='synthetic', **values))

    def reasons(self):
        items = inbox.list_items(self.db, self.config)['items']
        return items[0]['reasons'] if items else []

    def test_unreviewed_import_date_never_creates_reminder(self):
        self.assertEqual(self.reasons(), [])
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM runs').fetchone()[0], 0)

    def test_tagged_questions_do_not_inherit_later_date(self):
        self.tagged('identity', identity_status='same_business')
        self.tagged('website', '2026-09-30', website_status='no_site_found')
        self.tagged('activity', activity='active')
        self.tagged('fit', ownership='independent', residential='yes', service_fit='unknown', delivery='unknown')
        self.tagged('website', '2026-09-30', website_status='no_site_found')
        reasons = {r['question']: r for r in self.reasons()}
        # Unrelated saves preserve both the identity answer and its original basis.
        self.assertEqual(reasons['identity']['kind'], 'evidence_old')
        self.assertEqual(reasons['identity']['evidence_observed_at'], '2026-01-01')
        self.assertEqual(reasons['activity']['evidence_observed_at'], '2026-01-01')
        self.assertEqual(reasons['fit']['evidence_observed_at'], '2026-01-01')
        self.assertNotIn('website', reasons)

    def test_cli_change_invalidates_snapshot_even_if_answer_returns(self):
        self.tagged('website', website_status='no_site_found')
        core.review(self.db, 1, self.entry('2026-09-30', website_status='uncertain'))
        core.review(self.db, 1, self.entry('2026-09-30', website_status='no_site_found'))
        reason = self.reasons()[0]
        self.assertEqual(reason['kind'], 'evidence_unknown')
        self.assertIsNone(reason['evidence_observed_at'])

    def test_unrelated_cli_changes_keep_fit_basis(self):
        self.tagged('fit', ownership='independent', residential='yes', service_fit='unknown', delivery='unknown')
        core.review(self.db, 1, self.entry('2026-09-30', website_status='no_site_found'))
        reason = next(r for r in self.reasons() if r['question'] == 'fit')
        self.assertEqual(reason['kind'], 'evidence_old')
        self.assertEqual(reason['evidence_observed_at'], '2026-01-01')

    def test_legacy_generic_basis_unknown_but_explicit_activity_trusted(self):
        core.review(self.db, 1, self.entry(website_status='no_site_found', activity='active'))
        kinds = {r['question']: r['kind'] for r in self.reasons()}
        self.assertEqual(kinds, dict(website='evidence_unknown', activity='evidence_old'))
        # Simulate pre activity-basis legacy review rather than assigning its
        # latest generic evidence to every carried question.
        row = json.loads(self.db.execute('SELECT data FROM reviews').fetchone()[0])
        for field in core.ACTIVITY_EVIDENCE_FIELDS:
            row.pop(field)
        with self.db:
            self.db.execute('UPDATE reviews SET data=?', (json.dumps(row),))
        self.assertTrue(all(r['kind'] == 'evidence_unknown' for r in self.reasons()))

    def test_boundary_zero_day_future_and_invalid_dates(self):
        boundary = (date(2026, 10, 1) - timedelta(days=180)).isoformat()
        self.tagged('activity', boundary, activity='active')
        self.assertEqual(self.reasons(), [])
        self.config['as_of'] = '2026-10-02'
        self.assertEqual(self.reasons()[0]['evidence_age_days'], 181)
        self.config.update(as_of=boundary, freshness_days=0)
        self.assertEqual(self.reasons(), [])
        self.config['as_of'] = (date.fromisoformat(boundary) + timedelta(days=1)).isoformat()
        self.assertEqual(self.reasons()[0]['kind'], 'evidence_old')
        self.config['as_of'] = '2025-01-01'
        self.assertEqual(self.reasons()[0]['kind'], 'evidence_unknown')
        row = json.loads(self.db.execute('SELECT data FROM reviews').fetchone()[0])
        row['activity_evidence_observed_at'] = '2026-02-30'
        with self.db:
            self.db.execute('UPDATE reviews SET data=?', (json.dumps(row),))
        self.assertEqual(self.reasons()[0]['kind'], 'evidence_unknown')

    def test_seen_restore_restart_new_basis_and_read_only(self):
        self.tagged('website', website_status='no_site_found')
        rid = self.reasons()[0]['id']
        original = self.db.total_changes
        self.reasons()
        self.assertEqual(self.db.total_changes, original)
        result = inbox.set_seen(self.db, self.config, [rid, 'change:missing'], True)
        self.assertEqual(result['updated'], [rid])
        self.assertEqual(result['ignored'], ['change:missing'])
        self.db.close()
        self.db = core.connect(self.path)
        self.assertTrue(self.reasons()[0]['seen'])
        inbox.set_seen(self.db, self.config, [rid], False)
        self.assertFalse(self.reasons()[0]['seen'])
        inbox.set_seen(self.db, self.config, [rid], True)
        self.tagged('website', '2026-02-01', website_status='no_site_found')
        later = self.reasons()[0]
        self.assertNotEqual(later['id'], rid)
        self.assertFalse(later['seen'])
        inbox.set_seen(self.db, self.config, [rid], True)
        self.assertFalse(self.reasons()[0]['seen'])
        self.tagged('website', '2026-09-30', website_status='no_site_found')
        self.assertEqual(self.reasons(), [])
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM reviews').fetchone()[0], 3)

    def test_exclusions_drafts_and_changes_remain_inspectable(self):
        self.db.execute('CREATE TABLE IF NOT EXISTS change_events(id INTEGER PRIMARY KEY,business_id INTEGER,batch_id INTEGER,data TEXT)')
        with self.db:
            self.db.execute('INSERT INTO change_events VALUES(1,1,1,?)', (json.dumps(dict(kind='listing_change', questions=['website'], fields=[dict(field='websites', before=[], after=['https://example.com'], question='website')])),))
            investigation.write_state(self.db, 1, 1, dict(step=0, mode='guided', questions={}), {})
        core.review(self.db, 1, self.entry(disposition='contacted'))
        item = inbox.list_items(self.db, self.config)['items'][0]
        self.assertFalse(item['can_review'])
        self.assertTrue(item['has_draft'])
        self.assertTrue(item['has_review'])
        self.assertIn('suppressed_or_contacted', item['excluded_reasons'])
        self.assertEqual(item['reasons'][0]['id'], 'change:1')

    def test_research_context_is_local_and_independent(self):
        business = core.all_businesses(self.db)[0]
        with self.db:
            self.db.execute('INSERT INTO research_records VALUES(?,?,?,?,?,?)',
                            ('card', 1, 'request', '{}', research.fingerprint(business), json.dumps(dict(observed_at='2026-01-01', url='https://example.com'))))
        self.assertEqual(self.reasons(), [])
        row = json.loads(self.db.execute('SELECT data FROM observations').fetchone()[0])
        row['name'] = 'Fictional Updated Painting'
        with self.db:
            self.db.execute('UPDATE observations SET data=?', (json.dumps(row),))
        reason = self.reasons()[0]
        self.assertEqual(reason['kind'], 'research_context_changed')
        self.assertEqual(reason['research_id'], 'card')

    def test_new_and_legacy_research_cards_ignore_formatting_import(self):
        row = json.loads(self.db.execute('SELECT data FROM observations').fetchone()[0])
        row.update(phones=['2025550101'], websites=['https://EXAMPLE.com', 'https://example.com/Services'])
        with self.db:
            self.db.execute('UPDATE observations SET data=?', (json.dumps(row),))
        business = core.all_businesses(self.db)[0]
        legacy = hashlib.sha256(investigation.encoded({key: business.get(key) for key in
            ('name', 'address', 'city', 'state', 'phones', 'websites')}).encode()).hexdigest()
        fingerprints = [legacy, research.fingerprint(business)]
        with self.db:
            for number, fingerprint in enumerate(fingerprints):
                self.db.execute('INSERT INTO research_records VALUES(?,?,?,?,?,?)',
                    (str(number), 1, 'request-' + str(number), '{}', fingerprint, json.dumps(dict(url='https://example.com'))))
        formatted = {**row, 'name': 'FICTIONAL  PAINTING!', 'city': 'Austin.',
                     'phones': ['+1 (202) 555-0101'],
                     'websites': ['https://example.com/Services', 'https://example.com/']}
        metrics = core.import_rows(self.db, [core.normalize(formatted)], {'source': 'fictional'}, 'formatting-only')
        self.assertEqual(metrics['comparison']['changed_businesses'], 0)
        current = core.all_businesses(self.db)[0]
        records = research.list_records(self.db, 1, current)['records']
        self.assertTrue(all(not record['stale'] for record in records))
        self.assertFalse(any(reason['kind'] == 'research_context_changed' for reason in self.reasons()))
        # Compatibility is read-only and also applies to retry and dismiss paths.
        self.assertFalse(research.existing(self.db, 1, '{}', 'request-0', current)['record']['stale'])
        self.assertFalse(research.dismiss(self.db, 1, '0', {'dismissed': False}, current)['record']['stale'])
        self.assertEqual([r[0] for r in self.db.execute('SELECT fingerprint FROM research_records ORDER BY id')], fingerprints)

    def test_research_material_changes_and_unmatched_legacy_hash_are_stale(self):
        business = core.all_businesses(self.db)[0]
        legacy = hashlib.sha256(investigation.encoded({key: business.get(key) for key in
            ('name', 'address', 'city', 'state', 'phones', 'websites')}).encode()).hexdigest()
        stored = [dict(fingerprint=legacy), dict(fingerprint=research.fingerprint(business))]
        self.assertTrue(research.is_stale({'fingerprint': '0' * 64}, business))
        row = json.loads(self.db.execute('SELECT data FROM observations').fetchone()[0])
        for field, value in (('name', 'Different Painting'), ('phones', ['2025550199']),
                             ('websites', ['https://example.com/new-business-site'])):
            with self.subTest(field=field):
                changed = {**row, field: value}
                core.import_rows(self.db, [core.normalize(changed)], {'source': 'fictional'}, 'material-' + field)
                current = core.all_businesses(self.db)[0]
                self.assertTrue(all(research.is_stale(card, current) for card in stored))

    def test_configuration_and_triage_validation(self):
        for value in (True, -1, '180'):
            with self.assertRaises(ValueError):
                inbox.list_items(self.db, {**self.config, 'advisory_review_days': value})
        for value in (1, 'true', None):
            with self.assertRaises(ValueError):
                inbox.set_seen(self.db, self.config, ['anything'], value)
        for value in ('anything', [], [1]):
            with self.assertRaises(ValueError):
                inbox.set_seen(self.db, self.config, value, True)

    def test_real_investigation_undo_restores_old_question_basis(self):
        def business():
            return core.evaluate(core.all_businesses(self.db)[0], self.config, set(), date(2026, 10, 1))

        def save(observed, request_id):
            revision, _, _ = investigation.state(self.db, 1)
            return investigation.commit(self.db, 1, dict(
                review_revision=investigation.revision(self.db, 1), draft_revision=revision,
                request_id=request_id, questions={'website': self.entry(observed, website_status='no_site_found')}), business())

        save('2026-01-01', 'old')
        old_id = self.reasons()[0]['id']
        saved = save('2026-09-30', 'new')
        self.assertEqual(self.reasons(), [])
        investigation.undo(self.db, 1, dict(session_id=saved['session_id'],
                           review_revision=saved['review_revision']), business())
        self.assertEqual(self.reasons()[0]['id'], old_id)
        self.assertEqual(self.reasons()[0]['evidence_observed_at'], '2026-01-01')

    def test_desk_and_inbox_share_effective_basis_after_cross_path_undo(self):
        def business():
            return core.evaluate(core.all_businesses(self.db)[0], self.config, set(), date(2026, 10, 1))

        def save(questions, request_id):
            return investigation.commit(self.db, 1, dict(
                review_revision=investigation.revision(self.db, 1),
                draft_revision=investigation.state(self.db, 1)[0],
                request_id=request_id, questions=questions), business())

        original = self.entry(website_status='no_site_found')
        save({'website': original}, 'original')
        # Returning to the same answer does not revive its superseded basis.
        for status in ('uncertain', 'no_site_found'):
            core.review(self.db, 1, self.entry(website_status=status))
        save({'fit': self.entry(ownership='independent', residential='yes',
                               service_fit='unknown', delivery='unknown')}, 'unrelated')
        current_draft = dict(step=2, mode='workspace', questions={'activity': {'notes': 'Keep unfinished'}})
        state = investigation.read(self.db, 1, business())
        investigation.save_draft(self.db, 1, dict(review_revision=state['review_revision'],
            draft_revision=state['draft_revision'], draft=current_draft))
        investigation.undo(self.db, 1, dict(session_id=state['latest_session']['session_id'],
            review_revision=state['review_revision']), business())
        state = investigation.read(self.db, 1, business())
        item = inbox.list_items(self.db, self.config)['items'][0]
        self.assertEqual(state['saved_answers'], item['saved_answers'])
        self.assertFalse(state['saved_answers']['website']['basis_known'])
        self.assertEqual(state['questions']['website']['notes'], '')
        self.assertEqual(state['question_history']['website'], original)
        self.assertEqual(state['draft']['questions']['activity'], current_draft['questions']['activity'])
        self.assertIn('fit', state['draft']['questions'])

    def test_desk_legacy_and_carried_answers_use_only_proven_question_basis(self):
        self.tagged('website', website_status='no_site_found')
        core.review(self.db, 1, self.entry(identity_status='same_business', ownership='independent'))
        business = core.evaluate(core.all_businesses(self.db)[0], self.config, set(), date(2026, 10, 1))
        desk = investigation.read(self.db, 1, business)
        item = inbox.list_items(self.db, self.config)['items'][0]
        self.assertEqual(desk['saved_answers'], item['saved_answers'])
        self.assertTrue(desk['saved_answers']['website']['basis_known'])
        self.assertEqual(desk['questions']['website'], self.entry(website_status='no_site_found'))
        for question in ('identity', 'fit'):
            self.assertFalse(desk['saved_answers'][question]['basis_known'])
            self.assertEqual(desk['questions'][question]['evidence_url'], '')

    def test_advisory_setting_does_not_change_qualification(self):
        self.tagged('website', website_status='no_site_found')
        self.config['advisory_review_days'] = 0
        self.assertTrue(self.reasons()[0]['advisory'])
        before = core.evaluate(core.all_businesses(self.db)[0], self.config, set(), date(2026, 10, 1))
        self.config['advisory_review_days'] = 365
        self.assertEqual(self.reasons(), [])
        after = core.evaluate(core.all_businesses(self.db)[0], self.config, set(), date(2026, 10, 1))
        self.assertEqual(before['qualified'], after['qualified'])
        self.assertEqual(before['score'], after['score'])


if __name__ == '__main__':
    unittest.main()
