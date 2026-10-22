"""SQLite storage, provenance, conservative filtering and explainable ranking."""
import csv
import hashlib
import io
import json
import re
import sqlite3
import unicodedata
from contextlib import ExitStack
from datetime import datetime, timezone, date
from pathlib import Path
from urllib.parse import urlsplit, parse_qsl, urlencode
from .providers import ADAPTERS, records, many
from .signals import (classify_link, website_assessment, trade_assessment,
                      identity_assessment, activity_assessment, confirmable_site,
                      host_matches, SHARED_PLATFORM_HOSTS, scope_assessment)

STATES = set('AL AK AZ AR CA CO CT DE FL GA HI ID IL IN IA KS KY LA ME MD MA MI MN MS MO MT NE NV NH NJ NM NY NC ND OH OK OR PA RI SC SD TN TX UT VT VA WA WV WI WY DC'.split())
WEBSITE_STATUSES = ('unreviewed', 'no_site_found', 'site_found', 'uncertain')


def now():
    return datetime.now(timezone.utc).isoformat()


def norm(value):
    return re.sub(r'[^a-z0-9]', '', unicodedata.normalize('NFKD', str(value or '')).encode('ascii', 'ignore').decode().lower())


def phone(value):
    digits = re.sub(r'\D', '', str(value or ''))
    if len(digits) == 11 and digits.startswith('1'):
        digits = digits[1:]
    return digits if len(digits) == 10 else ''


def domain(value):
    value = str(value or '').strip()
    if not value:
        return ''
    try:
        return (urlsplit(value if '://' in value else 'https://' + value).hostname or '').lower().removeprefix('www.')
    except ValueError:
        return ''


def public_url(value):
    try:
        u = urlsplit(str(value or ''))
        return u.scheme in ('http', 'https') and bool(u.hostname) and not u.username and not u.password
    except ValueError:
        return False


def standalone(value):
    return classify_link(value)['kind'] == 'standalone_website'


def connect(path):
    db = sqlite3.connect(path)
    db.row_factory = sqlite3.Row
    db.execute('PRAGMA foreign_keys=ON')
    db.executescript('''
    CREATE TABLE IF NOT EXISTS businesses(id INTEGER PRIMARY KEY, created_at TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS batches(id INTEGER PRIMARY KEY, imported_at TEXT, source TEXT,
      file_sha256 TEXT, manifest TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS observations(id INTEGER PRIMARY KEY, business_id INTEGER REFERENCES businesses(id),
      batch_id INTEGER REFERENCES batches(id), source TEXT, source_id TEXT, data TEXT NOT NULL);
    CREATE INDEX IF NOT EXISTS obs_identity ON observations(source,source_id);
    CREATE TABLE IF NOT EXISTS reviews(id INTEGER PRIMARY KEY, business_id INTEGER REFERENCES businesses(id),
      created_at TEXT, data TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS suppressions(kind TEXT, value TEXT, reason TEXT, PRIMARY KEY(kind,value));
    CREATE TABLE IF NOT EXISTS runs(id INTEGER PRIMARY KEY, created_at TEXT, kind TEXT, metrics TEXT);
    ''')
    from . import changes
    changes.initialize(db)
    return db


def load_rights(path, adapter):
    p = Path(path)
    m = json.loads(p.read_text(encoding='utf-8-sig'))
    required = ('source', 'license', 'terms_url', 'attribution', 'reviewed_at')
    if any(not isinstance(m.get(k), str) or not m[k].strip() for k in required):
        raise ValueError('Rights manifest requires nonempty ' + ', '.join(required))
    if m.get('retention_permitted') is not True or m.get('export_permitted') is not True or m.get('lead_discovery_permitted') is not True:
        raise ValueError('Source rights must explicitly permit retention, exports and lead discovery')
    if 'google' in (m['source'] + m['license'] + m['terms_url']).lower():
        raise ValueError('Google content is unsupported in the persistent import pipeline')
    if adapter == 'overture' and m['source'] != 'overture':
        raise ValueError('Overture adapter requires source=overture')
    if adapter == 'osm' and m['source'] != 'osm':
        raise ValueError('OSM adapter requires source=osm')
    date.fromisoformat(m['reviewed_at'])
    texts = []
    for filename in m.get('notice_files', []):
        file = p.parent / filename
        texts.append({'name': file.name, 'text': file.read_text(encoding='utf-8')})
    if m['license'] != 'synthetic' and not texts:
        raise ValueError('Non-synthetic data requires notice_files containing applicable licenses/notices')
    m['notices'] = texts
    return m


