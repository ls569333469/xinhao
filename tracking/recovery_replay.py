"""Local replay and explicit bridge. These functions never acquire sources."""
import json,hashlib
from .ledger import _canonical
from .recovery import Recovery,bump_conn
from .recovery_effects import prepare_step
from .tracking_engine import iso,digest,milliseconds

def bridge_need(recovery,legacy_job_key,canonical_job_key,*,consumer,rule_version,event_id=None,step_ms=14400000,clock_ms=0):
    """Retain legacy intent and link it to verified acquisition; no grant implied."""
    with recovery.transaction() as db:
        old=db.execute('SELECT * FROM tracking_recovery WHERE job_key=?',(legacy_job_key,)).fetchone()
        new=db.execute('SELECT * FROM tracking_recovery WHERE job_key=?',(canonical_job_key,)).fetchone()
        if not old or not new or not new['identity_json'] or old['contract']!=new['contract'] or old['source']!=new['source'] or old['start_ms']<new['start_ms'] or old['target_ms']>new['target_ms']:raise ValueError('recovery_incompatible_bridge')
        db.execute("CREATE TABLE IF NOT EXISTS recovery_bridges(legacy_job_key TEXT NOT NULL,canonical_job_key TEXT NOT NULL,consumer TEXT NOT NULL,rule_version TEXT NOT NULL,PRIMARY KEY(legacy_job_key,canonical_job_key,consumer,rule_version))")
        db.execute('INSERT OR IGNORE INTO recovery_bridges VALUES(?,?,?,?)',(legacy_job_key,canonical_job_key,consumer,rule_version))
        bump_conn(db,clock_ms)
    dependency=recovery.depend(canonical_job_key,consumer=consumer,rule_version=rule_version,event_id=event_id,
        start_ms=old['start_ms'],target_ms=old['target_ms'],step_ms=step_ms,clock_ms=clock_ms)
    # Transfer immutable original context, not today's row, across the verified
    # mapping. Legacy needs/contexts stay in place for old report reconstruction.
    with recovery.transaction() as db:
        contexts=db.execute('SELECT c.payload_json FROM recovery_dependencies d JOIN recovery_replay_contexts c ON c.dependency_id=d.dependency_id AND c.epoch=d.input_epoch WHERE d.job_key=? AND d.consumer=? AND d.rule_version=? AND d.event_id IS ? LIMIT 2',(legacy_job_key,consumer,rule_version,event_id)).fetchall()
    if len(contexts)>1:raise ValueError('bridge_context_ambiguous')
    if contexts:register_context(recovery,dependency,inputs=json.loads(contexts[0][0]),clock_ms=clock_ms)
    return dependency

def reopen_revision(recovery,job_key,*,affected_ms,configuration_version,clock_ms):
    """Explicit new revision epoch. Older chunks, effects and reports stay intact."""
    if not isinstance(configuration_version,str) or not configuration_version:raise ValueError('revision_configuration_required')
    with recovery.transaction() as db:
        j=db.execute('SELECT * FROM tracking_recovery WHERE job_key=?',(job_key,)).fetchone()
        if not j or not j['identity_json'] or type(affected_ms) is not int or not j['start_ms']<=affected_ms<=j['target_ms'] or (affected_ms-j['start_ms'])%j['period_ms']:raise ValueError('revision_window')
        if j['state']=='running':raise ValueError('revision_requires_no_active_lease')
        db.execute("UPDATE tracking_recovery SET epoch=epoch+1,next_ms=?,state='pending',archived_continuous_ms=NULL,evaluated_ms=NULL,next_retry_ms=NULL,error_code=NULL WHERE job_key=?",(affected_ms,job_key))
        # Conservative full consumer replay includes necessary indicator warmup.
        db.execute("UPDATE recovery_dependencies SET input_epoch=input_epoch+1,next_ms=start_ms,evaluated_ms=NULL,state=CASE WHEN state='not_required' THEN state ELSE 'pending' END,reason=CASE WHEN state='not_required' THEN reason ELSE 'source_revision_replay' END WHERE job_key=?",(job_key,))
        db.execute('INSERT INTO recovery_migrations VALUES(?,?)',('revision:'+job_key+':'+str(j['epoch']+1),_canonical(dict(affected_ms=affected_ms,configuration_version=configuration_version,as_of_ms=clock_ms))))
        bump_conn(db,clock_ms)

