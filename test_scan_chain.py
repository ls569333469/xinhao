import datetime as dt
import io,json,pathlib,socket,sqlite3,tempfile,threading,unittest,urllib.error
from contextlib import closing
from unittest.mock import patch
from task_service import TaskService

class ChainTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=pathlib.Path(self.tmp.name)
        self.now=dt.datetime(2026,10,17,8,5,tzinfo=dt.timezone.utc)
        self.order=[];self.targets=[]
        def runner(target_close_boundary=None):
            self.order.append('scan');self.targets.append(target_close_boundary)
            return dict(scan_id='scan-fixture',received=1,total=1,quality=dict(status='success'),target_close_boundary=target_close_boundary)
        self.service=TaskService(self.root,runner,clock=lambda:self.now)
        self.universe=patch('b_observe_universe.fetch_universe',return_value=[dict(market='USDⓈ-M',symbol='TESTUSDT')]);self.universe.start()
        def observe(root,**kw):
            self.assertEqual(self.order[0],'scan');self.order.append('b');self.assertTrue(kw['observe']['enabled'])
            self.targets.append(kw['target_close_boundary']);kw['progress'](1,1)
            return dict(status='success',symbols=1,round_stats=dict(monitored_symbols=1),target_close_boundary=kw['target_close_boundary'])
        self.brunner=patch('b_observe_runner.run_b_observe_round',side_effect=observe);self.mock_b=self.brunner.start()
        self.cleanup=patch('cache_retention.daily_if_idle');self.cleanup.start()
    def tearDown(self):
        self.service.close();self.universe.stop();self.brunner.stop();self.cleanup.stop();self.tmp.cleanup()
    def finish(self):
        for _ in range(4):
            self.service.tick()
            for w in list(self.service.workers.values()):w.join(2)
            if not self.service.tasks_active():return
        self.fail('tasks did not terminate')
    def test_serial_both_execute_while_schedules_off(self):
        before=self.service.settings();result=self.service.submit_unified('first-request-key-0001');self.finish()
        self.assertEqual(self.order,['scan','b']);self.assertEqual(self.targets,[1792224000000]*2)
        self.assertEqual(self.service.latest_operation()['status'],'success')
        after=self.service.settings()
        for key in ('version','scan','observe','timing','next_due','observe_next_due'):self.assertEqual(after[key],before[key])
        again=self.service.submit_unified('first-request-key-0001');self.assertTrue(again['reused']);self.assertEqual(again['operation']['id'],result['operation']['id']);self.assertEqual(len(self.service.tasks()),2)
    def test_active_retry_cross_hour_reports_real_target(self):
        first=self.service.submit_unified('first-request-key-0002');self.now+=dt.timedelta(hours=1)
        again=self.service.submit_unified('second-request-key-0002')
        self.assertTrue(again['reused']);self.assertEqual(again['operation']['id'],first['operation']['id'])
        self.assertEqual(len(self.service.tasks()),2)
        # Existing operation keeps its original requested/actual target; reuse must
        # additionally expose the NEW request target without relabelling old work.
        self.assertEqual(again['operation']['targets'][1]['actual_target'],first['operation']['requested_target'])
        self.assertEqual(again['requested_target_close_boundary'],first['operation']['requested_target']+3600000)
        self.finish();again2=self.service.submit_unified('second-request-key-0002');self.assertEqual(again2['operation']['id'],first['operation']['id'])
        self.assertEqual(again2['operation']['status'],'success');self.assertFalse(again2['operation']['coverage_pending'])
        self.assertEqual(again2['operation']['targets'][1]['actual_target'],first['operation']['requested_target']+3600000)
        self.assertEqual(self.order,['scan','b','b']);self.assertEqual(len(self.service.tasks()),3)
    def test_b_failure_keeps_scan_result(self):
        self.mock_b.side_effect=RuntimeError('fixture B outage');self.service.submit_unified();self.finish()
        op=self.service.latest_operation();self.assertEqual(op['status'],'partial_success')
        self.assertEqual(op['tasks'][0]['result']['scan_id'],'scan-fixture');self.assertIn('fixture B outage',op['tasks'][1]['error'])
    def test_scan_failure_does_not_skip_b(self):
        self.service.runner=lambda:(_ for _ in ()).throw(RuntimeError('scan outage'))
        self.mock_b.side_effect=lambda *a,**kw:dict(status='success',symbols=1,round_stats=dict(monitored_symbols=1))
        self.service.submit_unified();self.finish();op=self.service.latest_operation()
        self.assertEqual(op['status'],'partial_success');self.assertEqual(op['tasks'][0]['status'],'failed');self.assertEqual(op['tasks'][1]['status'],'success')
    def test_restart_retains_group_and_prior_output(self):
        result=self.service.submit_unified();self.service.tick();self.service.worker.join(2)
        self.service.close();self.service=TaskService(self.root,lambda:None,clock=lambda:self.now)
        op=self.service.latest_operation();self.assertEqual(op['id'],result['operation']['id']);self.assertEqual(op['status'],'partial_success');self.assertEqual(op['tasks'][1]['status'],'failed');self.assertEqual(op['tasks'][0]['result']['scan_id'],'scan-fixture')
        self.assertFalse(self.service.settings()['observe']['enabled']);self.assertTrue(self.service.tasks_active())
    def test_transaction_rolls_back_if_second_enqueue_fails(self):
        with patch.object(self.service,'_enqueue_b_observe',side_effect=RuntimeError('DB fixture failure')):
            with self.assertRaises(RuntimeError):self.service.submit_unified()
        self.assertEqual(self.service.tasks(),[]);self.assertIsNone(self.service.latest_operation())
    def test_http_submission_query_and_completed_retry_share_identity(self):
        from http.server import ThreadingHTTPServer
        from server import handler_factory
        import urllib.request
        server=ThreadingHTTPServer(('127.0.0.1',0),None);host='127.0.0.1:'+str(server.server_port);url='http://'+host
        server.RequestHandlerClass=handler_factory(self.root,self.service,True,[host])
        thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        def submit():
            req=urllib.request.Request(url+'/api/scans',data=b'{}',headers={'Origin':url,'Content-Type':'application/json','Idempotency-Key':'http-submission-0001'},method='POST')
            with urllib.request.urlopen(req) as r:self.assertEqual(r.status,202);return json.load(r)
        try:
            first=submit();self.finish();retry=submit()
            self.assertEqual(first['operation']['id'],retry['operation']['id']);self.assertTrue(retry['reused'])
            with urllib.request.urlopen(url+'/api/tasks') as r:state=json.load(r)
            self.assertEqual(state['scan_operation']['status'],'success');self.assertEqual(len(state['tasks']),2)
            self.assertTrue(state['scan_operation']['tasks'][1]['request']['observe']['enabled'])
            self.assertFalse(self.service.settings()['observe']['enabled'])
        finally:server.shutdown();server.server_close();thread.join(2)
    def test_large_json_gzip_is_lossless_and_opt_out_supported(self):
        import gzip,urllib.request
        from http.server import ThreadingHTTPServer
        from server import handler_factory
        value=dict(rows=[dict(symbol='TESTUSDT',evidence='完整原始字段'*100) for _ in range(300)])
        server=ThreadingHTTPServer(('127.0.0.1',0),None);host='127.0.0.1:'+str(server.server_port)
        Base=handler_factory(self.root,self.service,True,[host])
        class Handler(Base):
            def do_GET(inner):inner.send_body(200,value)
        server.RequestHandlerClass=Handler
        thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        try:
            for encoding,compressed in [('gzip, deflate',True),('identity',False),('gzip;q=0',False)]:
                with urllib.request.urlopen(urllib.request.Request('http://'+host,headers={'Accept-Encoding':encoding})) as r:
                    raw=r.read();self.assertEqual(int(r.headers['Content-Length']),len(raw));self.assertEqual(r.headers.get('Content-Encoding')=='gzip',compressed)
                    self.assertEqual(json.loads(gzip.decompress(raw) if compressed else raw),value)
        finally:server.shutdown();server.server_close();thread.join(2)
    def test_concurrent_submissions_create_only_one_pair(self):
        with __import__('concurrent.futures').futures.ThreadPoolExecutor(max_workers=4) as pool:
            results=list(pool.map(lambda _:self.service.submit_unified(),range(8)))
        self.assertEqual(len(set(r['operation']['id'] for r in results)),1);self.assertEqual(len(self.service.tasks()),2)
    def test_real_b_runner_writes_round_and_ledger_without_network(self):
        self.brunner.stop()
        H=3600000;target=int(self.now.timestamp()*1000)//H*H
        rows=[[i,100,101,99,100,10,i+H-1,1000,1,1,1,0] for i in range(target-500*H,target,H)]
        from b_public_adapter import PublicAdapters
        with patch.object(PublicAdapters,'klines',return_value=rows),patch.object(PublicAdapters,'background',return_value=[]),patch.object(PublicAdapters,'evidence',return_value={}):
            self.service.submit_unified();self.finish()
        op=self.service.latest_operation();self.assertIn(op['tasks'][1]['status'],('success','partial_success'))
        last=json.loads((self.root/'runtime/b-observe-last-round.json').read_text(encoding='utf8'));self.assertEqual(last['target_close_boundary'],target)
        self.assertTrue((self.root/'runtime/b-observations.sqlite3').is_file())
        with closing(sqlite3.connect(self.root/'runtime/candle-cache/index.sqlite3')) as db:self.assertEqual(db.execute("select count(*) from bars where interval='1h'").fetchone()[0],500)
        self.assertFalse(self.service.settings()['observe']['enabled'])
        # tearDown may safely stop an already stopped patch.

