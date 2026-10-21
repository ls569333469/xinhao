import datetime as dt,json,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
from ema_observations import long_crosses,observe,bind_identity,BAR_MS
from tracking.ledger import TrackingLedger
from tracking.recovery import Recovery
from collection_projection import refresh
from collection_grants import saved_plan_grant,DAY
from collection_policy import DEFAULT
from b_cap_history import refresh_current


class LongCross(unittest.TestCase):
    def row(self,e,t=BAR_MS,valid=True):return dict(valid=valid,open_time=t,ema=e)
    def test_equal_before_strict_above_and_simultaneous(self):
        a=long_crosses(self.row([3,2,1]),self.row([1,1,1],0))
        self.assertEqual(a,dict(ema7_cross_25=True,ema7_cross_99=True,ema25_cross_99=True))
        self.assertFalse(long_crosses(self.row([1,1,1]),self.row([0,0,1],0))['ema7_cross_99'])
    def test_no_downward_or_repeated_above(self):
        self.assertFalse(long_crosses(self.row([1,1,3]),self.row([3,2,1],0))['ema7_cross_99'])
        self.assertFalse(long_crosses(self.row([3,2,1]),self.row([3,2,1],0))['ema7_cross_99'])
    def test_both_warmup_and_continuity(self):
        for prev in (None,self.row([1,1,1],0,False),self.row([1,1,1],-BAR_MS)):
            self.assertTrue(all(v is None for v in long_crosses(self.row([3,2,1]),prev).values()))
    def test_observation_history_and_ids_do_not_vanish(self):
        def bar(i,c):return [1700000000000+i*BAR_MS,c,c+1,c-1,c,10,1700000000000+(i+1)*BAR_MS-1,10]
        bars=[bar(i,100-i*.1) for i in range(130)]+[bar(130,200)]
        o=bind_identity(observe(bars),'USDⓈ-M','TESTUSDT')
        ids={x['event_id'] for x in o['long_cross_events']}
        self.assertTrue(ids)
        self.assertEqual(ids,{x['event_id'] for x in bind_identity(observe(bars),'USDⓈ-M','TESTUSDT')['long_cross_events']})
        later=bind_identity(observe(bars+[bar(131,200)]),'USDⓈ-M','TESTUSDT')
        self.assertTrue(ids<={x['event_id'] for x in later['recent_long_cross_events']})
        self.assertTrue(all(x['grade'] is None for x in o['long_cross_events']))
    def test_br_saved_archive(self):
        path=Path(__file__).parent.parent/'BR-closed-fixture.json'
        if not path.exists():self.skipTest('BR immutable archive not exported')
        o=bind_identity(observe(json.loads(path.read_text(encoding='utf8'))['raw']),'USDⓈ-M','BRUSDT')
        self.assertTrue(o['long_crosses']['ema7_cross_99'])
        self.assertFalse(o['long_crosses']['ema25_cross_99'])
        self.assertFalse(o['long_stack'])