def mark_not_required(recovery,dependency_id,*,reason,clock_ms):
    """Consumer edge only. Raw quality and all other consumers are untouched."""
    with recovery.transaction() as db:
        d=db.execute('SELECT * FROM recovery_dependencies WHERE dependency_id=?',(dependency_id,)).fetchone()
        if not d or not d['event_id']:raise ValueError('not_required_needs_business_evidence')
        if reason=='technical_fail_proven' and d['consumer'].endswith('_strategy'):
            r=db.execute('SELECT payload_json FROM event_revisions WHERE event_id=? ORDER BY revision DESC LIMIT 1',(d['event_id'],)).fetchone()
            p=json.loads(r[0]) if r else {}
            if p.get('technical_status') not in ('void','confirm_failed','invalidated'):raise ValueError('technical_fail_not_proven')
        elif reason=='observation_terminal_proven' and d['consumer'].endswith('_observation'):
            r=db.execute('SELECT observation_status FROM sessions WHERE event_id=? ORDER BY rowid DESC LIMIT 1',(d['event_id'],)).fetchone()
            if not r or r[0]!='ended':raise ValueError('observation_terminal_not_proven')
        else:raise ValueError('not_required_reason_or_consumer')
        db.execute("UPDATE recovery_dependencies SET state='not_required',reason=? WHERE dependency_id=?",(reason,dependency_id));bump_conn(db,clock_ms)

