"""Private, revisioned investigation drafts and append-only guided sessions."""
import json
import re
import secrets
import sqlite3
from contextlib import closing
from datetime import date, datetime, timezone
from urllib.parse import urlsplit

from . import core, changes, review_state


class Conflict(Exception):
    """The browser must reload current state before attempting this change."""


CHOICES = {
    'identity': {'identity_status': ('same_business', 'different_businesses', 'uncertain')},
    'website': {'website_status': ('no_site_found', 'site_found', 'uncertain')},
    'activity': {'activity': ('active', 'closed', 'unknown')},
    'fit': {'ownership': ('unknown', 'independent', 'chain'), 'residential': ('unknown', 'yes', 'no'),
            'service_fit': ('unknown', 'yes', 'no'), 'delivery': ('unknown', 'mobile', 'fixed')},
}
EVIDENCE = {'evidence_url', 'notes', 'evidence_observed_at'}


def initialize(db):
    # Serialize schema inspection too: two app launches must not both decide
    # to add a column. Individual execute calls keep DDL and backfill in the
    # same transaction; executescript would implicitly commit it first.
    with db:
        db.execute('BEGIN IMMEDIATE')
        db.execute('''CREATE TABLE IF NOT EXISTS investigation_state(
          business_id INTEGER PRIMARY KEY REFERENCES businesses(id), draft_revision INTEGER NOT NULL,
          draft TEXT, questions TEXT NOT NULL)''')
        db.execute('''CREATE TABLE IF NOT EXISTS investigation_sessions(
          session_id TEXT PRIMARY KEY, business_id INTEGER NOT NULL REFERENCES businesses(id),
          request_id TEXT UNIQUE NOT NULL, payload TEXT NOT NULL, result TEXT NOT NULL,
          before_review TEXT NOT NULL, before_questions TEXT NOT NULL, submitted TEXT NOT NULL,
          before_draft TEXT, final_revision INTEGER NOT NULL, undone INTEGER NOT NULL DEFAULT 0)''')
        if 'draft_source_revision' not in {r['name'] for r in db.execute('PRAGMA table_info(investigation_state)')}:
            db.execute('ALTER TABLE investigation_state ADD COLUMN draft_source_revision TEXT')
        if 'source_revision' not in {r['name'] for r in db.execute('PRAGMA table_info(investigation_sessions)')}:
            db.execute('ALTER TABLE investigation_sessions ADD COLUMN source_revision TEXT')
        # Treat existing v0.4 work as the upgrade baseline, without changing answers.
        for row in db.execute('SELECT business_id FROM investigation_state WHERE draft_source_revision IS NULL').fetchall():
            db.execute('UPDATE investigation_state SET draft_source_revision=? WHERE business_id=?',
                       (changes.source_revision(db, row['business_id']), row['business_id']))
        for row in db.execute('SELECT session_id,business_id FROM investigation_sessions WHERE source_revision IS NULL').fetchall():
            db.execute('UPDATE investigation_sessions SET source_revision=? WHERE session_id=?',
                       (changes.source_revision(db, row['business_id']), row['session_id']))


def encoded(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False)


def revision(db, bid):
    return db.execute('SELECT COALESCE(MAX(id),0) FROM reviews WHERE business_id=?', (bid,)).fetchone()[0]


def state(db, bid):
    row = db.execute('SELECT * FROM investigation_state WHERE business_id=?', (bid,)).fetchone()
    return (row['draft_revision'], json.loads(row['draft']), json.loads(row['questions'])) if row else (0, None, {})


def draft_source(db, bid):
    row = db.execute('SELECT draft_source_revision FROM investigation_state WHERE business_id=?', (bid,)).fetchone()
    return row[0] if row else None


def write_state(db, bid, draft_revision, draft, questions, source_revision=None):
    source_revision = source_revision or draft_source(db, bid) or changes.source_revision(db, bid)
    db.execute('INSERT OR REPLACE INTO investigation_state(business_id,draft_revision,draft,questions,draft_source_revision) VALUES(?,?,?,?,?)',
               (bid, draft_revision, encoded(draft), encoded(questions), source_revision))


