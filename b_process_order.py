"""Single ordered event pipeline; commits events/cursor/outbox atomically."""
from b_accumulation_store import BAccumulationStore,stable_event_id
from b_observe_evaluate import calculate_event
from b_accumulation_rules import evaluate_h_event
H=3600000
def process_bars_in_order(root,*,market,symbol,bars_asc,observation_started_open=None,bg4h_for=None,structure_for=None,target_close_boundary=None,reference_open=None,observed=None,observation_sink=None,changed_opens=None,replay_opens=None,cache_coverage=None):
    store=BAccumulationStore(root);store.ensure_observation_start()
    bars=sorted({int(b['open_time']):b for b in bars_asc}.values(),key=lambda b:b['open_time'])
    target=target_close_boundary if target_close_boundary is not None else (bars[-1]['open_time']+H if bars else 0)
    bars=[b for b in bars if b['open_time']+H<=target]
    previous={r['trigger_open_time']:r for r in store.list_events(market=market,symbol=symbol,full=False,revisions=False)}
    prior_cursor=store.cursor(market,symbol) or {}
    frontier=prior_cursor.get('last_processed_boundary',0)-H
    unresolved=set(prior_cursor.get('unresolved_opens',[]))
    updates=[];confirmed=0;new=0;observations=[]
    def bg(t):return bg4h_for(t-H) if callable(bg4h_for) else None
    structure=structure_for or (lambda t:dict(state='unknown',reason='同期证据未取得'))
    for i,b in enumerate(bars):
        if i<168:continue
        old=previous.get(b['open_time'])
        if changed_opens is not None and prior_cursor and b['open_time']<frontier and b['open_time'] not in unresolved and b['open_time'] not in (replay_opens or ()) and not any(c-2*H<=b['open_time']<=c+168*H for c in changed_opens):continue
        preliminary=evaluate_h_event(bars,i,bg4h=None)
        if old is None and preliminary['technical_event']=='fail':
            observations.append(dict(open_time=b['open_time'],technical='fail',structure='unknown',event_ratio=preliminary.get('ratio'),quality=preliminary['reasons']))
            continue
        p=calculate_event(bars,i,target=target,background=bg,structure=structure,reference_open=reference_open,observed=(old or {}).get('observed_quote'))
        if p['technical_confirmation']=='pass' and not (p.get('observed_quote') or {}).get('price') and callable(observed):
            try:quote=observed()
            except Exception as exc:quote={'error':str(exc)[:300]}
            p=calculate_event(bars,i,target=target,background=bg,structure=structure,reference_open=reference_open,observed=quote)
        observations.append(dict(open_time=b['open_time'],technical=p['technical_event'],structure=p['structure_h']['state'],quality=p['reasons'],event_ratio=p.get('event_ratio'),event_id=stable_event_id(market=market,symbol=symbol,trigger_open_time=b['open_time']) if p['technical_event']=='pass' or old else None))
        old=previous.get(b['open_time'])
        # Non-trigger observations remain a denominator, never fake candidates.
        if p['technical_event']!='pass' and old is None:continue
        updates.append((b['open_time'],p))
        if old is None:new+=1
        if p['technical_confirmation']=='pass' and (old or {}).get('technical_confirmation')!='pass':confirmed+=1
    cursor=dict(last_processed_boundary=bars[-1]['open_time']+H if bars else 0,pending_gaps=[dict(after=a['open_time'],before=b['open_time']) for a,b in zip(bars,bars[1:]) if b['open_time']-a['open_time']!=H])
    if bars and bars[-1]['open_time']+H<target:cursor['pending_gaps'].append(dict(after=bars[-1]['open_time'],before=target,kind='missing_tail'))
    if cache_coverage:
        cursor.update(pending_gaps=cache_coverage['gaps'],archived_contiguous_through=cache_coverage['archived_contiguous_through'],fetched_through=(cache_coverage['last_open']+H if cache_coverage['last_open'] is not None else None))
    cursor['last_processed_boundary']=max(cursor['last_processed_boundary'],prior_cursor.get('last_processed_boundary',0))
    cursor['unresolved_opens']=sorted((unresolved-{r['open_time'] for r in observations})|{r['open_time'] for r in observations if r['technical']=='unknown'})
    pending_evidence={ot for ot,r in previous.items() if r.get('structure_gate') in ('unknown','pending')}
    for ot,p in updates:
        pending_evidence.discard(ot)
        if p.get('structure_gate') in ('unknown','pending'):pending_evidence.add(ot)
    cursor['evidence_unresolved_opens']=sorted(pending_evidence)
    # Preserve raw source unknowns; discharge only this qualification consumer.
    current=dict(previous)
    current.update({ot:p for ot,p in updates})
    cursor['business_required_evidence_opens']=sorted(ot for ot in pending_evidence if current[ot].get('technical_event')!='fail' and current[ot].get('technical_confirmation')!='fail')
    cursor['qualification_not_required_opens']=sorted(pending_evidence-set(cursor['business_required_evidence_opens']))
    cursor['technical_observation_unresolved_opens']=list(cursor['unresolved_opens'])
    summary=dict(market=market,symbol=symbol,target_close_boundary=target,technical_evaluable=sum(r['technical']!='unknown' for r in observations),new_events=new,new_confirmations=confirmed)
    # Persist the idempotent denominator before advancing the event/cursor frontier.
    # A failed ledger leaves the cursor unchanged; retry safely fills either side.
    if observation_sink:observation_sink(observations)
    rows=store.commit_symbol(market,symbol,updates,cursor,summary)
    new_rows=[r for r in rows if r['trigger_open_time'] not in previous]
    new_forward=sum(not r.get('historical') for r in new_rows)
    new_historical=sum(bool(r.get('historical')) for r in new_rows)
    return dict(market=market,symbol=symbol,confirmed=confirmed,new_events=new,new_forward=new_forward,new_historical=new_historical,pending=sum(p['pending_confirm'] for _,p in updates),observations=observations)
