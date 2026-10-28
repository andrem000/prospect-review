import argparse
import json
import sqlite3
import sys
from contextlib import closing
from pathlib import Path
from . import core
from . import __version__


def main(argv=None):
    p = argparse.ArgumentParser(description='Offline US service-business listing review; no network calls.')
    p.add_argument('--version', action='version', version='Prospect Review ' + __version__)
    p.add_argument('--db', default='prospects.sqlite3')
    sub = p.add_subparsers(dest='command', required=True)
    sub.add_parser('init')
    demo = sub.add_parser('demo', help='Run bundled fictional data with reproducible checks; no network')
    demo.add_argument('--out', default='demo-output', help='A new directory; existing paths are refused')
    app = sub.add_parser('app', help='Open the local browser app; data stays in your workspace')
    app.add_argument('--workspace', default='prospect-review-workspace', help='Local directory for database, source files and exports')
    app.add_argument('--port', type=int, default=0, help='Loopback port; 0 chooses an available port')
    app.add_argument('--no-browser', action='store_true', help='Print the local URL without opening a browser')
    imp = sub.add_parser('import')
    imp.add_argument('file')
    imp.add_argument('--adapter', choices=('canonical', 'overture', 'osm'), default='canonical')
    imp.add_argument('--rights', required=True)
    sup = sub.add_parser('suppress')
    sup.add_argument('file')
    preview = sub.add_parser('reconcile-preview', help='Read-only, hash-pinned dated snapshot plan; never applies')
    preview.add_argument('--manifest', required=True)
    preview.add_argument('--controls', required=True)
    preview.add_argument('--out', required=True)
    apply = sub.add_parser('reconcile-apply', help='Apply approved conflict-free plan only to a fresh DB copy')
    apply.add_argument('--plan', required=True)
    apply.add_argument('--approve-plan', required=True)
    apply.add_argument('--out-db', required=True)
    rev = sub.add_parser('review', argument_default=argparse.SUPPRESS)
    rev.add_argument('id', type=int)
    rev.add_argument('--website-status', choices=core.WEBSITE_STATUSES)
    rev.add_argument('--verified-website')
    rev.add_argument('--candidate-website', help='Unresolved possible website; retained as uncertain, never confirmed automatically')
    rev.add_argument('--activity', choices=('unknown', 'active', 'closed'))
    rev.add_argument('--ownership', choices=('unknown', 'independent', 'chain'))
    rev.add_argument('--residential', choices=('unknown', 'yes', 'no'))
    rev.add_argument('--service-fit', choices=('unknown', 'yes', 'no'), help='Fit for non-residential service profiles; requires manual evidence')
    rev.add_argument('--delivery', choices=('unknown', 'mobile', 'fixed'))
    rev.add_argument('--disposition', choices=('pending', 'rejected', 'contacted'))
    rev.add_argument('--replace', action='store_true', help='Reset omitted decisions to defaults; history and suppression remain')
    rev.add_argument('--evidence-observed-at', help='Evidence observation date, YYYY-MM-DD; no implicit current date')
    rev.add_argument('--evidence-url', required=True)
    rev.add_argument('--notes', required=True)
    for command in ('search', 'export'):
        s = sub.add_parser(command)
        s.add_argument('--config', required=True)
        s.add_argument('--include-excluded', action='store_true')
        s.add_argument('--qualified-only', action='store_true')
        if command == 'export':
            s.add_argument('--out', required=True)
    show = sub.add_parser('show')
    show.add_argument('id', type=int)
    sub.add_parser('stats')
    a = p.parse_args(argv)
    try:
        if a.command == 'app':
            from .app import run
            return run(a.workspace, port=a.port, open_browser=not a.no_browser) or 0
        if a.command == 'demo':
            from .demo import run
            run(a.out)
            return 0
        if a.command == 'export':
            core.validate_export_paths(a.out, protected=(a.db, a.config))
        if a.command in ('reconcile-preview', 'reconcile-apply'):
            from . import reconcile
            if a.command == 'reconcile-preview':
                target = Path(a.out)
                if target.exists() or target.resolve() == Path(a.db).resolve():
                    raise ValueError('Plan output must be a fresh path')
                with closing(reconcile.open_readonly(a.db)) as db:
                    plan = reconcile.preview(db, a.manifest, a.controls)
                target.parent.mkdir(parents=True, exist_ok=True)
                with target.open('x', encoding='utf-8') as file:
                    json.dump(plan, file, indent=2, ensure_ascii=False)
                result = {'mode': 'dry_run', 'plan_id': plan['plan_id'], 'actions_proposed': len(plan['actions']),
                          'conflicts': plan['conflicts'], 'unmatched_controls': len(plan['unmatched_controls']), 'plan_path': str(target)}
            else:
                result = reconcile.apply_copy(a.db, reconcile.read(a.plan), a.out_db, a.approve_plan)
            print(json.dumps(result, indent=2, ensure_ascii=False))
            return 0
        with closing(core.connect(a.db)) as db, db:
            if a.command == 'init':
                result = {'database': a.db, 'ready': True}
            elif a.command == 'import':
                result = core.import_data(db, a.file, a.adapter, a.rights)
            elif a.command == 'suppress':
                result = core.suppress(db, a.file)
            elif a.command == 'review':
                data = {k: v for k, v in vars(a).items() if k not in ('command', 'db', 'id', 'replace')}
                saved = core.review(db, a.id, data, replace=getattr(a, 'replace', False), recording_route='cli')
                result = {'reviewed': a.id, 'review': saved}
            elif a.command in ('search', 'export'):
                config = json.loads(Path(a.config).read_text(encoding='utf-8-sig'))
                rows, metrics = core.search(db, config, a.include_excluded, a.qualified_only)
                if a.command == 'export':
                    result = {**core.export(db, rows, a.out), 'metrics': metrics}
                else:
                    fields = ('id', 'name', 'city', 'state', 'score', 'score_breakdown', 'website_signal', 'verification_status',
                              'qualified', 'excluded_reasons', 'queue_status', 'screening_state', 'review_state',
                              'review_history_count', 'latest_review_recorded_at', 'latest_evidence_observed_at',
                              'review_flags', 'trade_assessment', 'activity_assessment', 'scope_assessment')
                    result = {'metrics': metrics, 'output_scope': metrics['output_scope'],
                              ('records' if a.include_excluded else 'candidates'): [{k: b[k] for k in fields} for b in rows]}
            elif a.command == 'show':
                result = next((b for b in core.all_businesses(db) if b['id'] == a.id), None)
                if result is None:
                    raise ValueError('Unknown business ID')
            else:
                runs = [dict(r) for r in db.execute('SELECT * FROM runs ORDER BY id')]
                for r in runs:
                    r['metrics'] = json.loads(r['metrics'])
                result = {'import_count': sum(r['kind'] == 'import' for r in runs),
                          'search_count': sum(r['kind'] == 'search' for r in runs),
                          'api_requests': 0, 'estimated_cost_usd': 0,
                          'cost_scope': 'This tool only; excludes externally acquired files and operator time', 'runs': runs}
        print(json.dumps(result, indent=2, ensure_ascii=False))
        return 0
    except (ValueError, KeyError, TypeError, OSError, sqlite3.Error) as e:
        print('Error: ' + str(e), file=sys.stderr)
        return 2


if __name__ == '__main__':
    sys.exit(main())
