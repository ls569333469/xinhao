"""Bounded public-data adapter: target range, incremental pages and explicit evidence."""
def _hosts(market):
    if market in ('COIN-M','COIN_M'):return 'https://dapi.binance.com','/dapi/v1/'
    if market in ('USDⓈ-M','USD-M','USDT-M'):return 'https://fapi.binance.com','/fapi/v1/'
    raise ValueError('未知合约市场')
def _request(requester,url,params):
    if requester is None:
        from scanner import safe
        requester=safe
    data,error=requester(url,params)
    if error:raise RuntimeError(str(error))
    return data
def fetch_1h_klines(market,symbol,*,limit=500,requester=None,start_time=None,end_time=None,interval='1h'):
    host,prefix=_hosts(market);params=dict(symbol=symbol,interval=interval,limit=min(int(limit),1500))
    if start_time is not None:params['startTime']=int(start_time)
    if end_time is not None:params['endTime']=int(end_time)-1
    data=_request(requester,host+prefix+'klines',params)
    if not isinstance(data,list):raise ValueError('K线响应不是列表')
    return data
def fetch_oi_lsr_1h(market,symbol,*,pair=None,limit=500,requester=None,start_time=None,end_time=None,sources=('oi','lsr')):
    host,_=_hosts(market);inverse=host.endswith('dapi.binance.com')
    if inverse and not pair:raise ValueError('COIN-M历史统计必须提供可信pair，不推断symbol')
    params=dict(period='1h',limit=min(limit,500));params['pair' if inverse else 'symbol']=pair if inverse else symbol
    if start_time is not None:params['startTime']=int(start_time)
    if end_time is not None:params['endTime']=int(end_time)
    out=dict(oi_hist=None,lsr=None,oi_quality='unknown',lsr_quality='unknown',oi_samples=[],lsr_samples=[],errors={})
    # COIN-M OI endpoint needs contractType and returns base-asset value, not USD.
    for key,endpoint in [('oi','openInterestHist'),('lsr','globalLongShortAccountRatio')]:
        if key not in sources:continue
        args=dict(params)
        if inverse and key=='oi':args['contractType']='PERPETUAL'
        try:
            rows=_request(requester,host+'/futures/data/'+endpoint,args)
            if not isinstance(rows,list):raise ValueError('历史统计不是列表')
            out[key+'_samples']=rows
            if rows:
                out['oi_hist' if key=='oi' else 'lsr']=rows[-1];out[key+'_quality']='pass'
                field='sumOpenInterestValue' if key=='oi' else 'longShortRatio'
                if rows[-1].get(field) is not None:out[field]=float(rows[-1][field])
        except (OSError,ValueError,TypeError,RuntimeError) as e:out['errors'][key]=str(e)
    out['oi_value_unit']='base_asset' if inverse else 'usd'
    return out
def make_fetch_1h(requester=None):
    def fetch(market,symbol,**kwargs):return fetch_1h_klines(market,symbol,requester=requester,**kwargs)
    return fetch
