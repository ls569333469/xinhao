"""Loopback-only dashboard and scan service with explicit static allowlists."""
import json
import gzip
import mimetypes
import re
import sqlite3
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlsplit

from research_experience import ExperienceConflict
from task_service import Conflict, TaskService
from ai_service import AiService, Conflict as AiConflict
from ai_research import AiResearch
from price_index import list_snapshot_revisions, merge_series, summarize_price_index
from coverage_gate import catalog as coverage_catalog, evaluate as coverage_evaluate, evaluate_scan_fields as coverage_scan, build_field_gaps as coverage_gaps
from strategy_store import StrategyStore
from rule_bindings import RuleBindings, BindingConflict
from frozen_exec import FrozenExecConflict
from rule_export_gate import resolve_export_fingerprint, stamp_export_payload
from rule_coverage_gate import assert_deps_covered, dep_field_map
from sim_store import SimStore, SimConflict
from eval_store import EvalStore, EvalConflict
from review_store import ReviewStore, ReviewConflict
from struct_indicators import catalog as struct_catalog, compute_for_symbol as struct_compute, list_struct_ready
from backtest_store import BacktestStore, BacktestConflict
from config_store import ConfigStore, ConfigConflict
from ext_store import ExtStore, ExtConflict

from runtime_paths import CODE_ROOT, data_root, HostLease
ROOT = data_root()
import hashlib,os,datetime
LOADED_AT=datetime.datetime.now(datetime.timezone.utc).isoformat()
LOADED_SOURCE_FILES=('server.py','task_service.py','scanner.py','b_observe_runner.py','b_process_order.py','b_public_adapter.py','b_evidence.py','b_accumulation_rules.py','b_observe_evaluate.py','b_accumulation_store.py','b_1h_fetch.py','b_1h_ingest.py','b_observe_universe.py','b_observe_api.py','observe_config.py','market_candle_cache.py','shared_candles.py','snapshot_store.py','cache_retention.py','durable_io.py','strategy-registry.json','unified_workbench.py','ai_research.py','unified-signal-registry.json','design/unified-core.js','design/unified-combos.js','design/unified-workbench.js','design/unified-workbench.css','design/unified-workbench-fragment.html','design/unified-strategies.html','design/unified-guide.js','design/prototype.html','design/ai-runtime.js','design/tasks-runtime.js')
LOADED_SOURCE_FILES += ('design/signal-catalog.js', 'design/workbench-integrity.js', 'design/workbench-integrity.css', 'design/pro-table-runtime.js', 'design/reports-runtime.js', 'design/strategy-runtime.js', 'design/coverage-runtime.js', 'design/ext-runtime.js', 'design/config-runtime.js')
LOADED_SOURCE_FILES += ('design/report-format.js','design/tracking-workbench.js','design/tracking-workbench.css','design/tracking-workbench.html')
LOADED_SOURCE_FILES += ('scan_verification.py','request_pacer.py')
LOADED_SOURCE_FILES += ('source_resume.py','scan_demands.py','b_event_sqlite.py','migrate_b_events.py','export_b_events.py','request_telemetry.py')
LOADED_SOURCE_FILES += ('b_repair_store.py','b_evidence_repair.py','b_cap_history.py')
LOADED_SOURCE_FILES += ('tracking_service.py',) + tuple('tracking/'+p.relative_to(CODE_ROOT/'tracking').as_posix() for p in sorted((CODE_ROOT/'tracking').rglob('*')) if p.is_file() and p.suffix in ('.py','.json','.html','.js','.css'))
LOADED_SOURCE_FILES += ('countdown_schedule.py','runtime_paths.py','collection_policy.py','collection_recovery.py','collection_status.py','request_gate.py','case_tool.py','asset-mappings.json')
LOADED_FINGERPRINT=hashlib.sha256(b''.join((CODE_ROOT/n).read_bytes() for n in LOADED_SOURCE_FILES)).hexdigest()
from tracking.release_identity import capture as capture_tracking_release
LOADED_TRACKING_RELEASE=capture_tracking_release(ROOT,source_root=CODE_ROOT)
DOC_PATHS = ('/DOCS/独立审计报告.md', '/DOCS/ai-review.md', '/DOCS/chain-flow-report.md', '/DOCS/data-capability-audit.md', '/DOCS/implementation-plan.md', '/DOCS/migration-report.md', '/DOCS/README.md', '/DOCS/report.md', '/DOCS/strategy-test-report.md', '/DOCS/design/design-code-review.md', '/DOCS/design/iteration-v2.1.md', '/DOCS/design/iteration-v2.2.md', '/DOCS/design/iteration-v2.3.md', '/DOCS/design/iteration-v2.4.md', '/DOCS/design/iteration-v2.md', '/DOCS/design/README.md', '/DOCS/design/requirements.md', '/DOCS/INDEX.md', '/DOCS/整改清单.md', '/DOCS/design/iteration-v2.5.md', '/DOCS/design/iteration-v2.6.md', '/DOCS/design/iteration-v2.7.md', '/DOCS/design/iteration-v2.8-ai01.md', '/DOCS/design/iteration-v2.8-data01.md', '/DOCS/design/iteration-v2.8-data01-coverage.md', '/DOCS/design/iteration-v2.8-rule01.md', '/DOCS/design/iteration-v2.8-rule01-bindings.md', '/DOCS/design/iteration-v2.8-rule01-coverage-gate.md', '/DOCS/design/iteration-v2.8-rule01-export-fingerprint.md', '/DOCS/design/iteration-v2.8-rule01-export-fingerprint-deepen.md', '/DOCS/design/iteration-v2.8-sim01.md', '/DOCS/design/iteration-v2.8-sim01-autofill.md', '/DOCS/design/iteration-v2.8-eval01.md', '/DOCS/design/iteration-v2.8-eval01-schedule.md', '/DOCS/design/iteration-v2.8-review01.md', '/DOCS/design/iteration-v2.8-review01-schedule.md', '/DOCS/design/iteration-v2.8-struct01.md', '/DOCS/design/iteration-v2.8-backtest01.md', '/DOCS/design/iteration-v2.8-config01.md', '/DOCS/GROK实施交接.md')
DOC_PATHS += ('/DOCS/design/strategies/v3.2-Codex接手交付.md', '/DOCS/design/iteration-scan-reserves-refresh.md', '/DOCS/维护与清理说明.md', '/DOCS/design/strategies/01-EMA三线金叉与合约Squeeze.md')
DOC_PATHS += ('/DOCS/design/iteration-v3.3-r5-delivery.md', '/DOCS/design/strategies/03-统一信号工作台-最终优化方案-v3.3-r5.md')
DOC_PATHS += ('/DOCS/design/v3.3-r5-全站审查报告.md',)
PROTOTYPE_FILES = {'workbench-integrity.js', 'workbench-integrity.css', 'signal-catalog.js'} | {'prototype.html', 'strategy-registry.js', 'market-evidence.js', 'chain-history.js',
                   'reserves-runtime.js', 'prompt-templates.json', 'chain-history.json', 'reserves-data.json', 'tasks-runtime.js', 'ranges-runtime.js', 'ai-runtime.js', 'reports-runtime.js', 'strategy-runtime.js', 'rule-market-runtime.js', 'sim-runtime.js', 'eval-runtime.js', 'review-runtime.js', 'struct-runtime.js', 'backtest-runtime.js', 'config-runtime.js', 'coverage-runtime.js', 'ext-runtime.js', 'evidence-runtime.js', 'pro-table-runtime.js', 'a-shell.css', 'experience-runtime.js'}


