"""Bounded reuse of verified local evidence; never sends HTTP or grants permission."""
import hashlib
import json
import math
import re
import sqlite3
from pathlib import Path
from functools import lru_cache

H=3600000

@lru_cache(maxsize=2)
def _scan(path,size,stamp):
    if size>64*1024*1024:raise ValueError('local_scan_size')
    raw=Path(path).read_bytes()
    return hashlib.sha256(raw).hexdigest(),json.loads(raw)

def sync_cap_identities(root):
    """Use existing audited project IDs, never resolve an identity by ticker."""
    from durable_io import atomic_json
    root=Path(root);scan=root/'scan.json';path=root/'runtime/tracking-source-registry.json'
    if not scan.is_file() or not path.is_file():return dict(updated=0)
    stat=scan.stat();sha,saved=_scan(str(scan),stat.st_size,stat.st_mtime_ns)
    data=json.loads(path.read_text(encoding='utf-8-sig'))
    if data.get('cap_scan_sha256')==sha:return dict(updated=0,cached=True)
    changed=0
    for row in saved.get('rows',[]):
        market='USDT-M' if row.get('market')=='USDⓈ-M' else row.get('market')
        meta=data.get('contracts',{}).get(str(market)+'|'+str(row.get('symbol')))
        provider={'CoinGecko':'coingecko','CoinPaprika':'coinpaprika'}.get(row.get('capSource'))
        if not meta or meta.get('verified') is not True:continue
        if not (row.get('capTrusted') is True and row.get('capIdentityVerified') is True
                and row.get('capApplicable') is True and row.get('capMappingRevision')
                and row.get('capIdentityBasis') and provider
                and re.fullmatch('[a-zA-Z0-9_-]+',str(row.get('capId','')))):continue
        prior=meta.get('cap') or {}
        cap=dict(verified=True,provider=provider,id=row['capId'],
            identity_evidence='audited-asset-map:'+row['capMappingRevision']+':'+row['capIdentityBasis'])
        if prior.get('id')==cap['id'] and prior.get('provider','coingecko')==provider and prior.get('plan')=='pro':cap['plan']='pro'
        if prior!=cap:meta['cap']=cap;changed+=1
    data['cap_scan_sha256']=sha
    atomic_json(path,data)
    return dict(updated=changed,scan_sha256=sha)

def _number(value):return type(value) in (int,float) and math.isfinite(value) and value>=0

