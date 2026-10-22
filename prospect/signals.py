"""Offline classification rules. Labels are heuristics, never live verification."""
import ipaddress
import re
from datetime import date
from urllib.parse import urlsplit

SOCIAL_HOSTS = {'facebook.com', 'fb.com', 'instagram.com', 'linkedin.com', 'x.com',
                'twitter.com', 'tiktok.com', 'youtube.com', 'pinterest.com'}
CONTACT_HOSTS = {'linktr.ee', 'linktree.com', 'beacons.ai', 'campsite.bio', 'bio.site',
                 'lnk.bio', 'msha.ke', 'solo.to', 'taplink.cc', 'contactin.bio',
                 'wa.me', 'whatsapp.com', 'm.me', 'messenger.com', 't.me', 'telegram.me'}
DIRECTORY_HOSTS = {'yelp.com', 'nextdoor.com', 'bbb.org', 'houzz.com', 'angi.com',
                   'angieslist.com', 'homeadvisor.com', 'manta.com', 'yellowpages.com',
                   'superpages.com', 'localsearch.com', 'thumbtack.com', 'porch.com',
                   'mapquest.com', 'maps.google.com', 'bizapedia.com', 'chamberofcommerce.com'}
SHORTENER_HOSTS = {'bit.ly', 't.co', 'goo.gl', 'tinyurl.com', 'ow.ly', 'tiny.cc',
                   'rb.gy', 'rebrand.ly', 'shorturl.at'}
# Tenant subdomains can be business websites, but the host alone cannot verify
# their contents or identity. Provider roots/directory paths are not tenant sites.
HOSTED_SITE_HOSTS = {'localsearch.com', 'business.site', 'wixsite.com', 'weebly.com',
                     'wordpress.com', 'godaddysites.com', 'squarespace.com',
                     'lovable.app', 'replit.app', 'carrd.co'}
SHARED_PLATFORM_HOSTS = (SOCIAL_HOSTS | CONTACT_HOSTS | DIRECTORY_HOSTS |
                         SHORTENER_HOSTS | HOSTED_SITE_HOSTS | {'google.com', 'bing.com'})
RESERVED_TENANTS = {'www', 'm', 'app', 'search', 'directory', 'login', 'support', 'help'}
NEUTRAL_CATEGORIES = {'services_and_business', 'home_service', 'building_or_construction_service'}
BROAD_CATEGORIES = {'contractor', 'building_contractor', 'carpenter'} | NEUTRAL_CATEGORIES
NAME_PATTERNS = {
    'painting': r'\b(paint(?:ing|er|ers)?|coatings?)\b',
    'landscaping': r'\b(landscap\w*|garden\w*|lawn\w*|yard|irrigation|tree\s+care)\b',
    'fencing_decks': r'\b(fenc\w*|decks?|railing\w*)\b',
    'residential_remodeling': r'\b(remodel\w*|renovat\w*|home\s+improvement|kitchen\w*|bathroom\w*|residential)\b',
    'pressure_washing': r'\b(?:pressure|power|soft)[ -]?(?:wash\w*)\b',
    'mobile_detailing': r'\b(?:auto|car|vehicle|mobile)[ -]?detail\w*\b|\bdetailing\b',
}
NEGATIVE_NAME_PATTERNS = {
    'parking_or_pavement_marking': r'\b(parking\s+(?:lot\s+)?(?:mark\w*|strip\w*)|pavement\s+mark\w*|line\s+striping)\b',
    'locksmith_or_access_control': r'\b(locksmith\w*|lock\s+safe|access\s+control)\b',
}
NEGATIVE_CATEGORIES = {'locksmith', 'parking_lot', 'pavement_marking', 'parking_lot_striping',
                       'paint_store', 'hardware_store', 'home_improvement_store', 'fencing_club'}


def host_matches(host, hosts):
    return any(host == h or host.endswith('.' + h) for h in hosts)


