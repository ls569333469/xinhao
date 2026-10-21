"""Public Binance adapters with persistent historical evidence and bounded recovery."""
import hashlib,json,time
from pathlib import Path
from b_1h_fetch import fetch_1h_klines,fetch_oi_lsr_1h,_hosts,_request
from durable_io import atomic_json,protection
from market_candle_cache import read_json_maybe_gzip
H=3600000
class PublicAdapters:
    def __init__(self,root,requester=None):self.root=Path(root);self.requester=requester;self.requests=0
    def folder(self,item):
        return self.root/'runtime'/'b-source'/hashlib.sha256((item['market']+'|'+item['symbol']).encode()).hexdigest()
    def klines(self,market,symbol,**kwargs):
        self.requests+=1
        return fetch_1h_klines(market,symbol,requester=self.requester,**kwargs)
    def background(self,item,target):
        p=self.folder(item)/'4h.json'
        old=read_json_maybe_gzip(p) if p.exists() else {'raw':[]}
        if not old['raw'] and item.get('candleArchives'):
            from ema_observations import archive_raw
            old['raw']=archive_raw(self.root,item) or []
        rows={int(r[0]):r for r in old['raw'] if int(r[0])+4*H<=target};latest=max(rows,default=None)
        original=dict(rows);item['_background_revisions']=[]
        desired=(target//(4*H))*4*H-1640*4*H
        gaps=[a for a,b in zip(sorted(rows),sorted(rows)[1:]) if b-a!=4*H]
        needs_prefix=bool(rows and min(rows)>desired and old.get('preheat_complete') is not True)
        # Re-read the final closed bar too: a source correction must be observable.
        windows=[]
        if needs_prefix:windows.append((desired,min(rows)))
        windows.append((min(gaps) if gaps else latest if latest is not None else desired,target))
        for start,stop in windows:
            for _ in range(4):
                if start+4*H>stop:break
                fetched=self.klines(item['market'],item['symbol'],interval='4h',start_time=start,end_time=stop,limit=500)
                closed=[r for r in fetched if len(r)>7 and int(r[6])==int(r[0])+4*H-1 and int(r[0])+4*H<=stop]
                if not closed:break
                rows.update({int(r[0]):r for r in closed});advance=max(int(r[0]) for r in closed)+4*H
                if advance<=start:break
                start=advance
        raw=[rows[k] for k in sorted(rows)]
        item['_background_revisions']=sorted(set(old.get('pending_revision_opens',[]))|{t for t,row in rows.items() if t in original and original[t]!=row})
        from shared_candles import write
        write(self.root,p,dict(market=item['market'],symbol=item['symbol'],interval='4h',raw=raw,retrieved_at=int(time.time()*1000),source='Binance 4h',through=target,preheat_complete=True,pending_revision_opens=item['_background_revisions']))
        return raw
    def evidence(self,item,target):
        p=self.folder(item)/'structure.json';old=json.loads(p.read_text(encoding='utf-8')) if p.exists() else {}
        # Binance statistics only cover a rolling month. Preserve acquired samples
        # locally; never relabel a current sample with an old boundary.
        from source_resume import resume_source
        fetched=dict(errors={},source_cursors={},changed_timestamps=list(old.get('pending_revision_timestamps',[])),oi_value_unit='base_asset' if item['market'] in ('COIN-M','COIN_M') else 'usd')
        for source in ('oi','lsr'):
            key=source+'_samples'
            def read(start,end):
                self.requests+=1
                result=fetch_oi_lsr_1h(item['market'],item['symbol'],pair=item.get('pair'),requester=self.requester,start_time=start,end_time=end,sources=(source,))
                if result['errors'].get(source):raise RuntimeError(result['errors'][source])
                return result[key]
            rows,revisions,cursor=resume_source(old.get(key,[]),target=target,now_ms=int(time.time()*1000),fetch=read,prior=old.get('source_cursors',{}).get(source))
            fetched[key]=rows;fetched[key+'_revisions']=old.get(key+'_revisions',[])+revisions
            previous={int(r['timestamp']):{k:v for k,v in r.items() if not k.startswith('_')} for r in old.get(key,[])}
            fetched['changed_timestamps'].extend(int(r['timestamp']) for r in rows if previous.get(int(r['timestamp']))!={k:v for k,v in r.items() if not k.startswith('_')})
            fetched['source_cursors'][source]=cursor
            if cursor['errors']:fetched['errors'][source]='; '.join(cursor['errors'])
            fetched[source+'_quality']='pass' if rows else 'unknown'
            fetched['oi_hist' if source=='oi' else 'lsr']=rows[-1] if rows else None
        from b_evidence import ms
        from b_repair_store import RepairStore
        caps=list(old.get('cap_samples',[]))+RepairStore(self.root).caps(item['market'],item['symbol'],item);prior_caps={ms(c.get('capObservedAt')):c for c in caps}
        cap={k:v for k,v in item.items() if k.startswith('cap') and not isinstance(v,(dict,list))}
        if cap.get('capTrusted') is True and ms(cap.get('capObservedAt')) is not None:
            caps.append(cap)
        bytime={ms(c.get('capObservedAt')):c for c in caps if ms(c.get('capObservedAt')) is not None}
        fetched['cap_samples']=[bytime[k] for k in sorted(bytime)]
        fetched['changed_cap_timestamps']=sorted(set(old.get('pending_cap_timestamps',[]))|{t for t,c in bytime.items() if prior_caps.get(t)!=c})
        fetched['pending_revision_timestamps']=sorted(set(fetched['changed_timestamps']))
        fetched['pending_cap_timestamps']=fetched['changed_cap_timestamps']
        fetched['retrieved_at']=int(time.time()*1000)
        with protection(self.root):atomic_json(p,fetched)
        return fetched
    def acknowledge(self,item,evidence):
        # Cache/source writes may precede a failed event transaction. Clear dirty
        # keys only AFTER event/cursor/outbox commit; a repeated ack is harmless.
        with protection(self.root):
            p=self.folder(item)/'structure.json'
            if p.exists():
                data=json.loads(p.read_text(encoding='utf8'))
                for key,done in [('pending_revision_timestamps',evidence.get('changed_timestamps',[])),('pending_cap_timestamps',evidence.get('changed_cap_timestamps',[]))]:data[key]=sorted(set(data.get(key,[]))-set(done))
                atomic_json(p,data)
            p=self.folder(item)/'4h.json'
            if p.exists() and item.get('_background_revisions'):
                data=read_json_maybe_gzip(p);data['pending_revision_opens']=sorted(set(data.get('pending_revision_opens',[]))-set(item['_background_revisions']))
                from shared_candles import write
                write(self.root,p,data)
    def reference(self,item,target):
        # Only take the actual OPEN of a new unclosed bar; never use its evolving
        # close/volume for event or confirmation calculations.
        rows=self.klines(item['market'],item['symbol'],start_time=target,end_time=target+H,limit=1)
        for r in rows:
            if int(r[0])==target:return dict(open_time=target,price=float(r[1]),retrieved_at=int(time.time()*1000))
        return None
    def quote(self,item,target):
        host,prefix=_hosts(item['market']);self.requests+=1
        data=_request(self.requester,host+prefix+'ticker/price',{'symbol':item['symbol']})
        if isinstance(data,list):data=next((r for r in data if r.get('symbol')==item['symbol']),{})
        return dict(price=float(data['price']),retrieved_at=int(time.time()*1000),source=host+prefix+'ticker/price')
