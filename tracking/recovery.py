"""Explicit bounded recovery: existing queue/chunks, no scheduler or HTTP client."""
import hashlib
import json
import time
import uuid
import unicodedata
from contextlib import contextmanager
from .ledger import _canonical
from .recovery_control import AttemptGate, RecoveryDenied, integer, schema as control_schema

DDL=[
"CREATE TABLE IF NOT EXISTS tracking_recovery(job_key TEXT PRIMARY KEY,source TEXT NOT NULL,contract TEXT NOT NULL,timeframe TEXT NOT NULL,start_ms INTEGER NOT NULL,target_ms INTEGER NOT NULL,next_ms INTEGER NOT NULL,state TEXT NOT NULL,attempt_count INTEGER NOT NULL DEFAULT 0,next_retry_ms INTEGER,error_code TEXT,fetched_max_ms INTEGER,archived_continuous_ms INTEGER,evaluated_ms INTEGER)",
"CREATE TABLE IF NOT EXISTS tracking_recovery_chunks(job_key TEXT NOT NULL,version TEXT NOT NULL,payload_json TEXT NOT NULL,available_at_ms INTEGER NOT NULL,PRIMARY KEY(job_key,version))"]
EXTRA=dict(provider="TEXT",endpoint="TEXT",period_ms="INTEGER",time_semantics="TEXT",unit_identity="TEXT",adapter_version="TEXT",quality_version="TEXT",identity_json="TEXT",end_exclusive_ms="INTEGER",queue_class="TEXT NOT NULL DEFAULT 'historical'",last_scheduled_ms="INTEGER NOT NULL DEFAULT 0",lease_token="TEXT",lease_until_ms="INTEGER",lease_owner="TEXT",epoch="INTEGER NOT NULL DEFAULT 0",updated_ms="INTEGER NOT NULL DEFAULT 0")
SCHEMA="recovery-v2.3"
LEGACY_STATES={"archived":"archived_awaiting_effect","unrecoverable":"unavailable_history","closed_session_ended":"pending","resolved":"pending"}

def bump_conn(db,now):
    db.execute("INSERT INTO meta VALUES('recovery_commit_seq','1') ON CONFLICT(key) DO UPDATE SET value=CAST(CAST(value AS INTEGER)+1 AS TEXT)")
    db.execute("INSERT INTO meta VALUES('recovery_as_of_ms',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",(str(now),))

