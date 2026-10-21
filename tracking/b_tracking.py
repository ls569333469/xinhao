"""Consume existing B revisions without recalculating/replacing h or h+1.

The immutable source payload is kept in the tracking inbox. Source-side ACK is
deliberately outside this database transaction; a retry uses the same inbox key.
"""
from __future__ import annotations
from .tracking_engine import observation_notices
import json

from .chain_validate import judge
from .mappings import map_market
from .tracking_engine import (H,H4,digest,consumed,event_record,source_ref,new_session,
    transition,advance_observation,latest,milliseconds,_canonical,corrected_session,retire_for_revision)


class BTracker:
    def __init__(self, ledger):
        self.ledger=ledger

    def consume_revision(self, source, *, clock_iso, historical_import=True,recovery_step=None):
        source={k:v for k,v in source.items() if k!='revisions'}
        from .recovery_effects import bind_payload
        bind_payload(source,recovery_step)
        market=map_market(source['market']); symbol=source['symbol']; t0=source['trigger_open_time']
        eid=source['event_id']; rev=source['event_revision']
        if type(t0) is not int or t0%H or type(rev) is not int or rev<1:
            raise ValueError('B_invalid_source_identity')
        if eid!=judge.b_legacy_id(source['market'],symbol,t0):
            raise ValueError('B_legacy_id_mismatch')
        legacy_unverified=source.get('rule_version') is None and source.get('migration_status')=='legacy_unverified'
        if source.get('rule_version')!='b-accum-1h-v1' and not legacy_unverified:
            raise ValueError('B_rule_version_mismatch')
        key=f'b-revision|{eid}|{rev}'+('|recovery|'+digest(recovery_step) if recovery_step else '')
        done=consumed(self.ledger,key,source)
        if done:return done
        cursor_key=f'b-source-revision|{eid}'
        prev_cursor=self.ledger.get_cursor(cursor_key)
        if prev_cursor and int(prev_cursor)>=rev:
            raise ValueError('B_source_revision_regression_or_conflict')
        # Import the immutable prefix before current state; missing old revisions
        # remain an explicit source gap, never a fabricated continuous history.
        if prev_cursor is None and rev!=1:
            raise ValueError('B_initial_source_revision_prefix_missing')
        if prev_cursor and rev!=int(prev_cursor)+1:
            raise ValueError('B_source_revision_gap')
        te,tc=source.get('technical_event'),source.get('technical_confirmation')
        if legacy_unverified:te,tc='unknown','unknown'
        if te not in ('pass','fail','unknown') or tc not in ('pass','fail','unknown','pending'):
            raise ValueError('B_invalid_source_state')
        if te=='pass' and tc=='pass':
            h1=source.get('h1') or {}
            if h1.get('open_time')!=t0+H:
                raise ValueError('B_confirmation_not_real_h1')
        status='confirmed' if te=='pass' and tc=='pass' else 'confirm_failed' if tc=='fail' else 'pending' if tc=='pending' else 'unknown'
        decision=t0+(H if tc=='pending' else 2*H)
        available=source.get('last_evaluated_at') or source.get('first_seen_at')
        if not available or milliseconds(available)>milliseconds(clock_iso):
            raise ValueError('B_source_availability_unknown_or_future')
        ref=source_ref('candles_1h',source,ref=f'b-source:{eid}:r{rev}',version=str(rev),
                       boundary=decision,available_at=available)
        gate='unknown' if legacy_unverified else source.get('structure_gate')
        q='pass' if te=='pass' and tc=='pass' and gate=='pass' else 'fail' if gate=='fail' else 'unknown'
        missing=[] if gate in ('pass','fail') else ['missing_oi','missing_lsr','missing_cap']
        record=event_record(self.ledger,eid=eid,strategy='B',market=market,symbol=symbol,
            trigger=t0,decision=decision,status=status,qualification=q,sources=[ref],clock_iso=clock_iso,
            discovery='import' if historical_import or source.get('historical') else 'live',
            confirm_open=t0+H if status=='confirmed' else None,missing=missing,
            reason='source_revision')
        session=self.ledger.get_session(eid);trs=[];retired=[]
        old_context_raw=self.ledger.get_cursor('tracking.context|'+eid)
        old_context=json.loads(old_context_raw) if old_context_raw else {}
        observation_hash=digest({k:source.get(k) for k in ('h','h1','bg4h','bg4h_h1','S0','technical_event')})
        if session and session['observation_status']!='ended' and old_context.get('observation_source_hash') and old_context['observation_source_hash']!=observation_hash:
            retirement=retire_for_revision(session,record,session['last_evaluated_bar_close_ms'],clock_iso)
            if te=='pass':
                retired=[retirement]
                s0=source.get('S0')
                session=corrected_session(record,'B_trend_observation',start=t0+H,
                    reference=dict(kind='s0_risk_line' if judge.fin(s0) is not None else 'none',value=s0,from_open_ms=None,to_open_ms=None),source_epoch=record['evidence_hash'])
                trs=[transition(session,None,'observing','session_started',t0+H,clock_iso,record['evidence_hash'])]
            else:session,trs=retirement['session'],retirement['transitions']
        if session is None and te=='pass':
            s0=source.get('S0')
            session=new_session(record,'B_trend_observation',start=t0+H,
                reference=dict(kind='s0_risk_line' if judge.fin(s0) is not None else 'none',
                    value=s0,from_open_ms=None,to_open_ms=None))
            trs=[transition(session,None,'observing','session_started',t0+H,clock_iso,record['evidence_hash'])]
        if session and session['observation_status']!='ended' and session['reference']['value'] is None and judge.fin(source.get('S0')) is not None:
            session['reference']=dict(kind='s0_risk_line',value=source['S0'],from_open_ms=None,to_open_ms=None)
        # Qualification revisions do not reset the original fixed S0 or revive a session.
        context=dict(old_context,observation_source_hash=observation_hash,source_revision=rev,source_input_fingerprint=source.get('input_fingerprint'),
            legacy_market=source['market'],source_first_seen_at=source.get('first_seen_at'),
            legacy_unverified=legacy_unverified,source_rule_version=source.get('rule_version'),
            structure_h=source.get('structure_h'),structure_confirmation=source.get('structure_confirmation'),
            historical_import=historical_import or bool(source.get('historical')))
        return self.ledger.consume_and_commit(source_message_key=key,message_source='b_event_revision',recovery_effect=recovery_step,
            message_payload=source,record=record,session=session,transitions=trs,retired_sessions=retired,
            cursor_updates={cursor_key:str(rev),f'tracking.context|{eid}':_canonical(context)},
            expected_cursors={cursor_key:prev_cursor},
            outbox_kind=None if historical_import or source.get('historical') else 'b_original_state_change',
            outbox_payload=dict(event_id=eid,technical_status=status,qualification_status=q))

    def observe(self,eid,bars,*,target_close_ms,clock_iso,source_version,human_stop=None,recovery_step=None):
        if len(bars)>1499:raise ValueError('B_observation_page_exceeds_1499')
        record=latest(self.ledger,eid)
        if not record or record['strategy']!='B':raise ValueError('B_event_not_found')
        if target_close_ms%H4 or milliseconds(clock_iso)<target_close_ms:
            raise ValueError('B_observation_target_invalid')
        session=self.ledger.get_session(eid)
        if not session:return dict(ok=False,errors=[dict(code='B_session_not_formed')])
        payload=dict(event_id=eid,bars=bars,target_close_ms=target_close_ms,
                     source_version=source_version,human_stop=human_stop)
        key=f'b-observation|{eid}|{digest(payload)}'
        if recovery_step is not None:
            payload['recovery_step']=recovery_step
            key+='|recovery|'+digest(recovery_step)
        done=consumed(self.ledger,key,payload)
        if done:return done
        # Bind the observation input to an immutable inbox hash, not the old signal hash.
        obs_record=dict(record,evidence_hash=digest(payload))
        sess,trs,gaps,notices=advance_observation(self.ledger,obs_record,session,bars,
            clock=clock_iso,target=target_close_ms,human_stop=human_stop)
        notices+=observation_notices(record,trs)
        if record['discovery']!='live':notices=[]
        cursor=f'b-observer|{eid}'; old=self.ledger.get_cursor(cursor)
        return self.ledger.consume_and_commit(source_message_key=key,message_source='closed_4h',recovery_effect=recovery_step,
            message_payload=payload,session=sess,transitions=trs,gaps=gaps,
            cursor_updates={cursor:str(sess['last_evaluated_bar_close_ms'])},
            expected_cursors={cursor:old},extra_outbox=notices,
            outbox_kind='b_observation_state' if trs and record['discovery']=='live' else None,
            outbox_payload=dict(event_id=eid,observation_status=sess['observation_status']))