def classify_link(value):
    """A well-formed custom domain is only a standalone-site signal, not proof of a live site."""
    value = str(value or '').strip()
    if not value:
        return {'url': value, 'kind': 'empty', 'reason': 'blank_value', 'host': ''}
    if value.lower().startswith(('mailto:', 'tel:')):
        return {'url': value, 'kind': 'contact_aggregator', 'reason': 'contact_uri', 'host': ''}
    try:
        u = urlsplit(value if '://' in value else 'https://' + value)
        host = (u.hostname or '').lower().removeprefix('www.')
        valid = (u.scheme in ('http', 'https') and not u.username and not u.password
                 and '.' in host and not re.search(r'\s', value))
        if not valid or not all(re.fullmatch(r'[a-z0-9](?:[a-z0-9-]*[a-z0-9])?', x) for x in host.encode('idna').decode().split('.')):
            raise ValueError('invalid URL/domain')
        # Reject malformed/out-of-range ports without touching the network.
        _ = u.port
        try:
            ipaddress.ip_address(host)
        except ValueError:
            pass
        else:
            return {'url': value, 'kind': 'uncertain_domain', 'reason': 'ip_address_requires_review', 'host': host}
    except (ValueError, UnicodeError):
        return {'url': value, 'kind': 'uncertain_domain', 'reason': 'malformed_or_non_web_link', 'host': ''}
    if any(host.endswith('.' + root) and host[:-len(root)-1] not in RESERVED_TENANTS
           for root in HOSTED_SITE_HOSTS):
        return {'url': value, 'kind': 'uncertain_domain',
                'reason': 'hosted_business_page_requires_review', 'host': host}
    for hosts, kind, reason in (
        (SOCIAL_HOSTS, 'social', 'known_social_host'),
        (CONTACT_HOSTS, 'contact_aggregator', 'known_contact_or_link_aggregator'),
        (DIRECTORY_HOSTS, 'directory', 'known_directory_host'),
        (SHORTENER_HOSTS, 'uncertain_domain', 'shortener_destination_unknown'),
    ):
        if host_matches(host, hosts):
            return {'url': value, 'kind': kind, 'reason': reason, 'host': host}
    if host in ('google.com', 'bing.com') and u.path.startswith('/maps'):
        return {'url': value, 'kind': 'directory', 'reason': 'map_listing_link', 'host': host}
    if host_matches(host, HOSTED_SITE_HOSTS):
        return {'url': value, 'kind': 'uncertain_domain', 'reason': 'provider_root_or_reserved_page', 'host': host}
    return {'url': value, 'kind': 'standalone_website', 'reason': 'custom_domain_signal_unverified', 'host': host}


def confirmable_site(value):
    link = classify_link(value)
    return (link['kind'] == 'standalone_website' or
            link['reason'] == 'hosted_business_page_requires_review')


def website_assessment(b):
    review = b.get('review', {})
    links = []
    for field in ('websites', 'socials'):
        for value in b.get(field, []):
            link = classify_link(value)
            if link['kind'] != 'empty':
                links.append({**link, 'origin': field})
    history = b.get('reviews') or [review]
    confirmed = [r for r in history if r.get('website_status') == 'site_found' and r.get('verified_website')]
    for r in confirmed:
        link = classify_link(r['verified_website'])
        if link['reason'] == 'hosted_business_page_requires_review':
            link = {**link, 'kind': 'provider_hosted_website', 'reason': 'manual_hosted_site_confirmation'}
        links.append({**link, 'origin': 'manual_site_found'})
    # A manually recorded possible domain is not a confirmed standalone website.
    if review.get('candidate_website'):
        links.append({**classify_link(review['candidate_website']), 'kind': 'uncertain_domain',
                      'reason': 'manual_candidate_domain_unresolved', 'origin': 'manual_candidate'})
    kinds = {x['kind'] for x in links}
    if 'standalone_website' in kinds:
        signal = 'standalone_website'
    elif 'provider_hosted_website' in kinds:
        signal = 'provider_hosted_website'
    elif confirmed:
        signal = 'standalone_website'  # Preserve pre-existing manual confirmations.
    elif 'uncertain_domain' in kinds:
        signal = 'uncertain_domain'
    elif kinds & {'social', 'contact_aggregator'} and 'directory' in kinds:
        signal = 'social_and_directory'
    elif kinds & {'social', 'contact_aggregator'}:
        signal = 'social_only'
    elif 'directory' in kinds:
        signal = 'directory_only'
    else:
        signal = 'no_website_listed'
    points = {'standalone_website': 0, 'provider_hosted_website': 0, 'uncertain_domain': 4, 'directory_only': 18,
              'social_only': 24, 'social_and_directory': 22, 'no_website_listed': 14}[signal]
    basis = 'listing_signal_only'
    if confirmed:
        basis = 'recorded_manual_site_confirmation_not_reverified'
    if review.get('website_status') == 'uncertain' and signal not in ('standalone_website', 'provider_hosted_website'):
        points = min(points, 8)
        basis = 'manual_uncertainty_unresolved'
    elif review.get('website_status') == 'no_site_found' and signal not in ('standalone_website', 'provider_hosted_website', 'uncertain_domain'):
        points = 35
        basis = 'dated_manual_no_site_found_not_proof_of_absence'
    return {'classification': signal, 'links': links, 'points': points, 'maximum': 35,
            'basis': basis, 'absence_verified': False}


