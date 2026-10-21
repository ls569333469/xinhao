"""Versioned, journalled maintenance. Unknown dependencies fail closed."""
from __future__ import annotations
import datetime as dt,gzip,hashlib,json,os,re,sqlite3,time,uuid
from contextlib import closing
from pathlib import Path
from durable_io import protection,atomic_json
RETENTION_DEFAULTS={'temp_hours':48,'debug_days':14,'log_days':30,'raw_api_days':30,'full_snapshot_days':90,'oi_lsr_days':365,'1h_days':365,'backup_days':30,'backup_min_success':3}
POLICY='v32-r2-retention-v1'

def validate_retention(value):
    if not isinstance(value,dict) or set(value)!=set(RETENTION_DEFAULTS):raise ValueError('保留期字段不完整或含未知字段')
    for k,v in value.items():
        if type(v) is not int or not 1<=v<=3650:raise ValueError('保留期须为1–3650的整数')
    if value['backup_min_success']<3:raise ValueError('成功可恢复备份至少保留3份')
    return dict(value)

def effective_policy(root):
    p=Path(root)/'runtime'/'cleanup-settings.json'
    cfg=_read(p) if p.exists() else {}
    return validate_retention(dict(RETENTION_DEFAULTS,**cfg.get('retention',{})))
def age_days(ts,now=None):return ((time.time() if now is None else now)-ts)/86400

def safe_path(root,rel):
    root=Path(root).resolve();p=root/rel
    # Refuse junctions/symlinks at every component, not only final path.
    for parent in (p,*p.parents):
        if parent==root:break
        if parent.is_symlink() or getattr(parent,'is_junction',lambda:False)():raise ValueError('联接或符号链接不清理')
    resolved=p.resolve();resolved.relative_to(root)
    return resolved

def classify_path(rel):
    rel=rel.replace('\\','/')
    if re.fullmatch(r'runtime/b-rounds/bround-[a-f0-9]{32}\.json',rel):return 'b_round'
    if re.fullmatch(r'logs/[^/]+\.log(?:\.gz)?',rel):return 'log'
    if re.fullmatch(r'runtime/tmp/[^/]+',rel):return 'temp'
    if re.fullmatch(r'runtime/debug/[^/]+',rel):return 'debug'
    if re.fullmatch(r'runtime/raw-api/[^/]+\.json(?:\.gz)?',rel):return 'raw_api'
    if re.fullmatch(r'scan-history/scan-[a-f0-9]{32}/revision-[0-9]{4}\.json(?:\.gz)?',rel):return 'full_snapshot'
    # 4H/daily shared chunks are long-lived; first release does not age-delete them.
    return None

def _read(p):
    blob=p.read_bytes()
    if p.suffix=='.gz':blob=gzip.decompress(blob)
    if p.name.endswith(('.jsonl','.jsonl.gz')):return [json.loads(line) for line in blob.splitlines() if line.strip()]
    return json.loads(blob)

