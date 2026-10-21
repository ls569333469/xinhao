"""Hourly observation, immutable target and bounded incremental recovery."""
import inspect,time,json,sqlite3,hashlib,uuid,threading
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from pathlib import Path
from b_1h_ingest import ingest_symbol_1h
from b_observe_evaluate import bars_from_cache
from b_process_order import process_bars_in_order
from b_evidence import background_at,structure_at,ms
from b_accumulation_store import BAccumulationStore
from market_candle_cache import MarketCandleCache
from observe_config import target_close_boundary_ms
from durable_io import atomic_json,protected
H=3600000
def background_revision_affects(trigger_open,changed_open):
    # h and h+1 close independently; background_at uses exactly 1499 closed 4h bars.
    return any(changed_open+4*H<=((t//(4*H))*(4*H))<=changed_open+1499*4*H
               for t in (trigger_open+H,trigger_open+2*H))
@protected
def _observations(root,market,symbol,target,rows,metadata=None):
    path=Path(root)/'runtime'/'b-observations.sqlite3';path.parent.mkdir(parents=True,exist_ok=True)
    with closing(sqlite3.connect(path)) as db, db:
        db.execute('CREATE TABLE IF NOT EXISTS observations(market TEXT,symbol TEXT,bar_open INTEGER,rule_version TEXT,payload TEXT,PRIMARY KEY(market,symbol,bar_open,rule_version))')
        db.execute('CREATE TABLE IF NOT EXISTS observation_revisions(market TEXT,symbol TEXT,bar_open INTEGER,target INTEGER,payload_hash TEXT,payload TEXT,PRIMARY KEY(market,symbol,bar_open,payload_hash))')
        db.execute('CREATE TABLE IF NOT EXISTS round_observations(market TEXT,symbol TEXT,target INTEGER,payload TEXT,PRIMARY KEY(market,symbol,target))')
        db.execute('CREATE TABLE IF NOT EXISTS round_observation_revisions(market TEXT,symbol TEXT,target INTEGER,payload_hash TEXT,payload TEXT,PRIMARY KEY(market,symbol,target,payload_hash))')
        newest=rows[-1] if rows else dict(technical='unknown',quality=['warmup_short'])
        summary=dict(newest,**(metadata or {}),target_close_boundary=target,retrieved_at=int(time.time()*1000),rule_version='b-accum-1h-v1',evidence_profile='binance-1h-oi-lsr-v1')
        old=db.execute('SELECT payload FROM round_observations WHERE market=? AND symbol=? AND target=?',(market,symbol,target)).fetchone()
        payload=json.dumps(summary,ensure_ascii=False,sort_keys=True)
        for version in ([old[0]] if old else [])+[payload]:db.execute('INSERT OR IGNORE INTO round_observation_revisions VALUES(?,?,?,?,?)',(market,symbol,target,hashlib.sha256(version.encode()).hexdigest(),version))
        db.execute('INSERT OR REPLACE INTO round_observations VALUES(?,?,?,?)',(market,symbol,target,payload))
        for r in rows:
            payload=json.dumps(dict(r,evidence_profile='binance-1h-oi-lsr-v1'),ensure_ascii=False,sort_keys=True);fp=hashlib.sha256(payload.encode()).hexdigest()
            db.execute('INSERT OR REPLACE INTO observations VALUES(?,?,?,?,?)',(market,symbol,r['open_time'],'b-accum-1h-v1',payload))
            db.execute('INSERT OR IGNORE INTO observation_revisions VALUES(?,?,?,?,?,?)',(market,symbol,r['open_time'],target,fp,payload))
def run_b_observe_round(root,*,symbols,observe,timing,now_ms,fetch_1h=None,target_close_boundary=None,fetch_background=None,fetch_evidence=None,fetch_reference=None,fetch_quote=None,progress=None,round_metadata=None,request_count=None,fetch_acknowledge=None):
    if not observe.get('enabled'):return dict(skipped=True,status='skipped',reason='observe_disabled',written=0)
    from collection_recovery import register_directory
    register_directory(root,symbols,now_ms)
    round_id='bround-'+uuid.uuid4().hex
    boundary=int(target_close_boundary) if target_close_boundary is not None else target_close_boundary_ms(now_ms,offset_sec=int(timing.get('closeOffsetSec',120)))
    store=BAccumulationStore(root);store.ensure_observation_start();cache=MarketCandleCache(root)
    written=evaluated=ordered=evaluable=0;errors=[];details=[];missing=[]
    state_lock=threading.Lock();completed=0
    def one(item):
        nonlocal written,evaluated,ordered,evaluable,completed
        market=item.get('market');symbol=item.get('symbol')
        if not market or not symbol:return
        acquired=int(time.time()*1000)
        try:
            coverage=cache.coverage(market=market,symbol=symbol,target=boundary)
            previous_last=coverage['last_open'];page_count=0
            cursor=store.cursor(market,symbol) or {};gaps=cursor.get('pending_gaps') or []
            starts=[g['after'] for g in coverage['gaps']]+[g['after'] for g in gaps]+coverage['tail']
            start=min(starts) if starts else None
            changed_opens=None
            if fetch_1h:
                supports=any(p.kind==p.VAR_KEYWORD or p.name=='start_time' for p in inspect.signature(fetch_1h).parameters.values())
                pages=[];next_start=start
                for _ in range(4):
                    page_count+=1
                    rows=fetch_1h(market,symbol,start_time=next_start,end_time=boundary,limit=500) if supports else fetch_1h(market,symbol)
                    pages.extend(rows or [])
                    closed=[r for r in rows or [] if int(r[0])+H<=boundary]
                    if not supports or not closed or max(int(r[0]) for r in closed)+H>=boundary:break
                    advance=max(int(r[0]) for r in closed)+H
                    if next_start is not None and advance<=next_start:break
                    next_start=advance
                meta=ingest_symbol_1h(cache,market=market,symbol=symbol,raw_klines=pages,target_close_boundary=boundary,contract_size=item.get('contractSize'))
                changed_opens=meta['changed_opens']
                with state_lock:written+=meta['written']
            coverage=cache.coverage(market=market,symbol=symbol,target=boundary)
            dirty_opens=[t for t in cache.pending_replay(market,symbol) if t+H<=boundary]
            if changed_opens is not None:changed_opens=sorted(set(changed_opens)|set(dirty_opens))
            four=fetch_background(item,boundary) if fetch_background else item.get('raw4h') or []
            evidence=fetch_evidence(item,boundary) if fetch_evidence else item.get('evidence') or {}
            symbol_events=store.list_events(market=market,symbol=symbol,full=False,revisions=False)
            replay={r['trigger_open_time'] for r in symbol_events if r.get('technical_confirmation') in ('pending','unknown')}
            for event in symbol_events:
                ot=event['trigger_open_time']
                if 'changed_timestamps' not in evidence or any(ot<t<=ot+2*H for t in evidence['changed_timestamps']) or any(ot<t<=ot+2*H for t in evidence.get('changed_cap_timestamps',[])) or any(background_revision_affects(ot,c) for c in item.get('_background_revisions',[])):replay.add(ot)
            replay.update(cursor.get('unresolved_opens',[]))
            candidates=[cursor.get('last_processed_boundary',boundary)-2*H]+list(replay)+list(changed_opens or [])
            candidates.extend(g['after'] for g in coverage['gaps'])
            read_start=max(coverage['first_open'] or 0,min(candidates)-168*H) if cursor else None
            bars=bars_from_cache(cache,market=market,symbol=symbol,start_open=read_start,end_open=boundary)
            if not bars or coverage['gaps']:missing.append(dict(market=market,symbol=symbol,reason='K线覆盖存在缺口',gaps=coverage['gaps']))
            # Compatibility injection only for offline fixtures; real adapter supplies raw4h.
            bg_memo={}
            def background(ot):
                boundary4h=((ot+H)//(4*H))*4*H
                if boundary4h not in bg_memo:bg_memo[boundary4h]=background_at(four,ot+H) if four else item.get('bg4h')
                return bg_memo[boundary4h]
            def price(t):return next((float(b['c']) for b in bars if b['open_time']+H==t),None)
            from b_repair_store import RepairStore
            history_caps=RepairStore(root).caps(market,symbol,item)
            def struct(t):
                caps=evidence.get('cap_samples',[])+history_caps+[item]
                eligible=[c for c in caps if ms(c.get('capObservedAt')) is not None and ms(c.get('capObservedAt'))<=t]
                cap=max(eligible,key=lambda c:ms(c['capObservedAt'])) if eligible else item
                return structure_at(t,oi_samples=evidence.get('oi_samples'),lsr_samples=evidence.get('lsr_samples'),cap=cap,oi_unit=evidence.get('oi_value_unit','usd'),price=price)
            reference=(lambda:fetch_reference(item,boundary)) if fetch_reference else None
            quote=(lambda:fetch_quote(item,boundary)) if fetch_quote else None
            metadata={k:item.get(k) for k in ('universe_scope','directory_retrieved_at','directory_version','directory_expected')}
            metadata.update(scan_id=round_id,revision=1)
            po=process_bars_in_order(root,market=market,symbol=symbol,bars_asc=bars,bg4h_for=background,structure_for=struct,target_close_boundary=boundary,reference_open=reference,observed=quote,observation_sink=lambda rows:_observations(root,market,symbol,boundary,rows,metadata),changed_opens=changed_opens,replay_opens=replay,cache_coverage=coverage)
            if po['new_events'] or po['confirmed'] or int(hashlib.sha256((market+'|'+symbol).encode()).hexdigest()[:8],16)%256==0:
                from tracking.case_library import capture_safe
                capture_safe(root,strategy='B',market=market,symbol=symbol,boundary_ms=boundary,
                    inputs=dict(bars_asc=bars,raw4h=four,evidence=evidence,contract=item,input_available_at_ms=int(time.time()*1000)),
                    provenance=dict(kind='real_B_collection',round_id=round_id,collection_started_ms=acquired,expected_not_inferred=True),
                    clock_iso=__import__('datetime').datetime.now(__import__('datetime').timezone.utc).isoformat())
            cache.acknowledge_replay(market,symbol,dirty_opens)
            if fetch_acknowledge:fetch_acknowledge(item,evidence)
            with state_lock:evaluable+=any(r["technical"]!="unknown" for r in po["observations"])
            with state_lock:evaluated+=1;ordered+=po['new_events']+po['confirmed']
            details.append(dict(market=market,symbol=symbol,started_at=acquired,retrieved_at=int(time.time()*1000),latest_boundary=bars[-1]['open_time']+H if bars else None,new_events=po['new_events'],new_forward=po['new_forward'],new_historical=po['new_historical'],new_confirmations=po['confirmed'],evidence_errors=evidence.get('errors'),source_cursors=evidence.get('source_cursors'),candle_coverage=coverage,coverage=store.cursor(market,symbol),loaded_bars=len(bars),continuation_required=bool(page_count==4 and coverage['last_open'] is not None and coverage['last_open']>(previous_last if previous_last is not None else -1) and coverage['last_open']+H<boundary)))
        except Exception as e:
            errors.append(dict(market=market,symbol=symbol,error=str(e)[:300]))
            # Keep a verified cache deficit even if acquisition failed before
            # the normal detail was published. Registration never fetches.
            try:
                details.append(dict(market=market,symbol=symbol,candle_coverage=cache.coverage(market=market,symbol=symbol,target=boundary),
                    coverage=store.cursor(market,symbol) or {},continuation_required=False,source_cursors={},
                    new_forward=0,new_historical=0,collection_error=True))
            except Exception:pass
        with state_lock:
            completed+=1
            if progress:progress(completed,len(symbols))
    with ThreadPoolExecutor(max_workers=4,thread_name_prefix="b-public") as pool:
        list(pool.map(one,symbols or []))
    status='failed' if not symbols or evaluated==0 else 'partial_success' if (round_metadata or {}).get('universe_fallback_reason') or errors or missing or evaluable<evaluated or any(d.get('evidence_errors') for d in details) else 'success'
    result=dict(skipped=False,status=status,target_close_boundary=boundary,symbols=len(symbols or []),written=written,evaluated=evaluated,ordered_updates=ordered,errors=errors,missing=missing,details=details,rule_version=observe.get('rule_version'),round_stats=dict(universe=len(symbols or []),monitored_symbols=evaluated,evaluated_symbols=evaluable,warmup_or_gap_symbols=evaluated-evaluable,unmonitored=len(symbols or [])-evaluated,errors=len(errors),target_close_boundary=boundary),published_at=int(time.time()*1000))
    result.update(round_metadata or {})
    result['continuation_required']=any(d['continuation_required'] for d in details)
    result['coverage_status']='incomplete' if missing or errors or any((d.get('coverage') or {}).get('unresolved_opens') or (d.get('coverage') or {}).get('evidence_unresolved_opens') or any(c.get('gaps') for c in (d.get('source_cursors') or {}).values()) for d in details) else 'recorded'
    if callable(request_count):result['request_count']=request_count()
    from request_telemetry import task_snapshot
    result['http_attempts']=task_snapshot()
    result['directory_sources']=[dict(market=m,version=next((i.get('directory_version') for i in symbols if i.get('market')==m),None),retrieved_at=next((i.get('directory_retrieved_at') for i in symbols if i.get('market')==m),None),scope=next((i.get('universe_scope') for i in symbols if i.get('market')==m),None)) for m in sorted({i['market'] for i in symbols})]
    all_events=store.list_events(full=False,revisions=False)
    events=[e for e in all_events if e.get('migration_status')!='legacy_unverified']
    result['round_stats']['legacy_unverified_records']=len(all_events)-len(events)
    result['round_id']=round_id
    result['round_stats'].update(technical_events=sum(e.get('technical_event')=='pass' for e in events),technical_confirmations=sum(e.get('technical_confirmation')=='pass' for e in events),complete_confirmations=sum(e.get('technical_confirmation')=='pass' and e.get('structure_gate')=='pass' for e in events),structure_fail=sum(e.get('structure_gate')=='fail' for e in events),structure_unknown=sum(e.get('structure_gate')=='unknown' for e in events),new_forward=sum(d['new_forward'] for d in details),new_historical=sum(d['new_historical'] for d in details),event_count_scope='累计稳定事件ID；合约分母见universe')
    # Additive recovery intent; original task status and source gaps stay intact.
    try:
        import tracking_service
        if tracking_service.enabled(root):
            from tracking.recovery_bridge import register_b_round
            register_b_round(tracking_service.writer(root),result)
    except Exception as exc:
        tracking_service.capture_error(root,exc)
        result['tracking_recovery_registration_error']=str(exc)[:1000]
    atomic_json(Path(root)/'runtime'/'b-rounds'/(result['round_id']+'.json'),result)
    atomic_json(Path(root)/'runtime'/'b-observe-last-round.json',result)
    return result