def local_samples(root,job,registry,clock_ms):
    """Return only the fixed page with original timestamps and traceable source."""
    from b_evidence import ms
    root=Path(root);market,symbol=job['contract'].split('|',1)
    market='USDⓈ-M' if market=='USDT-M' else market
    start=job['next_ms'];end=min(job['target_ms'],start+499*job['period_ms'])
    if job['source']=='cap':
        dbpath=root/'runtime/b-evidence-repair.sqlite3'
        if not dbpath.is_file():return []
        cfg=(registry.get(job['contract']) or {}).get('cap') or {}
        if cfg.get('verified') is not True or not cfg.get('identity_evidence'):return []
        provider={'coingecko':'CoinGecko','coinpaprika':'CoinPaprika'}.get(cfg.get('provider','coingecko'))
        db=sqlite3.connect(dbpath.resolve().as_uri()+'?mode=ro',uri=True,timeout=2)
        try:
            rows=db.execute('SELECT observed_at,digest,payload FROM caps WHERE market=? AND symbol=? AND observed_at BETWEEN ? AND ? ORDER BY observed_at DESC,digest LIMIT 32',(market,symbol,start-H,start)).fetchall()
        finally:db.close()
        accepted=[]
        for observed,digest,payload in rows:
            cap=json.loads(payload)
            if cap.get('capTrusted') is not True or cap.get('capSource')!=provider or cap.get('capId')!=cfg.get('id'):continue
            if cap.get('capIdentityVerified') is not True or not cap.get('capMappingRevision') or not cap.get('capIdentityBasis'):continue
            available=ms(cap.get('capFirstRetrievedAt'));effective=ms(cap.get('capObservedAt'))
            if available is None or available>clock_ms or effective!=observed or not _number(cap.get('capUsd')) or cap['capUsd']<=0:continue
            accepted.append(dict(effective_at_ms=observed,available_at_ms=available,value=cap['capUsd'],unit='USD',trusted=True,
                provider=cfg.get('provider','coingecko'),asset_id=cfg['id'],identity_evidence=cfg['identity_evidence'],
                local_provenance=dict(path='runtime/b-evidence-repair.sqlite3',table='caps',digest=digest)))
        if not accepted:return []
        newest=max(x['effective_at_ms'] for x in accepted)
        matches=[s for s in accepted if s['effective_at_ms']==newest]
        # Equal-time conflicting historical values need explicit revision review.
        if len({s['value'] for s in matches})>1:return []
        return [max(matches,key=lambda s:s['available_at_ms'])]
    if job['source'] not in ('oi','lsr'):return []
    # Inverse OI needs an independently archived contemporaneous conversion.
    if market=='COIN-M' and job['source']=='oi':return []
    rel='runtime/b-source/'+hashlib.sha256((market+'|'+symbol).encode()).hexdigest()+'/structure.json'
    path=root/rel
    if not path.is_file() or path.stat().st_size>16*1024*1024:return []
    raw=path.read_bytes();data=json.loads(raw);digest=hashlib.sha256(raw).hexdigest()
    source=job['source'];field='sumOpenInterestValue' if source=='oi' else 'longShortRatio'
    by={}
    for sample in data.get(source+'_samples',[]):
        t=ms(sample.get('timestamp'));a=ms(sample.get('_revision_retrieved_at',sample.get('_first_retrieved_at')))
        try:value=float(sample.get(field))
        except (TypeError,ValueError):continue
        if t is None or a is None or not start<=t<=end or (t-job['start_ms'])%job['period_ms'] or a>clock_ms or not _number(value) or isinstance(sample.get(field),bool):continue
        if sample.get('symbol') is not None and sample['symbol']!=symbol:continue
        by[t]=dict(effective_at_ms=t,available_at_ms=a,value=value,unit='USD' if source=='oi' else 'ratio',
            local_provenance=dict(path=rel,sha256=digest,source=source))
    # Only commit a continuous prefix; a later point cannot jump an earlier gap.
    result=[]
    while start<=end and start in by:
        result.append(by[start]);start+=job['period_ms']
    return result

def drain_local(root,recovery,registry,*,clock_ms,limit=24,allowed=lambda:None):
    with recovery.transaction() as db:
        after=(db.execute("SELECT value FROM meta WHERE key='collection_local_after'").fetchone() or ('',))[0]
        rows=[dict(r) for r in db.execute("SELECT * FROM tracking_recovery WHERE provider IS NOT NULL AND job_key>? AND source IN ('cap','oi','lsr') AND state NOT IN ('running','archived_awaiting_effect','evaluated','complete','cancelled') ORDER BY job_key LIMIT ?",(after,limit))]
    from tracking.recovery import SourceFailure
    results=[];errors=[]
    for job in rows:
        allowed()
        try:
            samples=local_samples(root,job,registry,clock_ms)
            if samples:results.append(recovery.import_local(job['job_key'],samples,clock_ms=clock_ms))
        except (OSError,ValueError,sqlite3.Error,SourceFailure) as exc:
            errors.append(dict(job_key=job['job_key'],error=type(exc).__name__+':'+str(exc)[:300]))
    with recovery.transaction() as db:
        cursor=rows[-1]['job_key'] if len(rows)==limit else ''
        db.execute("INSERT INTO meta VALUES('collection_local_after',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",(cursor,))
    return dict(inspected=len(rows),archived=len(results),items=results,errors=errors,http_attempts=0)
