"""Exact historical no-cycle evaluations, separate from the live C controller.

Missing evidence stays pending. A complete negative C0 is a business result,
written with the source effect ACK, without rewinding today's controller.
"""
import json
from . import c_rebound_rules as rules
from .ledger import LedgerReject, _canonical
from .tracking_engine import digest

CHECKS=('rsi','below_all_ema','oi_cap','lsr','new_lows14')


def root_checks(payload,boundary,clock_ms):
    known=lambda key:[s for s in payload.get(key,[]) if type(s.get('available_at_ms')) is int and s['available_at_ms']<=clock_ms]
    bars=rules.prepare_bars(payload['raw_bars'],target_close_ms=boundary,
        oi_samples=known('oi_samples'),lsr_samples=known('lsr_samples'),caps=known('caps'))
    return rules.c0(bars,boundary-rules.H4)


def proof_key(step):
    return 'c-recovery-root|'+step['dependency_id']+'|'+str(step['epoch'])+'|'+str(step['boundary_ms'])


def recover_no_cycle(ledger,dependency,step,inputs,*,clock_ms,source_version):
    """Return None only when the full cycle engine can own a positive root."""
    boundary=step['boundary_ms'];market,symbol=dependency['contract'].split('|',1)
    payload=dict(inputs,market=market,symbol=symbol,target_close_ms=boundary,
        source_version=source_version,recovery_step=step,c_recovery_no_cycle=True)
    checks=root_checks(payload,boundary,clock_ms)
    missing=[key for key in CHECKS if checks.get(key) not in ('pass','fail')]
    if missing:
        return dict(ok=True,pending=True,evaluation_acked=False,
            waiting_reason='C_historical_root_missing_evidence',missing_checks=missing)
    if checks['c0']=='pass':
        raw=ledger.get_cursor('c-controller|'+dependency['contract']+'|'+rules.RULE_VERSION)
        state=json.loads(raw) if raw else {}
        branch_raw=ledger.get_cursor('c-recovery-controller|'+dependency['contract']+'|'+rules.RULE_VERSION+'|'+str(boundary-rules.H4))
        branch=json.loads(branch_raw) if branch_raw else {}
        if ((state.get('last_seen_close_ms') or 0)>boundary and state.get('t0')==boundary-rules.H4) or (branch.get('last_seen_close_ms') or 0)>=boundary:
            # The same candidate is already further along. Prove this initial
            # root without replacing its later event/session with "waiting".
            event_id=f'C|c_cycle|{market}|{symbol}|4h|{rules.RULE_VERSION}|{boundary-rules.H4}'
            events=ledger._read('SELECT payload_json FROM event_revisions WHERE event_id=? ORDER BY revision DESC LIMIT 1',(event_id,))
            if events:
                payload['c_recovery_existing_cycle']=event_id
                proof=dict(boundary_ms=boundary,checks=checks,rule_version=rules.RULE_VERSION,event_id=event_id)
                return ledger.consume_and_commit(source_message_key='c-recovery-existing|'+digest(payload),
                    message_source='c_recovery_existing_cycle',message_payload=payload,recovery_effect=step,
                    cursor_updates={proof_key(step):_canonical(proof)})
            return dict(ok=True,pending=True,evaluation_acked=False,waiting_reason='C_current_cycle_event_missing',missing_checks=[])
        # Reconstruct the old candidate on its own durable controller. Today's
        # live controller remains byte-for-byte unchanged; subsequent slices
        # continue this branch using actual archived evidence.
        if (state.get('last_seen_close_ms') or 0)>boundary and state.get('t0')!=boundary-rules.H4:
            from .c_rebound_engine import CReboundEngine
            from .tracking_engine import iso
            return CReboundEngine(ledger).consume(market=market,symbol=symbol,
                **{key:inputs.get(key,[]) for key in ('raw_bars','oi_samples','lsr_samples','caps')},
                target_close_ms=boundary,clock_iso=iso(clock_ms),source_version=source_version,
                historical_import=True,source_revision=True,recovery_step=step,recovery_cycle_t0=boundary-rules.H4)
        return None
    proof=dict(boundary_ms=boundary,checks=checks,rule_version=rules.RULE_VERSION)
    key='c-recovery-no-cycle|'+digest(payload)
    return ledger.consume_and_commit(source_message_key=key,message_source='c_recovery_no_cycle',
        message_payload=payload,recovery_effect=step,cursor_updates={proof_key(step):_canonical(proof)})


def validate_no_cycle_effect(payload,cursor_updates,dependency,boundary,clock_ms):
    if dependency['event_id'] is not None:raise LedgerReject('RECOVERY_no_cycle_event_conflict')
    checks=root_checks(payload,boundary,clock_ms)
    if any(checks.get(k) not in ('pass','fail') for k in CHECKS) or checks.get('c0')!='fail':
        raise LedgerReject('RECOVERY_business_evidence_incomplete')
    proof=dict(boundary_ms=boundary,checks=checks,rule_version=rules.RULE_VERSION)
    if (cursor_updates or {}).get(proof_key(payload['recovery_step']))!=_canonical(proof):
        raise LedgerReject('RECOVERY_no_cycle_proof_mismatch')
    return boundary


