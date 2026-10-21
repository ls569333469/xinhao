import hashlib,json,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
from collection_local import sync_cap_identities,local_samples,drain_local
from tracking.ledger import TrackingLedger,_canonical
from tracking.recovery import Recovery,SourceFailure
from tracking.recovery_bridge import enqueue_verified
from tracking.live_source import LiveHistoricalAdapter,CONVERSION,cap_endpoint
from tracking.runtime import registry
from b_repair_store import RepairStore
import test_1020_schedule_replay_fix as fixtures
B,NOW,H4=fixtures.B,fixtures.NOW,fixtures.H4

H=3600000

class LocalEvidence(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name)
        self.at=1792400000000//H*H
        self.meta=dict(verified=True,identity_evidence='exchangeInfo:fixture',quote_asset='USDT',valid_from_ms=0,valid_to_ms=self.at+10*H,history_policy=dict(window_ms=30*24*H,version='fixture'))
        (self.root/'runtime').mkdir()
        (self.root/'runtime/tracking-source-registry.json').write_text(json.dumps(dict(schema='tracking-source-registry-v3',contracts={'USDT-M|TESTUSDT':self.meta})),encoding='utf8')
        self.row=dict(market='USDⓈ-M',symbol='TESTUSDT',capTrusted=True,capIdentityVerified=True,capApplicable=True,capMappingRevision='fixture-rev',capIdentityBasis='exact address',capSource='CoinGecko',capId='fixture',capUsd=100,capObservedAt=self.at-1000,capFirstRetrievedAt=self.at,capRetrievedAt=self.at)
        (self.root/'scan.json').write_text(json.dumps(dict(scan_id='fixture',rows=[self.row])),encoding='utf8')
        self.ledger=TrackingLedger(self.root/'isolated.sqlite3');self.recovery=Recovery(self.ledger);self.recovery.set_enabled(True,clock_ms=self.at)
    def tearDown(self):self.ledger.close();self.tmp.cleanup()
    def synced(self):
        sync_cap_identities(self.root)
        return registry(self.root)
    def job(self,source='cap',at=None):
        at=at or self.at
        return enqueue_verified(self.recovery,self.synced(),contract='USDT-M|TESTUSDT',source=source,start_ms=at,target_ms=at,clock_ms=self.at)
    def jobrow(self,key):
        with self.recovery.transaction() as db:return dict(db.execute('SELECT * FROM tracking_recovery WHERE job_key=?',(key,)).fetchone())
    def test_identity_comes_from_audited_id_not_symbol(self):
        self.assertEqual(sync_cap_identities(self.root)['updated'],1)
        cap=registry(self.root)['USDT-M|TESTUSDT']['cap']
        self.assertEqual(cap['id'],'fixture');self.assertEqual(cap['provider'],'coingecko')
        self.assertTrue(sync_cap_identities(self.root)['cached'])
    def test_unverified_or_nonapplicable_identity_not_registered(self):
        self.row['capIdentityVerified']=False
        (self.root/'scan.json').write_text(json.dumps(dict(rows=[self.row])),encoding='utf8')
        self.assertEqual(sync_cap_identities(self.root)['updated'],0)
        self.assertNotIn('cap',registry(self.root)['USDT-M|TESTUSDT'])
    def test_paprika_exact_provider_contract(self):
        self.row.update(capSource='CoinPaprika',capId='fixture-coin')
        (self.root/'scan.json').write_text(json.dumps(dict(rows=[self.row])),encoding='utf8')
        job=self.jobrow(self.job())
        self.assertEqual(job['provider'],'coinpaprika')
        self.assertEqual(job['endpoint'],'https://api.coinpaprika.com/v1/tickers/fixture-coin/historical')
        adapter=LiveHistoricalAdapter(self.synced(),clock=lambda:self.at)
        adapter.request=lambda url,params,**kw:dict(status=402,data=None)
        with self.assertRaises(SourceFailure) as raised:adapter(job,start_ms=self.at,end_ms=self.at,limit=1,attempt=lambda fn:fn(timeout_seconds=1))
        self.assertEqual(raised.exception.code,'402')
    def test_token_usd_cap_does_not_require_derivative_quote_conversion(self):
        meta=self.synced();meta['USDT-M|TESTUSDT']['quote_asset']='USDC'
        key=enqueue_verified(self.recovery,meta,contract='USDT-M|TESTUSDT',source='cap',start_ms=self.at,target_ms=self.at,clock_ms=self.at)
        self.assertEqual(self.jobrow(key)['unit_identity'],'USD')
        with self.assertRaises(SourceFailure):enqueue_verified(self.recovery,meta,contract='USDT-M|TESTUSDT',source='oi',start_ms=self.at,target_ms=self.at,clock_ms=self.at)
    def test_permission_denied_is_shared_cooldown_without_hidden_retry(self):
        from collection_grants import saved_plan_grant
        from collection_policy import DEFAULT
        key=self.job();grant,_=saved_plan_grant(self.recovery,self.jobrow(key),DEFAULT,clock_ms=self.at)
        self.recovery.bind_grant(key,grant,clock_ms=self.at)
        adapter=LiveHistoricalAdapter(self.synced(),clock=lambda:self.at)
        calls=[]
        def request(*args,**kw):calls.append(args);return dict(status=402,data=None)
        adapter.request=request
        result=self.recovery.page(fetch=adapter,clock_ms=self.at,grant_id=grant,clock=lambda:self.at)
        self.assertEqual(result[0]['state'],'blocked_permission');self.assertEqual(len(calls),1)
        with self.recovery.transaction() as db:
            limit=db.execute('SELECT * FROM recovery_provider_limits WHERE provider=?',('coingecko',)).fetchone()
            self.assertEqual(limit['reason'],'provider_402');self.assertGreaterEqual(limit['until_ms'],self.at+6*H)
    def test_local_cap_archives_without_http_or_business_ack(self):
        key=self.job();RepairStore(self.root).record_caps([self.row],self.at)
        found=local_samples(self.root,self.jobrow(key),self.synced(),self.at)
        self.assertEqual(found[0]['effective_at_ms'],self.at-1000)
        result=self.recovery.import_local(key,found,clock_ms=self.at)
        self.assertFalse(result['evaluation_acked']);self.assertEqual(result['http_attempts'],0)
        self.assertEqual(self.jobrow(key)['state'],'archived_awaiting_effect')
        with self.recovery.transaction() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM recovery_attempts').fetchone()[0],0)
            self.assertEqual(db.execute("SELECT value FROM meta WHERE key='collection_local_archive_count'").fetchone()[0],'1')
            self.assertEqual(db.execute('SELECT COUNT(*) FROM recovery_effects').fetchone()[0],0)
    def test_outside_48h_local_cap_is_usable_without_expanding_network_window(self):
        old=self.at-72*H;key=self.job(at=old)
        item=dict(self.row,capObservedAt=old-1000)
        RepairStore(self.root).record_caps([item],self.at)
        result=drain_local(self.root,self.recovery,self.synced(),clock_ms=self.at)
        self.assertEqual(result['archived'],1);self.assertEqual(result['http_attempts'],0)
    def test_future_and_wrong_asset_caps_are_not_used(self):
        key=self.job()
        for update in (dict(capId='different'),dict(capFirstRetrievedAt=self.at+1),dict(capObservedAt=self.at+1),dict(capIdentityVerified=False)):
            tmp=tempfile.TemporaryDirectory();other=Path(tmp.name)
            RepairStore(other).record_caps([dict(self.row,**update)],self.at)
            self.assertEqual(local_samples(other,self.jobrow(key),self.synced(),self.at),[])
            tmp.cleanup()
    def test_local_oi_cannot_jump_an_earlier_gap(self):
        key=self.job('oi');row=self.jobrow(key)
        folder=self.root/'runtime/b-source'/hashlib.sha256('USDⓈ-M|TESTUSDT'.encode()).hexdigest();folder.mkdir(parents=True)
        (folder/'structure.json').write_text(json.dumps(dict(oi_samples=[dict(timestamp=self.at+H,_first_retrieved_at=self.at+H,sumOpenInterestValue=2)])),encoding='utf8')
        self.assertEqual(local_samples(self.root,row,self.synced(),self.at+2*H),[])
    def test_import_rejects_foreign_time_and_restores_original_state(self):
        key=self.job();before=self.jobrow(key)['state']
        bad=dict(effective_at_ms=self.at+1,available_at_ms=self.at,value=100,unit='USD',local_provenance={'path':'fixture'})
        with self.assertRaises(SourceFailure):self.recovery.import_local(key,[bad],clock_ms=self.at)
        self.assertEqual(self.jobrow(key)['state'],before)
    def test_native_string_oi_is_reused_with_exact_identity(self):
        key=self.job('oi');row=self.jobrow(key)
        folder=self.root/'runtime/b-source'/hashlib.sha256('USDⓈ-M|TESTUSDT'.encode()).hexdigest();folder.mkdir(parents=True)
        (folder/'structure.json').write_text(json.dumps(dict(oi_samples=[dict(symbol='TESTUSDT',timestamp=self.at,_first_retrieved_at=self.at,sumOpenInterestValue='123.25')])),encoding='utf8')
        found=local_samples(self.root,row,self.synced(),self.at)
        self.assertEqual(found[0]['value'],123.25)
        result=drain_local(self.root,self.recovery,self.synced(),clock_ms=self.at)
        self.assertEqual(result['archived'],1)
    def test_finished_reservation_released_without_resetting_used_budget(self):
        from collection_grants import saved_plan_grant,release_completed
        from collection_policy import DEFAULT
        key=self.job();cfg=dict(DEFAULT,maxHttpAttempts=70)
        grant,_=saved_plan_grant(self.recovery,self.jobrow(key),cfg,clock_ms=self.at)
        RepairStore(self.root).record_caps([self.row],self.at)
        self.recovery.import_local(key,local_samples(self.root,self.jobrow(key),self.synced(),self.at),clock_ms=self.at)
        self.assertEqual(release_completed(self.recovery,clock_ms=self.at),1)
        self.assertEqual(release_completed(self.recovery,clock_ms=self.at),0)
        other=self.job('oi');new,denied=saved_plan_grant(self.recovery,self.jobrow(other),cfg,clock_ms=self.at)
        self.assertIsNone(denied);self.assertNotEqual(new,grant)
        with self.recovery.transaction() as db:self.assertLessEqual(db.execute('SELECT SUM(max_attempts) FROM recovery_grants').fetchone()[0],70)
    def test_unknown_http_attempt_keeps_its_reservation(self):
        from collection_grants import saved_plan_grant,release_completed
        from collection_policy import DEFAULT
        key=self.job();grant,_=saved_plan_grant(self.recovery,self.jobrow(key),DEFAULT,clock_ms=self.at)
        RepairStore(self.root).record_caps([self.row],self.at)
        self.recovery.import_local(key,local_samples(self.root,self.jobrow(key),self.synced(),self.at),clock_ms=self.at)
        with self.recovery.transaction() as db:
            db.execute('INSERT INTO recovery_attempts(attempt_id,grant_id,operation_id,job_key,lease_token,provider,endpoint,start_ms,end_ms,started_ms) VALUES(?,?,?,?,?,?,?,?,?,?)',('unknown',grant,'fixture',key,'fixture','coingecko','fixture',self.at,self.at,self.at))
        self.assertEqual(release_completed(self.recovery,clock_ms=self.at),0)

