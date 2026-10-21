"""Historical as-of inputs; no network, no inferred availability."""
import datetime as dt,math
from b_contemporaneous import pick_sample_at_or_before
from b_accumulation_rules import structure_gate
H=3600000
def ms(value):
    try:
        if isinstance(value,(int,float)):return int(value)
        t=dt.datetime.fromisoformat(str(value).replace('Z','+00:00'))
        return int(t.timestamp()*1000) if t.tzinfo else None
    except (TypeError,ValueError,OverflowError):return None
def background_at(rows,boundary):
    # Same first-value initialization as scanner; a rolling API window has
    # one current, unclosed bar, leaving 1499 closed bars at normal boundaries.
    eligible=[r for r in rows if isinstance(r,(list,tuple)) and len(r)>7 and int(r[0])%(4*H)==0 and int(r[6])==int(r[0])+4*H-1 and int(r[0])+4*H<=boundary]
    eligible=sorted({int(r[0]):r for r in eligible}.values(),key=lambda r:int(r[0]))[-1499:]
    if len(eligible)<120:return None
    if any(int(b[0])-int(a[0])!=4*H for a,b in zip(eligible,eligible[1:])):return None
    if int(eligible[-1][0])+4*H!=(boundary//(4*H))*(4*H):return None
    values=[float(r[4]) for r in eligible]
    if not all(math.isfinite(v) and v>0 for v in values):return None
    def ema(n):
        v=values[0]
        for x in values[1:]:v+=2/(n+1)*(x-v)
        return v
    return dict(open_time=int(eligible[-1][0]),close_boundary=int(eligible[-1][0])+4*H,ema7=ema(7),ema25=ema(25),ema99=ema(99),window_start=int(eligible[0][0]),count=len(values),method='A-first-value-rolling1499-v1',input_rows=eligible)
def structure_at(t,*,oi_samples,lsr_samples,cap,oi_unit='usd',price=None):
    oi,oq,od=pick_sample_at_or_before(t,oi_samples or []);ls,lq,ld=pick_sample_at_or_before(t,lsr_samples or [])
    # Stale/future/missing samples are unknown data, never an economic fail.
    oq='pass' if oq=='pass' else 'unknown';lq='pass' if lq=='pass' else 'unknown'
    cap={k:v for k,v in (cap or {}).items() if k.startswith('cap') and not isinstance(v,(dict,list))};cap_t=ms(cap.get('capObservedAt'));cq='unknown'
    try:
        cv=float(cap.get('capUsd'));trusted=cap.get('capTrusted') is True
        if trusted and cap_t is not None and 0<=t-cap_t<=H and math.isfinite(cv) and cv>0:cq='pass'
    except (TypeError,ValueError):cv=None
    try:
        value=float(oi['sumOpenInterestValue']) if oq=='pass' else None
        if oi_unit!='usd':
            # Requires an aligned price at the very same source timestamp.
            aligned=price(int(oi['timestamp'])) if callable(price) and oi else None
            value=value*aligned if aligned is not None and value is not None else None
        if value is None or not math.isfinite(value) or value<0:oq='unknown';value=None
    except (KeyError,TypeError,ValueError):oq='unknown';value=None
    try:
        ratio=float(ls['longShortRatio']) if lq=='pass' else None
        if ratio is None or not math.isfinite(ratio) or ratio<0:lq='unknown';ratio=None
    except (KeyError,TypeError,ValueError):lq='unknown';ratio=None
    pct=value/cv*100 if value is not None and cq=='pass' else None
    reasons=[]
    if oq!='pass':reasons.append('OI同期证据缺失或超出5分钟门禁')
    elif cq=='pass' and pct<=30:reasons.append('空头燃料不足：OI/流通市值未超过30%')
    if cq!='pass':reasons.append('可信市值缺失、过期或来源时点未知')
    if lq!='pass':reasons.append('多空比同期证据缺失或超出5分钟门禁')
    elif ratio>=.8:reasons.append('多头账户偏拥挤：多空比未低于0.80')
    return dict(reasons=reasons,state=structure_gate(oi_cap_pct=pct,lsr=ratio,oi_quality=oq,lsr_quality=lq,cap_quality=cq),oi_cap_pct=pct,lsr=ratio,oi_usd=value,oi_quality=oq,lsr_quality=lq,cap_quality=cq,oi_qty=oi.get('sumOpenInterest') if oi else None,oi_source_period='1h',lsr_source_period='1h',oi_delta_ms=od,lsr_delta_ms=ld,oi_sample=oi,lsr_sample=ls,cap=cap,source_boundary=t,evidence_profile='binance-1h-oi-lsr-v1')
