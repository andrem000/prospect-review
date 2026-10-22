"""Read-time attention reasons, with triage separate from human decisions.

Counts count business groups with at least one reason in that state; a group
can therefore contribute to both counts. Reading never records a search run.
"""
import hashlib
import json
import re
from datetime import date, datetime, timezone

from . import core, investigation, research


FIELDS = {q: tuple(fields) + (('verified_website', 'candidate_website') if q == 'website' else ())
          for q, fields in investigation.CHOICES.items()}


def initialize(db):
    db.executescript('''CREATE TABLE IF NOT EXISTS inbox_acknowledgements(
        reason_id TEXT PRIMARY KEY, seen INTEGER NOT NULL CHECK(seen IN (0,1)));''')


def _tables(db):
    return {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}


def _digest(value):
    return hashlib.sha256(investigation.encoded(value).encode('utf-8')).hexdigest()


def _answers(review, question):
    return {field: review.get(field, core.REVIEW_DEFAULTS.get(field)) for field in FIELDS[question]}


def _reviewed(review, question):
    return any(value != core.REVIEW_DEFAULTS.get(field) and value is not None
               for field, value in _answers(review, question).items())


def _bases(db, business, tables):
    """Associate evidence with effective answers, never with recording dates.

    Tagged snapshots establish a question basis. Later legacy snapshots only
    retain that association if all relevant answers are unchanged. Undo restores
    its saved question snapshot, subject to the same answer comparison.
    """
    bases, reviewed, previous = {}, set(), {}
    undo = {}
    if 'investigation_sessions' in tables:
        undo = {r['session_id']: json.loads(r['before_questions']) for r in db.execute(
            'SELECT session_id,before_questions FROM investigation_sessions WHERE business_id=?', (business['id'],))}
    for row in business.get('reviews', []):
        for question in FIELDS:
            if _reviewed(row, question):
                reviewed.add(question)
            if _answers(row, question) != _answers(previous, question):
                bases.pop(question, None)
        if row.get('investigation_undo') in undo:
            bases = {q: entry for q, entry in undo[row['investigation_undo']].items()
                     if q in FIELDS and _answers(entry, q) == _answers(row, q)}
            reviewed.update(bases)
        question = row.get('investigation_question')
        if question in FIELDS and row.get('investigation_session'):
            bases[question] = {key: row.get(key) for key in (*FIELDS[question], *investigation.EVIDENCE)}
            reviewed.add(question)
        previous = row
    effective = business['review']
    # Since v0.2 these explicit activity fields carry their own association,
    # including through unrelated CLI reviews. Generic legacy evidence does not.
    if 'activity' in reviewed and all(field in effective for field in core.ACTIVITY_EVIDENCE_FIELDS):
        bases['activity'] = dict(activity=effective.get('activity'),
            evidence_observed_at=effective['activity_evidence_observed_at'],
            evidence_url=effective['activity_evidence_url'], notes=effective['activity_evidence_notes'])
    return reviewed, bases


def _age_reason(bid, question, basis, answers, today, days):
    observed = basis.get('evidence_observed_at') if basis else None
    age = None
    if isinstance(observed, str) and re.fullmatch(r'\d{4}-\d{2}-\d{2}', observed):
        try:
            age = (today - date.fromisoformat(observed)).days
        except ValueError:
            pass
    unknown = (not basis or not core.public_url(basis.get('evidence_url'))
               or not str(basis.get('notes') or '').strip() or age is None or age < 0)
    if not unknown and age <= days:
        return None
    kind = 'evidence_unknown' if unknown else 'evidence_old'
    return dict(id=f'evidence:{bid}:{question}:' + _digest(dict(basis=basis, answers=answers)),
                kind=kind, question=question, questions=[question],
                label=('Evidence date/basis unknown' if unknown else 'Old evidence'),
                evidence_observed_at=observed, evidence_age_days=age,
                evidence_url=basis.get('evidence_url') if basis else None,
                saved_answer=answers, evidence_basis=basis,
                threshold_days=days, advisory=question != 'activity', basis_known=bool(basis))


