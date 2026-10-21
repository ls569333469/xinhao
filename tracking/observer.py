from .tracking_engine import observation_notices
"""Bounded contract-wide B/C observation: one prepared input shared by sessions."""
import json
from .control_contract import observation_bar_source
from .tracking_engine import (digest,consumed,latest,advance_observation,_canonical,H4,milliseconds,corrected_session,retire_for_revision,transition)

def observe_contract(ledger, *, market,symbol,bars,target_close_ms,clock_iso,source_version,
                     after_session_id='',limit=100,recovery_step=None):
    if type(limit) is not int or not 1<=limit<=100 or len(bars)>1499:raise ValueError('observation_bound')
    if target_close_ms%H4 or milliseconds(clock_iso)<target_close_ms:raise ValueError('observation_target')
    event_filter='';params=[market,symbol,after_session_id]
    if recovery_step is not None:
        dep=ledger._read('SELECT event_id FROM recovery_dependencies WHERE dependency_id=?',(recovery_step['dependency_id'],))
        if not dep or not dep[0][0]:raise ValueError('observation_recovery_event_required')
        event_filter=' AND s.event_id=?';params.append(dep[0][0])
    rows=ledger._read("SELECT s.payload_json FROM sessions s JOIN event_revisions e ON e.event_id=s.event_id WHERE e.revision=(SELECT MAX(x.revision) FROM event_revisions x WHERE x.event_id=e.event_id) AND json_extract(e.payload_json,'$.market')=? AND e.symbol=? AND e.strategy IN ('B','C') AND s.observation_status<>'ended' AND s.session_id>?"+event_filter+" ORDER BY s.session_id LIMIT ?",tuple(params+[limit+1]))
    if recovery_step is not None and not rows:raise ValueError('observation_recovery_session_not_active')
    results=[]
    for row in rows[:limit]:
        session=json.loads(row[0]);eid=session['event_id'];record=latest(ledger,eid)
        context_key='tracking.context|'+eid;old_context=ledger.get_cursor(context_key);context=json.loads(old_context) if old_context else {}
        prior_key=context.get('observation_input_key');prior=ledger.get_inbox(prior_key) if prior_key else None
        retired=[];start_transitions=[];revised=False
        if record['strategy']=='B' and prior:
            previous={b['open_ms']:b for b in prior['payload'].get('bars',[])}
            revised=any(b['open_ms'] in previous and digest(observation_bar_source(b))!=digest(observation_bar_source(previous[b['open_ms']])) for b in bars)
            if revised:
                source_epoch=digest(dict(prior=prior_key,bars=bars))
                retired=[retire_for_revision(session,dict(record,evidence_hash=source_epoch),session['last_evaluated_bar_close_ms'],clock_iso)]
                session=corrected_session(record,session['tracking_profile_id'],start=session['started_at_bar_close_ms'],reference=session['reference'],source_epoch=source_epoch)
                start_transitions=[transition(session,None,'observing','session_started',session['started_at_bar_close_ms'],clock_iso,source_epoch)]
        payload=dict(event_id=eid,session_id=session['session_id'],bars=bars,target=target_close_ms,source_version=source_version,prior_input_key=prior_key if revised else None)
        if recovery_step is not None:payload['recovery_step']=recovery_step
        key='observer|'+session['session_id']+'|'+digest(payload)
        done=consumed(ledger,key,payload)
        if done:results.append(done);continue
        sess,trs,gaps,notices=advance_observation(ledger,dict(record,evidence_hash=digest(payload)),session,bars,clock=clock_iso,target=target_close_ms)
        notices+=observation_notices(record,trs) if not revised else []
        ck=context_key;old=old_context;context['observation_input_key']=key
        context['observation']=dict(status=sess['observation_status'],end_reason=sess['end_reason'],profile_id=sess['tracking_profile_id'],last_evaluated_bar_close_ms=sess['last_evaluated_bar_close_ms'])
        if record['strategy']=='C' and record['technical_status']=='expired':
            from .c_rebound_rules import expired_followup
            context['expired_followup']=expired_followup(bars,t0_open_ms=record['trigger_bar_open_ms'],
                terminal_close_ms=session['started_at_bar_close_ms'],evaluated_close_ms=sess['last_evaluated_bar_close_ms'],
                previous=context.get('expired_followup'))
        results.append(ledger.consume_and_commit(source_message_key=key,message_source='observation_source_revision' if revised else 'contract_closed_4h',message_payload=payload,
            session=sess,transitions=start_transitions+trs,gaps=gaps,retired_sessions=retired,extra_outbox=notices if record['discovery']=='live' else [],
            cursor_updates={ck:_canonical(context)},expected_cursors={ck:old},recovery_effect=recovery_step))
    return dict(ok=all(r.get('ok') for r in results),items=results,
        continuation=json.loads(rows[limit-1][0])['session_id'] if len(rows)>limit else None,shared_input_reads=1)