def replay_step(ledger,dependency_id,*,inputs,clock_ms):
    """One exact root through actual A/B/C engines. No callbacks or network.

    Caller supplies frozen original context/warmup. Archive samples are merged
    below and the ledger verifies their actual use before the same-TX effect ACK.
    B strategy still consumes original B pipeline revisions; it never rebuilds B
    with C's structure rule or fabricates h+1.
    """
    step=prepare_step(ledger,dependency_id)
    if step is None:return dict(ok=True,pending=True,evaluation_acked=False)
    d=dict(ledger._read('SELECT d.*,j.contract,j.source FROM recovery_dependencies d JOIN tracking_recovery j USING(job_key) WHERE dependency_id=?',(dependency_id,))[0])
    market,symbol=d['contract'].split('|',1);boundary=step['boundary_ms']
    inp=json.loads(_canonical(inputs))
    rows=[]
    for ref in step['input_refs']:
        source=ledger._read('SELECT source FROM tracking_recovery WHERE job_key=?',(ref['job_key'],))[0][0]
        if source=='candles_1h':raise ValueError('original_1h_cache_and_B_changefeed_required')
        field={'oi':'oi_samples','lsr':'lsr_samples','cap':'caps','candles_4h':'raw_bars'}[source]
        by={s['open_ms' if field=='raw_bars' else 'effective_at_ms']:s for s in inp.get(field,[])}
        # Only the exact immutable refs prepared for this step may enter input.
        archived=ledger._read("""SELECT s.* FROM tracking_recovery_samples s WHERE job_key=? AND version=? AND effective_ms<=?
            AND NOT EXISTS(SELECT 1 FROM tracking_recovery_samples n WHERE n.job_key=s.job_key AND n.effective_ms=s.effective_ms AND (n.available_ms>s.available_ms OR (n.available_ms=s.available_ms AND n.version>s.version)))
            ORDER BY effective_ms DESC LIMIT 1500""",(ref['job_key'],ref['version'],boundary))
        if len(archived)>1499:raise ValueError('recovery_replay_window_page_required')
        rows.extend(archived)
        for r in archived:
            s=json.loads(r['payload_json'])
            if field=='raw_bars':
                b=s.get('bar')
                if not isinstance(b,dict):raise ValueError('recovery_candle_payload')
                by[b['open_ms']]=b
            else:by[s['effective_at_ms']]=dict(s,available_at_ms=r['available_ms'])
        inp[field]=[by[t] for t in sorted(by)]
    # Frozen context can include later roots as indicator/replay input, but an
    # individual effect may only evaluate the exact next boundary.
    inp['raw_bars']=[b for b in inp.get('raw_bars',[]) if b['open_ms']+14400000<=boundary]
    for key in ('oi_samples','lsr_samples','caps'):
        if key in inp:inp[key]=[s for s in inp[key] if s['effective_at_ms']<=boundary and s.get('available_at_ms',clock_ms+1)<=clock_ms]
    version='recovery-'+digest(step)
    if d['consumer']=='C_strategy':
        from .c_rebound_engine import CReboundEngine
        if d['event_id'] is None:
            from .c_recovery import recover_no_cycle
            result=recover_no_cycle(ledger,d,step,inp,clock_ms=clock_ms,source_version=version)
            if result is not None:return result
        return CReboundEngine(ledger).consume(market=market,symbol=symbol,raw_bars=inp['raw_bars'],
            oi_samples=inp.get('oi_samples',[]),lsr_samples=inp.get('lsr_samples',[]),caps=inp.get('caps',[]),
            target_close_ms=boundary,clock_iso=iso(clock_ms),source_version=version,
            historical_import=True,source_revision=True,recovery_step=step)
    if d['consumer']=='C_observation':
        from .observer import observe_contract
        from .c_rebound_rules import prepare_bars
        return observe_contract(ledger,market=market,symbol=symbol,bars=prepare_bars(inp['raw_bars'],target_close_ms=boundary),
            target_close_ms=boundary,clock_iso=iso(clock_ms),source_version=version,recovery_step=step)
    if d['consumer'] in ('A_strategy','A_observation'):
        from .a_tracking import ATracker
        row=(inp.get('published_rows') or {}).get(str(boundary),inp.get('row'))
        if not row or row.get('candleAsOf')!=boundary-1:raise ValueError('original_A_published_row_required_for_root')
        inp['row']=dict(row)
        if d['source']=='oi' and inp.get('oi_samples'):
            s=inp['oi_samples'][-1]
            if s['unit']=='USD' and boundary-300000<=s['effective_at_ms']<=boundary:
                inp['row'].update(oiUsd=s['value'],oiAsOf=s['effective_at_ms'])
        available=max([r['available_ms'] for r in rows]+[milliseconds(inp['available_at'])])
        return ATracker(ledger).drive_snapshot(row=inp['row'],raw_bars=inp['raw_bars'],clock_iso=iso(clock_ms),
            source_version=version,available_at=iso(available),historical_import=True,recovery_step=step)
    if d['consumer']=='B_observation':
        from .b_tracking import BTracker
        from .c_rebound_rules import prepare_bars
        return BTracker(ledger).observe(d['event_id'],prepare_bars(inp['raw_bars'],target_close_ms=boundary),
            target_close_ms=boundary,clock_iso=iso(clock_ms),source_version=version,recovery_step=step)
    if d['consumer']=='B_strategy':
        from .b_tracking import BTracker
        if 'source_revision_payload' not in inp:raise ValueError('original_B_pipeline_revision_required')
        return BTracker(ledger).consume_revision(inp['source_revision_payload'],clock_iso=iso(clock_ms),historical_import=True,recovery_step=step)
    raise ValueError('recovery_consumer_has_no_engine')

