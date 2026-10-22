"""Append-only source comparisons. No review, draft or exclusion writes.

Comparison means import order, never chronological freshness. Empty fields mean
not supplied, and absent rows have no deletion semantics. Only future imports
produce events: initializing an old workspace never backfills historical alerts.
"""
import hashlib
import json
from datetime import datetime, timezone
from urllib.parse import urlsplit, urlunsplit


QUESTIONS = {
    'name': 'identity', 'country': 'identity', 'state': 'identity',
    'city': 'identity', 'address': 'identity', 'postcode': 'identity',
    'phones': 'identity', 'websites': 'website', 'socials': 'website',
    'activity': 'activity', 'categories': 'fit', 'brand': 'fit',
}
SUMMARY_KEYS = ('new_businesses', 'changed_businesses', 'unchanged_rows',
                'additional_sources', 'identity_warnings', 'skipped_rows')


def initialize(db):
    db.execute('''CREATE TABLE IF NOT EXISTS change_events(
        id INTEGER PRIMARY KEY, business_id INTEGER REFERENCES businesses(id),
        batch_id INTEGER REFERENCES batches(id), data TEXT NOT NULL)''')
    db.execute('CREATE INDEX IF NOT EXISTS change_event_business ON change_events(business_id,id)')


def _digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def _url(value):
    """Ignore host casing/default ports/root slash, preserve tenant path/query."""
    value = str(value).strip()
    try:
        parts = urlsplit(value if '://' in value else 'https://' + value)
        host = (parts.hostname or '').lower()
        if not host or parts.username or parts.password:
            return value
        port = parts.port
        host = '[' + host + ']' if ':' in host else host
        if port and (parts.scheme.lower(), port) not in (('http', 80), ('https', 443)):
            host += ':' + str(port)
        return urlunsplit((parts.scheme.lower(), host, parts.path or '/', parts.query, parts.fragment))
    except ValueError:
        return value


def meaningful(row):
    """Stable semantic fields only; original values remain in observations/events."""
    from . import core
    result = {}
    for field in QUESTIONS:
        value = row.get(field)
        if field in ('websites', 'socials'):
            result[field] = sorted({_url(v) for v in value or []})
        elif field == 'phones':
            result[field] = sorted({core.phone(v) or core.norm(v) for v in value or []})
        elif field == 'categories':
            result[field] = sorted({core.norm(v) for v in value or []})
        else:
            result[field] = core.norm(value)
    return result


def source_revision(db, bid):
    """Hash latest semantic evidence plus its last material transition.

    A -> B -> A must not silently restore an old draft's revision: aggregate
    evidence still retains B. Identical imports create no events, so remain
    stable. Dates, formatting and list ordering do not change this revision.
    """
    latest = {}
    for row in db.execute('SELECT source,source_id,data FROM observations WHERE business_id=? ORDER BY id', (bid,)):
        latest[(row['source'], row['source_id'])] = meaningful(json.loads(row['data']))
    event_id = db.execute('SELECT COALESCE(MAX(id),0) FROM change_events WHERE business_id=?', (bid,)).fetchone()[0]
    return _digest(dict(aliases=[[*alias, value] for alias, value in sorted(latest.items())], last_transition=event_id))


def batch_summary(db, batch_id):
    """New/changed businesses are distinct IDs; additional_sources counts aliases.

    unchanged_rows counts accepted rows matching the previous same-alias content
    (including equivalent repeats in one batch). identity_warnings counts aliases
    needing identity attention, at most once per alias. skipped_rows counts rows
    outside supported geography. An import summary is saved with its run, even
    when no attention events were created. Old batches have no comparison data.
    """
    for row in db.execute("SELECT metrics FROM runs WHERE kind='import' ORDER BY id DESC"):
        metrics = json.loads(row['metrics'])
        if metrics.get('batch_id') == batch_id:
            return metrics.get('comparison', dict.fromkeys(SUMMARY_KEYS, 0))
    return dict.fromkeys(SUMMARY_KEYS, 0)


def _stamp(value):
    try:
        parsed = datetime.fromisoformat(str(value).replace('Z', '+00:00'))
        return parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed.astimezone(timezone.utc)
    except (ValueError, TypeError):
        return None


