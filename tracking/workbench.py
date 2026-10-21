"""Read-only main-site tracking projection. No writer, scheduler or network imports.

Immutable bounded views keep pagination independent of later ledger commits.
Long reads wait for publication/replay notifications; they never start collection.
"""
import copy
import datetime as dt
import hashlib
import json
import math
import sqlite3
import threading
import time
from collections import OrderedDict
from pathlib import Path
from urllib.parse import urlsplit, parse_qs

from .read_repository import TrackingReadRepository, ReadRepositoryError

VERSION='tracking-workbench-1022-v2'
H4=14400000
_LOCK=threading.RLock()
_VIEWS=OrderedDict()
_CONDITIONS={}
_WATCHERS=threading.BoundedSemaphore(8)
MAX_EVENTS=10000
MAX_BYTES=20*1024*1024

def notify(root):
    with _LOCK:
        condition=_CONDITIONS.get(str(Path(root).resolve()))
    if condition:
        with condition:condition.notify_all()

def finite(value):
    return isinstance(value,(int,float)) and not isinstance(value,bool) and math.isfinite(value)

def canonical_market(m):
    return 'USDT-M' if m=='USDⓈ-M' else m

def digest(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,ensure_ascii=False,separators=(',',':')).encode()).hexdigest()[:32]

def b_generation(root):
    path=Path(root)/'runtime/b-events-v3.sqlite3'
    if not path.exists():return None
    db=sqlite3.connect(path.resolve().as_uri()+'?mode=ro',uri=True,timeout=3)
    try:
        row=db.execute("SELECT value FROM meta WHERE key='generation'").fetchone()
        return row[0] if row else None
    finally:db.close()

def b_history(root):
    """Read current B event facts without evidence blobs or writer initialization."""
    path=Path(root)/'runtime/b-events-v3.sqlite3'
    if not path.exists():return []
    db=sqlite3.connect(path.resolve().as_uri()+'?mode=ro',uri=True,timeout=3)
    try:
        rows=db.execute('SELECT payload FROM events ORDER BY bar_open DESC,event_id LIMIT ?',(MAX_EVENTS+1,)).fetchall()
        if len(rows)>MAX_EVENTS:raise ValueError('B_history_event_bound')
        result=[]
        for row in rows:
            src=json.loads(row[0]);trigger=src.get('trigger_open_time')
            if type(trigger) is not int:continue
            technical=src.get('technical_confirmation')
            confirmation='confirmed' if technical=='pass' else 'confirm_failed' if technical=='fail' else 'pending' if technical=='pending' else 'unknown'
            q=src.get('structure_gate') or 'unknown'
            result.append(dict(event_id=src['event_id'],market=canonical_market(src['market']),symbol=src['symbol'],strategy='B',
                timeframe='1h',trigger_close_ms=trigger+3600000,first_seen_at=src.get('first_seen_at'),
                historical_import=bool(src.get('historical')),rule_version=src.get('rule_version'),
                original_confirmation=dict(technical_status=confirmation),qualification=dict(status=q),
                source_revision=src.get('event_revision'),source='b_event_archive',observation={},session_id=None,
                signal_label='1H 吸筹背离 · 放量记录',technical_grade=None,trigger_price=None,data_quality={}))
        return result
    finally:db.close()

def result_state(event):
    status=event.get('original_confirmation',{}).get('technical_status')
    q=event.get('qualification',{}).get('status')
    if status in ('confirm_failed','invalidated','expired') or q=='fail':return 'fail'
    if status in ('pending','waiting'):return 'pending'
    if status=='confirmed' and q=='pass':return 'confirmed'
    return 'unknown'

def date_bounds(q):
    values=[]
    for key in ('from','to'):
        value=q.get(key,'')
        try:values.append(int(dt.datetime.combine(dt.date.fromisoformat(value),dt.time(),tzinfo=dt.timezone(dt.timedelta(hours=8))).timestamp()*1000) if value else None)
        except (TypeError,ValueError):raise ValueError('DATE_invalid')
    if all(v is not None for v in values) and values[0]>values[1]:raise ValueError('DATE_order_invalid')
    return values[0],values[1]+86400000 if values[1] is not None else None