def references(root):
    root=Path(root);scans=set();paths=set();hashes=set();errors=[];tokens=[]
    def inspect(obj):
        if isinstance(obj,dict):
            for k,v in obj.items():
                if k in ('payload_hash','block_id') and isinstance(v,str):hashes.add(v)
                inspect(v)
        elif isinstance(obj,list):
            for v in obj:inspect(v)
        elif isinstance(obj,str):
            scans.update(re.findall(r'scan-[a-f0-9]{32}',obj))
            if obj.startswith(('scan-history/','runtime/','candle-history/','logs/')):paths.add(obj)
    files=[]
    current=root/'scan.json'
    if current.exists():files.append(current)
    # Current + three most recent AVAILABLE published scans, including partial_success.
    published=[]
    for status in (root/'scan-history').glob('scan-*/status.json'):
        try:
            state=_read(status)
            if state.get('status') in ('success','partial_success') and state.get('revision'):
                revision=status.parent/('revision-%04d.json'%int(state['revision']))
                if revision.exists():published.append((state.get('updatedAt',''),status.parent.name,revision))
        except (ValueError,OSError,TypeError):errors.append(str(status))
    for _,sid,p in sorted(published,reverse=True)[:3]:scans.add(sid);files.append(p)
    # Reports, strategies, simulations, experiments and activity can pin original
    # scan windows. No credential/config directory is opened by the collector.
    for name in ('reports','strategies','sim','eval','reviews','backtests','task-freeze','research-batches','research-intervals'):
        d=root/'runtime'/name
        if d.exists():files.extend(p for p in d.rglob('*') if p.is_file() and p.suffix in ('.json','.jsonl'))
    for name in ('b-accumulation-events.json','ema-squeeze-state.json','ema-squeeze-candidates.json','protected-references.json'):
        p=root/'runtime'/name
        if p.exists():files.append(p)
    # Legacy A state names vary: retain all scans until an unknown state is understood.
    for p in (root/'runtime').glob('*ema*.json'):files.append(p)
    for p in sorted(set(files)):
        try:
            raw=p.read_bytes();tokens.append((str(p.relative_to(root)),hashlib.sha256(raw).hexdigest()));inspect(_read(p))
        except (OSError,ValueError,TypeError):errors.append(str(p.relative_to(root)))
    if (root/'runtime'/'b-events-v3.sqlite3').exists():
        try:
            from b_accumulation_store import BAccumulationStore
            store=BAccumulationStore(root)
            # Expand immutable refs before collecting candle identities; old JSON
            # alone does not protect newly appended events after the migration.
            inspect(store.list_events());tokens.append(('b_events_generation',store.generation()))
        except (OSError,ValueError,sqlite3.Error):errors.append('B事件证据引用未能核实')
    taskdb=root/'runtime'/'workbench.sqlite3';active=(root/'scan.lock').exists()
    if taskdb.exists():
        try:
            with closing(sqlite3.connect(taskdb.as_uri()+'?mode=ro',uri=True)) as db:
                active=active or db.execute("SELECT 1 FROM tasks WHERE status IN ('queued','running') LIMIT 1").fetchone() is not None
        except sqlite3.Error:errors.append('活动任务不可读')
    # All surviving candle manifests protect their chunks, even ordinary history.
    # Chunk GC waits until its last verified manifest is removed.
    for p in (root/'scan-history').glob('scan-*/candles/*.json'):
        try:
            doc=_read(p)
            for ref in doc.get('chunks',[]):paths.add(ref['path'])
        except (ValueError,OSError,KeyError):errors.append(str(p.relative_to(root)))
    generation=hashlib.sha256(json.dumps([sorted(tokens),sorted(paths),sorted(scans),sorted(hashes)],sort_keys=True).encode()).hexdigest()
    return dict(scans=scans,paths=paths,hashes=hashes,errors=errors,active=active,generation=generation)