def normalize(row):
    for k in ('source_id', 'name', 'country', 'state', 'city', 'address', 'postcode', 'activity', 'brand', 'source_url', 'observed_at'):
        row[k] = str(row.get(k) or '').strip()
    row['country'] = row['country'].upper()
    row['state'] = row['state'].upper().removeprefix('US-')
    row['activity'] = row['activity'].lower()
    row['activity'] = row['activity'] or 'unknown'
    for k in ('categories', 'websites', 'phones', 'socials'):
        row[k] = sorted(set(many(row.get(k))))
    if not row['name'] or not row['source_id']:
        raise ValueError('Every record requires source_id and name')
    if row['observed_at']:
        datetime.fromisoformat(row['observed_at'].replace('Z', '+00:00'))
    return row


def identity_keys(row):
    # No name-only, address-only, or phone-only automatic merge.
    prefix = (row['country'], row['state'], norm(row['name']))
    keys = {prefix + ('phone', phone(p)) for p in row['phones'] if phone(p)}
    if row['address'] and row['city']:
        keys.add(prefix + ('address', norm(row['city']), norm(row['address'])))
    return keys


def all_businesses(db):
    grouped = {}
    for o in db.execute('SELECT * FROM observations ORDER BY id'):
        d = json.loads(o['data'])
        defaults = dict.fromkeys(('name', 'country', 'state', 'city', 'address', 'postcode', 'source_url', 'observed_at'), '')
        b = grouped.setdefault(o['business_id'], {**defaults, 'activity': 'unknown', 'id': o['business_id'], 'observations': [], 'websites': [], 'phones': [], 'socials': [], 'brands': [], 'categories': []})
        b['observations'].append({'source': o['source'], 'source_id': o['source_id'], 'batch_id': o['batch_id'], **d})
        for k in ('websites', 'phones', 'socials', 'categories'):
            b[k] = sorted(set(b[k] + d[k]))
        if d['brand']:
            b['brands'] = sorted(set(b['brands'] + [d['brand']]))
        for k in ('name', 'country', 'state', 'city', 'address', 'postcode', 'activity', 'source_url', 'observed_at'):
            if d[k]:
                b[k] = d[k]
    for r in db.execute('SELECT * FROM reviews ORDER BY id'):
        if r['business_id'] in grouped:
            grouped[r['business_id']].setdefault('reviews', []).append({**json.loads(r['data']), 'created_at': r['created_at']})
    for b in grouped.values():
        b['review'] = (b.get('reviews') or [{}])[-1]
    return list(grouped.values())


def log_run(db, kind, metrics):
    db.execute('INSERT INTO runs(created_at,kind,metrics) VALUES(?,?,?)', (now(), kind, json.dumps(metrics)))


def import_data(db, path, adapter, rights):
    manifest = load_rights(rights, adapter)
    digest = hashlib.sha256(Path(path).read_bytes()).hexdigest()
    normalized = (normalize(ADAPTERS[adapter](raw)) for raw in records(path))
    return import_rows(db, normalized, manifest, digest)


