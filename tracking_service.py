"""ABC integration. Disabled by default; no scheduler/network/AI/orders.

Only already-published source data may enter tracking. GET uses a separate
mode=ro repository. Explicit worker calls have fixed budgets and durable ACKs.
"""
from pathlib import Path
import hashlib,json,sqlite3,threading,time
from datetime import datetime,timezone

VERSION='codex-abc-tracking-20261019-auto-recovery-v1-r4'
_LOCK=threading.RLock()
_ROUTERS={}

def _notify_workbench(root):
    # Presentation-only notification; never changes a business commit outcome.
    from tracking.workbench import notify
    notify(root)

def now():return datetime.now(timezone.utc).isoformat(timespec='milliseconds')
def enabled(root):
    p=Path(root)/'tracking-control.json'
    if not p.is_file():return False
    if p.stat().st_size>16384:raise ValueError('tracking_control_size')
    obj=json.loads(p.read_text(encoding='utf-8-sig'))
    return obj.get('enabled') is True and obj.get('profile_version')=='tracking-observation-v1.1.0'

def status(root):
    result=dict(version=VERSION,implemented=['A','B','C'],enabled=enabled(root),
        ledger_initialized=(Path(root)/'runtime/signal-tracking.sqlite3').is_file(),
        scheduler_created='bounded_runtime_when_explicitly_enabled',network_acquisition='explicit_grant_only',production_validated=False)
    from tracking.runtime import config as runtime_config
    result['runtime_control']=runtime_config(root)
    from tracking.read_repository import TrackingReadRepository,ReadRepositoryError
    try:
        repo=TrackingReadRepository.for_runtime(root)
        with repo.snapshot():
            control=repo.read_one("SELECT value FROM recovery_controls WHERE key='enabled'") if repo.meta('recovery_schema') else None
            last=repo.meta('collection_last_recovery')
            result['collection_recovery']=json.loads(last) if last else None
            result['source_recovery']=dict(enabled=bool(control and control[0]=='true'),schema=repo.meta('recovery_schema'),commit_seq=repo.recovery_commit_seq(),requires_explicit_grant=True,implicit_worker=False)
    except ReadRepositoryError:
        result['source_recovery']=dict(enabled=False,not_initialized=True,requires_explicit_grant=True,implicit_worker=False)
    p=Path(root)/'runtime/tracking-input.sqlite3'
    if p.is_file():
        db=sqlite3.connect(p.resolve().as_uri()+'?mode=ro',uri=True,timeout=3)
        try:
            exists=db.execute("SELECT 1 FROM sqlite_master WHERE name='publication_rows'").fetchone()
            if exists:
                result['publication_queue']=dict(pending_rows=db.execute("SELECT COUNT(*) FROM publication_rows WHERE state<>'done'").fetchone()[0],
                    errors=[dict(source_key=k,row_index=i,error=e[:2000],next_retry=t) for k,i,e,t in db.execute("SELECT p.source_key,r.row_index,r.error,r.next_retry FROM publication_rows r JOIN publications p ON p.seq=r.publication_seq WHERE r.error IS NOT NULL ORDER BY p.seq,r.row_index LIMIT 20")])
        except sqlite3.Error as exc:result['queue_read_error']=str(exc)
        finally:db.close()
    p=Path(root)/'runtime/tracking-publication-source.sqlite3'
    if p.is_file():
        db=sqlite3.connect(p.resolve().as_uri()+'?mode=ro',uri=True,timeout=3)
        try:
            result['source_handoff']=dict(unacked=db.execute('SELECT COUNT(*) FROM source_outbox WHERE acked_at IS NULL').fetchone()[0],errors=[dict(source_key=k,error=e[:2000]) for k,e in db.execute('SELECT source_key,error FROM source_outbox WHERE error IS NOT NULL ORDER BY seq LIMIT 20')])
        except sqlite3.Error as exc:result['source_read_error']=str(exc)
        finally:db.close()
    return result


def writer(root):
    from tracking.ledger import TrackingLedger
    if not enabled(root):raise ValueError('tracking_disabled')
    return TrackingLedger.for_runtime(root)