def stamp(root):
    root=Path(root)
    p=root/'scan.json'
    stat=p.stat() if p.exists() else None
    repo=TrackingReadRepository.for_runtime(root)
    with repo.snapshot():
        values=dict(scan=[stat.st_mtime_ns,stat.st_size] if stat else None,
            tracking=repo.tracking_commit_seq(),recovery=repo.recovery_commit_seq(),b_events=b_generation(root))
    return dict(ok=True,version=digest(values),sources=values,implementation=VERSION)

def queue_status(root):
    p=Path(root)/'runtime/tracking-input.sqlite3'
    if not p.is_file():return dict(pending_rows=0,errors=[])
    db=sqlite3.connect(p.resolve().as_uri()+'?mode=ro',uri=True,timeout=3)
    try:
        return dict(pending_rows=db.execute("SELECT COUNT(*) FROM publication_rows WHERE state<>'done'").fetchone()[0],
            errors=[dict(error=e,n=n) for e,n in db.execute("SELECT error,COUNT(*) FROM publication_rows WHERE state<>'done' AND error IS NOT NULL GROUP BY error LIMIT 5")])
    except sqlite3.Error:return dict(pending_rows=None,errors=[dict(error='queue_status_unavailable')])
    finally:db.close()

def event_dto(summary,record):
    ctx=summary.get('context') or {}
    cross=ctx.get('cross_bar') or {}
    trigger=record.get('trigger_bar_close_ms')
    obs=summary.get('observation') or {}
    event=dict(event_id=summary['event_id'],session_id=summary.get('session_id'),market=summary['market'],symbol=summary['symbol'],
        strategy=summary.get('strategy'),event_kind=record.get('event_kind'),timeframe=record.get('timeframe'),
        trigger_close_ms=trigger,decision_close_ms=summary.get('decision_close_ms'),first_seen_at=record.get('first_seen_at'),
        observation=obs,qualification=summary.get('qualification') or {},original_confirmation=summary.get('original_confirmation') or {},
        data_quality=summary.get('data_quality') or {},historical_import=bool(summary.get('historical_import')),
        rule_version=record.get('rule_version'),trigger_price=None,signal_label=None,technical_grade=None)
    # Label the frozen original profile; never relabel it as a 1021 technical grade.
    if event['strategy']=='A':
        event['signal_label']='4H 原A三线金叉' if record.get('event_kind')=='a_full_cross' else '4H EMA7↑EMA25（原A中观察）'
        if cross.get('open_ms',-H4)+H4==trigger:event['trigger_price']=cross.get('c')
    elif event['strategy']=='B':
        event['signal_label']='1H 吸筹背离 · 技术放量'
    elif event['strategy']=='C':
        event['signal_label']='4H 超卖反弹'
        t0=ctx.get('prepared_t0') or {}
        if t0.get('open_ms',-H4)+H4==trigger:event['trigger_price']=t0.get('c')
    return event

