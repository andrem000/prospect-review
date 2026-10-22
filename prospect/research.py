"""Explicit, explainable single-page research; separate from business decisions."""
import hashlib
import json
import re
import secrets
from datetime import date, datetime, timezone
from html.parser import HTMLParser
from urllib.parse import urljoin

from . import core, investigation, changes
from . import research_network as network

MAX_CONTENT = network.MAX_CONTENT
CONTEXT_FIELDS = ('name', 'address', 'city', 'state', 'phones', 'websites')


def fingerprint(business):
    """Versioned semantic aggregate context, excluding equivalent formatting."""
    normalized = changes.meaningful(business)
    value = {key: normalized[key] for key in CONTEXT_FIELDS}
    return 'v2:' + hashlib.sha256(investigation.encoded(value).encode()).hexdigest()


def _legacy_fingerprint(business):
    value = {key: business.get(key) for key in CONTEXT_FIELDS}
    return hashlib.sha256(investigation.encoded(value).encode()).hexdigest()


def _legacy_contexts(business):
    """Exact historical aggregate hashes paired with their semantic contexts.

    Legacy cards stored no business snapshot. Recover an association only when
    their original hash matches a prefix of the retained observation history;
    never accept merely similar names or an unmatched legacy hash. No old card
    or evidence is rewritten during this read-time compatibility check.
    """
    aggregate = dict.fromkeys(('name', 'address', 'city', 'state'), '')
    aggregate.update(phones=[], websites=[])
    contexts = {}
    for observation in business.get('observations', []):
        for key in ('name', 'address', 'city', 'state'):
            if observation.get(key):
                aggregate[key] = observation[key]
        for key in ('phones', 'websites'):
            aggregate[key] = sorted(set(aggregate[key]) | set(observation.get(key) or []))
        contexts[_legacy_fingerprint(aggregate)] = fingerprint(aggregate)
    return contexts


def is_stale(row, business, legacy_contexts=None):
    """Keep new and provably associated legacy cards valid across formatting."""
    stored, current = row['fingerprint'], fingerprint(business)
    if stored.startswith('v2:'):
        return stored != current
    if stored == _legacy_fingerprint(business):
        return False
    contexts = _legacy_contexts(business) if legacy_contexts is None else legacy_contexts
    return contexts.get(stored) != current


def initialize(db):
    db.executescript('''CREATE TABLE IF NOT EXISTS research_records(
        id TEXT PRIMARY KEY, business_id INTEGER NOT NULL REFERENCES businesses(id),
        request_id TEXT NOT NULL, payload TEXT NOT NULL, fingerprint TEXT NOT NULL,
        data TEXT NOT NULL, UNIQUE(business_id,request_id));''')


def validate_request(data):
    if set(data) - {'request_id', 'mode', 'url', 'retention_confirmed', 'content', 'observed_at'}:
        raise ValueError('Unsupported research request field')
    if not isinstance(data.get('request_id'), str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,128}', data['request_id']):
        raise ValueError('A bounded request_id is required')
    if data.get('mode') not in ('fetch', 'paste') or data.get('retention_confirmed') is not True:
        raise ValueError('Choose fetch/paste and explicitly confirm evidence retention')
    network.validate_url(data.get('url'))
    if data['mode'] == 'paste':
        content = data.get('content')
        if not isinstance(content, str) or not content.strip() or len(content.encode('utf-8')) > MAX_CONTENT:
            raise ValueError('Saved-page content must contain text and be at most 512 KiB')
        observed = data.get('observed_at')
        if not isinstance(observed, str) or not re.fullmatch(r'\d{4}-\d{2}-\d{2}', observed) or date.fromisoformat(observed) > datetime.now(timezone.utc).date():
            raise ValueError('Saved-page observation date must be explicit YYYY-MM-DD and nonfuture')
    elif 'content' in data or 'observed_at' in data:
        raise ValueError('Live captures do not accept saved-page content or observation dates')
    return hashlib.sha256(investigation.encoded(data).encode('utf-8')).hexdigest()


class PageParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.text, self.titles, self.meta, self.links, self.phones, self.structured = [], [], [], [], [], []
        self.stack = []
        self.script = None
        self.script_parts = []
        self.in_title = False

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == 'script':
            self.script = attrs.get('type', '').lower() == 'application/ld+json'
            self.script_parts = []
        hidden = tag in ('script', 'style', 'template', 'noscript') or 'hidden' in attrs or attrs.get('aria-hidden') == 'true' or bool(re.search(r'(display\s*:\s*none|visibility\s*:\s*hidden)', attrs.get('style', ''), re.I))
        inherited = self.stack[-1][1] if self.stack else False
        # Void elements must not leave the remaining page hidden.
        if tag not in ('area', 'base', 'br', 'col', 'embed', 'hr', 'img', 'input', 'link', 'meta', 'param', 'source', 'track', 'wbr'):
            self.stack.append((tag, hidden or inherited))
        if hidden or inherited:
            return
        if tag == 'title':
            self.in_title = True
        if tag == 'meta' and attrs.get('name', '').lower() == 'description':
            self.meta.append(attrs.get('content', '')[:1000])
        if tag == 'a':
            href = attrs.get('href', '')
            if href.lower().startswith('tel:'):
                self.phones.append(href[4:][:100])
            elif len(self.links) < 60:
                self.links.append(href[:4096])

    def handle_endtag(self, tag):
        if tag == 'script' and self.script is not None:
            if self.script and sum(map(len, self.script_parts)) <= 65536:
                try:
                    self.structured.append(json.loads(''.join(self.script_parts)))
                except (ValueError, RecursionError):
                    pass
            self.script = None
            self.script_parts = []
        if tag == 'title':
            self.in_title = False
        for index in range(len(self.stack) - 1, -1, -1):
            if self.stack[index][0] == tag:
                del self.stack[index:]
                break

    def handle_data(self, text):
        if self.script:
            if sum(map(len, self.script_parts)) <= 65536:
                self.script_parts.append(text[:65537])
        if self.stack and self.stack[-1][1]:
            return
        if self.in_title:
            self.titles.append(text)
        self.text.append(text)


def business_entities(values):
    entities, remaining = [], list(values)
    for _ in range(100):
        if not remaining:
            break
        value = remaining.pop(0)
        if isinstance(value, list):
            remaining.extend(value[:30])
        elif isinstance(value, dict):
            types = value.get('@type', [])
            types = [types] if isinstance(types, str) else types
            if isinstance(types, list) and any(isinstance(t, str) and (t.rsplit('/', 1)[-1] in ('Organization', 'LocalBusiness', 'ProfessionalService', 'HomeAndConstructionBusiness', 'HousePainter', 'GeneralContractor', 'Plumber', 'Electrician', 'RoofingContractor', 'Locksmith', 'CleaningService', 'MovingCompany', 'Organization', 'AutomotiveBusiness', 'Store', 'Corporation') or t.endswith('Business')) for t in types):
                entities.append(value)
            remaining.extend(v for k, v in value.items() if isinstance(v, (dict, list)))
    return entities[:20]