def import_rows(db, normalized_rows, manifest, file_sha256):
    """Import already normalized, rights-validated rows, also usable on a preview copy.

    Internal callers must validate the source manifest before calling this helper.
    Row iteration and all observation/event writes share one atomic transaction.
    """
    from . import changes
    metrics = dict(records_read=0, inserted=0, duplicates=0, outside_us=0, missing_geography=0,
                   non_us_or_unsupported_geography=0, missing_categories=0, api_requests=0, estimated_cost_usd=0)
    # Pin identity and comparison baselines under the same writer lock as the
    # inserts. A concurrent CLI/app import must not reuse a stale alias map.
    with db:
        db.execute('BEGIN IMMEDIATE')
        existing = all_businesses(db)
        comparison = changes.Comparison(db, existing)
        aliases = {(o['source'], o['source_id']): b['id'] for b in existing for o in b['observations']}
        keys = {}
        for b in existing:
            for o in b['observations']:
                for key in identity_keys(o):
                    keys.setdefault(key, set()).add(b['id'])
        batch = db.execute('INSERT INTO batches(imported_at,source,file_sha256,manifest) VALUES(?,?,?,?)',
                           (now(), manifest['source'], file_sha256, json.dumps(manifest))).lastrowid
        for row in normalized_rows:
            metrics['records_read'] += 1
            if not row['categories']:
                metrics['missing_categories'] += 1
            if row['country'] != 'US' or row['state'] not in STATES:
                metrics['outside_us'] += 1
                key = 'missing_geography' if not row['country'] or not row['state'] else 'non_us_or_unsupported_geography'
                metrics[key] += 1
                continue
            alias = (manifest['source'], row['source_id'])
            matches = set().union(*(keys.get(k, set()) for k in identity_keys(row)))
            bid = aliases.get(alias)
            if bid is None and len(matches) == 1:
                bid = next(iter(matches))
            if bid is None:
                bid = db.execute('INSERT INTO businesses(created_at) VALUES(?)', (now(),)).lastrowid
                metrics['inserted'] += 1
            else:
                metrics['duplicates'] += 1
            db.execute('INSERT INTO observations(business_id,batch_id,source,source_id,data) VALUES(?,?,?,?,?)',
                       (bid, batch, *alias, json.dumps(row)))
            comparison.record(bid, batch, manifest['source'], row, matches)
            aliases[alias] = bid
            for key in identity_keys(row):
                keys.setdefault(key, set()).add(bid)
        metrics['batch_id'] = batch
        metrics['comparison'] = comparison.finish(metrics['inserted'], metrics['outside_us'])
        log_run(db, 'import', metrics)
    return metrics


def suppression_link_key(value):
    """Dedicated hosts identify a business; shared hosts require a specific URL.

    No network/redirect lookup. Preserve identity query parameters and path case;
    remove only known tracking parameters. A bare shared host never identifies a
    contractor, including legacy broad-domain suppression rows.
    """
    link = classify_link(value)
    host = link['host']
    if not host:
        return None
    # Tenant hostnames identify a business, unlike the provider root. Retain
    # compatibility with historical dedicated tenant-domain suppression keys.
    if link['reason'] == 'hosted_business_page_requires_review':
        return ('domain', host)
    if not host_matches(host, SHARED_PLATFORM_HOSTS):
        return ('domain', host) if link['kind'] == 'standalone_website' else None
    u = urlsplit(value if '://' in value else 'https://' + value)
    path = u.path.rstrip('/')
    query = [(k, v) for k, v in parse_qsl(u.query, keep_blank_values=True)
             if not k.lower().startswith('utm_') and k.lower() not in ('fbclid', 'gclid', 'msclkid')]
    if path in ('', '/maps', '/search', '/login', '/home', '/biz') and not query:
        return None
    return ('profile_url', host + path + ('?' + urlencode(sorted(query)) if query else '') +
            ('#' + u.fragment if u.fragment else ''))


