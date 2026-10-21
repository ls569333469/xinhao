"""Recovery effect ACK companion, validated inside the business transaction.

An archive receipt never advances a consumer. The engine must prove the same
root in its committed session/controller/record and use the archived inputs.
"""
import hashlib,json
from .ledger import _canonical,LedgerReject
from .recovery import bump_conn

def expected_rule(consumer):
    return 'c-1015-v1.1' if consumer=='C_strategy' else 'b-accum-1h-v1' if consumer=='B_strategy' else 'ema-squeeze-v4' if consumer=='A_strategy' else 'tracking-observation-v1.1.0'

FIELDS={'oi':'oi_samples','lsr':'lsr_samples','cap':'caps','candles_4h':'raw_bars'}

def input_jobs_conn(db,dependency):
    """Only explicitly bound, identical frozen contexts share recovered roles."""
    d=dependency
    own=db.execute('SELECT job_key,source,epoch,quality_version FROM tracking_recovery WHERE job_key=?',(d['job_key'],)).fetchone()
    jobs={own['job_key']:{k:own[k] for k in ('job_key','source','epoch')}}
    rows=db.execute("""SELECT DISTINCT j.job_key,j.source,j.epoch FROM recovery_replay_contexts original
        JOIN recovery_replay_contexts c ON c.input_sha=original.input_sha
        JOIN recovery_dependencies n ON n.dependency_id=c.dependency_id AND n.input_epoch=c.epoch
        JOIN tracking_recovery j ON j.job_key=n.job_key AND j.epoch=n.input_epoch
        WHERE original.dependency_id=? AND original.epoch=? AND n.consumer=? AND n.rule_version=?
        AND n.event_id IS ? AND j.contract=? AND j.provider IS NOT NULL
        AND n.start_ms<=? AND n.target_ms>=? AND j.quality_version IS ? LIMIT 6""",
        (d['dependency_id'],d['input_epoch'],d['consumer'],d['rule_version'],d['event_id'],
         d['contract'],d['next_ms'],d['next_ms'],own['quality_version'])).fetchall()
    # Mapping corrections create a new immutable job identity for every source.
    # A shared frozen context alone must not mix those quality generations.
    # Retain both histories; each consumer binds only its own identity generation.
    if len(rows)>4:raise ValueError('recovery_ambiguous_source_bindings')
    for r in rows:jobs[r['job_key']]=dict(r)
    if len({j['source'] for j in jobs.values()})!=len(jobs):raise ValueError('recovery_ambiguous_source_bindings')
    return sorted(jobs.values(),key=lambda j:j['job_key'])

def input_refs_conn(db,dependency):
    refs=[]
    for job in input_jobs_conn(db,dependency):
        rows=db.execute("""SELECT DISTINCT s.version FROM tracking_recovery_samples s
            WHERE s.job_key=? AND s.effective_ms<=? AND NOT EXISTS(SELECT 1
            FROM tracking_recovery_samples n WHERE n.job_key=s.job_key AND n.effective_ms=s.effective_ms
            AND (n.available_ms>s.available_ms OR (n.available_ms=s.available_ms AND n.version>s.version)))
            ORDER BY s.version LIMIT 17""",(job['job_key'],dependency['next_ms'])).fetchall()
        refs.extend(dict(job_key=job['job_key'],version=r['version']) for r in rows)
    if len(refs)>16:raise ValueError('recovery_effect_input_page_required')
    return refs

def prepare_step(ledger,dependency_id):
    db=ledger._connect()
    try:
        db.execute('BEGIN')
        row=db.execute("SELECT d.*,j.epoch,j.contract,j.source FROM recovery_dependencies d JOIN tracking_recovery j USING(job_key) WHERE dependency_id=?",(dependency_id,)).fetchone()
        if not row:raise ValueError('recovery_dependency_missing')
        d=dict(row)
        if d['state']=='not_required' or d['next_ms']>d['target_ms']:return None
        if d['input_epoch']!=d['epoch']:raise ValueError('recovery_dependency_epoch_mismatch')
        if d['rule_version']!=expected_rule(d['consumer']):raise ValueError('recovery_rule_version_mismatch')
        refs=input_refs_conn(db,d)
        if not any(r['job_key']==d['job_key'] for r in refs):return None
        return dict(dependency_id=dependency_id,epoch=d['epoch'],boundary_ms=d['next_ms'],input_refs=refs)
    finally:db.close()

def bind_payload(payload,step):
    if step is not None: payload['recovery_step']=step
    return payload

