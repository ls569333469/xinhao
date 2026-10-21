"""Local-only B replay from immutable recovery evidence through ORIGINAL B rules.

No task submission, no downloading, no 1H cache writes. Cross-DB effects use the
existing B changefeed; this continuation is not an acquisition authority/ACK.
"""
import hashlib
import json
from pathlib import Path
from .recovery import Recovery, recovered_inputs
from .ledger import _canonical

H=3600000


def structure_overlay(recovered, boundary, legacy):
    from b_evidence import structure_at
    from .c_rebound_rules import sample_at
    oi=sample_at(recovered.get('oi', []),boundary,'value',unit='USD')
    lsr=sample_at(recovered.get('lsr', []),boundary,'value',unit='ratio')
    cap=sample_at([s for s in recovered.get('cap', []) if s.get('trusted') is True],boundary,'value',lag=H,unit='USD')
    if not (oi or lsr or cap):
        return legacy
    old_oi=legacy.get('oi_sample'); old_lsr=legacy.get('lsr_sample')
    # Existing B result already carries the proven USD value. Do not multiply a
    # COIN value twice or infer a conversion from today's contract metadata.
    if oi:
        o=dict(timestamp=oi['effective_at_ms'],sumOpenInterestValue=oi['value'],
            _first_retrieved_at=oi['available_at_ms'],_recovery_sample=oi)
    elif legacy.get('oi_quality')=='pass' and old_oi and legacy.get('oi_usd') is not None:
        o=dict(old_oi,sumOpenInterestValue=legacy['oi_usd'])
    else:
        o=None
    l=dict(timestamp=lsr['effective_at_ms'],longShortRatio=lsr['value'],
        _first_retrieved_at=lsr['available_at_ms'],_recovery_sample=lsr) if lsr else old_lsr
    c=dict(capUsd=cap['value'],capTrusted=True,capObservedAt=cap['effective_at_ms'],
        capFirstRetrievedAt=cap['available_at_ms'],capSource=cap.get('provider'),capId=cap.get('asset_id')) if cap else legacy.get('cap')
    return structure_at(boundary,oi_samples=[o] if o else [],lsr_samples=[l] if l else [],cap=c,oi_unit='usd')


def schema(db):
    db.execute("CREATE TABLE IF NOT EXISTS recovery_b_replays(job_key TEXT NOT NULL,version TEXT NOT NULL,contract TEXT NOT NULL,start_ms INTEGER NOT NULL,target_ms INTEGER NOT NULL,next_ms INTEGER NOT NULL,state TEXT NOT NULL DEFAULT 'pending',error TEXT,next_retry_ms INTEGER NOT NULL DEFAULT 0,PRIMARY KEY(job_key,version))")
    db.execute('CREATE INDEX IF NOT EXISTS recovery_b_replay_due ON recovery_b_replays(state,next_retry_ms,job_key,version)')
    db.execute('CREATE INDEX IF NOT EXISTS recovery_b_replay_contract ON recovery_b_replays(contract,state)')