def suppression_keys(b):
    result = {('business_id', str(b['id']))}
    result |= {('phone', phone(p)) for p in b['phones'] if phone(p)}
    # Include historical verified URLs and social fields so later reviews/imports
    # cannot undo contact suppression. Shared-platform identities stay URL-scoped.
    reviews = b.get('reviews') or [b.get('review', {})]
    links = b['websites'] + b.get('socials', []) + [r['verified_website'] for r in reviews if r.get('verified_website')]
    result |= {key for w in links if (key := suppression_link_key(w))}
    result |= {('source_id', o['source'] + ':' + o['source_id']) for o in b['observations']}
    for o in b['observations']:
        if o['address'] and o['city']:
            result.add(('identity', '|'.join(norm(o.get(k)) for k in ('name', 'address', 'city', 'state'))))
    return result


def suppress(db, path):
    count = 0
    ignored_links = 0
    with db:
        for row in records(path):
            found = []
            unsafe_link = False
            if row.get('phone'):
                found.append(('phone', phone(row['phone'])))
            if row.get('website') or row.get('domain'):
                key = suppression_link_key(row.get('website') or row.get('domain'))
                if key:
                    found.append(key)
                else:
                    unsafe_link = True
                    ignored_links += 1
            if row.get('source') and row.get('source_id'):
                found.append(('source_id', row['source'] + ':' + row['source_id']))
            if all(row.get(k) for k in ('name', 'address', 'city', 'state')):
                found.append(('identity', '|'.join(norm(row[k]) for k in ('name', 'address', 'city', 'state'))))
            found = [(k, v) for k, v in found if v]
            if not found:
                if unsafe_link:
                    raise ValueError('Suppression website/domain must identify a dedicated business domain or specific shared-platform profile; bare shared hosts are unsafe. Use phone, source+source_id or full business identity instead.')
                raise ValueError('Suppression row needs a US phone, dedicated domain/profile URL, source+source_id, or name+address+city+state')
            for k, v in found:
                db.execute('INSERT OR REPLACE INTO suppressions VALUES(?,?,?)', (k, v, row.get('reason') or 'already_contacted'))
                count += 1
    return {'suppression_keys_loaded': count, 'unsafe_link_values_ignored': ignored_links}


REVIEW_DEFAULTS = dict(website_status='unreviewed', verified_website='', candidate_website='',
                       activity='unknown', ownership='unknown', residential='unknown',
                       service_fit='unknown', delivery='unknown', disposition='pending',
                       queue_hold=False, evidence_observed_at=None, identity_status='unknown')
ACTIVITY_EVIDENCE_FIELDS = ('activity_evidence_observed_at', 'activity_evidence_url', 'activity_evidence_notes')