def build(root):
    root=Path(root)
    for attempt in range(3):
        before=stamp(root)
        p=root/'scan.json'
        if p.exists() and p.stat().st_size>80*1024*1024:raise ValueError('SCAN_size_limit')
        scan=json.loads(p.read_text(encoding='utf-8-sig')) if p.exists() else {}
        prices={};scan_rows={}
        for row in scan.get('rows',[]):
            ident=(canonical_market(row.get('market')),row.get('symbol'));scan_rows[ident]=row
            prices[ident]={k:row.get(k) for k in
                ('asset','quote','price','priceChange24Pct','volume24Usd','candleAsOf','asOf','ema4h','closed4h')}
        repo=TrackingReadRepository.for_runtime(root)
        with repo.snapshot():
            # Current summary heads only; no event-by-event history or candle reads.
            rows=repo.read('''SELECT v.summary_json,e.payload_json FROM tracking_summary_versions v
                JOIN event_revisions e ON e.event_id=v.event_id AND e.revision=(SELECT MAX(r.revision) FROM event_revisions r WHERE r.event_id=v.event_id)
                WHERE v.valid_to_seq IS NULL ORDER BY v.market,v.symbol,v.decision_close_ms DESC,v.event_id LIMIT ?''',(MAX_EVENTS+1,))
            if len(rows)>MAX_EVENTS:raise ValueError('TRACKING_projection_event_bound')
            events=[event_dto(json.loads(r[0]),json.loads(r[1])) for r in rows]
        # Enrich only matching historical technical events, never today's grade.
        for event in events:
            if event['strategy']!='A':continue
            row=scan_rows.get((event['market'],event['symbol']))
            if row:
                observed=row.get('emaObservation') or {}
                matched=[]
                for old in observed.get('recent_long_cross_events') or []:
                    bar=old.get('bar_time',old.get('bar_close_ms'))
                    if bar is not None and bar+1==event['trigger_close_ms']:
                        matched.append(old)
                if matched:
                    old=matched[0];ema=old.get('ema');ratio=old.get('volume_ratio');kinds={v.get('kind') for v in matched}
                    if isinstance(ema,list) and len(ema)==3 and all(finite(v) and v>0 for v in ema):
                        stack=ema[0]>ema[1]>ema[2]
                        event['technical_grade']=('strong' if finite(ratio) and ratio>=2 else 'medium') if stack else ('medium' if kinds&{'ema7_cross_99','ema25_cross_99'} or ema[0]>ema[2] else 'weak')
                        names={'ema7_cross_25':'EMA7↑EMA25','ema7_cross_99':'EMA7↑EMA99','ema25_cross_99':'EMA25↑EMA99'}
                        event['signal_label']='4H '+' ＋ '.join(names[k] for k in names if k in kinds)
                        event['technical_grade_version']='ema-technical-grade-v1'
        # History includes native B records not yet consumed by the tracking inbox.
        # Join only on exact event identity; observation remains ledger-owned.
        by_id={e['event_id']:copy.deepcopy(e) for e in events}
        for archived in b_history(root):
            previous=by_id.get(archived['event_id'])
            if previous:
                archived['observation']=previous['observation']
                archived['session_id']=previous.get('session_id')
            by_id[archived['event_id']]=archived
        history=list(by_id.values())
        if len(history)>MAX_EVENTS:raise ValueError('HISTORY_projection_event_bound')
        for event in history:event['result_state']=result_state(event)
        after=stamp(root)
        if before['version']!=after['version']:continue
        groups={}
        for event in events:
            ident=(event['market'],event['symbol'])
            group=groups.setdefault(ident,dict(market=ident[0],symbol=ident[1],events=[],quote_data=prices.get(ident,{})))
            group['events'].append(event)
        data=dict(ok=True,version=before['version'],sources=before['sources'],rows=list(groups.values()),history=history,
            scan_as_of=scan.get('available_at') or scan.get('finishedAt'),queue=queue_status(root),
            coverage_note='既有ABC观察；新增技术交叉未匹配观察规则的，不冒充持续追踪已覆盖。')
        size=len(json.dumps(data,ensure_ascii=False).encode())
        if size>MAX_BYTES:raise ValueError('TRACKING_projection_byte_bound')
        token='tw_'+digest([str(root.resolve()),before['version']])
        data['view']=token
        with _LOCK:
            _VIEWS[token]=(str(root.resolve()),time.monotonic(),data,size)
            while len(_VIEWS)>4 or sum(v[3] for v in _VIEWS.values())>MAX_BYTES:
                _VIEWS.popitem(last=False)
        return data
    raise ReadRepositoryError('TRACKING_sources_changing',retryable=True)

def view(root,token=None):
    key=str(Path(root).resolve())
    with _LOCK:
        if token:
            cached=_VIEWS.get(token)
            if not cached or cached[0]!=key or time.monotonic()-cached[1]>600:
                raise ReadRepositoryError('TRACKING_view_expired')
            return cached[2]
        current=stamp(root)['version']
        for cached in reversed(list(_VIEWS.values())):
            if cached[0]==key and cached[2]['version']==current and time.monotonic()-cached[1]<600:return cached[2]
    return build(root)

