import copy,datetime as dt,hashlib,json,os,sqlite3,tempfile,threading,unittest
from pathlib import Path
from unittest.mock import patch
H=3600000;H4=4*H;BASE=1699999200000//H4*H4

def raw(t,q=10):return [t,100,101,99,100.5,1,t+H-1,q]
def bar4(i,c=100,q=10):return dict(open_ms=BASE+i*H4,o=c,h=c+1,l=c-1,c=c,q=q,q_unit='USD nominal quote')

class Integrated(unittest.TestCase):
 def setUp(self):self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name)
 def tearDown(self):self.tmp.cleanup()
 def ledger(self):
  from tracking.ledger import TrackingLedger
  return TrackingLedger(self.root/'isolated.sqlite3')
 def test_request_pause_and_shared_retry_budget(self):
  import request_gate as g
  allowed=[True]
  g.install('one',lambda:allowed[0],attempts=2,seconds=30)
  try:
   g.before_send();g.before_send()
   with self.assertRaises(g.CollectionStopped):g.before_send()
   allowed[0]=False
   with self.assertRaises(g.CollectionStopped):g.before_send(reserve=False)
  finally:self.assertEqual(g.release('one')['used'],2)
  self.assertIsNone(g.release('one'))
 def test_bundled_references_replay_and_deduplicate(self):
  from tracking.reference_cases import seed
  from tracking.case_library import replay,load
  rows=seed(self.root);self.assertEqual(len(rows),4)
  for row in rows:
   self.assertTrue(replay(self.root,row['case_id'])['ok'],row['name'])
   self.assertEqual(json.loads(load(self.root,row['case_id'])['provenance'])['kind'],'synthetic_reference')
  self.assertTrue(all(r.get('reused') for r in seed(self.root)))
 def test_guard_failure_releases_and_records_task(self):
  from task_service import TaskService
  import request_gate as g
  service=TaskService(self.root,lambda:(_ for _ in ()).throw(ValueError('runner failed')))
  try:
   task=service.submit()['task'];service.tick();service.worker.join(5)
   done=service.task(task['id']);self.assertEqual(done['status'],'failed');self.assertIn('collection_budget',done['result'])
   g.install('next',lambda:True,attempts=1,seconds=1);g.release('next')
  finally:service.close()
 def test_one_missing_hour_archive_original_ingest_replay_and_retention(self):
  from tracking.recovery import Recovery,recovered_inputs
  from tracking.recovery_control import create_grant
  from tracking.source_fetch import HistoricalAdapter
  from tracking.candle_handoff import deliver
  from b_1h_ingest import ingest_symbol_1h
  from market_candle_cache import MarketCandleCache
  from b_event_sqlite import SQLiteBStore
  from tracking.b_recovery import drain
  ledger=self.ledger();recovery=Recovery(ledger);cache=MarketCandleCache(self.root);SQLiteBStore(self.root,initialize=True)
  rows=[raw(BASE+i*H,80 if i==170 else 25 if i==171 else 10) for i in range(175)]
  target=BASE+175*H;missing=BASE+170*H
  ingest_symbol_1h(cache,market='USDⓈ-M',symbol='TESTUSDT',raw_klines=[r for r in rows if r[0]!=missing],target_close_boundary=target)
  from contextlib import closing
  with closing(cache._connect()) as db:before=set(tuple(r) for r in db.execute("SELECT market,symbol,interval,open_time,payload_hash FROM bars"))
  folder=self.root/'runtime/b-source'/hashlib.sha256('USDⓈ-M|TESTUSDT'.encode()).hexdigest();folder.mkdir(parents=True)
  four_end=target//H4*H4
  four=[]
  for i in range(1499):
   t=four_end-(1499-i)*H4;c=100+i*.1
   four.append([t,c,c+1,c-1,c,1,t+H4-1,10])
  (folder/'4h.json').write_text(json.dumps(dict(raw=four)))
  times=[missing+H,missing+2*H]
  evidence=dict(oi_samples=[dict(timestamp=t,sumOpenInterestValue=50) for t in times],lsr_samples=[dict(timestamp=t,longShortRatio=.5) for t in times],cap_samples=[dict(capObservedAt=t,capUsd=100,capTrusted=True) for t in times])
  (folder/'structure.json').write_text(json.dumps(evidence))
  end=missing+H;now=target+120000
  job=recovery.enqueue(source='candles_1h',market='USDT-M',symbol='TESTUSDT',timeframe='1h',start_ms=end,target_ms=end,provider='binance',endpoint='https://fapi.binance.com/fapi/v1/klines',period_ms=H,time_semantics='effective_at_ms',unit_identity='USD nominal quote',adapter_version='test-v1',quality_version='test-v1',clock_ms=now)
  recovery.set_enabled(True,clock_ms=now)
  grant=create_grant(recovery,operation_id='test',scope=dict(providers=['binance'],contracts=['USDT-M|TESTUSDT'],sources=['candles_1h'],start_ms=end,target_ms=end),starts_ms=now,expires_ms=now+100000,max_attempts=1)
  recovery.bind_grant(job,grant,clock_ms=now)
  calls=[]
  def request(url,args,**kw):calls.append(args);return dict(status=200,data=[rows[170]],http_attempts=1,retries=0)
  adapter=HistoricalAdapter(request=request,clock=lambda:now,contracts={('USDT-M','TESTUSDT'):dict(verified=True)})
  result=recovery.page(fetch=adapter,clock_ms=now,clock=lambda:now,grant_id=grant)
  self.assertEqual(result[0]['state'],'archived_awaiting_effect');self.assertEqual(calls[0]['interval'],'1h');self.assertEqual(calls[0]['startTime'],missing)
  version=result[0]['version'];a=deliver(self.root,ledger,job,version,clock_ms=now);self.assertTrue(a['delivered'])
  self.assertTrue(deliver(self.root,ledger,job,version,clock_ms=now)['reused'])
  with closing(cache._connect()) as db:after=set(tuple(r) for r in db.execute("SELECT market,symbol,interval,open_time,payload_hash FROM bars"))
  self.assertTrue(before<=after);self.assertEqual(len(after)-len(before),1)
  self.assertEqual(len(recovered_inputs(ledger,market='USDT-M',symbol='TESTUSDT',start_ms=end,target_ms=end)['candles_1h']),1)
  r=drain(self.root,ledger,clock_ms=now,limit=2)
  self.assertTrue(r['ok'],r)
  events=SQLiteBStore(self.root).list_events();self.assertTrue(events)
  self.assertEqual(events[0]['trigger_open_time'],missing)
  self.assertEqual(events[0]['technical_event'],'pass')
  self.assertEqual(events[0]['technical_confirmation'],'pass')
  self.assertEqual(events[0]['structure_gate'],'pass')
  self.assertNotEqual(events[0]['bg4h']['close_boundary'],events[0]['bg4h_h1']['close_boundary'])
  with recovery.transaction() as db:self.assertEqual(db.execute('SELECT COUNT(*) FROM recovery_effects').fetchone()[0],0)
 def test_empty_cache_generates_explicit_1h_warmup_need(self):
  from tracking.recovery_bridge import register_b_round
  ledger=self.ledger();target=BASE+200*H
  register_b_round(ledger,dict(round_id='empty',published_at=target+120000,target_close_boundary=target,details=[dict(market='USDⓈ-M',symbol='EMPTY',candle_coverage=dict(first_open=None,gaps=[]))]))
  jobs=ledger._read("SELECT * FROM tracking_recovery WHERE source='candles_1h'")
  self.assertEqual(len(jobs),1);self.assertEqual((jobs[0]['target_ms']-jobs[0]['start_ms'])//H+1,170)
 def test_fetch_failure_retains_deficit_for_automatic_recovery(self):
  from b_observe_runner import run_b_observe_round
  import tracking_service
  (self.root/'tracking-control.json').write_text('{"enabled":true,"profile_version":"tracking-observation-v1.1.0"}')
  def fail(*args,**kw):raise OSError('fixture timeout')
  t=BASE+200*H
  out=run_b_observe_round(self.root,symbols=[dict(market='USDⓈ-M',symbol='EMPTY')],observe=dict(enabled=True),timing={},now_ms=t+120000,target_close_boundary=t,fetch_1h=fail)
  self.assertEqual(out['status'],'failed');self.assertTrue(out['errors'])
  ledger=tracking_service.writer(self.root)
  self.assertTrue(ledger._read("SELECT job_key FROM tracking_recovery WHERE source='candles_1h'"))
 def test_directory_refresh_does_not_duplicate_same_fixed_acquisition(self):
  from tracking.recovery import Recovery
  from tracking.recovery_bridge import enqueue_verified
  from tracking.live_source import CONVERSION
  ledger=self.ledger();recovery=Recovery(ledger)
  meta=dict(verified=True,quote_asset='USDT',pair='TESTUSDT',valid_from_ms=BASE,valid_to_ms=BASE+500*H,identity_evidence='directory-a',oi_conversion=CONVERSION,history_policy=dict(window_ms=30*24*H,version='test'))
  registry={'USDT-M|TESTUSDT':meta}
  a=enqueue_verified(recovery,registry,contract='USDT-M|TESTUSDT',source='oi',start_ms=BASE+H,target_ms=BASE+2*H,clock_ms=BASE+3*H)
  registry['USDT-M|TESTUSDT']=dict(meta,valid_to_ms=BASE+600*H,identity_evidence='directory-b')
  b=enqueue_verified(recovery,registry,contract='USDT-M|TESTUSDT',source='oi',start_ms=BASE+H,target_ms=BASE+2*H,clock_ms=BASE+4*H)
  self.assertEqual(a,b)
 def test_asof_fences_and_reviewed_sample_dedup(self):
  from tracking import case_library as lib
  inp=dict(raw_bars=[dict(bar4(0),available_at_ms=BASE+H4),dict(bar4(1),available_at_ms=BASE+3*H4)],oi_samples=[dict(effective_at_ms=BASE+H4,available_at_ms=BASE+2*H4,unit='USD',value=1)])
  known=lib.asof_inputs(inp,BASE+2*H4,BASE+2*H4);self.assertEqual(len(known['raw_bars']),1)
  out=lib.capture(self.root,strategy='C',market='USDT-M',symbol='TEST',boundary_ms=BASE+H4,inputs=inp,provenance=dict(kind='synthetic_boundary'),clock_iso='2026-10-19T00:00:00Z')
  self.assertTrue(lib.capture(self.root,strategy='C',market='USDT-M',symbol='TEST',boundary_ms=BASE+H4,inputs=inp,provenance={},clock_iso='2026-10-19T00:00:00Z')['reused'])
  with self.assertRaises(ValueError):lib.replay(self.root,out['case_id'])
  expected=dict(steps=[dict(boundary_ms=BASE+H4,result=dict(count=1))]);lib.review(self.root,out['case_id'],expected=expected,basis='synthetic: closed-bar count independently counted')
  self.assertTrue(lib.replay(self.root,out['case_id'],engines={'C':lambda inputs,boundary:dict(count=len(inputs['raw_bars']))})['ok'])
  with self.assertRaises(ValueError):lib.review(self.root,out['case_id'],expected=dict(steps=[dict(boundary_ms=BASE+H4,result=1)]),basis='changed')
 def test_original_C_negative_replay_without_preseeded_events(self):
  from tracking import case_library as lib
  raw4=[bar4(i,100+i*.2) for i in range(122)];boundary=raw4[-1]['open_ms']+H4
  out=lib.capture(self.root,strategy='C',market='USDT-M',symbol='TEST',boundary_ms=boundary,inputs=dict(raw_bars=raw4,oi_samples=[],lsr_samples=[],caps=[]),provenance=dict(kind='synthetic_increasing_prices'),clock_iso='2026-10-19T00:00:00Z')
  expected=dict(steps=[dict(boundary_ms=boundary,result=dict(candidate=None,events=[],sessions=[]))])
  lib.review(self.root,out['case_id'],expected=expected,basis='RSI=100 in monotonic rise, RSI<25 false; no C0')
  self.assertTrue(lib.replay(self.root,out['case_id'])['ok']);self.assertTrue(lib.replay(self.root,out['case_id'])['ok'])
 def test_capture_failure_is_nonfatal(self):
  from tracking.case_library import capture_safe
  with patch('tracking.case_library.capture',side_effect=OSError('disk full')):
   self.assertEqual(capture_safe(self.root)['captured'],0)
 def test_self_release_authorization_pinned_hash(self):
  from tracking.release import approval
  path=self.root/'release.json';record=dict(manifest_sha256='a'*64,reviewer='Codex',verdict='PASS',authorization='user_authorized_1019_plan',checks_passed=True)
  path.write_text(json.dumps(record));self.assertEqual(approval(path,'a'*64)['verdict'],'PASS')
  with self.assertRaises(ValueError):approval(path,'b'*64)
  record['checks_passed']=False;path.write_text(json.dumps(record))
  with self.assertRaises(ValueError):approval(path,'a'*64)
 def test_data_root_refuses_empty_and_single_host(self):
  from runtime_paths import data_root,HostLease
  with patch.dict(os.environ,{'ABC_DATA_ROOT':str(self.root)}):
   with self.assertRaises(ValueError):data_root()
   (self.root/'runtime').mkdir();(self.root/'runtime/data-root.json').write_text('{"schema":"abc-data-root-v1"}')
   self.assertEqual(data_root(),self.root.resolve())
  with HostLease(self.root):
   with self.assertRaises(RuntimeError):
    with HostLease(self.root):pass
  with HostLease(self.root):pass

 def test_original_A_recomputes_and_does_not_reuse_confirmed_row(self):
  from tracking.case_replay import OriginalEngines
  engine=OriginalEngines(self.root,dict(contract='USDT-M|TESTUSDT',strategy='A'))
  boundary=BASE+122*H4
  inp=dict(row=dict(quote='USDT',signals=[True]*8,emaSqueezeStage=dict(confirmedEvents=[dict(fake=True)])),raw_bars=[bar4(i,100+i*.2) for i in range(122)])
  out=engine.evaluate(inp,boundary,boundary+1)
  self.assertEqual(out['events'],[]);self.assertIsNone(out['candidate']['confirmation_status'])

 def test_original_C_C0_T1_and_missing_fuel_are_distinct(self):
  from tracking.case_replay import OriginalEngines
  from tracking.case_library import asof_inputs
  raw4=[]
  for i in range(122):
   c=100 if i<119 else 90 if i==119 else 91
   raw4.append(dict(bar4(i,c,4 if i==120 else 1),o=c-.1))
  def samples(unit,value):return [dict(effective_at_ms=b['open_ms']+H4,available_at_ms=b['open_ms']+H4+1,unit=unit,value=value,trusted=True) for b in raw4]
  inp=dict(raw_bars=raw4,oi_samples=samples('USD',50),lsr_samples=samples('ratio',1),caps=samples('USD',100))
  engine=OriginalEngines(self.root/'positive',dict(contract='USDT-M|TESTUSDT',strategy='C'))
  for n,status in [(120,'waiting'),(121,'confirmed')]:
   t=BASE+n*H4;out=engine.evaluate(asof_inputs(inp,t,t+1),t,t+1)
   self.assertEqual(out['events'][-1]['technical_status'],status)
   self.assertEqual(out['events'][-1]['qualification_status'],'pass')
  no_oi=OriginalEngines(self.root/'missing',dict(contract='USDⓈ-M|TESTUSDT',strategy='C'))
  t=BASE+120*H4;other=asof_inputs(dict(inp,oi_samples=[]),t,t+1)
  out=no_oi.evaluate(other,t,t+1);self.assertFalse(any(e['technical_status']=='confirmed' for e in out['events']))
  state=json.loads(no_oi.ledger.get_cursor('c-controller|USDT-M|TESTUSDT|c-1015-v1.1'))
  self.assertEqual(state['last_c0']['oi_cap'],'unknown')

 def test_original_B_case_adapter_runs_producer_then_tracker(self):
  from tracking.case_replay import OriginalEngines
  t=BASE+171*H
  bars=[dict(open_time=BASE+i*H,o=100,h=101,l=99,c=100.5,q=80 if i==168 else 25 if i==169 else 10) for i in range(171)]
  end=t//H4*H4
  four=[[end-(1499-i)*H4,100+i*.1,102+i*.1,99+i*.1,100+i*.1,1,end-(1499-i-1)*H4-1,10] for i in range(1499)]
  engine=OriginalEngines(self.root,dict(contract='USDT-M|TESTUSDT',strategy='B'))
  inp=dict(bars_asc=bars,raw4h=four,evidence={})
  out=engine.evaluate(inp,t,t+1)
  self.assertTrue(out['events']);self.assertEqual(out['events'][-1]['technical_status'],'confirmed')
  self.assertEqual(out['events'][-1]['qualification_status'],'unknown')

 def test_saved_pause_blocks_next_scheduled_http_attempt(self):
  from task_service import TaskService
  service=TaskService(self.root,lambda:None)
  try:
   cfg=service.settings();cfg=service.save_settings(dict(version=cfg['version'],scan=dict(cfg['scan'],enabled=True)))
   task=dict(reason='scheduled',kind='scan',request={})
   self.assertTrue(service._collection_allowed(task))
   service.save_settings(dict(version=cfg['version'],scan=dict(cfg['scan'],enabled=False)))
   self.assertFalse(service._collection_allowed(task))
   self.assertTrue(service._collection_allowed(dict(task,reason='manual')))
   service.close();service=TaskService(self.root,lambda:None)
   self.assertFalse(service.settings()['scan']['enabled'])
  finally:service.close()

 def test_source_bad_candle_does_not_advance_archive(self):
  from tracking.recovery import Recovery
  from tracking.source_fetch import HistoricalAdapter
  from tracking.recovery_control import create_grant
  recovery=Recovery(self.ledger());now=BASE+100*H;boundary=BASE+H
  job=recovery.enqueue(source='candles_1h',market='USDT-M',symbol='BADUSDT',timeframe='1h',start_ms=boundary,target_ms=boundary,provider='binance',endpoint='https://fapi.binance.com/fapi/v1/klines',period_ms=H,time_semantics='effective_at_ms',unit_identity='USD nominal quote',adapter_version='test',quality_version='test',clock_ms=now)
  recovery.set_enabled(True,clock_ms=now)
  grant=create_grant(recovery,operation_id='bad',scope=dict(providers=['binance'],contracts=['USDT-M|BADUSDT'],sources=['candles_1h'],start_ms=boundary,target_ms=boundary),starts_ms=now,expires_ms=now+10000,max_attempts=1);recovery.bind_grant(job,grant,clock_ms=now)
  bad=raw(BASE);bad[2]=50
  adapter=HistoricalAdapter(request=lambda *a,**k:dict(status=200,data=[bad]),clock=lambda:now,contracts={('USDT-M','BADUSDT'):dict(verified=True)})
  result=recovery.page(fetch=adapter,clock_ms=now,clock=lambda:now,grant_id=grant)
  self.assertEqual(result[0]['error_code'],'invalid_candle_ohlc_or_boundary')
  with recovery.transaction() as db:
   self.assertEqual(db.execute('SELECT COUNT(*) FROM tracking_recovery_chunks').fetchone()[0],0)
   self.assertIsNone(db.execute('SELECT archived_continuous_ms FROM tracking_recovery WHERE job_key=?',(job,)).fetchone()[0])

 def test_replaced_schedule_is_retained_but_not_resumed(self):
  from task_service import TaskService
  service=TaskService(self.root,lambda:None)
  try:
   cfg=service.settings();old=service.save_settings(dict(version=cfg['version'],scan=dict(cfg['scan'],enabled=True)))
   with service.lock,service._db() as db:
    service._enqueue(db,'scheduled',old['version'])
   current=service.save_settings(dict(version=old['version'],scan=dict(old['scan'],enabled=True,intervalHours=4)))
   with service._db() as db:
    service._suspend_disabled_schedule_demands(db)
    stale=db.execute("SELECT state FROM scan_demands WHERE scope='schedule' AND version=?",(old['version'],)).fetchone()
    self.assertEqual(stale[0],'superseded')
    self.assertEqual(db.execute("SELECT status FROM tasks WHERE settings_version=?",(old['version'],)).fetchone()[0],'cancelled')
  finally:service.close()

 def test_collection_status_is_readonly_and_defaults_off(self):
  from collection_status import status
  before=list(self.root.rglob('*'));out=status(self.root)
  self.assertFalse(out['policy']['recoveryEnabled']);self.assertFalse(out['initialized'])
  self.assertEqual(list(self.root.rglob('*')),before)

 def test_exhausted_fixed_grant_does_not_starve_next_job(self):
  import time
  from task_service import TaskService
  import tracking_service
  from tracking.recovery import Recovery
  from tracking.recovery_control import create_grant
  from tracking.recovery_bridge import enqueue_verified
  from tracking.source_fetch import HistoricalAdapter
  from tracking.live_source import CONVERSION
  from collection_recovery import run
  (self.root/'tracking-control.json').write_text('{"enabled":true,"profile_version":"tracking-observation-v1.1.0"}')
  service=TaskService(self.root,lambda:None)
  try:
   cfg=service.settings();cfg=service.save_settings(dict(version=cfg['version'],scan=cfg['scan'],collection=dict(cfg['collection'],recoveryEnabled=True,recoveryAttempts=1)))
   at=int(time.time()*1000);t=at//H*H
   meta=dict(verified=True,quote_asset='USDT',identity_evidence='test-directory',valid_from_ms=t-H,valid_to_ms=at,oi_conversion=CONVERSION,history_policy=dict(window_ms=30*24*H,version='test'))
   registry={'USDT-M|AAUSDT':meta,'USDT-M|BBUSDT':meta}
   (self.root/'runtime/tracking-source-registry.json').write_text(json.dumps(dict(schema='tracking-source-registry-v3',contracts=registry)))
   rec=Recovery(tracking_service.writer(self.root));jobs=[enqueue_verified(rec,registry,contract=k,source='lsr',start_ms=t,target_ms=t,clock_ms=at) for k in registry]
   grant=create_grant(rec,operation_id='exhausted',scope=dict(providers=['binance'],contracts=['USDT-M|AAUSDT'],sources=['lsr'],start_ms=t,target_ms=t),starts_ms=at,expires_ms=at+100000,max_attempts=1)
   key=hashlib.sha256(json.dumps([cfg['version'],jobs[0]],sort_keys=True).encode()).hexdigest()
   with rec.transaction() as db:
    db.execute('CREATE TABLE collection_grants(scope_key TEXT PRIMARY KEY,grant_id TEXT NOT NULL)')
    db.execute('INSERT INTO collection_grants VALUES(?,?)',(key,grant));db.execute('UPDATE recovery_grants SET attempts_used=1 WHERE grant_id=?',(grant,))
    db.execute('UPDATE tracking_recovery SET last_scheduled_ms=-1 WHERE job_key=?',(jobs[0],))
   calls=[]
   def adapter(registry,clock,before_send):
    def fetch(url,args,**kw):calls.append(args['symbol']);return dict(status=200,data=[dict(timestamp=t,longShortRatio=.5)])
    return HistoricalAdapter(request=fetch,clock=lambda:int(time.time()*1000),contracts={('USDT-M',k.split('|')[1]):v for k,v in registry.items()})
   with patch('tracking.live_source.LiveHistoricalAdapter',side_effect=adapter):out=run(service,cfg['version'],cfg['collection'])
   self.assertEqual(calls,['BBUSDT'])
   with rec.transaction() as db:self.assertEqual(db.execute('SELECT COUNT(*) FROM recovery_grants WHERE operation_id=?',('exhausted',)).fetchone()[0],1)
  finally:service.close()

if __name__=='__main__':unittest.main()