def register_context(recovery,dependency_id,*,inputs,clock_ms):
    """Persist reviewed original context. No new source or run authorization.

    Same epoch is immutable. A source correction needs an explicit new epoch;
    changing today's row must never rewrite a historical replay context.
    """
    if type(clock_ms) is not int or clock_ms<0:raise ValueError('replay_clock')
    if not isinstance(inputs,dict):raise ValueError('replay_context_object')
    payload=_canonical(inputs)
    if len(payload.encode())>4*1024*1024:raise ValueError('replay_context_page_required')
    for field,limit in [('raw_bars',1499),('oi_samples',6000),('lsr_samples',6000),('caps',6000)]:
        if not isinstance(inputs.get(field,[]),list) or len(inputs.get(field,[]))>limit:raise ValueError('replay_context_input_bound')
    sha=hashlib.sha256(payload.encode()).hexdigest()
    with recovery.transaction() as db:
        d=db.execute('SELECT input_epoch FROM recovery_dependencies WHERE dependency_id=?',(dependency_id,)).fetchone()
        if not d:raise ValueError('replay_dependency_missing')
        prior=db.execute('SELECT input_sha FROM recovery_replay_contexts WHERE dependency_id=? AND epoch=?',(dependency_id,d[0])).fetchone()
        if prior and prior[0]!=sha:raise ValueError('replay_context_immutable')
        if not prior:
            ready=db.execute('SELECT 1 FROM tracking_recovery_samples s JOIN recovery_dependencies d USING(job_key) WHERE d.dependency_id=? LIMIT 1',(dependency_id,)).fetchone()
            db.execute('INSERT INTO recovery_replay_contexts(dependency_id,epoch,input_sha,payload_json,created_ms,state) VALUES(?,?,?,?,?,?)',(dependency_id,d[0],sha,payload,clock_ms,'pending' if ready else 'awaiting_archive'))
            bump_conn(db,clock_ms)
    return dict(dependency_id=dependency_id,epoch=d[0],input_sha=sha)

def drain(ledger,*,clock_ms,limit=10,allow_step=None):
    """Bounded durable LOCAL-only consumer continuation after an explicit kick.

    Business effect+watermark are atomic in the ledger. Context receipt is a
    separate idempotent bookkeeping step: a crash after the effect is reconciled
    from the dependency cursor without evaluating or notifying twice.
    """
    if type(limit) is not int or not 1<=limit<=100:raise ValueError('replay_limit_1_100')
    recovery=Recovery(ledger);items=[]
    for _ in range(limit):
        if allow_step is not None and not allow_step():break
        with recovery.transaction() as db:
            r=db.execute("SELECT c.*,d.next_ms,d.target_ms,d.state AS dependency_state FROM recovery_replay_contexts c JOIN recovery_dependencies d ON d.dependency_id=c.dependency_id AND d.input_epoch=c.epoch WHERE c.state='pending' AND c.next_retry_ms<=? ORDER BY c.last_scheduled_ms,c.dependency_id,c.epoch LIMIT 1",(clock_ms,)).fetchone()
            if not r:break
            r=dict(r)
            db.execute('UPDATE recovery_replay_contexts SET last_scheduled_ms=?,attempts=attempts+1 WHERE dependency_id=? AND epoch=?',(clock_ms,r['dependency_id'],r['epoch']))
        result=None;error=None
        try:
            if hashlib.sha256(r['payload_json'].encode()).hexdigest()!=r['input_sha']:raise ValueError('replay_context_hash_changed')
            if r['dependency_state']=='not_required' or r['next_ms']>r['target_ms']:result=dict(ok=True,reconciled=True)
            else:result=replay_step(ledger,r['dependency_id'],inputs=json.loads(r['payload_json']),clock_ms=clock_ms)
            if not result.get('ok'):error=_canonical(result.get('errors') or result)[:2000]
        except Exception as exc:error=type(exc).__name__+':'+str(exc)[:1800]
        with recovery.transaction() as db:
            d=db.execute('SELECT * FROM recovery_dependencies WHERE dependency_id=?',(r['dependency_id'],)).fetchone()
            done=d['input_epoch']==r['epoch'] and (d['next_ms']>d['target_ms'] or d['state']=='not_required')
            waiting=not done and d['next_ms']==r['next_ms']
            retry=clock_ms+min(3600000,5000*2**min(r['attempts'],9)) if error or waiting else 0
            db.execute('UPDATE recovery_replay_contexts SET state=?,next_retry_ms=?,error=? WHERE dependency_id=? AND epoch=?',('done' if done else 'pending',retry,error or ('awaiting_complete_inputs' if waiting else None),r['dependency_id'],r['epoch']))
        items.append(dict(dependency_id=r['dependency_id'],ok=not bool(error),complete=done,pending=waiting,error=error,
            waiting_reason=(result or {}).get('waiting_reason'),missing_checks=(result or {}).get('missing_checks',[])))
    return dict(ok=all(x['ok'] for x in items),count=len(items),items=items,network_attempts=0)