def page(root,q):
    limit=int(q.get('limit','50'));offset=int(q.get('offset','0'))
    if not 1<=limit<=100 or not 0<=offset<=MAX_EVENTS:raise ValueError('PAGE_invalid')
    if q.get('tab','tracking')=='history':return history_page(root,q)
    if q.get('tab','tracking')!='tracking':raise ValueError('TAB_invalid')
    status=q.get('status','active');strategy=q.get('strategy','all');market=q.get('market','all');search=q.get('search','').strip().upper()
    if status not in ('active','paused','ended','all') or strategy not in ('all','A','B','C') or market not in ('all','USDT-M','COIN-M'):raise ValueError('FILTER_invalid')
    if len(search)>64:raise ValueError('SEARCH_invalid')
    data=view(root,q.get('view'));result=[];start,end=date_bounds(q)
    for group in data['rows']:
        if market!='all' and market!=group['market']:continue
        if search and search not in group['symbol'] and search not in (group['quote_data'].get('asset') or '').upper():continue
        events=[e for e in group['events'] if (strategy=='all' or e['strategy']==strategy) and (start is None or (e['trigger_close_ms'] or 0)>=start) and (end is None or (e['trigger_close_ms'] or 0)<end)]
        observing=[e for e in events if e['observation'].get('status') in ('observing','paused_awaiting_data')]
        paused=[e for e in observing if e['observation'].get('status')=='paused_awaiting_data']
        ended=[e for e in events if e['observation'].get('status')=='ended']
        if not events or status=='active' and not observing or status=='paused' and not paused or status=='ended' and (observing or not ended):continue
        represented=max(observing or ended or events,key=lambda e:(e['trigger_close_ms'] or 0,e['event_id']))
        price=group['quote_data'].get('price');base=represented['trigger_price']
        result.append(dict(market=group['market'],symbol=group['symbol'],quote_data=group['quote_data'],representative=represented,
            active_count=len(observing),paused_count=len(paused),ended_count=len(ended),event_count=len(events),
            return_pct=(price/base-1)*100 if represented['observation'].get('status')!='ended' and finite(price) and finite(base) and base>0 else None))
    sort=q.get('sort','trigger');descending=q.get('direction','desc')=='desc'
    if sort not in ('trigger','symbol'):raise ValueError('SORT_invalid')
    result.sort(key=lambda g:(g['representative']['trigger_close_ms'] or 0,g['market'],g['symbol']) if sort=='trigger' else (g['symbol'],g['market']),reverse=descending)
    rows=result[offset:offset+limit]
    return {k:v for k,v in data.items() if k not in ('rows','history')}|dict(tab='tracking',rows=rows,total=len(result),next_offset=offset+limit if offset+limit<len(result) else None,
        query={k:q.get(k,'') for k in ('search','status','strategy','market','sort','direction')})

def history_page(root,q):
    limit=int(q.get('limit','50'));offset=int(q.get('offset','0'))
    if not 1<=limit<=100 or not 0<=offset<=MAX_EVENTS:raise ValueError('PAGE_invalid')
    strategy=q.get('strategy','all');market=q.get('market','all');status=q.get('status','all');search=q.get('search','').strip().upper()
    if strategy not in ('all','A','B','C') or market not in ('all','USDT-M','COIN-M') or status not in ('all','confirmed','pending','fail','unknown'):raise ValueError('FILTER_invalid')
    if len(search)>64:raise ValueError('SEARCH_invalid')
    start,end=date_bounds(q);data=view(root,q.get('view'));result=[]
    for e in data['history']:
        if strategy!='all' and strategy!=e['strategy'] or market!='all' and market!=e['market']:continue
        if search and search not in e['symbol']:continue
        if status!='all' and status!=e['result_state']:continue
        t=e.get('trigger_close_ms') or 0
        if start is not None and t<start or end is not None and t>=end:continue
        result.append(e)
    sort=q.get('sort','trigger')
    if sort not in ('trigger','symbol'):raise ValueError('SORT_invalid')
    result.sort(key=lambda e:(e['trigger_close_ms'] or 0,e['market'],e['symbol'],e['event_id']) if sort=='trigger' else (e['symbol'],e['market'],e['trigger_close_ms'] or 0),reverse=q.get('direction','desc')=='desc')
    return {k:v for k,v in data.items() if k not in ('rows','history')}|dict(tab='history',rows=result[offset:offset+limit],total=len(result),contract_count=len({(e['market'],e['symbol']) for e in result}),next_offset=offset+limit if offset+limit<len(result) else None)