def list_items(db, config):
    """Return current reasons without mutating reviews, drafts or run history."""
    today = date.fromisoformat(config['as_of']) if config.get('as_of') else datetime.now(timezone.utc).date()
    for key in ('freshness_days', 'advisory_review_days'):
        if type(config.get(key, 180)) is not int or config.get(key, 180) < 0:
            raise ValueError(key + ' must be a nonnegative integer')
    tables = _tables(db)
    acknowledgements = {r['reason_id']: bool(r['seen']) for r in db.execute(
        'SELECT reason_id,seen FROM inbox_acknowledgements')} if 'inbox_acknowledgements' in tables else {}
    events = {}
    if 'change_events' in tables:
        for row in db.execute('SELECT * FROM change_events ORDER BY id DESC'):
            data = json.loads(row['data'])
            questions = data.get('questions') or list(dict.fromkeys(
                field['question'] for field in data.get('fields', []) if field.get('question')))
            reason = {**data, 'id': 'change:' + str(row['id']), 'batch_id': row['batch_id'],
                      'question': questions[0] if questions else 'identity', 'questions': questions,
                      'label': data.get('label') or 'Imported listing changed'}
            events.setdefault(row['business_id'], []).append(reason)
    suppression = {(r['kind'], r['value']) for r in db.execute('SELECT kind,value FROM suppressions')}
    items = []
    for business in core.all_businesses(db):
        bid = business['id']
        evaluated = core.evaluate(business, config, suppression, today)
        reasons = list(events.get(bid, []))
        reviewed, bases = _bases(db, business, tables)
        for question in FIELDS:
            if question not in reviewed:
                continue
            days = config.get('freshness_days' if question == 'activity' else 'advisory_review_days', 180)
            reason = _age_reason(bid, question, bases.get(question), _answers(business['review'], question), today, days)
            if reason:
                reasons.append(reason)
        if 'research_records' in tables:
            fingerprint = research.fingerprint(business)
            for row in db.execute('SELECT * FROM research_records WHERE business_id=? ORDER BY rowid DESC', (bid,)):
                record = json.loads(row['data'])
                if not record.get('dismissed') and research.is_stale(row, business):
                    reasons.append(dict(id=f"research:{row['id']}:{fingerprint}",
                        kind='research_context_changed', question='identity', questions=['identity', 'website'],
                        label='Business details changed since this research card', research_id=row['id'],
                        evidence_observed_at=record.get('observed_at'), evidence_url=record.get('final_url') or record.get('url')))
        if not reasons:
            continue
        for reason in reasons:
            reason['seen'] = acknowledgements.get(reason['id'], False)
        draft = db.execute('SELECT draft FROM investigation_state WHERE business_id=?', (bid,)).fetchone() if 'investigation_state' in tables else None
        items.append(dict(business_id=bid, name=business['name'], city=business['city'], state=business['state'],
            has_review=bool(business.get('reviews')), has_draft=bool(draft and json.loads(draft[0]) is not None),
            can_review='suppressed_or_contacted' not in evaluated['excluded_reasons'],
            saved_answers={question: dict(answers=_answers(business['review'], question),
                evidence_observed_at=(bases.get(question) or {}).get('evidence_observed_at'),
                evidence_url=(bases.get(question) or {}).get('evidence_url'),
                notes=(bases.get(question) or {}).get('notes'), basis_known=question in bases)
                for question in FIELDS if question in reviewed},
            excluded_reasons=evaluated['excluded_reasons'], reasons=reasons))
    return dict(items=items, counts=dict(pending=sum(any(not r['seen'] for r in item['reasons']) for item in items),
                                        seen=sum(any(r['seen'] for r in item['reasons']) for item in items)))


def set_seen(db, config, reason_ids, seen):
    """Triage only named, still-current reasons under SQLite's writer lock."""
    if type(seen) is not bool:
        raise ValueError('seen must be an explicit boolean')
    if not isinstance(reason_ids, list) or not reason_ids or len(reason_ids) > 10000 or any(
            not isinstance(value, str) or not value or len(value) > 512 for value in reason_ids):
        raise ValueError('reason_ids must be a nonempty bounded list of strings')
    with db:
        db.execute('BEGIN IMMEDIATE')
        current = {r['id'] for item in list_items(db, config)['items'] for r in item['reasons']}
        valid = sorted(set(reason_ids) & current)
        db.executemany('INSERT OR REPLACE INTO inbox_acknowledgements(reason_id,seen) VALUES(?,?)',
                       [(rid, int(seen)) for rid in valid])
    return dict(updated=valid, ignored=sorted(set(reason_ids) - current), seen=seen)