class BoundaryAndPacingTests(unittest.TestCase):
    def test_old_target_limits_request_and_excludes_later_closed_bars(self):
        import scanner
        H=3600000;target=20000*4*H+H;start=target//(4*H)*4*H
        rows=[[t,100,101,99,100,10,t+4*H-1,1000,1,1,1,0] for t in range(start-1500*4*H,start+4*H,4*H)]
        params=[]
        def read(url,args):params.append(args);return rows,None
        row=scanner.fetch_contract(dict(symbol='TESTUSDT',baseAsset='TEST',quoteAsset='USDT',pair='TESTUSDT'), 'https://fapi.binance.com',{}, {}, {},only={'candles'},requester=read,count_progress=False,target_close_boundary=target)
        self.assertEqual(params[0]['limit'],1500);self.assertEqual(params[0]['endTime'],start)
        self.assertLess(row['candleAsOf'],target);self.assertTrue(all(k[6]<target for k in row['_candles']['4h']))
        self.assertEqual(row['ema4h'],[scanner.ema([float(k[4]) for k in row['_candles']['4h']],p) for p in (7,25,99)])
    def test_shared_binance_cooldown_no_immediate_retry(self):
        import scanner
        from request_pacer import RequestPacer
        pacer=RequestPacer(480,1);error=urllib.error.HTTPError('https://fapi.binance.com/x',429,'rate limited',{'Retry-After':'120'},io.BytesIO(b''))
        with patch.object(scanner,'BINANCE_PACER',pacer),patch.object(pacer,'wait') as wait,patch('urllib.request.urlopen',side_effect=error) as send:
            with self.assertRaisesRegex(RuntimeError,'shared cooldown 120s'):scanner.request('https://fapi.binance.com/x')
            self.assertEqual(send.call_count,1);self.assertEqual(wait.call_count,1);self.assertGreater(pacer._cooldown,0);self.assertEqual(pacer._interval,.25)

if __name__=='__main__':unittest.main()