def check_source(db, bid, data, allow_stale=False):
    supplied = data.get('source_revision')
    if supplied is not None and (not isinstance(supplied, str) or not re.fullmatch(r'[a-f0-9]{64}', supplied)):
        raise ValueError('source_revision must identify the displayed source evidence')
    current = changes.source_revision(db, bid)
    _, draft, _ = state(db, bid)
    baseline = supplied or (draft_source(db, bid) if draft else None) or current
    if not allow_stale and baseline != current:
        raise Conflict('Source evidence changed; review the updated evidence before saving. Your unfinished draft is preserved.')
    return baseline


def contact_excluded(db, bid):
    business = next(b for b in core.all_businesses(db) if b['id'] == bid)
    suppressions = {(r['kind'], r['value']) for r in db.execute('SELECT kind,value FROM suppressions')}
    return business['review'].get('disposition') == 'contacted' or any(key in suppressions for key in core.suppression_keys(business))


def checked_keys(value, allowed, required=()):
    if not isinstance(value, dict) or set(value) - set(allowed) or set(required) - set(value):
        raise ValueError('Missing or unsupported investigation fields')


def safe_url(value):
    if not core.public_url(value) or re.search(r'[\s\x00-\x1f\x7f]', value):
        return False
    try:
        _ = urlsplit(value).port
    except ValueError:
        return False
    return True


def check_questions(questions, incomplete=False):
    checked_keys(questions, CHOICES)
    if not questions and not incomplete:
        raise ValueError('Check at least one question before saving')
    for question, entry in questions.items():
        choices = CHOICES[question]
        allowed = EVIDENCE | set(choices) | ({'verified_website', 'candidate_website'} if question == 'website' else set())
        checked_keys(entry, allowed, () if incomplete else EVIDENCE | set(choices))
        for field, value in entry.items():
            maximum = 8000 if field == 'notes' else 2048 if field.endswith('url') or field.endswith('website') else 64
            if not isinstance(value, str) or len(value) > maximum or '\x00' in value:
                raise ValueError('Investigation values must be bounded strings')
            if field in choices and value not in choices[field] and not (incomplete and value == ''):
                raise ValueError('Invalid ' + field)
        if incomplete:
            continue
        if not entry['notes'].strip() or not safe_url(entry['evidence_url']):
            raise ValueError('Each checked question requires an HTTP(S) evidence URL and nonempty notes')
        for field in ('verified_website', 'candidate_website'):
            if entry.get(field) and not safe_url(entry[field]):
                raise ValueError(field + ' must be a safe HTTP(S) URL')
        observed = entry['evidence_observed_at']
        if not re.fullmatch(r'\d{4}-\d{2}-\d{2}', observed) or date.fromisoformat(observed) > datetime.now(timezone.utc).date():
            raise ValueError('Each checked question requires a real, nonfuture YYYY-MM-DD evidence date')
    return questions


def check_draft(draft):
    if draft is None:
        return
    checked_keys(draft, ('step', 'mode', 'questions'), ('step', 'mode', 'questions'))
    if type(draft['step']) is not int or not 0 <= draft['step'] <= 3 or draft['mode'] not in ('workspace', 'guided'):
        raise ValueError('Invalid draft step or mode')
    check_questions(draft['questions'], incomplete=True)


def check_revisions(db, bid, data, with_draft=True):
    for field in ('review_revision', 'draft_revision') if with_draft else ('review_revision',):
        if type(data.get(field)) is not int or data[field] < 0:
            raise ValueError(field + ' must be a nonnegative integer')
    draft_revision, draft, questions = state(db, bid)
    if data['review_revision'] != revision(db, bid) or (with_draft and data['draft_revision'] != draft_revision):
        raise Conflict('This investigation changed; reload current state before saving')
    return draft_revision, draft, questions


