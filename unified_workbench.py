"""v3.3 r5 read-only presentation generation. Never modifies frozen strategy evidence."""
import copy
import datetime as dt
import hashlib
import json
import sqlite3
from contextlib import closing
from pathlib import Path
from b_accumulation_store import BAccumulationStore
from ema_squeeze_store import hydrate_rows_from_store
from strategy_registry import indicator_states, VERSION_HASH

UI_VERSION = 'v3.3-r5-1020-unified-r3'
INPUTS = ('scan.json', 'runtime/b-accumulation-events.json',
          'runtime/b-observe-last-round.json', 'runtime/ema-squeeze-candidates.json','runtime/b-observe-universe.json','runtime/ema-long-observations-bootstrap.json')
_HASH_CACHE={}

def epoch(value):
    if isinstance(value, (int, float)):
        return value
    try:
        return dt.datetime.fromisoformat(value.replace('Z', '+00:00')).timestamp()*1000
    except (TypeError, ValueError, AttributeError):
        return None

def fingerprint(root):
    result = {}
    for name in INPUTS:
        if name=='runtime/b-accumulation-events.json' and (root/'runtime/b-events-v3.sqlite3').exists():
            result['b_event_generation']=BAccumulationStore(root).generation();continue
        p = root/name
        if not p.exists():result[name]=None;continue
        stat=p.stat();key=(str(p),stat.st_mtime_ns,stat.st_size)
        if key not in _HASH_CACHE:
            _HASH_CACHE.clear() if len(_HASH_CACHE)>64 else None
            _HASH_CACHE[key]=hashlib.sha256(p.read_bytes()).hexdigest()
        result[name]=_HASH_CACHE[key]
    from tracking_service import tracking_seq,recovery_seq,c_check_generation
    result['tracking_commit_seq']=tracking_seq(root)
    result['c_check_generation']=c_check_generation(root)
    result['recovery_commit_seq']=recovery_seq(root)
    return result

def available_event(event, cutoff):
    """Reconstruct a revision available then; later corrections cannot leak backwards."""
    candidates = [event] + [r.get('snapshot', {}) for r in event.get('revisions', [])]
    candidates = [e for e in candidates if epoch(e.get('last_evaluated_at') or e.get('first_seen_at')) is not None
                  and epoch(e.get('last_evaluated_at') or e.get('first_seen_at')) <= cutoff]
    return copy.deepcopy(max(candidates, key=lambda e: epoch(e.get('last_evaluated_at') or e.get('first_seen_at')))) if candidates else None

def round_observations(root, target):
    """Read the immutable, published-target ledger using SQLite read-only mode.

    Hash the selected logical rows, not a live WAL file. No connection is opened
    when the ledger is absent; missing coverage never becomes a negative signal.
    """
    path = root/'runtime/b-observations.sqlite3'
    if not target or not path.exists():
        return {}, None
    try:
        with closing(sqlite3.connect(path.resolve().as_uri()+'?mode=ro', uri=True)) as db:
            data = {(m,s):json.loads(p) for m,s,p in db.execute(
                'SELECT market,symbol,payload FROM round_observations WHERE target=? ORDER BY market,symbol', (target,))}
        return data, None
    except (sqlite3.Error, ValueError):
        return {}, '同期观察账本不可读；覆盖保持未知'

