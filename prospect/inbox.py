"""Read-time attention reasons, with triage separate from human decisions.

Counts count business groups with at least one reason in that state; a group
can therefore contribute to both counts. Reading never records a search run.
"""
import hashlib
import json
import re
from datetime import date, datetime, timezone

from . import core, investigation, research, review_state


FIELDS = review_state.FIELDS


def initialize(db):
    db.executescript('''CREATE TABLE IF NOT EXISTS inbox_acknowledgements(
        reason_id TEXT PRIMARY KEY, seen INTEGER NOT NULL CHECK(seen IN (0,1)));''')


def _tables(db):
    return {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}


def _digest(value):
    return hashlib.sha256(investigation.encoded(value).encode('utf-8')).hexdigest()


def _answers(review, question):
    return review_state.answers(review, question)


def _bases(db, business, tables):
    return review_state.resolve(business)


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


def list_items(db, config, businesses=None):
    """Return reasons, optionally for supplied businesses from the same snapshot.

    Already evaluated businesses can be reused by the evidence desk; callers
    must supply them from this database snapshot and configuration.
    """
    today = date.fromisoformat(config['as_of']) if config.get('as_of') else datetime.now(timezone.utc).date()
    for key in ('freshness_days', 'advisory_review_days'):
        if type(config.get(key, 180)) is not int or config.get(key, 180) < 0:
            raise ValueError(key + ' must be a nonnegative integer')
    tables = _tables(db)
    acknowledgements = {r['reason_id']: bool(r['seen']) for r in db.execute(
        'SELECT reason_id,seen FROM inbox_acknowledgements')} if 'inbox_acknowledgements' in tables else {}
    events = {}
    if 'change_events' in tables:
        query, params = 'SELECT * FROM change_events', []
        if businesses is not None:
            params = [b['id'] for b in businesses]
            query += ' WHERE business_id IN (' + ','.join('?' for _ in params) + ')'
        for row in db.execute(query + ' ORDER BY id DESC', params):
            data = json.loads(row['data'])
            questions = data.get('questions') or list(dict.fromkeys(
                field['question'] for field in data.get('fields', []) if field.get('question')))
            reason = {**data, 'id': 'change:' + str(row['id']), 'batch_id': row['batch_id'],
                      'question': questions[0] if questions else 'identity', 'questions': questions,
                      'label': data.get('label') or 'Imported listing changed'}
            events.setdefault(row['business_id'], []).append(reason)
    suppression = {(r['kind'], r['value']) for r in db.execute('SELECT kind,value FROM suppressions')}
    items = []
    for business in core.all_businesses(db) if businesses is None else businesses:
        bid = business['id']
        evaluated = business if 'excluded_reasons' in business else core.evaluate(business, config, suppression, today)
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
            saved_answers=review_state.saved_answers(business),
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
