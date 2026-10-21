"""Isolated scheduling behavior, temporary stores, no market requests."""
import datetime as dt
import json
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch
from task_service import TaskService

class CountdownTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.now=dt.datetime(2026,10,20,0,40,tzinfo=dt.timezone(dt.timedelta(hours=8)))
        self.service=TaskService(Path(self.tmp.name),lambda:{},clock=lambda:self.now)
    def tearDown(self):
        self.service.close();self.tmp.cleanup()
    def advance(self,**kw):self.now+=dt.timedelta(**kw)
    def fire(self):self.service._tick_countdown()
    def consume(self):
        with self.service.lock,self.service._db() as db:self.service._consume_demands(db)
    def finish(self,ident,status='success',result=None):
        with self.service._db() as db:
            db.execute('UPDATE tasks SET status=?,result=?,finished_at=? WHERE id=?',(status,json.dumps(result or {}),self.now.isoformat(),ident))
    def test_exact_15_minutes_one_shot_preserves_schedule(self):
        before=self.service.settings();armed=self.service.arm_countdown()
        self.assertEqual(armed['remaining_seconds'],900)
        self.advance(minutes=14,seconds=59);self.fire();self.assertEqual(self.service.tasks(),[])
        self.advance(seconds=1);self.fire();self.fire()
        tasks=self.service.tasks();self.assertEqual(len(tasks),2)
        self.assertEqual({x['kind'] for x in tasks},{'scan','b_observe_1h'})
        self.assertTrue(next(x for x in tasks if x['kind']=='b_observe_1h')['request']['observe']['enabled'])
        after=self.service.settings()
        for k in ('version','scan','observe','next_due','observe_next_due'):self.assertEqual(before[k],after[k])
    def test_concurrent_arm_and_restart_persists_due(self):
        from concurrent.futures import ThreadPoolExecutor
        with ThreadPoolExecutor(8) as pool:items=list(pool.map(lambda _:self.service.arm_countdown(),range(16)))
        self.assertEqual(len({x['id'] for x in items}),1)
        original=items[0];self.advance(minutes=5)
        self.service.close();self.service=TaskService(Path(self.tmp.name),lambda:{},clock=lambda:self.now)
        self.assertEqual(self.service.countdown()['due_at'],original['due_at'])
        self.assertEqual(self.service.countdown()['remaining_seconds'],600)
        self.advance(minutes=11);self.fire();self.assertEqual(len(self.service.tasks()),2)
    def test_cancel_survives_due_and_can_rearm(self):
        old=self.service.arm_countdown();self.assertEqual(self.service.cancel_countdown()['state'],'cancelled')
        self.advance(minutes=16);self.fire();self.assertEqual(self.service.tasks(),[])
        self.assertNotEqual(self.service.arm_countdown()['id'],old['id'])
    def test_invalid_duration_no_mutation(self):
        for minutes in (0,1,14,16,True,'15',None):
            with self.subTest(minutes=minutes),self.assertRaises(ValueError):self.service.arm_countdown(minutes=minutes)
        self.assertIsNone(self.service.countdown())
    def test_reuse_older_task_retains_target_until_continuation(self):
        old=self.service.submit()['task'];frozen=old['request'].copy()
        armed=self.service.arm_countdown();self.advance(hours=5);self.fire()
        self.assertEqual(self.service.task(old['id'])['request'],frozen)
        self.finish(old['id']);self.consume()
        value=self.service.countdown();new=next(t for t in value['tasks'] if t['kind']=='scan')
        self.assertNotEqual(new['id'],old['id']);self.assertEqual(new['status'],'queued')
        self.assertEqual(value['state'],'queued');self.assertEqual(value['id'],armed['id'])
    def test_incomplete_target_continuation_is_not_reported_finished(self):
        self.service.arm_countdown();self.advance(minutes=15);self.fire()
        first=self.service.countdown()
        for t in first['tasks']:self.finish(t['id'],result={'continuation_required':True})
        self.assertEqual(self.service.countdown()['state'],'queued')
        self.consume()
        latest=self.service.countdown();self.assertEqual(latest['state'],'queued')
        self.assertTrue(set(first['task_ids']).isdisjoint(latest['task_ids']))
    def test_success_partial_failure_and_no_duplicate_arm_while_running(self):
        ident=self.service.arm_countdown()['id'];self.advance(minutes=15);self.fire()
        self.assertEqual(self.service.arm_countdown()['id'],ident)
        ts=self.service.countdown()['tasks'];self.finish(ts[0]['id']);self.finish(ts[1]['id'],'partial_success');self.consume()
        self.assertEqual(self.service.countdown()['state'],'partial_success')
        self.assertNotEqual(self.service.arm_countdown()['id'],ident)
        self.advance(minutes=15);self.fire()
        for t in self.service.countdown()['tasks']:self.finish(t['id'],'failed')
        self.consume();self.assertEqual(self.service.countdown()['state'],'failed')
    def test_cancel_after_dispatch_does_not_mutate_enqueued_tasks(self):
        self.service.arm_countdown();self.advance(minutes=15);self.fire()
        before=self.service.countdown();self.service.cancel_countdown()
        self.assertEqual(self.service.countdown()['state'],'queued')
        self.assertEqual(self.service.countdown()['task_ids'],before['task_ids'])
    def test_recovery_worker_does_not_block_countdown_registration(self):
        import collection_recovery
        started=threading.Event();release=threading.Event()
        def blocked(_):started.set();release.wait(3);return {'deferred':True}
        with patch('collection_recovery.tick',blocked):
            collection_recovery.start_slice(self.service);self.assertTrue(started.wait(1))
            original=self.service._collection_worker
            collection_recovery.start_slice(self.service);self.assertIs(original,self.service._collection_worker)
            self.service.arm_countdown();self.advance(minutes=15)
            t=time.monotonic();self.fire();self.assertLess(time.monotonic()-t,1)
            self.assertEqual(len(self.service.tasks()),2)
            self.assertIsNotNone(self.service.settings()['test_countdown'])
            release.set();original.join(2)
    def test_same_boundary_formal_schedules_and_timer_do_not_duplicate(self):
        config=self.service.settings()
        config=self.service.save_settings({'version':config['version'],'scan':{**config['scan'],'enabled':True,'intervalHours':4},'observe':{**config['observe'],'enabled':True}})
        self.now=self.now.replace(hour=3,minute=47);self.service.arm_countdown();self.advance(minutes=15);self.fire()
        with self.service._db() as db:self.service._enqueue(db,'scheduled',config['version'])
        self.service._tick_b_observe();self.assertEqual(len(self.service.tasks()),2)
    def test_registration_pair_rolls_back_on_second_enqueue_failure(self):
        self.service.arm_countdown();self.advance(minutes=15)
        with patch.object(self.service,'_enqueue_b_observe',side_effect=RuntimeError('injected')):
            with self.assertRaises(RuntimeError):self.fire()
        self.assertEqual(self.service.tasks(),[]);self.assertEqual(self.service.countdown()['state'],'armed')
        self.fire();self.assertEqual(len(self.service.tasks()),2)

if __name__=='__main__':unittest.main()