def _input_db(root):
    p=Path(root)/'runtime/tracking-input.sqlite3';p.parent.mkdir(parents=True,exist_ok=True)
    db=sqlite3.connect(p,timeout=10);db.row_factory=sqlite3.Row
    db.execute('CREATE TABLE IF NOT EXISTS publications(seq INTEGER PRIMARY KEY AUTOINCREMENT,source_key TEXT UNIQUE,rel TEXT,sha TEXT,next_row INTEGER DEFAULT 0,continuation TEXT,acked_at TEXT,error TEXT,next_retry REAL DEFAULT 0)')
    db.execute("CREATE TABLE IF NOT EXISTS publication_rows(publication_seq INTEGER NOT NULL,row_index INTEGER NOT NULL,contract TEXT NOT NULL,state TEXT NOT NULL DEFAULT 'pending',continuation TEXT,error TEXT,next_retry REAL DEFAULT 0,attempt_count INTEGER DEFAULT 0,PRIMARY KEY(publication_seq,row_index))")
    db.execute('CREATE INDEX IF NOT EXISTS publication_rows_contract ON publication_rows(contract,publication_seq,row_index,state)')
    db.commit();return db

def _source_db(root):
    p=Path(root)/'runtime/tracking-publication-source.sqlite3'
    p.parent.mkdir(parents=True,exist_ok=True)
    db=sqlite3.connect(p,timeout=10);db.row_factory=sqlite3.Row
    db.execute('CREATE TABLE IF NOT EXISTS source_outbox(seq INTEGER PRIMARY KEY AUTOINCREMENT,source_key TEXT UNIQUE NOT NULL,scan_id TEXT NOT NULL,revision INTEGER NOT NULL,archive TEXT NOT NULL,sha TEXT NOT NULL,acked_at TEXT,error TEXT,next_retry REAL DEFAULT 0)')
    db.commit();return db


def prepare_publication(root,result,payload):
    """Durable producer intent BEFORE immutable/latest files can become visible.

    A prepared intent is not a published scan. Resume validates the source's
    committed status and exact immutable hash before committing the local inbox.
    """
    if not enabled(root):return
    key=result['scan_id']+':r'+str(result['revision']);sha=hashlib.sha256(payload).hexdigest()
    with _LOCK:
        db=_source_db(root)
        try:
            old=db.execute('SELECT sha FROM source_outbox WHERE source_key=?',(key,)).fetchone()
            if old and old['sha']!=sha:raise ValueError('source_publication_intent_hash_conflict')
            db.execute('INSERT OR IGNORE INTO source_outbox(source_key,scan_id,revision,archive,sha) VALUES(?,?,?,?,?)',(key,result['scan_id'],result['revision'],result['archive'],sha));db.commit()
        finally:db.close()


def resume_publications(root,*,limit=20):
    """Cross-store ACK: source committed -> tracking inbox committed -> source ACK.

    No history-directory walk, acquisition, UI mutation or timetable is created.
    Merely prepared/failed scans remain unconsumed; errors are visible per item.
    """
    if not enabled(root):return dict(ok=True,count=0,disabled=True)
    if type(limit) is not int or not 1<=limit<=100:raise ValueError('source_resume_limit')
    p=Path(root)/'runtime/tracking-publication-source.sqlite3'
    if not p.is_file():return dict(ok=True,count=0)
    count=0;errors=[]
    with _LOCK:
        db=_source_db(root)
        try:
            rows=db.execute('SELECT * FROM source_outbox WHERE acked_at IS NULL AND next_retry<=? ORDER BY seq LIMIT ?',(time.time(),limit)).fetchall()
            for r in rows:
                try:
                    archive=(Path(root)/r['archive']).resolve()
                    if archive.parent!=Path(root).resolve()/'scan-history' or archive.name!=r['scan_id']:raise ValueError('source_archive_path')
                    status_path=archive/'status.json'
                    if not status_path.is_file():
                        db.execute('UPDATE source_outbox SET next_retry=? WHERE seq=?',(time.time()+300,r['seq']));db.commit();continue
                    status=json.loads(status_path.read_bytes())
                    if status.get('status') not in ('success','partial_success') or status.get('revision',0)<r['revision']:
                        db.execute('UPDATE source_outbox SET next_retry=? WHERE seq=?',(time.time()+300,r['seq']));db.commit();continue
                    immutable=archive/('revision-%04d.json'%r['revision'])
                    if immutable.stat().st_size>64*1024*1024:raise ValueError('source_publication_size')
                    raw=immutable.read_bytes()
                    if hashlib.sha256(raw).hexdigest()!=r['sha']:raise ValueError('source_publication_hash_drift')
                    result=json.loads(raw)
                    if result.get('scan_id')!=r['scan_id'] or result.get('revision')!=r['revision']:raise ValueError('source_publication_identity')
                    on_publication(root,result)
                    db.execute('UPDATE source_outbox SET acked_at=?,error=NULL WHERE seq=?',(now(),r['seq']));db.commit();count+=1
                except Exception as exc:
                    db.execute('UPDATE source_outbox SET error=?,next_retry=? WHERE seq=?',(str(exc),time.time()+300,r['seq']));db.commit()
                    errors.append(dict(source_key=r['source_key'],error=str(exc)))
        finally:db.close()
    return dict(ok=not errors,count=count,errors=errors)


