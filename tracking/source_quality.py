"""Coverage facts from actual inputs, independent per source and from evaluation."""
from .c_rebound_rules import structure_at

def quality(raw,oi,lsr,caps,*,target,clock,first_required=None,evaluated=None):
    start=first_required or target
    boundaries=range(start,target+1,14400000)
    valid=lambda seq:[s for s in seq if type(s.get('available_at_ms')) is int and s['available_at_ms']<=clock]
    oi,lsr,caps=valid(oi),valid(lsr),valid(caps)
    raw_closes={b['open_ms']+14400000 for b in raw}
    states={name:dict(requested_end_ms=target,fetched_max_ms=max([s['effective_at_ms'] for s in seq],default=None),continuous_end_ms=None,first_gap_ms=None,evaluated_end_ms=evaluated) for name,seq in [('oi',oi),('lsr',lsr),('cap',caps)]}
    states['candles_4h']=dict(requested_end_ms=target,fetched_max_ms=max(raw_closes,default=None),continuous_end_ms=None,first_gap_ms=None,evaluated_end_ms=evaluated)
    for t in boundaries:
        st=structure_at(t,oi_samples=oi,lsr_samples=lsr,caps=caps)
        good=dict(oi=st['oi_sample'] is not None,lsr=st['lsr_sample'] is not None,cap=st['cap_sample'] is not None,candles_4h=t in raw_closes)
        for name,row in states.items():
            if row['first_gap_ms'] is None:
                if good[name]:row['continuous_end_ms']=t
                else:row['first_gap_ms']=t
    for row in states.values():
        if row['evaluated_end_ms'] is not None:
            row['evaluated_end_ms']=min(row['evaluated_end_ms'],row['continuous_end_ms']) if row['continuous_end_ms'] is not None else None
        row['coverage_start_ms']=start
        row['reason']='missing_or_invalid_contemporaneous_evidence' if row['first_gap_ms'] is not None else None
    return states
