"""Shared deterministic B/C persistence. No network, scheduler, AI or orders.

Source inbox, event revision, observation, gaps, controller cursors and local
notifications commit together. A source database is ACKed only AFTER this commit.
"""
from __future__ import annotations

import copy
import hashlib
import json
from datetime import datetime, timezone

from .a_pipeline import make_transition_id
from .chain_validate import judge
from .ledger import _canonical

H = 3_600_000
H4 = 4 * H
SCHEMA = 'tracking-event-schema-v1.1.0'
PROFILE = 'tracking-observation-v1.1.0'
VERSION = 'codex-abc-tracking-v1'


def digest(value):
    return hashlib.sha256(_canonical(value).encode()).hexdigest()


def iso(ms):
    return datetime.fromtimestamp(ms / 1000, timezone.utc).isoformat(timespec='milliseconds')


def milliseconds(value):
    d = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if d.utcoffset() is None:
        raise ValueError('UTC_timestamp_required')
    return int(d.timestamp() * 1000)


def latest(ledger, eid):
    rows = ledger._read('SELECT payload_json FROM event_revisions WHERE event_id=? ORDER BY revision DESC LIMIT 1', (eid,))
    return json.loads(rows[0][0]) if rows else None


def consumed(ledger, key, payload):
    row = ledger.get_inbox(key)
    if not row:
        return None
    if row['payload_sha256'] != digest(payload):
        raise ValueError('SOURCE_message_key_content_conflict')
    if row['status'] == 'acked':
        return dict(row['result'], idempotent=True)
    return None


def source_ref(role, payload, *, ref, version, boundary, available_at):
    return dict(source=role, ref=ref, version=version, effective_at_ms=boundary,
                available_at=available_at, payload_sha256=digest(payload))


def event_record(ledger, *, eid, strategy, market, symbol, trigger, decision,
                 status, qualification, sources, clock_iso, discovery='historical_replay',
                 confirm_open=None, missing=(), reason='state_progression', event_kind=None, candidate_cycle_id=None):
    prev = latest(ledger, eid)
    tf = H if strategy == 'B' else H4
    rev = prev['revision'] + 1 if prev else 1
    av = max(s['available_at'] for s in sources)
    if milliseconds(clock_iso) < max(milliseconds(av), decision):
        raise ValueError('CLOCK_before_input_available_or_decision')
    cf = None
    if status == 'confirmed':
        seen = next((r['confirm']['first_confirmed_seen_at'] for r in ledger.get_revisions(eid)
                     if r.get('technical_status') == 'confirmed'), clock_iso)
        cf = dict(confirm_bar_open_ms=confirm_open, confirm_bar_close_ms=confirm_open+tf,
                  confirm_offset=(confirm_open-trigger)//tf, first_confirmed_seen_at=seen,
                  legacy_confirm_time=None, legacy_confirm_time_semantics=None)
    return dict(schema_version=SCHEMA, event_id=eid, strategy=strategy,
        event_kind=event_kind or ('b_tech_h' if strategy == 'B' else 'c_cycle'), market=market, symbol=symbol,
        timeframe='1h' if strategy == 'B' else '4h',
        rule_version={'A':'ema-squeeze-v4','B':'b-accum-1h-v1','C':'c-1015-v1.1'}[strategy],
        trigger_bar_open_ms=trigger, trigger_bar_close_ms=trigger+tf,
        source_close_time_ms=trigger+tf-1, occurred_at_ms=trigger+tf,
        decision_bar_close_ms=decision, first_seen_at=prev['first_seen_at'] if prev else clock_iso,
        available_at=av, revision_recorded_at=clock_iso, discovery=discovery,
        technical_status=status, qualification_status=qualification, confirm=cf,
        candidate_cycle_id=candidate_cycle_id, revision=rev, revision_id=f'{eid}:r{rev}',
        prior_revision_id=prev['revision_id'] if prev else None,
        revision_reason=reason if prev else None, source_versions=sources,
        evidence_hash=judge.evidence_hash(sources),
        legacy_refs=[dict(system='b_accumulation_store.stable_event_id',id=eid)] if strategy=='B' else [],
        missing_data_flags=sorted(set(missing)))


def new_session(record, profile, *, start, reference=None):
    return dict(schema_version=SCHEMA,
        session_id=f'{record["event_id"]}|{profile}|{PROFILE}', event_id=record['event_id'],
        strategy=record['strategy'], tracking_profile_id=profile, tracking_profile_version=PROFILE,
        original_cycle_state=record['technical_status'], observation_status='observing',
        started_at_bar_close_ms=start, last_evaluated_bar_close_ms=start,
        ended_at_bar_close_ms=None, end_reason=None, human_stopped=False,
        reference=reference or dict(kind='none',value=None,from_open_ms=None,to_open_ms=None), gap_refs=[])


def transition(session, before, after, reason, close, clock, evidence_hash):
    tf = H if session['strategy'] == 'B' and reason == 'session_started' else H4
    return dict(schema_version=session['schema_version'],
        transition_id=make_transition_id(session['session_id'],close,after),
        session_id=session['session_id'],timeframe='1h' if tf == H else '4h',from_state=before,to_state=after,
        at_bar_open_ms=close-tf,at_bar_close_ms=close,reason=reason,recorded_at=clock,
        evidence_hash=evidence_hash,source_version=VERSION,revision=1,corrects_transition_id=None)