def handler_factory(root, service, prototype=False, allowed_hosts=None, ai=None, research=None, strategies=None, sim=None, eval_store=None, review_store=None, backtest_store=None, config_store=None, ext_store=None, rule_bindings=None):
    root = Path(root)
    code_root=CODE_ROOT
    hosts = set(allowed_hosts or ('127.0.0.1:8767', '127.0.0.1:8768', 'localhost:8767', 'localhost:8768'))
    if ai is None:
        ai = AiService(root)
    if research is None:
        research = AiResearch(root, ai)
    if strategies is None:
        strategies = StrategyStore(root)
    strategies.research = research
    if rule_bindings is None:
        rule_bindings = RuleBindings(root, strategies)
    if sim is None:
        sim = SimStore(root)
    if eval_store is None:
        eval_store = EvalStore(root, strategy_store=strategies, sim_store=sim)
    if review_store is None:
        review_store = ReviewStore(root, eval_store=eval_store, sim_store=sim, ai_service=ai)
    if backtest_store is None:
        backtest_store = BacktestStore(root, strategy_store=strategies, sim_store=sim)
    if config_store is None:
        config_store = ConfigStore(root)
    if ext_store is None:
        ext_store = ExtStore(root)
    config_store.bind_stores(
        strategy_store=strategies,
        sim_store=sim,
        eval_store=eval_store,
        review_store=review_store,
    )

    class Handler(BaseHTTPRequestHandler):
        def send_body(self, status, value, content_type='application/json; charset=utf-8', *, attachment=False):
            body = json.dumps(value, ensure_ascii=False, allow_nan=False).encode('utf-8') if isinstance(value, (dict, list)) else value
            large_json = content_type.startswith('application/json') and len(body) >= 131072
            gzip_ok = False
            for part in self.headers.get('Accept-Encoding','').split(','):
                coding, *params = part.strip().split(';')
                if coding.lower() == 'gzip':
                    try:
                        gzip_ok = all(float(p.strip()[2:]) > 0 for p in params if p.strip().startswith('q='))
                    except ValueError:
                        gzip_ok = False
            compressed = large_json and gzip_ok
            if compressed:
                body = gzip.compress(body, compresslevel=3, mtime=0)
            self.send_response(status)
            self.send_header('Content-Type', content_type)
            if attachment:
                suffix='csv' if content_type.startswith('text/csv') else 'html' if content_type.startswith('text/html') else 'json'
                self.send_header('Content-Disposition', 'attachment; filename="tracking.'+suffix+'"')
            if large_json:
                self.send_header('Vary', 'Accept-Encoding')
            if compressed:
                self.send_header('Content-Encoding', 'gzip')
            self.send_header('Content-Length', str(len(body)))
            self.send_header('Cache-Control', 'no-store')
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.send_header('Referrer-Policy', 'no-referrer')
            self.end_headers()
            if self.command != 'HEAD':
                self.wfile.write(body)

        def fail(self, status, message):
            self.send_body(status, {'error': message})

        def valid_host(self):
            if self.headers.get('Host') not in hosts:
                self.fail(403, '只接受本机工作台地址')
                return False
            return True

        def do_HEAD(self):
            self.do_GET()

        def do_GET(self):
            if not self.valid_host():
                return
            path = unquote(urlsplit(self.path).path)
            try:
                if path in ('/tracking','/tracking/','/tracking/index.html','/tracking/app.js','/tracking/styles.css'):
                    filename='app.js' if path.endswith('app.js') else 'styles.css' if path.endswith('styles.css') else 'index.html'
                    target=code_root/'tracking'/'ui'/filename
                    return self.send_body(200,target.read_bytes(),('text/javascript' if filename=='app.js' else 'text/css' if filename=='styles.css' else 'text/html')+'; charset=utf-8')
                if path.startswith('/api/tracking/'):
                    from tracking_service import read_route
                    code,data,ctype=read_route(root,'GET',self.path)
                    return self.send_body(code,data,ctype,attachment=code==200 and path=='/api/tracking/export')
                if path=='/api/collection/status':
                    from collection_status import status
                    return self.send_body(200,status(root))
                if path in ('/tracking-workbench.js','/tracking-workbench.css','/tracking-workbench.html','/unified-core.js','/unified-combos.js','/unified-workbench.js','/unified-workbench.css','/unified-workbench-fragment.html','/unified-strategies.html','/unified-guide.js','/report-format.js','/signal-catalog.js','/workbench-integrity.js','/workbench-integrity.css'):
                    target=code_root/'design'/path.lstrip('/')
                    return self.send_body(200,target.read_bytes(),(mimetypes.guess_type(target.name)[0] or 'text/plain')+'; charset=utf-8')
                if path == '/api/unified/signals':
                    from unified_workbench import load_payload
                    qs=parse_qs(urlsplit(self.path).query)
                    return self.send_body(200,load_payload(root,context=(qs.get('context') or ['latest'])[0],projection=(qs.get('projection') or ['full'])[0],market=(qs.get('market') or [None])[0],symbol=(qs.get('symbol') or [None])[0]))
                if path in ('/','/index.html') and not prototype:
                    self.send_response(302);self.send_header('Location','http://127.0.0.1:8768/'+('?' + urlsplit(self.path).query if urlsplit(self.path).query else '')+('#'+urlsplit(self.path).fragment if urlsplit(self.path).fragment else ''));self.end_headers();return
                if path == '/b-observe.html':
                    qs=urlsplit(self.path).query
                    self.send_response(302);self.send_header('Location','/?combo=b_confirmed_v32'+('&'+qs if qs else ''));self.end_headers();return
                if path == '/api/health':
                    settings = service.settings()
                    settings = dict(settings)
                    caps = dict(settings.get('capabilities') or {})
                    caps['ai'] = True
                    caps['settingsScope'] = list(caps.get('settingsScope') or ['scan']) + ['ai']
                    caps['strategies'] = True
                    caps['sim'] = True
                    caps['eval'] = True
                    caps['review'] = True
                    caps['struct'] = True
                    caps['backtest'] = True
                    caps['config'] = True
                    caps['ext'] = True
                    caps['coverage'] = True
                    settings['capabilities'] = caps
                    self.send_body(200, {'service': 'anomaly-workbench', 'version': '2.8',
                                         'emaImplementation': 'v32-codex-20261016-r2', 'workbenchImplementation':'v3.3-r5', 'workbenchRepair':'1001-1002-r1', 'scanCoordination':'one-click-4h-1h-durable-r2', 'loadedSourceFingerprint': LOADED_FINGERPRINT, 'loadedSourceFiles':LOADED_SOURCE_FILES, 'loadedAt':LOADED_AT, 'processId':os.getpid(),
                                         'settings': settings, 'ai': ai.public_config(),
                                         'strategies': {'registry': strategies.registry_ref(), 'bindings': 'market/reports/basket/experiment/validation 同 ID/version/hash', 'coverage_hard_gate': True, 'export_fingerprint': True, 'export_fingerprint_v2': True, 'strategy_export_import': True, 'market_filter_ui': True},
                                         'sim': {'same_symbol_policy': 'reject', 'autofill': True, 'tier_budget_hard_gate': True, 'disclaimer': '研究/模拟，非实盘；确认信号可预览/填仓；T1/T2/T3 保证金预算硬闸'},
                                         'eval': {
                                             'ambiguity': 'worse_for_trader/gap_open/data_gap',
                                             'server_effect': (eval_store.status() or {}).get('server_effect'),
                                             'disclaimer': '研究/模拟前向观察，非实盘',
                                         },
                                         'review': {
                                             'pause_new_entries': review_store.get_pause_new_entries(),
                                             'server_effect': (review_store.status() or {}).get('server_effect'),
                                             'disclaimer': '研究/模拟复盘，数值不调AI',
                                         },
                                         'struct': {'params_version': 'struct-params-v1', 'ath_policy': '区间高点（无全历史核验源）', 'backtest_filter_gate': True, 'disclaimer': '研究用结构指标；预热不足不给伪数；可挂接回测过滤闸'},
                                         'backtest': {'slice': 'BACKTEST-01-compare-knife', 'fair_compare': True, 'struct_filter_gate': True, 'disclaimer': '本地K线回测+对照；STRUCT过滤闸；非宇宙重建；无市值回填'},
                                         'config': {'slice': 'CONFIG-01-settings-bind', 'disclaimer': '设置页双向绑定；观察池/提示词/扫描/SIM预算/计划开关；备份向导；无凭证回显'},
                                         'ext': {'slice': 'EXT-01-precise-flow-deepen', 'disclaimer': '⑤采用聪明钱流入；旧交易所净流槽位仅兼容历史；USDC/COIN-M引擎未就绪'},
                                         'coverage': {'slice': 'DATA-01-coverage-deepen', 'disclaimer': '多字段覆盖+缺口补查提示+指纹；不宣称OI/市值历史齐全；不重建宇宙'},
                                         'reserveRefresh': {'version': 1, 'scan_snapshot_bound': True, 'exact_netflow': False},
                                         'tracking':dict(__import__('tracking_service').status(root),loaded_release=LOADED_TRACKING_RELEASE),'scheduler_error': getattr(service, 'scheduler_error', None)})
                    return
                if path == '/api/settings':
                    self.send_body(200, service.settings())
                    return
                if path == '/api/ai/config':
                    self.send_body(200, ai.public_config())
                    return
                if path == '/api/research/capability':
                    self.send_body(200, research.retrieval_capability())
                    return
                if path == '/api/research/prompts':
                    self.send_body(200, {'catalog': research._templates(), 'meta': config_store.get().get('meta')})
                    return
                if path == '/api/research/experiences':
                    self.send_body(200, research.experiences.listing())
                    return
                if re.fullmatch(r'/api/research/experiences/exp-[a-f0-9]{32}', path):
                    try:
                        self.send_body(200, research.experiences.get(path.rsplit('/', 1)[-1]))
                    except KeyError:
                        self.fail(404, '经验不存在')
                    return
                if path == '/api/ai/models':
                    self.send_body(200, ai.list_models())
                    return
                if path == '/api/reports':
                    self.send_body(200, {'reports': research.history()})
                    return
                if path == '/api/ai/calls':
                    self.send_body(200, {'calls': research.call_history()})
                    return
                if re.fullmatch(r'/api/reports/batches/batch-[a-f0-9]{32}', path):
                    try:
                        self.send_body(200, research.get_batch(path.rsplit('/',1)[-1]))
                    except KeyError:
                        self.fail(404, '批次不存在')
                    return
                if path.startswith('/api/reports/report-'):
                    try:
                        self.send_body(200, research.get_report(path.rsplit('/',1)[-1]))
                    except KeyError:
                        self.fail(404, '报告不存在')
                    return
                if path == '/api/candles/history':
                    self.send_body(200, {'ranges': service.ranges.history()})
                    return

                if path == '/api/coverage/catalog':
                    self.send_body(200, coverage_catalog())
                    return
                if path == '/api/coverage':
                    qs = parse_qs(urlsplit(self.path).query)
                    try:
                        candle_limit = int((qs.get('candle_limit') or ['40'])[0])
                    except ValueError:
                        self.fail(400, 'candle_limit 须为整数')
                        return
                    revision = (qs.get('revision') or [None])[0]
                    try:
                        self.send_body(200, coverage_evaluate(
                            root, revision=revision, candle_limit=candle_limit,
                        ))
                    except FileNotFoundError as exc:
                        self.fail(404, str(exc)[:300])
                    except ValueError as exc:
                        self.fail(400, str(exc)[:300])
                    return
                if path == '/api/coverage/scan':
                    qs = parse_qs(urlsplit(self.path).query)
                    revision = (qs.get('revision') or [None])[0]
                    try:
                        self.send_body(200, coverage_scan(root, revision=revision))
                    except FileNotFoundError as exc:
                        self.fail(404, str(exc)[:300])
                    return
                if path == '/api/coverage/gaps':
                    qs = parse_qs(urlsplit(self.path).query)
                    revision = (qs.get('revision') or [None])[0]
                    try:
                        self.send_body(200, coverage_gaps(root, revision=revision))
                    except FileNotFoundError as exc:
                        self.fail(404, str(exc)[:300])
                    except ValueError as exc:
                        self.fail(400, str(exc)[:300])
                    return

                if path == '/api/price-index':
                    qs = parse_qs(urlsplit(self.path).query)
                    interval = (qs.get('interval') or [None])[0]
                    try:
                        limit = int((qs.get('limit') or ['300'])[0])
                    except ValueError:
                        self.fail(400, 'limit须为整数')
                        return
                    limit = max(1, min(limit, 2000))
                    try:
                        self.send_body(200, summarize_price_index(root, interval=interval, limit=limit))
                    except ValueError as exc:
                        self.fail(400, str(exc)[:300])
                    return
                if path == '/api/price-index/series':
                    qs = parse_qs(urlsplit(self.path).query)
                    market = (qs.get('market') or [None])[0]
                    symbol = (qs.get('symbol') or [None])[0]
                    interval = (qs.get('interval') or [None])[0]
                    start = (qs.get('start') or [None])[0]
                    end = (qs.get('end') or [None])[0]
                    if not market or not symbol or not interval:
                        self.fail(400, '须提供market、symbol、interval')
                        return
                    try:
                        self.send_body(200, merge_series(root, market, symbol, interval, start=start, end=end))
                    except ValueError as exc:
                        self.fail(400, str(exc)[:300])
                    return
                if path == '/api/snapshots':
                    qs = parse_qs(urlsplit(self.path).query)
                    try:
                        limit = int((qs.get('limit') or ['100'])[0])
                    except ValueError:
                        self.fail(400, 'limit须为整数')
                        return
                    limit = max(1, min(limit, 500))
                    self.send_body(200, list_snapshot_revisions(root, limit=limit))
                    return



                if path == '/api/strategies/coverage-gate/map':
                    self.send_body(200, dep_field_map())
                    return
                if path == '/api/strategies/coverage-gate':
                    qs = parse_qs(urlsplit(self.path).query)
                    version_id = (qs.get('version_id') or [None])[0]
                    deps_raw = (qs.get('deps') or [None])[0]
                    deps = None
                    if version_id:
                        try:
                            ver = strategies.get_version(version_id)
                        except KeyError:
                            self.fail(404, '冻结版本不存在')
                            return
                        deps = ver.get('data_dependencies') or []
                    elif deps_raw:
                        deps = [x.strip() for x in deps_raw.split(',') if x.strip()]
                    else:
                        deps = []
                    self.send_body(200, assert_deps_covered(root, deps))
                    return
                if path == '/api/strategies/bindings/market/filter':
                    self.send_body(200, rule_bindings.market_filter())
                    return

                if path == '/api/strategies/bindings/export-fingerprint':
                    self.send_body(200, resolve_export_fingerprint(
                        rule_bindings, require_bound=False, root=root,
                    ))
                    return

                if path == '/api/strategies/bindings':
                    self.send_body(200, rule_bindings.list_bindings())
                    return
                if path == '/api/strategies/bindings/catalog':
                    self.send_body(200, rule_bindings.catalog())
                    return
                if re.fullmatch(r'/api/strategies/ref/ver-[a-f0-9]{12}', path):
                    try:
                        self.send_body(200, rule_bindings.canonical_ref(path.rsplit('/', 1)[-1]))
                    except KeyError:
                        self.fail(404, '冻结版本不存在')
                    return

                if path == '/api/strategies':
                    self.send_body(200, strategies.list_all())
                    return
                if path == '/api/strategies/registry':
                    self.send_body(200, strategies.registry_ref())
                    return
                if re.fullmatch(r'/api/strategies/drafts/draft-[a-f0-9]{12}', path):
                    try:
                        self.send_body(200, strategies.get_draft(path.rsplit('/', 1)[-1]))
                    except KeyError:
                        self.fail(404, '草案不存在')
                    return
                if re.fullmatch(r'/api/strategies/versions/ver-[a-f0-9]{12}', path):
                    try:
                        self.send_body(200, strategies.get_version(path.rsplit('/', 1)[-1]))
                    except KeyError:
                        self.fail(404, '冻结版本不存在')
                    return
                if path == '/api/sim/accounts':
                    self.send_body(200, sim.list_accounts())
                    return
                if path == '/api/sim/config':
                    self.send_body(200, sim.get_config())
                    return
                if path == '/api/sim/events':
                    qs = parse_qs(urlsplit(self.path).query)
                    try:
                        limit = int((qs.get('limit') or ['50'])[0])
                    except ValueError:
                        self.fail(400, 'limit须为整数')
                        return
                    account_id = (qs.get('account_id') or [None])[0]
                    self.send_body(200, sim.list_events(limit=limit, account_id=account_id))
                    return
                if re.fullmatch(r'/api/sim/accounts/acc-[A-Za-z0-9_-]+', path):
                    try:
                        self.send_body(200, sim.get_account(path.rsplit('/', 1)[-1]))
                    except KeyError:
                        self.fail(404, '账户不存在')
                    return

                if path == '/api/eval/rules':
                    self.send_body(200, eval_store.rules())
                    return
                if path == '/api/eval/observations':
                    qs = parse_qs(urlsplit(self.path).query)
                    status = (qs.get('status') or [None])[0]
                    try:
                        limit = int((qs.get('limit') or ['100'])[0])
                    except ValueError:
                        self.fail(400, 'limit须为整数')
                        return
                    self.send_body(200, eval_store.list_observations(status=status, limit=limit))
                    return
                if path == '/api/eval/settlements':
                    qs = parse_qs(urlsplit(self.path).query)
                    try:
                        limit = int((qs.get('limit') or ['100'])[0])
                    except ValueError:
                        self.fail(400, 'limit须为整数')
                        return
                    self.send_body(200, eval_store.list_settlements(limit=limit))
                    return
                if path == '/api/eval/grades':
                    qs = parse_qs(urlsplit(self.path).query)
                    svid = (qs.get('strategy_version_id') or [None])[0]
                    self.send_body(200, eval_store.grades(strategy_version_id=svid))
                    return
                if path == '/api/eval/status':
                    self.send_body(200, eval_store.status())
                    return
                if path == '/api/eval/plans':
                    self.send_body(200, eval_store.list_plans())
                    return
                if path == '/api/eval/backfill-links':
                    qs = parse_qs(urlsplit(self.path).query)
                    try:
                        limit = int((qs.get('limit') or ['50'])[0])
                    except ValueError:
                        limit = 50
                    self.send_body(200, eval_store.list_backfill_links(limit=limit))
                    return
                if re.fullmatch(r'/api/eval/observations/obs-[A-Za-z0-9_-]+', path):
                    try:
                        self.send_body(200, eval_store.get_observation(path.rsplit('/', 1)[-1]))
                    except KeyError:
                        self.fail(404, '观察不存在')
                    return


                if path == '/api/review/status':
                    self.send_body(200, review_store.status())
                    return
                if path == '/api/review/reviews':
                    qs = parse_qs(urlsplit(self.path).query)
                    kind = (qs.get('kind') or [None])[0]
                    try:
                        limit = int((qs.get('limit') or ['50'])[0])
                    except ValueError:
                        self.fail(400, 'limit须为整数')
                        return
                    self.send_body(200, review_store.list_reviews(kind=kind, limit=limit))
                    return
                if path == '/api/review/plans':
                    self.send_body(200, review_store.list_plans())
                    return
                if re.fullmatch(r'/api/review/reviews/rev-[A-Za-z0-9_-]+', path):
                    try:
                        self.send_body(200, review_store.get_review(path.rsplit('/', 1)[-1]))
                    except KeyError:
                        self.fail(404, '复盘不存在')
                    return




                if path == '/api/config/catalog':
                    self.send_body(200, config_store.catalog())
                    return
                if path == '/api/config/settings-bind':
                    ai_public = None
                    try:
                        ai_public = ai.public_config() if hasattr(ai, 'public_config') else ai.get_config()
                    except Exception:
                        ai_public = None
                    if isinstance(ai_public, dict):
                        ai_public = dict(ai_public)
                        for bad in ('api_key', 'apiKey', 'key', 'secret', 'token', 'authorization', 'encrypted_key'):
                            ai_public.pop(bad, None)
                    extras = {
                        'scan_settings': service.settings(),
                        'ai_public': ai_public,
                        'sim_config': sim.get_config(),
                        'eval_plans': eval_store.list_plans(),
                        'review_plans': review_store.list_plans() if hasattr(review_store, 'list_plans') else None,
                    }
                    self.send_body(200, config_store.settings_bind_snapshot(extras))
                    return
                if path == '/api/config':
                    self.send_body(200, config_store.get())
                    return
                if path == '/api/ext/catalog':
                    self.send_body(200, ext_store.catalog())
                    return
                if path == '/api/ext/sources':
                    self.send_body(200, ext_store.list_sources())
                    return
                if re.fullmatch(r'/api/ext/sources/(precise_indicator_5|events|unlock|supply|historical_mcap)', path):
                    self.send_body(200, ext_store.get_source(path.rsplit('/', 1)[-1]))
                    return
                if path == '/api/ext/precise-flow':
                    self.send_body(200, ext_store.precise_flow_status())
                    return

                if path == '/api/backtest/catalog':
                    self.send_body(200, backtest_store.catalog())
                    return
                if path == '/api/backtest/runs':
                    qs = parse_qs(urlsplit(self.path).query)
                    try:
                        limit = int((qs.get('limit') or ['50'])[0])
                    except ValueError:
                        self.fail(400, 'limit 须为整数')
                        return
                    self.send_body(200, backtest_store.list_runs(limit=limit))
                    return
                if re.fullmatch(r'/api/backtest/runs/bt-[A-Za-z0-9_-]+', path):
                    try:
                        self.send_body(200, backtest_store.get_run(path.rsplit('/', 1)[-1]))
                    except KeyError:
                        self.fail(404, '回测记录不存在')
                    return
                if path == '/api/backtest/compares':
                    qs = parse_qs(urlsplit(self.path).query)
                    try:
                        limit = int((qs.get('limit') or ['30'])[0])
                    except ValueError:
                        self.fail(400, 'limit 须为整数')
                        return
                    self.send_body(200, backtest_store.list_compares(limit=limit))
                    return
                if re.fullmatch(r'/api/backtest/compares/btc-[A-Za-z0-9_-]+', path):
                    try:
                        self.send_body(200, backtest_store.get_compare(path.rsplit('/', 1)[-1]))
                    except KeyError:
                        self.fail(404, '对照批次不存在')
                    return

                if path == '/api/struct/catalog':
                    self.send_body(200, struct_catalog())
                    return
                if path == '/api/struct/list':
                    qs = parse_qs(urlsplit(self.path).query)
                    interval = (qs.get('interval') or ['1d'])[0]
                    try:
                        limit = int((qs.get('limit') or ['40'])[0])
                    except ValueError:
                        self.fail(400, 'limit 须为整数')
                        return
                    try:
                        self.send_body(200, list_struct_ready(root, interval=interval, limit=limit))
                    except ValueError as exc:
                        self.fail(400, str(exc)[:300])
                    return
                if path == '/api/struct/compute':
                    qs = parse_qs(urlsplit(self.path).query)
                    market = (qs.get('market') or [None])[0]
                    symbol = (qs.get('symbol') or [None])[0]
                    interval = (qs.get('interval') or ['1d'])[0]
                    start = (qs.get('start') or [None])[0]
                    end = (qs.get('end') or [None])[0]
                    if not market or not symbol:
                        self.fail(400, '须提供 market 与 symbol')
                        return
                    try:
                        self.send_body(200, struct_compute(root, market, symbol, interval=interval, start=start, end=end))
                    except ValueError as exc:
                        self.fail(400, str(exc)[:300])
                    return

                if path == '/api/cache/retention':
                    from cache_retention import effective_policy,capacity
                    p=Path(root)/'runtime'/'cleanup-settings.json'
                    return self.send_body(200,dict(settings=json.loads(p.read_text(encoding='utf-8')) if p.exists() else dict(enabled=True,last_attempt_at=None),policy=effective_policy(root),capacity=capacity(root)))
                if path == '/api/b-observe/repairs':
                    from b_repair_store import RepairStore
                    return self.send_body(200,RepairStore(root).summary())
                if path == '/api/b-observe/report':
                    from b_observe_api import events_report
                    return self.send_body(200,events_report(root).encode('utf-8'),'text/markdown; charset=utf-8')
                if path == '/api/b-observe/events':
                    from b_observe_api import events_payload
                    return self.send_body(200, events_payload(root))
                if path == '/api/b-observe/events.csv':
                    from b_observe_api import events_csv
                    body = events_csv(root).encode('utf-8')
                    self.send_response(200)
                    self.send_header('Content-Type', 'text/csv; charset=utf-8')
                    self.send_header('Content-Disposition', 'attachment; filename="b-accumulation-events.csv"')
                    self.send_header('Content-Length', str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                    return
                if path == '/api/tasks':
                    self.send_body(200, {'tasks': service.tasks(), 'scan_operation': service.latest_operation()})
                    return
                if re.fullmatch(r'/api/tasks/task-[a-f0-9]{32}', path):
                    task = service.task(path.rsplit('/', 1)[-1])
                    self.send_body(200, task) if task else self.fail(404, '任务不存在')
                    return
                if path == '/reserves-data.json' and prototype:
                    from reserve_refresh import unavailable
                    saved = json.loads((root / 'scan.json').read_bytes())
                    self.send_body(200, saved.get('reserveEvidence') or unavailable())
                    return
                if path == '/api/scan':
                    target = root / 'scan.json'
                    if not target.exists():
                        self.fail(503, '尚无已发布扫描')
                        return
                elif path == '/report.md':
                    target = root / 'DOCS' / 'report.md'
                elif path in DOC_PATHS:
                    target = (root if path=='/DOCS/report.md' else code_root) / path.lstrip('/')
                elif prototype and (path.lstrip('/') in PROTOTYPE_FILES or path == '/'):
                    target = code_root / 'design' / ('prototype.html' if path == '/' else path.lstrip('/'))
                elif not prototype and path in ('/', '/index.html', '/scan.json', '/tasks-runtime.js', '/ranges-runtime.js', '/ai-runtime.js', '/reports-runtime.js', '/strategy-runtime.js', '/sim-runtime.js', '/eval-runtime.js', '/review-runtime.js', '/struct-runtime.js', '/backtest-runtime.js', '/config-runtime.js', '/rule-market-runtime.js', '/coverage-runtime.js', '/ext-runtime.js', '/evidence-runtime.js'):
                    target = (root if path=='/scan.json' else code_root) / ('index.html' if path == '/' else path.lstrip('/'))
                    if path in ('/tasks-runtime.js', '/ranges-runtime.js', '/ai-runtime.js', '/reports-runtime.js', '/strategy-runtime.js', '/sim-runtime.js', '/eval-runtime.js', '/review-runtime.js', '/struct-runtime.js', '/backtest-runtime.js', '/config-runtime.js', '/rule-market-runtime.js', '/coverage-runtime.js', '/ext-runtime.js', '/evidence-runtime.js'):
                        target = code_root / 'design' / path.lstrip('/')
                else:
                    self.fail(404, '未开放的资源或接口')
                    return
                body = target.read_bytes()

                def _hydrate_scan_payload(saved, source_bytes):
                    """合并持续观察池/确认事件到行级 emaSqueezeStage；不改本根 ignitionGrade。"""
                    try:
                        from ema_squeeze_store import hydrate_rows_from_store
                        n = hydrate_rows_from_store(saved.get('rows') or [], root=root)
                        saved['emaPoolHydrated'] = n
                        import hashlib
                        from ema_observation_bootstrap import enrich
                        upgraded = enrich(saved.get('rows') or [], root, scan_id=saved.get('scan_id'),
                                          scan_sha256=hashlib.sha256(source_bytes).hexdigest())
                        from ema_observations import enrich_rows
                        saved['emaObservationHydrated'] = upgraded + enrich_rows(saved.get('rows') or [], root)
                    except Exception as exc:
                        saved['emaPoolHydrateError'] = str(exc)[:180]
                    return saved

                if path in ('/api/scan', '/scan.json'):
                    saved = json.loads(body)
                    _hydrate_scan_payload(saved, body)
                    body = json.dumps(saved, ensure_ascii=False, allow_nan=False).encode('utf-8')

                if target.name == 'prototype.html':
                    # The embedded fallback is historical. HTTP always reads the saved latest file.
                    scan_path = root / 'scan.json'
                    if scan_path.exists():
                        scan_bytes = scan_path.read_bytes()
                        saved = json.loads(scan_bytes)
                        _hydrate_scan_payload(saved, scan_bytes)
                        scan = json.dumps(saved, ensure_ascii=False, allow_nan=False).replace('</', '<\\/')
                        html = body.decode('utf-8')
                        html = re.sub(r'(<script id="snapshot-data"[^>]*>).*?(</script>)', lambda m: m[1] + scan + m[2], html, count=1, flags=re.S)
                        from reserve_refresh import unavailable
                        reserves = json.dumps(saved.get('reserveEvidence') or unavailable(), ensure_ascii=False, allow_nan=False).replace('</', '<\\/')
                        html = re.sub(r'(<script id="reserves-data"[^>]*>).*?(</script>)', lambda m: m[1] + reserves + m[2], html, count=1, flags=re.S)
                        body = html.encode('utf-8')
                content_type = 'application/json; charset=utf-8' if target.suffix == '.json' else (mimetypes.guess_type(target.name)[0] or 'application/octet-stream') + '; charset=utf-8'
                if target.suffix == '.md':
                    content_type = 'text/plain; charset=utf-8'
                self.send_body(200, body, content_type)
            except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                # The browser cancelled this response. Do not try a second 503 on a closed socket.
                self.close_connection = True
                return
            except FileNotFoundError:
                self.fail(404, '资源不存在')
            except (OSError, ValueError, sqlite3.Error):
                self.fail(503, '本地数据暂不可读，请重试')

        def reject_write(self, status, message):
            try:
                length = int(self.headers.get('Content-Length', '0'))
                if 0 < length <= 262144:
                    self.connection.settimeout(.5)
                    self.rfile.read(length)
            except (OSError, ValueError):
                pass
            self.fail(status, message)

        def write_api(self):
            if not self.valid_host():
                return
            # Browser writes must originate in the very same local page.
            origin = self.headers.get('Origin')
            if origin != 'http://' + self.headers.get('Host', '') or self.headers.get('Sec-Fetch-Site') not in (None, 'same-origin'):
                self.reject_write(403, '写操作要求本机页面同源请求')
                return
            if self.headers.get_content_type() != 'application/json':
                self.reject_write(415, '仅接受application/json')
                return
            try:
                length = int(self.headers.get('Content-Length', '0'))
                path = unquote(urlsplit(self.path).path)
                if path in ('/api/reports', '/api/reports/preview', '/api/reports/local') or re.fullmatch(r'/api/reports/report-[a-f0-9]{32}/revisions', path):
                    limit = 262144
                elif path.startswith('/api/config/backup/'):
                    limit = 8 * 1024 * 1024  # full backup packages (no credentials)
                else:
                    limit = 16384
                if length <= 0 or length > limit:
                    self.reject_write(413, '请求体大小不符')
                    return
                self.connection.settimeout(5)
                raw = self.rfile.read(length)
                if len(raw) != length:
                    raise ValueError('请求体不完整')
                body = json.loads(raw, parse_constant=lambda _: (_ for _ in ()).throw(ValueError('非有限值')))
                if path.startswith('/api/tracking/'):
                    if self.command!='POST' or path not in ('/api/tracking/read_view','/api/tracking/exports','/api/tracking/notifications/delivered','/api/tracking/notifications/read'):
                        return self.fail(405,'追踪页面只提供读取与静态导出')
                    from tracking_service import read_route
                    code,data,ctype=read_route(root,'POST',self.path,raw)
                    return self.send_body(code,data,ctype)
                if self.command == 'PUT' and path == '/api/cache/retention':
                    if not isinstance(body,dict) or not {'enabled'}<=set(body)<={'enabled','retention'} or type(body['enabled']) is not bool:raise ValueError('清理设置字段无效')
                    from cache_retention import validate_retention
                    if 'retention' in body:body['retention']=validate_retention(body['retention'])
                    from durable_io import protection,atomic_json
                    p=Path(root)/'runtime'/'cleanup-settings.json'
                    with protection(root):
                        old=json.loads(p.read_text(encoding='utf-8')) if p.exists() else dict(last_attempt_at=None)
                        old.update(body);atomic_json(p,old)
                    return self.send_body(200,old)
                if self.command == 'POST' and path == '/api/b-observe/repairs/settings':
                    from b_repair_store import RepairStore
                    RepairStore(root).set_enabled(body['enabled'])
                    return self.send_body(200,RepairStore(root).summary())
                if self.command == 'POST' and path == '/api/b-observe/run':
                    st = service.settings()
                    if not st.get('observe', {}).get('enabled'):
                        return self.fail(409, 'B观察未启用')
                    from observe_config import target_close_boundary_ms
                    now = service.clock()
                    now_ms = int(now.timestamp() * 1000)
                    offset = int(st.get('timing', {}).get('closeOffsetSec', 120))
                    boundary = target_close_boundary_ms(now_ms, offset_sec=offset)
                    with service.lock, service._db() as db:
                        tid, reused = service._enqueue_b_observe(db, 'manual_b_observe', st['version'], boundary)
                    service.tick()
                    task = service.task(tid)
                    return self.send_body(200, {'task': task, 'reused': reused, 'target_close_boundary': task['request']['target_close_boundary'], 'requested_target_close_boundary': boundary})
                if self.command == 'POST' and path == '/api/schedule/test-countdown':
                    if set(body)-{'minutes'}:raise ValueError('只接受minutes参数')
                    return self.send_body(200, service.arm_countdown(minutes=body.get('minutes',15)))
                if self.command == 'POST' and path == '/api/schedule/test-countdown/cancel':
                    if body:raise ValueError('取消倒计时只接受空对象')
                    return self.send_body(200, service.cancel_countdown())
                if self.command == 'PUT' and path == '/api/settings':
                    self.send_body(200, service.save_settings(body))
                elif self.command == 'PUT' and path == '/api/ai/config':
                    self.send_body(200, ai.save_config(body))
                elif self.command == 'POST' and path == '/api/ai/test/connection':
                    if body != {}:
                        raise ValueError('连接测试只接受空JSON对象；配置以已保存值为准')
                    self.send_body(200, ai.test_connection())
                elif self.command == 'POST' and path == '/api/ai/test/capabilities':
                    checks = body.get('checks') if isinstance(body, dict) else None
                    if body and set(body) - {'checks'}:
                        raise ValueError('能力测试只接受可选checks字段')
                    self.send_body(200, ai.test_capabilities(checks))
                elif self.command == 'POST' and path == '/api/research/feedback':
                    if not isinstance(body, dict):
                        raise ValueError('请求体须为对象')
                    report = research.get_report(body.get('report_id') or '')
                    self.send_body(201, research.experiences.feedback(report['report_id'], body))
                elif self.command == 'POST' and path == '/api/research/experiences':
                    self.send_body(201, research.experiences.create(body))
                elif self.command == 'PUT' and re.fullmatch(r'/api/research/experiences/exp-[a-f0-9]{32}', path):
                    self.send_body(200, research.experiences.update(path.rsplit('/', 1)[-1], body))
                elif self.command == 'POST' and path == '/api/reports/preview':
                    if not isinstance(body, dict):
                        raise ValueError('请求体须为对象')
                    preview_body = dict(body)
                    # Use the same server-owned scan settings version as confirmation.
                    # Otherwise every browser preview hashes None while submit hashes the live version.
                    preview_body['settings_version'] = service.settings()['version']
                    self.send_body(200, research.preview(preview_body))
                elif self.command == 'POST' and path == '/api/reports/local':
                    stamped, _gate = stamp_export_payload(rule_bindings, body if isinstance(body, dict) else {})
                    self.send_body(201, research.save_local_report(stamped))
                elif self.command == 'POST' and path == '/api/reports':
                    stamped, _gate = stamp_export_payload(rule_bindings, body if isinstance(body, dict) else {})
                    self.send_body(202, service.submit_research(stamped))
                elif self.command == 'POST' and path == '/api/scans':
                    if body != {}:
                        raise ValueError('扫描接口只接受空JSON对象；币池固定全市场')
                    self.send_body(202, service.submit_unified(self.headers.get('Idempotency-Key')))
                elif self.command == 'POST' and path == '/api/candles/preflight':
                    self.send_body(200, service.ranges.preflight(body))
                elif self.command == 'POST' and path == '/api/candles/backfills':
                    self.send_body(202, service.submit_range(body))


                elif self.command == 'POST' and path == '/api/strategies/ref/validate':
                    self.send_body(200, rule_bindings.validate_ref(body if isinstance(body, dict) else {}))
                elif self.command == 'POST' and path == '/api/strategies/bindings/bind-all':
                    self.send_body(200, rule_bindings.bind_all(body if isinstance(body, dict) else {}))
                elif self.command == 'PUT' and re.fullmatch(r'/api/strategies/bindings/(market|reports|basket|experiment|validation)', path):
                    surface = path.rsplit('/', 1)[-1]
                    self.send_body(200, rule_bindings.bind_surface(surface, body if isinstance(body, dict) else {}))
                elif self.command == 'DELETE' and re.fullmatch(r'/api/strategies/bindings/(market|reports|basket|experiment|validation)', path):
                    surface = path.rsplit('/', 1)[-1]
                    self.send_body(200, rule_bindings.unbind_surface(surface))
                elif self.command == 'POST' and path == '/api/strategies/drafts':
                    self.send_body(201, strategies.create_draft(body))
                elif self.command == 'PUT' and re.fullmatch(r'/api/strategies/drafts/draft-[a-f0-9]{12}', path):
                    self.send_body(200, strategies.update_draft(path.rsplit('/', 1)[-1], body))
                elif self.command == 'POST' and path == '/api/strategies/from-report':
                    self.send_body(201, strategies.create_draft_from_report(body, research=research))
                elif self.command == 'POST' and re.fullmatch(r'/api/strategies/drafts/draft-[a-f0-9]{12}/freeze', path):
                    self.send_body(201, strategies.freeze(path.rsplit('/', 2)[1], body if isinstance(body, dict) else {}))
                elif self.command == 'POST' and re.fullmatch(r'/api/strategies/versions/ver-[a-f0-9]{12}/fork', path):
                    self.send_body(201, strategies.create_draft_from_version(path.rsplit('/', 2)[1], body if isinstance(body, dict) else {}))
                elif self.command == 'POST' and re.fullmatch(r'/api/strategies/versions/ver-[a-f0-9]{12}/enable', path):
                    # RULE-01 deepen: allow fingerprint fields + acknowledge_coverage_drift
                    allowed = {
                        'enabled',
                        'expected_content_hash', 'content_hash',
                        'expected_coverage_fingerprint', 'coverage_fingerprint',
                        'expected_joint_hash', 'joint_hash',
                        'strategy_fingerprint', 'joint_fingerprint', 'rule_fingerprint',
                        'acknowledge_coverage_drift',
                    }
                    if not isinstance(body, dict) or set(body) - allowed:
                        raise ValueError(
                            '启停接受 enabled + 可选指纹核验字段 '
                            '(expected_content_hash/coverage_fingerprint/joint_hash/'
                            'acknowledge_coverage_drift 等)'
                        )
                    if 'enabled' not in body or not isinstance(body['enabled'], bool):
                        raise ValueError('须提供布尔 enabled')
                    result = strategies.set_enabled(
                        path.rsplit('/', 2)[1], body['enabled'], body=body,
                    )
                    if result.get('enable_refused'):
                        self.send_body(409, {
                            **result,
                            'error': result.get('fingerprint_refusal')
                            or result.get('enable_block_reason')
                            or '启用硬闸拒绝',
                            'http_status': 409,
                        })
                    else:
                        self.send_body(200, result)
                elif self.command == 'POST' and re.fullmatch(r'/api/strategies/versions/ver-[a-f0-9]{12}/export', path):
                    self.send_body(200, strategies.export_version(path.rsplit('/', 2)[1]))
                elif self.command == 'POST' and path == '/api/strategies/import':
                    self.send_body(201, strategies.import_version(body if isinstance(body, dict) else {}))
                elif self.command == 'PUT' and path == '/api/sim/config':
                    self.send_body(200, sim.save_config(body))

                elif self.command == 'POST' and path == '/api/sim/autofill/preview':
                    self.send_body(200, sim.plan_autofill(
                        body if isinstance(body, dict) else {},
                        strategy_store=strategies,
                        rule_bindings=rule_bindings,
                        review_store=review_store,
                    ))
                elif self.command == 'POST' and path == '/api/sim/autofill':
                    self.send_body(201, sim.autofill(
                        body if isinstance(body, dict) else {},
                        strategy_store=strategies,
                        rule_bindings=rule_bindings,
                        review_store=review_store,
                    ))
                elif self.command == 'POST' and path == '/api/sim/events/open':
                    self.send_body(201, sim.open_position(body))
                elif self.command == 'POST' and path == '/api/sim/events/close':
                    self.send_body(201, sim.close_position(body))
                elif self.command == 'POST' and path == '/api/sim/events/stub':
                    self.send_body(201, sim.stub_event(body))
                elif self.command == 'POST' and path == '/api/sim/accounts/independent':
                    self.send_body(201, sim.ensure_independent_account(
                        body.get('strategy_version_id'), label=body.get('label')))


                elif self.command == 'PUT' and path == '/api/config/observation-pool':
                    self.send_body(200, config_store.set_observation_pool(body if isinstance(body, dict) else {}))
                elif self.command == 'PUT' and path == '/api/config/prompt-edits':
                    self.send_body(200, config_store.set_prompt_edits(body if isinstance(body, dict) else {}))
                elif self.command == 'POST' and path == '/api/config/export':
                    self.send_body(200, config_store.export_bundle())
                elif self.command == 'POST' and path == '/api/config/import':
                    self.send_body(200, config_store.import_bundle(body if isinstance(body, dict) else {}))
                elif self.command == 'POST' and path == '/api/config/backup/manifest':
                    self.send_body(200, config_store.backup_manifest(body if isinstance(body, dict) else {}))
                elif self.command == 'POST' and path == '/api/config/backup/export':
                    self.send_body(200, config_store.backup_export(body if isinstance(body, dict) else {}))
                elif self.command == 'POST' and path == '/api/config/backup/review':
                    self.send_body(200, config_store.backup_review(body if isinstance(body, dict) else {}))
                elif self.command == 'POST' and path == '/api/config/backup/import':
                    self.send_body(200, config_store.backup_import(body if isinstance(body, dict) else {}))
                elif self.command == 'POST' and path == '/api/ext/precise-flow/claim':
                    self.send_body(200, ext_store.claim_precise_flow(body if isinstance(body, dict) else {}))
                elif self.command == 'POST' and path == '/api/ext/claim':
                    self.send_body(200, ext_store.claim_capability(body if isinstance(body, dict) else {}))
                elif self.command == 'PUT' and re.fullmatch(r'/api/ext/sources/(precise_indicator_5|events|unlock|supply|historical_mcap)', path):
                    self.send_body(200, ext_store.upsert_source(path.rsplit('/', 1)[-1], body if isinstance(body, dict) else {}))
                elif self.command == 'POST' and re.fullmatch(r'/api/ext/sources/(precise_indicator_5|events|unlock|supply|historical_mcap)/revoke', path):
                    sid = path.rsplit('/', 2)[1]
                    self.send_body(200, ext_store.revoke_source(sid, body if isinstance(body, dict) else {}))
                elif self.command == 'POST' and path == '/api/backtest/run':
                    self.send_body(201, backtest_store.run(body if isinstance(body, dict) else {}))
                elif self.command == 'POST' and path == '/api/backtest/compare':
                    self.send_body(201, backtest_store.compare(body if isinstance(body, dict) else {}))
                elif self.command == 'POST' and path == '/api/eval/observations/start':
                    self.send_body(201, eval_store.start_observation(body))
                elif self.command == 'PUT' and path == '/api/eval/plans':
                    self.send_body(200, eval_store.save_plan(body if isinstance(body, dict) else {}))
                elif self.command == 'POST' and re.fullmatch(r'/api/eval/plans/eplan-[A-Za-z0-9_-]+/enable', path):
                    if not isinstance(body, dict) or not isinstance(body.get('enabled'), bool):
                        raise ValueError('须提供布尔 enabled')
                    self.send_body(200, eval_store.set_plan_enabled(path.rsplit('/', 2)[1], body['enabled']))
                elif self.command == 'POST' and re.fullmatch(r'/api/eval/observations/obs-[A-Za-z0-9_-]+/settle', path):
                    oid = path.rsplit('/', 2)[1]
                    self.send_body(201, eval_store.settle(oid, body if isinstance(body, dict) else {}))
                elif self.command == 'POST' and re.fullmatch(r'/api/eval/observations/obs-[A-Za-z0-9_-]+/close', path):
                    oid = path.rsplit('/', 2)[1]
                    payload = dict(body) if isinstance(body, dict) else {}
                    payload.setdefault('mode', 'manual')
                    self.send_body(201, eval_store.settle(oid, payload))

                elif self.command == 'POST' and path == '/api/review/data':
                    self.send_body(201, review_store.create_data_review(body if isinstance(body, dict) else {}))
                elif self.command == 'POST' and path == '/api/review/ai':
                    self.send_body(201, review_store.create_ai_manual_review(body if isinstance(body, dict) else {}))
                elif self.command == 'PUT' and path == '/api/review/plans':
                    self.send_body(200, review_store.save_plan(body if isinstance(body, dict) else {}))
                elif self.command == 'POST' and re.fullmatch(r'/api/review/plans/plan-[A-Za-z0-9_-]+/enable', path):
                    if not isinstance(body, dict) or set(body) - {'enabled'}:
                        raise ValueError('启停只接受 {"enabled": true|false}')
                    if 'enabled' not in body or not isinstance(body['enabled'], bool):
                        raise ValueError('须提供布尔 enabled')
                    self.send_body(200, review_store.set_plan_enabled(path.rsplit('/', 2)[1], body['enabled']))
                elif self.command == 'PUT' and path == '/api/review/pause-new-entries':
                    if not isinstance(body, dict) or set(body) - {'pause_new_entries'}:
                        raise ValueError('只接受 {"pause_new_entries": true|false}')
                    if 'pause_new_entries' not in body or not isinstance(body['pause_new_entries'], bool):
                        raise ValueError('须提供布尔 pause_new_entries')
                    self.send_body(200, review_store.set_pause_new_entries(body['pause_new_entries']))

                elif self.command == 'POST' and re.fullmatch(r'/api/reports/report-[a-f0-9]{32}/revisions', path):
                    self.send_body(201, research.revisions().add_revision(path.split('/')[3], body))
                elif self.command == 'POST' and re.fullmatch(r'/api/reports/report-[a-f0-9]{32}/human-reviews', path):
                    self.send_body(201, research.revisions().add_human_review(path.split('/')[3], body))
                elif self.command == 'POST' and re.fullmatch(r'/api/reports/batches/batch-[a-f0-9]{32}/retry-failed', path):
                    if not isinstance(body, dict) or set(body) - {'include_unknown'}:
                        raise ValueError('重试接口只接受可选 include_unknown')
                    self.send_body(202, service.submit_batch_retry(path.split('/')[4], body.get('include_unknown', False)))
                elif self.command == 'POST' and re.fullmatch(r'/api/tasks/task-[a-f0-9]{32}/cancel', path):
                    if body != {}:
                        raise ValueError('取消接口只接受空JSON对象')
                    self.send_body(200, service.cancel(path.split('/')[3]))
                else:
                    self.fail(404, '接口未实现')
            except (ExperienceConflict, Conflict, AiConflict, SimConflict, EvalConflict, ReviewConflict, BacktestConflict, ConfigConflict, ExtConflict, BindingConflict, FrozenExecConflict) as exc:
                self.fail(409, str(exc))
            except KeyError:
                self.fail(404, '任务不存在')
            except (ValueError, UnicodeError) as exc:
                self.fail(400, str(exc)[:300])
            except (OSError, sqlite3.Error):
                self.fail(503, '本地写入失败')

        do_POST = write_api
        do_PUT = write_api

        def log_message(self, *args):
            pass

    return Handler


def main():
    from timezone_support import ensure_timezone_data
    ensure_timezone_data()  # Fail before binding ports or recovering schedules.
    import scanner
    servers = []
    service = None
    lease=None
    try:
        # Bind before recovery so a second startup cannot mutate the active database.
        for port in (8767, 8768):
            server = ThreadingHTTPServer(('127.0.0.1', port), BaseHTTPRequestHandler)
            server.daemon_threads = True
            servers.append(server)
        lease=HostLease(ROOT);lease.__enter__()
        service = TaskService(ROOT, scanner.scan, progress=lambda: dict(scanner.PROGRESS))
        from request_telemetry import configure
        configure(ROOT)
        ai = AiService(ROOT)
        research = AiResearch(ROOT, ai)
        strategies = StrategyStore(ROOT)
        rule_bindings = RuleBindings(ROOT, strategies)
        sim = SimStore(ROOT)
        eval_store = EvalStore(ROOT, strategy_store=strategies, sim_store=sim, ranges=service.ranges)
        review_store = ReviewStore(ROOT, eval_store=eval_store, sim_store=sim, ai_service=ai)
        eval_store.review_store = review_store
        backtest_store = BacktestStore(ROOT, strategy_store=strategies, sim_store=sim)
        config_store = ConfigStore(ROOT)
        ext_store = ExtStore(ROOT)
        config_store.bind_stores(
            strategy_store=strategies,
            sim_store=sim,
            eval_store=eval_store,
            review_store=review_store,
        )
        service.set_research(research)
        service.bind_eval_review(eval_store=eval_store, review_store=review_store)
        for server in servers:
            server.RequestHandlerClass = handler_factory(
                ROOT, service, prototype=server.server_port == 8768, ai=ai, research=research,
                strategies=strategies, rule_bindings=rule_bindings, sim=sim,
                eval_store=eval_store, review_store=review_store,
                backtest_store=backtest_store, config_store=config_store, ext_store=ext_store,
            )
            threading.Thread(target=server.serve_forever, daemon=True).start()
            print(f'http://127.0.0.1:{server.server_port}', flush=True)
        # Resume only enabled local tracking queues; no fetches or new scan plan.
        from tracking_service import kick
        kick(ROOT)
        from tracking.runtime import start as start_tracking_runtime
        start_tracking_runtime(ROOT,host=service)
        service.start()
        threading.Event().wait()
    finally:
        from tracking.runtime import stop as stop_tracking_runtime
        stop_tracking_runtime(ROOT)
        if service:
            service.close()
        for server in servers:
            server.server_close()
        if lease:lease.__exit__(None,None,None)


if __name__ == '__main__':
    main()