def review(db, bid, data, replace=False):
    """Append an effective decision, preserving omitted fields without re-dating evidence."""
    if not db.execute('SELECT 1 FROM businesses WHERE id=?', (bid,)).fetchone():
        raise ValueError('Unknown business ID')
    supplied = dict(data)
    supplied.pop('created_at', None)  # Recording metadata belongs to this insertion.
    if 'evidence_observed_at' in supplied and supplied['evidence_observed_at'] is not None:
        value = supplied['evidence_observed_at']
        if not isinstance(value, str) or not re.fullmatch(r'\d{4}-\d{2}-\d{2}', value):
            raise ValueError('evidence_observed_at must be YYYY-MM-DD')
        observed = date.fromisoformat(value)
        if observed > datetime.now(timezone.utc).date():
            raise ValueError('evidence_observed_at cannot be in the future')
    previous_row = db.execute('SELECT data FROM reviews WHERE business_id=? ORDER BY id DESC LIMIT 1', (bid,)).fetchone()
    previous = json.loads(previous_row['data']) if previous_row else {}
    effective = dict(REVIEW_DEFAULTS)
    if not replace:
        # Carry decisions, not insertion metadata or snapshot-origin claims.
        effective.update({k: previous[k] for k in REVIEW_DEFAULTS if k in previous})
    effective.update(supplied)
    if not public_url(effective.get('evidence_url')) or not str(effective.get('notes') or '').strip():
        raise ValueError('Review requires an HTTP(S) evidence URL and nonempty notes')
    if effective['website_status'] not in WEBSITE_STATUSES:
        raise ValueError('Invalid website_status')
    choices = {'activity': ('unknown', 'active', 'closed'), 'ownership': ('unknown', 'independent', 'chain'),
               'residential': ('unknown', 'yes', 'no'), 'service_fit': ('unknown', 'yes', 'no'),
               'delivery': ('unknown', 'mobile', 'fixed'), 'disposition': ('pending', 'rejected', 'contacted'),
               'identity_status': ('unknown', 'same_business', 'different_businesses', 'uncertain')}
    for field, allowed in choices.items():
        if effective[field] not in allowed:
            raise ValueError('Invalid ' + field)
    if effective['website_status'] == 'site_found' and not public_url(effective.get('verified_website')):
        raise ValueError('site_found requires --verified-website with an HTTP(S) URL')
    if effective['website_status'] == 'site_found' and not confirmable_site(effective['verified_website']):
        raise ValueError('site_found requires a dedicated-domain or tenant-hosted business site with evidence and notes; social/directory/contact/shortener links cannot be confirmed as sites')
    if effective.get('candidate_website') and not public_url(effective['candidate_website']):
        raise ValueError('candidate_website requires an HTTP(S) URL')
    # Only an explicit activity assertion binds the current review evidence to
    # activity. A website/date-only correction retains the original activity basis.
    if 'activity' in supplied or replace:
        activity_basis = effective
        values = (activity_basis.get('evidence_observed_at'), activity_basis.get('evidence_url'), activity_basis.get('notes'))
    else:
        values = tuple(previous.get(k, previous.get(fallback)) for k, fallback in zip(
            ACTIVITY_EVIDENCE_FIELDS, ('evidence_observed_at', 'evidence_url', 'notes')))
    effective.update(zip(ACTIVITY_EVIDENCE_FIELDS, values))
    recorded_at = now()
    with db:
        db.execute('INSERT INTO reviews(business_id,created_at,data) VALUES(?,?,?)', (bid, recorded_at, json.dumps(effective)))
        if effective.get('disposition') == 'contacted':
            b = next(b for b in all_businesses(db) if b['id'] == bid)
            for kind, value in suppression_keys(b):
                db.execute('INSERT OR REPLACE INTO suppressions VALUES(?,?,?)', (kind, value, 'manually_marked_contacted'))
    return {**effective, 'created_at': recorded_at}


