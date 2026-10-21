"""Explicit Binance historical normalization; no default network implementation.

Caller supplies request(url, params) -> {status, data, http_attempts, retries}.
The worker is not installed as a timer/GET side effect. Provider cap permission
and trusted contract identity must be supplied by the authorized collector.
"""
import math
from .recovery import SourceFailure
H=3600000;H4=4*H

def number(x):
    try:r=float(x)
    except (TypeError,ValueError):raise SourceFailure('invalid_numeric_source')
    if isinstance(x,bool) or not math.isfinite(r) or r<0:raise SourceFailure('invalid_numeric_source')
    return r

class HistoricalAdapter:
    def __init__(self,*,request,clock,contracts,cap_fetch=None,history_window_ms=30*24*H,history_policy_version='legacy-binance-30d-assumption-v1'):
        if not callable(request) or not callable(clock):raise ValueError('explicit_request_and_clock_required')
        self.request=request;self.clock=clock;self.contracts=contracts;self.cap_fetch=cap_fetch
        if type(history_window_ms) is not int or history_window_ms<=0 or not isinstance(history_policy_version,str) or not history_policy_version:raise ValueError('explicit_history_policy')
        self.history_window_ms=history_window_ms;self.history_policy_version=history_policy_version
        self.telemetry=[]
    def __call__(self,job,*,start_ms,end_ms,limit,attempt):
        if type(limit) is not int or not 1<=limit<=500 or start_ms>end_ms:raise SourceFailure('invalid_source_page')
        market,symbol=job['contract'].split('|',1);meta=self.contracts.get((market,symbol))
        if not isinstance(meta,dict) or meta.get('verified') is not True:raise SourceFailure('contract_identity_unverified')
        if market not in ('USDT-M','COIN-M'):raise SourceFailure('unsupported_market')
        source=job['source'];clock=self.clock();inverse=market=='COIN-M'
        if source not in ('candles_1h','candles_4h','cap') and start_ms<clock-self.history_window_ms:raise SourceFailure('history_window_exceeded',self.history_policy_version)
        if source=='cap':
            if not callable(self.cap_fetch):raise SourceFailure('cap_history_provider_not_configured')
            # Callback must preserve effective/available/trusted/unit provenance;
            # source_quality applies the <=1h historical gate, never current fill.
            # Configured cap transport performs exactly one gated attempt and
            # returns provenance-bearing data. Permissions never auto-upgrade.
            response=attempt(lambda **kw:self.cap_fetch(market=market,symbol=symbol,start_ms=start_ms,end_ms=end_ms,limit=limit,**kw))
            if response['status']!=200:raise SourceFailure(str(response['status']))
            rows=response.get('data')
            if not isinstance(rows,list):raise SourceFailure('invalid_cap_source')
            for r in rows:
                if r.get('unit')!='USD' or r.get('trusted') is not True or type(r.get('effective_at_ms')) is not int or type(r.get('available_at_ms')) is not int:raise SourceFailure('cap_provenance_unverified')
                number(r.get('value'))
            return rows
        host='https://dapi.binance.com' if inverse else 'https://fapi.binance.com'
        if source in ('candles_1h','candles_4h'):
            candle_step=H4 if source=='candles_4h' else H
            interval='4h' if source=='candles_4h' else '1h'
            if job['timeframe']!=interval:raise SourceFailure('candle_interval_mismatch')
            # Recovery times are CLOSE boundaries; Binance kline times are OPEN.
            url=host+('/dapi/v1/klines' if inverse else '/fapi/v1/klines')
            params=dict(symbol=symbol,interval=interval,startTime=start_ms-candle_step,endTime=end_ms-1,limit=limit)
        elif source in ('oi','lsr'):
            if inverse and not meta.get('pair'):raise SourceFailure('coin_pair_unknown')
            url=host+'/futures/data/'+('openInterestHist' if source=='oi' else 'globalLongShortAccountRatio')
            periods={300000:'5m',900000:'15m',1800000:'30m',H:'1h',2*H:'2h',H4:'4h',6*H:'6h',12*H:'12h',24*H:'1d'}
            period=periods.get(job.get('period_ms'))
            if period is None:raise SourceFailure('adapter_contract_missing')
            params=dict(period=period,startTime=start_ms,endTime=end_ms,limit=limit)
            params['pair' if inverse else 'symbol']=meta['pair'] if inverse else symbol
            if inverse and source=='oi':params['contractType']='PERPETUAL'
        else:raise SourceFailure('unsupported_source')
        if job.get('provider')!='binance' or job.get('endpoint')!=url:raise SourceFailure('adapter_contract_missing')
        response=attempt(lambda **kw:self.request(url,params,**kw))
        if not isinstance(response,dict) or type(response.get('status')) is not int:raise SourceFailure('http_status_telemetry_required')
        status=response['status'];attempts=response.get('http_attempts',1);retries=response.get('retries',0)
        self.telemetry.append(dict(source=source,contract=job['contract'],start_ms=start_ms,end_ms=end_ms,http_status=status,http_attempts=attempts,retries=retries,clock_ms=self.clock()))
        if status!=200:raise SourceFailure(str(status))
        if type(attempts) is not int or attempts<1 or type(retries) is not int or not 0<=retries<attempts:raise SourceFailure('http_attempt_telemetry_required')
        data=response.get('data');available=self.clock()
        if not isinstance(data,list) or len(data)>limit:raise SourceFailure('invalid_http_page')
        rows=[]
        for r in data:
            if source in ('candles_1h','candles_4h'):
                if not isinstance(r,list) or len(r)<8 or int(r[6])!=int(r[0])+candle_step-1:raise SourceFailure('invalid_candle_close')
                q=number(r[5])*number(meta.get('contract_size')) if inverse else number(r[7])
                if inverse and number(meta.get('contract_size'))<=0:raise SourceFailure('coin_multiplier_unknown')
                bar=dict(open_ms=int(r[0]),o=number(r[1]),h=number(r[2]),l=number(r[3]),c=number(r[4]),q=q,q_unit='USD nominal quote')
                if bar['open_ms']%candle_step or bar['open_ms']+candle_step>available or min(bar[k] for k in ('o','h','l','c'))<=0 or not bar['l']<=min(bar['o'],bar['c'])<=max(bar['o'],bar['c'])<=bar['h']:
                    raise SourceFailure('invalid_candle_ohlc_or_boundary')
                rows.append(dict(effective_at_ms=bar['open_ms']+candle_step,available_at_ms=available,unit='USD nominal quote',bar=bar,raw_kline=r,contract_size=meta.get('contract_size')))
            else:
                if not isinstance(r,dict) or type(r.get('timestamp')) is not int:raise SourceFailure('source_effective_time_unknown')
                unit='base_asset' if inverse and source=='oi' else 'USD' if source=='oi' else 'ratio'
                rows.append(dict(effective_at_ms=r['timestamp'],available_at_ms=available,value=number(r.get('sumOpenInterestValue' if source=='oi' else 'longShortRatio')),unit=unit))
        return rows
