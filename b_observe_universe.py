"""B uses the trading directory or a trustworthy full scan, never the A pool."""
import json,time,hashlib
from pathlib import Path
from durable_io import atomic_json
def load_universe(root):
    root=Path(root);snap=root/'scan.json';saved={}
    if snap.exists():
        d=json.loads(snap.read_text(encoding='utf-8'));saved={(r['market'],r['symbol']):r for r in d.get('rows',[]) if r.get('market') and r.get('symbol')}
    p=root/'runtime'/'b-observe-universe.json'
    cached=json.loads(p.read_text(encoding='utf-8')) if p.exists() else {}
    if isinstance(cached,dict) and cached.get('scope')=='trading_directory':
        rows=cached.get('symbols',[])
        for r in rows:
            for k,v in saved.get((r['market'],r['symbol']),{}).items():
                if k.startswith('cap'):r[k]=v
        return rows
    if saved:return [dict(r,universe_scope='saved_scan',directory_retrieved_at=d.get('available_at'),directory_expected=d.get('total')) for r in saved.values()]
    rows=cached.get('symbols',[]) if isinstance(cached,dict) else cached
    return [dict(r,universe_scope='explicit_subset') for r in rows if isinstance(r,dict) and r.get('market') and r.get('symbol')]
def fetch_universe(root,requester):
    items=[]
    for market,host,prefix in [('USDⓈ-M','https://fapi.binance.com','/fapi/v1/'),('COIN-M','https://dapi.binance.com','/dapi/v1/')]:
        d,error=requester(host+prefix+'exchangeInfo',{})
        if error or not isinstance(d,dict):raise RuntimeError(str(error or 'invalid trading directory'))
        rows=[r for r in d.get('symbols',[]) if r.get('contractType')=='PERPETUAL' and r.get('status',r.get('contractStatus'))=='TRADING']
        if not rows:raise ValueError('交易目录为空，不能发布空覆盖')
        items.extend(dict(r,market=market,universe_scope='trading_directory') for r in rows)
    directory_version=hashlib.sha256(json.dumps(items,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode()).hexdigest()
    for item in items:
        item['directory_retrieved_at']=int(time.time()*1000)
        item['directory_version']=directory_version
        item['directory_expected']=len(items)
    # Enrich with existing verified market-cap metadata, without moving its source time.
    prior={(r['market'],r['symbol']):r for r in load_universe(root)}
    for r in items:
        old=prior.get((r['market'],r['symbol']),{})
        for k,v in old.items():
            if k.startswith('cap') or k in ('asset','underlyingType','candleArchives','candleAsOf'):r.setdefault(k,v)
    atomic_json(Path(root)/'runtime'/'b-observe-universe.json',dict(symbols=items,source='Binance exchangeInfo',retrieved_at=int(time.time()*1000),scope='trading_directory'))
    return items