def _sample_present(payload,source,sample,received_ms):
    # The actual engine payload, rather than an arbitrary declared receipt, must
    # contain the same source values. Arrival can only become later, never earlier.
    if source=='candles_1h':return False  # original 1H cache receipt is not a consumer effect
    if source=='candles_4h':
        bar=sample.get('bar')
        rows=payload.get('raw_bars',payload.get('bars',[]))
        return isinstance(bar,dict) and any(all(b.get(k)==bar.get(k) for k in ('open_ms','o','h','l','c','q','q_unit') if k in bar) for b in rows)
    for row in payload.get(FIELDS[source],[]):
        if all(row.get(k)==sample.get(k) for k in ('effective_at_ms','unit','value','trusted') if k in sample) and row.get('available_at_ms',-1)>=received_ms: return True
    # B's immutable repair revision must itself become available after receipt.
    from .tracking_engine import milliseconds
    b_available=payload.get('last_evaluated_at') or payload.get('first_seen_at')
    b_received=False
    if isinstance(b_available,str):
        try:b_received=milliseconds(b_available)>=received_ms
        except (ValueError,TypeError):pass
    # B original evidence carries immutable as-of samples in its source payload.
    for field in ('structure_h','structure_confirmation'):
        st=payload.get(field) or {};r=st.get(source+'_sample') if source!='cap' else st.get('cap')
        if not r or not b_received: continue
        if source=='cap':
            if r.get('capUsd')==sample.get('value') and r.get('capTrusted')==sample.get('trusted') and r.get('capObservedAt')==sample.get('effective_at_ms'): return True
        elif r.get('timestamp')==sample.get('effective_at_ms') and r.get('sumOpenInterestValue' if source=='oi' else 'longShortRatio')==sample.get('value') and sample.get('unit')==('USD' if source=='oi' else 'ratio'): return True
    # A original OI is a specific published as-of scalar, never today's value.
    row=payload.get('row') or {}
    available=payload.get('available_at');a_received=False
    if isinstance(available,str):
        try:a_received=milliseconds(available)>=received_ms
        except (ValueError,TypeError):pass
    return a_received and source=='oi' and row.get('oiAsOf')==sample.get('effective_at_ms') and row.get('oiUsd')==sample.get('value') and sample.get('unit')=='USD'