def discover(ledger, clock_ms, root=None):
    recovery=Recovery(ledger)
    from market_candle_cache import MarketCandleCache
    from b_accumulation_store import BAccumulationStore
    with recovery.transaction() as db:
        schema(db)
        rows=db.execute("SELECT j.job_key,c.version,j.contract,j.source,j.start_ms,j.target_ms FROM tracking_recovery_chunks c JOIN tracking_recovery j USING(job_key) WHERE NOT EXISTS(SELECT 1 FROM recovery_b_replays b WHERE b.job_key=c.job_key AND b.version=c.version) ORDER BY c.available_at_ms,c.version LIMIT 100").fetchall()
        for row in rows:
            db.execute("UPDATE recovery_b_replays SET state='pending',next_retry_ms=0 WHERE contract=? AND state='evaluated_incomplete'",(row['contract'],))
            # A candle receipt must reach original storage before coverage can
            # constrain the affected business range (also restart-idempotent).
            market,symbol=row['contract'].split('|',1)
            bm='USDⓈ-M' if market=='USDT-M' else market
            start=max(0,row['start_ms']-2*H)
            target=row['target_ms']+(4*H if row['source']=='candles_4h' else 168*H if row['source']=='candles_1h' else 0)
            applicable=True
            if root is not None:
                # A 1499-root background warmup does not require a new scan of
                # every negative B root since the oldest cached 1H bar. Revisit
                # actual unresolved/event roots whose frozen inputs can change.
                if row['source']!='candles_1h' and (Path(root)/'runtime/b-events-v3.sqlite3').is_file():
                    store=BAccumulationStore(root);cursor=store.cursor(bm,symbol) or {}
                    known=set(cursor.get('unresolved_opens',[])+cursor.get('business_required_evidence_opens',[]))
                    known.update(e['trigger_open_time'] for e in store.list_events(market=bm,symbol=symbol,full=False,revisions=False))
                    affected=sorted(t for t in known if start<=t<target)
                    if affected:start,target=affected[0],affected[-1]+2*H
                    else:applicable=False
                cache=MarketCandleCache(root,readonly=True)
                if cache.db_path.is_file():
                    cc=cache.coverage(market=bm,symbol=symbol,target=target)
                    if cc['first_open'] is not None:
                        start=max(start,cc['first_open']+168*H)
                        target=min(target,cc['last_open']+H)
            db.execute('INSERT OR IGNORE INTO recovery_b_replays(job_key,version,contract,start_ms,target_ms,next_ms) VALUES(?,?,?,?,?,?)',
                (row['job_key'],row['version'],row['contract'],start,target,start))
            if not applicable:
                db.execute("UPDATE recovery_b_replays SET state='not_applicable',error='no_registered_B_root_depends_on_this_receipt' WHERE job_key=? AND version=?",(row['job_key'],row['version']))
        return len(rows)