def read(db, bid, business):
    current = revision(db, bid)
    draft_revision, draft, questions = state(db, bid)
    row = db.execute('SELECT * FROM investigation_sessions WHERE business_id=? ORDER BY rowid DESC LIMIT 1', (bid,)).fetchone()
    latest = None
    if row:
        latest = dict(session_id=row['session_id'], questions=json.loads(row['submitted']),
                      review_revision=row['final_revision'], undone=bool(row['undone']),
                      can_undo=not row['undone'] and row['final_revision'] == current)
    saved = review_state.saved_answers(business)
    return dict(review_revision=current, draft_revision=draft_revision, draft=draft,
                questions=review_state.editor_questions(saved), question_history=questions, saved_answers=saved,
                can_review='suppressed_or_contacted' not in business['excluded_reasons'], latest_session=latest,
                source_revision=changes.source_revision(db, bid), draft_source_revision=draft_source(db, bid),
                source_changed=bool(draft and draft_source(db, bid) != changes.source_revision(db, bid)))


def save_draft(db, bid, data):
    checked_keys(data, ('review_revision', 'draft_revision', 'source_revision', 'draft'), ('review_revision', 'draft_revision', 'draft'))
    check_draft(data['draft'])
    with db:
        db.execute('BEGIN IMMEDIATE')
        number, _, questions = check_revisions(db, bid, data)
        source_revision = check_source(db, bid, data, allow_stale=True)
        if data['draft'] is not None and contact_excluded(db, bid):
            raise ValueError('Contact-excluded businesses cannot be investigated')
        write_state(db, bid, number + 1, data['draft'], questions, source_revision)
    return dict(review_revision=revision(db, bid), draft_revision=number + 1, draft=data['draft'],
                source_revision=changes.source_revision(db, bid), draft_source_revision=source_revision,
                source_changed=bool(data['draft'] and source_revision != changes.source_revision(db, bid)))


def commit(db, bid, data, business):
    checked_keys(data, ('review_revision', 'draft_revision', 'source_revision', 'request_id', 'questions'),
                 ('review_revision', 'draft_revision', 'request_id', 'questions'))
    request_id = data['request_id']
    if not isinstance(request_id, str) or not request_id.strip() or len(request_id) > 128:
        raise ValueError('request_id must be a nonempty string of at most 128 characters')
    payload = encoded(data)
    existing = db.execute('SELECT * FROM investigation_sessions WHERE request_id=?', (request_id,)).fetchone()
    if existing:
        if existing['business_id'] != bid or existing['payload'] != payload:
            raise Conflict('request_id was already used for a different investigation')
        return json.loads(existing['result'])
    check_questions(data['questions'])
    data_version = db.execute('PRAGMA data_version').fetchone()[0]
    number, draft, questions = check_revisions(db, bid, data)
    source_revision = check_source(db, bid, data)
    if 'suppressed_or_contacted' in business['excluded_reasons']:
        raise ValueError('Contact-excluded businesses cannot be investigated')
    session_id = secrets.token_hex(16)
    # Core review commits internally. Stage every assertion before replacing live
    # state, so even a failure in the second assertion cannot save a partial session.
    with closing(sqlite3.connect(':memory:')) as staged:
        staged.row_factory = sqlite3.Row
        db.backup(staged)
        # Capture the snapshot's own previous effective review, rather than a
        # possibly older rendering obtained before another local writer ran.
        before_row = staged.execute('SELECT data FROM reviews WHERE business_id=? ORDER BY id DESC LIMIT 1', (bid,)).fetchone()
        before_review = json.loads(before_row[0]) if before_row else {}
        first_new_id = staged.execute('SELECT COALESCE(MAX(id),0) FROM reviews').fetchone()[0]
        for question in CHOICES:
            if question not in data['questions']:
                continue
            changes = dict(data['questions'][question], investigation_question=question, investigation_session=session_id)
            if question == 'identity' and changes['identity_status'] == 'different_businesses':
                changes['queue_hold'] = True
            result_review = core.review(staged, bid, changes)
        final_revision = revision(staged, bid)
        result = dict(review_revision=final_revision, draft_revision=number + 1, session_id=session_id, review=result_review,
                      source_revision=source_revision, source_changed=False)
        with staged:
            write_state(staged, bid, number + 1, None, {**questions, **data['questions']}, source_revision)
            staged.execute('INSERT INTO investigation_sessions VALUES(?,?,?,?,?,?,?,?,?,?,0,?)',
                           (session_id, bid, request_id, payload, encoded(result), encoded(before_review),
                            encoded(questions), encoded(data['questions']), encoded(draft), final_revision, source_revision))
        # Publish the staged delta in one live transaction. Holding SQLite's
        # writer lock and checking data_version also protects against a second
        # server/CLI writer. Never overwrite their imports or suppressions with
        # an earlier whole-database snapshot.
        with db:
            db.execute('BEGIN IMMEDIATE')
            raced = db.execute('SELECT * FROM investigation_sessions WHERE request_id=?', (request_id,)).fetchone()
            if raced:
                if raced['business_id'] != bid or raced['payload'] != payload:
                    raise Conflict('request_id was already used for a different investigation')
                return json.loads(raced['result'])
            if db.execute('PRAGMA data_version').fetchone()[0] != data_version:
                raise Conflict('Workspace changed while saving; reload before retrying')
            check_revisions(db, bid, data)
            check_source(db, bid, data)
            if contact_excluded(db, bid):
                raise ValueError('Contact-excluded businesses cannot be investigated')
            db.executemany('INSERT INTO reviews(id,business_id,created_at,data) VALUES(?,?,?,?)',
                           [tuple(row) for row in staged.execute('SELECT * FROM reviews WHERE id>? ORDER BY id', (first_new_id,))])
            write_state(db, bid, number + 1, None, {**questions, **data['questions']}, source_revision)
            db.execute('INSERT INTO investigation_sessions SELECT ?,?,?,?,?,?,?,?,?,?,0,?',
                       (session_id, bid, request_id, payload, encoded(result), encoded(before_review),
                        encoded(questions), encoded(data['questions']), encoded(draft), final_revision, source_revision))
    return result


