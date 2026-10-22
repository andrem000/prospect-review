"""Local source adapters. No network access or credentials required."""
import csv
import json
from pathlib import Path


def records(path):
    path = Path(path)
    if path.suffix.lower() == '.csv':
        with path.open(encoding='utf-8-sig', newline='') as f:
            yield from csv.DictReader(f)
    elif path.suffix.lower() in ('.jsonl', '.ndjson', '.geojsonseq'):
        with path.open(encoding='utf-8-sig') as f:
            for line in f:
                if line.strip():
                    yield json.loads(line.lstrip('\x1e'))
    else:
        data = json.loads(path.read_text(encoding='utf-8-sig'))
        if isinstance(data, list):
            yield from data
        elif 'features' in data:
            yield from data['features']
        elif 'elements' in data:
            if data.get('remark'):
                raise ValueError('Overpass response has a remark/error; obtain a complete extract')
            yield from data['elements']
        else:
            raise ValueError('Expected JSON array, FeatureCollection, or Overpass elements')


def many(value):
    if value is None:
        return []
    if isinstance(value, list):
        return [str(x).strip() for x in value if x is not None and str(x).strip()]
    return [x.strip() for x in str(value).split('|') if x.strip()]


def canonical(row):
    result = dict(row)
    result['websites'] = many(row.get('websites') or row.get('website'))
    result['phones'] = many(row.get('phones') or row.get('phone'))
    result['socials'] = many(row.get('socials'))
    result['categories'] = many(row.get('categories') or row.get('trade'))
    return result


def overture(row):
    p = row.get('properties', row)
    a = next(iter(p.get('addresses') or []), {})
    taxonomy = p.get('taxonomy') or p.get('categories') or {}
    categories = [p.get('basic_category'), taxonomy.get('primary')]
    categories += taxonomy.get('hierarchy') or []
    categories += taxonomy.get('alternates') or taxonomy.get('alternate') or []
    brand = p.get('brand') or {}
    return canonical(dict(
        source_id=p.get('id') or row.get('id'), name=(p.get('names') or {}).get('primary'),
        country=a.get('country'), state=a.get('region'), city=a.get('locality'),
        address=a.get('freeform'), postcode=a.get('postcode'),
        categories=[x for x in categories if x], websites=p.get('websites'),
        category_primary=taxonomy.get('primary') or '', category_alternates=taxonomy.get('alternates') or taxonomy.get('alternate') or [],
        basic_category=p.get('basic_category') or '',
        phones=p.get('phones'), socials=p.get('socials'),
        brand=(brand.get('names') or {}).get('primary') or brand.get('wikidata') or '',
        activity=p.get('operating_status') or 'unknown',
        observed_at=max((s.get('update_time') or '' for s in p.get('sources') or []), default=''),
        source_url='https://explore.overturemaps.org/', upstream_sources=p.get('sources') or []))


def osm(row):
    t = row.get('tags') or {}
    activity = 'closed' if any(t.get(k) for k in ('disused:craft', 'abandoned:craft', 'end_date')) else 'unknown'
    return canonical(dict(
        source_id=f"{row['type']}/{row['id']}", name=t.get('name'),
        country=t.get('addr:country'), state=t.get('addr:state'), city=t.get('addr:city'),
        address=' '.join(filter(None, [t.get('addr:housenumber'), t.get('addr:street')])),
        postcode=t.get('addr:postcode'), categories=[t.get('craft') or t.get('shop') or ''],
        websites=many(t.get('website')) + many(t.get('contact:website')) + many(t.get('url')),
        phones=many(t.get('phone')) + many(t.get('contact:phone')),
        socials=many(t.get('contact:facebook')), brand=t.get('brand') or t.get('brand:wikidata') or '',
        activity=activity, observed_at=t.get('check_date') or '',
        source_url=f"https://www.openstreetmap.org/{row['type']}/{row['id']}"))


ADAPTERS = {'canonical': canonical, 'overture': overture, 'osm': osm}
