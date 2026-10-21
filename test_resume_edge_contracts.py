import datetime as dt,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
from task_service import TaskService
from b_observe_runner import background_revision_affects,H
from source_resume import resume_source

class BoundaryEdges(unittest.TestCase):
    def test_source_revision_remains_pending_until_event_commit_ack(self):
        from b_public_adapter import PublicAdapters
        import json,time
        target=(int(time.time()*1000)//H)*H
        def requester(url,params):
            field='sumOpenInterestValue' if 'openInterest' in url else 'longShortRatio'
            return [dict(timestamp=t,**{field:1}) for t in range(params['startTime'],params['endTime']+H,H)],None
        with tempfile.TemporaryDirectory() as td:
            adapter=PublicAdapters(Path(td),requester);item=dict(market='USDⓈ-M',symbol='TEST')
            a=adapter.evidence(item,target);self.assertTrue(a['changed_timestamps'])
            b=adapter.evidence(item,target);self.assertEqual(set(a['changed_timestamps']),set(b['changed_timestamps']))
            adapter.acknowledge(item,b)
            c=adapter.evidence(item,target);self.assertFalse(c['changed_timestamps'])

    def test_old_candle_revision_dirty_key_survives_failed_event_commit(self):
        from b_observe_runner import run_b_observe_round
        from market_candle_cache import MarketCandleCache
        from observe_config import OBSERVE_DEFAULT,TIMING_DEFAULT
        from b_process_order import process_bars_in_order
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);target=10000*H
            def fetch(m,s,start_time=None,end_time=None,limit=500):
                a=target-500*H if start_time is None else start_time
                return [[t,100,101,99,100,1,t+H-1,10] for t in range(a,min(end_time,a+500*H),H)]
            def run():return run_b_observe_round(root,symbols=[dict(market='USDⓈ-M',symbol='TEST',bg4h=dict(ema7=2,ema25=1))],observe={**OBSERVE_DEFAULT,'enabled':True},timing=TIMING_DEFAULT,now_ms=target+120000,target_close_boundary=target,fetch_1h=fetch,fetch_evidence=lambda *a:dict(changed_timestamps=[]))
            run();cache=MarketCandleCache(root);ot=target-200*H
            old=cache.get_bars(market='USDⓈ-M',symbol='TEST',interval='1h',start_open=ot,end_open=ot+H)[0]['payload']
            old['bar']['q']=90;cache.put_many([old])
            with patch('b_observe_runner.process_bars_in_order',side_effect=OSError('disk failed')):self.assertTrue(run()['errors'])
            self.assertIn(ot,cache.pending_replay('USDⓈ-M','TEST'))
            with patch('b_observe_runner.process_bars_in_order',wraps=process_bars_in_order) as process:self.assertFalse(run()['errors'])
            self.assertIn(ot,process.call_args.kwargs['changed_opens']);self.assertFalse(cache.pending_replay('USDⓈ-M','TEST'))

    def test_both_independent_background_windows_and_exact_outer_edges(self):
        c=10000*4*H
        self.assertFalse(background_revision_affects(c+H,c))
        self.assertTrue(background_revision_affects(c+2*H,c)) # h+1 first sees closed 4h
        self.assertTrue(background_revision_affects(c+1499*4*H-H,c)) # h still uses earliest bar
        self.assertFalse(background_revision_affects(c+1500*4*H-H,c))

    def test_completed_scan_demand_reopens_while_b_running_new_4h_requested(self):
        with tempfile.TemporaryDirectory() as td:
            now=dt.datetime(2026,10,17,8,5,tzinfo=dt.timezone.utc)
            s=TaskService(Path(td),lambda:None,clock=lambda:now)
            try:
                op=s.submit_unified('edge-request-1002-a')['operation']
                scan,b=op['tasks']
                with s._db() as db:
                    db.execute("UPDATE tasks SET status='success' WHERE id=?",(scan['id'],))
                    s._consume_demands(db)
                now+=dt.timedelta(hours=4)
                s.submit_unified('edge-request-1002-b')
                with s._db() as db:s._consume_demands(db)
                new=s.latest_operation()
                self.assertNotEqual(new['tasks'][0]['id'],scan['id'])
                self.assertEqual(new['tasks'][0]['request']['target_close_boundary'],op['requested_target']+4*H)
                self.assertEqual(new['tasks'][1]['id'],b['id'])
                self.assertEqual(b['request']['target_close_boundary'],op['requested_target'])
            finally:s.close()

    def test_cancelled_pending_demand_does_not_resurrect(self):
        with tempfile.TemporaryDirectory() as td:
            s=TaskService(Path(td),lambda:None)
            try:
                op=s.submit_unified('edge-cancel-request-1002')['operation']
                for t in op['tasks']:s.cancel(t['id'])
                with s._db() as db:s._consume_demands(db)
                self.assertFalse(s.tasks_active());self.assertEqual(len(s.tasks()),2)
            finally:s.close()

    def test_one_source_429_keeps_own_gap_other_source_can_advance(self):
        old=[dict(timestamp=100*H,value=1)]
        def limited(*args):raise RuntimeError('HTTP 429 retry-after 60')
        oi,_,oc=resume_source(old,target=103*H,now_ms=103*H,fetch=limited)
        lsr,_,lc=resume_source(old,target=103*H,now_ms=103*H,fetch=lambda a,b:[dict(timestamp=t,value=1) for t in range(a,b+H,H)])
        self.assertEqual(oc['archived_contiguous_through'],100*H);self.assertTrue(oc['errors'])
        self.assertEqual(lc['archived_contiguous_through'],103*H);self.assertEqual(len(oi),1);self.assertEqual(len(lsr),4)

if __name__=='__main__':unittest.main()
