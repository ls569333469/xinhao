"""Persistent one-shot scan test. Uses the original fixed-target data lane."""
import datetime as dt
import json
import uuid


class CountdownSchedule:
    def _countdown_schema(self, db):
        db.execute('''CREATE TABLE IF NOT EXISTS scan_countdown(
            id TEXT PRIMARY KEY,created_at TEXT NOT NULL,due_at TEXT NOT NULL,
            state TEXT NOT NULL,task_ids TEXT NOT NULL DEFAULT '[]',error TEXT)''')

    def countdown(self):
        with self.lock, self._db() as db:
            row = db.execute('SELECT * FROM scan_countdown ORDER BY rowid DESC LIMIT 1').fetchone()
            if not row:
                return None
            out = dict(row)
            out['task_ids'] = json.loads(out['task_ids'])
            demands=[dict(r) for r in db.execute('SELECT kind,desired,task_id,state FROM scan_demands WHERE scope=? ORDER BY kind',(out['id'],))]
            out['demands']=demands
            if demands:out['task_ids']=[d['task_id'] for d in demands]
            out['tasks'] = [dict(r) for ident in out['task_ids'] for r in db.execute(
                'SELECT id,kind,status,progress,error FROM tasks WHERE id=?', (ident,))]
        for task in out['tasks']:
            task['progress'] = json.loads(task['progress'])
        if out['state'] == 'submitted':
            states = {t['status'] for t in out['tasks']}
            unresolved=any(d['state']=='pending' for d in out['demands'])
            if unresolved and not states & {'queued','running'}:states.add('queued')
            if len(out['tasks'])!=2:states.add('failed')
            out['state'] = ('running' if 'running' in states else 'queued' if 'queued' in states
                else 'failed' if states & {'failed', 'cancelled'} else 'partial_success'
                if 'partial_success' in states else 'success')
        out['remaining_seconds'] = max(0, int((dt.datetime.fromisoformat(out['due_at']) - self.clock()).total_seconds()))
        out['server_at'] = self.clock().isoformat()
        return out

    def arm_countdown(self, *, minutes=15):
        if type(minutes) is not int or minutes != 15:
            raise ValueError('测试倒计时固定15分钟，不更改正式扫描周期')
        with self.lock, self._db() as db:
            old = db.execute("SELECT id FROM scan_countdown c WHERE state='armed' OR (state='submitted' AND EXISTS(SELECT 1 FROM scan_demands d WHERE d.scope=c.id AND d.state='pending')) LIMIT 1").fetchone()
            if not old:
                now = self.clock()
                db.execute('INSERT INTO scan_countdown(id,created_at,due_at,state) VALUES(?,?,?,?)',
                    ('countdown-' + uuid.uuid4().hex, now.isoformat(),
                     (now + dt.timedelta(minutes=minutes)).isoformat(), 'armed'))
        return self.countdown()

    def cancel_countdown(self):
        with self.lock, self._db() as db:
            db.execute("UPDATE scan_countdown SET state='cancelled' WHERE state='armed'")
        return self.countdown()

    def _tick_countdown(self):
        # All changes commit with both enqueues: a crash cannot fire half a pair.
        from observe_config import migrate_observe, migrate_timing, target_close_boundary_ms
        with self.lock, self._db() as db:
            row = db.execute("SELECT * FROM scan_countdown WHERE state='armed' ORDER BY rowid LIMIT 1").fetchone()
            if not row or dt.datetime.fromisoformat(row['due_at'])>self.clock():
                return
            setting = db.execute('SELECT * FROM settings WHERE id=1').fetchone()
            timing = migrate_timing(json.loads(setting['timing'] or '{}'))
            boundary = target_close_boundary_ms(int(self.clock().timestamp()*1000), offset_sec=timing['closeOffsetSec'])
            sid, _ = self._enqueue(db, 'test_countdown', setting['version'])
            bid, reused = self._enqueue_b_observe(db, 'test_countdown', setting['version'], boundary)
            if not reused:
                req = json.loads(db.execute('SELECT request FROM tasks WHERE id=?', (bid,)).fetchone()[0])
                req['observe'] = {**migrate_observe(json.loads(setting['observe'] or '{}')), 'enabled': True}
                db.execute('UPDATE tasks SET request=? WHERE id=?', (json.dumps(req), bid))
            for kind, ident in [('scan', sid), ('b_observe_1h', bid)]:
                req = json.loads(db.execute('SELECT request FROM tasks WHERE id=?', (ident,)).fetchone()[0])
                req['target_close_boundary'] = boundary
                if kind=='b_observe_1h':req['observe']={**migrate_observe(json.loads(setting['observe'] or '{}')), 'enabled': True}
                self._demand(db, row['id'], kind, setting['version'], boundary, ident, request=req)
            db.execute("UPDATE scan_countdown SET state='submitted',task_ids=? WHERE id=? AND state='armed'",
                       (json.dumps([sid, bid]), row['id']))
