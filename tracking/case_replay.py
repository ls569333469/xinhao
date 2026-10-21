"""Offline ORIGINAL producers; no pre-seeded confirmation, request or live root."""
from pathlib import Path
import json
from .tracking_engine import iso

class OriginalEngines:
    def __init__(self,root,case):
        from .ledger import TrackingLedger
        self.root=Path(root);self.case=case
        self.ledger=TrackingLedger(self.root/'cases.sqlite3')
        self.market,self.symbol=case['contract'].split('|',1)
        if self.market=='USDⓈ-M':self.market='USDT-M'
        self.legacy_market='USDⓈ-M' if self.market=='USDT-M' else self.market

    def evaluate(self,inp,boundary,as_of):
        strategy=self.case['strategy'];stage=None
        if strategy=='C':
            from .c_rebound_engine import CReboundEngine
            result=CReboundEngine(self.ledger).consume(market=self.market,symbol=self.symbol,raw_bars=inp['raw_bars'],oi_samples=inp.get('oi_samples',[]),lsr_samples=inp.get('lsr_samples',[]),caps=inp.get('caps',[]),target_close_ms=boundary,clock_iso=iso(as_of),source_version='case:'+str(boundary),historical_import=True)
            if not result.get('ok'):raise ValueError('case_C_engine:'+str(result))
        elif strategy=='B':
            from b_process_order import process_bars_in_order
            from b_evidence import structure_at,background_at,ms
            from b_accumulation_store import BAccumulationStore
            from .b_tracking import BTracker
            evidence=inp.get('evidence') or {};bars=inp.get('bars_asc',[])
            caps=evidence.get('cap_samples',[])
            def structure(t):
                known=[c for c in caps if ms(c.get('capObservedAt')) is not None and ms(c.get('capObservedAt'))<=t]
                cap=max(known,key=lambda c:ms(c['capObservedAt'])) if known else {}
                return structure_at(t,oi_samples=evidence.get('oi_samples'),lsr_samples=evidence.get('lsr_samples'),cap=cap,oi_unit=evidence.get('oi_value_unit','usd'),price=lambda at:next((b['c'] for b in bars if b['open_time']+3600000==at),None))
            process_bars_in_order(self.root,market=self.legacy_market,symbol=self.symbol,bars_asc=bars,bg4h_for=lambda ot:background_at(inp.get('raw4h',[]),ot+3600000),structure_for=structure,target_close_boundary=boundary)
            store=BAccumulationStore(self.root)
            for event in store.list_events(market=self.legacy_market,symbol=self.symbol,full=True,revisions=True):
                prefix=[r.get('snapshot',r) for r in event.get('revisions',[])]+[event]
                for rev in prefix:
                    payload=dict(rev,market=self.legacy_market,symbol=self.symbol,event_id=event['event_id'])
                    cursor=self.ledger.get_cursor('b-source-revision|'+event['event_id'])
                    if cursor and int(cursor)>=payload['event_revision']:continue
                    from .tracking_engine import milliseconds
                    # Original B records its real local computation time. Keep
                    # that receipt; the simulated input as-of fence has already
                    # been applied and must not backdate a new source revision.
                    written=max([as_of]+[milliseconds(payload[k]) for k in ('first_seen_at','last_evaluated_at') if payload.get(k)])
                    out=BTracker(self.ledger).consume_revision(payload,clock_iso=iso(written+1),historical_import=True)
                    if not out.get('ok'):raise ValueError('case_B_handoff:'+str(out))
        elif strategy=='A':
            stage=self._a(inp,boundary,as_of)
        else:raise ValueError('case_strategy')
        events=[]
        for row in self.ledger._read('SELECT r.payload_json FROM event_revisions r JOIN (SELECT event_id,MAX(revision) revision FROM event_revisions GROUP BY event_id) x USING(event_id,revision) ORDER BY r.event_id'):
            value=json.loads(row[0]);events.append({k:value.get(k) for k in ('strategy','event_kind','trigger_bar_close_ms','decision_bar_close_ms','technical_status','qualification_status','missing_data_flags')})
        events.sort(key=lambda e:(e.get('trigger_bar_close_ms') or 0,json.dumps(e,sort_keys=True)))
        sessions=[dict(r) for r in self.ledger._read('SELECT observation_status,end_reason,last_evaluated_bar_close_ms FROM sessions ORDER BY last_evaluated_bar_close_ms,observation_status')]
        return dict(candidate=stage,events=events,sessions=sessions)

    def _a(self,inp,boundary,as_of):
        from scanner import fetch_contract,classify
        from ema_squeeze_store import EmaSqueezeStore
        from .a_tracking import ATracker
        from .c_rebound_rules import sample_at
        raw=inp.get('raw_bars',[]);identity=inp.get('row') or {}
        # Only metadata may come from an archived row. All decision fields,
        # candidate/confirmed arrays and grades are calculated by original code.
        if not raw:raise ValueError('case_A_raw_missing')
        inverse=self.market=='COIN-M';mult=identity.get('contractSize')
        if inverse and (not isinstance(mult,(int,float)) or mult<=0):raise ValueError('case_historical_multiplier_missing')
        klines=[[b['open_ms'],b['o'],b['h'],b['l'],b['c'],b['q']/mult if inverse else 0,b['open_ms']+14400000-1,b['q'],0,0,0,0] for b in raw]
        close=raw[-1]['c']
        oi=sample_at(inp.get('oi_samples',[]),boundary,'value',unit='USD')
        lsr=sample_at(inp.get('lsr_samples',[]),boundary,'value',unit='ratio')
        cap=sample_at([s for s in inp.get('caps',[]) if s.get('trusted')],boundary,'value',unit='USD',lag=3600000)
        def requester(url,args):
            if url.endswith('/klines'):return klines,None
            if url.endswith('/openInterest'):return (dict(openInterest=oi['value']/(mult if inverse else close),time=oi['effective_at_ms']),None) if oi else (None,'case_oi_missing')
            if url.endswith('/globalLongShortAccountRatio'):return ([dict(longShortRatio=lsr['value'],timestamp=lsr['effective_at_ms'])],None) if lsr else (None,'case_lsr_missing')
            raise ValueError('case_unexpected_request')
        meta=dict(symbol=self.symbol,pair=identity.get('pair',self.symbol),baseAsset=identity.get('asset','CASE'),quoteAsset=identity.get('quote','USD' if inverse else 'USDT'),contractSize=mult,onboardDate=identity.get('contractOnboardAt'))
        row=fetch_contract(meta,'https://dapi.binance.com' if inverse else 'https://fapi.binance.com',dict(lastPrice=close),dict(markPrice=close),{},only=('oi','ratio','candles'),requester=requester,count_progress=False,target_close_boundary=boundary)
        row.update(capUsd=cap['value'] if cap else None,capTrusted=bool(cap),capApplicable=True,capObservedAt=cap['effective_at_ms'] if cap else None)
        row['oiCapPct']=oi['value']/cap['value']*100 if oi and cap and cap['value']>0 else None
        row['asOf']=iso(as_of);row['observed_at']=iso(as_of);row['fieldAvailableAt']={k:iso(as_of) for k in row if not k.startswith('_')}
        classify(row)
        stage=EmaSqueezeStore(self.root).evaluate_row(row,now=iso(as_of));row['emaSqueezeStage']=stage
        out=ATracker(self.ledger).drive_snapshot(row=row,raw_bars=raw,clock_iso=iso(as_of),source_version='case:'+str(boundary),available_at=iso(as_of),historical_import=True)
        if not out.get('ok'):raise ValueError('case_A_handoff:'+str(out))
        confirmation=stage.get('crossConfirm') or {}
        return dict(status=stage.get('candidateStatus'),pending_level=(stage.get('pendingCross') or {}).get('level'),confirmation_status=confirmation.get('status'),confirmation_level=confirmation.get('level'),note=stage.get('stageNote'))