def on_publication(root,result):
    if not enabled(root):return dict(ok=True,disabled=True)
    root=Path(root).resolve()
    rel=str(Path(result['archive'])/('revision-%04d.json'%result['revision']))
    p=(root/rel).resolve()
    if not p.is_relative_to(root/'scan-history'):raise ValueError('publication_path')
    raw=p.read_bytes();sha=hashlib.sha256(raw).hexdigest()
    key=result['scan_id']+':r'+str(result['revision'])
    with _LOCK:
        db=_input_db(root)
        try:
            old=db.execute('SELECT sha FROM publications WHERE source_key=?',(key,)).fetchone()
            if old and old[0]!=sha:raise ValueError('publication_version_content_conflict')
            db.execute('INSERT OR IGNORE INTO publications(source_key,rel,sha) VALUES(?,?,?)',(key,rel,sha))
            seq=db.execute('SELECT seq FROM publications WHERE source_key=?',(key,)).fetchone()[0]
            _register_rows(db,seq,json.loads(raw)['rows']);db.commit()
        finally:db.close()
    return dict(ok=True,queued=True,source_key=key)

def capture_error(root,error):
    # Does not relabel the original scan success; independently exposes tracking failure.
    from durable_io import atomic_json
    atomic_json(Path(root)/'runtime/tracking-last-error.json',dict(at=now(),error=str(error),tracking_only=True))

def publication_hook(root,result):
    try:
        result=on_publication(root,result)
        resume_publications(root)
        if result.get('queued'):kick(root)
        return result
    except Exception as exc:
        capture_error(root,exc)
        return dict(ok=False,error=str(exc),tracking_only=True)

def _known_time(value):
    from b_evidence import ms
    return value if type(value) is int else ms(value)

