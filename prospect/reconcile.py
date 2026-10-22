"""Hash-pinned dated outcome previews; application only creates a new DB copy."""
from datetime import datetime, timezone
from contextlib import closing
import hashlib
import json
from pathlib import Path
import re
import sqlite3
from . import core
from .signals import confirmable_site


def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8-sig'))


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def file_hash(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def open_readonly(path):
    return _rows(sqlite3.connect(Path(path).resolve().as_uri() + '?mode=ro', uri=True))


def _rows(db):
    db.row_factory = sqlite3.Row
    return db


def state_hash(db):
    return digest({t: [dict(r) for r in db.execute('SELECT * FROM ' + t + ' ORDER BY ' + order)]
                   for t, order in (('businesses', 'id'), ('observations', 'id'), ('batches', 'id'),
                                    ('reviews', 'id'), ('suppressions', 'kind,value'))})


def stamp(value):
    try:
        parsed = datetime.fromisoformat(str(value).replace('Z', '+00:00'))
        return parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed.astimezone(timezone.utc)
    except (ValueError, TypeError):
        return None


def name_form(value):
    value = str(value).lower().replace('&', ' and ')
    value = re.sub(r'\b(?:llc|inc|incorporated|corp|corporation|ltd)\.?\s*$', '', value).strip(' ,.')
    return core.norm(value)


def case_date(doc):
    return next((doc.get(k) for k in ('checked_utc', 'source_stop_utc', 'checked_date_utc', 'observed_date', 'source_date', 'date')
                 if stamp(doc.get(k))), None)


def outcome(case):
    """Conservative adapter for supplied snapshots and an explicit interchange."""
    if isinstance(case.get('queue_review'), dict):
        return dict(case['queue_review'])
    decision = str(case.get('decision') or case.get('disposition') or '')
    website = case.get('website')
    url = website.get('url') if isinstance(website, dict) else website
    url = url or (case.get('saved_route') or {}).get('website')
    known_sites = ('leave_no_site_priority_working_', 'working_hosted_business_landing_page', 'working_dedicated_website')
    site = decision.startswith(known_sites) or (isinstance(website, dict) and website.get('status') == 'Working first-party service website directly read')
    if decision.startswith('exclude_') or 'prioritization_exclusion' in decision:
        return {'website_status': 'uncertain', 'candidate_website': url if core.public_url(url) else '', 'disposition': 'rejected', 'queue_hold': False}
    if site:
        return {'website_status': 'site_found', 'verified_website': url or '', 'disposition': 'pending', 'queue_hold': False}
    if decision.lower().startswith(('hold', 'park', 'retain_', 'conditional_review', 'advance_recipient_review')):
        # Advancing to recipient review does not clear that review or any contact control.
        return {'website_status': 'uncertain', 'candidate_website': url or '', 'disposition': 'pending', 'queue_hold': True}
    if decision.lower().startswith('exclude from no-site priority') and url:
        return {'website_status': 'site_found', 'verified_website': url, 'disposition': 'pending', 'queue_hold': False}
    raise ValueError('Unsupported decision; supply explicit queue_review rather than guessing: ' + decision)


def urls(case):
    values = [s.get('url') for s in case.get('sources', []) if isinstance(s, dict)]
    values += case.get('urls') or []
    values += [case.get('saved_source_url'), case.get('evidence_url')]
    return [u for u in values if core.public_url(u)]


def preview(db, manifest_path, controls_path):
    manifest_path, controls_path = Path(manifest_path).resolve(), Path(controls_path).resolve()
    manifest, controls = read(manifest_path), read(controls_path)
    businesses = {b['id']: b for b in core.all_businesses(db)}
    bank_path = Path(manifest['bank'])
    if not bank_path.is_absolute():
        bank_path = manifest_path.parent / bank_path
    bank = {b['id']: b for b in read(bank_path)}
    inputs = {str(manifest_path): file_hash(manifest_path), str(controls_path): file_hash(controls_path), str(bank_path.resolve()): file_hash(bank_path)}
    plan = {'schema_version': 1, 'mode': 'dry_run', 'base_state_sha256': state_hash(db), 'input_hashes': inputs,
            'actions': [], 'conflicts': [], 'skipped': [], 'unmatched_controls': [], 'source_cases': 0,
            'warning': 'No recipient permission, current activity verification or provider refresh is implied.'}
    for parent in controls.get('derived_from', []):
        if file_hash(parent['path']) != parent['sha256']:
            raise ValueError('Derived control parent hash mismatch')
        inputs[str(Path(parent['path']).resolve())] = parent['sha256']
    cases = {}
    for source in manifest['sources']:
        path = Path(source['absolute_path'])
        if file_hash(path) != source['sha256']:
            raise ValueError('Snapshot hash mismatch: ' + str(path))
        inputs[str(path.resolve())] = source['sha256']
        doc = read(path)
        for case in doc['cases']:
            plan['source_cases'] += 1
            bid = case.get('id')
            row = {'case': case, 'decision_at': case_date(doc), 'source_path': str(path.resolve()), 'source_sha256': source['sha256']}
            old = cases.get(bid)
            if old and (not stamp(row['decision_at']) or not stamp(old['decision_at'])) and case != old['case']:
                plan['conflicts'].append({'id': bid, 'reason': 'Conflicting snapshots with unknown date ordering'})
                continue
            if old and stamp(row['decision_at']) and stamp(old['decision_at']):
                if stamp(row['decision_at']) < stamp(old['decision_at']):
                    continue
                if stamp(row['decision_at']) == stamp(old['decision_at']) and case != old['case']:
                    plan['conflicts'].append({'id': bid, 'reason': 'Conflicting same-date snapshots'})
                    continue
            cases[bid] = row
    bound_controls = {}
    for control in controls.get('records', []):
        label = control.get('business') or control.get('name') or ''
        explicit = control.get('engineering_id')
        match = re.search(r'\bENG\s*(\d+)\b', label, re.I)
        if explicit is None and match:
            explicit = int(match[1])
        matches = {explicit} if explicit in businesses else set()
        control_names = [label.split(' / ')[0], *control.get('aliases', [])]
        if matches and label and name_form(businesses[explicit]['name']) not in {name_form(n) for n in control_names}:
            plan['conflicts'].append({'id': explicit, 'reason': 'Explicit contact ID/name mismatch'})
            continue
        if control.get('recipient'):
            address = control['recipient'].lower()
            # Exact sanitized route in an ID-bound dated case; no email/domain guesses.
            matches |= {bid for bid, row in cases.items() if bid in businesses and
                        re.search(r'(?<![\w.+-])' + re.escape(address) + r'(?![\w.+-])', json.dumps(row['case']).lower())}
        if len(matches) > 1:
            plan['conflicts'].append({'business': label, 'reason': 'Contact route matches multiple case identities', 'ids': sorted(matches)})
            continue
        if len(matches) == 1:
            bid = next(iter(matches))
            prior = bound_controls.get(bid)
            if prior and prior['status'] != control['status']:
                plan['conflicts'].append({'id': bid, 'reason': 'Conflicting contact controls'})
            else:
                bound_controls[bid] = control
        else:
            names = [label.split(' / ')[0]]
            possible = [b['id'] for b in businesses.values() if name_form(b['name']) in {name_form(n) for n in names}]
            already = [bid for bid in possible if any(r.get('disposition') == 'contacted' for r in businesses[bid].get('reviews', []))]
            entry = {'business': label, 'reason': 'No identity-safe current-bank binding; not an automatic name-only suppression', 'possible_ids': possible}
            if possible and not already:
                plan['conflicts'].append(entry)
            else:
                plan['unmatched_controls'].append({**entry, 'already_contacted_ids': already})
    for bid in sorted(set(cases) | set(bound_controls)):
        row = cases.get(bid)
        b = businesses.get(bid)
        if not b or bid not in bank:
            plan['conflicts'].append({'id': bid, 'reason': 'ID absent from bank/database; no business creation'})
            continue
        aliases = {(o['source'], o['source_id']) for o in b['observations']}
        expected = {(o['source'], o['source_id']) for o in bank[bid]['observations']}
        if not aliases & expected:
            plan['conflicts'].append({'id': bid, 'reason': 'Database identity differs from approved bank'})
            continue
        if row:
            label = row['case'].get('name', '')
            if not any(name_form(n) == name_form(b['name']) for n in label.split(' / ')):
                plan['conflicts'].append({'id': bid, 'reason': 'Case name differs from bound bank; explicit mapping required'})
                continue
            try:
                payload = outcome(row['case'])
            except ValueError as exc:
                plan['conflicts'].append({'id': bid, 'reason': str(exc)})
                continue
        else:
            payload = {'website_status': 'uncertain', 'disposition': 'pending', 'queue_hold': True}
        control = bound_controls.get(bid)
        decision_at = controls.get('created_utc') if control else row['decision_at']
        current = b.get('review', {})
        action_id = digest({'id': bid, 'source': row, 'control': control})
        if any(r.get('snapshot_action_id') == action_id for r in b.get('reviews', [])):
            plan['skipped'].append({'id': bid, 'reason': 'Identical source action already applied'})
            continue
        latest_date = current.get('snapshot_decided_at') or current.get('created_at')
        incoming, existing = stamp(decision_at), stamp(latest_date)
        outcome_time = stamp(row['decision_at']) if row else None
        if row and existing and (not outcome_time or outcome_time <= existing or
                                 (len(str(row['decision_at'])) == 10 and outcome_time.date() == existing.date())):
            plan['conflicts'].append({'id': bid, 'reason': 'Older outcome cannot piggyback on a newer contact control'})
            continue
        if incoming is None or (existing and (incoming <= existing or (len(str(decision_at)) == 10 and incoming.date() == existing.date()))):
            plan['conflicts'].append({'id': bid, 'reason': 'Undated, same-day ambiguous or older decision would supersede current review'})
            continue
        if control:
            payload.update(disposition='contacted' if control['status'].startswith('contacted_') or control['status'].startswith('human_reported_') else 'pending', queue_hold=True)
        elif any(r.get('disposition') == 'contacted' for r in b.get('reviews', [])):
            payload.update(disposition='contacted', queue_hold=True)
        elif current.get('disposition') == 'rejected':
            payload['disposition'] = 'rejected'
        if payload.get('website_status') not in core.WEBSITE_STATUSES or not isinstance(payload.get('queue_hold', False), bool):
            plan['conflicts'].append({'id': bid, 'reason': 'Invalid explicit review status/hold type'})
            continue
        if payload.get('disposition') == 'contacted' and not control and not any(r.get('disposition') == 'contacted' for r in b.get('reviews', [])):
            plan['conflicts'].append({'id': bid, 'reason': 'Contacted outcome requires a current contact control'})
            continue
        payload.pop('created_at', None)
        if payload.get('website_status') == 'site_found' and (not core.public_url(payload.get('verified_website')) or not confirmable_site(payload['verified_website'])):
            plan['conflicts'].append({'id': bid, 'reason': 'Known-site decision lacks valid confirmable business site'})
            continue
        candidate = payload.get('candidate_website')
        if candidate and not core.public_url(candidate):
            plan['conflicts'].append({'id': bid, 'reason': 'Invalid candidate website URL'})
            continue
        evidence = payload.get('evidence_url') or payload.get('verified_website') or next(iter(urls(row['case']) if row else []), None)
        evidence = evidence or next((u for u in b['websites'] + b['socials'] if core.public_url(u)), None)
        if not core.public_url(evidence):
            plan['conflicts'].append({'id': bid, 'reason': 'Missing public evidence URL'})
            continue
        observed = (row['case'].get('evidence_observed_at') or payload.get('evidence_observed_at') or
                    row['case'].get('saved_date') or row['case'].get('saved_observation_date')) if row else None
        payload.update(activity='unknown', ownership='unknown', residential='unknown', service_fit='unknown', delivery='unknown',
                       evidence_url=evidence, evidence_observed_at=observed, snapshot_decided_at=decision_at,
                       snapshot_action_id=action_id, review_origin='coordinator_snapshot',
                       snapshot_source={'path': row['source_path'], 'sha256': row['source_sha256']} if row else None,
                       contact_control=control, notes='Carried dated Coordinator snapshot; original observation date may be unknown. '
                       'No activity/ownership/service/delivery or recipient clearance is re-certified. Decision: ' +
                       str((row['case'].get('decision') or row['case'].get('disposition')) if row else 'current contact control'))
        plan['actions'].append({'business_id': bid, 'data': payload})
    plan['plan_id'] = digest(plan)
    return plan


def apply_copy(source, plan, destination, approve_plan):
    unsigned = {k: v for k, v in plan.items() if k != 'plan_id'}
    if approve_plan != plan.get('plan_id') or digest(unsigned) != plan.get('plan_id'):
        raise ValueError('Explicit matching approval digest required; plan may not be edited')
    if plan['conflicts']:
        raise ValueError('Plan has unresolved conflicts; no copy/application performed')
    for action in plan['actions']:
        if any(action['data'].get(k) != 'unknown' for k in ('activity', 'ownership', 'residential', 'service_fit', 'delivery')):
            raise ValueError('Snapshot application cannot re-certify activity/ownership/scope/delivery')
    destination = Path(destination).resolve()
    if destination.exists() or destination == Path(source).resolve():
        raise ValueError('Destination must be a fresh working-copy path')
    for path, expected in plan['input_hashes'].items():
        if file_hash(path) != expected:
            raise ValueError('Input changed since preview; regenerate plan')
    with closing(open_readonly(source)) as original:
        original.execute('BEGIN')  # Pin the read snapshot used for both approval check and backup.
        if state_hash(original) != plan['base_state_sha256']:
            raise ValueError('Database changed since preview; regenerate plan')
        destination.parent.mkdir(parents=True, exist_ok=True)
        created = False
        try:
            with destination.open('xb'):
                created = True
            with closing(sqlite3.connect(destination)) as target:
                original.backup(target)
            with closing(core.connect(destination)) as db:
                for action in plan['actions']:
                    core.review(db, action['business_id'], action['data'])
            if any(file_hash(path) != expected for path, expected in plan['input_hashes'].items()):
                raise ValueError('Input changed during application; working copy discarded')
        except Exception:
            if created:
                destination.unlink(missing_ok=True)
            raise
    return {'mode': 'applied_to_new_copy', 'destination': str(destination), 'actions_applied': len(plan['actions']),
            'source_unchanged': True, 'plan_id': plan['plan_id']}
