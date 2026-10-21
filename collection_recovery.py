"""Explicit saved-policy recovery in TaskService's existing single data lane."""
from pathlib import Path
import hashlib,json,time
from tracking.recovery_control import RecoveryDenied

def register_directory(root,symbols,clock_ms):
    """Current identity evidence does not certify pre-listing/historical multipliers."""
    from durable_io import atomic_json
    from tracking.live_source import CONVERSION
    path=Path(root)/'runtime/tracking-source-registry.json'
    old=json.loads(path.read_text(encoding='utf-8-sig')) if path.is_file() else dict(schema='tracking-source-registry-v3',contracts={})
    contracts=old['contracts'];changed=False
    for row in symbols:
        if row.get('universe_scope')!='trading_directory' or not row.get('directory_version'):continue
        market='USDT-M' if row.get('market')=='USDⓈ-M' else row.get('market')
        if market not in ('USDT-M','COIN-M'):continue
        contract=market+'|'+row['symbol'];prior=contracts.get(contract,{})
        # A current inverse multiplier cannot prove older history. Existing
        # verified historical identity is retained instead of widening its range.
        if market=='COIN-M':
            same=prior.get('verified') is True and prior.get('pair')==row.get('pair') and prior.get('contract_size')==row.get('contractSize') and prior.get('quote_asset')==row.get('quoteAsset')
            start=prior['valid_from_ms'] if same else clock_ms//3600000*3600000
        else:start=int(row.get('onboardDate') or clock_ms)
        meta=dict(verified=True,identity_evidence='exchangeInfo:'+row['directory_version'],
                  quote_asset=row.get('quoteAsset'),pair=row.get('pair'),
                  valid_from_ms=start,valid_to_ms=clock_ms,
                  history_policy=dict(window_ms=30*24*3600000,version='binance-statistics-30d-v1'),
                  contract_size=row.get('contractSize'),oi_conversion=CONVERSION)
        if prior.get('cap'):meta['cap']=prior['cap']
        if prior and prior.get('verified') is True and market=='USDT-M':
            meta['valid_from_ms']=min(meta['valid_from_ms'],prior.get('valid_from_ms',start))
        contracts[contract]=meta;changed=True
    if changed:atomic_json(path,old)

def start_slice(host):
    import threading
    if host.stop_event.is_set():return
    with host.lock:
        worker=getattr(host,'_collection_worker',None)
        if worker is not None and worker.is_alive():return
        if time.monotonic()<getattr(host,'_collection_next_tick',0):return
        def work():
            try:
                result=tick(host)
                if result and result.get('ok') is False:
                    host.subsystem_errors['collection']=result.get('error','recovery_failed')
                elif result and not result.get('deferred') and not result.get('disabled'):
                    errors=(result.get('effects') or {}).get('items',[])
                    failures=[x.get('error') for x in errors if x.get('error')]
                    if failures:host.subsystem_errors['replay']=failures[-1]
                    elif errors:host.subsystem_errors.pop('replay',None)
                    host.subsystem_errors.pop('collection',None)
            except Exception as exc:
                host.subsystem_errors['collection']=type(exc).__name__+': '+str(exc)[:220]
            finally:host._collection_next_tick=time.monotonic()+2
        host._collection_worker=threading.Thread(target=work,name='collection-recovery-slice',daemon=True)
        host._collection_worker.start()



def tick(host):
    from collection_policy import read
    cfg=read(host.root)
    if not cfg['recoveryEnabled'] or host.stop_event.is_set():return dict(disabled=True)
    import tracking_service as service
    if not service.enabled(host.root):return dict(disabled=True,reason='tracking_disabled')
    # The scheduler and manual submissions see this exact same lane reservation.
    lane='collection-recovery'
    with host.lock,host._db() as db:
        if any(k not in ('ai_research','ai_research_batch_retry') for k in host.active_tasks.values()):return dict(deferred=True)
        if db.execute("SELECT 1 FROM tasks WHERE status IN ('queued','running') AND kind NOT IN ('ai_research','ai_research_batch_retry') LIMIT 1").fetchone():return dict(deferred=True)
        host.active_tasks[lane]='tracking_recovery_runtime'
        version=db.execute('SELECT version FROM settings WHERE id=1').fetchone()[0]
    try:return run(host,version,cfg)
    except RecoveryDenied as exc:
        if str(exc) in ('collection_yield_to_scan','collection_paused_before_send','collection_slice_deadline'):
            return dict(deferred=True,reason=str(exc))
        raise
    except Exception as exc:
        host.subsystem_errors['collection']='自动补齐: '+type(exc).__name__+':'+str(exc)[:200]
        return dict(ok=False,error=str(exc)[:300])
    finally:
        with host.lock:host.active_tasks.pop(lane,None)