def evaluate(b, config, suppression, today):
    r = b['review']
    website = website_assessment(b)
    trade = trade_assessment(b, config)
    identity = identity_assessment(b, phone)
    activity = activity_assessment(b, today, config.get('freshness_days', 180))
    scope = scope_assessment(b, config)
    site = website['classification'] in ('standalone_website', 'provider_hosted_website')
    reasons = []
    if any(x in suppression for x in suppression_keys(b)) or r.get('disposition') == 'contacted':
        reasons.append('suppressed_or_contacted')
    if b['brands'] or r.get('ownership') == 'chain' or any(norm(x) in norm(b['name']) for x in config.get('chain_names', []) if norm(x)):
        reasons.append('chain_signal')
    if r.get('activity') == 'closed' or (r.get('activity') != 'active' and b['activity'] in ('closed', 'permanently_closed', 'temporarily_closed')):
        reasons.append('closed_or_temporarily_closed')
    if scope['explicitly_outside_scope'] or r.get('disposition') == 'rejected':
        reasons.append('manually_rejected')
    if scope['explicitly_outside_delivery']:
        reasons.append('outside_delivery_scope')
    if r.get('queue_hold') is True:
        reasons.append('manual_hold')
    if site:
        reasons.append('website_present')
    if b['country'] != 'US' or b['state'] not in STATES:
        reasons.append('missing_or_unsupported_geography')
    regions = config.get('regions', [])
    if regions and not any(b['state'] == x['state'].upper() and (not x.get('cities') or norm(b['city']) in {norm(c) for c in x['cities']}) for x in regions):
        reasons.append('outside_region')
    trades = sorted(set(trade['matched_categories']) | set(trade['name_hints']))
    if trade['status'] in ('non_target', 'unknown'):
        reasons.append('outside_trade')
    if trade['negative_signals'] and trade['status'] == 'non_target':
        reasons.append('unrelated_trade_signal')
    parts = {'website_gap': website['points'], 'identity_contact': identity['points'],
             'trade_relevance': trade['points'], 'activity_evidence': activity['points']}
    flags = []
    if website['classification'] == 'uncertain_domain' or r.get('website_status') == 'uncertain':
        flags.append('website_needs_verification')
    if trade['status'] == 'uncertain':
        flags.append('trade_needs_verification')
    if not scope['fit_verified']:
        flags.append('residential_scope_unverified' if scope['scope'] == 'residential' else 'service_scope_unverified')
    if not scope['delivery_verified']:
        flags.append('delivery_scope_unverified')
    if r.get('ownership') != 'independent':
        flags.append('independence_unverified')
    if trade['unknown_category']:
        flags.append('category_unmapped')
    if not activity['current_operation_verified']:
        flags.append('activity_unverified')
    qualified = (not reasons and r.get('activity') == 'active' and r.get('ownership') == 'independent'
                 and scope['fit_verified'] and scope['delivery_verified'] and r.get('website_status') == 'no_site_found'
                 and activity['current_operation_verified'] and website['classification'] != 'uncertain_domain'
                 and trade['status'] == 'target')
    history = b.get('reviews') or []
    carried = bool(r.get('reconciliation_action_id') or r.get('snapshot_action_id') or r.get('research_record_key'))
    review_state = ('carried_research_review' if carried else 'saved_human_review') if history else 'raw_import_only'
    queue_status = ('contact_excluded' if 'suppressed_or_contacted' in reasons else
                    'website_signal_excluded' if 'website_present' in reasons else
                    'rejected' if 'manually_rejected' in reasons else
                    'held' if r.get('queue_hold') else 'excluded' if reasons else 'review_needed')
    return {**b, 'website_signal': website['classification'], 'verification_status': r.get('website_status', 'unreviewed'),
            'queue_status': queue_status, 'screening_state': 'machine_evaluated',
            'review_state': review_state, 'review_history_count': len(history),
            'latest_review_recorded_at': r.get('created_at'), 'latest_evidence_observed_at': r.get('evidence_observed_at'),
            'scope_assessment': scope,
            'trades': trades, 'excluded_reasons': reasons, 'qualified': bool(qualified),
            'score': sum(parts.values()), 'score_breakdown': parts, 'ranking_version': '2',
            'website_assessment': website, 'trade_assessment': trade, 'identity_assessment': identity,
            'activity_assessment': activity, 'review_flags': flags,
            'ranking_interpretation': 'Heuristic review priority, not a probability of conversion or verified website absence.'}


