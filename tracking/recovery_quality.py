"""Materialized quality counts in the SAME ledger; GET is query-only."""
import json
SCHEMA='recovery-quality-v1'

def schema(db):
    if db.execute("SELECT 1 FROM meta WHERE key='recovery_quality_schema'").fetchone():return
    db.execute("CREATE TABLE IF NOT EXISTS recovery_quality_counts(contract TEXT NOT NULL,source TEXT NOT NULL,period_ms INTEGER NOT NULL,state TEXT NOT NULL,n INTEGER NOT NULL,PRIMARY KEY(contract,source,period_ms,state))")
    db.execute("CREATE TABLE IF NOT EXISTS recovery_consumer_counts(event_id TEXT NOT NULL,consumer TEXT NOT NULL,state TEXT NOT NULL,n INTEGER NOT NULL,PRIMARY KEY(event_id,consumer,state))")
    db.execute("INSERT INTO recovery_quality_counts SELECT contract,source,COALESCE(period_ms,0),state,COUNT(*) FROM tracking_recovery GROUP BY contract,source,COALESCE(period_ms,0),state")
    db.execute("INSERT INTO recovery_consumer_counts SELECT COALESCE(event_id,''),consumer,state,COUNT(*) FROM recovery_dependencies GROUP BY COALESCE(event_id,''),consumer,state")
    for table,target,fields,expressions in [
        ('tracking_recovery','recovery_quality_counts',['contract','source','period_ms','state'],['{x}.contract','{x}.source','COALESCE({x}.period_ms,0)','{x}.state']),
        ('recovery_dependencies','recovery_consumer_counts',['event_id','consumer','state'],["COALESCE({x}.event_id,'')",'{x}.consumer','{x}.state']),
    ]:
        cols=','.join(fields)
        def plus(x):
            values=','.join(v.format(x=x) for v in expressions)
            return 'INSERT INTO '+target+'('+cols+',n) VALUES('+values+',1) ON CONFLICT('+cols+') DO UPDATE SET n=n+1;'
        def minus(x):
            where=' AND '.join(f+'='+e.format(x=x) for f,e in zip(fields,expressions))
            return 'UPDATE '+target+' SET n=n-1 WHERE '+where+';'
        db.execute('CREATE TRIGGER '+target+'_insert AFTER INSERT ON '+table+' BEGIN '+plus('NEW')+' END')
        db.execute('CREATE TRIGGER '+target+'_delete AFTER DELETE ON '+table+' BEGIN '+minus('OLD')+' END')
        db.execute('CREATE TRIGGER '+target+'_update AFTER UPDATE OF '+cols+' ON '+table+' BEGIN '+minus('OLD')+plus('NEW')+' END')
    db.execute("CREATE INDEX IF NOT EXISTS recovery_contract_schedule ON tracking_recovery(contract,last_scheduled_ms DESC,job_key)")
    db.execute("CREATE INDEX IF NOT EXISTS recovery_contract_retry ON tracking_recovery(contract,next_retry_ms,job_key)")
    db.execute("INSERT INTO meta VALUES('recovery_quality_schema',?)",(SCHEMA,))

def snapshot(repo,*,market,symbol,event_id):
    # Sharing is limited to the current SQLite read transaction. Never reuse
    # quality across requests or cache a dynamic source by process lifetime.
    cache=getattr(repo._local,'recovery_quality_cache',None)
    key=(market,symbol,event_id)
    if cache is not None and key in cache:return cache[key]
    result=_snapshot(repo,market=market,symbol=symbol,event_id=event_id)
    if cache is not None:cache[key]=result
    return result

def _snapshot(repo,*,market,symbol,event_id):
    contract=market+'|'+symbol
    seq=int(repo.meta('recovery_commit_seq') or 0)
    if not repo.meta('recovery_quality_schema'):
        return dict(schema=SCHEMA,quality_seq=seq,status='not_initialized',as_of_ms=None,source_counts=[],consumer_counts=[],count_unit='acquisition_intents_not_missing_samples')
    rows=repo.read("SELECT source,period_ms,state,n FROM recovery_quality_counts WHERE contract=? AND n>0 ORDER BY source,period_ms,state LIMIT 129",(contract,))
    if len(rows)>128:
        from .read_repository import ReadRepositoryError
        raise ReadRepositoryError('RECOVERY_quality_page_required')
    consumers=repo.read("SELECT consumer,state,n FROM recovery_consumer_counts WHERE event_id=? AND n>0 ORDER BY consumer,state LIMIT 65",(event_id,))
    if len(consumers)>64:
        from .read_repository import ReadRepositoryError
        raise ReadRepositoryError('RECOVERY_consumer_page_required')
    last=repo.read_one("SELECT source,state,error_code,last_scheduled_ms,next_retry_ms,fetched_max_ms,archived_continuous_ms,epoch,provider,endpoint FROM tracking_recovery WHERE contract=? ORDER BY last_scheduled_ms DESC,job_key LIMIT 1",(contract,))
    due=repo.read_one("SELECT next_retry_ms FROM tracking_recovery WHERE contract=? AND next_retry_ms IS NOT NULL ORDER BY next_retry_ms,job_key LIMIT 1",(contract,))
    control=repo.read_one("SELECT value FROM recovery_controls WHERE key='enabled'")
    states={r['state'] for r in rows}
    return dict(schema=SCHEMA,quality_seq=seq,as_of_ms=int(repo.meta('recovery_as_of_ms') or 0) or None,
        status='incomplete' if states-{'evaluated','archived_awaiting_effect'} else 'awaiting_effect' if 'archived_awaiting_effect' in states else 'no_registered_gap' if not states else 'evaluated',
        enabled=bool(control and control[0]=='true'),source_counts=[dict(r) for r in rows],consumer_counts=[dict(r) for r in consumers],
        last_job=dict(last) if last else None,next_retry_ms=due[0] if due else None,
        count_unit='acquisition_intents_not_missing_samples',
        quality_scope='registered_tracking_recovery_only_not_all_production_sources')

