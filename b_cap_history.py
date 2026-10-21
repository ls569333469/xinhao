"""Verified historical cap acquisition. Source timestamps never get backdated."""
import datetime as dt,json,math,re,http.client
from pathlib import Path
from b_evidence import ms
from b_repair_store import RepairStore,H
from durable_io import read_bytes_shared

def valid_number(value):return type(value) in (int,float) and math.isfinite(value) and value>0

def import_local(root,now,limit=2):
    """Incremental scan revisions; never read the changing top-level scan.json."""
    store=RepairStore(root);store.initialize();done=0
    paths=sorted((Path(root)/'scan-history').glob('scan-*/revision-*.json'),key=lambda p:(p.stat().st_mtime_ns,str(p)),reverse=True)
    for path in paths:
        stamp=str(path.stat().st_mtime_ns)+':'+str(path.stat().st_size)
        with store.db() as db:old=db.execute('SELECT stamp FROM imported WHERE path=?',(str(path),)).fetchone()
        if old and old[0]==stamp:continue
        doc=json.loads(read_bytes_shared(path));store.record_caps(doc.get('rows',[]),now)
        with store.db(True) as db:db.execute('INSERT OR REPLACE INTO imported VALUES(?,?)',(str(path),stamp))
        done+=1
        if done>=limit:break
    return done