def search(db, config, include_excluded=False, qualified_only=False):
    if config.get('service_scope', 'residential') not in ('residential', 'service'):
        raise ValueError('service_scope must be residential or service')
    if config.get('delivery_scope', 'any') not in ('any', 'mobile', 'fixed'):
        raise ValueError('delivery_scope must be any, mobile or fixed')
    for key in ('broad_categories', 'excluded_categories'):
        if not isinstance(config.get(key, []), list) or not all(isinstance(x, str) and x for x in config.get(key, [])):
            raise ValueError(key + ' must be a list of category strings')
    if not isinstance(config.get('trades'), dict) or not config['trades']:
        raise ValueError('Config requires nonempty trades mapping')
    if any(not isinstance(v, list) or not v or not all(isinstance(a, str) and a for a in v) for v in config['trades'].values()):
        raise ValueError('Each trade needs a nonempty list of category aliases')
    if not isinstance(config.get('regions', []), list):
        raise ValueError('regions must be a list')
    for region in config.get('regions', []):
        if not isinstance(region, dict) or region.get('state', '').upper() not in STATES:
            raise ValueError('Each region requires a two-letter US state')
        if not isinstance(region.get('cities', []), list):
            raise ValueError('Region cities must be a list')
    if not isinstance(config.get('chain_names', []), list):
        raise ValueError('chain_names must be a list')
    if type(config.get('freshness_days', 180)) is not int or config.get('freshness_days', 180) < 0:
        raise ValueError('freshness_days must be a nonnegative integer')
    suppression = {(r['kind'], r['value']) for r in db.execute('SELECT * FROM suppressions')}
    as_of = date.fromisoformat(config['as_of']) if config.get('as_of') else datetime.now(timezone.utc).date()
    results = [evaluate(b, config, suppression, as_of) for b in all_businesses(db)]
    selected = [b for b in results if (include_excluded or not b['excluded_reasons']) and (not qualified_only or b['qualified'])]
    selected.sort(key=lambda b: (-b['score'], b['id']))
    counts = {}
    for b in results:
        for reason in b['excluded_reasons']:
            counts[reason] = counts.get(reason, 0) + 1
    metrics = {'businesses_scanned': len(results), 'candidates': sum(not b['excluded_reasons'] for b in results),
               'output_scope': 'all_records_audit' if include_excluded else 'qualified_only' if qualified_only else 'eligible_candidate_queue',
               'actual_reviewed_businesses': sum(bool(b.get('reviews')) for b in results),
               'raw_import_only_businesses': sum(not b.get('reviews') for b in results),
               'candidate_review_states': {state: sum(b['review_state'] == state and not b['excluded_reasons'] for b in results)
                                           for state in ('raw_import_only', 'saved_human_review', 'carried_research_review')},
               'qualified': sum(b['qualified'] for b in results), 'returned': len(selected),
               'candidate_yield': sum(not b['excluded_reasons'] for b in results) / len(results) if results else 0,
               'exclusions': counts, 'api_requests': 0, 'estimated_cost_usd': 0, 'config': config}
    # Preserve unsafe legacy rows as historical records, but surface their lack of
    # business identity rather than applying a platform-wide exclusion.
    metrics['legacy_shared_domain_suppressions_ignored'] = sum(
        kind == 'domain' and host_matches(domain(value), SHARED_PLATFORM_HOSTS)
        and suppression_link_key(value) != ('domain', domain(value))
        for kind, value in suppression)
    targets = [b for b in results if b['trade_assessment']['status'] == 'target']
    uncertain = [b for b in results if b['trade_assessment']['status'] == 'uncertain']
    target_candidates = sum(not b['excluded_reasons'] for b in targets)
    target_records = candidate_target_records = 0
    for b in results:
        for o in b['observations']:
            if trade_assessment(o, config)['status'] == 'target':
                target_records += 1
                candidate_target_records += not bool(b['excluded_reasons'])
    metrics.update(target_trade_businesses=len(targets), target_trade_candidates=target_candidates,
                   target_trade_yield=target_candidates / len(targets) if targets else 0,
                   target_trade_records=target_records, candidate_target_trade_records=candidate_target_records,
                   target_trade_record_yield=candidate_target_records / target_records if target_records else 0,
                   uncertain_trade_businesses=len(uncertain),
                   uncertain_trade_candidates=sum(not b['excluded_reasons'] for b in uncertain),
                   missing_category_count=sum(b['trade_assessment']['missing_category'] for b in results),
                   unknown_category_count=sum(b['trade_assessment']['unknown_category'] for b in results),
                   missing_geography_count=sum(not b['country'] or not b['state'] for b in results),
                   ranking_version='2', target_yield_denominator='All specific-target businesses, before region/website/chain/status/suppression exclusions',
                   target_record_yield_denominator='Stored observations individually classified as specific target trades; reimports add observations')
    imports = [json.loads(r['metrics']) for r in db.execute("SELECT metrics FROM runs WHERE kind='import'")]
    metrics['import_rows_missing_geography'] = sum(r.get('missing_geography', 0) for r in imports)
    metrics['import_rows_missing_categories'] = sum(r.get('missing_categories', 0) for r in imports)
    metrics['legacy_import_geography_metrics_unavailable'] = sum('missing_geography' not in r for r in imports)
    with db:
        log_run(db, 'search', metrics)
    return selected, metrics