def undo(db, bid, data, business):
    checked_keys(data, ('session_id', 'review_revision'), ('session_id', 'review_revision'))
    if not isinstance(data['session_id'], str) or len(data['session_id']) > 128:
        raise ValueError('Invalid session_id')
    with db:
        db.execute('BEGIN IMMEDIATE')
        number, current_draft, _ = check_revisions(db, bid, data, with_draft=False)
        row = db.execute('SELECT * FROM investigation_sessions WHERE business_id=? ORDER BY rowid DESC LIMIT 1', (bid,)).fetchone()
        if not row or row['session_id'] != data['session_id'] or row['undone'] or row['final_revision'] != revision(db, bid):
            raise Conflict('Only the latest untouched investigation save can be undone')
        if contact_excluded(db, bid):
            raise ValueError('Contact-excluded businesses cannot be investigated')
        before = json.loads(row['before_review'])
        restored = {**core.REVIEW_DEFAULTS, **before}
        restored.pop('created_at', None)
        # A compensating record preserves the prior evidence verbatim, including
        # an absent activity basis. Confirmed sites remain in core history.
        restored.update(investigation_undo=row['session_id'])
        recorded = core.now()
        previous_draft = current_draft or json.loads(row['before_draft'])
        draft = dict(step=previous_draft['step'] if previous_draft else 0,
                     mode=previous_draft['mode'] if previous_draft else 'workspace',
                     questions={**json.loads(row['submitted']), **(current_draft['questions'] if current_draft else {})})
        db.execute('INSERT INTO reviews(business_id,created_at,data) VALUES(?,?,?)', (bid, recorded, encoded(restored)))
        restored_source = draft_source(db, bid) if current_draft else row['source_revision']
        # A newer draft may override some submitted questions, but cannot certify
        # the source context of older answers restored alongside it.
        session_contributes = any(q not in (current_draft or {}).get('questions', {}) for q in json.loads(row['submitted']))
        if session_contributes and row['source_revision'] != changes.source_revision(db, bid):
            restored_source = row['source_revision']
        write_state(db, bid, number + 1, draft, json.loads(row['before_questions']), restored_source)
        db.execute('UPDATE investigation_sessions SET undone=1 WHERE session_id=?', (row['session_id'],))
    return dict(review_revision=revision(db, bid), draft_revision=number + 1, review={**restored, 'created_at': recorded})