def source_input(root,row,target):
    """Bounded, local cached data only. Unknown availability never becomes current."""
    from ema_observations import archive_raw
    from market_candle_cache import read_json_maybe_gzip
    from tracking.tracking_engine import H4
    raw=archive_raw(root,row)
    if not raw or len(raw)>1499:raise ValueError('published_raw_missing_or_unbounded')
    coin=row['market']=='COIN-M';mult=row.get('contractSize')
    if coin and (not isinstance(mult,(int,float)) or isinstance(mult,bool) or mult<=0):raise ValueError('coin_contract_multiplier_unknown')
    normalized=[dict(open_ms=int(k[0]),o=float(k[1]),h=float(k[2]),l=float(k[3]),c=float(k[4]),q=float(k[5])*mult if coin else float(k[7]),q_unit='USD nominal quote') for k in raw if int(k[6])==int(k[0])+H4-1 and int(k[0])+H4<=target]
    if len(normalized)!=len(raw):raise ValueError('published_raw_not_closed')
    folder=Path(root)/'runtime/b-source'/hashlib.sha256((row['market']+'|'+row['symbol']).encode()).hexdigest()
    p=folder/'structure.json';cache={}
    if p.exists():
        if p.stat().st_size>16*1024*1024:raise ValueError('source_cache_page_required')
        cache=json.loads(p.read_bytes())
    start=min(b['open_ms'] for b in normalized)
    def samples(role,field,unit):
        result=[]
        for s in cache.get(role+'_samples',[]):
            effective=_known_time(s.get('timestamp'));available=_known_time(s.get('_revision_retrieved_at',s.get('_first_retrieved_at')))
            if effective is None or not start<=effective<=target or available is None:continue
            # COIN-M OI native unit is NOT USD. Missing aligned price is explicit unknown.
            actual_unit='base_asset' if role=='oi' and coin else unit
            try:value=float(s[field])
            except (KeyError,TypeError,ValueError):continue
            result.append(dict(effective_at_ms=effective,available_at_ms=available,value=value,unit=actual_unit))
        if len(result)>6000:raise ValueError('source_sample_page_required')
        return result
    oi=samples('oi','sumOpenInterestValue','USD');lsr=samples('lsr','longShortRatio','ratio')
    for field,timefield,unit,dest in [('oiUsd','oiAsOf','USD',oi),('accountRatio','accountAsOf','ratio',lsr)]:
        effective=_known_time(row.get(timefield));available=_known_time((row.get('fieldAvailableAt') or {}).get(field))
        if effective is not None and available is not None and isinstance(row.get(field),(int,float)):
            dest.append(dict(effective_at_ms=effective,available_at_ms=available,value=row[field],unit=unit))
    caps=[]
    for c in cache.get('cap_samples',[])+[row]:
        effective=_known_time(c.get('capObservedAt'));available=_known_time(c.get('capFirstRetrievedAt'))
        if available is None:available=_known_time((row.get('fieldAvailableAt') or {}).get('capUsd')) if c is row else None
        if effective is None or available is None or not start<=effective<=target:continue
        try:value=float(c['capUsd'])
        except (KeyError,TypeError,ValueError):continue
        caps.append(dict(effective_at_ms=effective,available_at_ms=available,unit='USD',value=value,trusted=c.get('capTrusted') is True))
    if len(caps)>6000:raise ValueError('cap_page_required')
    # Recovery workers only archive evidence. This normal evaluation path later
    # consumes it; downloading never marks a strategy root evaluated.
    from tracking.recovery import recovered_inputs
    # Bind normal consumption to the same verified identity generation as the
    # acquisition bridge. Never mix old cap mappings/units with corrected ones.
    from tracking.runtime import registry
    from tracking.recovery_bridge import acquisition_quality
    from tracking.live_source import VERSION as recovery_adapter
    ledger=writer(root);market='USDT-M' if row['market']=='USDⓈ-M' else row['market']
    meta=registry(root).get(market+'|'+row['symbol']) or {}
    anchors=ledger._read('SELECT job_key FROM tracking_recovery WHERE contract=? AND quality_version=? AND adapter_version=? ORDER BY job_key LIMIT 1',
        (market+'|'+row['symbol'],acquisition_quality(meta),recovery_adapter)) if meta.get('verified') is True and ledger._read("SELECT 1 FROM sqlite_master WHERE type='table' AND name='tracking_recovery'") else []
    recovered=recovered_inputs(ledger,market=market,symbol=row['symbol'],start_ms=start,target_ms=target,
        acquisition_job_key=anchors[0]['job_key']) if anchors else {}
    for role,dest in [('oi',oi),('lsr',lsr),('cap',caps)]:
        if role in recovered:
            by={s['effective_at_ms']:s for s in dest}
            by.update({s['effective_at_ms']:s for s in recovered[role]})
            dest[:]=[by[t] for t in sorted(by)]
    if recovered.get('candles_4h'):
        by={b['open_ms']:b for b in normalized}
        for sample in recovered['candles_4h']:
            bar=sample.get('bar')
            if not isinstance(bar,dict) or sample['effective_at_ms']!=bar.get('open_ms',-H4)+H4:raise ValueError('recovered_candle_boundary')
            if sample['available_at_ms']<=int(time.time()*1000):by[bar['open_ms']]=bar
        normalized=[by[t] for t in sorted(by)]
        if len(normalized)>1499:raise ValueError('recovered_candle_page_required')
    return dict(raw_bars=normalized,oi_samples=oi,lsr_samples=lsr,caps=caps,_source_revised=bool(cache.get('pending_revision_timestamps') or cache.get('pending_cap_timestamps')))

def _register_rows(db,seq,rows):
    if not isinstance(rows,list) or len(rows)>1000:raise ValueError('publication_row_bound')
    for i,row in enumerate(rows):
        contract=('USDT-M' if row['market']=='USDⓈ-M' else row['market'])+'|'+row['symbol']
        db.execute('INSERT OR IGNORE INTO publication_rows(publication_seq,row_index,contract) VALUES(?,?,?)',(seq,i,contract))

