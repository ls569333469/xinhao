"""Normalize closed Binance candles without borrowing units across markets."""
import math,time
BAR_MS=3_600_000
def closed_1h_bars(raw_rows, *, target_close_boundary=None, market='USDⓈ-M', contract_size=None):
    stop=int(time.time()*1000) if target_close_boundary is None else int(target_close_boundary)
    result={}
    for r in raw_rows or []:
        try:
            if len(r)<8:continue
            ot=int(r[0]);ct=int(r[6]);o,h,l,c=map(float,r[1:5]);base=float(r[5])
            if ot % BAR_MS != 0 or ct!=ot+BAR_MS-1 or ot+BAR_MS>stop:continue
            if market in ('COIN-M','COIN_M'):
                if not contract_size or float(contract_size)<=0:continue
                q=base*float(contract_size)
            else:q=float(r[7])
            if not all(math.isfinite(x) for x in (o,h,l,c,q,base)):continue
            if min(o,h,l,c)<=0 or q<0 or base<0 or h<max(o,c,l) or l>min(o,c,h):continue
            result[ot]=dict(open_time=ot,close_time=ct,o=o,h=h,l=l,c=c,q=q,base_v=base)
        except (ValueError,TypeError,OverflowError):continue
    return [result[k] for k in sorted(result)]
def ingest_symbol_1h(cache, *, market, symbol, raw_klines, target_close_boundary=None, quality=None, contract_size=None):
    bars=closed_1h_bars(raw_klines,target_close_boundary=target_close_boundary,market=market,contract_size=contract_size)
    refs=cache.put_many([dict(market=market,symbol=symbol,interval='1h',open_time=b['open_time'],close_time=b['close_time'],bar={k:b[k] for k in ('o','h','l','c','q','base_v')}) for b in bars],quality=quality or 'ok')
    return dict(market=market,symbol=symbol,interval='1h',closed_bars=len(bars),changed_opens=[b['open_time'] for b,ref in zip(bars,refs) if ref['changed']],written=sum(not r["reused"] for r in refs),first_open=bars[0]['open_time'] if bars else None,last_open=bars[-1]['open_time'] if bars else None,target_close_boundary=target_close_boundary)
