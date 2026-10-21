import datetime as dt,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
from source_resume import resume_source,H
from task_service import TaskService

class Sources(unittest.TestCase):
    def test_independent_gap_and_revisions(self):
        target=1000*H;calls=[]
        def fetch(a,b):
            calls.append((a,b));return [dict(timestamp=t,value=1) for t in range(a,b+H,H)]
        oi=[dict(timestamp=t,value=1) for t in range(target-12*H,target-9*H,H)]
        oi.append(dict(timestamp=target,value=1))
        rows,rev,cursor=resume_source(oi,target=target,now_ms=target+120000,fetch=fetch)
        self.assertEqual(len(rows),13);self.assertFalse(cursor['gaps']);self.assertEqual(calls[0][0],target-9*H)
        self.assertEqual(cursor['archived_contiguous_through'],target)
        def changed(a,b):return [dict(timestamp=t,value=2) for t in range(a,b+H,H)]
        rows,rev,cursor=resume_source(rows,target=target,now_ms=target+180000,fetch=changed,prior=cursor)
        self.assertEqual(len(rev),2);self.assertEqual(len(rows),13)

    def test_empty_error_expired_and_budget_do_not_advance(self):
        target=1000*H;old=[dict(timestamp=0,value=1)]
        rows,_,cursor=resume_source(old,target=target,now_ms=target,fetch=lambda a,b:[],page_budget=1)
        self.assertEqual(rows,old);self.assertEqual(cursor['archived_contiguous_through'],0)
        self.assertEqual(cursor['gaps'][0]['status'],'unrecoverable');self.assertEqual(cursor['request_count'],1)

class Demands(unittest.TestCase):
    def test_cross_hour_consumed_and_interruption_recoverable_with_plans_off(self):
        with tempfile.TemporaryDirectory() as td:
            now=dt.datetime(2026,10,17,8,5,tzinfo=dt.timezone.utc)
            service=TaskService(Path(td),lambda:None,clock=lambda:now)
            first=service.submit_unified('first-request-1002')
            now+=dt.timedelta(hours=48)
            service.submit_unified('next-request-1002')
            with service._db() as db:
                db.execute("UPDATE tasks SET status='success',finished_at=?",(now.isoformat(),))
                service._consume_demands(db)
            op=service.latest_operation()
            self.assertEqual(len(service.tasks()),4);self.assertFalse(op['coverage_pending'])
            self.assertEqual(op['tasks'][1]['request']['target_close_boundary'],first['operation']['requested_target']+48*H)
            self.assertEqual(first['operation']['tasks'][1]['request']['target_close_boundary'],first['operation']['requested_target'])
            service.close();service=TaskService(Path(td),lambda:None,clock=lambda:now)
            old=service.latest_operation()['tasks'][1]
            self.assertEqual(old['status'],'failed')
            with service._db() as db:service._consume_demands(db)
            self.assertNotEqual(service.latest_operation()['tasks'][1]['id'],old['id'])
            self.assertFalse(service.settings()['observe']['enabled']);self.assertFalse(service.settings()['scan']['enabled'])
            service.close()

if __name__=='__main__':unittest.main()