class RecoverySummary(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.ledger=TrackingLedger(Path(self.tmp.name)/'ledger.sqlite3');self.r=Recovery(self.ledger)
        self.now=1792400000000
    def tearDown(self):self.ledger.close();self.tmp.cleanup()
    def job(self,symbol='TESTUSDT',provider=None):
        return self.r.enqueue(source='oi',market='USDT-M',symbol=symbol,timeframe='1h',start_ms=3600000,target_ms=3600000,
            provider=provider,endpoint='fixture' if provider else None,period_ms=3600000,time_semantics='effective_at_ms' if provider else None,unit_identity='USD' if provider else None,adapter_version='fixture' if provider else None,clock_ms=self.now)
    def test_bridge_requires_exact_consumer_and_keeps_original(self):
        old,new=self.job(),self.job(provider='binance')
        dep=self.r.depend(old,consumer='C_strategy',rule_version='v1',event_id='e',start_ms=3600000,target_ms=3600000,step_ms=3600000,clock_ms=self.now)
        with self.r.transaction() as db:
            refresh(db,clock_ms=self.now,window_hours=48)
            db.execute('INSERT INTO recovery_origin_links VALUES(?,?,?)',(old,new,'{}'))
            db.execute('INSERT INTO recovery_bridge_results VALUES(?,?,?,?,?,?,?)',(old,new,'verified',None,'fixture',self.now,None))
            self.assertEqual(refresh(db,clock_ms=self.now,window_hours=48)['legacy_bridged'],0)
        self.r.depend(new,consumer='C_strategy',rule_version='v1',event_id='e',start_ms=3600000,target_ms=3600000,step_ms=3600000,clock_ms=self.now)
        with self.r.transaction() as db:
            a=refresh(db,clock_ms=self.now,window_hours=48)
            b=refresh(db,clock_ms=self.now,window_hours=48)
            self.assertEqual(a,b);self.assertEqual(a['legacy_bridged'],1)
            self.assertEqual(sum(x['count'] for x in a['source_counts']),1)
            self.assertEqual(a['outside_automatic_window'][0]['count'],1)
            self.assertEqual(db.execute('SELECT COUNT(*) FROM tracking_recovery').fetchone()[0],2)
    def test_unverified_link_does_not_hide_old_blocker(self):
        old,new=self.job(),self.job(provider='binance')
        with self.r.transaction() as db:
            refresh(db,clock_ms=self.now,window_hours=48)
            db.execute('INSERT INTO recovery_origin_links VALUES(?,?,?)',(old,new,'{}'))
            self.assertEqual(refresh(db,clock_ms=self.now,window_hours=48)['legacy_bridged'],0)
    def test_daily_grant_does_not_reset_with_display_settings(self):
        key=self.job(provider='binance')
        with self.r.transaction() as db:job=dict(db.execute('SELECT * FROM tracking_recovery WHERE job_key=?',(key,)).fetchone())
        cfg=dict(DEFAULT)
        a,_=saved_plan_grant(self.r,job,cfg,clock_ms=self.now)
        b,_=saved_plan_grant(self.r,job,dict(cfg),clock_ms=self.now+100)
        self.assertEqual(a,b)
        with self.r.transaction() as db:db.execute('UPDATE recovery_grants SET attempts_used=max_attempts WHERE grant_id=?',(a,))
        same,reason=saved_plan_grant(self.r,job,cfg,clock_ms=self.now+200)
        self.assertEqual(a,same);self.assertIn('exhausted',reason)
        new,reason=saved_plan_grant(self.r,job,cfg,clock_ms=self.now+DAY)
        self.assertNotEqual(new,a);self.assertIsNone(reason)
        self.r.bind_grant(key,new,clock_ms=self.now+DAY)
        self.r.revoke(new,clock_ms=self.now+DAY)
        self.assertEqual(saved_plan_grant(self.r,job,cfg,clock_ms=self.now+2*DAY),(None,'saved_plan_revoked'))
    def test_all_jobs_share_daily_cap(self):
        cfg=dict(DEFAULT,maxHttpAttempts=70)
        for i in range(3):
            key=self.job('T'+str(i)+'USDT',provider='binance')
            with self.r.transaction() as db:job=dict(db.execute('SELECT * FROM tracking_recovery WHERE job_key=?',(key,)).fetchone())
            grant,reason=saved_plan_grant(self.r,job,cfg,clock_ms=self.now)
            if i==2:self.assertIsNone(grant);self.assertEqual(reason,'saved_plan_daily_budget_reserved')
        with self.r.transaction() as db:self.assertEqual(db.execute('SELECT SUM(max_attempts) FROM recovery_grants').fetchone()[0],70)
    def test_bridge_cache_reopens_when_consumer_identity_changes(self):
        from tracking.recovery_bridge import bridge_page
        old=self.job()
        registry={'USDT-M|TESTUSDT':dict(verified=True,identity_evidence='fixture',quote_asset='USDT',valid_from_ms=0,valid_to_ms=self.now,history_policy=dict(window_ms=30*DAY,version='fixture'))}
        a=bridge_page(self.ledger,registry,clock_ms=self.now)
        self.assertTrue(a['items'][0]['canonical'])
        b=bridge_page(self.ledger,registry,clock_ms=self.now+1)
        self.assertTrue(b['items'][0]['cached'])
        self.r.depend(old,consumer='C_strategy',rule_version='v2',event_id='new-event',start_ms=3600000,target_ms=3600000,step_ms=3600000,clock_ms=self.now+2)
        c=bridge_page(self.ledger,registry,clock_ms=self.now+3)
        self.assertEqual(c['items'][0]['dependencies'],1)
        with self.r.transaction() as db:self.assertEqual(refresh(db,clock_ms=self.now+3,window_hours=48)['legacy_bridged'],1)
    def test_bridge_failed_config_backoff_and_registry_revision(self):
        from tracking.recovery_bridge import bridge_page
        self.job()
        a=bridge_page(self.ledger,{},clock_ms=self.now)
        self.assertIn('blocked',a['items'][0])
        b=bridge_page(self.ledger,{},clock_ms=self.now+1)
        self.assertTrue(b['items'][0]['cached'])
        registry={'USDT-M|TESTUSDT':dict(verified=True,identity_evidence='fixture',quote_asset='USDT',valid_from_ms=0,valid_to_ms=self.now,history_policy=dict(window_ms=30*DAY,version='fixture'))}
        c=bridge_page(self.ledger,registry,clock_ms=self.now+2)
        self.assertTrue(c['items'][0]['canonical'])


class CapRefresh(unittest.TestCase):
    def setUp(self):
        self.now=1792400000000;self.tmp=tempfile.TemporaryDirectory()
        self.items=[dict(market='USDⓈ-M',symbol='TESTUSDT',capSource='CoinGecko',capId='test',capTrusted=True)]
        self.stamp=dt.datetime.fromtimestamp(self.now/1000,dt.timezone.utc).isoformat()
    def tearDown(self):self.tmp.cleanup()
    def cg(self):return [dict(id='test',last_updated=self.stamp,market_cap=1000,current_price=10,circulating_supply=100)]
    def test_timeout_retries_once_then_archives(self):
        calls=[]
        def request(*args):
            calls.append(args)
            if len(calls)==1:raise TimeoutError('timed out')
            return self.cg()
        out=refresh_current(self.tmp.name,self.items,self.now,request)
        self.assertEqual(len(calls),2);self.assertEqual(out['recorded'],1)
        self.assertEqual(out['http_attempts'],2);self.assertEqual(len(out['errors']),1)
    def test_402_is_not_retried_and_successful_provider_survives(self):
        items=self.items+[dict(market='USDⓈ-M',symbol='OTHERUSDT',capSource='CoinPaprika',capId='other',capTrusted=True)]
        calls=[]
        def request(url,p):
            calls.append(url)
            if 'coinpaprika' in url:raise RuntimeError('HTTP 402')
            return self.cg()
        out=refresh_current(self.tmp.name,items,self.now,request)
        self.assertEqual(out['status'],'partial_success');self.assertEqual(out['recorded'],1);self.assertEqual(len(calls),2)
    def test_late_response_cannot_be_preclose_evidence(self):
        with patch('b_cap_history.RepairStore.record_caps') as record:
            out=refresh_current(self.tmp.name,self.items,self.now,lambda *a:self.cg(),target_boundary=self.now+1,clock=lambda:self.now+2)
        self.assertEqual(out['recorded'],0)
        self.assertIn('preclose_boundary_missed_after_response',str(out['errors']))
        self.assertEqual(record.call_count,1) # only already-known saved evidence
    def test_attempt_budget_error_ends_retry(self):
        calls=[]
        def request(*a):calls.append(a);raise RuntimeError('repair_request_budget_exhausted')
        out=refresh_current(self.tmp.name,self.items,self.now,request)
        self.assertEqual(len(calls),1);self.assertEqual(out['recorded'],0)


class PresentationBootstrap(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name)
        self.row=dict(market='m',symbol='X',candleAsOf=123,candleArchives={'4h':'scan-history/x.json'})
        self.data=dict(schema='ema-observation-bootstrap-v1',scan_id='scan-1',scan_sha256='hash-1',rows=[dict(market='m',symbol='X',archive='scan-history/x.json',candleAsOf=123,observation=dict(schema='ema-observation-v1.2',long_crosses={'ema7_cross_99':True}))])
        path=self.root/'runtime/ema-long-observations-bootstrap.json';path.parent.mkdir();path.write_text(json.dumps(self.data),encoding='utf8')
    def tearDown(self):self.tmp.cleanup()
    def test_valid_source_bound_upgrade(self):
        from ema_observation_bootstrap import enrich
        self.assertEqual(enrich([self.row],self.root,scan_id='scan-1',scan_sha256='hash-1'),1)
        self.assertTrue(self.row['emaObservation']['long_crosses']['ema7_cross_99'])
        self.assertFalse((self.root/'scan.json').exists())
    def test_new_scan_or_changed_source_cannot_inherit_old_events(self):
        from ema_observation_bootstrap import enrich
        for scan,sha in [('scan-2','hash-1'),('scan-1','hash-2')]:self.assertEqual(enrich([self.row],self.root,scan_id=scan,scan_sha256=sha),0)
        self.assertNotIn('emaObservation',self.row)
    def test_exact_contract_archive_and_boundary_are_required(self):
        from ema_observation_bootstrap import enrich
        for changes in [dict(symbol='Y'),dict(candleAsOf=124),dict(candleArchives={'4h':'scan-history/other.json'})]:
            row=dict(self.row,**changes)
            self.assertEqual(enrich([row],self.root,scan_id='scan-1',scan_sha256='hash-1'),0)
            self.assertNotIn('emaObservation',row)

if __name__=='__main__':unittest.main()
