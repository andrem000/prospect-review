"""Read-only saved-review events. Stored insertion order is the sole chronology."""
import json
import re

from . import core, review_state


EVENT_ID = re.compile(r'(?:session:[a-f0-9]{32}|review:[1-9][0-9]{0,18})')
SUMMARY_ARRAYS = ('restored_decisions', 'session_restored_questions', 'retained_questions', 'overlap_questions')
SUMMARY_FLAGS = ('before_context_unknown', 'source_changed', 'review_changed', 'review_context_unknown')


def parameters(query):
    if set(query) - {'question', 'offset', 'limit', 'event_id'}:
        raise ValueError('Unsupported history query parameter')
    if any(len(values) != 1 for values in query.values()):
        raise ValueError('History query parameters must have one value')
    if 'event_id' in query:
        event_id = query['event_id'][0]
        if len(query) != 1 or not EVENT_ID.fullmatch(event_id):
            raise ValueError('Use one valid event_id without pagination or filter parameters')
        return dict(question='all', offset=0, limit=1, event_id=event_id)
    question = query.get('question', ['all'])[0]
    if question not in ('all', *review_state.FIELDS):
        raise ValueError('Unknown history question')
    offset, limit = query.get('offset', ['0'])[0], query.get('limit', ['20'])[0]
    if (not re.fullmatch(r'[0-9]{1,10}', offset) or not re.fullmatch(r'[0-9]{1,2}', limit)
            or not 1 <= int(limit) <= 50):
        raise ValueError('Offset must be nonnegative and limit between 1 and 50')
    return dict(question=question, offset=int(offset), limit=int(limit))


def _groups(rows, sessions):
    """Group only an entire contiguous, provable session; never guess legacy gaps."""
    counts = {}
    for row in rows:
        sid = row['data'].get('investigation_session')
        if isinstance(sid, str) and not row['data'].get('investigation_undo'):
            counts[sid] = counts.get(sid, 0) + 1
    index = 0
    while index < len(rows):
        row = rows[index]
        data = row['data']
        sid = data.get('investigation_session')
        session = sessions.get(sid) if isinstance(sid, str) else None
        submitted = json.loads(session['submitted']) if session else {}
        questions = set(submitted) if isinstance(submitted, dict) else set()
        candidate = rows[index:index + len(questions)]
        proven = bool(not data.get('investigation_undo') and session
                      and re.fullmatch(r'[a-f0-9]{32}', sid)
                      and questions and questions <= set(review_state.FIELDS)
                      and counts.get(sid) == len(questions) and len(candidate) == len(questions)
                      and candidate[-1]['id'] == session['final_revision'])
        if proven:
            seen = set()
            for member in candidate:
                entry = member['data']
                question = entry.get('investigation_question')
                if (entry.get('investigation_undo') or entry.get('investigation_session') != sid
                        or not isinstance(question, str) or question not in questions or question in seen
                        or not isinstance(submitted[question], dict)
                        or any(entry.get(k) != v for k, v in submitted[question].items())):
                    proven = False
                    break
                seen.add(question)
        if proven:
            yield candidate, sid
            index += len(candidate)
        else:
            yield [row], None
            index += 1


def _summary(session, revision):
    if not session or not session['undo_result']:
        return None
    receipt = json.loads(session['undo_result'])
    if receipt.get('review_revision') != revision or not isinstance(receipt.get('undo_result'), dict):
        return None
    summary = receipt['undo_result']
    # A history receipt is not a draft snapshot. Exclude historical text,
    # revision/context maps and source tokens even when present in old receipts.
    return {**{key: [q for q in summary[key] if isinstance(q, str) and q in review_state.FIELDS]
               for key in SUMMARY_ARRAYS if isinstance(summary.get(key), list)},
            **{key: summary[key] for key in SUMMARY_FLAGS if type(summary.get(key)) is bool}}


def read(db, bid, options):
    """The caller supplies one private SQLite snapshot for every returned fact."""
    if not db.execute('SELECT 1 FROM businesses WHERE id=?', (bid,)).fetchone():
        raise LookupError('Business not found')
    rows = [dict(id=row['id'], recorded_at=row['created_at'], data=json.loads(row['data']))
            for row in db.execute('SELECT id,created_at,data FROM reviews WHERE business_id=? ORDER BY id', (bid,))]
    sessions = {row['session_id']: row for row in db.execute(
        'SELECT * FROM investigation_sessions WHERE business_id=?', (bid,))}
    replay = review_state.Replay()
    events, originals = [], {}
    for members, sid in _groups(rows, sessions):
        first, last = members[0], members[-1]
        previous = replay.previous
        before = replay.saved()
        for row in members:
            replay.advance(row['data'])
        after = replay.saved()
        undo = last['data'].get('investigation_undo')
        original = originals.get(undo) if isinstance(undo, str) else None
        # Undo must win over inherited question/session metadata in its row.
        kind = 'undo' if undo else 'investigation_save' if sid else 'saved_review'
        asserted = [q for q in review_state.FIELDS if sid and q in json.loads(sessions[sid]['submitted'])]
        changed = [q for q in review_state.FIELDS
                   if (before.get(q) or {}).get('answers') != (after.get(q) or {}).get('answers')]
        event = dict(id='session:' + sid if sid else 'review:' + str(last['id']), kind=kind,
                     origin='guided' if sid or original else 'unknown',
                     review_ids=[r['id'] for r in members], first_revision=first['id'], last_revision=last['id'],
                     recorded_at=first['recorded_at'], recorded_until=last['recorded_at'], session_id=sid,
                     original_session_id=undo if isinstance(undo, str) else None,
                     original_event_id=original['id'] if original else None,
                     asserted_questions=asserted, changed_questions=changed,
                     association_unknown=not bool(sid or original), matches_filter='known',
                     before=before, after=after,
                     other_changes=[dict(field=key, before=previous.get(key, core.REVIEW_DEFAULTS[key]),
                                         after=last['data'].get(key, core.REVIEW_DEFAULTS[key]))
                                    for key in ('disposition', 'queue_hold')
                                    if previous.get(key, core.REVIEW_DEFAULTS[key]) != last['data'].get(key, core.REVIEW_DEFAULTS[key])],
                     record_evidence=[dict(review_id=r['id'], question=r['data'].get('investigation_question') if sid else None,
                                           association_known=bool(sid),
                                           **{key: r['data'].get(key) for key in review_state.EVIDENCE}) for r in members],
                     undo_result=_summary(sessions.get(undo), last['id']) if isinstance(undo, str) else None)
        # Known restored question/basis associations can matter even if values
        # are unchanged. Unknown generic assertion sets remain visible to filters.
        associated = set(asserted + changed + (original['asserted_questions'] if original else []))
        associated.update(q for q in review_state.FIELDS if before.get(q) != after.get(q))
        question = options['question']
        if question != 'all' and question not in associated:
            if event['association_unknown']:
                event['matches_filter'] = 'unknown'
            else:
                event = None
        if sid:
            originals[sid] = dict(id='session:' + sid, asserted_questions=asserted)
        if event is not None:
            events.append(event)
    events.reverse()
    offset, limit = options['offset'], options['limit']
    selected = options.get('event_id')
    if selected is not None:
        events = [event for event in events if event['id'] == selected]
    total = len(events)
    result = dict(business_id=bid, history_revision=rows[-1]['id'] if rows else 0,
                  question=options['question'], offset=offset, limit=limit, total=total,
                  has_more=offset + limit < total, items=events[offset:offset + limit])
    if selected is not None:
        result['selected_event_id'] = selected
    return result