def run(host,version,cfg):
    from tracking.runtime import registry
    from tracking.recovery import Recovery
    from tracking.recovery_bridge import bridge_page
    from tracking.recovery_control import create_grant,RecoveryDenied
    from tracking.live_source import LiveHistoricalAdapter
    from tracking.b_recovery import drain
    import tracking_service as service
    root=host.root;ledger=service.writer(root);recovery=Recovery(ledger);at=int(time.time()*1000)
    deadline=time.monotonic()+20
    def allowed(job=None):
        state=host.settings()
        if host.stop_event.is_set() or state['version']!=version or not state['collection']['recoveryEnabled']:raise RecoveryDenied('collection_paused_before_send')
        if time.monotonic()>=deadline:raise RecoveryDenied('collection_slice_deadline')
        with host.lock,host._db() as db:
            if db.execute("SELECT 1 FROM tasks WHERE status='queued' AND kind NOT IN ('ai_research','ai_research_batch_retry') LIMIT 1").fetchone():raise RecoveryDenied('collection_yield_to_scan')
    with recovery.transaction() as db:
        # One persisted lease also fences an accidentally duplicated host.
        db.execute('CREATE TABLE IF NOT EXISTS collection_recovery_lease(slot INTEGER PRIMARY KEY,owner TEXT,until_ms INTEGER)')
        lease=db.execute('SELECT * FROM collection_recovery_lease WHERE slot=1').fetchone()
        if lease and lease['until_ms']>at:return dict(deferred=True,reason='recovery_lease_busy')
        import uuid
        owner=uuid.uuid4().hex
        db.execute('INSERT OR REPLACE INTO collection_recovery_lease VALUES(1,?,?)',(owner,at+120000))
        db.execute('CREATE TABLE IF NOT EXISTS collection_grants(scope_key TEXT PRIMARY KEY,grant_id TEXT NOT NULL)')
    try:
        allowed();recovery.set_enabled(True,clock_ms=at)
        from collection_local import sync_cap_identities,drain_local
        identities=sync_cap_identities(root)
        after=ledger.get_cursor('collection_bridge_after') or ''
        bridge=bridge_page(ledger,registry(root),after=after,limit=50,clock_ms=at)
        with recovery.transaction() as db:
            db.execute("INSERT INTO cursors VALUES('collection_bridge_after',?,?) ON CONFLICT(cursor_name) DO UPDATE SET cursor_value=excluded.cursor_value,updated_at_utc=excluded.updated_at_utc",(bridge['next_cursor'] or '',service.now()))
        local=drain_local(root,recovery,registry(root),clock_ms=at,allowed=allowed)
        from collection_grants import release_completed
        local['released_reservations']=release_completed(recovery,clock_ms=at)
        with recovery.transaction() as db:
            jobs=[dict(r) for r in db.execute("SELECT * FROM tracking_recovery WHERE provider IS NOT NULL AND state IN ('pending','backoff','pending_authorization') AND target_ms>=? AND (next_retry_ms IS NULL OR next_retry_ms<=?) ORDER BY last_scheduled_ms,job_key LIMIT 20",(at-cfg['recoveryWindowHours']*3600000,at))]
        pages=[]
        remaining=cfg['recoveryAttempts']
        attempted_jobs=0
        for job in jobs:
            with recovery.transaction() as db:
                current=db.execute('SELECT state FROM tracking_recovery WHERE job_key=?',(job['job_key'],)).fetchone()
            if not current or current[0] not in ('pending','backoff','pending_authorization'):continue
            if remaining<=0 or time.monotonic()>=deadline or attempted_jobs>=2:break
            scope=dict(providers=[job['provider']],contracts=[job['contract']],sources=[job['source']],start_ms=job['start_ms'],target_ms=job['target_ms'])
            from collection_grants import saved_plan_grant
            grant,denied=saved_plan_grant(recovery,job,cfg,clock_ms=at)
            if denied:
                with recovery.transaction() as db:
                    db.execute("UPDATE tracking_recovery SET state='pending_authorization',error_code=?,last_scheduled_ms=?,next_retry_ms=? WHERE job_key=?",(denied,at,(at//86400000+1)*86400000,job['job_key']))
                    recovery.bump(db,at)
                pages.append(dict(job_key=job['job_key'],state='pending_authorization',error_code=denied));continue
            attempted_jobs+=1
            recovery.bind_grant(job['job_key'],grant,clock_ms=at)
            adapter=LiveHistoricalAdapter(registry(root),clock=lambda:int(time.time()*1000),before_send=allowed)
            with recovery.transaction() as db:before=db.execute('SELECT attempts_used FROM recovery_grants WHERE grant_id=?',(grant,)).fetchone()[0]
            pages.extend(recovery.page(fetch=adapter,clock_ms=at,clock=lambda:int(time.time()*1000),grant_id=grant,page_budget=1,max_http_attempts=remaining,deadline_seconds=max(.001,deadline-time.monotonic())))
            with recovery.transaction() as db:after=db.execute('SELECT attempts_used FROM recovery_grants WHERE grant_id=?',(grant,)).fetchone()[0]
            remaining-=after-before
        # Local effects remain separate from acquisition grants and ACKs.
        b=effects=handoff=dict(ok=True,count=0,items=[])
        def local_allowed():
            try:allowed();return True
            except RecoveryDenied:return False
        with service._LOCK:
            if local_allowed():b=drain(root,ledger,clock_ms=int(time.time()*1000),limit=1)
            if local_allowed():effects=service.drain_recovery(root,limit=8,allow_step=local_allowed)
            if local_allowed():handoff=service.drain_b(root,limit=1)
        historical=dict(ok=True,count=0,items=[])
        if local_allowed():
            from tracking.c_recovery import drain_cycles
            with service._LOCK:historical=drain_cycles(root,ledger,clock_ms=int(time.time()*1000),allowed=allowed)
        result=dict(at_ms=at,pages=pages,local_evidence=local,identity_sync=identities,b_replay=b,effects=effects,historical_cycles=historical,source_handoff=handoff)
        with recovery.transaction() as db:
            db.execute("INSERT INTO meta VALUES('collection_last_recovery',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",(json.dumps(result,ensure_ascii=False),))
        return result
    finally:
        with recovery.transaction() as db:
            try:
                from collection_projection import refresh
                refresh(db,clock_ms=int(time.time()*1000),window_hours=cfg['recoveryWindowHours'])
            finally:db.execute('DELETE FROM collection_recovery_lease WHERE slot=1 AND owner=?',(owner,))
