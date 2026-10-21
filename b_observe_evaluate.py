"""Unified side-effect-free event calculation and compatible cache entry point."""
import hashlib,json
from b_accumulation_rules import BAR_MS,BASELINE_BARS,evaluate_h_event,evaluate_h1_confirm,combine_tri,risk_theory,structure_gate
from b_accumulation_store import BAccumulationStore
from b_evidence import structure_at
from market_candle_cache import MarketCandleCache
def bars_from_cache(cache,*,market,symbol,interval='1h',start_open=None,end_open=None):
    if not cache.db_path.exists():return []
    result=[]
    for r in cache.get_bars(market=market,symbol=symbol,interval=interval,start_open=start_open,end_open=end_open):
        p=r['payload'];result.append(dict(p['bar'],open_time=int(p['open_time']),close_time=p.get('close_time'),payload_hash=r['payload_hash'],block_id=r['block_id']))
    return result
def _fingerprint(obj):return hashlib.sha256(json.dumps(obj,ensure_ascii=False,sort_keys=True,separators=(',',':'),allow_nan=False).encode()).hexdigest()
def calculate_event(bars,index,*,target,background,structure,reference_open=None,observed=None):
    h=bars[index];ot=int(h['open_time']);t=ot+BAR_MS
    bg=background(t);bg1=background(t+BAR_MS) if t+BAR_MS<=target else None
    ev=evaluate_h_event(bars,index,bg4h=bg)
    conf=evaluate_h1_confirm(bars,ot,bg4h_h1=bg1,h1_closed=t+BAR_MS<=target)
    sh=structure(t);sc=structure(t+BAR_MS) if t+BAR_MS<=target else dict(state='pending')
    combined=combine_tri(sh['state'],sc['state']) if sc['state']!='pending' else ('fail' if sh['state']=='fail' else 'pending')
    h1=next((b for b in bars if int(b['open_time'])==ot+BAR_MS),None)
    nextopen=next((b for b in bars if int(b['open_time'])==ot+2*BAR_MS),None)
    reference_error=None
    if callable(reference_open):
        if not nextopen and conf['technical_confirmation']=='pass':
            try:reference_open=reference_open()
            except Exception as exc:reference_open=None;reference_error=str(exc)[:300]
        else:reference_open=None
    p=nextopen.get('o') if nextopen else reference_open.get('price') if reference_open and reference_open.get('open_time')==ot+2*BAR_MS else None
    s0=bg1.get('ema99') if bg1 else None
    def volume_inputs(idx):
        prev=bars[max(0,idx-168):idx];values=[b.get('q') for b in prev]
        total=sum(values) if len(prev)==168 and all(isinstance(v,(int,float)) for v in values) else None
        return dict(numerator=bars[idx].get('q'),baseline_sum=total,baseline_mean=total/168 if total is not None else None,count=len(prev),start_open_time=prev[0]['open_time'] if prev else None,end_open_time=prev[-1]['open_time'] if prev else None,unit='USD nominal quote')
    h1_index=next((i for i,b in enumerate(bars) if b['open_time']==ot+BAR_MS),None)
    payload=dict(reference_error=reference_error,technical_event=ev['technical_event'],technical_confirmation=conf['technical_confirmation'],pending_confirm=conf['pending'],structure_h=sh,structure_confirmation=sc,structure_gate=combined,event_ratio=ev.get('ratio'),confirm_ratio=conf.get('ratio'),abs_return_ok=ev.get('abs_return_ok'),expected_confirmation_open_time=ot+BAR_MS,h=h,h1=h1,bg4h=bg,bg4h_h1=bg1,baseline=bars[max(0,index-168):index],P_theory=p,P_observed=observed.get('price') if observed else None,observed_quote=observed,risk_observed=risk_theory(p_theory=observed.get('price') if observed else None,s0=s0),S0=s0,risk_theory=risk_theory(p_theory=p,s0=s0),target_close_boundary=target,reasons=ev['reasons']+conf['reasons'],event_volume=volume_inputs(index),confirmation_volume=volume_inputs(h1_index) if h1_index is not None else None,theory_open_time=ot+2*BAR_MS if p is not None else None,schema_version=2,rule_registry_hash=__import__('strategy_registry').VERSION_HASH,evidence_profile='binance-1h-oi-lsr-v1')
    # Decision frontier advances only until the actual h+1 closes. No endless
    # revisions merely because another unrelated hour was scanned.
    semantic={k:v for k,v in payload.items() if k!='target_close_boundary'}
    payload['input_fingerprint']=_fingerprint(semantic)
    return payload
def evaluate_symbol_from_cache(root,*,market,symbol,target_close_boundary=None,bg4h=None,bg4h_h1=None,oi_cap_pct=None,lsr=None,oi_quality='unknown',lsr_quality='unknown',cap_quality='unknown',oi_samples=None,lsr_samples=None,max_lag_ms=300000,market_cap=None,cap_observed_at=None):
    cache=MarketCandleCache(root);store=BAccumulationStore(root);store.ensure_observation_start();bars=bars_from_cache(cache,market=market,symbol=symbol)
    if not bars:return dict(evaluated=False,reason='no_bars')
    target=target_close_boundary or bars[-1]['open_time']+BAR_MS;ot=target-BAR_MS
    index=next((i for i,b in enumerate(bars) if b['open_time']==ot),None)
    if index is None or index<168:return dict(evaluated=False,reason='warmup_short')
    def structure(t):
        if oi_samples is not None or lsr_samples is not None:
            return structure_at(t,oi_samples=oi_samples,lsr_samples=lsr_samples,cap=dict(capUsd=market_cap,capTrusted=cap_quality=='pass',capObservedAt=cap_observed_at))
        return dict(state=structure_gate(oi_cap_pct=oi_cap_pct,lsr=lsr,oi_quality=oi_quality,lsr_quality=lsr_quality,cap_quality=cap_quality),oi_cap_pct=oi_cap_pct,lsr=lsr,oi_quality=oi_quality,lsr_quality=lsr_quality,cap_quality=cap_quality)
    payload=calculate_event(bars,index,target=target,background=lambda t:bg4h if t==target else bg4h_h1,structure=structure)
    rec=store.upsert_event(market=market,symbol=symbol,trigger_open_time=ot,payload=payload)
    return dict(evaluated=True,event_id=rec['event_id'],event_revision=rec['event_revision'],technical_event=rec['technical_event'],technical_confirmation=rec['technical_confirmation'])
