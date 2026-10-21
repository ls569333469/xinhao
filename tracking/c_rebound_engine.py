"""Persistent C0/C1 cycles, complete rearm, and independent post-cycle tracking.

Only explicit published input drives this engine; opening a page never runs it.
Waiting cycles are controller records, not a second mutable session identity.
After confirmation/expiry the frozen observation profile gets its own session.
"""
from __future__ import annotations
from .tracking_engine import observation_notices
import json
import copy
from .control_contract import CONTROL_SCHEMA, SUPERSEDED

from . import c_rebound_rules as rules
from .source_quality import quality
from .tracking_engine import (H4,PROFILE,_canonical,digest,iso,milliseconds,consumed,
    latest,source_ref,event_record,new_session,transition,advance_observation,corrected_session,retire_for_revision)


class CReboundEngine:
    def __init__(self,ledger):self.ledger=ledger

    def consume(self, *, market, symbol, raw_bars, oi_samples, lsr_samples, caps,
                target_close_ms, clock_iso, source_version, historical_import=True,
                source_revision=False, human_stop=None,recovery_step=None,recovery_cycle_t0=None):
        if market not in ('USDT-M','COIN-M') or not symbol or not symbol.replace('_','').isalnum() or symbol.upper()!=symbol:
            raise ValueError('C_invalid_contract')
        if type(target_close_ms) is not int or target_close_ms%H4 or milliseconds(clock_iso)<target_close_ms:
            raise ValueError('C_invalid_target')
        for name, samples in [('oi',oi_samples),('lsr',lsr_samples),('cap',caps)]:
            if not isinstance(samples,list) or len(samples)>6000 or any(not isinstance(s,dict) for s in samples):
                raise ValueError('C_source_page_bound_'+name)
        # Availability is independent of effective time. A retrospective source
        # can be used only after it has actually arrived, and stays historical.
        known=lambda rows:[s for s in rows if type(s.get('available_at_ms')) is int and s['available_at_ms']<=milliseconds(clock_iso)]
        bars=rules.prepare_bars(raw_bars,target_close_ms=target_close_ms,
            oi_samples=known(oi_samples),lsr_samples=known(lsr_samples),caps=known(caps))
        state_key=f'c-controller|{market}|{symbol}|{rules.RULE_VERSION}'
        if recovery_cycle_t0 is not None:
            if not historical_import or type(recovery_cycle_t0) is not int or recovery_cycle_t0<0 or recovery_cycle_t0%H4 or recovery_cycle_t0+H4>target_close_ms:raise ValueError('C_invalid_recovery_cycle')
            state_key=f'c-recovery-controller|{market}|{symbol}|{rules.RULE_VERSION}|{recovery_cycle_t0}'
        old_state=self.ledger.get_cursor(state_key)
        state=json.loads(old_state) if old_state else dict(t0=None,terminal=None,armed=True,
            last_seen_close_ms=None,evaluated_close_ms=None,first_unresolved_close_ms=None)
        if recovery_cycle_t0 is not None and old_state is None:state.update(t0=recovery_cycle_t0,armed=False)
        target_open=target_close_ms-H4
        if state['last_seen_close_ms'] is not None and target_close_ms<state['last_seen_close_ms'] and not source_revision:
            raise ValueError('C_boundary_regression_requires_source_revision')
        if state['last_seen_close_ms'] is not None and target_close_ms>state['last_seen_close_ms']+H4 and state['t0'] is None:
            raise ValueError('C_initial_or_rearm_recovery_requires_adjacent_pages')
        payload=dict(market=market,symbol=symbol,raw_bars=raw_bars,oi_samples=oi_samples,
            lsr_samples=lsr_samples,caps=caps,target_close_ms=target_close_ms,
            source_version=source_version,source_revision=source_revision,human_stop=human_stop)
        if recovery_cycle_t0 is not None:payload['recovery_cycle_t0']=recovery_cycle_t0
        if recovery_step is not None: payload['recovery_step']=recovery_step
        key=f'c-input|{market}|{symbol}|{digest(payload)}'
        done=consumed(self.ledger,key,payload)
        if done:return done
        chk=rules.c0(bars,target_open)
        complete=all(chk.get(k) in ('pass','fail') for k in ('rsi','below_all_ema','oi_cap','lsr','new_lows14'))
        previous_t0=state['t0']
        if state['terminal'] is not None and not source_revision and recovery_cycle_t0 is None:
            # Terminating a cycle and rearming on that same root are separate.
            if target_close_ms>state['terminal'] and complete and chk['c0']=='fail':state['armed']=True
            if target_close_ms>state['terminal'] and state['armed'] and complete and chk['c0']=='pass':
                state.update(t0=target_open,terminal=None,armed=False)
        elif state['t0'] is None and (state['terminal'] is None or state['armed']) and complete and chk['c0']=='pass':
            state.update(t0=target_open,armed=False)
        state['source_quality']=quality(raw_bars,oi_samples,lsr_samples,caps,target=min(target_close_ms,state['t0']+11*H4) if state['t0'] is not None else target_close_ms,
            clock=milliseconds(clock_iso),first_required=state['t0']+H4 if state['t0'] is not None else target_close_ms,
            evaluated=state.get('evaluated_close_ms'))
        state['last_seen_close_ms']=max(target_close_ms,state['last_seen_close_ms'] or 0)
        if state['t0'] is None:
            # No-cycle observations remain a denominator/quality fact, never a
            # fake candidate. A false but incomplete C0 cannot arm another cycle.
            state['last_c0']=chk
            state['evaluated_close_ms']=target_close_ms if complete else state['evaluated_close_ms']
            gaps=[]
            for role,items in [('oi',known(oi_samples)),('lsr',known(lsr_samples)),('cap',known(caps))]:
                from .c_rebound_rules import sample_at
                eligible=sample_at(items,target_close_ms,'value',lag=3600000 if role=='cap' else 300000,unit='ratio' if role=='lsr' else 'USD')
                if eligible is None or (role=='cap' and eligible.get('trusted') is not True):
                    gid=digest(['C',market,symbol,role,target_close_ms])
                    gaps.append(dict(gap_id=gid,session_id=None,scope=role,status='open',detail=dict(market=market,symbol=symbol,boundary_ms=target_close_ms,reason='missing_'+role,consumer='C_strategy',rule_version=rules.RULE_VERSION,event_id=None)))
            return self.ledger.consume_and_commit(source_message_key=key,message_source='c_published_4h',recovery_effect=recovery_step,
                message_payload=payload,gaps=gaps,cursor_updates={state_key:_canonical(state)},expected_cursors={state_key:old_state})
        t0=state['t0'];eid=f'C|c_cycle|{market}|{symbol}|4h|{rules.RULE_VERSION}|{t0}'
        prior_context_raw=self.ledger.get_cursor(f'tracking.context|{eid}')
        prior_context=json.loads(prior_context_raw) if prior_context_raw else {}
        frozen_t0=prior_context.get('prepared_t0')
        if frozen_t0 and not source_revision:
            bars=[frozen_t0 if b['open_ms']==t0 else b for b in bars]
        if frozen_t0 and not source_revision:
            def preserve(samples,old):
                if not old:return samples
                matches=[s for s in samples if s.get('effective_at_ms')==old.get('effective_at_ms')]
                semantic=lambda s:{k:s.get(k) for k in ('effective_at_ms','unit','value','trusted')}
                if matches and any(semantic(s)!=semantic(old) for s in matches):
                    raise ValueError('C_historical_source_change_requires_source_revision')
                return samples+[old] if not matches else samples
            frozen_structure=frozen_t0.get('structure') or {}
            oi_samples=preserve(oi_samples,frozen_structure.get('oi_sample'))
            lsr_samples=preserve(lsr_samples,frozen_structure.get('lsr_sample'))
            caps=preserve(caps,frozen_structure.get('cap_sample'))
        cycle=(prior_context.get('cycle') if state['terminal'] is not None and not source_revision and previous_t0==t0 else None) or rules.evaluate_cycle(bars,t0,target_close_ms)
        if cycle['status']=='no_cycle':
            checks=rules.c0(bars,t0)
            full=all(checks.get(k) in ('pass','fail') for k in ('rsi','below_all_ema','oi_cap','lsr','new_lows14'))
            if not source_revision or not full or checks['c0']!='fail':
                return dict(ok=False,errors=[dict(code='C_trigger_context_unresolved',event_id=eid,c0=checks)])
            # Explicit source retraction is a control action, never market invalidation.
            ref=source_ref('candles_4h',payload,ref='c-retraction:'+eid+':'+digest(payload),
                version=digest(payload),boundary=t0+H4,available_at=clock_iso)
            record=event_record(self.ledger,eid=eid,strategy='C',market=market,symbol=symbol,
                trigger=t0,decision=t0+H4,status='unknown',qualification='fail',sources=[ref],
                clock_iso=clock_iso,discovery='late_backfill',reason='source_revision')
            session=self.ledger.get_session(eid);trs=[]
            if session and session['observation_status']!='ended':
                close=max(target_close_ms,session['last_evaluated_bar_close_ms'])
                tr=transition(session,session['observation_status'],'ended',SUPERSEDED,close,clock_iso,record['evidence_hash'])
                tr['schema_version']=CONTROL_SCHEMA;trs=[tr]
                session.update(schema_version=CONTROL_SCHEMA,observation_status='ended',end_reason=SUPERSEDED,
                    ended_at_bar_close_ms=close,last_evaluated_bar_close_ms=close)
            context=dict(cycle=dict(cycle,status='source_retracted'),source_revision=True,
                observation=dict(status='ended',end_reason=SUPERSEDED),retracted=True,original_cycle_preserved=True)
            state.update(t0=None,terminal=target_close_ms,armed=False,evaluated_close_ms=target_close_ms)
            return self.ledger.consume_and_commit(source_message_key=key,message_source='c_published_4h',
                message_payload=payload,record=record,session=session,transitions=trs,recovery_effect=recovery_step,
                cursor_updates={state_key:_canonical(state),'tracking.context|'+eid:_canonical(context)},
                expected_cursors={state_key:old_state})
        if cycle['terminal_at_close_ms'] is not None and (state['terminal'] is None or source_revision):
            state.update(terminal=cycle['terminal_at_close_ms'],armed=False)
        unresolved=cycle.get('first_unresolved_offset')
        state['first_unresolved_close_ms']=t0+(unresolved+1)*H4 if unresolved else None
        decision=cycle['terminal_at_close_ms'] or min(target_close_ms,t0+11*H4)
        state['evaluated_close_ms']=(state['first_unresolved_close_ms']-H4 if unresolved else decision)
        state['source_quality']=quality(raw_bars,oi_samples,lsr_samples,caps,target=decision,
            clock=milliseconds(clock_iso),first_required=t0+H4,evaluated=state['evaluated_close_ms'])
        # The signal source contains only roots/samples effective by its decision;
        # later post-confirmation candles cannot rewrite the original evidence.
        decided_bars=[b for b in raw_bars if b['open_ms']+H4<=decision]
        source_parts=[('candles_4h',decided_bars),('oi',[s for s in known(oi_samples) if s['effective_at_ms']<=decision]),
            ('lsr',[s for s in known(lsr_samples) if s['effective_at_ms']<=decision]),
            ('cap',[s for s in known(caps) if s['effective_at_ms']<=decision])]
        refs=[]
        for role,part in source_parts:
            av=max([s['available_at_ms'] for s in part if 'available_at_ms' in s] or [decision])
            refs.append(source_ref(role,part,ref=f'c-cycle:{eid}:{role}:{digest(part)}',version=digest(part),
                boundary=decision,available_at=iso(av)))
        if state['terminal'] is not None and not source_revision and prior_context.get('cycle_sources') and previous_t0==t0:
            refs=prior_context['cycle_sources']
        prev=latest(self.ledger,eid)
        record=event_record(self.ledger,eid=eid,strategy='C',market=market,symbol=symbol,
            trigger=t0,decision=decision,status=cycle['status'],qualification='pass' if not cycle['partially_unverified'] else 'unknown',
            sources=refs,clock_iso=clock_iso,discovery='late_backfill' if source_revision else 'import' if historical_import else 'live',
            confirm_open=t0+cycle['terminal_offset']*H4 if cycle['status']=='confirmed' else None,
            missing=cycle['missing_reasons'],reason='source_revision' if source_revision else 'state_progression')
        # Signal revisions are semantic. Advancing observation does not create an
        # endless stream of identical "confirmed" revisions at later boundaries.
        if prev and all(prev.get(k)==record.get(k) for k in ('technical_status','qualification_status','decision_bar_close_ms','evidence_hash','missing_data_flags')):
            record=prev;write_record=None
        else:write_record=record
        session=self.ledger.get_session(eid);trs=[];gaps=[];notices=[];retired=[]
        old_profile_session=None
        desired_profile={"confirmed":"C_confirmed_observation","expired":"C_expired_technical_observation"}.get(cycle["status"])
        epoch=prior_context.get('source_epoch')
        if source_revision and write_record and session and not session['human_stopped'] and session['observation_status']!='ended':
            epoch=record['evidence_hash']
            if desired_profile:
                old_profile_session=copy.deepcopy(session);session=None
            else:
                retirement=retire_for_revision(session,record,target_close_ms,clock_iso)
                session,trs=retirement['session'],retirement['transitions']
        if desired_profile and session and session.get('end_reason')==SUPERSEDED and epoch:
            session=None
        obs=None
        if cycle['status'] in ('confirmed','expired'):
            terminal_open=t0+cycle['terminal_offset']*H4
            if session is None:
                lows={b['open_ms']:b['l'] for b in bars}
                needed=list(range(t0,terminal_open+1,H4)) if cycle['status']=='confirmed' else [t0]
                if all(o in lows for o in needed):
                    kind='cycle_low' if cycle['status']=='confirmed' else 't0_low'
                    profile='C_confirmed_observation' if cycle['status']=='confirmed' else 'C_expired_technical_observation'
                    reference=dict(kind=kind,value=min(lows[o] for o in needed),from_open_ms=t0,to_open_ms=terminal_open if kind=='cycle_low' else t0)
                    session=(corrected_session(record,profile,start=cycle['terminal_at_close_ms'],reference=reference,source_epoch=epoch) if epoch else new_session(record,profile,start=cycle['terminal_at_close_ms'],reference=reference))
                    trs=[transition(session,None,'observing','session_started',cycle['terminal_at_close_ms'],clock_iso,record['evidence_hash'])]
            if session and old_profile_session:
                retired=[retire_for_revision(old_profile_session,record,target_close_ms,clock_iso)]
            if session:
                session,new_trs,gaps,notices=advance_observation(self.ledger,record,session,bars,clock=clock_iso,target=target_close_ms,human_stop=human_stop)
                trs+=new_trs
                obs=dict(status=session['observation_status'],profile_id=session['tracking_profile_id'],
                         end_reason=session['end_reason'],last_evaluated_bar_close_ms=session['last_evaluated_bar_close_ms'])
        elif cycle['status']=='waiting':
            obs=dict(status='paused_awaiting_data' if unresolved else 'observing',profile_id='C_waiting_observation',
                     end_reason=None,last_evaluated_bar_close_ms=state['evaluated_close_ms'])
        elif cycle['status']=='invalidated':
            obs=dict(status='ended',profile_id='C_waiting_observation',end_reason='cycle_invalidated',
                     last_evaluated_bar_close_ms=cycle['terminal_at_close_ms'])
        context=dict(input_key=key,cycle=cycle,cycle_sources=refs,observation=obs,algorithm=rules.ALGORITHM,source_epoch=epoch,source_quality=state["source_quality"],
            prepared_t0=next((b for b in bars if b['open_ms']==t0),frozen_t0),
            t0_new_lows14=rules.strict_new_lows(bars,t0) if any(b['open_ms']==t0 for b in bars) else prior_context.get('t0_new_lows14'),
            structure_reference=(session or {}).get('reference'),
            first_unresolved_close_ms=state['first_unresolved_close_ms'],historical_import=historical_import,recovery_cycle_t0=recovery_cycle_t0,
            expired_rebound_progress=cycle.get('t11_confirm_like_ignored',False),
            source_revision=bool(source_revision),previous_cycle_t0=previous_t0 if previous_t0!=t0 else None)
        if cycle['status']=='expired' and session:
            context['expired_followup']=rules.expired_followup(bars,t0_open_ms=t0,
                terminal_close_ms=cycle['terminal_at_close_ms'],evaluated_close_ms=session['last_evaluated_bar_close_ms'],
                previous=None if source_revision else prior_context.get('expired_followup'))
        # Gaps for waiting-cycle evidence are durable even before the post-cycle
        # session exists. They use its deterministic waiting-profile identity.
        gap_sid=(session or {}).get('session_id') or f'{eid}|C_waiting_observation|{PROFILE}'
        for reason in cycle['missing_reasons']:
            boundary=state['first_unresolved_close_ms'] or decision
            gid=digest([eid,reason,boundary])
            gaps.append(dict(gap_id=gid,session_id=gap_sid,scope=reason.removeprefix('missing_'),status='open',
                detail=dict(event_id=eid,market=market,symbol=symbol,boundary_ms=boundary,reason=reason,
                            attempt_count=0,next_retry_at=None,continuation=boundary)))
        for gap in self.ledger.list_gaps(gap_sid,status='open'):
            if gap['detail'].get('reason') not in cycle['missing_reasons']:
                gaps.append(dict(gap,status='resolved'))
        notices+=observation_notices(record,trs)
        if historical_import or source_revision:notices=[]
        if source_revision and prev and write_record and prev['technical_status']!=record['technical_status']:
            notices.append(dict(kind='c_delayed_correction',key=eid+'|delayed|'+str(record['revision']),payload=dict(event_id=eid,status=cycle['status'],delayed=True,prior_status=prev['technical_status'])))
        return self.ledger.consume_and_commit(source_message_key=key,message_source='c_published_4h',
            message_payload=payload,record=write_record,session=session,transitions=trs,gaps=gaps,recovery_effect=recovery_step,
            cursor_updates={state_key:_canonical(state),f'tracking.context|{eid}':_canonical(context)},
            expected_cursors={state_key:old_state},extra_outbox=notices,retired_sessions=retired,
            outbox_kind='c_state_change' if write_record and not historical_import and not source_revision else None,
            outbox_payload=dict(event_id=eid,status=cycle['status'],delayed=False))