def ensure_schema(db):
    for ddl in DDL: db.execute(ddl)
    columns={r[1] for r in db.execute("PRAGMA table_info(tracking_recovery)")}
    for name,decl in EXTRA.items():
        if name not in columns: db.execute("ALTER TABLE tracking_recovery ADD COLUMN "+name+" "+decl)
    for ddl in [
        "CREATE INDEX IF NOT EXISTS recovery_ready ON tracking_recovery(state,next_retry_ms,queue_class,last_scheduled_ms,job_key)",
        "CREATE INDEX IF NOT EXISTS recovery_contract ON tracking_recovery(contract,source,start_ms,target_ms)",
        "CREATE TABLE IF NOT EXISTS recovery_dependencies(dependency_id TEXT PRIMARY KEY,job_key TEXT NOT NULL,consumer TEXT NOT NULL,rule_version TEXT NOT NULL,event_id TEXT,start_ms INTEGER NOT NULL,target_ms INTEGER NOT NULL,step_ms INTEGER NOT NULL,next_ms INTEGER NOT NULL,state TEXT NOT NULL DEFAULT 'pending',evaluated_ms INTEGER,input_epoch INTEGER NOT NULL DEFAULT 0,reason TEXT,FOREIGN KEY(job_key) REFERENCES tracking_recovery(job_key))",
        "CREATE INDEX IF NOT EXISTS recovery_dependency_job ON recovery_dependencies(job_key,state,dependency_id)",
        "CREATE INDEX IF NOT EXISTS recovery_dependency_event ON recovery_dependencies(event_id,state,dependency_id)",
        "CREATE TABLE IF NOT EXISTS recovery_effects(effect_key TEXT PRIMARY KEY,dependency_id TEXT NOT NULL,epoch INTEGER NOT NULL,boundary_ms INTEGER NOT NULL,input_refs_json TEXT NOT NULL,message_key TEXT NOT NULL,result_hash TEXT NOT NULL,created_ms INTEGER NOT NULL,UNIQUE(dependency_id,epoch,boundary_ms))",
        "CREATE TABLE IF NOT EXISTS tracking_recovery_samples(job_key TEXT NOT NULL,effective_ms INTEGER NOT NULL,version TEXT NOT NULL,available_ms INTEGER NOT NULL,payload_json TEXT NOT NULL,PRIMARY KEY(job_key,effective_ms,version),FOREIGN KEY(job_key,version) REFERENCES tracking_recovery_chunks(job_key,version))",
        "CREATE INDEX IF NOT EXISTS recovery_samples_lookup ON tracking_recovery_samples(job_key,effective_ms,available_ms DESC,version DESC)",
        "CREATE TABLE IF NOT EXISTS recovery_migrations(version TEXT PRIMARY KEY,detail_json TEXT NOT NULL)",
        "CREATE TABLE IF NOT EXISTS recovery_replay_contexts(dependency_id TEXT NOT NULL,epoch INTEGER NOT NULL,input_sha TEXT NOT NULL,payload_json TEXT NOT NULL,created_ms INTEGER NOT NULL,state TEXT NOT NULL DEFAULT 'pending',next_retry_ms INTEGER NOT NULL DEFAULT 0,last_scheduled_ms INTEGER NOT NULL DEFAULT 0,attempts INTEGER NOT NULL DEFAULT 0,error TEXT,PRIMARY KEY(dependency_id,epoch))",
        "CREATE INDEX IF NOT EXISTS recovery_context_input ON recovery_replay_contexts(input_sha,dependency_id,epoch)",
        "CREATE INDEX IF NOT EXISTS recovery_replay_ready ON recovery_replay_contexts(state,next_retry_ms,last_scheduled_ms,dependency_id,epoch)",
    ]: db.execute(ddl)
    control_schema(db)
    if not db.execute("SELECT 1 FROM meta WHERE key='recovery_schema'").fetchone():
        db.execute("INSERT OR IGNORE INTO recovery_migrations VALUES(?,?)",(SCHEMA,_canonical(dict(legacy_state_mapping=LEGACY_STATES,legacy_external_execution=False))))
        for old,new in LEGACY_STATES.items(): db.execute("UPDATE tracking_recovery SET state=? WHERE state=?",(new,old))
        db.execute("INSERT INTO meta VALUES('recovery_schema',?)",(SCHEMA,))
        db.execute("INSERT OR IGNORE INTO meta VALUES('recovery_commit_seq','0')")
        db.execute("INSERT OR IGNORE INTO meta VALUES('recovery_as_of_ms','0')")
    from .recovery_quality import schema as quality_schema
    quality_schema(db)
    db.execute("UPDATE meta SET value=? WHERE key='recovery_schema'",(SCHEMA,))

class SourceFailure(Exception):
    def __init__(self,code,detail=''): self.code=str(code);super().__init__(detail or self.code)

def classify(code,attempt,clock):
    code=str(code)
    if code in ('401','402','403'): return 'blocked_permission',None,'provider_'+code
    if code in ('cap_history_provider_not_configured','contract_identity_unverified','coin_pair_unknown','coin_multiplier_unknown','adapter_contract_missing','contract_metadata_outside_history','unsupported_quote_conversion'): return 'blocked_configuration',None,code
    if code in ('history_unavailable','history_window_exceeded'): return 'unavailable_history',None,code
    delay=min(86400000,(3600000 if code=='418' else 60000)*2**min(attempt,8)) if code in ('429','418') else min(3600000,300000*2**min(attempt,4))
    return 'backoff',clock+delay,'provider_'+code if code in ('429','418') else code