def _plan(root,now):
    refs=references(root);items=[];policy=effective_policy(root)
    dirs=['logs','runtime/tmp','runtime/debug','runtime/raw-api','runtime/shared-candles','runtime/b-rounds']
    candidates=[p for d in dirs for p in (root/d).glob('*') if p.is_file()]
    candidates+=list((root/'scan-history').glob('scan-*/revision-*.json'))
    for path in candidates:
        rel=path.relative_to(root).as_posix();cat=classify_path(rel)
        if not cat:continue
        try:
            p=safe_path(root,rel);stat=p.stat();age=now-stat.st_mtime
            if cat in ('full_snapshot','b_round'):
                doc=_read(p);value=doc.get('finishedAt') or doc.get('available_at') or doc.get('published_at')
                from b_evidence import ms
                source_ms=ms(value)
                if source_ms is None:continue
                age=now-source_ms/1000
            if cat=='temp':
                metadata=p.with_name(p.name+'.metadata.json')
                if not metadata.exists():continue
                meta=_read(metadata)
                if meta.get('task_status') not in ('success','partial_success','failed','cancelled') or not meta.get('finished_at'):continue
            if cat=='log' and p.name in ('dashboard.out.log','dashboard.err.log'):continue
            ttl=(policy['temp_hours']*3600 if cat=='temp' else policy.get(cat+'_days',policy['full_snapshot_days'])*86400)
            if age<=ttl or age<0:continue
            reason=None
            if refs['errors']:reason='引用未能核实'
            elif refs['active']:reason='有在途任务'
            elif rel in refs['paths'] or any(s in rel for s in refs['scans']):reason='当前／最近三轮／研究或活动引用保护'
            # Legacy published windows are retained unless both revision and
            # dependency inventory are verifiable. Never infer from mtime alone.
            if cat=='full_snapshot':
                doc=_read(p)
                if not doc.get('available_at') or doc.get('quality',{}).get('status') not in ('success','partial_success'):reason='发布时间或已发布状态未知'
            items.append(dict(path=rel,category=cat,size=stat.st_size,mtime=stat.st_mtime,source_hash=hashlib.sha256(p.read_bytes()).hexdigest(),age_days=age/86400,state='skipped' if reason else 'planned',reason=reason))
        except (OSError,ValueError,TypeError):continue
    return dict(schema=POLICY,retention=policy,id='cleanup-'+uuid.uuid4().hex,created_at=now,generation=refs['generation'],items=items,reference_errors=refs['errors'],actual_bytes=0,attempted_bytes=0)

def dry_run_candidates(root,*,now=None):
    return _plan(Path(root).resolve(),time.time() if now is None else now)['items']

def prune_hourly(root,refs,journal,plan,now):
    """Delete eligible ordinary rows transactionally; frozen event inputs stay inline."""
    dbpath=Path(root)/'runtime'/'candle-cache'/'index.sqlite3'
    if not dbpath.exists():return
    # Legacy research intervals without explicit block references conservatively
    # pin the shared index until their dependency mapping is available.
    if any((Path(root)/'runtime'/d).exists() and any((Path(root)/'runtime'/d).rglob('*.json')) for d in ('reports','strategies','eval','sim','backtests','research-intervals')):
        plan['hourly_prune']='skipped: legacy research dependency protection';return
    from market_candle_cache import MarketCandleCache
    cache=MarketCandleCache(root)
    with closing(cache._connect()) as db,db:
        cutoff=int((now-effective_policy(root)['1h_days']*86400)*1000)
        candidates=list(db.execute("SELECT market,symbol,interval,open_time,payload_hash,block_id FROM bars WHERE interval='1h' AND open_time+3600000<?",(cutoff,)))
        deletable=[tuple(r) for r in candidates if r['payload_hash'] not in refs['hashes'] and r['block_id'] not in refs['hashes']]
        plan['hourly_prune']=dict(state='planned',rows=len(deletable),keys=deletable);atomic_json(journal,plan)
        db.executemany('DELETE FROM bars WHERE market=? AND symbol=? AND interval=? AND open_time=? AND payload_hash=? AND block_id=?',deletable)
        db.execute('DELETE FROM retrievals WHERE NOT EXISTS(SELECT 1 FROM bars b WHERE b.market=retrievals.market AND b.symbol=retrievals.symbol AND b.interval=retrievals.interval AND b.open_time=retrievals.open_time AND b.payload_hash=retrievals.payload_hash)')
        db.execute("DELETE FROM block_payloads WHERE NOT EXISTS(SELECT 1 FROM bars b WHERE b.block_id=block_payloads.block_id)")
        db.execute("DELETE FROM blocks WHERE path='' AND NOT EXISTS(SELECT 1 FROM bars b WHERE b.block_id=blocks.block_id)")
    plan['hourly_prune']['state']='deleted';atomic_json(journal,plan)
    cutoff=int((now-365*86400)*1000)
    obs=Path(root)/'runtime'/'b-observations.sqlite3'
    if obs.exists():
        with closing(sqlite3.connect(obs)) as db,db:
            for table in ('observations','observation_revisions','round_observations'):
                if db.execute('SELECT 1 FROM sqlite_master WHERE name=?',(table,)).fetchone():db.execute('DELETE FROM '+table+' WHERE '+('target' if table=='round_observations' else 'bar_open+3600000')+'<?',(cutoff,))
    # The event file contains protected OI/LSR snapshots independently. Ordinary
    # source history may expire; omit unknown timestamps rather than guessing age.
    cutoff=int((now-effective_policy(root)['oi_lsr_days']*86400)*1000)
    for p in (Path(root)/'runtime'/'b-source').glob('*/structure.json'):
        data=_read(p)
        for key in ('oi_samples','lsr_samples','oi_samples_revisions','lsr_samples_revisions'):
            data[key]=[r for r in data.get(key,[]) if not isinstance(r.get('timestamp'),(int,float)) or r['timestamp']>=cutoff]
        from b_evidence import ms
        data['cap_samples']=[r for r in data.get('cap_samples',[]) if ms(r.get('capObservedAt')) is None or ms(r.get('capObservedAt'))>=cutoff]
        atomic_json(p,data)