def drain(root, ledger, *, clock_ms, limit=2):
    if type(limit) is not int or not 1<=limit<=10:
        raise ValueError('b_replay_page_budget')
    from market_candle_cache import MarketCandleCache,read_json_maybe_gzip
    from b_observe_evaluate import bars_from_cache
    from b_evidence import background_at,structure_at,ms
    from b_process_order import process_bars_in_order
    from b_accumulation_store import BAccumulationStore
    from b_observe_runner import _observations
    from b_repair_store import RepairStore
    recovery=Recovery(ledger)
    from .candle_handoff import deliver
    with recovery.transaction() as db:
        receipts=[tuple(r) for r in db.execute("SELECT c.job_key,c.version FROM tracking_recovery_chunks c JOIN tracking_recovery j USING(job_key) WHERE j.source='candles_1h' AND NOT EXISTS(SELECT 1 FROM sqlite_master WHERE type='table' AND name='recovery_candle_deliveries') LIMIT 10")]
        db.execute('CREATE TABLE IF NOT EXISTS recovery_candle_deliveries(job_key TEXT,version TEXT,payload_sha TEXT,delivered_ms INTEGER,PRIMARY KEY(job_key,version))')
        receipts=[tuple(r) for r in db.execute("SELECT c.job_key,c.version FROM tracking_recovery_chunks c JOIN tracking_recovery j USING(job_key) WHERE j.source='candles_1h' AND NOT EXISTS(SELECT 1 FROM recovery_candle_deliveries d WHERE d.job_key=c.job_key AND d.version=c.version) ORDER BY c.available_at_ms LIMIT 10")]
    for job,version in receipts:
        try:deliver(root,ledger,job,version,clock_ms=clock_ms)
        except Exception:pass  # replay persists the precise delivery error below
    discover(ledger,clock_ms,root); results=[]
    for _ in range(limit):
        with recovery.transaction() as db:
            row=db.execute("SELECT * FROM recovery_b_replays WHERE state='pending' AND next_retry_ms<=? ORDER BY next_retry_ms,job_key,version LIMIT 1",(clock_ms,)).fetchone()
            if not row:break
            row=dict(row)
        try:
            market,symbol=row['contract'].split('|',1)
            bmarket='USDⓈ-M' if market=='USDT-M' else market
            folder=Path(root)/'runtime/b-source'/hashlib.sha256((bmarket+'|'+symbol).encode()).hexdigest()
            structure_path=folder/'structure.json'; four_path=folder/'4h.json'
            if not (Path(root)/'runtime/b-events-v3.sqlite3').is_file():
                raise ValueError('B_original_source_not_initialized')
            evidence={}
            if structure_path.is_file():
                if structure_path.stat().st_size>16*1024*1024:raise ValueError('B_source_page_required')
                evidence=json.loads(structure_path.read_bytes())
            raw4h=[]
            if four_path.is_file():
                if four_path.stat().st_size>16*1024*1024:raise ValueError('B_background_page_required')
                raw4h=read_json_maybe_gzip(four_path).get('raw',[])
            stop=min(row['target_ms'],row['next_ms']+64*H)
            from .candle_handoff import deliver
            deliver(root,ledger,row['job_key'],row['version'],clock_ms=clock_ms)
            cache=MarketCandleCache(root,readonly=True)
            bars=bars_from_cache(cache,market=bmarket,symbol=symbol,start_open=max(0,row['next_ms']-168*H),end_open=stop)
            if not bars or len(bars)<169:raise ValueError('B_replay_1h_warmup_missing')
            # Original evaluator records missing/interrupted roots; a single gap
            # does not prevent independent later roots being evaluated.
            restored=recovered_inputs(ledger,market=market,symbol=symbol,start_ms=max(0,((row['next_ms']//(4*H))-1499)*4*H),target_ms=stop,acquisition_job_key=row['job_key'])
            if restored.get('candles_4h'):
                by={int(b[0]):b for b in raw4h}
                for sample in restored['candles_4h']:
                    b=sample.get('bar')
                    if b and b['open_ms']+4*H==sample['effective_at_ms']:
                        by[b['open_ms']]=[b['open_ms'],b['o'],b['h'],b['l'],b['c'],0,b['open_ms']+4*H-1,b['q']]
                raw4h=[by[t] for t in sorted(by)]
            caps=evidence.get('cap_samples',[])+RepairStore(root).caps(bmarket,symbol,{})
            def structure(t):
                eligible=[c for c in caps if ms(c.get('capObservedAt')) is not None and ms(c.get('capObservedAt'))<=t]
                cap=max(eligible,key=lambda c:ms(c['capObservedAt'])) if eligible else {}
                old=structure_at(t,oi_samples=evidence.get('oi_samples'),lsr_samples=evidence.get('lsr_samples'),cap=cap,
                    oi_unit=evidence.get('oi_value_unit','base_asset' if market=='COIN-M' else 'usd'),
                    price=lambda at:next((b['c'] for b in bars if b['open_time']+H==at),None))
                return structure_overlay(restored,t,old)
            result=process_bars_in_order(root,market=bmarket,symbol=symbol,bars_asc=bars,
                target_close_boundary=stop,bg4h_for=lambda ot:background_at(raw4h,ot+H),structure_for=structure,
                changed_opens=[],replay_opens=list(range(row['next_ms'],stop,H)),cache_coverage=cache.coverage(market=bmarket,symbol=symbol,target=max(stop,(BAccumulationStore(root).cursor(bmarket,symbol) or {}).get('last_processed_boundary',stop))),
                observation_sink=lambda rows:_observations(root,bmarket,symbol,stop,rows,dict(recovery_job=row['job_key'],historical_replay=True)))
            # Evaluation incompleteness stays visible; never claim acquisition == qualification.
            cursor=BAccumulationStore(root).cursor(bmarket,symbol) or {}
            pending=[t for t in set(cursor.get('unresolved_opens',[])+cursor.get('evidence_unresolved_opens',[])) if row['start_ms']<=t<row['target_ms']]
            complete=stop>=row['target_ms']
            with recovery.transaction() as db:
                db.execute('UPDATE recovery_b_replays SET next_ms=?,state=?,error=NULL,next_retry_ms=0 WHERE job_key=? AND version=? AND next_ms=?',
                    (stop,'evaluated_incomplete' if complete and pending else 'evaluated' if complete else 'pending',row['job_key'],row['version'],row['next_ms']))
                recovery.bump(db,clock_ms)
            results.append(dict(contract=row['contract'],job_key=row['job_key'],evaluated_to_ms=stop,
                pending_roots=len(pending),source_changefeed_ack_separate=True,evaluation_acked=False))
        except Exception as exc:
            with recovery.transaction() as db:
                db.execute('UPDATE recovery_b_replays SET error=?,next_retry_ms=? WHERE job_key=? AND version=?',
                    (type(exc).__name__+':'+str(exc)[:1000],clock_ms+300000,row['job_key'],row['version']))
            results.append(dict(contract=row['contract'],error=str(exc)[:1000]))
    return dict(ok=all('error' not in item for item in results),count=len(results),items=results,network_attempts=0)