class Recovery:
    def __init__(self,ledger): self.ledger=ledger
    def _db(self):
        db=self.ledger._connect()
        try:
            row=db.execute("SELECT value FROM meta WHERE key='recovery_schema'").fetchone()
            if row and row[0]==SCHEMA:return db
            db.execute('BEGIN IMMEDIATE');ensure_schema(db);db.execute('COMMIT');return db
        except BaseException: db.close();raise
    @contextmanager
    def transaction(self):
        db=self._db()
        try:
            db.execute('BEGIN IMMEDIATE');yield db;db.execute('COMMIT')
        except BaseException:
            if db.in_transaction: db.execute('ROLLBACK')
            raise
        finally: db.close()
    bump=staticmethod(bump_conn)
    def set_enabled(self,enabled,*,clock_ms):
        if type(enabled) is not bool: raise ValueError('recovery_boolean_required')
        integer(clock_ms,'clock')
        with self.transaction() as db:
            db.execute("UPDATE recovery_controls SET value=? WHERE key='enabled'",('true' if enabled else 'false',))
            self.bump(db,clock_ms)
    def revoke(self,grant_id,*,clock_ms):
        with self.transaction() as db:
            db.execute("UPDATE recovery_grants SET revoked=1 WHERE grant_id=?",(grant_id,));self.bump(db,clock_ms)
    def authorize(self,db,job,grant_id,clock):
        c=db.execute("SELECT value FROM recovery_controls WHERE key='enabled'").fetchone()
        if not c or c[0]!='true': raise RecoveryDenied('recovery_paused')
        g=db.execute("SELECT * FROM recovery_grants WHERE grant_id=?",(grant_id,)).fetchone()
        if not g or g['revoked'] or not g['starts_ms']<=clock<g['expires_ms']: raise RecoveryDenied('grant_missing_expired_or_revoked')
        if g['attempts_used']>=g['max_attempts']: raise RecoveryDenied('grant_budget_exhausted')
        self._scope(job,json.loads(g['scope_json']))
        if not db.execute("SELECT 1 FROM recovery_grant_jobs WHERE grant_id=? AND job_key=?",(grant_id,job['job_key'])).fetchone(): raise RecoveryDenied('grant_not_bound')
        lim=db.execute("SELECT until_ms FROM recovery_provider_limits WHERE provider=?",(job['provider'],)).fetchone()
        if lim and clock<lim[0]: raise RecoveryDenied('provider_cooldown')
    @staticmethod
    def _scope(j,s):
        if any(j[f] not in s[k] for f,k in [('provider','providers'),('contract','contracts'),('source','sources')]) or j['start_ms']<s['start_ms'] or j['target_ms']>s['target_ms']: raise RecoveryDenied('outside_grant_scope')
    def bind_grant(self,job_key,grant_id,*,clock_ms):
        with self.transaction() as db:
            j=db.execute("SELECT * FROM tracking_recovery WHERE job_key=?",(job_key,)).fetchone()
            g=db.execute("SELECT * FROM recovery_grants WHERE grant_id=?",(grant_id,)).fetchone()
            if not j or not g: raise RecoveryDenied('grant_or_job_missing')
            self._scope(j,json.loads(g['scope_json']))
            db.execute("INSERT OR IGNORE INTO recovery_grant_jobs VALUES(?,?)",(grant_id,job_key))
            db.execute("UPDATE tracking_recovery SET state='pending',error_code=NULL WHERE job_key=? AND state='pending_authorization'",(job_key,));self.bump(db,clock_ms)
    def enqueue(self,*,source,market,symbol,timeframe,start_ms,target_ms,provider=None,endpoint=None,period_ms=None,time_semantics=None,unit_identity=None,adapter_version=None,quality_version='source-quality-v1',queue_class='historical',clock_ms=0):
        market={'USDⓈ-M':'USDT-M','USDT-M':'USDT-M','COIN-M':'COIN-M'}.get(market)
        if market is None or not isinstance(symbol,str) or not symbol or '|' in symbol: raise ValueError('recovery_contract')
        symbol=unicodedata.normalize('NFC',symbol)
        if source not in ('candles_1h','candles_4h','oi','lsr','cap') or timeframe not in ('4h','1h'): raise ValueError('recovery_source_or_timeframe')
        integer(start_ms,'start');integer(target_ms,'target',start_ms)
        if queue_class not in ('recent','historical'): raise ValueError('recovery_queue_class')
        verified=all(isinstance(x,str) and 0<len(x)<=200 for x in (provider,endpoint,time_semantics,unit_identity,adapter_version,quality_version))
        if period_ms is None: period_ms=14400000 if timeframe=='4h' else 3600000
        integer(period_ms,'period',1,86400000)
        if (target_ms-start_ms)%period_ms: raise ValueError('recovery_unaligned_window')
        if verified and time_semantics not in ('open_ms','effective_at_ms','asof_snapshot'): raise ValueError('recovery_time_semantics')
        if source=='cap' and verified and (time_semantics!='asof_snapshot' or start_ms!=target_ms): raise ValueError('cap_requires_single_asof_window')
        identity=dict(provider=provider,endpoint=endpoint,contract=market+'|'+symbol,source=source,period_ms=period_ms,time_semantics=time_semantics,unit_identity=unit_identity,start_ms=start_ms,end_exclusive_ms=integer(target_ms+period_ms,'end'),adapter_version=adapter_version,quality_version=quality_version)
        key=hashlib.sha256(_canonical(identity).encode()).hexdigest()
        with self.transaction() as db:
            if db.execute("INSERT OR IGNORE INTO tracking_recovery(job_key,source,contract,timeframe,start_ms,target_ms,next_ms,state,provider,endpoint,period_ms,time_semantics,unit_identity,adapter_version,quality_version,identity_json,end_exclusive_ms,queue_class,error_code,updated_ms) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",(key,source,market+'|'+symbol,timeframe,start_ms,target_ms,start_ms,'pending' if verified else 'blocked_configuration',provider,endpoint,period_ms,time_semantics,unit_identity,adapter_version,quality_version,_canonical(identity),target_ms+period_ms,queue_class,None if verified else 'adapter_contract_missing',clock_ms)).rowcount: self.bump(db,clock_ms)
        return key
    def depend(self,job_key,*,consumer,rule_version,start_ms,target_ms,step_ms,event_id=None,clock_ms=0):
        if consumer not in ('A_strategy','B_strategy','C_strategy','A_observation','B_observation','C_observation','technical_observation'): raise ValueError('recovery_consumer')
        if not isinstance(rule_version,str) or not rule_version: raise ValueError('recovery_rule_version')
        integer(start_ms,'dependency_start');integer(target_ms,'dependency_target',start_ms);integer(step_ms,'dependency_step',1)
        if (target_ms-start_ms)%step_ms: raise ValueError('recovery_dependency_alignment')
        key=hashlib.sha256(_canonical([job_key,consumer,rule_version,event_id,start_ms,target_ms,step_ms]).encode()).hexdigest()
        with self.transaction() as db:
            j=db.execute("SELECT * FROM tracking_recovery WHERE job_key=?",(job_key,)).fetchone()
            if not j or start_ms<j['start_ms'] or target_ms>j['target_ms']: raise ValueError('dependency_outside_acquisition')
            if db.execute("INSERT OR IGNORE INTO recovery_dependencies(dependency_id,job_key,consumer,rule_version,event_id,start_ms,target_ms,step_ms,next_ms,input_epoch) VALUES(?,?,?,?,?,?,?,?,?,?)",(key,job_key,consumer,rule_version,event_id,start_ms,target_ms,step_ms,start_ms,j['epoch'])).rowcount: self.bump(db,clock_ms)
        return key
    def validate_claim(self,db,job,clock):
        row=db.execute("SELECT * FROM tracking_recovery WHERE job_key=?",(job['job_key'],)).fetchone()
        if not row or row['state']!='running' or row['lease_token']!=job['lease_token'] or row['lease_until_ms']<=clock or row['epoch']!=job['epoch']: raise RecoveryDenied('stale_recovery_lease')
        return row
    def claim(self,*,grant_id,clock_ms,owner='explicit-worker',lease_ms=120000,prefer_recent=False):
        integer(clock_ms,'clock');integer(lease_ms,'lease',1000,600000)
        with self.transaction() as db:
            db.execute("UPDATE tracking_recovery SET state='pending',lease_token=NULL,lease_owner=NULL WHERE state='running' AND lease_until_ms<=?",(clock_ms,))
            row=db.execute("""SELECT j.* FROM tracking_recovery j JOIN recovery_grant_jobs gj ON gj.job_key=j.job_key AND gj.grant_id=? JOIN recovery_grants g ON g.grant_id=gj.grant_id WHERE j.state IN ('pending','backoff','pending_authorization') AND j.provider IS NOT NULL AND (j.next_retry_ms IS NULL OR j.next_retry_ms<=?) AND g.revoked=0 AND g.starts_ms<=? AND g.expires_ms>? AND g.attempts_used<g.max_attempts AND NOT EXISTS(SELECT 1 FROM recovery_provider_limits l WHERE l.provider=j.provider AND l.until_ms>?) ORDER BY CASE WHEN ? AND j.queue_class='recent' THEN 0 ELSE 1 END,j.last_scheduled_ms,j.job_key LIMIT 1""",(grant_id,clock_ms,clock_ms,clock_ms,clock_ms,int(prefer_recent))).fetchone()
            if not row: return None
            job=dict(row);self.authorize(db,job,grant_id,clock_ms)
            if not job['period_ms'] or not job['identity_json']: raise RecoveryDenied('adapter_contract_missing')
            token=uuid.uuid4().hex
            db.execute("UPDATE tracking_recovery SET state='running',lease_token=?,lease_until_ms=?,lease_owner=?,last_scheduled_ms=?,updated_ms=? WHERE job_key=?",(token,clock_ms+lease_ms,owner,clock_ms,clock_ms,job['job_key']))
            job.update(lease_token=token,lease_until_ms=clock_ms+lease_ms,state='running',page_start=job['next_ms'],page_end=min(job['target_ms'],job['next_ms']+499*job['period_ms']))
            self.bump(db,clock_ms);return job
    def _archive(self,job,received,*,clock_ms):
        if not isinstance(received,list) or not 1<=len(received)<=500: raise SourceFailure('empty_response' if received==[] else 'invalid_page')
        by={}
        for s in received:
            if not isinstance(s,dict): raise SourceFailure('invalid_sample')
            t=s.get('effective_at_ms');a=s.get('available_at_ms')
            cap_sample=job['source']=='cap'
            if type(t) is not int or (not cap_sample and not job['page_start']<=t<=job['page_end']) or (cap_sample and not 0<=job['page_start']-t<=3600000): raise SourceFailure('sample_outside_fixed_page')
            if not cap_sample and (t-job['start_ms'])%job['period_ms']: raise SourceFailure('unaligned_sample')
            if type(a) is not int or a<0 or a>clock_ms: raise SourceFailure('sample_availability_unknown_or_future')
            if s.get('unit')!=job['unit_identity']: raise SourceFailure('source_unit_mismatch')
            if t in by and by[t]!=s: raise SourceFailure('conflicting_source_sample')
            by[t]=s
        text=_canonical(received);version=hashlib.sha256(text.encode()).hexdigest()
        with self.transaction() as db:
            self.validate_claim(db,job,clock_ms)
            db.execute('INSERT OR IGNORE INTO tracking_recovery_chunks VALUES(?,?,?,?)',(job['job_key'],version,text,clock_ms))
            for t,s in by.items(): db.execute("INSERT OR IGNORE INTO tracking_recovery_samples VALUES(?,?,?,?,?)",(job['job_key'],t,version,max(s['available_at_ms'],clock_ms),_canonical(s)))
            db.execute("UPDATE recovery_replay_contexts SET state='pending',next_retry_ms=0 WHERE epoch=? AND dependency_id IN (SELECT dependency_id FROM recovery_dependencies WHERE job_key=?) AND state IN ('pending','awaiting_archive')",(job['epoch'],job['job_key']))
            # The same frozen consumer context can depend on several source
            # roles. Newly arrived cap/oi/lsr must wake those sibling replays too.
            db.execute("""UPDATE recovery_replay_contexts SET next_retry_ms=0
                WHERE state='pending' AND dependency_id IN (
                  SELECT other.dependency_id FROM recovery_replay_contexts own
                  JOIN recovery_dependencies d ON d.dependency_id=own.dependency_id AND d.input_epoch=own.epoch
                  JOIN recovery_replay_contexts other ON other.input_sha=own.input_sha
                  JOIN recovery_dependencies e ON e.dependency_id=other.dependency_id AND e.input_epoch=other.epoch
                  JOIN tracking_recovery j ON j.job_key=e.job_key
                  WHERE d.job_key=? AND j.contract=? AND e.consumer=d.consumer AND e.rule_version=d.rule_version AND e.event_id IS d.event_id)""",(job['job_key'],job['contract']))
            if all(isinstance(s.get('local_provenance'),dict) for s in received):
                db.execute("INSERT INTO meta VALUES('collection_local_archive_count','1') ON CONFLICT(key) DO UPDATE SET value=CAST(CAST(value AS INTEGER)+1 AS TEXT)")
            expected=job['next_ms'];prefix=job['archived_continuous_ms']
            while expected<=job['page_end'] and expected in by: prefix=expected;expected+=job['period_ms']
            # Cap proves only this as-of sample, not an invented dense source series.
            if job['source']=='cap':
                prefix=None
                expected=job['target_ms']+job['period_ms']
            state='archived_awaiting_effect' if expected>job['target_ms'] else 'backoff'
            db.execute("UPDATE tracking_recovery SET next_ms=?,state=?,attempt_count=attempt_count+1,next_retry_ms=?,error_code=?,fetched_max_ms=?,archived_continuous_ms=?,lease_token=NULL,lease_owner=NULL,lease_until_ms=NULL,updated_ms=? WHERE job_key=?",(expected,state,None if state=='archived_awaiting_effect' else clock_ms+300000,None if state=='archived_awaiting_effect' else 'source_gap',max(max(by),job['fetched_max_ms'] or 0),prefix,clock_ms,job['job_key']));self.bump(db,clock_ms)
        return dict(job_key=job['job_key'],state=state,version=version,evaluation_acked=False)
    def import_local(self,job_key,samples,*,clock_ms):
        """Archive existing evidence without an HTTP grant; never acknowledge business."""
        integer(clock_ms,'clock')
        if not samples or any(not isinstance(x.get('local_provenance'),dict) for x in samples):raise ValueError('local_provenance_required')
        with self.transaction() as db:
            enabled=db.execute("SELECT value FROM recovery_controls WHERE key='enabled'").fetchone()
            if not enabled or enabled[0]!='true':raise RecoveryDenied('recovery_paused')
            row=db.execute('SELECT * FROM tracking_recovery WHERE job_key=?',(job_key,)).fetchone()
            if not row or not row['provider'] or row['state'] in ('running','archived_awaiting_effect','evaluated','complete','cancelled'):raise RecoveryDenied('local_job_not_pending')
            job=dict(row);prior=job['state'];token=uuid.uuid4().hex
            db.execute("UPDATE tracking_recovery SET state='running',lease_token=?,lease_until_ms=?,lease_owner='local-evidence' WHERE job_key=?",(token,clock_ms+120000,job_key))
            job.update(state='running',lease_token=token,lease_until_ms=clock_ms+120000,page_start=job['next_ms'],page_end=min(job['target_ms'],job['next_ms']+499*job['period_ms']))
        try:
            result=self._archive(job,samples,clock_ms=clock_ms)
            return dict(result,acquisition='local_archived_evidence',http_attempts=0)
        except BaseException:
            with self.transaction() as db:
                db.execute("UPDATE tracking_recovery SET state=?,lease_token=NULL,lease_owner=NULL,lease_until_ms=NULL WHERE job_key=? AND lease_token=?",(prior,job_key,token))
            raise

    def page(self,*,fetch,clock_ms,grant_id=None,page_budget=1,max_http_attempts=4,deadline_seconds=30,timeout_seconds=10,clock=None,monotonic=time.monotonic):
        if not callable(fetch): raise ValueError('explicit_source_adapter_required')
        integer(page_budget,'page_budget',1,4);integer(max_http_attempts,'http_budget',1,16)
        if type(deadline_seconds) not in (int,float) or not 0<deadline_seconds<=60: raise ValueError('recovery_deadline')
        if type(timeout_seconds) not in (int,float) or not 0<timeout_seconds<=30: raise ValueError('recovery_timeout')
        integer(clock_ms,'clock')
        started=monotonic()
        clock=clock or (lambda:clock_ms+int((monotonic()-started)*1000))
        deadline=started+deadline_seconds;results=[];used=0
        for _ in range(page_budget):
            if used>=max_http_attempts or monotonic()>=deadline: break
            try: job=self.claim(grant_id=grant_id,clock_ms=integer(clock(),'clock'),prefer_recent=not results)
            except RecoveryDenied as exc:
                results.append(dict(state='pending_authorization',error_code=str(exc)));break
            if not job: break
            limit=fetch.page_limit(job,max_http_attempts-used) if hasattr(fetch,'page_limit') else 500
            integer(limit,'adapter_page_limit',1,500)
            job['page_end']=min(job['target_ms'],job['page_start']+(limit-1)*job['period_ms'])
            gate=AttemptGate(self,job,grant_id,clock=clock,deadline=deadline,max_attempts=max_http_attempts-used,monotonic=monotonic,timeout_seconds=timeout_seconds)
            try:
                received=fetch(dict(job),start_ms=job['page_start'],end_ms=job['page_end'],limit=limit,attempt=gate.call)
                if gate.used==0: raise SourceFailure('adapter_did_not_use_attempt_gate')
                if gate.last_status is None or not 200<=gate.last_status<300: raise SourceFailure(str(gate.last_status or 'invalid_transport'))
                results.append(self._archive(job,received,clock_ms=integer(clock(),'clock')))
            except Exception as exc:
                code=exc.code if isinstance(exc,SourceFailure) else str(exc) if isinstance(exc,RecoveryDenied) else 'transient_'+type(exc).__name__
                with self.transaction() as db:
                    try: self.validate_claim(db,job,integer(clock(),'clock'))
                    except RecoveryDenied:
                        results.append(dict(job_key=job['job_key'],state='stale_lease',error_code='stale_recovery_lease'));continue
                    state,retry,error=('pending_authorization',None,code) if isinstance(exc,RecoveryDenied) else classify(code,job['attempt_count'],clock())
                    db.execute("UPDATE tracking_recovery SET state=?,next_retry_ms=?,error_code=?,attempt_count=attempt_count+?,lease_token=NULL,lease_owner=NULL,lease_until_ms=NULL,updated_ms=? WHERE job_key=?",(state,retry,error,gate.used,clock(),job['job_key']));self.bump(db,clock())
                results.append(dict(job_key=job['job_key'],state=state,error_code=error))
            finally: used+=gate.used
        return results
    def requeue(self,job_key,*,configuration_version,clock_ms):
        if not isinstance(configuration_version,str) or not configuration_version: raise ValueError('configuration_version_required')
        with self.transaction() as db:
            row=db.execute("SELECT identity_json FROM tracking_recovery WHERE job_key=?",(job_key,)).fetchone()
            if not row or not row[0]: raise ValueError('legacy_job_needs_verified_bridge')
            db.execute("INSERT OR IGNORE INTO recovery_migrations VALUES(?,?)",('requeue:'+job_key+':'+configuration_version,_canonical(dict(job_key=job_key,configuration_version=configuration_version,at=clock_ms))))
            db.execute("UPDATE tracking_recovery SET state='pending',next_retry_ms=NULL,error_code=NULL WHERE job_key=? AND state IN ('blocked_permission','blocked_configuration','unavailable_history')",(job_key,));self.bump(db,clock_ms)