def capture_gap_contexts_conn(db,gaps,message,*,clock_ms):
    """Producer transaction records a replayable need, including no-event C.

    Original message remains in inbox; bounded normalized context is frozen here
    so verified mapping/archival can wake the same local consumer after restart.
    No provider identity, grant, source success or network execution is inferred.
    """
    if not message:return
    p=message.get('payload') or {}
    for gap in gaps:
        if gap.get('status')!='open':continue
        job_key='gap:'+gap['gap_id']
        j=db.execute('SELECT * FROM tracking_recovery WHERE job_key=?',(job_key,)).fetchone()
        if not j:continue
        detail=gap.get('detail') or {};eid=detail.get('event_id')
        ev=db.execute('SELECT strategy FROM event_revisions WHERE event_id=? ORDER BY revision DESC LIMIT 1',(eid,)).fetchone() if eid else None
        strategy=ev[0] if ev else 'C' if detail.get('consumer')=='C_strategy' else None
        if not strategy:continue
        consumer=detail.get('consumer') or (strategy+'_strategy' if message.get('source')=='c_published_4h' and j['source']!='candles_4h' else strategy+'_observation')
        rule=detail.get('rule_version') or ('c-1015-v1.1' if consumer=='C_strategy' else 'tracking-observation-v1.1.0')
        if consumer not in ('A_observation','B_observation','C_observation','C_strategy'):continue
        inp={k:p[k] for k in ('raw_bars','oi_samples','lsr_samples','caps','row','available_at') if k in p}
        if 'raw_bars' not in inp:
            inp['raw_bars']=[{k:b[k] for k in ('open_ms','o','h','l','c','q','q_unit') if k in b} for b in p.get('bars',[])]
        if not inp.get('raw_bars') and strategy!='C':continue
        text=_canonical(inp)
        if len(text.encode())>4*1024*1024:
            db.execute("UPDATE tracking_recovery SET error_code='replay_context_page_required' WHERE job_key=?",(job_key,))
            db.execute('INSERT OR IGNORE INTO recovery_migrations VALUES(?,?)',('context-bound:'+job_key+':'+str(j['epoch']),_canonical(dict(reason='replay_context_page_required',original_inbox_key=message.get('key'),input_sha=hashlib.sha256(text.encode()).hexdigest()))))
            continue
        boundary=j['start_ms'];dep=hashlib.sha256(_canonical([job_key,consumer,rule,eid,boundary,boundary,14400000]).encode()).hexdigest()
        db.execute("INSERT OR IGNORE INTO recovery_dependencies(dependency_id,job_key,consumer,rule_version,event_id,start_ms,target_ms,step_ms,next_ms,input_epoch) VALUES(?,?,?,?,?,?,?,?,?,?)",(dep,job_key,consumer,rule,eid,boundary,boundary,14400000,boundary,j['epoch']))
        db.execute("INSERT OR IGNORE INTO recovery_replay_contexts(dependency_id,epoch,input_sha,payload_json,created_ms,state) VALUES(?,?,?,?,?,'awaiting_archive')",(dep,j['epoch'],hashlib.sha256(text.encode()).hexdigest(),text,clock_ms))

