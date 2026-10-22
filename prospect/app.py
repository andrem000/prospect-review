"""Private, loopback-only browser application. No runtime network dependencies."""
import csv
import hashlib
import io
import json
import mimetypes
import os
import re
import secrets
import shutil
import sqlite3
import threading
import time
import webbrowser
import zipfile
from contextlib import closing
from datetime import date, datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib import resources
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from . import core, evidence, investigation, research, __version__
from . import inbox
from .providers import ADAPTERS

MAX_BODY = 8 * 1024 * 1024
MAX_ROWS = 5000
MAX_IDENTITIES = 20000
FIELDS = 'source_id name country state city address postcode categories phones websites socials activity brand observed_at source_url'.split()
ALIASES = {'source_id': ['id'], 'name': ['business_name'], 'state': ['region'], 'city': ['locality'],
           'postcode': ['zip', 'postal_code'], 'categories': ['trade', 'category'], 'phones': ['phone'],
           'websites': ['website'], 'observed_at': ['date']}
REVIEW_FIELDS = set(core.REVIEW_DEFAULTS) | {'evidence_url', 'notes'}


def _json(value):
    return json.dumps(value, ensure_ascii=False, allow_nan=False).encode('utf-8')


def _label(value):
    if not isinstance(value, str) or not value.strip():
        raise ValueError('A filename label is required')
    return re.sub(r'[^\w. -]', '_', value.replace('\\', '/').split('/')[-1])[:120] or 'upload'


def _parse(filename, content):
    if not isinstance(content, str):
        raise ValueError('Upload content must be UTF8 text')
    content.encode('utf-8')
    suffix = Path(filename).suffix.lower()
    if suffix == '.csv':
        reader = csv.DictReader(io.StringIO(content.lstrip('\ufeff'), newline=''))
        if not reader.fieldnames or len(set(reader.fieldnames)) != len(reader.fieldnames):
            raise ValueError('CSV requires unique column names')
        rows = []
        for row in reader:
            if None in row or any(v is None for v in row.values()):
                raise ValueError('CSV row does not match its column count')
            rows.append(row)
            if len(rows) > MAX_ROWS:
                raise ValueError('Import is limited to 5,000 rows')
    elif suffix in ('.jsonl', '.ndjson', '.geojsonseq'):
        rows = []
        for line in content.lstrip('\ufeff').splitlines():
            if line.strip():
                rows.append(json.loads(line.lstrip('\x1e')))
                if len(rows) > MAX_ROWS:
                    raise ValueError('Import is limited to 5,000 rows')
    elif suffix in ('.json', '.geojson'):
        data = json.loads(content.lstrip('\ufeff'))
        if isinstance(data, dict):
            if data.get('remark'):
                raise ValueError('Overpass response contains a remark/error; use a complete extract')
            data = data.get('features', data.get('elements'))
        if not isinstance(data, list):
            raise ValueError('Expected a JSON array, FeatureCollection or Overpass elements')
        rows = data
    else:
        raise ValueError('Use CSV, JSON, GeoJSON or JSONL UTF8 input')
    if len(rows) > MAX_ROWS:
        raise ValueError('Import is limited to 5,000 rows')
    if not all(isinstance(row, dict) and all(isinstance(k, str) for k in row) for row in rows):
        raise ValueError('Each row must be a JSON object')
    if not rows:
        raise ValueError('Upload contains no records')
    return rows


def _memory(db):
    copy = sqlite3.connect(':memory:')
    copy.row_factory = sqlite3.Row
    db.backup(copy)
    return copy


def _import_revision(db):
    """Pin evidence and review work used by a comparison across HTTP requests."""
    digest = hashlib.sha256()
    for table in ('observations', 'reviews', 'suppressions', 'investigation_state'):
        digest.update(table.encode())
        for row in db.execute('SELECT * FROM ' + table + ' ORDER BY rowid'):
            digest.update(_json(list(row)))
    return digest.hexdigest()