def analyze(business, content, url, final_url, mode, observed_at, media='text/html'):
    raw = content.decode('utf-8', errors='replace')
    parser = PageParser()
    if media == 'text/plain' or not re.search(r'<[a-zA-Z][^>]*>', raw):
        parser.text = [raw]
    else:
        parser.feed(raw)
    clean = lambda text: re.sub(r'\s+', ' ', str(text)).strip()
    visible = clean(' '.join(parser.text))
    entities = business_entities(parser.structured)
    entity_text = []
    structured_names, structured_phones, structured_addresses = [], [], []
    complete_addresses = []
    for entity in entities:
        if isinstance(entity.get('name'), str):
            structured_names.append(clean(entity['name'])[:300])
        if isinstance(entity.get('telephone'), str):
            structured_phones.append(entity['telephone'][:100])
        address = entity.get('address')
        if isinstance(address, dict):
            if address.get('streetAddress') and address.get('addressLocality'):
                complete_addresses.append(address)
            address = ' '.join(str(address.get(k, ''))[:200] for k in ('streetAddress', 'addressLocality', 'addressRegion', 'postalCode'))
        if isinstance(address, str):
            structured_addresses.append(clean(address)[:800])
        entity_text.extend(structured_names[-1:] + structured_phones[-1:] + structured_addresses[-1:])
    evidence = clean(' '.join([visible] + parser.meta + entity_text))
    normalized = core.norm(evidence)
    signals = []
    def add(kind, field, label, detail):
        signals.append(dict(kind=kind, field=field, label=label, detail=clean(detail)[:800]))
    name = core.norm(business.get('name'))
    if name and name in normalized:
        add('match', 'name', 'Name appears on the page', business['name'])
    else:
        add('unknown', 'name', 'Name not established', 'No matching business name was extracted; missing text is not a contradiction.')
    for published in structured_names:
        if name and core.norm(published) != name:
            add('conflict', 'name', 'Different structured business name', published + ' (unverified structured claim)')
    expected = {core.phone(p) for p in business.get('phones', [])} - {''}
    published = {core.phone(p) for p in parser.phones + structured_phones} - {''}
    published.update(core.phone(p) for p in re.findall(r'(?<!\d)(?:\+?1[ .()-]*)?\(?\d{3}\)?[ .-]*\d{3}[ .-]*\d{4}(?!\d)', visible))
    published.discard('')
    if expected & published:
        add('match', 'phone', 'Matching phone published', ', '.join(sorted(expected & published)))
    elif expected and published:
        add('conflict', 'phone', 'Different phone published', ', '.join(sorted(published)[:8]))
    else:
        add('unknown', 'phone', 'Phone not established', 'No comparable phone evidence was extracted.')
    other_structured_phones = {core.phone(p) for p in structured_phones} - {''} - expected
    if expected and other_structured_phones and expected & published:
        add('conflict', 'phone', 'Inconsistent structured phone values', ', '.join(sorted(other_structured_phones)))
    address = core.norm(business.get('address'))
    city = core.norm(business.get('city'))
    if address and city and address in normalized and city in normalized:
        add('match', 'address', 'Street and city appear', business['address'] + ', ' + business['city'])
    else:
        add('unknown', 'address', 'Address not established', 'A matching street and city were not both extracted.')
    for item in complete_addresses:
        published = clean(' '.join(str(item.get(k, '')) for k in ('streetAddress', 'addressLocality', 'addressRegion', 'postalCode')))
        if address and city and (address not in core.norm(published) or city not in core.norm(published) or (item.get('addressRegion') and business.get('state') and core.norm(item['addressRegion']) != core.norm(business['state']))):
            add('conflict', 'address', 'Different structured location', published + ' (unverified structured claim)')
    ambiguous = len({core.norm(n) for n in structured_names}) > 1
    if ambiguous:
        add('conflict', 'entities', 'Multiple business identities', 'Different structured entities occur on this page; their relationship is unverified.')
    matches = {s['field'] for s in signals if s['kind'] == 'match'}
    strong = 'name' in matches and bool(matches & {'phone', 'address'}) and not any(s['kind'] == 'conflict' for s in signals)
    captured_at = core.now()
    notes = (f"Proposed from {'live capture' if mode == 'fetch' else 'user-supplied saved page (not independently fetched)'} of {final_url}; observed {observed_at}, captured {captured_at}. " + '; '.join(s['label'] + ': ' + s['detail'] for s in signals))[:5000]
    base = dict(evidence_url=final_url, evidence_observed_at=observed_at, notes=notes)
    site_found = strong and core.confirmable_site(final_url)
    proposals = dict(identity=dict(identity_status='same_business' if strong else 'uncertain', **base),
        website=dict(website_status='site_found' if site_found else 'uncertain', **({'verified_website': final_url} if site_found else {'candidate_website': final_url}), **base))
    links = []
    for href in parser.links:
        try:
            target = network.validate_url(urljoin(final_url, href))
        except ValueError:
            continue
        if target not in {link['url'] for link in links}:
            links.append(dict(url=target, label=target[:200]))
        if len(links) == 12:
            break
    limitations = ['One page only; no search, subresources or linked pages were checked.', 'A loading page does not establish current activity, ownership or qualification.', 'Extracted content and structured claims are unverified; proposals require human checking.']
    if mode == 'paste':
        limitations.append('Saved-page content was supplied by the user and was not independently fetched or verified.')
    return dict(id=secrets.token_hex(16), business_id=business['id'], mode=mode, url=url, final_url=final_url,
        title=clean(' '.join(parser.titles))[:300], captured_at=captured_at, observed_at=observed_at,
        content_sha256=hashlib.sha256(content).hexdigest(), excerpt=visible[:800], signals=signals[:50], links=links,
        proposals=proposals, limitations=limitations, dismissed=False, stale=False, retention_confirmed=True)


def read_record(row, business):
    record = json.loads(row['data'])
    record['stale'] = is_stale(row, business)
    return record


def list_records(db, bid, business):
    return {'records': [read_record(row, business) for row in db.execute('SELECT * FROM research_records WHERE business_id=? ORDER BY rowid DESC', (bid,))]}


def existing(db, bid, payload, request_id, business):
    row = db.execute('SELECT * FROM research_records WHERE business_id=? AND request_id=?', (bid, request_id)).fetchone()
    if row:
        if row['payload'] != payload:
            raise investigation.Conflict('Research request_id reused with different content')
        return {'record': read_record(row, business)}
    if db.execute('SELECT COUNT(*) FROM research_records WHERE business_id=?', (bid,)).fetchone()[0] >= 100:
        raise ValueError('Research history is limited to 100 records per business')
    return None


def dismiss(db, bid, rid, data, business):
    if set(data) != {'dismissed'} or type(data.get('dismissed')) is not bool:
        raise ValueError('dismissed must be an explicit boolean')
    row = db.execute('SELECT * FROM research_records WHERE id=? AND business_id=?', (rid, bid)).fetchone()
    if not row:
        raise LookupError('Research record not found')
    record = json.loads(row['data'])
    record['dismissed'] = data['dismissed']
    with db:
        db.execute('UPDATE research_records SET data=? WHERE id=?', (investigation.encoded(record), rid))
    record['stale'] = is_stale(row, business)
    return {'record': record}
