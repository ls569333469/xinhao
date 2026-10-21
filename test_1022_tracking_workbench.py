from contextlib import closing
import json
import hashlib
import sqlite3
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch
from tracking import workbench as w

class WorkbenchRead(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.root=Path(self.tmp.name);(self.root/'runtime').mkdir()
  self.path=self.root/'runtime/signal-tracking.sqlite3'
  with closing(sqlite3.connect(self.path)) as db, db:
   db.executescript('''CREATE TABLE meta(key TEXT PRIMARY KEY,value TEXT);
    INSERT INTO meta VALUES('schema','s2-isolated-tracking-ledger-v1.5-fixture');
    CREATE TABLE tracking_commits(commit_seq INTEGER);INSERT INTO tracking_commits VALUES(1);
    CREATE TABLE sessions(session_id TEXT);
    CREATE TABLE event_revisions(event_id TEXT,revision INTEGER,payload_json TEXT);
    CREATE TABLE tracking_summary_versions(event_id TEXT,market TEXT,symbol TEXT,decision_close_ms INTEGER,summary_json TEXT,valid_from_seq INTEGER,valid_to_seq INTEGER);
    CREATE TABLE tracking_effective_members(event_id TEXT,valid_from_seq INTEGER,valid_to_seq INTEGER,page_seq INTEGER,payload_json TEXT);
    CREATE TABLE gaps(session_id TEXT,gap_id TEXT,scope TEXT,status TEXT,detail_json TEXT);''')
  self.add('a1','JCTUSDT','observing',trigger=2*w.H4)
  self.add('a2','JCTUSDT','ended',trigger=w.H4)
  self.add('coin','JCTUSD_PERP','observing',market='COIN-M')
  self.write_scan()
 def write_scan(self):
  (self.root/'scan.json').write_text(json.dumps({'rows':[dict(market='USDⓈ-M',symbol='JCTUSDT',asset='JCT',quote='USDT',price=2,priceChange24Pct=5,candleAsOf=3*w.H4-1,
      ema4h=[3,2,1],emaObservation={'recent_long_cross_events':[dict(kind='ema7_cross_25',bar_time=2*w.H4-1,ema=[3,2,1],volume_ratio=3)]})]}),encoding='utf-8')
 def add(self,eid,symbol,status,market='USDT-M',trigger=None):
  trigger=trigger or w.H4
  summary=dict(event_id=eid,market=market,symbol=symbol,strategy='A',session_id=eid+'s',decision_close_ms=trigger,
    context={'cross_bar':{'open_ms':trigger-w.H4,'c':1}},observation={'status':status,'last_evaluated_bar_close_ms':trigger,'end_reason':'structure_invalid' if status=='ended' else None})
  record=dict(event_id=eid,event_kind='a_medium_cross',trigger_bar_close_ms=trigger,timeframe='4h')
  with closing(sqlite3.connect(self.path)) as db, db:
   db.execute('INSERT INTO event_revisions VALUES(?,1,?)',(eid,json.dumps(record)))
   db.execute('INSERT INTO tracking_summary_versions VALUES(?,?,?,?,?,1,NULL)',(eid,market,symbol,trigger,json.dumps(summary)))
 def page(self,**q):return w.route(self.root,'/api/tracking/workbench/list?'+__import__('urllib.parse',fromlist=['urlencode']).urlencode(q))
 def test_default_members_grouping_and_separate_markets(self):
  data,code=self.page();self.assertEqual(code,200);self.assertEqual(data['total'],2)
  j=next(r for r in data['rows'] if r['symbol']=='JCTUSDT');self.assertEqual(j['event_count'],2);self.assertEqual(j['active_count'],1)
  self.assertEqual(j['return_pct'],100);self.assertEqual(j['representative']['technical_grade'],'strong')
  self.assertEqual(j['representative']['observation']['last_evaluated_bar_close_ms'],2*w.H4)
 def test_global_search_and_unique_contract_pagination(self):
  for n in range(120):self.add('x'+str(n),'X'+str(n)+'USDT','observing')
  first,_=self.page(limit=100);second,_=self.page(limit=100,offset=100,view=first['view'])
  self.assertEqual(first['total'],122);self.assertEqual(len(first['rows'])+len(second['rows']),122)
  found,_=self.page(search='X119');self.assertEqual(found['total'],1)
 def test_missing_quote_does_not_remove_record(self):
  data,_=self.page(search='JCTUSD_PERP');self.assertEqual(data['rows'][0]['quote_data'],{})
 def test_stable_view_survives_later_writes_and_expiry_is_explicit(self):
  data,_=self.page();self.add('later','NEWUSDT','observing')
  with closing(sqlite3.connect(self.path)) as db, db:db.execute('INSERT INTO tracking_commits VALUES(2)')
  old,_=self.page(view=data['view']);fresh,_=self.page()
  self.assertEqual(old['total'],2);self.assertEqual(fresh['total'],3)
  with w._LOCK:w._VIEWS.pop(data['view'])
  _,code=self.page(view=data['view']);self.assertEqual(code,410)
 def test_status_and_all_routes_never_write(self):
  before=hashlib.sha256(self.path.read_bytes()).hexdigest()
  data,_=self.page();w.route(self.root,'/api/tracking/workbench/status')
  result,code=w.detail(self.root,dict(view=data['view'],market='USDT-M',symbol='JCTUSDT',event_id='a1'))
  self.assertEqual(code,200);self.assertEqual(result['event_count'],2)
  self.assertEqual(hashlib.sha256(self.path.read_bytes()).hexdigest(),before)
 def test_explicit_filters_and_no_c_fabrication(self):
  data,code=self.page(strategy='C');self.assertEqual(code,200);self.assertEqual(data['total'],0)
  _,code=self.page(limit=101);self.assertEqual(code,400)
  data,_=self.page(status='all');self.assertEqual(data['total'],2)
  data,_=self.page(status='ended');self.assertEqual(data['total'],0) # active contract is not hidden by its old ended event
 def test_detail_never_mixes_new_commit_into_old_header(self):
  data,_=self.page()
  with closing(sqlite3.connect(self.path)) as db, db:db.execute('INSERT INTO tracking_commits VALUES(2)')
  result,code=w.route(self.root,'/api/tracking/workbench/detail?view='+data['view']+'&market=USDT-M&symbol=JCTUSDT&event_id=a1')
  self.assertEqual(code,409)
 def test_watch_notified_on_real_change(self):
  version=w.stamp(self.root)['version'];result=[]
  t=threading.Thread(target=lambda:result.append(w.route(self.root,'/api/tracking/workbench/watch?version='+version)))
  t.start()
  import time
  time.sleep(.05)
  with closing(sqlite3.connect(self.path)) as db, db:db.execute('INSERT INTO tracking_commits VALUES(2)')
  w.notify(self.root);t.join(2);self.assertFalse(t.is_alive());self.assertTrue(result[0][0]['changed'])

 def add_b_archive(self):
  self.bpath=self.root/'runtime/b-events-v3.sqlite3'
  with closing(sqlite3.connect(self.bpath)) as db, db:
   db.executescript("CREATE TABLE meta(key TEXT,value TEXT);INSERT INTO meta VALUES('generation','1');CREATE TABLE events(event_id TEXT PRIMARY KEY,bar_open INTEGER,payload TEXT);")
   for eid,status,gate in [('br1','pass','pass'),('br2','pending','unknown'),('br3','pass','fail')]:
    db.execute('INSERT INTO events VALUES(?,?,?)',(eid,w.H4,json.dumps(dict(event_id=eid,market='USDⓈ-M',symbol='BRUSDT',trigger_open_time=w.H4,technical_confirmation=status,structure_gate=gate,event_revision=2))))
 def test_history_all_events_not_just_latest_or_active(self):
  self.add_b_archive();before=hashlib.sha256(self.bpath.read_bytes()).hexdigest()
  data,code=self.page(tab='history',status='all');self.assertEqual(code,200);self.assertEqual(data['total'],6)
  b,_=self.page(tab='history',status='all',search='BR');self.assertEqual(b['total'],3);self.assertEqual(b['contract_count'],1)
  self.assertEqual({e['result_state'] for e in b['rows']},{'confirmed','pending','fail'})
  self.assertTrue(all(not e['session_id'] for e in b['rows']))
  active,_=self.page();self.assertEqual(active['total'],2) # reading history creates no sessions
  self.assertEqual(before,hashlib.sha256(self.bpath.read_bytes()).hexdigest())
 def test_history_detail_without_tracking_session(self):
  self.add_b_archive();data,_=self.page(tab='history',status='all')
  detail,code=w.detail(self.root,dict(view=data['view'],market='USDT-M',symbol='BRUSDT',event_id='br2'))
  self.assertEqual(code,200);self.assertEqual(detail['selected']['result_state'],'pending');self.assertEqual(detail['event_count'],3)
 def test_history_status_filter_date_bounds_and_stable_pages(self):
  self.add_b_archive();data,_=self.page(tab='history',status='confirmed');self.assertEqual([e['event_id'] for e in data['rows']],['br1'])
  data,_=self.page(tab='history',status='all',limit=2);second,_=self.page(tab='history',status='all',limit=2,offset=2,view=data['view'])
  self.assertFalse({e['event_id'] for e in data['rows']}&{e['event_id'] for e in second['rows']})
  data,_=self.page(tab='history',status='all',**{'from':'2026-10-20'});self.assertEqual(data['total'],0)
  for dates in [{'from':'invalid'},{'from':'2026-10-21','to':'2026-10-20'}]:
   _,code=self.page(tab='history',**dates);self.assertEqual(code,400)
 def test_ended_return_not_computed_from_current_price(self):
  self.add('ended','OLDUSDT','ended')
  data,_=self.page(status='ended');self.assertIsNone(data['rows'][0]['return_pct'])

class PublicationIdentity(unittest.TestCase):
 def test_normal_source_uses_current_verified_generation_without_deleting_old(self):
  from test_1020_recovery_closure import LocalEvidence
  from tracking.recovery_bridge import enqueue_verified
  from tracking.recovery import recovered_inputs
  from tracking_service import source_input
  f=LocalEvidence();f.setUp();self.addCleanup(f.tearDown)
  oldmeta=f.synced();oldmeta['USDT-M|TESTUSDT']['cap']['id']='old-asset'
  old=enqueue_verified(f.recovery,oldmeta,contract='USDT-M|TESTUSDT',source='oi',start_ms=f.at,target_ms=f.at,clock_ms=f.at)
  new=f.job('oi')
  for job,value in [(old,1),(new,2)]:f.recovery.import_local(job,[dict(effective_at_ms=f.at,available_at_ms=f.at,value=value,unit='USD',local_provenance={'path':'fixture'})],clock_ms=f.at)
  raw=[[f.at-w.H4,1,2,1,2,1,f.at-1,1]]
  with patch('ema_observations.archive_raw',return_value=raw),patch('tracking_service.writer',return_value=f.ledger):
   result=source_input(f.root,f.row,f.at)
  self.assertEqual(result['oi_samples'][0]['value'],2)
  # Unanchored ambiguity protection and old immutable evidence remain intact.
  with self.assertRaisesRegex(ValueError,'ambiguous_acquisition'):
   recovered_inputs(f.ledger,market='USDT-M',symbol='TESTUSDT',start_ms=f.at,target_ms=f.at)
  self.assertEqual(recovered_inputs(f.ledger,market='USDT-M',symbol='TESTUSDT',start_ms=f.at,target_ms=f.at,acquisition_job_key=old)['oi'][0]['value'],1)

if __name__=='__main__':unittest.main()
