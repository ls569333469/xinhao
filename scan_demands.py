"""Durable fixed-boundary demands; the existing single data lane is the consumer.

Only explicit submissions/enabled schedules create demands. Wall time alone never
authorizes more work. Old task requests/results are immutable after submission.
"""
import json,uuid

class ScanDemands:
    def _init_demands(self,db):
        db.executescript('''
          CREATE TABLE IF NOT EXISTS scan_demands(
            scope TEXT,kind TEXT,version INTEGER,desired INTEGER,request TEXT,
            task_id TEXT,state TEXT,operation_id TEXT,PRIMARY KEY(scope,kind,version));
          CREATE TABLE IF NOT EXISTS scan_operation_tasks(
            operation_id TEXT,task_id TEXT,PRIMARY KEY(operation_id,task_id));
        ''')
        # Preserve interrupted task identity; resume only already authorized work.
        db.execute("UPDATE scan_demands SET state='pending' WHERE task_id IN (SELECT id FROM tasks WHERE status IN ('queued','running'))")

    def _demand(self,db,scope,kind,version,boundary,task_id,operation_id=None,request=None):
        period=14_400_000 if kind=='scan' else 3_600_000
        desired=int(boundary)//period*period
        task=db.execute('SELECT request FROM tasks WHERE id=?',(task_id,)).fetchone()
        req=request or json.loads(task['request'])
        db.execute('''INSERT INTO scan_demands VALUES(?,?,?,?,?,?,'pending',?)
          ON CONFLICT(scope,kind,version) DO UPDATE SET desired=MAX(desired,excluded.desired),request=excluded.request,state='pending' ''',
          (scope,kind,version,desired,json.dumps(req,ensure_ascii=False),task_id,operation_id))
        if operation_id:db.execute('INSERT OR IGNORE INTO scan_operation_tasks VALUES(?,?)',(operation_id,task_id))

    def _suspend_disabled_schedule_demands(self,db):
        settings=db.execute('SELECT version,scan,observe FROM settings WHERE id=1').fetchone()
        # Keep old intent/evidence, but do not resurrect a replaced schedule's
        # queued work after the user saves a newer configuration.
        db.execute("UPDATE scan_demands SET state='superseded' WHERE scope='schedule' AND version<>? AND state IN ('pending','paused')",(settings['version'],))
        db.execute("UPDATE tasks SET status='cancelled',finished_at=?,error='旧计划已被新配置替代，目标记录保留' WHERE status='queued' AND settings_version<>? AND (reason IN ('scheduled','scheduled_b_observe','recovery') OR (reason='boundary_resume' AND json_extract(request,'$.demand_scope')='schedule'))",(self.clock().isoformat(),settings['version']))
        allowed={'scan':bool(json.loads(settings['scan'])['enabled']),
                 'b_observe_1h':bool(json.loads(settings['observe'] or '{}').get('enabled'))}
        for kind,enabled in allowed.items():
            if enabled:
                db.execute("UPDATE scan_demands SET state='pending' WHERE scope='schedule' AND kind=? AND state='paused'",(kind,))
                continue
            db.execute("UPDATE scan_demands SET state='paused' WHERE scope='schedule' AND kind=? AND state='pending'",(kind,))
            db.execute("UPDATE tasks SET status='cancelled',finished_at=?,error='计划已暂停，目标保留待续接' WHERE kind=? AND status='queued' AND reason IN ('scheduled','scheduled_b_observe','recovery','boundary_resume') AND (reason<>'boundary_resume' OR json_extract(request,'$.demand_scope')='schedule')",(self.clock().isoformat(),kind))

    def _consume_demands(self,db):
        for demand in db.execute("SELECT * FROM scan_demands WHERE state='pending' ORDER BY rowid").fetchall():
            old=db.execute('SELECT * FROM tasks WHERE id=?',(demand['task_id'],)).fetchone()
            period=14_400_000 if demand['kind']=='scan' else 3_600_000
            actual=int(json.loads(old['request']).get('target_close_boundary',0))//period*period if old else 0
            if old and old['status'] in ('queued','running'):continue
            interrupted=bool(old and old['error'] and old['error'].startswith('服务中断'))
            continuation=bool(old and old['result'] and json.loads(old['result']).get('continuation_required'))
            if old and actual>=demand['desired'] and old['settings_version']==demand['version'] and not interrupted and not continuation:
                db.execute("UPDATE scan_demands SET state=? WHERE scope=? AND kind=? AND version=?",
                    ('done' if old['status']=='success' else 'incomplete',demand['scope'],demand['kind'],demand['version']))
                continue
            # One active task per kind; merge only the same frozen configuration.
            active=db.execute("SELECT * FROM tasks WHERE kind=? AND status IN ('queued','running') ORDER BY rowid LIMIT 1",(demand['kind'],)).fetchone()
            if active:
                active_target=int(json.loads(active['request']).get('target_close_boundary',0))//period*period
                if active['settings_version']==demand['version'] and active_target>=demand['desired']:self._bind_demand(db,demand,active['id'])
                continue
            reusable=db.execute("SELECT * FROM tasks WHERE kind=? AND settings_version=? AND reason='boundary_resume' AND status IN ('success','partial_success') AND COALESCE(json_extract(result,'$.continuation_required'),0)=0 AND json_extract(request,'$.target_close_boundary')=? ORDER BY rowid DESC LIMIT 1",(demand['kind'],demand['version'],demand['desired'])).fetchone()
            if reusable:self._bind_demand(db,demand,reusable['id']);continue
            task_id='task-'+uuid.uuid4().hex;req=json.loads(demand['request'])
            req.update(target_close_boundary=demand['desired'],resumes_task_id=demand['task_id'],demand_scope=demand['scope'])
            db.execute("INSERT INTO tasks(id,kind,status,reason,created_at,progress,settings_version,request) VALUES(?,?,'queued','boundary_resume',?,?,?,?)",
                (task_id,demand['kind'],self.clock().isoformat(),json.dumps(dict(phase='已登记目标续接',completed=0,total=0)),demand['version'],json.dumps(req,ensure_ascii=False)))
            self._bind_demand(db,demand,task_id)

    def _bind_demand(self,db,demand,task_id):
        db.execute('UPDATE scan_demands SET task_id=? WHERE scope=? AND kind=? AND version=?',(task_id,demand['scope'],demand['kind'],demand['version']))
        if demand['operation_id']:
            column='scan_task_id' if demand['kind']=='scan' else 'b_task_id'
            db.execute('UPDATE scan_operations SET '+column+'=? WHERE id=?',(task_id,demand['operation_id']))
            db.execute('INSERT OR IGNORE INTO scan_operation_tasks VALUES(?,?)',(demand['operation_id'],task_id))

    def _operation_demands(self,db,operation_id):
        return [dict(r) for r in db.execute('SELECT kind,version,desired,task_id,state FROM scan_demands WHERE operation_id=?',(operation_id,))]