def enqueue_gap_conn(db,gap):
    """Gap needs are retained even with no event. No implied acquisition grant.
    Closing one session never cancels shared evidence or claims an effect ACK."""
    detail=gap.get('detail') or {};source=gap.get('scope','').removeprefix('missing_')
    if source=='candles': source='candles_4h'
    if source not in ('candles_1h','candles_4h','oi','lsr','cap'): return
    market=detail.get('market');symbol=detail.get('symbol');target=detail.get('boundary_ms')
    if market not in ('USDT-M','COIN-M') or not symbol or type(target) is not int: return
    ensure_schema(db);key='gap:'+gap['gap_id']
    if gap.get('status')=='open':
        if db.execute("INSERT OR IGNORE INTO tracking_recovery(job_key,source,contract,timeframe,start_ms,target_ms,next_ms,state,error_code,updated_ms) VALUES(?,?,?,?,?,?,?,'blocked_configuration','adapter_contract_missing',?)",(key,source,market+'|'+symbol,'4h' if source=='candles_4h' else '1h',target,target,target,target)).rowcount: bump_conn(db,target)

def recovered_inputs(ledger,*,market,symbol,start_ms,target_ms,page_limit=12,acquisition_job_key=None):
    integer(page_limit,'input_page_budget',1,12)
    if not ledger._read("SELECT 1 FROM sqlite_master WHERE type='table' AND name='tracking_recovery_samples'"): return {}
    quality=None
    if acquisition_job_key is not None:
        anchor=ledger._read('SELECT contract,quality_version FROM tracking_recovery WHERE job_key=?',(acquisition_job_key,))
        if not anchor or anchor[0]['contract']!=market+'|'+symbol:raise ValueError('recovery_input_anchor_contract')
        quality=anchor[0]['quality_version']
    rows=ledger._read("""SELECT j.source,s.effective_ms,s.payload_json,s.available_ms,s.version,j.job_key FROM tracking_recovery j JOIN tracking_recovery_samples s ON s.job_key=j.job_key WHERE j.contract=? AND (? IS NULL OR j.quality_version IS ?) AND j.start_ms<=? AND j.target_ms>=? AND s.effective_ms>=? AND s.effective_ms<=? AND NOT EXISTS(SELECT 1 FROM tracking_recovery_samples newer WHERE newer.job_key=s.job_key AND newer.effective_ms=s.effective_ms AND (newer.available_ms>s.available_ms OR (newer.available_ms=s.available_ms AND newer.version>s.version))) ORDER BY j.source,s.effective_ms,s.available_ms,s.version LIMIT ?""",(market+'|'+symbol,acquisition_job_key,quality,target_ms,start_ms,start_ms,target_ms,page_limit*500+1))
    if len(rows)>page_limit*500: raise ValueError('recovery_input_window_page_required')
    result={}
    # Compatibility for pre-v2 immutable chunks. Never silently drop an old
    # receipt merely because the new indexed projection has not been populated.
    legacy=ledger._read("""SELECT j.source,c.payload_json,c.available_at_ms FROM tracking_recovery_chunks c JOIN tracking_recovery j USING(job_key) WHERE j.contract=? AND (? IS NULL OR j.quality_version IS ?) AND j.start_ms<=? AND j.target_ms>=? AND NOT EXISTS(SELECT 1 FROM tracking_recovery_samples s WHERE s.job_key=c.job_key AND s.version=c.version) ORDER BY c.available_at_ms DESC,c.version DESC LIMIT ?""",(market+'|'+symbol,acquisition_job_key,quality,target_ms,start_ms,page_limit+1))
    if len(legacy)>page_limit:raise ValueError('legacy_recovery_input_migration_page_required')
    for r in reversed(legacy):
        dest=result.setdefault(r['source'],{})
        for s in json.loads(r['payload_json']):
            t=s.get('effective_at_ms')
            if type(t) is int and start_ms<=t<=target_ms:dest[t]=dict(s,available_at_ms=max(s['available_at_ms'],r['available_at_ms']))
    for r in rows:
        dest=result.setdefault(r['source'],{});s=dict(json.loads(r['payload_json']),available_at_ms=r['available_ms']);t=r['effective_ms']
        if t in dest and dest[t]!=s: raise ValueError('recovery_ambiguous_acquisition_contract')
        dest[t]=s
    return {role:list(by.values()) for role,by in result.items()}