class Comparison:
    """Per-import state, used inside the import transaction by all entry points."""

    def __init__(self, db, existing):
        self.db = db
        self.existing_ids = {b['id'] for b in existing}
        self.latest = {(o['source'], o['source_id']): (b['id'], o)
                       for b in existing for o in b['observations']}
        self.previous_patterns = {}
        for b in existing:
            for o in b['observations']:
                alias = (o['source'], o['source_id'])
                if o['batch_id'] == self.latest[alias][1]['batch_id']:
                    self.previous_patterns.setdefault(alias, set()).add(_digest(meaningful(o)))
        self.pending = {}
        self.changed = set()
        self.additional = set()
        self.summary = dict.fromkeys(SUMMARY_KEYS, 0)

    def record(self, bid, batch, source, row, matches):
        """Retain every observation; compare final alias values once per batch.

        Conflicting rows are deterministic (last row wins the source baseline).
        Comparing only final values avoids A/B duplicate rows recreating alerts
        on each identical reimport. Conflict patterns are compared separately.
        """
        alias = (source, row['source_id'])
        prior = self.pending.get(alias)
        before = prior['row'] if prior else self.latest.get(alias, (None, None))[1]
        current = meaningful(row)
        if before is not None and meaningful(before) == current:
            self.summary['unchanged_rows'] += 1
        patterns = prior['patterns'] if prior else set()
        patterns.add(_digest(current))
        self.pending[alias] = dict(bid=bid, batch=batch, source=source, row=row,
                                   matches=set(matches) | (prior['matches'] if prior else set()), patterns=patterns)

    def _compare(self, bid, batch, source, row, matches, patterns):
        alias = (source, row['source_id'])
        prior = self.latest.get(alias)
        before = prior[1] if prior else None
        current = meaningful(row)
        duplicate = len(patterns) > 1 and patterns != self.previous_patterns.get(alias)
        identity_warnings = []
        if duplicate:
            identity_warnings.append('Conflicting rows for this source identity in one batch; observations retained in file order, last row is the comparison baseline.')
        if prior and meaningful(before) != current and matches - {bid}:
            identity_warnings.append('Source identity points to this business but strong identity clues also match another business; existing binding retained.')
        if not prior and len(matches) > 1:
            identity_warnings.append('Multiple strong identity matches; existing importer created a separate business without merging.')
        if before:
            old = meaningful(before)
            identity_changed = any(old[f] != current[f] for f in ('name', 'country', 'state', 'city', 'address', 'phones'))
            shared_phone = bool(set(old['phones']) & set(current['phones']))
            shared_address = all(old[f] and old[f] == current[f] for f in ('country', 'state', 'city', 'address'))
            if identity_changed and old['name'] != current['name'] and not shared_phone and not shared_address:
                identity_warnings.append('Possible reused source identity: name and supporting contact/location clues changed; existing binding retained.')
        if identity_warnings:
            self.summary['identity_warnings'] += 1
        if bid in self.existing_ids:
            cross_source = before is None
            if cross_source:
                self.additional.add(alias)
                alternatives = [o for other, o in self.latest.values() if other == bid]
                before = max(alternatives, key=lambda o: o['batch_id']) if alternatives else None
            fields = []
            if before:
                old = meaningful(before)
                for field, question in QUESTIONS.items():
                    # An incomplete additional source is not evidence of a removal.
                    if old[field] != current[field] and (not cross_source or current[field]):
                        fields.append(dict(field=field, before=before.get(field), after=row.get(field), question=question))
            if fields or identity_warnings:
                disagreement = cross_source and any(old[f['field']] and current[f['field']] for f in fields)
                warnings = list(identity_warnings)
                if cross_source:
                    warnings.append('Additional source identity ' + (
                        'disagrees with existing source evidence' if disagreement else
                        'supplies additional evidence; previously missing fields do not establish absence') +
                        '; not a replacement or independently verified finding.')
                new_date, old_date = _stamp(row.get('observed_at')), _stamp((before or {}).get('observed_at'))
                if new_date is None:
                    warnings.append('Incoming observation date unknown; import time does not establish freshness.')
                elif new_date > datetime.now(timezone.utc):
                    warnings.append('Incoming observation date is in the future; freshness is unverified.')
                elif old_date and new_date < old_date:
                    warnings.append('Incoming observation predates the previous evidence; comparison follows import order.')
                event = dict(kind='source_conflict' if disagreement else 'source_change' if cross_source and fields else 'identity_warning' if identity_warnings else 'source_change',
                             source=source, source_id=row['source_id'],
                             before_source=(before or {}).get('source'), before_source_id=(before or {}).get('source_id'),
                             before_batch_id=(before or {}).get('batch_id'), after_batch_id=batch,
                             before_observed_at=(before or {}).get('observed_at'), after_observed_at=row.get('observed_at'),
                             before_source_url=(before or {}).get('source_url'), after_source_url=row.get('source_url'),
                             fields=fields, questions=sorted({f['question'] for f in fields} | ({'identity'} if identity_warnings else set())), warnings=warnings)
                if cross_source and fields and not disagreement:
                    event['label'] = 'Additional source evidence'
                self.db.execute('INSERT INTO change_events(business_id,batch_id,data) VALUES(?,?,?)', (bid, batch, json.dumps(event)))
                self.changed.add(bid)
        self.latest[alias] = (bid, {**row, 'source': source, 'batch_id': batch})

    def finish(self, new_businesses, skipped_rows):
        for pending in self.pending.values():
            self._compare(**pending)
        self.summary.update(new_businesses=new_businesses, changed_businesses=len(self.changed),
                            additional_sources=len(self.additional), skipped_rows=skipped_rows)
        return self.summary
