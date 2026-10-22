"""Read-only imported evidence and retained screening signals for one desk.

Latest means last imported observation, including duplicates within a file.
Missing fields and missing rows never retract earlier evidence. This view does
not decide identity, freshness, qualification or acknowledgement.
"""
from . import core, inbox
from .signals import classify_link


def source_groups(observations):
    grouped = {}
    for observation in observations:
        grouped.setdefault((observation['source'], observation['source_id']), []).append(observation)
    return [dict(source=source, source_id=source_id, latest=rows[-1],
                 history=list(reversed(rows[:-1])), observation_count=len(rows))
            for (source, source_id), rows in grouped.items()]


def retained_signals(db, business, groups):
    """Explain historical signals and actual matched contact controls.

    A historical phone/profile alone is evidence, never a contact exclusion.
    Suppression claims use exactly the key matcher used by core screening.
    """
    suppression = {(r['kind'], r['value']) for r in db.execute('SELECT kind,value FROM suppressions')}
    matched = core.suppression_keys(business) & suppression
    signals, emitted = [], set()
    assessment = business['website_assessment']
    website_links = {(link['origin'], link['url']) for link in assessment['links']
                     if link['kind'] in ('standalone_website', 'provider_hosted_website')}
    for group in groups:
        latest = group['latest']
        for observation in group['history']:
            context = {key: observation.get(key) for key in ('source', 'source_id', 'batch_id', 'observed_at', 'source_url')}
            for field in ('websites', 'socials', 'phones'):
                for value in observation.get(field, []):
                    marker = (group['source'], group['source_id'], field, value)
                    if value in latest.get(field, []) or marker in emitted:
                        continue
                    emitted.add(marker)
                    if (field, value) in website_links:
                        signals.append(dict(kind='website', value=value, field=field, **context,
                            explanation='Earlier imported website evidence is retained and still contributes to website_present screening; a later omission does not establish removal or absence.'))
                    elif field == 'phones' or classify_link(value)['kind'] in ('social', 'contact_aggregator'):
                        key = ('phone', core.phone(value)) if field == 'phones' else core.suppression_link_key(value)
                        excluded = key in matched
                        signals.append(dict(kind='historical_contact', value=value, field=field, **context,
                            suppression_kind=key[0] if excluded else None,
                            explanation=('Earlier imported contact evidence matches a saved suppression and contributes to suppressed_or_contacted screening.' if excluded else
                                         'Earlier imported contact evidence remains available for identity/contact screening; reachability is unverified. This value does not match a saved contact suppression.')))
    # Site confirmations remain part of website_assessment even after a later
    # correction, including legacy confirmations of hosted business pages.
    for review in reversed(business.get('reviews', [])[:-1]):
        value = review.get('verified_website')
        marker = ('manual', value)
        if review.get('website_status') == 'site_found' and value and marker not in emitted:
            emitted.add(marker)
            signals.append(dict(kind='manual_website_confirmation', value=value,
                observed_at=review.get('evidence_observed_at') if review.get('investigation_question') == 'website' and review.get('investigation_session') else None,
                recorded_at=review.get('created_at'),
                source_url=review.get('evidence_url') if review.get('investigation_question') == 'website' and review.get('investigation_session') else None,
                explanation='A prior manual site confirmation is retained and still contributes to website_present screening after later answers; it has not been reverified.'))
    for kind, value in sorted(matched):
        signals.append(dict(kind='contact_suppression', value=value, suppression_kind=kind,
            explanation='This retained business evidence matches a saved ' + kind + ' suppression key and contributes to suppressed_or_contacted screening.'))
    if business['review'].get('disposition') == 'contacted':
        signals.append(dict(kind='contacted_disposition', value='contacted',
            explanation='The effective saved disposition is contacted and contributes to suppressed_or_contacted screening.'))
    # Full provenance remains in source_groups. The default explanation list
    # needs only one card per signal, even across aliases or repeated imports.
    distinct = {}
    for signal in signals:
        value = core.phone(signal['value']) if signal.get('field') == 'phones' else signal['value']
        distinct.setdefault((signal['kind'], value, signal.get('suppression_kind')), signal)
    return list(distinct.values())


def read(db, business, config):
    """Build from one evaluated business and its pinned database snapshot."""
    groups = source_groups(business['observations'])
    items = inbox.list_items(db, config, businesses=[business])['items']
    return dict(source_groups=groups, change_reasons=items[0]['reasons'] if items else [],
                retained_signals=retained_signals(db, business, groups))