def _publication_row(root,ledger,row,publication,version,continuation):
    from tracking.a_tracking import ATracker
    from tracking.c_rebound_engine import CReboundEngine
    from tracking.c_rebound_rules import prepare_bars
    from tracking.observer import observe_contract
    from tracking.tracking_engine import H4,milliseconds
    target=row.get('candleAsOf',-1)+1
    if target%H4:raise ValueError('publication_target')
    inp=source_input(root,row,target);source_revised=inp.pop('_source_revised',False);clock=now()
    from tracking.case_library import capture_publication_safe
    capture_publication_safe(root,row=row,inputs=inp,publication=publication,clock_iso=clock)
    from tracking.sample_capture import capture
    capture(root,row=row,raw_bars=inp['raw_bars'],publication=dict(source_key=version,
        available_at=publication['available_at'],revision=publication['revision']),clock_iso=clock)
    historical=milliseconds(clock)-milliseconds(publication['available_at'])>300000 or milliseconds(clock)-target>H4 or publication['revision']>1
    if not continuation.get('a_done'):
        a=ATracker(ledger).drive_snapshot(row=row,raw_bars=inp['raw_bars'],clock_iso=clock,source_version=version,
            available_at=publication['available_at'],historical_import=historical,after_event_id=continuation.get('a',''))
        if not a['ok']:raise ValueError('A_tracking_commit_failed:'+str(a))
        if a.get('continuation'):return dict(continuation,a=a['continuation'])
        continuation['a_done']=True
    cmarket='USDT-M' if row['market'] in ('USDⓈ-M','USDT-M') else row['market']
    if not continuation.get('c_done'):
        source_revised=source_revised or requires_c_replay(ledger,cmarket,row['symbol'],inp)
        raw=ledger.get_cursor('c-controller|'+cmarket+'|'+row['symbol']+'|c-1015-v1.1')
        state=json.loads(raw) if raw else {};last=state.get('last_seen_close_ms')
        first=last+H4 if last is not None and last<target else target
        cnext=continuation.get('c_next',first);stop=min(target,cnext+15*H4)
        for boundary in range(cnext,stop+1,H4):
            page=dict(inp,raw_bars=[b for b in inp['raw_bars'] if b['open_ms']+H4<=boundary])
            for role in ('oi_samples','lsr_samples','caps'):page[role]=[s for s in inp[role] if s['effective_at_ms']<=boundary]
            c=CReboundEngine(ledger).consume(market=cmarket,symbol=row['symbol'],**page,target_close_ms=boundary,clock_iso=clock,source_version=version,
                historical_import=historical or boundary<target,source_revision=(publication['revision']>1 or source_revised) and boundary==cnext)
            if not c['ok']:raise ValueError('C_tracking_commit_failed:'+str(c))
        if stop<target:return dict(continuation,c_next=stop+H4)
        continuation['c_done']=True
    obs=observe_contract(ledger,market=cmarket,symbol=row['symbol'],bars=prepare_bars(inp['raw_bars'],target_close_ms=target),target_close_ms=target,clock_iso=clock,source_version=version,after_session_id=continuation.get('observer',''))
    if not obs['ok']:raise ValueError('observation_commit_failed:'+str(obs))
    return dict(continuation,observer=obs['continuation']) if obs.get('continuation') else None

def drain_publications(root,*,row_budget=10):
    if not enabled(root):return dict(ok=True,disabled=True)
    if type(row_budget) is not int or not 1<=row_budget<=100:raise ValueError('row_budget')
    ledger=writer(root);count=0;errors=[];touched=set()
    with _LOCK:
        db=_input_db(root)
        try:
            for _ in range(row_budget):
                item=db.execute("SELECT p.*,r.row_index,r.continuation AS row_continuation FROM publication_rows r JOIN publications p ON p.seq=r.publication_seq WHERE p.acked_at IS NULL AND r.state<>'done' AND r.next_retry<=? AND NOT EXISTS(SELECT 1 FROM publication_rows old WHERE old.contract=r.contract AND old.state<>'done' AND old.publication_seq<r.publication_seq) ORDER BY p.seq,r.row_index LIMIT 1",(time.time(),)).fetchone()
                if not item:break
                touched.add(item['seq']);continuation=json.loads(item['row_continuation'] or '{}')
                try:
                    p=(Path(root)/item['rel']).resolve()
                    if not p.is_relative_to(Path(root).resolve()/'scan-history') or p.stat().st_size>64*1024*1024:raise ValueError('publication_path_or_size')
                    data=p.read_bytes()
                    if hashlib.sha256(data).hexdigest()!=item['sha']:raise ValueError('publication_source_hash_drift')
                    publication=json.loads(data);row=publication['rows'][item['row_index']]
                    continuation=_publication_row(root,ledger,row,publication,item['source_key'],continuation)
                    db.execute("UPDATE publication_rows SET state=?,continuation=?,error=NULL,next_retry=0,attempt_count=attempt_count+1 WHERE publication_seq=? AND row_index=?",('pending' if continuation else 'done',json.dumps(continuation),item['seq'],item['row_index']))
                except Exception as exc:
                    error=dict(source_key=item['source_key'],row_index=item['row_index'],error=str(exc));errors.append(error)
                    db.execute('UPDATE publication_rows SET error=?,next_retry=?,attempt_count=attempt_count+1 WHERE publication_seq=? AND row_index=?',(str(exc),time.time()+300,item['seq'],item['row_index']))
                    capture_error(root,error)
                db.commit();count+=1
            for seq in touched:
                pending=db.execute("SELECT MIN(row_index),MIN(next_retry),COUNT(*) FROM publication_rows WHERE publication_seq=? AND state<>'done'",(seq,)).fetchone()
                if not pending[2]:
                    n=db.execute('SELECT COUNT(*) FROM publication_rows WHERE publication_seq=?',(seq,)).fetchone()[0]
                    db.execute('UPDATE publications SET acked_at=?,next_row=?,error=NULL WHERE seq=?',(now(),n,seq))
                else:db.execute('UPDATE publications SET next_row=?,error=? WHERE seq=?',(pending[0],json.dumps(errors) if errors else None,seq))
            # Empty publications are valid and cannot leave an eternal pending job.
            db.execute('UPDATE publications SET acked_at=? WHERE acked_at IS NULL AND NOT EXISTS(SELECT 1 FROM publication_rows r WHERE r.publication_seq=publications.seq)',(now(),))
            db.commit()
            pending=db.execute("SELECT COUNT(*) FROM publication_rows WHERE state<>'done'").fetchone()[0]
            ready=db.execute("SELECT COUNT(*) FROM publication_rows r WHERE r.state<>'done' AND r.next_retry<=? AND NOT EXISTS(SELECT 1 FROM publication_rows old WHERE old.contract=r.contract AND old.state<>'done' AND old.publication_seq<r.publication_seq)",(time.time(),)).fetchone()[0]
            if count:_notify_workbench(root)
            return dict(ok=not errors,count=count,complete=pending==0,pending_rows=pending,eligible_remaining=ready,errors=errors)
        finally:db.close()