def safe_cell(value):
    s = json.dumps(value, ensure_ascii=False) if isinstance(value, (list, dict)) else str(value)
    return "'" + s if s.lstrip().startswith(('=', '+', '-', '@')) or s.startswith(('\t', '\r')) else s


def validate_export_paths(path, protected=()):
    """Reject unsafe destinations before opening files or running a CLI search."""
    path = Path(path)
    if path.suffix.lower() not in ('.csv', '.json'):
        raise ValueError('Export extension must be .csv or .json')
    sidecar = path.with_suffix(path.suffix + '.provenance.json')
    protected = {Path(value).resolve() for value in protected if value}
    for target in (path, sidecar):
        if target.resolve() in protected:
            raise ValueError('Export would overwrite a protected input or database path: ' + str(target))
        if target.exists() or target.is_symlink():
            raise ValueError('Export requires fresh output and provenance paths; already exists: ' + str(target))
    return path, sidecar


def export(db, rows, path):
    databases = [row[2] for row in db.execute('PRAGMA database_list') if row[2]]
    path, sidecar = validate_export_paths(path, protected=databases)
    batch_ids = {o['batch_id'] for b in rows for o in b['observations']}
    manifests = [json.loads(r['manifest']) for r in db.execute('SELECT id,manifest FROM batches') if r['id'] in batch_ids]
    if any(m.get('export_permitted') is not True for m in manifests):
        raise ValueError('Export blocked by source rights')
    metadata = {'exported_at': now(), 'rows': len(rows), 'sources': manifests,
                'contains_excluded_records': any(b.get('excluded_reasons') for b in rows),
                'warning_scope': 'Candidate/audit eligibility is not recipient permission or current contact-history clearance.',
                'warning': 'Missing website is a listing signal. no_site_found is a dated human finding, never proof of absence.'}
    if path.suffix.lower() == '.json':
        content = json.dumps(rows, indent=2, ensure_ascii=False)
        encoding = 'utf-8'
    else:
        fields = ['id', 'name', 'state', 'city', 'address', 'phones', 'websites', 'socials', 'trades', 'activity',
                  'queue_status', 'screening_state', 'review_state', 'review_history_count', 'latest_review_recorded_at', 'latest_evidence_observed_at', 'scope_assessment',
                  'website_signal', 'verification_status', 'qualified', 'score', 'score_breakdown', 'ranking_version',
                  'website_assessment', 'trade_assessment', 'identity_assessment', 'activity_assessment', 'review_flags',
                  'review', 'excluded_reasons', 'observations']
        stream = io.StringIO(newline='')
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        writer.writerows({k: safe_cell(b.get(k, '')) for k in fields} for b in rows)
        content = stream.getvalue()
        encoding = 'utf-8-sig'
    provenance = json.dumps(metadata, indent=2)
    path.parent.mkdir(parents=True, exist_ok=True)
    created = []
    try:
        # Exclusive creation also protects files appearing after the preflight.
        # Reserve both names before writing either document.
        with ExitStack() as stack:
            output = stack.enter_context(path.open('x', encoding=encoding, newline=''))
            created.append(path)
            notice = stack.enter_context(sidecar.open('x', encoding='utf-8', newline=''))
            created.append(sidecar)
            output.write(content)
            notice.write(provenance)
    except BaseException:
        # Remove only names successfully created by this attempt.
        for target in reversed(created):
            target.unlink(missing_ok=True)
        raise
    return {'exported': len(rows), 'path': str(path)}