class Application:
    def __init__(self, workspace):
        self.root = Path(workspace).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.dbpath = self.root / 'review.sqlite3'
        self.configpath = self.root / 'config.json'
        for name in ('imports', 'exports'):
            (self.root / name).mkdir(exist_ok=True)
        self.lock = threading.RLock()
        self.research_lock = threading.Lock()
        self.token = secrets.token_urlsafe(32)
        self.previews = {}
        self.default = json.loads(resources.files('prospect').joinpath('data/config.example.json').read_text(encoding='utf-8'))
        self.default['regions'] = []
        self.default.pop('as_of', None)
        with closing(core.connect(self.dbpath)) as db:
            investigation.initialize(db)
            research.initialize(db)
            inbox.initialize(db)
            db.execute('CREATE TABLE IF NOT EXISTS app_exports(id TEXT PRIMARY KEY, data TEXT NOT NULL)')
            db.commit()
            if not self.configpath.exists():
                self.save_config(self.default, db)
            else:
                self.validate_config(self.config(), db)

    def config(self):
        return json.loads(self.configpath.read_text(encoding='utf-8'))

    def validate_config(self, value, db):
        if not isinstance(value, dict):
            raise ValueError('Config must be an object')
        if type(value.get('advisory_review_days', 180)) is not int or not 0 <= value.get('advisory_review_days', 180) <= 3650:
            raise ValueError('Advisory review window must be an integer from 0 to 3650 days')
        if any(not isinstance(x, str) for x in value.get('chain_names', [])):
            raise ValueError('chain_names must contain strings')
        regions = value.get('regions', [])
        if not isinstance(regions, list) or any(not isinstance(r, dict) or not isinstance(r.get('state'), str)
                or not isinstance(r.get('cities', []), list) or any(not isinstance(c, str) for c in r.get('cities', [])) for r in regions):
            raise ValueError('Regions require state strings and city string lists')
        if not isinstance(value.get('trades'), dict) or any(not isinstance(k, str) for k in value.get('trades', {})):
            raise ValueError('Trades must be a mapping of names to category lists')
        with closing(_memory(db)) as copy:
            core.search(copy, value, include_excluded=True)

    def save_config(self, value, db):
        self.validate_config(value, db)
        temporary = self.root / ('config-' + secrets.token_hex(8) + '.tmp')
        try:
            temporary.write_bytes(_json(value))
            os.replace(temporary, self.configpath)
        finally:
            temporary.unlink(missing_ok=True)

    def evaluated(self, db):
        with closing(_memory(db)) as copy:
            return core.search(copy, self.config(), include_excluded=True)[0]

    def preview(self, data):
        filename = _label(data.get('filename'))
        content = data.get('content')
        rows = _parse(filename, content)
        adapter = data.get('adapter', 'canonical')
        if adapter not in ADAPTERS:
            raise ValueError('Unknown adapter')
        columns = list(dict.fromkeys(k for row in rows for k in row))
        mapping = data.get('mapping')
        if mapping is None:
            mapping = {field: next((x for x in [field] + ALIASES.get(field, []) if x in columns), '') for field in FIELDS}
        if not isinstance(mapping, dict) or any(k not in FIELDS or not isinstance(v, str) or (v and v not in columns) for k, v in mapping.items()):
            raise ValueError('Mapping must pair canonical fields with existing column names')
        normalized, errors, skipped = [], [], 0
        for number, row in enumerate(rows, 1):
            try:
                mapped = {k: row[v] for k, v in mapping.items() if v} if adapter == 'canonical' else row
                result = core.normalize(ADAPTERS[adapter](mapped))
                normalized.append(result)
                if result['country'] != 'US' or result['state'] not in core.STATES:
                    skipped += 1
            except (ValueError, TypeError, KeyError, AttributeError) as exc:
                errors.append({'row': number, 'message': str(exc) or 'Malformed adapter row'})
        preview_id = secrets.token_hex(16)
        result = dict(preview_id=preview_id, columns=columns, mapping=mapping if adapter == 'canonical' else {},
                      sample=normalized[:5], total_rows=len(rows), valid_rows=len(normalized) - skipped,
                      skipped_rows=skipped, warnings=['Unsupported or missing geography will be skipped.'] if skipped else [],
                      errors=errors, can_import=not errors and len(normalized) > skipped)
        if len(self.previews) >= 10:
            self.previews.pop(next(iter(self.previews)))
        self.previews[preview_id] = dict(result=result, normalized=normalized, original=content,
                                        filename=filename, adapter=adapter, mapping=result['mapping'])
        return result

    def compare(self, db, data):
        preview = self.previews.get(data.get('preview_id'))
        if not preview or not preview['result']['can_import']:
            raise ValueError('Preview is missing, invalid or expired; preview again')
        source = data.get('source')
        if not isinstance(source, str) or not source.strip() or len(source) > 500:
            raise ValueError('Enter a source name before comparing imports')
        if preview['adapter'] in ('osm', 'overture') and source != preview['adapter']:
            raise ValueError('Adapter requires matching source name')
        with closing(_memory(db)) as staged:
            revision = _import_revision(staged)
            metrics = core.import_rows(staged, preview['normalized'], {'source': source},
                                       hashlib.sha256(_json(preview['normalized'])).hexdigest())
            events = [dict(business_id=row['business_id'], **json.loads(row['data'])) for row in
                      staged.execute('SELECT * FROM change_events WHERE batch_id=? ORDER BY id LIMIT 201', (metrics['batch_id'],))]
        result = dict(comparison_id=secrets.token_hex(16), summary=metrics['comparison'],
                      changes=events[:200], changes_truncated=len(events) > 200)
        preview['comparison'] = dict(source=source, revision=revision, result=result)
        return result

    def commit(self, db, data):
        preview = self.previews.get(data.get('preview_id'))
        if not preview or not preview['result']['can_import']:
            raise ValueError('Preview is missing, invalid, expired or already imported; preview again')
        rights = data.get('rights')
        if not isinstance(rights, dict):
            raise ValueError('Explicit source rights are required')
        required = ('source', 'license', 'terms_url', 'attribution', 'reviewed_at')
        if any(not isinstance(rights.get(k), str) or not rights[k].strip() for k in required):
            raise ValueError('Rights require nonempty ' + ', '.join(required))
        if not core.public_url(rights['terms_url']):
            raise ValueError('terms_url must be an HTTP(S) URL without credentials')
        if not re.fullmatch(r'\d{4}-\d{2}-\d{2}', rights['reviewed_at']):
            raise ValueError('reviewed_at must be YYYY-MM-DD')
        if date.fromisoformat(rights['reviewed_at']) > datetime.now(timezone.utc).date():
            raise ValueError('reviewed_at cannot be in the future')
        if any(rights.get(k) is not True for k in ('retention_permitted', 'export_permitted', 'lead_discovery_permitted')):
            raise ValueError('Explicit retention, export and lead discovery permissions are required')
        notices = data.get('notices', [])
        if not isinstance(notices, list) or any(not isinstance(n, dict) or not isinstance(n.get('name'), str)
                or not isinstance(n.get('text'), str) or not n['text'].strip() for n in notices):
            raise ValueError('Notices require a name and nonempty text')
        if rights['license'] != 'synthetic' and not notices:
            raise ValueError('Non-synthetic sources require uploaded license/notice text')
        if preview['adapter'] in ('osm', 'overture') and rights['source'] != preview['adapter']:
            raise ValueError('Adapter requires matching source name')
        comparison = preview.get('comparison')
        if comparison or data.get('comparison_id') is not None:
            if (not comparison or data.get('comparison_id') != comparison['result']['comparison_id']
                    or comparison['source'] != rights['source'] or comparison['revision'] != _import_revision(db)):
                raise investigation.Conflict('Workspace or source changed since comparison; compare again before importing')
        manifest = {k: rights[k] for k in required + ('retention_permitted', 'export_permitted', 'lead_discovery_permitted')}
        ident = secrets.token_hex(16)
        folder = self.root / 'imports' / ident
        folder.mkdir()
        try:
            original = preview['original'].encode('utf-8')
            normalized = _json(preview['normalized'])
            (folder / 'original.txt').write_bytes(original)
            (folder / 'normalized.json').write_bytes(normalized)
            manifest['notice_files'] = []
            for number, notice in enumerate(notices):
                name = f'notice-{number}.txt'
                (folder / name).write_text(notice['text'], encoding='utf-8')
                manifest['notice_files'].append(name)
            manifest['app_provenance'] = dict(original_filename=preview['filename'], original_sha256=hashlib.sha256(original).hexdigest(),
                normalized_sha256=hashlib.sha256(normalized).hexdigest(), adapter=preview['adapter'], mapping=preview['mapping'],
                notice_labels=[_label(n['name']) for n in notices], transformation='adapter/column mapping followed by canonical normalization')
            (folder / 'rights.json').write_bytes(_json(manifest))
            # Stage validation, then publish only new import rows under a writer
            # lock. Never replace newer reviews/drafts with an earlier DB backup.
            data_version = db.execute('PRAGMA data_version').fetchone()[0]
            tables = ('businesses', 'batches', 'observations', 'change_events', 'runs')
            maxima = {table: db.execute('SELECT COALESCE(MAX(id),0) FROM ' + table).fetchone()[0] for table in tables}
            with closing(_memory(db)) as staged:
                metrics = core.import_data(staged, folder / 'normalized.json', 'canonical', folder / 'rights.json')
                if staged.execute('SELECT COUNT(*) FROM businesses').fetchone()[0] > MAX_IDENTITIES:
                    raise ValueError('Workspace is limited to 20,000 identities')
                with db:
                    db.execute('BEGIN IMMEDIATE')
                    if db.execute('PRAGMA data_version').fetchone()[0] != data_version:
                        raise investigation.Conflict('Workspace changed while importing; compare again before retrying')
                    if comparison and comparison['revision'] != _import_revision(db):
                        raise investigation.Conflict('Workspace changed since comparison; compare again before importing')
                    for table in tables:
                        rows = [tuple(row) for row in staged.execute('SELECT * FROM ' + table + ' WHERE id>? ORDER BY id', (maxima[table],))]
                        if rows:
                            db.executemany('INSERT INTO ' + table + ' VALUES(' + ','.join('?' for _ in rows[0]) + ')', rows)
            del self.previews[data['preview_id']]
            return {'metrics': metrics}
        except BaseException:
            shutil.rmtree(folder)
            raise

    def research_business(self, db, bid):
        if not db.execute('SELECT 1 FROM businesses WHERE id=?', (bid,)).fetchone():
            raise LookupError('Business not found')
        return next(b for b in self.evaluated(db) if b['id'] == bid)

    def capture_research(self, bid, data):
        # Validation/persistence run under the ordinary mutation lock. Transport
        # never does; independent draft/review requests remain responsive.
        payload = research.validate_request(data)
        with self.lock, closing(core.connect(self.dbpath)) as db:
            before = self.research_business(db, bid)
            if 'suppressed_or_contacted' in before['excluded_reasons']:
                raise ValueError('Contact-excluded businesses cannot start new research')
            prior = research.existing(db, bid, payload, data['request_id'], before)
            if prior:
                return prior
            identity = research.fingerprint(before)
        # Bound network and analysis workers. Offline paste also uses this slot,
        # so concurrent retries cannot create duplicate captures.
        if not self.research_lock.acquire(blocking=False):
            raise investigation.Conflict('A research check is already running; retry after it completes')
        try:
            # Recheck after acquiring the slot in case another capture completed.
            with self.lock, closing(core.connect(self.dbpath)) as db:
                current = self.research_business(db, bid)
                if 'suppressed_or_contacted' in current['excluded_reasons']:
                    raise investigation.Conflict('Business became contact-excluded; research cancelled')
                if research.fingerprint(current) != identity:
                    raise investigation.Conflict('Business identity changed; research cancelled')
                prior = research.existing(db, bid, payload, data['request_id'], current)
                if prior:
                    return prior
            if data['mode'] == 'fetch':
                final_url, content, media = research.network.fetch_page(data['url'])
                observed = datetime.now(timezone.utc).date().isoformat()
            else:
                final_url, content, media = data['url'], data['content'].encode('utf-8'), 'text/html'
                observed = data['observed_at']
            record = research.analyze(before, content, data['url'], final_url, data['mode'], observed, media)
            with self.lock, closing(core.connect(self.dbpath)) as db:
                after = self.research_business(db, bid)
                if 'suppressed_or_contacted' in after['excluded_reasons']:
                    raise investigation.Conflict('Business became contact-excluded; captured result discarded')
                if research.fingerprint(after) != identity:
                    raise investigation.Conflict('Business identity changed; captured result discarded')
                prior = research.existing(db, bid, payload, data['request_id'], after)
                if prior:
                    return prior
                with db:
                    db.execute('INSERT INTO research_records VALUES(?,?,?,?,?,?)',
                        (record['id'], bid, data['request_id'], payload, identity, investigation.encoded(record)))
            return {'record': record}
        finally:
            self.research_lock.release()

    def dispatch(self, method, path, query, data, db):
        if method == 'GET' and path == '/api/state':
            rows = self.evaluated(db)
            return dict(version=__version__, workspace=self.root.name, config=self.config(), default_config=self.default,
                        counts=dict(total=len(rows), candidates=sum(not b['excluded_reasons'] for b in rows),
                                    reviewed=sum(bool(b.get('reviews')) for b in rows), qualified=sum(b['qualified'] for b in rows),
                                    excluded=sum(bool(b['excluded_reasons']) for b in rows)),
                        imports=[dict(r) for r in db.execute('SELECT id,source,imported_at FROM batches ORDER BY id DESC')],
                        exports=[json.loads(r[0]) for r in db.execute('SELECT data FROM app_exports ORDER BY rowid DESC')])
        if method == 'POST' and path == '/api/import/preview':
            return self.preview(data)
        if method == 'POST' and path == '/api/import/compare':
            return self.compare(db, data)
        if method == 'POST' and path == '/api/import/commit':
            return self.commit(db, data)
        if method == 'GET' and path == '/api/changes':
            return inbox.list_items(db, self.config())
        if method == 'POST' and path == '/api/changes/seen':
            return inbox.set_seen(db, self.config(), data.get('reason_ids'), data.get('seen'))
        if method == 'POST' and path == '/api/demo':
            if db.execute('SELECT COUNT(*) FROM batches').fetchone()[0] or db.execute('SELECT COUNT(*) FROM businesses').fetchone()[0] or db.execute('SELECT COUNT(*) FROM suppressions').fetchone()[0]:
                raise ValueError('Fictional demo requires an empty workspace')
            source = resources.files('prospect').joinpath('data')
            preview = self.preview(dict(filename='fictional.csv', content=source.joinpath('businesses.csv').read_text(encoding='utf-8'), adapter='canonical'))
            rights = json.loads(source.joinpath('rights.synthetic.json').read_text(encoding='utf-8'))
            result = self.commit(db, dict(preview_id=preview['preview_id'], rights=rights))
            with resources.as_file(source.joinpath('suppression.csv')) as suppression:
                result['metrics'].update(core.suppress(db, suppression))
            return result
        if method == 'POST' and path == '/api/config':
            self.save_config(data.get('config'), db)
            return {'config': self.config()}
        if method == 'POST' and path == '/api/exclusions':
            filename = _label(data.get('filename'))
            rows = _parse(filename, data.get('content'))
            temporary = self.root / 'imports' / (secrets.token_hex(16) + '.json')
            try:
                temporary.write_bytes(_json(rows))
                return {'metrics': core.suppress(db, temporary)}
            finally:
                temporary.unlink(missing_ok=True)
        research_route = re.fullmatch(r'/api/businesses/([1-9][0-9]*)/research(?:/([a-f0-9]{32})/dismiss)?', path)
        if research_route:
            bid, rid = int(research_route[1]), research_route[2]
            business = self.research_business(db, bid)
            if method == 'GET' and rid is None:
                return research.list_records(db, bid, business)
            if method == 'POST' and rid:
                return research.dismiss(db, bid, rid, data, business)
            raise LookupError('Route not found')
        investigation_route = re.fullmatch(r'/api/businesses/([1-9][0-9]*)/(investigation(?:/undo)?|draft)', path)
        if investigation_route:
            bid, operation = int(investigation_route[1]), investigation_route[2]
            if not db.execute('SELECT 1 FROM businesses WHERE id=?', (bid,)).fetchone():
                raise LookupError('Business not found')
            if method == 'GET' and operation == 'investigation':
                # Details and revision tokens describe exactly one evidence
                # snapshot, even when another server/CLI writes during this GET.
                # Never combine old displayed details with a newer save token.
                with closing(_memory(db)) as snapshot:
                    business = next(b for b in self.evaluated(snapshot) if b['id'] == bid)
                    return {**investigation.read(snapshot, bid, business), 'business': business,
                            'evidence': evidence.read(snapshot, business, self.config())}
            business = next(b for b in self.evaluated(db) if b['id'] == bid)
            if method == 'POST':
                if operation == 'draft':
                    if data.get('draft') is not None and 'suppressed_or_contacted' in business['excluded_reasons']:
                        raise ValueError('Contact-excluded businesses cannot be investigated')
                    return investigation.save_draft(db, bid, data)
                if operation == 'investigation':
                    return investigation.commit(db, bid, data, business)
                if operation == 'investigation/undo':
                    return investigation.undo(db, bid, data, business)
            raise LookupError('Route not found')
        match = re.fullmatch(r'/api/businesses/([1-9][0-9]*)(/review)?', path)
        if match:
            bid = int(match[1])
            if not db.execute('SELECT 1 FROM businesses WHERE id=?', (bid,)).fetchone():
                raise LookupError('Business not found')
            if method == 'GET' and not match[2]:
                return next(b for b in self.evaluated(db) if b['id'] == bid)
            if method == 'POST' and match[2]:
                changes = data.get('changes')
                if not isinstance(changes, dict) or any(k not in REVIEW_FIELDS for k in changes):
                    raise ValueError('Review contains unsupported fields')
                if type(data.get('replace', False)) is not bool:
                    raise ValueError('replace must be a boolean')
                if 'queue_hold' in changes and type(changes['queue_hold']) is not bool:
                    raise ValueError('queue_hold must be a boolean')
                if any(k != 'queue_hold' and v is not None and not isinstance(v, str) for k, v in changes.items()):
                    raise ValueError('Review values must be strings (evidence date may be null)')
                with db:
                    db.execute('BEGIN IMMEDIATE')
                    if 'review_revision' in data:
                        investigation.check_revisions(db, bid, data, with_draft=False)
                    investigation.check_source(db, bid, data)
                    review = core.review(db, bid, changes, replace=data.get('replace', False))
                return {'reviewed': bid, 'review': review}
        if method == 'GET' and path == '/api/businesses':
            return self.listing(db, query)
        if method == 'POST' and path == '/api/exports':
            ids, fmt = data.get('ids'), data.get('format')
            if not isinstance(ids, list) or not ids or any(type(x) is not int for x in ids) or len(ids) != len(set(ids)):
                raise ValueError('Select nonempty unique integer business IDs')
            if fmt not in ('csv', 'json'):
                raise ValueError('Export format must be csv or json')
            by_id = {b['id']: b for b in self.evaluated(db)}
            if any(i not in by_id for i in ids):
                raise ValueError('Export contains unknown business IDs')
            selected = [by_id[i] for i in ids]
            ident = secrets.token_hex(16)
            folder = self.root / 'exports' / ident
            folder.mkdir()
            try:
                payload = folder / ('businesses.' + fmt)
                core.export(db, selected, payload)
                with zipfile.ZipFile(folder / 'bundle.zip', 'x', zipfile.ZIP_DEFLATED) as bundle:
                    for p in (payload, payload.with_suffix(payload.suffix + '.provenance.json')):
                        bundle.write(p, p.name)
                entry = dict(id=ident, filename=f'prospect-review-{ident}.zip', created_at=core.now(), count=len(selected),
                             format=fmt, contains_excluded_records=any(bool(b['excluded_reasons']) for b in selected))
                with db:
                    db.execute('INSERT INTO app_exports VALUES(?,?)', (ident, json.dumps(entry)))
                return {**entry, 'download_url': f'/api/exports/{ident}/download'}
            except BaseException:
                shutil.rmtree(folder)
                raise
        raise LookupError('Route not found')

    def listing(self, db, query):
        get = lambda key, default='': query.get(key, [default])[0]
        queue, review, sort = get('queue', 'candidates'), get('review', 'all'), get('sort', 'score')
        if queue not in ('candidates', 'all', 'qualified', 'excluded') or review not in ('all', 'reviewed', 'unreviewed') or sort not in ('score', 'name', 'recent'):
            raise ValueError('Invalid queue, review or sort filter')
        offset, limit = int(get('offset', '0')), int(get('limit', '50'))
        if offset < 0 or not 1 <= limit <= 100:
            raise ValueError('Offset must be nonnegative and limit between 1 and 100')
        rows = self.evaluated(db)
        facets = dict(states=sorted({b['state'] for b in rows}), trades=sorted({t for b in rows for t in b['trades']}),
                      websites=sorted({b['website_signal'] for b in rows}))
        selected = []
        for b in rows:
            if queue == 'candidates' and b['excluded_reasons'] or queue == 'excluded' and not b['excluded_reasons'] or queue == 'qualified' and not b['qualified']:
                continue
            if review == 'reviewed' and not b.get('reviews') or review == 'unreviewed' and b.get('reviews'):
                continue
            if get('q').casefold() not in ' '.join(b[k] for k in ('name', 'city', 'address')).casefold():
                continue
            if get('state') and get('state') != b['state'] or get('trade') and get('trade') not in b['trades'] or get('website') and get('website') != b['website_signal']:
                continue
            selected.append(b)
        selected.sort(key=(lambda b: (b['name'].casefold(), b['id'])) if sort == 'name' else
                      (lambda b: (b.get('latest_review_recorded_at') or b['observed_at'] or '', -b['id'])) if sort == 'recent' else
                      (lambda b: (-b['score'], b['id'])), reverse=sort == 'recent')
        fields = 'id name city state trades score website_signal queue_status verification_status review_history_count review_flags qualified excluded_reasons latest_evidence_observed_at activity_assessment'.split()
        return dict(records=[{k: b[k] for k in fields} for b in selected[offset:offset + limit]], total=len(selected),
                    matching_ids=[b['id'] for b in selected], offset=offset, limit=limit, facets=facets)