class HistoricalCycle(fixtures.RecoveryRoot):
    def positive(self):
        self.inputs['raw_bars']=[dict(open_ms=(120000+i)*H4,o=500-min(i,226),h=501-min(i,226),l=498-min(i,226),c=499-min(i,226),q=100,q_unit='USD nominal quote') for i in range(100,240)]
        state=json.loads(self.live);state['t0']=B;self.live=json.dumps(state);self.ledger.set_cursor(self.ck,self.live)
    def test_late_positive_does_not_overwrite_another_live_cycle(self):
        from tracking.recovery_replay import replay_step
        self.positive()
        result=replay_step(self.ledger,self.dep,inputs=self.inputs,clock_ms=NOW)
        self.assertTrue(result['ok'],result)
        self.assertEqual(result['recovery_effect_ack']['evaluated_ms'],B)
        self.assertEqual(self.ledger.get_cursor(self.ck),self.live)
        key='c-recovery-controller|USDT-M|TESTUSDT|c-1015-v1.1|'+str(B-H4)
        self.assertEqual(json.loads(self.ledger.get_cursor(key))['t0'],B-H4)
        self.assertEqual(self.ledger._read('SELECT COUNT(*) FROM event_revisions')[0][0],1)
    def test_historical_cycle_continues_without_live_controller_rewind(self):
        from tracking.recovery_replay import replay_step
        from tracking.c_rebound_engine import CReboundEngine
        from tracking.tracking_engine import iso
        self.positive();replay_step(self.ledger,self.dep,inputs=self.inputs,clock_ms=NOW)
        bars=self.inputs['raw_bars']+[dict(open_ms=B,o=273,h=274,l=272,c=273,q=100,q_unit='USD nominal quote')]
        out=CReboundEngine(self.ledger).consume(market='USDT-M',symbol='TESTUSDT',**dict(self.inputs,raw_bars=bars),target_close_ms=B+H4,clock_iso=iso(NOW),source_version='next',historical_import=True,source_revision=True,recovery_cycle_t0=B-H4)
        self.assertTrue(out['ok'],out);self.assertEqual(self.ledger.get_cursor(self.ck),self.live)
    def test_branch_rejected_for_live_or_unclosed_target(self):
        from tracking.c_rebound_engine import CReboundEngine
        from tracking.tracking_engine import iso
        for change in (dict(historical_import=False),dict(recovery_cycle_t0=B)):
            kw=dict(market='USDT-M',symbol='TESTUSDT',**self.inputs,target_close_ms=B,clock_iso=iso(NOW),source_version='bad',historical_import=True,recovery_cycle_t0=B-H4)
            kw.update(change)
            with self.assertRaises(ValueError):CReboundEngine(self.ledger).consume(**kw)
    def test_same_live_cycle_keeps_later_event_and_state(self):
        from tracking.recovery_replay import replay_step
        self.positive()
        first=replay_step(self.ledger,self.dep,inputs=self.inputs,clock_ms=NOW)
        self.assertTrue(first['ok'])
        state=json.loads(self.live);state['t0']=B-H4;self.live=json.dumps(state);self.ledger.set_cursor(self.ck,self.live)
        # A second source role proves the same earlier candidate root.
        other=self.recovery.enqueue(source='lsr',market='USDT-M',symbol='TESTUSDT',timeframe='4h',start_ms=B,target_ms=B,provider='fixture',endpoint='fixture',period_ms=H4,time_semantics='effective_at_ms',unit_identity='ratio',adapter_version='fixture-v1',clock_ms=NOW)
        sample=self.inputs['lsr_samples'][0];text=_canonical([sample]);sha=hashlib.sha256(text.encode()).hexdigest()
        with self.recovery.transaction() as db:
            db.execute('INSERT INTO tracking_recovery_chunks VALUES(?,?,?,?)',(other,sha,text,NOW))
            db.execute('INSERT INTO tracking_recovery_samples VALUES(?,?,?,?,?)',(other,B,sha,NOW,_canonical(sample)))
        dep=self.recovery.depend(other,consumer='C_strategy',rule_version='c-1015-v1.1',start_ms=B,target_ms=B,step_ms=H4,clock_ms=NOW)
        before=self.ledger._read('SELECT COUNT(*) FROM event_revisions')[0][0]
        result=replay_step(self.ledger,dep,inputs=self.inputs,clock_ms=NOW)
        self.assertTrue(result['ok'],result);self.assertEqual(result['recovery_effect_ack']['evaluated_ms'],B)
        self.assertEqual(self.ledger.get_cursor(self.ck),self.live)
        self.assertEqual(self.ledger._read('SELECT COUNT(*) FROM event_revisions')[0][0],before)
    def test_durable_continuation_uses_next_saved_input_and_deduplicates(self):
        from tracking.recovery_replay import replay_step
        from tracking.c_recovery import drain_cycles
        self.positive();self.assertTrue(replay_step(self.ledger,self.dep,inputs=self.inputs,clock_ms=NOW)['ok'])
        root=Path(self.tmp.name);(root/'scan.json').write_text(json.dumps(dict(rows=[dict(market='USDⓈ-M',symbol='TESTUSDT',candleAsOf=B+H4-1)])),encoding='utf8')
        (root/'runtime').mkdir(exist_ok=True)
        (root/'runtime/tracking-source-registry.json').write_text(json.dumps(dict(schema='tracking-source-registry-v3',contracts={})),encoding='utf8')
        inp=dict(self.inputs,raw_bars=self.inputs['raw_bars']+[dict(open_ms=B,o=273,h=274,l=272,c=273,q=100,q_unit='USD nominal quote')])
        with patch('tracking_service.source_input',return_value=inp):
            result=drain_cycles(root,self.ledger,clock_ms=NOW)
            self.assertTrue(result['ok'],result);self.assertEqual(result['count'],1)
            repeated=drain_cycles(root,self.ledger,clock_ms=NOW+60001)
            self.assertTrue(repeated['items'][0]['result']['unchanged'])
        self.assertEqual(self.ledger.get_cursor(self.ck),self.live)

if __name__=='__main__':unittest.main()