def drain_b(root,*,limit=50):
    if not enabled(root):return dict(ok=True,disabled=True)
    from b_event_sqlite import SQLiteBStore
    from tracking.b_source import consume_page,seed_incremental
    if not (Path(root)/'runtime/b-events-v3.sqlite3').is_file():return dict(ok=True,count=0,not_initialized=True)
    with _LOCK:
        store=SQLiteBStore(root);seed=seed_incremental(store,limit=100)
        result=dict(consume_page(store,writer(root),clock_iso=now(),limit=limit),seeded=seed['count'],seed_complete=seed['complete'])
        if result.get('count'):_notify_workbench(root)
        return result

def read_route(root,method,url,body=None):
    from tracking.read_api import TrackingReadAPI
    from tracking.read_repository import TrackingReadRepository,ReadRepositoryError
    from tracking.tracking_http import TrackingHTTPRouter
    from urllib.parse import urlparse,parse_qs
    route=urlparse(url).path
    if route.startswith('/api/tracking/workbench/'):
        if method!='GET':return 405,b'{"ok":false,"errors":[{"code":"METHOD_not_allowed"}]}','application/json'
        from tracking.workbench import route as workbench_route
        result,code=workbench_route(root,url)
        return code,json.dumps(result,ensure_ascii=False,allow_nan=False).encode(),'application/json; charset=utf-8'
    if method=='GET' and route=='/api/tracking/runtime':
        from tracking.runtime import read_status
        return 200,json.dumps(read_status(root),ensure_ascii=False).encode(),'application/json; charset=utf-8'
    if method=='GET' and route=='/api/tracking/samples':
        from tracking.sample_capture import read
        q=parse_qs(urlparse(url).query)
        try:
            result=read(root,after=int(q.get('after',['0'])[0]),limit=int(q.get('limit',['20'])[0]))
        except (ValueError,TypeError) as exc:
            return 400,json.dumps(dict(ok=False,error=str(exc))).encode(),'application/json'
        return 200,json.dumps(result,ensure_ascii=False).encode(),'application/json; charset=utf-8'
    if route in ('/api/tracking/notifications','/api/tracking/notifications/delivered','/api/tracking/notifications/read'):
        from tracking.notifications import page,acknowledge
        try:
            if method=='GET' and route=='/api/tracking/notifications':
                q=parse_qs(urlparse(url).query);repo=TrackingReadRepository.for_runtime(root)
                result=page(repo,before_id=int(q['before_id'][0]) if 'before_id' in q else None,limit=int(q.get('limit',['20'])[0]))
            elif method=='POST' and route!='/api/tracking/notifications':
                payload=json.loads(body or b'{}')
                if set(payload)!={'ids'}:raise ValueError('notification_body')
                result=acknowledge(writer(root),ids=payload['ids'],action=route.rsplit('/',1)[1])
            else:return 405,b'{"ok":false,"errors":[{"code":"METHOD_not_allowed"}]}','application/json'
            return (200 if result['ok'] else 409),json.dumps(result,ensure_ascii=False).encode(),'application/json; charset=utf-8'
        except ReadRepositoryError as exc:return 503,json.dumps(dict(ok=False,errors=[exc.as_dict()])).encode(),'application/json'
        except (ValueError,TypeError) as exc:return 400,json.dumps(dict(ok=False,errors=[dict(code='NOTIFICATION_invalid',detail=str(exc))])).encode(),'application/json'
    if url.split('?')[0]=='/api/tracking/status':
        return 200,json.dumps(dict(ok=True,**status(root))).encode(),'application/json; charset=utf-8'
    with _LOCK:
        key=str(Path(root).resolve());router=_ROUTERS.get(key)
        if router is None:
            try:
                repo=TrackingReadRepository.for_runtime(root)
                router=TrackingHTTPRouter(TrackingReadAPI(repo,scan_generation='published-tracking-ledger'),mount_context='runtime')
            except ReadRepositoryError as exc:
                return 503,json.dumps(dict(ok=False,errors=[exc.as_dict()],status=status(root))).encode(),'application/json; charset=utf-8'
            _ROUTERS[key]=router
    return router.handle(method,url,body)

