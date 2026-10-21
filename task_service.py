"""Persistent local scan tasks and interval schedules; no AI or trading calls."""
from durable_io import protected
import copy
import datetime as dt
import json
import hashlib
import sqlite3
import threading
import uuid
from pathlib import Path

from observe_config import OBSERVE_DEFAULT, TIMING_DEFAULT, migrate_observe, migrate_timing, validate_observe, validate_timing
from b_observe_schedule import in_flight_reuse_key, next_observe_due_iso, should_fire
from observe_config import target_close_boundary_ms

UTC = dt.timezone.utc
DEFAULT_SCAN = {'enabled': False, 'intervalHours': 1, 'timezone': 'Asia/Shanghai', 'recoverMissed': True}
ACTIVE = ('queued', 'running')


def next_scan_due(now,hours,offset=120):
    step=int(hours*3600)
    boundary=(int(now.timestamp())//step)*step+offset
    if boundary<=now.timestamp():boundary+=step
    return iso(dt.datetime.fromtimestamp(boundary,UTC))


def iso(value):
    return value.astimezone(UTC).isoformat(timespec='microseconds')


class Conflict(ValueError):
    pass


def validate_scan(value):
    if not isinstance(value, dict) or set(value) != set(DEFAULT_SCAN):
        raise ValueError('scan必须包含enabled/intervalHours/timezone/recoverMissed；不支持其他设置')
    if type(value['enabled']) is not bool or type(value['recoverMissed']) is not bool:
        raise ValueError('开关必须是布尔值')
    if type(value['intervalHours']) not in (int, float) or value['intervalHours'] not in (0.25, 1, 4, 8, 24):
        raise ValueError('扫描间隔只支持15分钟或1/4/8/24小时')
    if value['timezone'] not in ('Asia/Shanghai', 'America/Los_Angeles', 'UTC'):
        raise ValueError('不支持的时区')
    # Interval arithmetic is UTC elapsed time. The whitelisted IANA label is
    # for browser display, not a wall-clock recurrence requiring tzdata.
    return copy.deepcopy(value)


from scan_demands import ScanDemands

from countdown_schedule import CountdownSchedule

class TaskService(ScanDemands, CountdownSchedule):
    def __init__(self, root, runner, progress=None, clock=None, ranges=None):
        self.root = Path(root)
        self.path = self.root / 'runtime' / 'workbench.sqlite3'
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.runner = runner
        from candle_ranges import CandleRanges
        self.ranges = ranges or CandleRanges(self.root, clock=clock)
        self.research = None
        self.eval_store = None
        self.review_store = None
        self.scheduler_error = None
        self.active_kind = None
        self.progress = progress or (lambda: {})
        self.clock = clock or (lambda: dt.datetime.now(UTC))
        self.lock = threading.RLock()
        self.stop_event = threading.Event()
        self.thread = None
        self.worker = None
        self.workers = {}
        self.active_tasks = {}
        self.research_workers = 2
        self.active_id = None
        self.recovery_tick = True
        self._repair_next_discovery = 0
        self.scheduler_heartbeat = None
        self.subsystem_errors = {}
        self._collection_worker = None
        self._collection_next_tick = 0
        with self._db() as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS settings (
                    id INTEGER PRIMARY KEY CHECK(id=1), version INTEGER NOT NULL,
                    scan TEXT NOT NULL, next_due TEXT, updated_at TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS scan_operations (
                    id TEXT PRIMARY KEY, created_at TEXT NOT NULL,
                    requested_target INTEGER NOT NULL, scan_task_id TEXT NOT NULL,
                    b_task_id TEXT NOT NULL, submission_key TEXT UNIQUE);
                CREATE TABLE IF NOT EXISTS scan_operation_requests (
                    submission_key TEXT PRIMARY KEY, operation_id TEXT NOT NULL,
                    requested_target INTEGER NOT NULL);
                CREATE TABLE IF NOT EXISTS tasks (
                    id TEXT PRIMARY KEY, kind TEXT NOT NULL, status TEXT NOT NULL,
                    reason TEXT NOT NULL, created_at TEXT NOT NULL, started_at TEXT,
                    finished_at TEXT, progress TEXT NOT NULL, result TEXT, error TEXT,
                    settings_version INTEGER NOT NULL);
                CREATE UNIQUE INDEX IF NOT EXISTS one_active_scan ON tasks(kind)
                    WHERE status IN ('queued','running');
            ''')
            columns = {r['name'] for r in db.execute('PRAGMA table_info(tasks)')}
            if 'request' not in columns:
                db.execute("ALTER TABLE tasks ADD COLUMN request TEXT NOT NULL DEFAULT '{}'")
            if 'request_hash' not in columns:
                db.execute('ALTER TABLE tasks ADD COLUMN request_hash TEXT')
            db.execute('DROP INDEX IF EXISTS one_active_scan')
            db.execute("CREATE UNIQUE INDEX one_active_scan ON tasks(kind) WHERE kind='scan' AND status IN ('queued','running')")
            db.execute("CREATE UNIQUE INDEX IF NOT EXISTS one_active_range ON tasks(request_hash) WHERE kind='candle_range' AND status IN ('queued','running')")
            # R1/REV-02: migrate EXISTING DB — drop content-hash active unique indexes for research
            db.execute('DROP INDEX IF EXISTS one_active_research')
            db.execute('DROP INDEX IF EXISTS one_active_scan_research')
            # New columns for submission idempotency + two-phase freeze (keep old rows)
            columns = {r['name'] for r in db.execute('PRAGMA table_info(tasks)')}
            if 'submission_id' not in columns:
                db.execute('ALTER TABLE tasks ADD COLUMN submission_id TEXT')
            if 'intent_hash' not in columns:
                db.execute('ALTER TABLE tasks ADD COLUMN intent_hash TEXT')
            if 'freeze_phase' not in columns:
                db.execute("ALTER TABLE tasks ADD COLUMN freeze_phase TEXT")
            # One logical task per confirm identity (all statuses); retries reuse same row
            db.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS one_submission_research "
                "ON tasks(submission_id) "
                "WHERE kind IN ('ai_research','scan_and_research') AND submission_id IS NOT NULL"
            )
            # request_hash remains for tracing / range-fill only — NOT research active-dedup
            now = iso(self.clock())
            db.execute('INSERT OR IGNORE INTO settings(id,version,scan,next_due,updated_at) VALUES (1,1,?,NULL,?)', (json.dumps(DEFAULT_SCAN), now))
            # v3.2: timing / observe blobs (default off); migrate old DBs
            cols = {r['name'] for r in db.execute('PRAGMA table_info(settings)')}
            if 'timing' not in cols:
                db.execute("ALTER TABLE settings ADD COLUMN timing TEXT")
            if 'observe' not in cols:
                db.execute("ALTER TABLE settings ADD COLUMN observe TEXT")
            if 'observe_next_due' not in cols:
                db.execute("ALTER TABLE settings ADD COLUMN observe_next_due TEXT")
            row0 = db.execute('SELECT timing, observe FROM settings WHERE id=1').fetchone()
            db.execute('CREATE TABLE IF NOT EXISTS schema_migrations(name TEXT PRIMARY KEY, applied_at TEXT)')
            if not db.execute("SELECT 1 FROM schema_migrations WHERE name='v32-hourly-default'").fetchone():
                oldscan=json.loads(db.execute('SELECT scan FROM settings WHERE id=1').fetchone()[0])
                oldscan['intervalHours']=1
                db.execute('UPDATE settings SET scan=?,next_due=? WHERE id=1',(json.dumps(oldscan),next_scan_due(self.clock(),1) if oldscan['enabled'] else None))
                db.execute('INSERT INTO schema_migrations VALUES(?,?)',('v32-hourly-default',now))
            if row0 is not None:
                if row0['timing'] is None:
                    db.execute('UPDATE settings SET timing=? WHERE id=1', (json.dumps(TIMING_DEFAULT),))
                if row0['observe'] is None:
                    db.execute('UPDATE settings SET observe=? WHERE id=1', (json.dumps(OBSERVE_DEFAULT),))

            if 'collection' not in {r['name'] for r in db.execute('PRAGMA table_info(settings)')}:
                db.execute('ALTER TABLE settings ADD COLUMN collection TEXT')
            self._init_demands(db)
            self._countdown_schema(db)
            # Only the process owning both HTTP ports constructs this service.
            # Interrupted tasks are not reported as success or blindly replayed.
            db.execute("UPDATE tasks SET status='failed',finished_at=?,error=?,progress=? WHERE status IN ('queued','running')",
                       (now, '服务中断；未自动重跑此任务。已发布快照如有保留，请核对输出。', json.dumps({'phase': '服务重启，任务中断'})))

    def _db(self):
        # sqlite connection context does not close; use an explicit context.
        from contextlib import contextmanager

        @contextmanager
        def connection():
            db = sqlite3.connect(self.path, timeout=10)
            db.row_factory = sqlite3.Row
            try:
                with db:
                    yield db
            finally:
                db.close()
        return connection()

    def settings(self):
        with self.lock, self._db() as db:
            row = db.execute('SELECT * FROM settings WHERE id=1').fetchone()
        timing_raw = row['timing'] if 'timing' in row.keys() else None
        observe_raw = row['observe'] if 'observe' in row.keys() else None
        timing = migrate_timing(json.loads(timing_raw) if timing_raw else None)
        observe = migrate_observe(json.loads(observe_raw) if observe_raw else None)
        from collection_policy import DEFAULT as COLLECTION_DEFAULT
        return {'collection':json.loads(row['collection']) if row['collection'] else dict(COLLECTION_DEFAULT), 'version': row['version'], 'scan': json.loads(row['scan']), 'next_due': row['next_due'],
                'timing': timing, 'observe': observe, 'observe_next_due': row['observe_next_due'] if 'observe_next_due' in row.keys() else None,
                'updated_at': row['updated_at'], 'scheduler_running': bool(self.thread and self.thread.is_alive()),
                'scheduler_error': getattr(self, 'scheduler_error', None),
                'scheduler_heartbeat': self.scheduler_heartbeat,
                'subsystem_errors': dict(self.subsystem_errors),
                'test_countdown': self.countdown(),
                'executor': {'research_workers': self.research_workers, 'data_workers': 1, 'active': dict(self.active_tasks)},
                'capabilities': {'scan': True, 'schedule': True, 'ai': False, 'ledger': False,
                                 'rangeBackfill': True, 'bObserve': True, 'settingsScope': ['scan', 'timing', 'observe', 'collection']}}

    @protected
    def save_settings(self, payload):
        if not isinstance(payload, dict) or type(payload.get('version')) is not int or 'scan' not in payload:
            raise ValueError('保存须带当前version和scan配置')
        allowed = {'version', 'scan', 'timing', 'observe', 'collection'}
        if set(payload) - allowed:
            raise ValueError('不支持的设置字段')
        scan = validate_scan(payload['scan'])
        from collection_policy import validate as validate_collection
        collection=validate_collection(payload['collection']) if 'collection' in payload else None
        with self.lock, self._db() as db:
            row = db.execute('SELECT * FROM settings WHERE id=1').fetchone()
            if payload['version'] != row['version']:
                raise Conflict('配置已被其他页面更新，请重新读取后保存')
            previous = json.loads(row['scan'])
            timing = validate_timing(payload['timing']) if 'timing' in payload else migrate_timing(json.loads(row['timing']) if row['timing'] else None)
            due = row['next_due']
            if not scan['enabled']:
                due = None
            elif not previous['enabled'] or scan['intervalHours'] != previous['intervalHours'] or due is None:
                due = next_scan_due(self.clock(),scan['intervalHours'],timing['closeOffsetSec'])
            if scan['enabled'] and 'timing' in payload and timing['closeOffsetSec'] != migrate_timing(json.loads(row['timing']) if row['timing'] else None)['closeOffsetSec']:
                due=next_scan_due(self.clock(),scan['intervalHours'],timing['closeOffsetSec'])
            observe = validate_observe(payload['observe']) if 'observe' in payload else migrate_observe(json.loads(row['observe']) if row['observe'] else None)
            # 改间隔/偏移/观察开关不得静默把 scan.enabled 打开
            obs_due = row['observe_next_due'] if 'observe_next_due' in row.keys() else None
            prev_obs = migrate_observe(json.loads(row['observe']) if row['observe'] else None)
            if not observe['enabled']:
                obs_due = None
            elif (not prev_obs['enabled']) or ('timing' in payload and timing['closeOffsetSec'] != migrate_timing(json.loads(row['timing']) if row['timing'] else None)['closeOffsetSec']) or obs_due is None:
                obs_due = next_observe_due_iso(self.clock(), offset_sec=timing['closeOffsetSec'])
            if observe['enabled']:
                from b_accumulation_store import BAccumulationStore
                BAccumulationStore(self.root).ensure_observation_start()
            db.execute('UPDATE settings SET version=version+1,scan=?,next_due=?,timing=?,observe=?,observe_next_due=?,updated_at=? WHERE id=1',
                       (json.dumps(scan), due, json.dumps(timing), json.dumps(observe), obs_due, iso(self.clock())))
            if collection is not None:
                db.execute('UPDATE settings SET collection=? WHERE id=1',(json.dumps(collection),))
        return self.settings()

    @staticmethod
    def _task(row):
        if row is None:
            return None
        result = dict(row)
        result['request'] = json.loads(result['request'])
        result['progress'] = json.loads(result['progress'])
        result['result'] = json.loads(result['result']) if result['result'] else None
        return result

    def tasks(self, limit=50):
        with self.lock, self._db() as db:
            return [self._task(r) for r in db.execute('SELECT * FROM tasks ORDER BY created_at DESC,id DESC LIMIT ?', (limit,))]

    def task(self, task_id):
        with self.lock, self._db() as db:
            return self._task(db.execute('SELECT * FROM tasks WHERE id=?', (task_id,)).fetchone())

    def _enqueue(self, db, reason, version):
        current = db.execute("SELECT * FROM tasks WHERE kind='scan' AND status IN ('queued','running')").fetchone()
        if current:
            if reason in ('scheduled','recovery'):
                timing=json.loads(db.execute('SELECT timing FROM settings WHERE id=1').fetchone()[0] or '{}')
                boundary=target_close_boundary_ms(int(self.clock().timestamp()*1000),offset_sec=timing.get('closeOffsetSec',120))
                self._demand(db,'schedule','scan',version,boundary,current['id'],request=dict(target_close_boundary=boundary))
            return current['id'], True
        task_id = 'task-' + uuid.uuid4().hex
        db.execute('INSERT INTO tasks (id,kind,status,reason,created_at,progress,settings_version) VALUES (?,\'scan\',\'queued\',?,?,?,?)',
                   (task_id, reason, iso(self.clock()), json.dumps({'phase': '等待采集', 'completed': 0, 'total': 0}), version))
        timing=json.loads(db.execute('SELECT timing FROM settings WHERE id=1').fetchone()[0] or '{}')
        boundary=target_close_boundary_ms(int(self.clock().timestamp()*1000),offset_sec=timing.get('closeOffsetSec',120))
        db.execute('UPDATE tasks SET request=? WHERE id=?',(json.dumps(dict(target_close_boundary=boundary)),task_id))
        if reason in ('scheduled','recovery'):self._demand(db,'schedule','scan',version,boundary,task_id)
        return task_id, False

    def submit(self):
        with self.lock, self._db() as db:
            version = db.execute('SELECT version FROM settings WHERE id=1').fetchone()[0]
            task_id, reused = self._enqueue(db, 'manual', version)
        return {'task': self.task(task_id), 'reused': reused}

    def _operation(self, db, row):
        if row is None:
            return None
        out = dict(row)
        latest_requested = db.execute('SELECT MAX(requested_target) FROM scan_operation_requests WHERE operation_id=?', (row['id'],)).fetchone()[0] or row['requested_target']
        out['latest_requested_target'] = latest_requested
        tasks = [self._task(db.execute('SELECT * FROM tasks WHERE id=?', (row[k],)).fetchone())
                 for k in ('scan_task_id', 'b_task_id')]
        states = [t['status'] if t else 'failed' for t in tasks]
        out['tasks'] = tasks
        out['demands']=self._operation_demands(db,row['id'])
        out['task_history_ids']=[r[0] for r in db.execute('SELECT task_id FROM scan_operation_tasks WHERE operation_id=?',(row['id'],))]
        out['status'] = ('running' if 'running' in states else 'queued' if 'queued' in states else
                         'success' if all(s == 'success' for s in states) else
                         'partial_success' if any(s in ('success','partial_success') for s in states) else 'failed')
        # Prefer the worker actually running over the other queued stage.
        out['current_task_id'] = next((t['id'] for status in ('running','queued') for t in tasks
                                       if t and t['status'] == status), None)
        out['finished_at'] = None if out['status'] in ACTIVE else max((t['finished_at'] or '' for t in tasks if t), default='')
        out['targets'] = [dict(task_id=t['id'], kind=t['kind'],
                              actual_target=t['request'].get('target_close_boundary'),
                              candle_close_boundary=(t['request'].get('target_close_boundary',row['requested_target']) // (14_400_000 if t['kind']=='scan' else 3_600_000)) * (14_400_000 if t['kind']=='scan' else 3_600_000),
                              lag_ms=max(0, latest_requested-t['request'].get('target_close_boundary',row['requested_target'])))
                          for t in tasks if t]
        for target in out['targets']:
            period=14_400_000 if target['kind']=='scan' else 3_600_000
            desired=latest_requested//period*period
            target['pending_range'] = dict(from_boundary=target['candle_close_boundary'],through_boundary=desired) if desired>target['candle_close_boundary'] else None
        out['coverage_pending'] = any(t['pending_range'] for t in out['targets'])
        if out['status']=='success' and out['coverage_pending']:
            out['status']='partial_success'
        return out

    def latest_operation(self):
        with self.lock, self._db() as db:
            return self._operation(db, db.execute('SELECT * FROM scan_operations ORDER BY rowid DESC LIMIT 1').fetchone())

    @protected
    def submit_unified(self, submission_key=None):
        # One transaction owns the parent identity, both children and their config.
        # The key also deduplicates a retried HTTP request after the operation ended.
        if submission_key is not None:
            import re
            if not isinstance(submission_key,str) or not re.fullmatch(r'[a-zA-Z0-9-]{16,80}',submission_key):
                raise ValueError('扫描请求标识无效')
        with self.lock, self._db() as db:
            settings=db.execute('SELECT * FROM settings WHERE id=1').fetchone()
            timing=migrate_timing(json.loads(settings['timing']) if settings['timing'] else None)
            boundary=target_close_boundary_ms(int(self.clock().timestamp()*1000),offset_sec=timing['closeOffsetSec'])
            known=db.execute('SELECT * FROM scan_operation_requests WHERE submission_key=?',(submission_key,)).fetchone() if submission_key else None
            prior = db.execute('SELECT * FROM scan_operations WHERE id=?',(known['operation_id'],)).fetchone() if known else None
            if known:boundary=known['requested_target']
            if prior is None:
                prior = db.execute("SELECT o.* FROM scan_operations o WHERE EXISTS(SELECT 1 FROM tasks t WHERE t.id IN (o.scan_task_id,o.b_task_id) AND t.status IN ('queued','running')) ORDER BY o.rowid DESC LIMIT 1").fetchone()
            reused = prior is not None
            if prior is None:
                sid,_=self._enqueue(db,'manual',settings['version'])
                bid,_=self._enqueue_b_observe(db,'manual_unified',settings['version'],boundary)
                b=db.execute('SELECT * FROM tasks WHERE id=?',(bid,)).fetchone()
                if b['status']=='queued':
                    req=json.loads(b['request']);observe=migrate_observe(json.loads(settings['observe']) if settings['observe'] else None)
                    req.update(observe={**observe,'enabled':True},timing=timing,execution_mode='manual_unified')
                    db.execute('UPDATE tasks SET request=? WHERE id=?',(json.dumps(req,ensure_ascii=False),bid))
                oid='scanop-'+uuid.uuid4().hex
                db.execute('INSERT INTO scan_operations VALUES(?,?,?,?,?,?)',(oid,iso(self.clock()),boundary,sid,bid,submission_key))
                prior=db.execute('SELECT * FROM scan_operations WHERE id=?',(oid,)).fetchone()
            db.execute('INSERT OR IGNORE INTO scan_operation_requests VALUES(?,?,?)',(submission_key or 'auto-'+uuid.uuid4().hex,prior['id'],boundary))
            if not known:
                for kind,column in (('scan','scan_task_id'),('b_observe_1h','b_task_id')):
                    req=dict(target_close_boundary=boundary)
                    if kind=='b_observe_1h':req.update(observe={**migrate_observe(json.loads(settings['observe']) if settings['observe'] else None),'enabled':True},timing=timing,execution_mode='manual_unified')
                    self._demand(db,'operation:'+prior['id'],kind,settings['version'],boundary,prior[column],prior['id'],req)
            operation=self._operation(db,prior)
        return dict(task=operation['tasks'][0],operation=operation,reused=reused,requested_target_close_boundary=boundary)

    def submit_range(self, payload):
        spec = self.ranges.prepare(payload)
        serialized = json.dumps(spec, sort_keys=True, ensure_ascii=False, allow_nan=False)
        request_hash = hashlib.sha256(serialized.encode()).hexdigest()
        with self.lock, self._db() as db:
            current = db.execute("SELECT id FROM tasks WHERE kind='candle_range' AND request_hash=? AND status IN ('queued','running')", (request_hash,)).fetchone()
            reused = bool(current)
            if current:
                task_id = current['id']
            else:
                task_id = 'task-' + uuid.uuid4().hex
                version = db.execute('SELECT version FROM settings WHERE id=1').fetchone()[0]
                db.execute("INSERT INTO tasks (id,kind,status,reason,created_at,progress,settings_version,request,request_hash) VALUES (?,'candle_range','queued','manual',?,?,?,?,?)",
                           (task_id, iso(self.clock()), json.dumps({'phase': '等待区间行情补查', 'completed': 0, 'total': len(spec['contracts']) * spec['expected_per_contract']}), version, serialized, request_hash))
        return {'task': self.task(task_id), 'reused': reused}

    def set_research(self, research):
        self.research = research

    def submit_research(self, payload):
        if self.research is None:
            raise ValueError("AI投研服务未初始化")
        if not isinstance(payload, dict):
            raise ValueError("请求体必须是对象")
        payload = dict(payload)
        # Idempotency: client retry keeps submission_id; independent confirm omits/new id
        submission_id = payload.get("submission_id")
        if submission_id is not None and not isinstance(submission_id, str):
            raise ValueError("submission_id必须是字符串")
        if isinstance(submission_id, str):
            submission_id = submission_id.strip() or None
        if not submission_id:
            submission_id = "sub-" + uuid.uuid4().hex
            payload["submission_id"] = submission_id
        # Stale preview guard: if client sends intent_hash from preview, it must still match
        preview_intent_hash = payload.pop("preview_intent_hash", None) or payload.pop("expected_intent_hash", None)
        with self.lock, self._db() as db:
            version = db.execute("SELECT version FROM settings WHERE id=1").fetchone()[0]
            payload["settings_version"] = version
            spec = self.research.prepare(payload)
            evidence = spec.pop("_evidence", None)
            kind = "scan_and_research" if spec.get("mode") == "scan_and_research" else "ai_research"
            intent_hash = spec.get("intent_hash")
            if preview_intent_hash and preview_intent_hash != intent_hash:
                raise Conflict(
                    "预览已失效（提示词/关注点/币池/快照/模型等已变更）；请重新预览后再确认"
                )
            # Ensure submission_id on frozen intent
            spec["submission_id"] = submission_id
            intent = dict(spec.get("intent") or {})
            intent["submission_id"] = submission_id
            intent["settings_version"] = version
            # Re-hash intent with settings_version bound (scan_and_research settings freeze)
            if hasattr(self.research, "_hash_material"):
                # settings_version participates in intent identity for scan path
                intent_for_hash = {k: v for k, v in intent.items() if k != "submission_id"}
                intent_hash = self.research._hash_material(intent_for_hash)
                intent["intent_hash"] = intent_hash
            spec["intent"] = intent
            spec["intent_hash"] = intent_hash
            spec["freeze_phase"] = "intent"
            spec["reuse_completed_report"] = False
            request_hash = spec.get("request_hash")  # tracing only
            existing = db.execute(
                "SELECT * FROM tasks WHERE submission_id=? AND kind IN ('ai_research','scan_and_research')",
                (submission_id,),
            ).fetchone()
            if existing:
                prev_intent = existing["intent_hash"]
                if prev_intent and intent_hash and prev_intent != intent_hash:
                    raise Conflict(
                        f"同一submission_id意图不一致（已绑定 {prev_intent[:12]}…，本次 {intent_hash[:12]}…）；拒绝"
                    )
                # Same id + same intent → return same task (idempotent retry)
                return {"task": self._task(existing), "reused": True, "submission_id": submission_id}
            task_id = "task-" + uuid.uuid4().hex
            spec["task_id"] = task_id
            serialized = json.dumps(spec, sort_keys=True, ensure_ascii=False, allow_nan=False)
            if kind == "scan_and_research":
                phase = {"phase": "等待扫描并投研", "completed": 0, "total": spec["selected_count"]}
            else:
                phase = {"phase": "等待AI投研", "completed": 0, "total": spec["selected_count"]}
            try:
                db.execute(
                    "INSERT INTO tasks (id,kind,status,reason,created_at,progress,settings_version,request,request_hash,submission_id,intent_hash,freeze_phase) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        task_id, kind, "queued", "manual", iso(self.clock()), json.dumps(phase),
                        version, serialized, request_hash, submission_id, intent_hash, "intent",
                    ),
                )
            except sqlite3.IntegrityError:
                # Concurrent retry with same submission_id
                raced = db.execute(
                    "SELECT * FROM tasks WHERE submission_id=? AND kind IN ('ai_research','scan_and_research')",
                    (submission_id,),
                ).fetchone()
                if not raced:
                    raise
                if raced["intent_hash"] and intent_hash and raced["intent_hash"] != intent_hash:
                    raise Conflict("同一submission_id意图不一致（并发）；拒绝")
                return {"task": self._task(raced), "reused": True, "submission_id": submission_id}
            # Persist freeze package for restart recovery (REV-02); credentials stay in secure store
            if hasattr(self.research, "persist_intent_freeze"):
                freeze_intent = {
                    **intent,
                    "submission_id": submission_id,
                    "intent_hash": intent_hash,
                    "freeze_phase": "intent",
                    "task_id": task_id,
                    "kind": kind,
                    "request_hash": request_hash,
                    "contracts": spec.get("contracts"),
                    "mode": spec.get("mode"),
                    "depth": spec.get("depth"),
                    "modelId": spec.get("modelId"),
                    "protocol": spec.get("protocol"),
                    "baseUrl": spec.get("baseUrl"),
                    "configId": spec.get("configId"),
                    "config_version": spec.get("config_version"),
                    "prompt_version": spec.get("prompt_version"),
                    "prompt_hash": spec.get("prompt_hash"),
                    "scan_id": spec.get("scan_id"),
                    "revision": spec.get("revision") if not spec.get("needs_scan") else "TBD",
                    "needs_scan": bool(spec.get("needs_scan")),
                }
                self.research.persist_intent_freeze(task_id, freeze_intent, evidence)
            if not hasattr(self, "_research_evidence"):
                self._research_evidence = {}
            self._research_evidence[task_id] = evidence
        return {"task": self.task(task_id), "reused": False, "submission_id": submission_id}

    def submit_batch_retry(self, batch_id, include_unknown=False):
        """R5 / CON-08: queue a retry of ONLY the failed groups of one batch (same confirm)."""
        if self.research is None:
            raise ValueError("AI投研服务未初始化")
        if not isinstance(include_unknown, bool):
            raise ValueError("include_unknown 须为布尔")
        self.research.get_batch(batch_id)  # KeyError → 404
        with self.lock, self._db() as db:
            active = db.execute("SELECT * FROM tasks WHERE kind='ai_research_batch_retry' AND status IN ('queued','running') AND request LIKE ?",
                                ('%' + batch_id + '%',)).fetchone()
            if active:
                return {"task": self._task(active), "reused": True}
            version = db.execute("SELECT version FROM settings WHERE id=1").fetchone()[0]
            task_id = "task-" + uuid.uuid4().hex
            db.execute("INSERT INTO tasks (id,kind,status,reason,created_at,progress,settings_version,request) VALUES (?,?,?,?,?,?,?,?)",
                       (task_id, 'ai_research_batch_retry', 'queued', 'manual', iso(self.clock()),
                        json.dumps({'phase': '等待重试失败组', 'completed': 0, 'total': 0}, ensure_ascii=False), version,
                        json.dumps({'batch_id': batch_id, 'include_unknown': include_unknown})))
        return {"task": self.task(task_id), "reused": False}

    def cancel(self, task_id):
        with self.lock, self._db() as db:
            task = db.execute('SELECT status FROM tasks WHERE id=?', (task_id,)).fetchone()
            if not task:
                raise KeyError(task_id)
            if task['status'] != 'queued':
                raise Conflict('仅排队任务可取消；运行中的数据任务不强制中断')
            db.execute("UPDATE tasks SET status='cancelled',finished_at=? WHERE id=?", (iso(self.clock()), task_id))
            db.execute("UPDATE scan_demands SET state='cancelled' WHERE task_id=?",(task_id,))
        return self.task(task_id)

    def tick(self):
        self.scheduler_heartbeat = iso(self.clock())
        self._tick_countdown()
        # Config changes activate only the bounded ABC worker, never a scan plan.
        # Its network phase reserves this service's original single data lane.
        try:
            from tracking.runtime import start as start_tracking_runtime
            start_tracking_runtime(self.root,host=self)
        except (OSError,ValueError,sqlite3.Error) as exc:
            # A broken new-runtime control must not stop the original scanner.
            self.subsystem_errors['tracking_runtime']=str(exc)[:220]
        with self.lock, self._db() as db:
            now = self.clock()
            row = db.execute('SELECT * FROM settings WHERE id=1').fetchone()
            config = json.loads(row['scan'])
            if config['enabled'] and row['next_due'] and dt.datetime.fromisoformat(row['next_due']) <= now:
                if not self.recovery_tick or config['recoverMissed']:
                    self._enqueue(db, 'recovery' if self.recovery_tick else 'scheduled', row['version'])
                # Skip the backlog instead of enqueueing every missed interval.
                db.execute('UPDATE settings SET next_due=? WHERE id=1', (next_scan_due(now,config['intervalHours'],json.loads(row['timing'] or '{}').get('closeOffsetSec',120)),))
            self.recovery_tick = False
            self._suspend_disabled_schedule_demands(db)
            self._consume_demands(db)
            # Research has its own bounded lane; one data lane prevents overlapping scans.
            # Claim all eligible work transactionally before starting any thread.
            launch = []
            research_kinds = ('ai_research', 'ai_research_batch_retry')
            for active_id, kind in list(self.active_tasks.items()):
                if kind in research_kinds and self.research:
                    progress = self.research.progress_for(active_id) if hasattr(self.research, 'progress_for') else {'phase': '投研执行中'}
                elif kind == 'scan':
                    progress = self.progress()
                else:
                    continue  # scan_and_research writes its phase using its own task ID
                db.execute("UPDATE tasks SET progress=? WHERE id=? AND status='running'", (json.dumps(progress, ensure_ascii=False, allow_nan=False), active_id))
            research_count = sum(k in research_kinds for k in self.active_tasks.values())
            data_busy = any(k not in research_kinds for k in self.active_tasks.values())
            for queued in db.execute("SELECT id,kind FROM tasks WHERE status='queued' ORDER BY created_at,rowid").fetchall():
                task_id, kind = queued['id'], queued['kind']
                is_research = kind in research_kinds
                if (is_research and research_count >= self.research_workers) or (not is_research and data_busy):
                    continue
                if self.stop_event.is_set():
                    break
                phase = '投研生成…' if is_research else '扫描行情…' if kind in ('scan', 'scan_and_research') else 'B观察1H…' if kind == 'b_observe_1h' else '证据缺口补采…' if kind=='b_evidence_repair' else '边界前市值归档…' if kind=='b_cap_refresh' else '准备区间补查'
                claimed = db.execute("UPDATE tasks SET status='running',started_at=?,progress=? WHERE id=? AND status='queued'",
                                     (iso(now), json.dumps({'phase': phase, 'completed': 0, 'total': 0}), task_id)).rowcount
                if claimed != 1:
                    continue
                self.active_tasks[task_id] = kind
                if is_research:
                    research_count += 1
                else:
                    data_busy = True
                launch.append(task_id)
            self.active_id = next(iter(self.active_tasks), None)  # compatibility for older clients/tests
            self.active_kind = self.active_tasks.get(self.active_id)
        # Commit claims first. Keep the registry until _run finishes, including DB publication.
        for task_id in launch:
            worker = threading.Thread(target=self._run, args=(task_id,), name='workbench-' + task_id, daemon=True)
            with self.lock:
                self.workers[task_id] = worker
                self.worker = worker
            worker.start()

        # B observe hour tick (default off)
        self._tick_b_observe()

        # EVAL/REVIEW schedule ticks (plans default off; no AI auto-bill)
        self._tick_eval_review()
        self._tick_evidence_repair()
        from collection_recovery import start_slice
        start_slice(self)
        if not self.active_tasks and not self.tasks_active():
            from cache_retention import daily_if_idle
            daily_if_idle(self.root,self.clock().timestamp())

    def _tick_evidence_repair(self):
        # This lane repairs already-discovered evidence; it never submits scan/B tasks.
        from b_repair_store import RepairStore
        from b_accumulation_store import BAccumulationStore
        now=int(self.clock().timestamp()*1000);queue=RepairStore(self.root)
        if not queue.enabled():return
        with self.lock,self._db() as db:
            if db.execute("SELECT 1 FROM tasks WHERE status IN ('queued','running') AND kind NOT IN ('ai_research','ai_research_batch_retry') LIMIT 1").fetchone():return
        if now>=self._repair_next_discovery:
            queue.discover(BAccumulationStore(self.root).list_events(full=False,revisions=False),now)
            from b_cap_history import import_local
            import_local(self.root,now)
            self._repair_next_discovery=now+5*60_000
        kind=None;settings=self.settings()
        boundary=(now//3600000+1)*3600000
        # When the user enables B observation, archive actual source caps BEFORE
        # each boundary. Plans remain off until the user changes them.
        if settings['observe']['enabled'] and now%3600000>=55*60_000:
            with queue.db() as db:last=db.execute("SELECT value FROM controls WHERE key='preclose_cap_boundary'").fetchone()
            if not last or int(last[0])!=boundary:kind='b_cap_refresh'
        if kind is None and queue.due(now):kind='b_evidence_repair'
        if kind is None:return
        with self.lock,self._db() as db:
            if db.execute("SELECT 1 FROM tasks WHERE status IN ('queued','running') AND kind NOT IN ('ai_research','ai_research_batch_retry') LIMIT 1").fetchone():return
            tid='task-'+uuid.uuid4().hex
            db.execute("INSERT INTO tasks(id,kind,status,reason,created_at,progress,settings_version,request) VALUES(?,?,'queued','evidence_gap_recovery',?,?,?,?)",(tid,kind,iso(self.clock()),json.dumps({'phase':'等待有界证据补采'}),settings['version'],json.dumps({'scope':'existing_evidence_only','target_close_boundary':boundary if kind=='b_cap_refresh' else None})))
        if kind=='b_cap_refresh':
            with queue.db(True) as db:db.execute("INSERT OR REPLACE INTO controls VALUES('preclose_cap_boundary',?)",(str(boundary),))

    def tasks_active(self):
        with self._db() as db:return db.execute("SELECT 1 FROM tasks WHERE status IN ('queued','running') UNION ALL SELECT 1 FROM scan_demands WHERE state='pending' LIMIT 1").fetchone() is not None

    def bind_eval_review(self, eval_store=None, review_store=None):
        """Wire EVAL/REVIEW stores and mark scheduler_bound on both."""
        if eval_store is not None:
            self.eval_store = eval_store
            try:
                eval_store.ranges = getattr(self, 'ranges', None)
                eval_store.task_service = self  # auto-enqueue candle_range when plan flag on
                eval_store.bind_scheduler(True)
            except Exception:
                pass
        if review_store is not None:
            self.review_store = review_store
            try:
                review_store.bind_scheduler(True)
            except Exception:
                pass


    def _enqueue_b_observe(self, db, reason, version, target_close_boundary):
        reuse = in_flight_reuse_key(target_close_boundary)
        active = db.execute(
            "SELECT * FROM tasks WHERE kind='b_observe_1h' AND status IN ('queued','running') ORDER BY created_at LIMIT 1",
            (),
        ).fetchone()
        if active:
            if reason=='scheduled_b_observe':
                req=dict(target_close_boundary=target_close_boundary,observe=migrate_observe(json.loads(db.execute('SELECT observe FROM settings WHERE id=1').fetchone()[0] or '{}')),timing=migrate_timing(json.loads(db.execute('SELECT timing FROM settings WHERE id=1').fetchone()[0] or '{}')),execution_mode=reason)
                self._demand(db,'schedule','b_observe_1h',version,target_close_boundary,active['id'],request=req)
            return active['id'], True
        task_id = 'task-' + uuid.uuid4().hex
        db.execute(
            "INSERT INTO tasks (id,kind,status,reason,created_at,progress,settings_version,request,request_hash) VALUES (?,?,?,?,?,?,?,?,?)",
            (task_id, 'b_observe_1h', 'queued', reason, iso(self.clock()),
             json.dumps({'phase': '等待B观察1H', 'completed': 0, 'total': 0}, ensure_ascii=False),
             version, json.dumps({'target_close_boundary': int(target_close_boundary), 'observe': migrate_observe(json.loads(db.execute('SELECT observe FROM settings WHERE id=1').fetchone()[0] or '{}')), 'timing': migrate_timing(json.loads(db.execute('SELECT timing FROM settings WHERE id=1').fetchone()[0] or '{}')), 'execution_mode': reason}), reuse),
        )
        if reason=='scheduled_b_observe':self._demand(db,'schedule','b_observe_1h',version,target_close_boundary,task_id)
        return task_id, False

    def _tick_b_observe(self):
        """观察默认关闭：不入队。开启后按 boundary+offset 对齐。"""
        with self.lock, self._db() as db:
            row = db.execute('SELECT * FROM settings WHERE id=1').fetchone()
            observe = migrate_observe(json.loads(row['observe']) if row['observe'] else None)
            timing = migrate_timing(json.loads(row['timing']) if row['timing'] else None)
            if not observe['enabled']:
                return
            now = self.clock()
            due = row['observe_next_due'] if 'observe_next_due' in row.keys() else None
            if due is None:
                due = next_observe_due_iso(now, offset_sec=timing['closeOffsetSec'])
                db.execute('UPDATE settings SET observe_next_due=? WHERE id=1', (due,))
                return
            if not should_fire(now, due, enabled=True):
                return
            now_ms = int(now.timestamp() * 1000)
            boundary = target_close_boundary_ms(now_ms, offset_sec=timing['closeOffsetSec'])
            self._enqueue_b_observe(db, 'scheduled_b_observe', row['version'], boundary)
            # 跳过积压：直接排下一小时
            nxt = next_observe_due_iso(now + dt.timedelta(seconds=timing['closeOffsetSec'] + 1), offset_sec=timing['closeOffsetSec'])
            # 更稳：边界+1h+offset
            nxt_ms = boundary + 3_600_000 + timing['closeOffsetSec'] * 1000
            nxt = dt.datetime.fromtimestamp(nxt_ms / 1000, tz=UTC).isoformat(timespec='microseconds')
            db.execute('UPDATE settings SET observe_next_due=? WHERE id=1', (nxt,))

    def _tick_eval_review(self):
        now = self.clock()
        if self.eval_store is not None:
            try:
                self.eval_store.scheduler_tick(now=now)
            except Exception as exc:  # noqa: BLE001
                # Visible but non-fatal to scan scheduler
                self.scheduler_error = f'eval_tick: {str(exc)[:220]}'
        if self.review_store is not None:
            try:
                self.review_store.scheduler_tick(now=now)
            except Exception as exc:  # noqa: BLE001
                self.scheduler_error = f'review_tick: {str(exc)[:220]}'

    def _run(self, task_id):
        task = self.task(task_id)
        kind = task['kind']
        policy=self.settings()['collection']
        guarded=kind in ('scan','b_observe_1h','candle_range','scan_and_research','b_evidence_repair','b_cap_refresh')
        gate_installed=False;telemetry_started=False;gate=None
        telemetry_kind=guarded
        try:
            if guarded:
                from request_gate import install
                install(task_id,lambda:self._collection_allowed(task),attempts=policy['maxHttpAttempts'],seconds=policy['deadlineSeconds'])
                gate_installed=True
                from request_telemetry import begin_task,end_task
                begin_task(task_id,kind,task['request'].get('target_close_boundary'))
                telemetry_started=True
            if self.research and hasattr(self.research,'bind_progress'):
                self.research.bind_progress(task_id)
            task = self.task(task_id)
            if task['kind'] == 'scan':
                import inspect
                runner_args = inspect.signature(self.runner).parameters
                result = self.runner(target_close_boundary=task['request'].get('target_close_boundary')) if 'target_close_boundary' in runner_args else self.runner()
                status = result.get('quality', {}).get('status', 'failed')
                if status not in ('success', 'partial_success'):
                    raise ValueError('采集没有返回通过覆盖门槛的已发布快照')
                output = {k: result.get(k) for k in ('scan_id', 'revision', 'available_at', 'finishedAt', 'received', 'total', 'quality', 'registryHash', 'verification', 'capCoverage', 'target_close_boundary')}
                progress = {'phase': '完成' if status == 'success' else '部分完成，缺口见扫描质量', 'completed': result.get('received', 0), 'total': result.get('total', 0)}
                error = None
                from b_repair_store import RepairStore
                try:RepairStore(self.root).record_caps(result.get('rows',[]),int(self.clock().timestamp()*1000))
                except Exception as exc:output['evidence_repair_registration_error']=str(exc)[:300]
            elif task['kind'] == 'candle_range':
                result = self.ranges.run(task['request'])
                status = result['status']
                output = {k: result[k] for k in ('range_id', 'available_at', 'known', 'expected', 'status')}
                progress = {'phase': '补查完成，缺口见归档' if status == 'partial_success' else '补查完成' if status == 'success' else '补查失败，保留证据',
                            'completed': result['known'], 'total': result['expected']}
                error = '没有取得可用的已收盘K线，请检查补查归档中的来源错误。' if status == 'failed' else None
            elif task['kind'] == 'b_observe_1h':
                from b_observe_runner import run_b_observe_round
                from b_observe_universe import fetch_universe, load_universe
                from b_public_adapter import PublicAdapters
                from scanner import safe
                settings = self.settings()
                observe = copy.deepcopy(task['request'].get('observe', settings['observe']))
                timing = copy.deepcopy(task['request'].get('timing', settings['timing']))
                adapter=PublicAdapters(self.root)
                universe_error=None
                try:symbols=fetch_universe(self.root,safe) if observe.get('enabled') else []
                except Exception as exc:
                    symbols=load_universe(self.root);universe_error=str(exc)[:300]
                    if not symbols:raise ValueError('交易目录不可得，覆盖未知: '+universe_error)
                now_ms = int(self.clock().timestamp() * 1000)
                def b_progress(done,total):
                    with self.lock,self._db() as db:
                        db.execute('UPDATE tasks SET progress=? WHERE id=?',(json.dumps(dict(phase='庄家吸筹1H · 采集与归档',completed=done,total=total,unit='symbols'),ensure_ascii=False),task_id))
                result = run_b_observe_round(
                    self.root,
                    symbols=symbols,
                    observe=observe,
                    timing=timing,
                    now_ms=now_ms,
                    fetch_1h=adapter.klines if observe.get('enabled') else None,
                    target_close_boundary=task['request'].get('target_close_boundary'),
                    fetch_background=adapter.background,fetch_evidence=adapter.evidence,
                    fetch_reference=adapter.reference,fetch_quote=adapter.quote,progress=b_progress,
                    round_metadata=dict(universe_fallback_reason=universe_error),request_count=lambda:adapter.requests+2,fetch_acknowledge=adapter.acknowledge,
                )
                if universe_error:
                    result['universe_fallback_reason']=universe_error
                    if result['status']=='success':result['status']='partial_success'
                from b_repair_store import RepairStore
                try:RepairStore(self.root).discover(__import__('b_accumulation_store').BAccumulationStore(self.root).list_events(full=False,revisions=False),now_ms)
                except Exception as exc:result['evidence_repair_registration_error']=str(exc)[:300]
                status = result.get('status','failed')
                output = result
                progress = {'phase': {'success':'B观察1H完成','partial_success':'B观察1H部分完成，缺口见轮次','failed':'B观察1H失败，保留上一轮','cancelled':'B观察关闭已跳过'}.get(status,'B观察1H状态未知'),
                            'completed': len(result.get('details',[]))+len(result.get('errors',[])),
                            'archived': (result.get('round_stats') or {}).get('monitored_symbols', 0),
                            'evaluated': (result.get('round_stats') or {}).get('evaluated_symbols', 0),
                            'total': result.get('symbols', 0), 'unit': 'symbols'}
                error = 'B观察本轮失败，详见轮次错误与覆盖缺口' if status=='failed' else None
            elif task['kind'] in ('b_evidence_repair','b_cap_refresh'):
                from b_evidence_repair import run_repairs,Budget
                from b_repair_store import RepairStore
                now_ms=int(self.clock().timestamp()*1000)
                if task['kind']=='b_evidence_repair':
                    output=run_repairs(self.root,now_ms)
                else:
                    from b_cap_history import refresh_current
                    from durable_io import read_bytes_shared
                    directory=self.root/'runtime/b-observe-universe.json'
                    items=json.loads(read_bytes_shared(directory)).get('symbols',[]) if directory.exists() else []
                    boundary=task['request'].get('target_close_boundary')
                    budget=Budget(max_requests=6,max_seconds=min(90,max(0,(boundary-now_ms)/1000)) if boundary else 90)
                    output=(dict(status='partial_success',recorded=0,reason='preclose_boundary_missed; 不使用收盘后值替代同期证据') if boundary and now_ms>=boundary else refresh_current(self.root,items,now_ms,budget,target_boundary=boundary,clock=lambda:int(self.clock().timestamp()*1000)))
                    output.update(requests=budget.calls,http_attempts=budget.calls,target_close_boundary=boundary)
                status=output['status'];error=None
                progress=dict(phase='证据补采完成，未解决项见缺口队列' if task['kind']=='b_evidence_repair' else '市值归档完成' if status=='success' else '市值归档部分完成，缺口见结果',completed=output.get('processed',output.get('recorded',0)),total=output.get('processed',output.get('eligible_contracts',0)))
            elif task['kind'] == 'ai_research':
                if self.research is None:
                    raise ValueError('AI投研服务未初始化')
                req = dict(task['request'])
                req['task_id'] = task_id
                req['submission_id'] = task.get('submission_id') or req.get('submission_id')
                req['reuse_completed_report'] = False
                ev = getattr(self, '_research_evidence', {}).pop(task_id, None)
                if ev is None and hasattr(self.research, 'load_freeze_evidence'):
                    ev = self.research.load_freeze_evidence(task_id)
                if ev is not None:
                    req['_evidence'] = ev
                else:
                    # recover minimal evidence from disk snapshot via prepare-like path
                    recovered = self.research.prepare({**req, 'mode': 'snapshot', 'contracts': req.get('contracts') or []})
                    # Keep confirm identity; do not invent a new submission
                    recovered['submission_id'] = req.get('submission_id')
                    recovered['task_id'] = task_id
                    recovered['reuse_completed_report'] = False
                    req = recovered
                # Mark freeze phase evidence before model
                with self.lock, self._db() as db:
                    db.execute(
                        "UPDATE tasks SET freeze_phase=? WHERE id=?",
                        ('evidence', task_id),
                    )
                result = self.research.run(req)
                status = result['status']
                output = {k: result[k] for k in ('report_id', 'available_at', 'contract_count', 'modelId', 'cache_key', 'batch_id', 'report_ids', 'batch_status', 'rejected_report_ids', 'validation_failed', 'error') if k in result}
                progress = {'phase': '完成' if status == 'success' else ('部分完成，失败币可只补失败' if status == 'partial_success' else '投研失败'), 'completed': result.get('contract_count', 0), 'total': result.get('contract_count', 0)}
                error = None if status != 'failed' else (result.get('error') or '没有通过校验的报告；原始输出和失败详情可查看')
            elif task['kind'] == 'ai_research_batch_retry':
                if self.research is None:
                    raise ValueError('AI投研服务未初始化')
                req = task['request']
                result = self.research.retry_batch_failed(req['batch_id'], include_unknown=bool(req.get('include_unknown')))
                status = result['status']
                output = {k: result[k] for k in ('report_id', 'contract_count', 'batch_id', 'report_ids', 'batch_status', 'retried_groups', 'rejected_report_ids', 'validation_failed', 'error') if k in result}
                progress = {'phase': '失败组重试完成', 'completed': result.get('contract_count', 0), 'total': result.get('contract_count', 0)}
                error = None if status != 'failed' else (result.get('error') or '重试后仍无通过校验的报告')
            elif task['kind'] == 'scan_and_research':
                if self.research is None:
                    raise ValueError('AI投研服务未初始化')
                frozen = dict(task['request'])
                frozen['task_id'] = task_id
                frozen['submission_id'] = task.get('submission_id') or frozen.get('submission_id')
                frozen['reuse_completed_report'] = False
                # Drop any stashed pre-scan evidence; must bind NEW snapshot only (REV-01)
                getattr(self, '_research_evidence', {}).pop(task_id, None)
                self._scan_research_phase = 'scan'
                with self.lock, self._db() as db:
                    db.execute(
                        "UPDATE tasks SET progress=? WHERE id=? AND status='running'",
                        (json.dumps({'phase': '扫描行情…', 'completed': 0, 'total': 0}, ensure_ascii=False), task_id),
                    )
                scan_result = self.runner()
                scan_status = (scan_result or {}).get('quality', {}).get('status', 'failed')
                if scan_status not in ('success', 'partial_success'):
                    # NEVER fall back to old snapshot for research
                    detail = None
                    if isinstance(scan_result, dict):
                        detail = (scan_result.get('quality') or {}).get('message') or scan_result.get('error')
                    raise ValueError(detail or '扫描未通过覆盖门槛，已停止投研；未使用旧快照')
                # DATA-01 deepen: expected/received + baseline field gate before research
                try:
                    from coverage_gate import assert_scan_research_gate
                    cov_gate = assert_scan_research_gate(self.root, scan_result)
                except Exception as exc:
                    raise ValueError(f'覆盖闸门不可用，已停止投研: {exc}') from exc
                if not cov_gate.get('ok'):
                    raise ValueError(
                        (cov_gate.get('reason') or '扫描后覆盖硬闸未通过')
                        + '；已停止投研，未使用旧快照'
                    )
                self._scan_research_phase = 'research'
                with self.lock, self._db() as db:
                    db.execute(
                        "UPDATE tasks SET progress=? WHERE id=? AND status='running'",
                        (json.dumps({
                            'phase': '投研生成…',
                            'completed': 0,
                            'total': frozen.get('selected_count') or len(frozen.get('contracts') or []),
                            'scan_id': scan_result.get('scan_id'),
                            'revision': scan_result.get('revision'),
                        }, ensure_ascii=False, allow_nan=False), task_id),
                    )
                # Bind newly published scan.json; request_hash uses NEW scan_id/revision B (not preview A)
                req = self.research.bind_snapshot(frozen)
                req['task_id'] = task_id
                req['submission_id'] = frozen.get('submission_id')
                req['reuse_completed_report'] = False
                # Confirm ID unchanged when revision B arrives
                with self.lock, self._db() as db:
                    # Persist updated request tracing hash; keep submission_id/intent_hash
                    updated = dict(frozen)
                    updated['bound_scan_id'] = req.get('scan_id')
                    updated['bound_revision'] = req.get('revision')
                    updated['freeze_phase'] = 'evidence'
                    db.execute(
                        "UPDATE tasks SET freeze_phase=?, request=? WHERE id=?",
                        (
                            'evidence',
                            json.dumps({k: v for k, v in req.items() if k != '_evidence'},
                                       sort_keys=True, ensure_ascii=False, allow_nan=False),
                            task_id,
                        ),
                    )
                if hasattr(self.research, 'persist_intent_freeze'):
                    # Keep intent file; evidence freeze written inside run()
                    intent_doc = self.research.load_intent_freeze(task_id) or {}
                    intent_doc['bound_scan_id'] = req.get('scan_id')
                    intent_doc['bound_revision'] = req.get('revision')
                    intent_doc['freeze_phase'] = 'evidence'
                    self.research.persist_intent_freeze(task_id, intent_doc, req.get('_evidence'))
                result = self.research.run(req)
                status = result['status']
                output = {k: result[k] for k in ('report_id', 'available_at', 'contract_count', 'modelId', 'cache_key', 'batch_id', 'report_ids', 'batch_status', 'rejected_report_ids', 'validation_failed', 'error') if k in result}
                if output is not None:
                    output['scan_id'] = scan_result.get('scan_id') or req.get('scan_id')
                    output['revision'] = scan_result.get('revision') if scan_result.get('revision') is not None else req.get('revision')
                    output['scan_status'] = scan_status
                progress = {'phase': '完成' if status == 'success' else '报告未全部通过校验，详情已保留', 'completed': result.get('contract_count', 0), 'total': result.get('contract_count', 0)}
                error = result.get('error') if status == 'failed' else None
            else:
                raise ValueError('未知任务类型')
        except Exception as exc:
            status, output, error = 'failed', None, str(exc)[:500]
            if kind == 'scan_and_research':
                progress = {'phase': '失败，未继续投研'}
            elif kind == 'ai_research':
                progress = {'phase': '投研失败'}
            else:
                progress = {'phase': '失败，保留上次成功快照'}
        finally:
            if gate_installed:
                from request_gate import release
                gate=release(task_id)
        finished_kind = kind
        if output is None:output={}
        if guarded:output['collection_budget']=gate
        if telemetry_started:output['http_attempts']=end_task(task_id)
        with self.lock, self._db() as db:
            db.execute('UPDATE tasks SET status=?,finished_at=?,result=?,error=?,progress=? WHERE id=?',
                       (status, iso(self.clock()), json.dumps(output, ensure_ascii=False, allow_nan=False) if output else None,
                        error, json.dumps(progress, ensure_ascii=False), task_id))
            self.active_tasks.pop(task_id, None)
            self.workers.pop(task_id, None)
            self.active_id = next(iter(self.active_tasks), None)
            self.active_kind = self.active_tasks.get(self.active_id)
        # V1 EVAL: candle_range done → auto re-settle linked open observations
        if finished_kind == 'candle_range' and getattr(self, 'eval_store', None) is not None:
            try:
                self.eval_store.on_candle_range_task_finished(task_id, status, output)
            except Exception as exc:  # noqa: BLE001
                self.scheduler_error = f'eval_resettle: {str(exc)[:220]}'

    def _collection_allowed(self,task):
        if self.stop_event.is_set():return False
        scheduled=task['reason'] in ('scheduled','scheduled_b_observe','recovery') or task['request'].get('demand_scope')=='schedule'
        if not scheduled:return True
        state=self.settings()
        return bool(state['scan']['enabled'] if task['kind']=='scan' else state['observe']['enabled'])

    def start(self):
        if self.thread and self.thread.is_alive():
            return
        self.thread = threading.Thread(target=self._loop, name='scan-scheduler', daemon=True)
        self.thread.start()

    def _loop(self):
        while not self.stop_event.is_set():
            try:
                self.tick()
                self.scheduler_error = None
            except Exception as exc:
                # A scheduler failure must be visible, not a success signal.
                self.scheduler_error = str(exc)[:300]
            self.stop_event.wait(1)

    def close(self):
        self.stop_event.set()
        if self.thread:
            self.thread.join(timeout=3)
        # Do not kill an in-flight paid request; bounded drain for local shutdown.
        with self.lock:
            workers = list(self.workers.values())
            if self._collection_worker is not None:
                workers.append(self._collection_worker)
        for worker in workers:
            worker.join(timeout=3)