def fetch_historical(root,item,boundaries,now,request):
    """Official provider history for explicit trusted IDs, with original timestamps."""
    store=RepairStore(root);store.initialize()
    if item.get('capTrusted') is not True or item.get('capSource') not in ('CoinGecko','CoinPaprika') or not item.get('capId'):
        return dict(blocked=True,reason='historical_cap_provider_not_configured_or_identity_unverified')
    if not re.fullmatch('[a-zA-Z0-9_-]+',item['capId']):return dict(blocked=True,reason='historical_cap_identity_invalid')
    start=min(boundaries)-H;end=max(boundaries)
    # Cache permission/no-coverage failures per asset and UTC day, across contracts/events.
    provider=item['capSource'].lower()
    key=provider+':'+item['capId']+':'+str(start//(24*H))+':'+str(end//(24*H))
    with store.db() as db:
        prior=db.execute("SELECT * FROM provider_windows WHERE key IN (?,?,?) AND next_try>? ORDER BY next_try DESC LIMIT 1",(key,provider+':historical_access',provider+':rate_limit',now)).fetchone()
    if prior and prior['next_try']>now:return dict(blocked=any(s in prior['error'] for s in ('401','402','403','404','no_sample','invalid')),reason=prior['error'],next_try=prior['next_try'])
    url=('https://api.coingecko.com/api/v3/coins/'+item['capId']+'/market_chart/range' if provider=='coingecko' else
         'https://api.coinpaprika.com/v1/tickers/'+item['capId']+'/historical')
    try:
        if provider=='coingecko':
            data=request(url,dict(vs_currency='usd',**{'from':start//1000,'to':end//1000}))
            if not isinstance(data,dict) or not isinstance(data.get('market_caps'),list):raise ValueError('historical_cap_response_invalid')
            points=[(int(t),v) for t,v in data['market_caps'] if valid_number(v) and start<=int(t)<=end]
        else:
            stamp=lambda t:dt.datetime.fromtimestamp(t/1000,dt.timezone.utc).isoformat().replace('+00:00','Z')
            data=request(url,dict(start=stamp(start),end=stamp(end+1000),interval='1h'))
            if not isinstance(data,list):raise ValueError('historical_cap_response_invalid')
            points=[(ms(x.get('timestamp')),x['market_cap']) for x in data if isinstance(x,dict) and ms(x.get('timestamp')) is not None and valid_number(x.get('market_cap')) and start<=ms(x['timestamp'])<=end]
        caps=[]
        for boundary in boundaries:
            eligible=[(t,v) for t,v in points if 0<=boundary-t<=H]
            if not eligible:continue
            t,v=max(eligible)
            cap={k:v for k,v in item.items() if k.startswith('cap') and not isinstance(v,(dict,list))}
            # Clear current-price/supply metadata before replacing it with historical evidence.
            for field in ('capPrice','capCirculatingSupply','capAsOf','capObservedAt','capRetrievedAt'):cap.pop(field,None)
            cap.update(capUsd=v,capObservedAt=t,capAsOf=t,capRetrievedAt=now,capTimestampKind='source_observed_at',capHistorical=True,capSourceUrl=url,capQualityStatus='供应商历史market_caps；未将当前值回填历史')
            caps.append(dict(market=item['market'],symbol=item['symbol'],**cap))
        store.record_caps(caps,now)
        reason=None if len(caps)==len(boundaries) else 'historical_cap_no_sample_within_freshness_gate'
    except (OSError,ValueError,TypeError,RuntimeError) as exc:reason=str(exc)[:300]
    if reason:
        permanent=any(s in reason for s in ('401','402','403','404','not_configured','unverified','no_sample','invalid'))
        with store.db(True) as db:
            db.execute('INSERT OR REPLACE INTO provider_windows VALUES(?,?,?)',(key,now+(24*H if permanent else 15*60_000),reason))
            if any(s in reason for s in ('401','403')):db.execute('INSERT OR REPLACE INTO provider_windows VALUES(?,?,?)',(provider+':historical_access',now+24*H,reason))
            if '429' in reason or '418' in reason:db.execute('INSERT OR REPLACE INTO provider_windows VALUES(?,?,?)',(provider+':rate_limit',now+15*60_000,reason))
        return dict(blocked=permanent,reason=reason)
    with store.db(True) as db:db.execute('DELETE FROM provider_windows WHERE key=?',(key,))
    return dict(blocked=False,reason=None)

def refresh_current(root,items,now,request,*,target_boundary=None,clock=None):
    """One shared current-cap batch per provider, for future closed boundaries."""
    store=RepairStore(root);store.record_caps(items,now)
    selected=[x for x in items if x.get('capTrusted') is True and x.get('capSource')=='CoinGecko' and x.get('capId')]
    ids=sorted({x['capId'] for x in selected});written=0;errors=[];attempts=0
    acquired_at=now
    def fetch_current(url,params):
        nonlocal attempts,acquired_at
        # Retry only a transient transport/server failure; every attempt still
        # passes through the caller's shared finite request/pacing gate.
        for attempt in range(2):
            try:
                attempts+=1
                data=request(url,params)
                acquired_at=clock() if clock else now
                if target_boundary and acquired_at>=target_boundary:
                    errors.append(dict(url=url,reason='preclose_boundary_missed_after_response',attempt=attempt+1));return None
                if not isinstance(data,list):raise ValueError('current_cap_response_invalid')
                return data
            except (OSError,ValueError,RuntimeError,http.client.IncompleteRead) as exc:
                import re
                reason=type(exc).__name__+': '+str(exc)[:220]
                errors.append(dict(url=url,reason=reason,attempt=attempt+1))
                code=re.search(r'HTTP\s+(\d{3})',reason)
                transient=isinstance(exc,(OSError,http.client.IncompleteRead)) or bool(code and int(code.group(1)) in (500,502,503,504))
                if not transient or any(k in reason for k in ('401','402','403','429','418','budget','paused','deadline')):break
        return None
    for offset in range(0,len(ids),250):
        data=fetch_current('https://api.coingecko.com/api/v3/coins/markets',dict(vs_currency='usd',ids=','.join(ids[offset:offset+250]),per_page=250,page=1))
        if data is None:continue
        by={x.get('id'):x for x in data if isinstance(x,dict)};caps=[]
        for item in selected:
            value=by.get(item['capId'],{});at=ms(value.get('last_updated'))
            if at is None or not 0<=acquired_at-at<=H or not valid_number(value.get('market_cap')):continue
            supply=value.get('circulating_supply');price=value.get('current_price')
            if supply is not None and (not valid_number(supply) or not valid_number(price) or abs(value['market_cap']/(supply*price)-1)>.05):continue
            cap={k:v for k,v in item.items() if k.startswith('cap') and not isinstance(v,(dict,list))}
            cap.update(capUsd=value['market_cap'],capPrice=price,capCirculatingSupply=supply,capObservedAt=value['last_updated'],capAsOf=value['last_updated'],capRetrievedAt=acquired_at,capHistorical=False,capTimestampKind='source_observed_at',capSourceUrl='https://api.coingecko.com/api/v3/coins/markets')
            caps.append(dict(market=item['market'],symbol=item['symbol'],**cap))
        store.record_caps(caps,acquired_at);written+=len(caps)
    paprika=[x for x in items if x.get('capTrusted') is True and x.get('capSource')=='CoinPaprika' and x.get('capId')]
    if paprika:
        data=fetch_current('https://api.coinpaprika.com/v1/tickers',{})
        data=data if data is not None else []
        by={x.get('id'):x for x in data if isinstance(x,dict)};caps=[]
        for item in paprika:
            value=by.get(item['capId'],{});at=ms(value.get('last_updated'));usd=value.get('quotes',{}).get('USD',{})
            if at is None or not 0<=acquired_at-at<=H or not valid_number(usd.get('market_cap')):continue
            cap={k:v for k,v in item.items() if k.startswith('cap') and not isinstance(v,(dict,list))}
            for field in ('capPrice','capCirculatingSupply'):cap.pop(field,None)
            cap.update(capUsd=usd['market_cap'],capObservedAt=at,capAsOf=at,capRetrievedAt=acquired_at,capHistorical=False,capTimestampKind='source_observed_at',capSourceUrl='https://api.coinpaprika.com/v1/tickers')
            caps.append(dict(market=item['market'],symbol=item['symbol'],**cap))
        store.record_caps(caps,acquired_at);written+=len(caps)
    selected+=paprika
    return dict(status='success' if written==len(items) and items else 'partial_success',recorded=written,errors=errors,http_attempts=attempts,missing_contracts=len(items)-written,target_close_boundary=target_boundary,provider_assets=len(ids)+len({x['capId'] for x in paprika}),
                eligible_contracts=len(selected),unsupported_contracts=len(items)-len(selected),
                reason=None if written==len(items) and items else '部分来源不支持提前归档或未返回合格市值；未视为全覆盖')