class Handler(BaseHTTPRequestHandler):
    server_version = 'ProspectReview'
    def log_message(self, *args):
        pass  # Imported text and credentials never enter request logs.

    def reply(self, status, body, content_type='application/json; charset=utf-8', filename=None):
        self.send_response(status)
        self.send_header('Content-Type', content_type)
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Referrer-Policy', 'no-referrer')
        self.send_header('Content-Security-Policy', "default-src 'none'; script-src 'self'; style-src 'self'; connect-src 'self'; img-src 'self' data:; base-uri 'none'; frame-ancestors 'none'; form-action 'self'")
        if filename:
            self.send_header('Content-Disposition', f'attachment; filename="{filename}"')
        self.end_headers()
        self.wfile.write(body)

    def reject(self, status, message):
        # Closing a Windows socket with unread request bytes can reset it before
        # the caller receives the denial. Discard a bounded body without parsing
        # it, keeping a total deadline even for a slow or incomplete request.
        lengths = self.headers.get_all('Content-Length') or []
        if self.command == 'POST' and len(lengths) == 1 and not self.headers.get('Transfer-Encoding'):
            try:
                remaining = min(max(int(lengths[0]), 0), 65536)
            except ValueError:
                remaining = 0
            previous_timeout = self.connection.gettimeout()
            deadline = time.monotonic() + 0.25
            try:
                while remaining and time.monotonic() < deadline:
                    self.connection.settimeout(max(0.001, deadline - time.monotonic()))
                    chunk = self.rfile.read1(remaining)
                    if not chunk:
                        break
                    remaining -= len(chunk)
            except OSError:
                pass
            finally:
                self.connection.settimeout(previous_timeout)
        return self.reply(status, _json({'error': message}))

    def do_GET(self):
        self.handle_request('GET')

    def do_POST(self):
        self.handle_request('POST')

    def handle_request(self, method):
        try:
            app = self.server.app
            host = f'127.0.0.1:{self.server.server_port}'
            if self.headers.get_all('Host') != [host]:
                return self.reject(403, 'Host must match the local application address')
            origins = self.headers.get_all('Origin') or []
            if origins and origins != ['http://' + host]:
                return self.reject(403, 'Cross-origin requests are not permitted')
            if self.headers.get('Sec-Fetch-Site') == 'cross-site':
                return self.reject(403, 'Cross-site requests are not permitted')
            parsed = urlsplit(self.path)
            if parsed.scheme or parsed.netloc:
                raise ValueError('Absolute request URLs are unsupported')
            path = parsed.path
            if path.startswith('/api/'):
                if self.headers.get_all('X-Prospect-Token') != [app.token]:
                    return self.reject(403, 'Session token is required; reload the application')
                data = {}
                if method == 'POST':
                    if self.headers.get('Transfer-Encoding') or len(self.headers.get_all('Content-Length') or []) != 1:
                        raise ValueError('A single Content-Length is required')
                    size = int(self.headers.get('Content-Length', '-1'))
                    if not 0 <= size <= MAX_BODY:
                        return self.reject(413, 'Request exceeds the 8 MiB limit')
                    if self.headers.get('Content-Type', '').split(';')[0].strip().lower() != 'application/json':
                        return self.reject(415, 'Use application/json UTF8 requests')
                    raw = self.rfile.read(size)
                    data = json.loads(raw.decode('utf-8'), parse_constant=lambda x: (_ for _ in ()).throw(ValueError('Nonfinite JSON value')))
                    if not isinstance(data, dict):
                        raise ValueError('Request JSON must be an object')
                capture = re.fullmatch(r'/api/businesses/([1-9][0-9]*)/research', path)
                if method == 'POST' and capture:
                    return self.reply(200, _json(app.capture_research(int(capture[1]), data)))
                with app.lock, closing(core.connect(app.dbpath)) as db:
                    download = re.fullmatch(r'/api/exports/([a-f0-9]{32})/download', path)
                    if method == 'GET' and download:
                        row = db.execute('SELECT data FROM app_exports WHERE id=?', (download[1],)).fetchone()
                        if not row:
                            raise LookupError('Export not found')
                        entry = json.loads(row[0])
                        return self.reply(200, (app.root / 'exports' / download[1] / 'bundle.zip').read_bytes(), 'application/zip', entry['filename'])
                    result = app.dispatch(method, path, parse_qs(parsed.query, max_num_fields=30), data, db)
                return self.reply(200, _json(result))
            if method != 'GET':
                raise LookupError('Route not found')
            # Explicit names only. Never resolve a request path against the filesystem.
            files = {'/': 'index.html', '/index.html': 'index.html', '/app.js': 'app.js', '/app.css': 'app.css'}
            if path not in files:
                raise LookupError('Route not found')
            name = files[path]
            asset = resources.files('prospect').joinpath('web', name).read_bytes()
            if name == 'index.html':
                asset = asset.replace(b'__SESSION_TOKEN__', app.token.encode('ascii'))
            self.reply(200, asset, mimetypes.guess_type(name)[0] + '; charset=utf-8')
        except investigation.Conflict as exc:
            self.reply(409, _json({'error': str(exc)}))
        except (ValueError, TypeError, KeyError, AttributeError, UnicodeError, OverflowError, RecursionError) as exc:
            self.reply(400, _json({'error': str(exc) or 'Invalid request'}))
        except (LookupError, FileNotFoundError):
            self.reply(404, _json({'error': 'Resource not found'}))
        except (BrokenPipeError, ConnectionResetError):
            pass
        except Exception:
            self.reply(500, _json({'error': 'Local operation failed; check workspace access and retry'}))


def create_server(workspace, port=0):
    """Create a loopback server; caller owns serve_forever/server_close."""
    if type(port) is not int or not 0 <= port <= 65535:
        raise ValueError('Port must be between 0 and 65535')
    server = ThreadingHTTPServer(('127.0.0.1', port), Handler)
    server.daemon_threads = True
    try:
        server.app = Application(workspace)
    except BaseException:
        server.server_close()
        raise
    return server


def run(workspace, port=0, open_browser=True):
    server = create_server(workspace, port)
    url = f'http://127.0.0.1:{server.server_port}/'
    print(f'Prospect Review: {url}\nWorkspace: {server.app.root}', flush=True)
    if open_browser:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
