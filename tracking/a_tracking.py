from .tracking_engine import observation_notices
"""Published A snapshots -> independent technical observations.

Uses frozen A cross/T/T+1 rules. Candidate evidence is read from the immutable
published row at the trigger, never today's candidate file. Missing historical
qualification remains unknown while technical observation continues.
"""
import json
from .control_contract import observation_bar_source
from .chain_validate import judge
from .c_rebound_rules import prepare_bars,ratio7
from .tracking_engine import (H4,digest,consumed,source_ref,event_record,new_session,
    transition,advance_observation,latest,milliseconds,_canonical,corrected_session,retire_for_revision)


class ATracker:
    def __init__(self,ledger):self.ledger=ledger

    def drive_snapshot(self, *, row, raw_bars, clock_iso, source_version, available_at,
                       historical_import=True, after_event_id='', budget=100,recovery_step=None):
        if type(budget) is not int or not 1<=budget<=100:raise ValueError('A_budget_1_100')
        market={'USDⓈ-M':'USDT-M','USDT-M':'USDT-M','COIN-M':'COIN-M'}.get(row.get('market'))
        if not market:raise ValueError('A_invalid_market')
        symbol=row['symbol'];target=max(b['open_ms']+H4 for b in raw_bars)
        if row.get('candleAsOf')!=target-1:raise ValueError('A_row_candle_boundary_mismatch')
        if milliseconds(available_at)>milliseconds(clock_iso):raise ValueError('A_unavailable_snapshot')
        bars=prepare_bars(raw_bars,target_close_ms=target)
        by={b['open_ms']:b for b in bars};current=bars[-1];prior=bars[-2] if len(bars)>1 else {}
        ci=dict(ema7=current['ema7'],ema25=current['ema25'],ema99=current['ema99'],
            prev_ema7=prior.get('ema7'),prev_ema25=prior.get('ema25'),prev_ema99=prior.get('ema99'),close=current['c'])
        cross=judge.a_cross_level(ci,judge.FROZEN)
        event_filter='';params=[market,symbol,after_event_id]
        if recovery_step is not None:
            deps=self.ledger._read('SELECT event_id FROM recovery_dependencies WHERE dependency_id=?',(recovery_step['dependency_id'],))
            if not deps or not deps[0][0]:raise ValueError('A_recovery_requires_event')
            event_filter=' AND e.event_id=?';params.append(deps[0][0])
        rows=self.ledger._read("SELECT DISTINCT e.event_id FROM event_revisions e JOIN sessions s ON s.event_id=e.event_id WHERE e.strategy='A' AND json_extract(e.payload_json,'$.market')=? AND e.symbol=? AND e.event_id>? AND s.observation_status<>'ended'"+event_filter+" ORDER BY e.event_id LIMIT ?",tuple(params+[budget+1]))
        ids=[r[0] for r in rows[:budget]]
        if cross.get('creates_tracking_session'):
            kind='a_full_cross' if cross['technical_level']=='strong' else 'a_medium_cross'
            new=f'A|{kind}|{market}|{symbol}|4h|ema-squeeze-v4|{current["open_ms"]}'
            if new not in ids and not latest(self.ledger,new):ids.append(new)
        output=[]
        if recovery_step is not None:
            deps=self.ledger._read('SELECT event_id FROM recovery_dependencies WHERE dependency_id=?',(recovery_step['dependency_id'],))
            if not deps or not deps[0][0]:raise ValueError('A_recovery_requires_event')
            ids=[eid for eid in ids if eid==deps[0][0]]
            if not ids:raise ValueError('A_recovery_event_not_ready')
        for eid in ids:
            prev=latest(self.ledger,eid);t0=prev['trigger_bar_open_ms'] if prev else current['open_ms']
            kind=prev['event_kind'] if prev else kind;level='strong' if kind=='a_full_cross' else 'medium'
            context_key=f'tracking.context|{eid}';old_context=self.ledger.get_cursor(context_key)
            context=json.loads(old_context) if old_context else {}
            stage=row.get('emaSqueezeStage') or {}
            if not prev:
                # Candidate lifetime is independent of the newly-created technical session.
                candidate=stage.get('candidateCycleId') if stage.get('candidateStatus')=='active' else None
                entered=milliseconds(stage['candidateEnteredAt']) if candidate and stage.get('candidateEnteredAt') else None
                context.update(candidate_cycle_id=candidate,candidate_entered_at_ms=entered,
                    candidate_snapshot=stage,cross_bar=current,volume_active=False,
                    cross_oi_source=dict(oiUsd=row.get('oiUsd'),oiAsOf=row.get('oiAsOf'),available_at=available_at))
            candidate=context.get('candidate_cycle_id');entered=context.get('candidate_entered_at_ms')
            if target==t0+2*H4:
                context['t1_oi_valid']=judge.fin(row.get('oiUsd')) is not None and row['oiUsd']>0
                context['t1_oi_source']=dict(oiUsd=row.get('oiUsd'),oiAsOf=row.get('oiAsOf'),available_at=available_at)
            t1_oi_valid=context.get('t1_oi_valid',False)
            cb=context['cross_bar']
            def confirm_bar(b,oi_valid):
                return dict(bar_close_ms=b['open_ms']+H4-1,ema7=b['ema7'],ema25=b['ema25'],ema99=b['ema99'],
                    close=b['c'],vol_ratio7=ratio7(bars,b['open_ms']),quality_ok=b['ema7'] is not None,oi_valid=oi_valid)
            original=prev and prev['technical_status'] in ('confirmed','void','technical_only')
            if original:
                record=prev;write_record=None
            else:
                trigger_bar=by.get(t0,cb)
                inp=dict(level=level,candidate_entered_at_ms=entered,
                    historical=bool(historical_import and entered is None),
                    cross=confirm_bar(trigger_bar,context.get('cross_oi_valid',bool(row.get('oiUsd')) if target==t0+H4 else False)),
                    next_bars=[confirm_bar(by[t0+H4],t1_oi_valid)] if t0+H4 in by else [])
                if not prev:context['cross_oi_valid']=inp['cross']['oi_valid']
                confirmation=judge.a_confirm(inp,judge.FROZEN)
                status=confirmation['status']
                if status=='confirmed':
                    matches=[e for e in (stage.get('confirmedEvents') or [])+[stage.get('crossConfirm') or {}]
                        if e.get('status')=='confirmed' and e.get('candidate_cycle_id')==candidate
                        and e.get('cross_bar_time',e.get('cross_time'))==t0+H4-1
                        and e.get('confirm_bar_time')==confirmation['confirm_bar_close_ms']]
                    if not matches:
                        status='blocked';confirmation=dict(confirmation,status=status,reason='original_published_confirmation_not_present')
                decision=confirmation.get('confirm_bar_close_ms')
                # Frozen A judge uses inclusive Binance closes. Public contract is exclusive.
                decision=decision+1 if decision is not None else min(target,t0+2*H4)
                refs=[source_ref('candles_4h',[b for b in raw_bars if b['open_ms']+H4<=decision],
                    ref=f'a-published:{source_version}:{eid}',version=source_version,boundary=min(decision,target),available_at=available_at)]
                if candidate:
                    refs.append(source_ref('candidate_store',context['candidate_snapshot'],ref=candidate,version=digest(context['candidate_snapshot']),boundary=t0+H4,available_at=available_at))
                    oi_context=context.get('t1_oi_source') if confirmation.get('confirm_offset')==1 else context.get('cross_oi_source')
                    oi_context=oi_context or {}
                    refs.append(source_ref('oi',oi_context,ref='a-oi:'+digest(oi_context),version=digest(oi_context),boundary=min(decision,target),available_at=oi_context.get('available_at',available_at)))
                # Keep the original technical confirmation. Full evidence grade
                # also requires a traceable contemporaneous published OI time;
                # a scalar from an undated snapshot is not historical proof.
                oi_time=oi_context.get('oiAsOf') if candidate else None
                if isinstance(oi_time,str):oi_time=milliseconds(oi_time)
                if type(oi_time) is not int:oi_time=None
                oi_proven=oi_time is not None and 0<=decision-oi_time<=300000
                q='pass' if status=='confirmed' and candidate and oi_proven else 'unknown'
                record=event_record(self.ledger,eid=eid,strategy='A',market=market,symbol=symbol,
                    trigger=t0,decision=decision,status=status,qualification=q,sources=refs,clock_iso=clock_iso,
                    event_kind=kind,candidate_cycle_id=candidate,discovery='import' if historical_import else 'live',
                    confirm_open=t0+confirmation.get('confirm_offset',0)*H4 if status=='confirmed' else None)
                write_record=record;context['original_confirmation_result']=confirmation
            session=self.ledger.get_session(eid);trs=[]
            if session is None:
                session=new_session(record,'A_strong_observation' if level=='strong' else 'A_medium_observation',start=t0+H4)
                trs=[transition(session,None,'observing','session_started',t0+H4,clock_iso,record['evidence_hash'])]
            prior_key=context.get('observation_input_key');prior=self.ledger.get_inbox(prior_key) if prior_key else None
            retired=[];revised=False
            if prior and session['observation_status']!='ended':
                previous={b['open_ms']:b for b in prior['payload'].get('bars',[])}
                revised=any(b['open_ms'] in previous and observation_bar_source(b)!=observation_bar_source(previous[b['open_ms']]) for b in bars)
                if revised:
                    epoch=digest(dict(prior=prior_key,bars=raw_bars))
                    retired=[retire_for_revision(session,dict(record,evidence_hash=epoch),session['last_evaluated_bar_close_ms'],clock_iso)]
                    session=corrected_session(record,session['tracking_profile_id'],start=session['started_at_bar_close_ms'],reference=session['reference'],source_epoch=epoch)
                    trs=[transition(session,None,'observing','session_started',session['started_at_bar_close_ms'],clock_iso,epoch)]
            payload=dict(event_id=eid,session_id=session['session_id'],row=row,raw_bars=raw_bars,bars=bars,source_version=source_version,available_at=available_at,prior_input_key=prior_key if revised else None)
            if recovery_step is not None:payload['recovery_step']=recovery_step
            key=f'a-published|{eid}|{digest(payload)}'
            done=consumed(self.ledger,key,payload)
            if done:output.append(done);continue
            session,new_trs,gaps,notices=advance_observation(self.ledger,dict(record,evidence_hash=digest(payload)),session,bars,clock=clock_iso,target=target)
            trs+=new_trs
            # Volume segments are progress facts, never a revision of original confirmation.
            last_progress=context.get('volume_checked_close_ms',t0+H4)
            for bar in bars:
                close=bar['open_ms']+H4
                if close<=last_progress or close>session['last_evaluated_bar_close_ms']:continue
                ratio=ratio7(bars,bar['open_ms'])
                if ratio is None:continue
                active=ratio>=2
                if active and not context.get('volume_active'):
                    context['latest_volume_segment']=dict(close_ms=close,ratio=ratio)
                    notices.append(dict(kind='a_followup_volume',key=f'{eid}|volume|{close}',payload=dict(event_id=eid,close_ms=close,ratio=ratio,changes_original_confirmation=False)))
                context.update(volume_active=active,volume_checked_close_ms=close)
            context['candidate_current_status']=stage.get('candidateStatus')
            context['observation_input_key']=key
            notices+=observation_notices(record,trs) if not revised else []
            if historical_import:notices=[]
            result=self.ledger.consume_and_commit(source_message_key=key,message_source='observation_source_revision' if revised else 'a_published_snapshot',
                message_payload=payload,record=write_record,session=session,transitions=trs,gaps=gaps,retired_sessions=retired,
                cursor_updates={context_key:_canonical(context)},expected_cursors={context_key:old_context},extra_outbox=notices,recovery_effect=recovery_step)
            output.append(result)
        return dict(ok=all(r.get('ok') for r in output),items=output,
            continuation=rows[budget-1][0] if len(rows)>budget else None,shared_input_reads=1)