def validate_existing_cycle_effect(db,payload,cursor_updates,dependency,boundary,clock_ms):
    if dependency['event_id'] is not None:raise LedgerReject('RECOVERY_no_cycle_event_conflict')
    checks=root_checks(payload,boundary,clock_ms)
    if any(checks.get(k) not in ('pass','fail') for k in CHECKS) or checks.get('c0')!='pass':raise LedgerReject('RECOVERY_business_evidence_incomplete')
    market,symbol=dependency['contract'].split('|',1)
    event_id=f'C|c_cycle|{market}|{symbol}|4h|{rules.RULE_VERSION}|{boundary-rules.H4}'
    if payload.get('c_recovery_existing_cycle')!=event_id or not db.execute('SELECT 1 FROM event_revisions WHERE event_id=? LIMIT 1',(event_id,)).fetchone():raise LedgerReject('RECOVERY_existing_cycle_missing')
    proof=dict(boundary_ms=boundary,checks=checks,rule_version=rules.RULE_VERSION,event_id=event_id)
    if (cursor_updates or {}).get(proof_key(payload['recovery_step']))!=_canonical(proof):raise LedgerReject('RECOVERY_no_cycle_proof_mismatch')
    return boundary


def drain_cycles(root,ledger,*,clock_ms,allowed=lambda:None):
    """One durable historical candidate per slice; never rewinds the live controller."""
    from pathlib import Path
    from .recovery import Recovery
    from .c_rebound_engine import CReboundEngine
    from .tracking_engine import iso
    from collection_local import _scan,local_samples
    import tracking_service
    recovery=Recovery(ledger)
    with recovery.transaction() as db:
        db.execute('CREATE TABLE IF NOT EXISTS c_recovery_continuations(state_key TEXT PRIMARY KEY,next_retry_ms INTEGER,last_scheduled_ms INTEGER,input_sha TEXT,error TEXT)')
        row=db.execute("SELECT c.cursor_name,c.cursor_value,p.input_sha FROM cursors c LEFT JOIN c_recovery_continuations p ON p.state_key=c.cursor_name WHERE c.cursor_name LIKE 'c-recovery-controller|%' AND COALESCE(p.next_retry_ms,0)<=? ORDER BY COALESCE(p.last_scheduled_ms,0),c.cursor_name LIMIT 1",(clock_ms,)).fetchone()
    if not row:return dict(ok=True,count=0,items=[])
    key=row['cursor_name'];prior_sha=row['input_sha'];state=json.loads(row['cursor_value'])
    _,market,symbol,version,t0=key.split('|');t0=int(t0)
    result=None;error=None;sha=prior_sha
    try:
        allowed();path=Path(root)/'scan.json'
        stat=path.stat();_,scan=_scan(str(path),stat.st_size,stat.st_mtime_ns)
        item=next((r for r in scan.get('rows',[]) if r.get('symbol')==symbol and ('USDT-M' if r.get('market')=='USDⓈ-M' else r.get('market'))==market),None)
        if item is None:raise ValueError('C_historical_contract_not_in_latest_scan')
        target=item['candleAsOf']+1
        if target<t0+rules.H4:raise ValueError('C_historical_scan_before_candidate')
        inputs=tracking_service.source_input(root,item,target)
        inputs.pop('_source_revised',None)
        # Reuse only the archived cap values at required historical boundaries.
        from .runtime import registry
        meta=registry(root)
        by={s['effective_at_ms']:s for s in inputs.get('caps',[])}
        for boundary in range(t0+rules.H4,min(target,t0+11*rules.H4)+1,rules.H4):
            job=dict(contract=market+'|'+symbol,source='cap',next_ms=boundary,start_ms=boundary,target_ms=boundary,period_ms=3600000)
            for sample in local_samples(root,job,meta,clock_ms):by[sample['effective_at_ms']]=sample
        inputs['caps']=[by[t] for t in sorted(by)]
        sha=digest(dict(inputs=inputs,target=target))
        if sha!=prior_sha:
            allowed()
            result=CReboundEngine(ledger).consume(market=market,symbol=symbol,**inputs,
                target_close_ms=target,clock_iso=iso(clock_ms),source_version='historical-cycle-'+sha,
                historical_import=True,source_revision=tracking_service.requires_c_replay(ledger,market,symbol,inputs,cycle_t0=t0),recovery_cycle_t0=t0)
            if not result.get('ok'):error=str(result.get('errors') or result)[:1000]
        else:result=dict(ok=True,unchanged=True)
    except Exception as exc:error=type(exc).__name__+':'+str(exc)[:1000]
    with recovery.transaction() as db:
        db.execute('INSERT OR REPLACE INTO c_recovery_continuations VALUES(?,?,?,?,?)',(key,clock_ms+60000,clock_ms,prior_sha if error else sha,error))
    return dict(ok=error is None,count=1,items=[dict(state_key=key,error=error,result=result)],network_attempts=0)
