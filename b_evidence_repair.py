"""Targeted B evidence recovery. Never submits a market scan or changes A/B rules."""
import json,time,hashlib
from pathlib import Path
from b_repair_store import RepairStore,missing_evidence,H
from b_accumulation_store import BAccumulationStore
from b_evidence import ms,structure_at,background_at
from durable_io import protection,atomic_json,read_bytes_shared

LABELS={'cap':'缺同期市值','oi':'缺同期OI','lsr':'缺同期多空比','technical_h':'事件技术数据不完整','technical_h1':'确认技术数据不完整'}

def decision_inputs(value):
    """Transport IDs/retrieval times or another invalid cap are not new evidence."""
    if isinstance(value,list):return [decision_inputs(v) for v in value]
    if not isinstance(value,dict):return value
    skip={'block_id','payload_hash','capRetrievedAt','capFirstRetrievedAt','capQualityStatus'}
    if value.get('cap_quality')=='unknown':skip.add('cap')
    return {k:decision_inputs(v) for k,v in value.items() if k not in skip and not k.startswith('_')}

def repair_presentation(event,job=None):
    missing=missing_evidence(event)
    labels=list(dict.fromkeys(LABELS.get(k.split(':')[-1],k) for k in missing))
    return dict(missing=missing,labels=labels,state=(job or {}).get('state','not_queued'),
                attempts=(job or {}).get('attempts',0),next_retry_at=(job or {}).get('next_try'),
                last_error=(job or {}).get('last_error'))

class Budget:
    def __init__(self,request=None,max_requests=6,max_seconds=90):
        self.calls=0;self.deadline=time.monotonic()+max_seconds;self.limit=max_requests;self.request=request
    def __call__(self,url,params):
        if self.calls>=self.limit or time.monotonic()+16>self.deadline:raise RuntimeError('repair_request_budget_exhausted')
        self.calls+=1
        if self.request:return self.request(url,params)
        from scanner import request
        return request(url,params,attempts=1,max_wait=min(20,max(0,self.deadline-time.monotonic()-16)))

def _merge_statistics(root,item,event,evidence,now,request):
    """Request the actual missing event/confirmation boundaries, not all cached history."""
    from b_1h_fetch import fetch_oi_lsr_1h
    from source_resume import missing_ranges
    from b_contemporaneous import pick_sample_at_or_before
    changed=set(evidence.get('pending_revision_timestamps',[]));errors=[];blocked=False
    bounds=[event['trigger_open_time']+H,event['trigger_open_time']+2*H]
    for source in ('oi','lsr'):
        key=source+'_samples';rows={int(r['timestamp']):r for r in evidence.get(key,[])}
        required=[t for t in bounds if pick_sample_at_or_before(t,list(rows.values()))[1]!='pass']
        needed=[t for t in required if t>=now-30*24*H]
        if len(needed)!=len(required):blocked=True;errors.append(source+':supplier_history_window_exceeded')
        if needed:
            def safe(url,params):
                try:return request(url,params),None
                except (OSError,ValueError,RuntimeError) as exc:return None,str(exc)
            value=fetch_oi_lsr_1h(item['market'],item['symbol'],pair=item.get('pair'),sources=(source,),start_time=min(needed)-300_000,end_time=max(needed),requester=safe)
            if value['errors']:errors.append(source+':'+str(value['errors']))
            for row in value[key]:
                t=int(row['timestamp'])
                if not any(0<=boundary-t<=300_000 for boundary in needed):continue
                old=rows.get(t);bare={k:v for k,v in (old or {}).items() if not k.startswith('_')}
                raw={k:v for k,v in row.items() if not k.startswith('_')}
                if old and bare!=raw:evidence.setdefault(key+'_revisions',[]).append(old)
                if bare!=raw:
                    rows[t]=dict(raw,_first_retrieved_at=old.get('_first_retrieved_at',now) if old else now);changed.add(t)
            if any(pick_sample_at_or_before(t,list(rows.values()))[1]!='pass' for t in needed):errors.append(source+':source_returned_no_valid_sample')
        evidence[key]=[rows[t] for t in sorted(rows)]
        prior=evidence.setdefault('source_cursors',{}).get(source,{})
        start=prior.get('coverage_start',min(rows,default=min(bounds)));target=max(prior.get('requested_through',0),max(bounds))
        gaps=missing_ranges(rows,start,target)
        # Keep old error metadata, but recompute actual gaps after a targeted recovery.
        evidence['source_cursors'][source]=dict(prior,coverage_start=start,requested_through=target,
            archived_contiguous_through=gaps[0]['start']-H if gaps else target,latest_sample=max(rows,default=None),
            fetched_through=max(filter(lambda t:t is not None,[prior.get('fetched_through'),max(rows,default=None)]),default=None),
            repair_attempted_at=now,next_retry_at=now+H if gaps else None,
            gaps=[dict(g,status='unrecoverable' if g['end']<=now-30*24*H else 'pending',reason='supplier_history_window_exceeded' if g['end']<=now-30*24*H else 'not_returned_or_budget') for g in gaps])
    evidence['pending_revision_timestamps']=sorted(changed);evidence['changed_timestamps']=sorted(changed)
    return errors,blocked