def load_payload(root, context='latest',projection='full',market=None,symbol=None):
    root = Path(root)
    if context not in ('latest', 'snapshot'):
        raise ValueError('context must be latest or snapshot')
    if projection not in ('full','summary-v1'):raise ValueError('unknown projection')
    if bool(market)!=bool(symbol):raise ValueError('精确详情必须同时提供market和symbol')
    for _ in range(3):
        before = fingerprint(root)
        saved = json.loads((root/'scan.json').read_text(encoding='utf-8-sig'))
        store = BAccumulationStore(root)
        from b_event_sqlite import SQLiteBStore
        events=store.list_events(market=market,symbol=symbol,full=projection=='full',revisions=context=='snapshot' or projection=='full') if isinstance(store,SQLiteBStore) else store.list_events(market=market,symbol=symbol)
        rounds = store.meta().get('rounds', []) if context == 'latest' else []
        round_path = root/'runtime/b-observe-last-round.json'
        last_round = json.loads(round_path.read_text(encoding='utf8')) if round_path.exists() else None
        observations, observation_error = round_observations(root, (last_round or {}).get('target_close_boundary')) if context == 'latest' else ({}, None)
        cutoff = epoch(saved.get('available_at') or saved.get('finishedAt'))
        if context == 'snapshot':
            if cutoff is None:
                raise ValueError('快照没有可验证的可得时间')
            events = [e for e in (available_event(e, cutoff) for e in events) if e]
            last_round = None  # Current monitoring coverage is not historical coverage.
        else:
            hydrate_rows_from_store(saved.get('rows', []), root=root)
        if context=='latest':
            from ema_observation_bootstrap import enrich
            enrich(saved.get('rows',[]),root,scan_id=saved.get('scan_id'),scan_sha256=before.get('scan.json'))
        from b_observe_api import present
        events=[present(e) for e in events]
        from b_repair_store import RepairStore
        from b_evidence_repair import repair_presentation
        repairs=RepairStore(root).summary(market,symbol) if context=='latest' else dict(counts={},jobs=[])
        repair_by_id={j['event_id']:j for j in repairs['jobs']}
        for e in events:e['evidence_repair']=repair_presentation(e,repair_by_id.get(e['event_id']))
        if projection=='summary-v1':
            # Versioned projection only. The default API and explicit exports stay full.
            events=[{k:v for k,v in e.items() if k not in ('bg4h','bg4h_h1','baseline','h','h1','revisions')}|dict(evidence_projection='summary-v1',detail_available=True) for e in events]
        directory_path=root/'runtime/b-observe-universe.json'
        directory=json.loads(directory_path.read_text(encoding='utf8')) if directory_path.exists() else {}
        directory_rows=directory.get('symbols',[]) if isinstance(directory,dict) and directory.get('scope')=='trading_directory' else []
        directory_time=epoch(directory.get('retrieved_at')) if isinstance(directory,dict) else None
        if context=='snapshot' and (directory_time is None or directory_time>cutoff):directory_rows=[]
        trading={(r['market'],r['symbol']) for r in directory_rows if r.get('status',r.get('contractStatus'))=='TRADING'}
        out = {}
        reserve = saved.get('reserveEvidence') or {}
        reserve_by_asset = {v.get('asset'):v for v in reserve.get('records', [])}
        for row in saved.get('rows', []):
            if market and (row.get('market'),row.get('symbol'))!=(market,symbol):continue
            row = copy.deepcopy(row)
            if context == 'snapshot':
                for field, when in row.get('fieldAvailableAt', {}).items():
                    if epoch(when) is None or epoch(when) > cutoff:
                        row.pop(field, None)
                row.pop('bEvents', None)
            row['legacySixStates'] = indicator_states(row)
            re = copy.deepcopy(reserve_by_asset.get(row.get('asset')))
            if re:
                retrieved = epoch(reserve.get('retrievedAt'))
                valid_at = cutoff
                re['freshNow'] = bool(valid_at and retrieved and retrieved<=valid_at and isinstance(re.get('asOf'),(int,float)) and 0<=valid_at-re['asOf']*1000<=36*3600000)
                re.update(retrievedAt=reserve.get('retrievedAt'),source=reserve.get('source'),freshness_context='saved_snapshot',quality='native-chain coverage only; not smart money or exact flows')
                row['reserveEvidence'] = re
            # tradeStatus is an old research disclaimer, not the exchange listing status.
            # scan.json's universe is the saved TRADING universe; archive-only rows below
            # retain a separate historical identity, without pretending live delist coverage.
            row['unifiedListing'] = 'current' if row.get('contractStatus', 'TRADING') == 'TRADING' else 'history'
            out[(row.get('market'), row.get('symbol'))] = row
        for event in events:
            ident = (event.get('market'), event.get('symbol'))
            if not all(ident):
                continue
            if ident not in out:
                out[ident] = dict(market=ident[0], symbol=ident[1], asset='', quote='',
                                  unifiedListing='current' if ident in trading else 'unknown', legacySixStates=[None]*6,
                                  missing=['not_in_saved_scan'], source='B archive only')
            out[ident].setdefault('bEvents', []).append(event)
        by_symbol = {(x.get('market'),x.get('symbol')):x for x in rounds}
        for row in out.values():
            row['bEvents'] = sorted(row.get('bEvents', []), key=lambda e: (e.get('trigger_open_time', 0), e.get('event_id', '')), reverse=True)
            # No event and no successful current coverage is unmonitored, never "no signal".
            ident = (row.get('market'),row.get('symbol'))
            evidence = observations.get(ident)
            target = (last_round or {}).get('target_close_boundary')
            exact = evidence and target and evidence.get('target_close_boundary') == target and evidence.get('open_time') == target-3600000
            row['bMonitoring'] = ('none' if evidence.get('technical')=='fail' else 'insufficient') if exact else 'insufficient' if ident in by_symbol else 'unmonitored'
            row['bMonitoringEvidence'] = evidence
            row['bMonitoringGap'] = observation_error
        if market and symbol:
            from ema_observations import enrich_rows
            enrich_rows(list(out.values()),root)
        from tracking_service import compact_summaries,compact_c_checks,status as tracking_status
        tracking=tracking_status(root)
        if context=='latest':
            summaries,tracking['commit_seq']=compact_summaries(root,list(out))
            c_checks,tracking['c_check_commit_seq']=compact_c_checks(root,list(out))
            for row in out.values():
                ident=('USDT-M' if row.get('market')=='USDⓈ-M' else row.get('market'),row.get('symbol'))
                row['trackingSummary']=summaries.get(ident,[])
                row['trackingEnabled']=tracking['enabled']
                row['cCheck']=c_checks.get(ident)
        else:
            # Historical scan exports must never splice in today's tracking state.
            tracking=dict(tracking,context='snapshot',state='not_available_in_historical_scan')
        after = fingerprint(root)
        # Acquisition bookkeeping has its own read snapshot and sequence. It
        # cannot invalidate market rows; actual business effects still change
        # tracking_commit_seq / c_check_generation and remain fenced below.
        business_before={k:v for k,v in before.items() if k!='recovery_commit_seq'}
        business_after={k:v for k,v in after.items() if k!='recovery_commit_seq'}
        if business_before == business_after:
            after['recovery_commit_seq']=(tracking.get('source_recovery') or {}).get('commit_seq')
            observation_digest = hashlib.sha256(json.dumps(sorted((m,s,p) for (m,s),p in observations.items()),sort_keys=True).encode()).hexdigest()
            generation = hashlib.sha256(json.dumps({'inputs':business_after,'observation_digest':observation_digest,'context':context,'presentation_version':UI_VERSION},sort_keys=True).encode()).hexdigest()
            return dict(schema='unified-signals-v33r5', ui_version=UI_VERSION, generation=generation,projection=projection,full_evidence_url='/api/unified/signals?context='+context,
                        context=context, snapshot={k:saved.get(k) for k in ('scan_id','revision','startedAt','finishedAt','available_at','registryHash')},
                        registry={'frozen_hash':VERSION_HASH,'ui_version':UI_VERSION,'historical_registry_preserved':True},
                        input_hashes=after, auxiliary_consistency='recovery_status_independent_snapshot', observation_digest=observation_digest, rows=list(out.values()), last_round=last_round,
                        contract_count=len(out), event_count=len(events),
                        simulation_enabled=False, evidence_repair=repairs, tracking=tracking,c_status='connected_shadow' if tracking['enabled'] else 'connected_disabled', auxiliary_status='not_connected')
    raise OSError('证据发布中，未返回混合generation，请稍后重读')
