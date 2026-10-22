"""Shared read model for effective answers and their provable evidence basis.

Guided question snapshots are history, not the current review. Generic evidence
on a later partial review cannot establish a basis for carried answers.
"""
from . import core


FIELDS = {
    'identity': ('identity_status',),
    'website': ('website_status', 'verified_website', 'candidate_website'),
    'activity': ('activity',),
    'fit': ('ownership', 'residential', 'service_fit', 'delivery'),
}
EVIDENCE = ('evidence_url', 'notes', 'evidence_observed_at')


def answers(review, question):
    return {field: review.get(field, core.REVIEW_DEFAULTS.get(field)) for field in FIELDS[question]}


def resolve(business):
    bases, reviewed, previous, before_sessions = {}, set(), {}, {}
    for row in business.get('reviews', []):
        session = row.get('investigation_session')
        question = row.get('investigation_question')
        if session and question in FIELDS and session not in before_sessions:
            # Capture the actual pre-session association, not before_questions:
            # those retained snapshots may already have been superseded by CLI.
            before_sessions[session] = dict(bases)
        for key in FIELDS:
            current = answers(row, key)
            if any(v != core.REVIEW_DEFAULTS.get(f) and v is not None for f, v in current.items()):
                reviewed.add(key)
            if current != answers(previous, key):
                bases.pop(key, None)
        if row.get('investigation_undo'):
            bases = {q: entry for q, entry in before_sessions.get(row['investigation_undo'], {}).items()
                     if answers(entry, q) == answers(row, q)}
        if question in FIELDS and session:
            bases[question] = {key: row.get(key, core.REVIEW_DEFAULTS.get(key))
                               for key in (*FIELDS[question], *EVIDENCE)}
            reviewed.add(question)
        previous = row
    effective = business['review']
    # Only newly recorded assertion metadata can establish a default-valued
    # activity check; legacy generic evidence cannot prove it was supplied.
    if effective.get('activity_asserted') is True:
        reviewed.add('activity')
    if 'activity' in reviewed and all(f in effective for f in core.ACTIVITY_EVIDENCE_FIELDS):
        bases['activity'] = dict(activity=effective.get('activity'),
            evidence_observed_at=effective['activity_evidence_observed_at'],
            evidence_url=effective['activity_evidence_url'], notes=effective['activity_evidence_notes'])
    return reviewed, bases


def saved_answers(business):
    reviewed, bases = resolve(business)
    return {q: dict(answers=answers(business['review'], q),
                    **{key: (bases.get(q) or {}).get(key) for key in EVIDENCE}, basis_known=q in bases)
            for q in FIELDS if q in reviewed}


def editor_questions(saved):
    """Current editor defaults; unknown evidence is intentionally left blank."""
    return {q: {**{field: value for field, value in entry['answers'].items()
                   if field not in ('verified_website', 'candidate_website') or value},
                **{key: entry.get(key) or '' for key in EVIDENCE}}
            for q, entry in saved.items()}