def detail(root,q):
    data=view(root,q.get('view'));eid=q.get('event_id');market=q.get('market');symbol=q.get('symbol')
    events=[e for e in data['history'] if e['market']==market and e['symbol']==symbol]
    if not events:return dict(ok=False,errors=[dict(code='CONTRACT_not_found')]),404
    limit=int(q.get('limit','20'));offset=int(q.get('offset','0'))
    if not 1<=limit<=100 or not 0<=offset<=MAX_EVENTS:raise ValueError('PAGE_invalid')
    events=sorted(events,key=lambda e:(e['trigger_close_ms'] or 0,e['event_id']),reverse=True)
    selected=next((e for e in events if e['event_id']==eid),events[0] if events else None)
    if eid and (not selected or selected['event_id']!=eid):return dict(ok=False,errors=[dict(code='EVENT_not_found')]),404
    evidence={}
    if selected:
        repo=TrackingReadRepository.for_runtime(root)
        with repo.snapshot():
            # Old immutable list may outlive later commits. Detail requires a current
            # view, rather than combining frozen headers with new timeline entries.
            if stamp(root)['version']!=data['version']:raise ReadRepositoryError('TRACKING_detail_changed')
            bound=data['sources']['tracking']
            evidence['transitions']=[json.loads(r[0]) for r in repo.read('SELECT payload_json FROM tracking_effective_members WHERE event_id=? AND valid_from_seq<=? AND (valid_to_seq IS NULL OR valid_to_seq>?) ORDER BY page_seq DESC LIMIT 101',(selected['event_id'],bound,bound))]
            evidence['has_more_transitions']=len(evidence['transitions'])>100
            evidence['transitions']=evidence['transitions'][:100]
            evidence['gaps']=[dict(r) for r in repo.read('SELECT scope,status,detail_json FROM gaps WHERE session_id=? ORDER BY gap_id LIMIT 101',(selected.get('session_id'),))]
            if stamp(root)['version']!=data['version']:raise ReadRepositoryError('TRACKING_detail_changed')
    return dict(ok=True,view=data['view'],version=data['version'],market=market,symbol=symbol,selected=selected,
        events=events[offset:offset+limit],event_count=len(events),next_offset=offset+limit if offset+limit<len(events) else None,**evidence),200

def route(root,path):
    parsed=urlsplit(path);q={k:v[-1] for k,v in parse_qs(parsed.query).items()}
    try:
        action=parsed.path.removeprefix('/api/tracking/workbench/')
        if action=='status':return stamp(root),200
        if action=='list':return page(root,q),200
        if action=='detail':return detail(root,q)
        if action=='watch':
            if not _WATCHERS.acquire(blocking=False):return dict(ok=False,errors=[dict(code='WATCH_busy')]),429
            try:
                key=str(Path(root).resolve())
                with _LOCK:condition=_CONDITIONS.setdefault(key,threading.Condition())
                with condition:
                    current=stamp(root)
                    if current['version']==q.get('version'):
                        condition.wait(timeout=50)
                        current=stamp(root)
                return current|dict(changed=current['version']!=q.get('version')),200
            finally:_WATCHERS.release()
        return dict(ok=False,errors=[dict(code='NOT_FOUND')]),404
    except ReadRepositoryError as exc:
        code=410 if exc.code=='TRACKING_view_expired' else 409
        return dict(ok=False,errors=[exc.as_dict()]),code
    except (ValueError,TypeError) as exc:return dict(ok=False,errors=[dict(code=str(exc)[:120])]),400
    except (OSError,sqlite3.Error) as exc:return dict(ok=False,errors=[dict(code='TRACKING_read_unavailable',detail=type(exc).__name__)]),503