def trade_assessment(b, config):
    cats = {str(c).lower() for c in b.get('categories', []) if c}
    name = b.get('name', '').lower()
    matches = {trade: sorted(cats & {a.lower() for a in aliases}) for trade, aliases in config['trades'].items()}
    matches = {trade: values for trade, values in matches.items() if values}
    broad = BROAD_CATEGORIES | set(config.get('broad_categories', []))
    direct = {trade: [c for c in values if c not in broad] for trade, values in matches.items()}
    direct = {trade: values for trade, values in direct.items() if values}
    hints = []
    for trade, aliases in config['trades'].items():
        if trade == 'name_screened_contractor':
            continue
        pattern = NAME_PATTERNS.get(trade)
        if pattern is None:
            terms = [a.replace('_', ' ') for a in aliases if a not in BROAD_CATEGORIES]
            pattern = r'\b(?:' + '|'.join(re.escape(a) for a in terms) + r')\b' if terms else r'(?!)'
        if re.search(pattern, name):
            hints.append(trade)
    negatives = [key for key, pattern in NEGATIVE_NAME_PATTERNS.items() if re.search(pattern, name)]
    negatives += sorted(cats & NEGATIVE_CATEGORIES)
    negatives += sorted(cats & set(config.get('excluded_categories', [])))
    missing = not (cats - NEUTRAL_CATEGORIES)
    unmapped = bool(cats - NEUTRAL_CATEGORIES) and not matches and not (cats & NEGATIVE_CATEGORIES)
    if negatives and not hints:
        status, points, reason = 'non_target', 0, 'explicit_unrelated_service_signal'
    elif negatives:
        status, points, reason = 'uncertain', 6, 'conflicting_target_and_unrelated_signals'
    elif direct and hints and not (set(direct) & set(hints)):
        status, points, reason = 'uncertain', 16, 'category_and_name_trade_disagree'
    elif direct:
        status = 'target'
        points = 25 if set(direct) & set(hints) else 18
        reason = ('specific_category_and_name' if points == 25 else
                  'specific_category_only_residential_scope_unverified' if config.get('service_scope', 'residential') == 'residential'
                  else 'specific_category_only_service_scope_unverified')
    elif matches or hints:
        status, points, reason = 'uncertain', (12 if matches else 8), 'broad_or_name_only_match_requires_review'
    else:
        status, points, reason = 'unknown' if missing or unmapped else 'non_target', 0, 'no_target_mapping'
    return {'status': status, 'points': points, 'maximum': 25, 'reason': reason,
            'matched_categories': matches, 'specific_matches': direct, 'name_hints': hints,
            'negative_signals': negatives, 'missing_category': missing, 'unknown_category': unmapped,
            'residential_verified': b.get('review', {}).get('residential') == 'yes'}


def scope_assessment(b, config):
    """Service/delivery fit is independent of website, activity and ownership."""
    r = b.get('review', {})
    scope = config.get('service_scope', 'residential')
    delivery = config.get('delivery_scope', 'any')
    fit = r.get('residential') if scope == 'residential' else r.get('service_fit')
    delivery_fit = r.get('delivery')
    return {'scope': scope, 'fit_verified': fit == 'yes', 'explicitly_outside_scope': fit == 'no',
            'delivery_scope': delivery, 'delivery_verified': delivery == 'any' or delivery_fit == delivery,
            'explicitly_outside_delivery': delivery != 'any' and delivery_fit in ('mobile', 'fixed') and delivery_fit != delivery,
            'mobile_name_hint': bool(re.search(r'\bmobile\b', b.get('name', ''), re.I)),
            'basis': 'Explicit manual service/delivery findings required; name/category hints are not verification.'}


def identity_assessment(b, normalize_phone):
    phones = {normalize_phone(p) for p in b.get('phones', []) if normalize_phone(p)}
    name_tokens = re.findall(r'[a-z0-9]+', b.get('name', '').lower())
    usable_name = any(len(t) > 2 and t not in {'llc', 'inc', 'company', 'business', 'unknown', 'test'} for t in name_tokens)
    profiles = {x['url'] for x in website_assessment(b)['links'] if x['kind'] in ('social', 'contact_aggregator')}
    providers = set()
    for o in b.get('observations', []):
        for s in o.get('upstream_sources') or []:
            # Computed Overture confidence/status entries are not independent identity sources.
            if s.get('record_id') and s.get('dataset', '').lower() not in ('overture', 'overture-signals') and s.get('property', '') in ('', '/properties/names', '/properties/phones', '/properties/addresses'):
                providers.add(s['dataset'].lower())
    components = {'usable_name': 6 if usable_name else 0, 'us_phone': 10 if phones else 0,
                  'social_contact_profile': 4 if profiles else 0, 'street_address': 4 if b.get('address') else 0,
                  'identity_source_coverage': min(6, 2 * len(providers))}
    return {'points': sum(components.values()), 'maximum': 30, 'components': components,
            'upstream_identity_source_labels': sorted(providers), 'phone_reachability_verified': False}


def activity_assessment(b, today):
    r = b.get('review', {})
    if r.get('activity') == 'active' and r.get('evidence_url') and r.get('notes'):
        try:
            age = (today - date.fromisoformat(r.get('created_at', '')[:10])).days
        except ValueError:
            age = None
        if age is not None and 0 <= age <= 180:
            points, basis = 10, 'manual_active_with_evidence'
        else:
            points, basis = 4, 'manual_active_stale_or_undated'
    elif r.get('activity') == 'closed':
        points, basis = 0, 'manual_closed'
    elif b.get('activity') in ('open', 'active', 'operational'):
        points, basis = 2, 'provider_operating_status_only_unverified'
    else:
        points, basis = 0, 'no_positive_activity_evidence'
    return {'points': points, 'maximum': 10, 'basis': basis,
            'record_update_points': 0, 'current_operation_verified': basis == 'manual_active_with_evidence'}