def _remove_planned(root,item,now):
    p=safe_path(root,item['path'])
    if item['category']=='full_snapshot':
        atomic_json(p.with_name(p.name+'.cleaned.json'),dict(scan_id=p.parent.name,revision_file=p.name,status='cleaned',reason='已按保留策略清理',source_hash=item['source_hash'],cleaned_at=now))
    if item['category']=='log' and p.suffix=='.log':
        archive=safe_path(root,item['path']+'.gz');raw=p.read_bytes()
        if archive.exists():
            if gzip.decompress(archive.read_bytes())!=raw:raise ValueError('既有压缩日志不同，保留两份供核查')
        else:
            temporary=archive.with_name(archive.name+'.'+uuid.uuid4().hex+'.tmp')
            try:
                with temporary.open('xb') as f:f.write(gzip.compress(raw,mtime=0));f.flush();os.fsync(f.fileno())
                if gzip.decompress(temporary.read_bytes())!=raw:raise ValueError('日志压缩校验失败')
                temporary.replace(archive);os.utime(archive,(item['mtime'],item['mtime']))
            finally:temporary.unlink(missing_ok=True)
        item['compressed_path']=archive.relative_to(root).as_posix()
        item['compressed_hash']=hashlib.sha256(archive.read_bytes()).hexdigest()
        p.unlink();return max(0,item['size']-archive.stat().st_size)
    p.unlink();return item['size']

def maintain(root,*,dry_run=True,now=None,plan_path=None):
    root=Path(root).resolve();now=time.time() if now is None else now
    with protection(root):
        plan=_plan(root,now);journal=Path(plan_path) if plan_path else root/'runtime'/'cleanup-audit'/(plan['id']+'.json')
        atomic_json(journal,plan) # Persist BEFORE validating/deleting anything.
        if dry_run:return plan
        current=references(root)
        if current['generation']!=plan['generation'] or current['active'] or current['errors']:
            plan['aborted']='引用版本变化／活动任务／引用不完整';atomic_json(journal,plan);return plan
        for item in plan['items']:
            if item['state']!='planned':continue
            try:
                p=safe_path(root,item['path'])
                if not p.exists():item['state']='deleted';item['reason']='已计划对象已不存在';atomic_json(journal,plan);continue
                if p.stat().st_mtime!=item['mtime'] or hashlib.sha256(p.read_bytes()).hexdigest()!=item['source_hash']:raise ValueError('源对象已变更')
                item['state']='validated';atomic_json(journal,plan)
                item['state']='deleting';plan['attempted_bytes']+=item['size'];atomic_json(journal,plan)
                recovered=_remove_planned(root,item,now);item['state']='deleted';plan['actual_bytes']+=recovered
            except (OSError,ValueError) as exc:item['state']='failed';item['reason']=str(exc)
            atomic_json(journal,plan)
        prune_hourly(root,current,journal,plan,now)
        plan['finished_at']=time.time();atomic_json(journal,plan);return plan