def apply_effect_conn(db,step,*,record,session,message,cursor_updates,now_ms):
    if not isinstance(step,dict) or set(step)!={'dependency_id','epoch','boundary_ms','input_refs'}: raise LedgerReject('RECOVERY_invalid_effect')
    d=db.execute("SELECT d.*,j.epoch,j.contract,j.source FROM recovery_dependencies d JOIN tracking_recovery j USING(job_key) WHERE dependency_id=?",(step['dependency_id'],)).fetchone()
    if not d or d['state']=='not_required' or type(step['epoch']) is not int or step['epoch']!=d['epoch'] or d['input_epoch']!=d['epoch']: raise LedgerReject('RECOVERY_stale_dependency')
    if d['rule_version']!=expected_rule(d['consumer']):raise LedgerReject('RECOVERY_rule_version_mismatch')
    boundary=step['boundary_ms']
    if type(boundary) is not int or boundary!=d['next_ms'] or boundary>d['target_ms']: raise LedgerReject('RECOVERY_skip_unprocessed_root')
    payload=(message or {}).get('payload') or {}
    if payload.get('recovery_step')!=step: raise LedgerReject('RECOVERY_input_not_bound')
    identity=record or session or {}
    if d['event_id'] and identity.get('event_id')!=d['event_id']: raise LedgerReject('RECOVERY_wrong_event')
    market,symbol=d['contract'].split('|',1)
    row=payload.get('row') or {}
    pmarket=payload.get('market',row.get('market',market))
    pmarket='USDT-M' if pmarket=='USDⓈ-M' else pmarket
    if pmarket!=market or payload.get('symbol',row.get('symbol',symbol))!=symbol: raise LedgerReject('RECOVERY_wrong_contract')
    expected_strategy=d['consumer'].split('_')[0]
    if identity:
        existing=db.execute('SELECT payload_json FROM event_revisions WHERE event_id=? ORDER BY revision DESC LIMIT 1',(identity.get('event_id'),)).fetchone()
        event=record or (json.loads(existing[0]) if existing else {})
        if event.get('strategy')!=expected_strategy or event.get('market')!=market or event.get('symbol')!=symbol:raise LedgerReject('RECOVERY_wrong_consumer')
    refs=step['input_refs']
    if not isinstance(refs,list) or not 1<=len(refs)<=16 or len({_canonical(r) for r in refs})!=len(refs): raise LedgerReject('RECOVERY_input_refs')
    # Recheck inside the business transaction: an archive revision between
    # prepare and commit must invalidate the step, even when values are equal.
    if refs!=input_refs_conn(db,dict(d)):raise LedgerReject('RECOVERY_input_version_changed')
    jobs={j['job_key']:j for j in input_jobs_conn(db,dict(d))}
    matched=set()
    for ref in refs:
        if not isinstance(ref,dict) or set(ref)!={'job_key','version'} or ref['job_key'] not in jobs:raise LedgerReject('RECOVERY_foreign_input')
        job=jobs[ref['job_key']]
        chunk=db.execute("SELECT * FROM tracking_recovery_chunks WHERE job_key=? AND version=?",(ref['job_key'],ref['version'])).fetchone()
        if not chunk or hashlib.sha256(chunk['payload_json'].encode()).hexdigest()!=ref['version'] or chunk['available_at_ms']>now_ms:raise LedgerReject('RECOVERY_unverified_chunk')
        latest_sample=db.execute("SELECT effective_ms,version FROM tracking_recovery_samples WHERE job_key=? AND effective_ms<=? ORDER BY effective_ms DESC,available_ms DESC,version DESC LIMIT 1",(job['job_key'],boundary)).fetchone()
        for sample in json.loads(chunk['payload_json']):
            if latest_sample and sample.get('effective_at_ms')==latest_sample['effective_ms'] and ref['version']==latest_sample['version'] and _sample_present(payload,job['source'],sample,max(sample['available_at_ms'],chunk['available_at_ms'])):matched.add(job['job_key'])
    required={r['job_key'] for r in refs}
    if d['job_key'] not in required or matched!=required:raise LedgerReject('RECOVERY_archived_input_not_used')
    actual=None
    if d['consumer'].endswith('_observation'):
        actual=(session or {}).get('last_evaluated_bar_close_ms')
    elif record is not None:
        actual=record.get('decision_bar_close_ms')
        if record.get('missing_data_flags'):raise LedgerReject('RECOVERY_business_evidence_incomplete')
    elif d['consumer']=='C_strategy' and payload.get('c_recovery_existing_cycle'):
        from .c_recovery import validate_existing_cycle_effect
        actual=validate_existing_cycle_effect(db,payload,cursor_updates,d,boundary,now_ms)
    elif d['consumer']=='C_strategy' and payload.get('c_recovery_no_cycle') is True:
        from .c_recovery import validate_no_cycle_effect
        actual=validate_no_cycle_effect(payload,cursor_updates,d,boundary,now_ms)
    elif d['consumer']=='C_strategy':
        state_key='c-controller|'+d['contract']+'|'+d['rule_version']
        state=json.loads((cursor_updates or {}).get(state_key,'{}'))
        actual=state.get('evaluated_close_ms')
        if state.get('t0') is not None: raise LedgerReject('RECOVERY_no_event_requires_controller_only')
    if actual!=boundary: raise LedgerReject('RECOVERY_business_root_not_evaluated',actual=actual,expected=boundary)
    effect_key=hashlib.sha256(_canonical(step).encode()).hexdigest()
    result_hash=hashlib.sha256(_canonical(dict(record=record,session=session,cursors=cursor_updates)).encode()).hexdigest()
    db.execute("INSERT INTO recovery_effects VALUES(?,?,?,?,?,?,?,?)",(effect_key,d['dependency_id'],d['epoch'],boundary,_canonical(refs),message['key'],result_hash,now_ms))
    next_ms=boundary+d['step_ms']
    db.execute("UPDATE recovery_dependencies SET next_ms=?,evaluated_ms=?,state=?,reason=NULL WHERE dependency_id=?",(next_ms,boundary,'evaluated' if next_ms>d['target_ms'] else 'pending',d['dependency_id']))
    # Close the original gap only after its exact canonical consumers have
    # committed every required root. Archiving alone never reaches this branch.
    tables={r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name IN ('recovery_origin_links','recovery_bridge_results')")}
    if len(tables)==2:
        links=db.execute("""SELECT l.origin_key FROM recovery_origin_links l
          JOIN recovery_bridge_results b ON b.legacy_key=l.origin_key AND b.canonical_key=l.job_key AND b.state='verified'
          WHERE l.job_key=? AND l.origin_key LIKE 'gap:%'
          AND NOT EXISTS(SELECT 1 FROM recovery_dependencies old WHERE old.job_key=l.origin_key
            AND NOT EXISTS(SELECT 1 FROM recovery_dependencies n WHERE n.job_key=l.job_key
              AND n.consumer=old.consumer AND n.rule_version=old.rule_version AND n.event_id IS old.event_id
              AND n.start_ms<=old.start_ms AND n.target_ms>=old.target_ms AND n.step_ms=old.step_ms
              AND n.next_ms>old.target_ms AND n.state='evaluated')) LIMIT 100""",(d['job_key'],)).fetchall()
        from .tracking_engine import iso
        for link in links:
            db.execute("UPDATE gaps SET status='resolved',updated_at_utc=? WHERE gap_id=? AND status='open'",(iso(now_ms),link['origin_key'][4:]))
    # No MAX across independent consumers; job means downloaded evidence only.
    bump_conn(db,now_ms)
    return dict(effect_key=effect_key,dependency_id=d['dependency_id'],evaluated_ms=boundary,epoch=d['epoch'])

