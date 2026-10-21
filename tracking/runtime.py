"""Bounded persistent runtime. Startup is inert unless explicitly configured.

Uses publication inbox, B changefeed and Recovery as the existing authorities.
No task submissions, AI, trading or hidden network grants. GET is read-only.
"""
from __future__ import annotations
import json
import threading
import time
import uuid
from pathlib import Path
from .ledger import _canonical
from .recovery import Recovery
from .recovery_control import RecoveryDenied

VERSION='codex-abc-tracking-20261019-v3'
_STOP={}; _THREADS={}; _HOSTS={}; _LOCK=threading.Lock()
DEFAULT=dict(schema='tracking-runtime-v3',enabled=False,network_enabled=False,grant_ids=[],
    interval_seconds=15,row_budget=5,replay_budget=5,page_budget=2,http_attempts_per_tick=8,deadline_seconds=20)


def config(root):
    p=Path(root)/'runtime/tracking-runtime.json'
    if not p.is_file():return dict(DEFAULT)
    if p.stat().st_size>16384:raise ValueError('runtime_config_size')
    obj=json.loads(p.read_text(encoding='utf-8-sig'))
    if not isinstance(obj,dict) or set(obj)-set(DEFAULT):raise ValueError('runtime_config_fields')
    result=dict(DEFAULT,**obj)
    if result['schema']!=DEFAULT['schema'] or type(result['enabled']) is not bool or type(result['network_enabled']) is not bool:
        raise ValueError('runtime_control_boolean')
    for key,low,high in [('interval_seconds',5,300),('row_budget',1,20),('replay_budget',1,20),('page_budget',1,4),('http_attempts_per_tick',1,16),('deadline_seconds',1,60)]:
        if type(result[key]) is not int or not low<=result[key]<=high:raise ValueError('runtime_config_'+key)
    if not isinstance(result['grant_ids'],list) or len(result['grant_ids'])>8 or any(not isinstance(g,str) or not g.startswith('grant-') for g in result['grant_ids']):
        raise ValueError('runtime_grant_ids')
    return result


def registry(root):
    path=Path(root)/'runtime/tracking-source-registry.json'
    if not path.is_file():return {}
    if path.stat().st_size>2*1024*1024:raise ValueError('source_registry_size')
    obj=json.loads(path.read_text(encoding='utf-8-sig'))
    if not isinstance(obj,dict) or obj.get('schema')!='tracking-source-registry-v3' or not isinstance(obj.get('contracts'),dict) or len(obj['contracts'])>1000:
        raise ValueError('source_registry_schema')
    return obj['contracts']


def read_status(root):
    from .runtime_metrics import read
    from .sample_capture import read as samples
    from .read_repository import TrackingReadRepository,ReadRepositoryError
    cfg=config(root)
    result=dict(ok=True,version=VERSION,control=cfg,metrics=read(root,20),samples=samples(root,limit=5),
        network_requires_grant=True,starts_scan=False,starts_ai=False,starts_trade=False)
    try:
        repo=TrackingReadRepository.for_runtime(root)
        with repo.snapshot():
            if repo.meta('recovery_schema'):
                result['recovery_counts']=[dict(r) for r in repo.read('SELECT source,state,SUM(n) AS n FROM recovery_quality_counts WHERE n>0 GROUP BY source,state ORDER BY source,state LIMIT 100')]
                result['grants']=[dict(r) for r in repo.read('SELECT grant_id,operation_id,starts_ms,expires_ms,max_attempts,attempts_used,revoked FROM recovery_grants ORDER BY starts_ms DESC LIMIT 20')]
                tables={r[0] for r in repo.read("SELECT name FROM sqlite_master WHERE type='table' AND name='runtime_lease'")}
                if tables:
                    lease=repo.read_one('SELECT owner,until_ms FROM runtime_lease WHERE slot=1')
                    result['worker_lease']=dict(lease) if lease else None
                result['last_tick']=json.loads(repo.meta('runtime_last_tick') or 'null')
                age=repo.meta('runtime_oldest_pending_updated_ms')
                result['oldest_registered_pending_age_ms']=max(0,int(time.time()*1000)-int(age)) if age else None
    except ReadRepositoryError as exc:
        result['ledger_status']=exc.as_dict()
    return result


def _check_send(root,recovery,grant_id,job):
    from tracking_service import enabled
    cfg=config(root); at=int(time.time()*1000)
    if not enabled(root) or not cfg['enabled'] or not cfg['network_enabled'] or grant_id not in cfg['grant_ids']:
        raise RecoveryDenied('runtime_paused_before_send')
    with recovery.transaction() as db:
        recovery.validate_claim(db,job,at)
        control=db.execute("SELECT value FROM recovery_controls WHERE key='enabled'").fetchone()
        g=db.execute('SELECT * FROM recovery_grants WHERE grant_id=?',(grant_id,)).fetchone()
        if not control or control[0]!='true' or not g or g['revoked'] or not g['starts_ms']<=at<g['expires_ms']:
            raise RecoveryDenied('grant_expired_or_paused_before_send')
        # AttemptGate has already consumed this one permit, including the last one.
        recovery._scope(job,json.loads(g['scope_json']))
        cooldown=db.execute('SELECT until_ms FROM recovery_provider_limits WHERE provider=?',(job['provider'],)).fetchone()
        if cooldown and cooldown[0]>at:raise RecoveryDenied('provider_cooldown')