def repair_one(root,job,now,request):
    from b_observe_evaluate import bars_from_cache,calculate_event
    from market_candle_cache import MarketCandleCache,read_json_maybe_gzip
    from b_1h_fetch import fetch_1h_klines
    from b_1h_ingest import ingest_symbol_1h
    root=Path(root);store=BAccumulationStore(root)
    event=store.get_event(job['event_id']) if hasattr(store,'get_event') else next((r for r in store.list_events(market=job['market'],symbol=job['symbol']) if r['event_id']==job['event_id']),None)
    if not event:return dict(missing=[],error='event_no_longer_exists',receipt={'event_missing':True})
    queue=RepairStore(root);ot=event['trigger_open_time'];target=ot+2*H
    if target>now:return dict(missing=missing_evidence(event),error='confirmation_hour_not_closed',blocked=False)
    # Read the preserved directory, without fetching or rolling its market boundary.
    directory=root/'runtime/b-observe-universe.json'
    doc=json.loads(read_bytes_shared(directory)) if directory.exists() else {}
    item=next((r for r in doc.get('symbols',[]) if (r.get('market'),r.get('symbol'))==(job['market'],job['symbol'])),dict(market=job['market'],symbol=job['symbol']))
    folder=root/'runtime/b-source'/hashlib.sha256((item['market']+'|'+item['symbol']).encode()).hexdigest()
    path=folder/'structure.json';evidence=json.loads(read_bytes_shared(path)) if path.exists() else {}
    evidence.setdefault('oi_value_unit','base_asset' if item['market'] in ('COIN-M','COIN_M') else 'usd')
    errors=[];blocked=False;cap_blocked=False;bg_blocked=False
    try:
        new_errors,blocked=_merge_statistics(root,item,event,evidence,now,request);errors.extend(new_errors)
    except (OSError,ValueError,RuntimeError) as exc:errors.append(str(exc))
    caps=evidence.get('cap_samples',[])+queue.caps(item['market'],item['symbol'],item)+[item]
    bounds=[ot+H,ot+2*H]
    needed=[t for t in bounds if not any(c.get('capTrusted') is True and ms(c.get('capObservedAt')) is not None and 0<=t-ms(c['capObservedAt'])<=H for c in caps)]
    if needed:
        from b_cap_history import fetch_historical
        response=fetch_historical(root,item,needed,now,request)
        cap_blocked=response['blocked']
        blocked=blocked or response['blocked']
        if response.get('reason'):errors.append('cap:'+response['reason'])
        caps+=queue.caps(item['market'],item['symbol'],item)
    evidence['cap_samples']=[c for c in caps if c.get('capTrusted') is True and ms(c.get('capObservedAt')) is not None]
    evidence['retrieved_at']=now
    # Sources persist before event commit. Dirty keys remain until the normal B pipeline
    # has replayed all other affected events; never acknowledge just one event globally.
    with protection(root):atomic_json(path,evidence)
    cache=MarketCandleCache(root,readonly=True)
    bars=bars_from_cache(cache,market=item['market'],symbol=item['symbol'],start_open=ot-168*H,end_open=ot+3*H)
    expected=set(range(ot-168*H,target,H));present={r['open_time'] for r in bars}
    if not expected<=present:
        try:
            start=min(expected-present)
            raw=fetch_1h_klines(item['market'],item['symbol'],start_time=start,end_time=target,requester=lambda u,p:(request(u,p),None))
            ingest_symbol_1h(MarketCandleCache(root),market=item['market'],symbol=item['symbol'],raw_klines=raw,target_close_boundary=target,contract_size=item.get('contractSize'))
            bars=bars_from_cache(cache,market=item['market'],symbol=item['symbol'],start_open=ot-168*H,end_open=ot+3*H)
        except (OSError,ValueError,RuntimeError) as exc:errors.append('candles:'+str(exc))
    index=next((i for i,b in enumerate(bars) if b['open_time']==ot),None)
    if index is None:return dict(missing=['technical_h'],error='; '.join(errors+['event_candle_missing']),blocked=blocked)
    backgrounds={ot+H:event.get('bg4h'),ot+2*H:event.get('bg4h_h1')}
    if not all(backgrounds.values()):
        bgpath=folder/'4h.json'
        raw4h=read_json_maybe_gzip(bgpath).get('raw',[]) if bgpath.exists() else []
        for boundary in backgrounds:
            if not backgrounds[boundary]:backgrounds[boundary]=background_at(raw4h,boundary)
        if not all(backgrounds.values()):
            try:
                # A historical event must never replace today's 4H source file with
                # a window ending at its old boundary. Keep the acquired old inputs
                # inside the immutable event background instead.
                rows4h={int(r[0]):r for r in raw4h}
                start=((ot+H)//(4*H)-1499)*4*H
                for _ in range(4):
                    if start+4*H>target:break
                    page=fetch_1h_klines(item['market'],item['symbol'],interval='4h',start_time=start,end_time=target,limit=500,requester=lambda u,p:(request(u,p),None))
                    closed=[r for r in page if len(r)>7 and int(r[0])+4*H<=target and int(r[6])==int(r[0])+4*H-1]
                    if not closed:break
                    rows4h.update({int(r[0]):r for r in closed})
                    advance=max(int(r[0]) for r in closed)+4*H
                    if advance<=start:break
                    start=advance
                raw4h=[rows4h[t] for t in sorted(rows4h)]
                for boundary in backgrounds:
                    if not backgrounds[boundary]:backgrounds[boundary]=background_at(raw4h,boundary)
            except (OSError,ValueError,RuntimeError) as exc:errors.append('background:'+str(exc))
        if not all(backgrounds.values()):errors.append('background_4h_missing_or_insufficient_history');bg_blocked=True
    def structure(t):
        candidates=[c for c in caps if ms(c.get('capObservedAt')) is not None and ms(c['capObservedAt'])<=t]
        cap=max(candidates,key=lambda c:ms(c['capObservedAt'])) if candidates else {}
        return structure_at(t,oi_samples=evidence.get('oi_samples'),lsr_samples=evidence.get('lsr_samples'),cap=cap,
            oi_unit=evidence.get('oi_value_unit','usd'),price=lambda s:next((b['c'] for b in bars if b['open_time']+H==s),None))
    reference=dict(open_time=ot+2*H,price=event.get('P_theory')) if event.get('P_theory') is not None else None
    payload=calculate_event(bars,index,target=target,background=backgrounds.get,structure=structure,reference_open=reference,observed=event.get('observed_quote'))
    cursor=store.cursor(item['market'],item['symbol'])
    if cursor:
        if payload['technical_event']!='unknown':cursor['unresolved_opens']=[t for t in cursor.get('unresolved_opens',[]) if t!=ot]
        if payload['structure_gate'] not in ('unknown','pending'):cursor['evidence_unresolved_opens']=[t for t in cursor.get('evidence_unresolved_opens',[]) if t!=ot]
    # Event/cursor/outbox atomic; queue acknowledgement follows. A crash between them
    # replays the same semantic fingerprint without duplicate revision/notification.
    compare=[k for k in payload if k not in ('input_fingerprint','target_close_boundary')]
    changed=decision_inputs({k:event.get(k) for k in compare})!=decision_inputs({k:payload[k] for k in compare})
    updated=store.commit_symbol(item['market'],item['symbol'],[(ot,payload)],cursor,None)[0] if changed else event
    missing=missing_evidence(updated) if updated.get('structure_gate')!='fail' and updated.get('technical_event')!='fail' and updated.get('technical_confirmation')!='fail' else []
    def source_blocked(key):
        if key.startswith('technical_'):return bg_blocked
        hour,source=key.split(':');boundary=ot+(H if hour=='h' else 2*H)
        return cap_blocked if source=='cap' else boundary<now-30*24*H
    return dict(missing=missing,error='; '.join(errors) or None,blocked=bool(missing) and all(source_blocked(k) for k in missing),
        receipt=dict(event_id=updated['event_id'],revision=updated['event_revision'],technical_confirmation=updated.get('technical_confirmation'),structure_gate=updated.get('structure_gate'),input_fingerprint=updated.get('input_fingerprint')))

def run_repairs(root,now,*,request=None,max_events=2,max_requests=6):
    from b_cap_history import import_local
    queue=RepairStore(root);queue.initialize();import_local(root,now)
    budget=Budget(request,max_requests=max_requests);results=[]
    for _ in range(max_events):
        if not queue.enabled():break
        job=queue.claim(now)
        if not job:break
        try:result=repair_one(root,job,now,budget)
        except Exception as exc:result=dict(missing=json.loads(job['missing']),error=str(exc)[:500])
        queue.finish(job,now,**result);results.append(dict(event_id=job['event_id'],**result))
        if budget.calls>=max_requests:break
    summary=queue.summary()
    unresolved=sum(n for state,n in summary['counts'].items() if state!='resolved')
    return dict(status='partial_success' if unresolved else 'success',requests=budget.calls,processed=len(results),details=results,queue=summary)