def advance_observation(ledger, record, session, bars, *, clock, target, human_stop=None):
    """One shared continuous pass, never jump an absent root or revive an end."""
    if type(target) is not int or target%H4 or target>milliseconds(clock) or len(bars)>1499:
        raise ValueError('observation_input_bound_or_target')
    unique={}
    for b in bars:
        o=b.get('open_ms')
        if type(o) is not int or o%H4 or o+H4>target:raise ValueError('observation_unclosed_or_misaligned')
        if o in unique and unique[o]!=b:raise ValueError('observation_conflicting_duplicate')
        unique[o]=b
    if human_stop is not None and (type(human_stop) is not int or human_stop>target):raise ValueError('human_stop_target')
    if session['observation_status'] == 'ended':
        return copy.deepcopy(session), [], [], []
    sess = copy.deepcopy(session)
    transitions, gaps, notices = [], [], []
    eh = record['evidence_hash']
    by = {b['open_ms']:b for b in bars}
    expected = sess['last_evaluated_bar_close_ms']
    # Starts may be a B hourly close; observation only uses subsequent 4H closes.
    expected = (expected // H4) * H4
    if expected + H4 <= sess['last_evaluated_bar_close_ms']:
        expected += H4
    if human_stop is not None and human_stop < sess['started_at_bar_close_ms']:
        raise ValueError('HUMAN_stop_before_session')
    while expected + H4 <= target:
        close = expected + H4
        if human_stop is not None and close >= human_stop:
            break
        b = by.get(expected)
        required = ('ema7','ema25','ema99','c') if record['strategy']=='A' else ('ema7','ema25') if record['strategy']=='B' else ('c',)
        missing = b is None or any(judge.fin(b.get(k)) is None or b[k]<=0 for k in required)
        if missing:
            gid = digest([sess['session_id'],'candles_4h',expected])
            detail = dict(event_id=record['event_id'],market=record['market'],symbol=record['symbol'],
                source='candles_4h',boundary_ms=close,reason='missing_candles',attempt_count=0,
                next_retry_at=None,continuation=expected)
            gaps.append(dict(gap_id=gid,session_id=sess['session_id'],scope='candles_4h',status='open',detail=detail))
            if gid not in sess['gap_refs']:sess['gap_refs'].append(gid)
            if sess['observation_status'] != 'paused_awaiting_data':
                transitions.append(transition(sess,sess['observation_status'],'paused_awaiting_data',
                    'missing_candles',close,clock,eh))
                sess['observation_status']='paused_awaiting_data'
            break
        if sess['observation_status']=='paused_awaiting_data':
            transitions.append(transition(sess,'paused_awaiting_data','observing','backfilled',close,clock,eh))
            sess['observation_status']='observing'
            for g in ledger.list_gaps(sess['session_id'],status='open'):
                d=g.get('detail',g)
                if d.get('boundary_ms',close)<=close:
                    gaps.append(dict(gap_id=g['gap_id'],session_id=sess['session_id'],scope='candles_4h',status='resolved',detail=d))
        if record['strategy']=='A':
            holds=(b['ema7']>b['ema25']>b['ema99']) if sess['tracking_profile_id']=='A_strong_observation' else (b['ema7']>b['ema25'] and b['c']>b['ema99'])
            ended=not holds
        elif record['strategy']=='B':
            ended=b['ema7']<=b['ema25']
        else:
            ended=sess['reference']['value'] is not None and b['c']<sess['reference']['value']
        s0=sess['reference']['value'] if record['strategy']=='B' else None
        if s0 is not None and judge.fin(b.get('c')) is not None and b['c']<s0:
            notices.append(dict(kind='b_s0_risk_only',key=f'{sess["session_id"]}|s0|{close}',
                payload=dict(event_id=record['event_id'],close_ms=close,auto_sell=False)))
        sess['last_evaluated_bar_close_ms']=close
        if ended:
            transitions.append(transition(sess,'observing','ended','structure_invalid',close,clock,eh))
            sess.update(observation_status='ended',end_reason='structure_invalid',ended_at_bar_close_ms=close)
            break
        expected += H4
    if human_stop is not None and sess['observation_status']!='ended':
        transitions.append(transition(sess,sess['observation_status'],'ended','human_stop',human_stop,clock,eh))
        sess.update(observation_status='ended',end_reason='human_stop',human_stopped=True,
                    ended_at_bar_close_ms=human_stop,
                    last_evaluated_bar_close_ms=max(human_stop,sess['last_evaluated_bar_close_ms']))
    return sess, transitions, gaps, notices


def corrected_session(record,profile,*,start,reference,source_epoch):
    from .control_contract import CONTROL_SCHEMA
    sess=new_session(record,profile,start=start,reference=reference)
    sess['schema_version']=CONTROL_SCHEMA
    sess['session_id']+='|source-revision|'+source_epoch
    return sess

def retire_for_revision(old,record,target,clock):
    from .control_contract import CONTROL_SCHEMA,SUPERSEDED
    sess=copy.deepcopy(old);close=max(target,sess['last_evaluated_bar_close_ms'])
    sess['schema_version']=CONTROL_SCHEMA
    tr=transition(sess,sess['observation_status'],'ended',SUPERSEDED,close,clock,record['evidence_hash'])
    sess.update(observation_status='ended',end_reason=SUPERSEDED,ended_at_bar_close_ms=close,last_evaluated_bar_close_ms=close)
    return dict(session=sess,transitions=[tr])


def observation_notices(record,transitions):
    """Only actual changes; immutable key survives retries and page reloads."""
    if record.get('discovery')!='live':return []
    return [dict(kind='observation_state',key=tr['transition_id'],payload=dict(event_id=record['event_id'],session_id=tr['session_id'],state=tr['to_state'],reason=tr['reason'],close_ms=tr['at_bar_close_ms'],delayed=False)) for tr in transitions if tr.get('reason') not in ('source_revision_superseded',)]