def _bind_due(recovery,grant_id,at):
    with recovery.transaction() as db:
        grant=db.execute('SELECT * FROM recovery_grants WHERE grant_id=?',(grant_id,)).fetchone()
        if not grant or grant['revoked'] or not grant['starts_ms']<=at<grant['expires_ms'] or grant['attempts_used']>=grant['max_attempts']:
            return 0
        scope=json.loads(grant['scope_json'])
        marks=lambda n:','.join('?' for _ in range(n))
        rows=[dict(r) for r in db.execute('SELECT * FROM tracking_recovery WHERE provider IN ('+marks(len(scope['providers']))+') AND contract IN ('+marks(len(scope['contracts']))+') AND source IN ('+marks(len(scope['sources']))+') AND start_ms>=? AND target_ms<=? AND NOT EXISTS(SELECT 1 FROM recovery_grant_jobs g WHERE g.job_key=tracking_recovery.job_key AND g.grant_id=?) ORDER BY job_key LIMIT 100',
            tuple(scope['providers']+scope['contracts']+scope['sources']+[scope['start_ms'],scope['target_ms'],grant_id]))]
    for row in rows:recovery.bind_grant(row['job_key'],grant_id,clock_ms=at)
    return len(rows)


def tick(root):
    import tracking_service as service
    from .recovery_bridge import bridge_page
    from .runtime_metrics import record
    from .live_source import LiveHistoricalAdapter
    from .b_recovery import drain as drain_b_recovery
    root=Path(root).resolve();cfg=config(root)
    if not cfg['enabled'] or not service.enabled(root):return dict(ok=True,disabled=True)
    host=_HOSTS.get(str(root)); lane_key='tracking-runtime-'+uuid.uuid4().hex
    if host is None:
        return dict(ok=False,deferred=True,reason='use_host_runtime_to_share_original_data_lane')
    # Original TaskService's lock/active registry is the single data-lane owner.
    # Pending manual work has priority; no second request concurrency is created.
    with host.lock,host._db() as db:
        research=('ai_research','ai_research_batch_retry')
        busy=any(kind not in research for kind in host.active_tasks.values())
        queued=db.execute("SELECT 1 FROM tasks WHERE status IN ('queued','running') AND kind NOT IN ('ai_research','ai_research_batch_retry') LIMIT 1").fetchone()
        if busy or queued:return dict(ok=True,deferred=True,reason='original_data_lane_busy')
        host.active_tasks[lane_key]='tracking_recovery_runtime'
    try:
        ledger=service.writer(root); recovery=Recovery(ledger)
    except BaseException:
        with host.lock:host.active_tasks.pop(lane_key,None)
        raise
    owner=uuid.uuid4().hex; at=int(time.time()*1000)
    try:
        with recovery.transaction() as db:
            db.execute('CREATE TABLE IF NOT EXISTS runtime_lease(slot INTEGER PRIMARY KEY CHECK(slot=1),owner TEXT NOT NULL,until_ms INTEGER NOT NULL)')
            db.execute('CREATE INDEX IF NOT EXISTS runtime_pending_age ON tracking_recovery(state,updated_ms) WHERE provider IS NOT NULL')
            lease=db.execute('SELECT * FROM runtime_lease WHERE slot=1').fetchone()
            if lease and lease['until_ms']>at:
                acquired=False
            else:
                db.execute('INSERT INTO runtime_lease VALUES(1,?,?) ON CONFLICT(slot) DO UPDATE SET owner=excluded.owner,until_ms=excluded.until_ms',(owner,at+600000))
                acquired=True
    except BaseException:
        with host.lock:host.active_tasks.pop(lane_key,None)
        raise
    if not acquired:
        with host.lock:host.active_tasks.pop(lane_key,None)
        return dict(ok=True,busy=True)
    start=time.monotonic();result=dict(ok=True,version=VERSION,at_ms=at,network_pages=[])
    try:
        # The same process-wide lock as original producer kicks protects local cursors.
        with service._LOCK:
            result['publications_resumed']=service.resume_publications(root,limit=10)
            result['publication']=service.drain_publications(root,row_budget=cfg['row_budget'])
            result['b']=service.drain_b(root,limit=20)
            after=ledger.get_cursor('runtime_bridge_after') or ''
            bridge=bridge_page(ledger,registry(root),after=after,limit=50,clock_ms=at)
            with recovery.transaction() as db:
                db.execute("INSERT INTO cursors(cursor_name,cursor_value,updated_at_utc) VALUES('runtime_bridge_after',?,?) ON CONFLICT(cursor_name) DO UPDATE SET cursor_value=excluded.cursor_value,updated_at_utc=excluded.updated_at_utc",(bridge['next_cursor'] or '',service.now()))
            result['bridge']=dict(count=len(bridge['items']),blocked=sum('blocked' in i for i in bridge['items']))
        if cfg['network_enabled']:
            # One shared per-tick budget/deadline across all grants, never N times budget.
            deadline=time.monotonic()+cfg['deadline_seconds'];remaining=cfg['http_attempts_per_tick']
            grants=cfg['grant_ids'];offset=int(ledger.get_cursor('runtime_grant_offset') or 0)
            grants=grants[offset%len(grants):]+grants[:offset%len(grants)] if grants else []
            for grant_id in grants:
                if remaining<=0 or time.monotonic()>=deadline:break
                _bind_due(recovery,grant_id,int(time.time()*1000))
                adapter=LiveHistoricalAdapter(registry(root),clock=lambda:int(time.time()*1000),
                    before_send=lambda job,g=grant_id:_check_send(root,recovery,g,job))
                with recovery.transaction() as db:
                    before=db.execute('SELECT attempts_used FROM recovery_grants WHERE grant_id=?',(grant_id,)).fetchone()
                pages=recovery.page(fetch=adapter,clock_ms=int(time.time()*1000),clock=lambda:int(time.time()*1000),grant_id=grant_id,
                    page_budget=cfg['page_budget'],max_http_attempts=remaining,deadline_seconds=max(.001,min(60,deadline-time.monotonic())))
                with recovery.transaction() as db:
                    after_used=db.execute('SELECT attempts_used FROM recovery_grants WHERE grant_id=?',(grant_id,)).fetchone()
                remaining-=(after_used[0] if after_used else 0)-(before[0] if before else 0)
                result['network_pages'].extend(pages)
            with recovery.transaction() as db:
                db.execute("INSERT INTO cursors(cursor_name,cursor_value,updated_at_utc) VALUES('runtime_grant_offset',?,?) ON CONFLICT(cursor_name) DO UPDATE SET cursor_value=excluded.cursor_value,updated_at_utc=excluded.updated_at_utc",(str(offset+1),service.now()))
            result['attempt_permits_used']=cfg['http_attempts_per_tick']-remaining
        with service._LOCK:
            result['b_replay']=drain_b_recovery(root,ledger,clock_ms=int(time.time()*1000),limit=1)
            result['recovery_effects']=service.drain_recovery(root,limit=cfg['replay_budget'])
            result['b_after_replay']=service.drain_b(root,limit=20)
        result['ok']=all(x.get('ok',True) for x in (result['publication'],result['b'],result['b_replay'],result['recovery_effects'],result['b_after_replay']))
        return result
    except Exception as exc:
        result.update(ok=False,error=type(exc).__name__+':'+str(exc)[:2000])
        service.capture_error(root,result['error'])
        return result
    finally:
        result['duration_ms']=(time.monotonic()-start)*1000
        try:
            with recovery.transaction() as db:
                ages=[db.execute("SELECT updated_ms FROM tracking_recovery WHERE provider IS NOT NULL AND state=? ORDER BY updated_ms LIMIT 1",(state,)).fetchone() for state in ('pending','backoff','running')]
                values=[r[0] for r in ages if r and r[0]]
                if values:
                    db.execute("INSERT INTO meta(key,value) VALUES('runtime_oldest_pending_updated_ms',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",(str(min(values)),))
                else:
                    db.execute("DELETE FROM meta WHERE key='runtime_oldest_pending_updated_ms'")
                db.execute("INSERT INTO meta(key,value) VALUES('runtime_last_tick',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",(_canonical(result),))
                db.execute('DELETE FROM runtime_lease WHERE slot=1 AND owner=?',(owner,))
            record(root,'runtime_tick',result['duration_ms'],result)
        finally:
            with host.lock:host.active_tasks.pop(lane_key,None)


def start(root,host=None):
    from tracking_service import enabled,capture_error
    root=Path(root).resolve();key=str(root)
    if host is not None:_HOSTS[key]=host
    if not enabled(root) or not config(root)['enabled']:return False
    with _LOCK:
        if key in _THREADS and _THREADS[key].is_alive():return False
        event=threading.Event();_STOP[key]=event
        def loop():
            while not event.is_set():
                try:
                    cfg=config(root)
                    if not cfg['enabled'] or not enabled(root):break
                    tick(root)
                    event.wait(cfg['interval_seconds'])
                except Exception as exc:
                    capture_error(root,exc);break
        thread=threading.Thread(target=loop,name='ABC-persistent-runtime',daemon=True)
        _THREADS[key]=thread;thread.start();return True


def stop(root):
    event=_STOP.get(str(Path(root).resolve()))
    if event:event.set()