def resume(root,journal):
    # Recovery never enumerates a wider target set; a changed reference generation
    # leaves the old journal intact for inspection and a fresh daily plan.
    root=Path(root).resolve();p=Path(journal);old=_read(p)
    with protection(root):
        refs=references(root)
        if refs['generation']!=old['generation'] or refs['active'] or refs['errors']:return dict(resumed=False,reason='引用变化或活动中，保留旧计划')
        for item in old['items']:
            if item['state'] not in ('planned','validated','deleting','failed'):continue
            target=safe_path(root,item['path'])
            if not target.exists():item['state']='deleted'
            elif hashlib.sha256(target.read_bytes()).hexdigest()==item['source_hash']:
                try:
                    item['state']='deleting';atomic_json(p,old)
                    recovered=_remove_planned(root,item,time.time());item['state']='deleted';old['actual_bytes']+=recovered
                except (OSError,ValueError) as exc:item['state']='failed';item['reason']=str(exc)
            else:item['state']='skipped';item['reason']='源已变更'
            atomic_json(p,old)
        return dict(resumed=True,plan=old)

def backup_removals(manifests,now,policy=None):
    """Only verified independent recovery types count; retain dependency chains."""
    policy=policy or RETENTION_DEFAULTS
    protected=set();groups={}
    for m in manifests:
        if m.get('verified') is True and m.get('status')=='success' and m.get('type') and m.get('id') and isinstance(m.get('completed_at'),(int,float)) and m['completed_at']<=now:groups.setdefault(m['type'],[]).append(m)
        else:protected.add(m.get('id'))
    byid={m.get('id'):m for m in manifests}
    for group in groups.values():
        for m in sorted(group,key=lambda x:x.get('completed_at',0),reverse=True)[:max(3,policy['backup_min_success'])]:protected.add(m['id'])
    for m in manifests:
        ts=m.get('completed_at')
        if not isinstance(ts,(int,float)) or not 0<=now-ts<=policy['backup_days']*86400:continue
        protected.add(m['id'])
    todo=list(protected)
    while todo:
        m=byid.get(todo.pop(),{})
        for dep in m.get('depends_on',[]):
            if dep not in protected:protected.add(dep);todo.append(dep)
    return [m['id'] for m in manifests if m.get('id') not in protected and isinstance(m.get('completed_at'),(int,float)) and now-m['completed_at']>policy['backup_days']*86400]

def capacity(root):
    groups={};total=0
    for p in Path(root).rglob('*'):
        if not p.is_file() or p.is_symlink():continue
        try:n=p.stat().st_size
        except OSError:continue
        name=p.relative_to(root).parts[0];groups[name]=groups.get(name,0)+n;total+=n
    return dict(total_bytes=total,categories=groups,budget_bytes=40*1024**3,level='90%' if total>=.9*40*1024**3 else '80%' if total>=.8*40*1024**3 else '正常')

def daily_if_idle(root,now=None):
    now=time.time() if now is None else now;p=Path(root)/'runtime'/'cleanup-settings.json'
    cfg=_read(p) if p.exists() else dict(enabled=True,last_attempt_at=None)
    if not cfg.get('enabled') or (cfg.get('last_attempt_at') is not None and now-cfg['last_attempt_at']<86400):return None
    if references(root)['active']:return None
    result=maintain(root,dry_run=False,now=now);cfg['last_attempt_at']=now;atomic_json(p,cfg);return result

def write_dry_run_report(root,report_path=None):
    path=Path(report_path) if report_path else Path(root)/'runtime'/'cleanup-audit'/'dry-run.json'
    maintain(root,dry_run=True,plan_path=path);return path