def compact_summary(root,market,symbol):
    """Separate latest-only additive summary, max three strategy heads."""
    summaries,_=compact_summaries(root,[(market,symbol)])
    market='USDT-M' if market=='USDⓈ-M' else market
    return summaries.get((market,symbol),[])


_WORKERS={}
def kick(root):
    """Resume only local queued computations after an authorized original producer.

    This worker cannot fetch market data. Merely opening any UI never starts it.
    """
    if not enabled(root):return
    key=str(Path(root).resolve())
    with _LOCK:
        old=_WORKERS.get(key)
        if old and old.is_alive():return
        def run():
            while enabled(root):
                try:
                    resumed=resume_publications(root,limit=20)
                    b=drain_b(root,limit=50)
                    p=drain_publications(root,row_budget=10)
                    r=drain_recovery(root,limit=10)
                    if not b.get('ok') or (not p.get('ok') and not p.get('eligible_remaining')):break
                    if not resumed.get('count') and not b.get('count') and not b.get('seeded') and not p.get('count') and not r.get('count'):break
                except Exception as exc:
                    capture_error(root,exc);break
        thread=threading.Thread(target=run,name='local-ABC-tracking',daemon=True)
        _WORKERS[key]=thread;thread.start()


def compact_summaries(root,keys):
    from tracking.read_repository import TrackingReadRepository,ReadRepositoryError
    try:repo=TrackingReadRepository.for_runtime(root)
    except ReadRepositoryError:return {},0
    keys=sorted(set(('USDT-M' if m=='USDⓈ-M' else m,s) for m,s in keys))
    if len(keys)>1000:raise ValueError('tracking_summary_contract_budget')
    if not keys:return {},0
    marks=','.join('(?,?)' for _ in keys);args=tuple(v for pair in keys for v in pair)
    # Three indexed head seeks per requested contract; never rank every old
    # event/revision merely to return one A/B/C head.
    sql="WITH contracts(m,s) AS (VALUES "+marks+"), strategies(strategy) AS (VALUES ('A'),('B'),('C')) SELECT v.market,v.symbol,v.summary_json FROM contracts c CROSS JOIN strategies t JOIN tracking_summary_versions v ON v.rowid=(SELECT h.rowid FROM tracking_summary_versions h WHERE h.market=c.m AND h.symbol=c.s AND json_extract(h.summary_json,'$.strategy')=t.strategy AND h.valid_to_seq IS NULL ORDER BY h.decision_close_ms DESC,h.event_id DESC LIMIT 1)"

    with repo.snapshot():
        seq=repo.tracking_commit_seq();rows=repo.read(sql,args)
        from tracking.recovery_quality import snapshot as recovery_snapshot
        result={}
        for row in rows:
            summary=json.loads(row['summary_json'])
            summary.setdefault('data_quality',{})['recovery']=recovery_snapshot(repo,market=row['market'],symbol=row['symbol'],event_id=summary['event_id'])
            result.setdefault((row['market'],row['symbol']),[]).append(summary)
    return result,seq

