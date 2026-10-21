"""Bounded statistical-source windows; acquisition does not imply qualification."""
H=3_600_000
HISTORY_POLICY="legacy-binance-1h-30d-assumption-v1"

def missing_ranges(times,start,end,step=H):
    """Inclusive sample timestamps -> half-open gaps; no dense timestamp set."""
    result=[];position=start
    for t in sorted(set(int(t) for t in times if start<=int(t)<=end)):
        if t>position: result.append(dict(start=position,end=t))
        position=max(position,t+step)
    if position<=end: result.append(dict(start=position,end=end+step))
    return result

def resume_source(old_rows,*,target,now_ms,fetch,prior=None,page_budget=4,
                  history_window_ms=30*24*H,history_policy=HISTORY_POLICY):
    if type(page_budget) is not int or not 0<=page_budget<=4: raise ValueError('source_budget_0_4')
    if not callable(fetch): raise ValueError('source_adapter_required')
    if history_window_ms is not None and (type(history_window_ms) is not int or history_window_ms<=0): raise ValueError('source_history_policy')
    rows={int(r['timestamp']):dict(r) for r in old_rows if r.get('timestamp') is not None}
    prior=prior or {};first=min(min(rows,default=target-499*H),prior.get('coverage_start',target))
    available_from=((now_ms-history_window_ms+H-1)//H)*H if history_window_ms is not None else first
    overlap=max(available_from,target-H)
    # Do not merge the near-end overlap into the oldest backlog. This reserved
    # window is visited at least once per bounded round when due.
    historical=[]
    for gap in missing_ranges(rows,first,target):
        a=max(gap['start'],available_from);b=min(target,gap['end']-H,overlap-H)
        while a<=b:
            stop=min(b,a+499*H);historical.append((a,stop));a=stop+H
    retries=dict(prior.get('window_retries') or {})
    def key(w): return str(w[0])+':'+str(w[1])
    def due(w): return retries.get(key(w),{}).get('next_retry_at',0)<=now_ms
    recent=(overlap,target) if overlap<=target else None
    historical.sort(key=lambda w:(retries.get(key(w),{}).get('last_scheduled_at',0),w))
    windows=([recent] if recent and due(recent) else [])+[w for w in historical if due(w)]
    revisions=[];errors=[];requests=0;fetched_through=prior.get('fetched_through')
    for start,stop in windows[:page_budget]:
        w=(start,stop);requests+=1;old_retry=retries.get(key(w),{})
        try:
            received=fetch(start,stop)
            if not isinstance(received,list) or len(received)>500: raise ValueError('invalid_source_page')
            valid=[]
            for row in received:
                if not isinstance(row,dict) or type(row.get('timestamp')) is not int or not start<=row['timestamp']<=stop or (row['timestamp']-start)%H:
                    raise ValueError('source_page_timestamp')
                valid.append(row)
            for row in valid:
                t=row['timestamp'];previous=rows.get(t);bare={k:v for k,v in (previous or {}).items() if not k.startswith('_')}
                clean={k:v for k,v in row.items() if not k.startswith('_')}
                if previous and bare!=clean: revisions.append(previous)
                same=previous and bare==clean
                rows[t]=dict(clean,_first_retrieved_at=previous.get('_first_retrieved_at',now_ms) if previous else now_ms)
                if same and '_revision_retrieved_at' in previous: rows[t]['_revision_retrieved_at']=previous['_revision_retrieved_at']
                if previous and not same: rows[t]['_revision_retrieved_at']=now_ms
            if valid: fetched_through=max(fetched_through or 0,max(r['timestamp'] for r in valid))
            unresolved=bool(missing_ranges(rows,start,stop))
            failures=old_retry.get('empty_count',0)+1 if unresolved else 0
            retries[key(w)]=dict(last_scheduled_at=now_ms,empty_count=failures,next_retry_at=now_ms+(min(24*H,300000*2**min(failures-1,8)) if unresolved else H),reason='not_returned' if unresolved else 'overlap_complete')
        except (OSError,ValueError,TypeError,RuntimeError) as exc:
            errors.append(str(exc));failures=old_retry.get('empty_count',0)+1
            retries[key(w)]=dict(last_scheduled_at=now_ms,empty_count=failures,next_retry_at=now_ms+min(24*H,300000*2**min(failures-1,8)),reason='source_error')
    gaps=missing_ranges(rows,first,target);ranges=[]
    for gap in gaps:
        cut=min(gap['end'],available_from)
        if gap['start']<cut: ranges.append(dict(start=gap['start'],end=cut,status='unrecoverable',reason='supplier_history_window_assumption',history_policy=history_policy,as_of_ms=now_ms))
        if max(gap['start'],available_from)<gap['end']: ranges.append(dict(start=max(gap['start'],available_from),end=gap['end'],status='pending',reason='source_error' if errors else 'not_returned_or_budget'))
    # Keep only current windows; raw/revision rows stay intact. A split gap is
    # a new bounded request, but a stable empty gap honors its own durable backoff.
    live={key(w) for w in historical}
    if recent: live.add(key(recent))
    retries={k:v for k,v in retries.items() if k in live}
    state=dict(coverage_start=first,requested_through=target,fetched_through=fetched_through,
        archived_contiguous_through=gaps[0]['start']-H if gaps else target,latest_sample=max(rows,default=None),
        gaps=ranges,attempted_at=now_ms if requests else prior.get('attempted_at'),
        attempts=prior.get('attempts',0)+int(requests>0),request_count=requests,
        next_retry_at=min((r['next_retry_at'] for r in retries.values()),default=None),
        window_retries=retries,history_policy=history_policy,history_window_ms=history_window_ms,
        errors=errors,coverage_status='incomplete' if ranges else 'complete',
        acquisition_scope='existing_explicit_collection_only')
    return [rows[k] for k in sorted(rows)],revisions,state