def compact_c_checks(root,keys):
    """Bounded current C0 denominator facts, separate from candidate events."""
    from tracking.read_repository import TrackingReadRepository,ReadRepositoryError
    try:repo=TrackingReadRepository.for_runtime(root)
    except ReadRepositoryError:return {},0
    keys=sorted(set(('USDT-M' if m=='USDⓈ-M' else m,s) for m,s in keys))
    if len(keys)>1000:raise ValueError('C_check_contract_budget')
    if not keys:return {},0
    names=['c-controller|'+m+'|'+s+'|c-1015-v1.1' for m,s in keys]
    marks=','.join('?' for _ in names)
    with repo.snapshot():
        seq=repo.tracking_commit_seq()
        rows=repo.read('SELECT cursor_name,cursor_value FROM cursors WHERE cursor_name IN ('+marks+')',tuple(names))
        states={row['cursor_name']:json.loads(row['cursor_value']) for row in rows}
    result={}
    for key,name in zip(keys,names):
        state=states.get(name)
        if state is None:continue
        result[key]=dict(rule_version='c-1015-v1.1',target_close_ms=state.get('last_seen_close_ms'),
            evaluated_close_ms=state.get('evaluated_close_ms'),has_cycle=state.get('t0') is not None,
            checks=state.get('last_c0') if state.get('t0') is None else None,
            source_quality=state.get('source_quality'),scope='saved_published_input')
    return result,seq


def c_check_generation(root):
    from tracking.read_repository import TrackingReadRepository,ReadRepositoryError
    try:repo=TrackingReadRepository.for_runtime(root)
    except ReadRepositoryError:return None
    rows=repo.read("SELECT cursor_name,cursor_value FROM cursors WHERE cursor_name>=? AND cursor_name<? ORDER BY cursor_name LIMIT 1001",('c-controller|','c-controller}'))
    if len(rows)>1000:raise ValueError('C_check_contract_budget')
    return hashlib.sha256(json.dumps([list(row) for row in rows],ensure_ascii=False,separators=(',',':')).encode()).hexdigest()


def tracking_seq(root):
    from tracking.read_repository import TrackingReadRepository,ReadRepositoryError
    try:return TrackingReadRepository.for_runtime(root).tracking_commit_seq()
    except ReadRepositoryError:return None

def recovery_seq(root):
    from tracking.read_repository import TrackingReadRepository,ReadRepositoryError
    try:return TrackingReadRepository.for_runtime(root).recovery_commit_seq()
    except ReadRepositoryError:return None


def requires_c_replay(ledger,market,symbol,inp,*,cycle_t0=None):
    from tracking.tracking_engine import digest
    state=ledger.get_cursor('c-controller|'+market+'|'+symbol+'|c-1015-v1.1')
    state=json.loads(state) if state else {}
    if cycle_t0 is not None:state['t0']=cycle_t0
    if state.get('t0') is None:return False
    eid='C|c_cycle|'+market+'|'+symbol+'|4h|c-1015-v1.1|'+str(state['t0'])
    context=ledger.get_cursor('tracking.context|'+eid);context=json.loads(context) if context else {}
    old=ledger.get_inbox(context.get('input_key',''))
    if not old:return False
    previous=old['payload']
    for role,key in [('raw_bars','open_ms'),('oi_samples','effective_at_ms'),('lsr_samples','effective_at_ms'),('caps','effective_at_ms')]:
        before={s[key]:s for s in previous.get(role,[])}
        for sample in inp.get(role,[]):
            prior=before.get(sample[key])
            if prior is None:continue
            keys=('open_ms','o','h','l','c','q','q_unit') if role=='raw_bars' else ('effective_at_ms','unit','value','trusted')
            if any(prior.get(k)!=sample.get(k) for k in keys):return True
    return False


def recover_sources(root,*,fetch,clock_ms,grant_id=None,page_budget=1,max_http_attempts=4,deadline_seconds=30,clock=None):
    """Explicit adapter seam only. Never called by GET, publication kick or timer.

    The operator supplies a reviewed fetch callback under separately authorized
    collection; this module does not install a network client or schedule it.
    """
    if not enabled(root):return dict(ok=True,disabled=True)
    from tracking.recovery import Recovery
    with _LOCK:
        pages=Recovery(writer(root)).page(fetch=fetch,clock_ms=clock_ms,grant_id=grant_id,page_budget=page_budget,max_http_attempts=max_http_attempts,deadline_seconds=deadline_seconds,clock=clock)
    replay=drain_recovery(root,limit=10,clock_ms=clock_ms)
    return dict(ok=replay['ok'],pages=pages,replay=replay,evaluation_acked=False)

def drain_recovery(root,*,limit=10,clock_ms=None,allow_step=None):
    """Consume only persisted original context and archived evidence; no fetch."""
    if not enabled(root):return dict(ok=True,disabled=True,count=0,network_attempts=0)
    from tracking.recovery_replay import drain
    with _LOCK:
        result=drain(writer(root),clock_ms=int(time.time()*1000) if clock_ms is None else clock_ms,
                     limit=limit,allow_step=lambda:enabled(root) and (allow_step is None or allow_step()))
        if result.get('count'):_notify_workbench(root)
        return result
