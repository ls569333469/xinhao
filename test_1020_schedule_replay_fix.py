import copy,datetime as dt,hashlib,json,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
from task_service import TaskService,next_scan_due,validate_scan,DEFAULT_SCAN
from tracking.ledger import TrackingLedger,LedgerReject,_canonical
from tracking.recovery import Recovery
from tracking.recovery_replay import replay_step,register_context,drain
from tracking.recovery_effects import prepare_step
from tracking.c_recovery import root_checks,recover_no_cycle,validate_no_cycle_effect,proof_key

H4=14400000
B=(120000+240)*H4
NOW=B+2*H4


class Schedule(unittest.TestCase):
    def test_supported_intervals_and_boolean_rejection(self):
        for value in (0.25,1,4,8,24):self.assertEqual(validate_scan(dict(DEFAULT_SCAN,intervalHours=value))['intervalHours'],value)
        for value in (True,False,0,0.5,float('nan'),float('inf'),'0.25'):
            with self.assertRaises(ValueError):validate_scan(dict(DEFAULT_SCAN,intervalHours=value))
    def test_exact_quarter_hour_boundaries(self):
        now=dt.datetime(2026,10,20,2,23,tzinfo=dt.timezone(dt.timedelta(hours=8)))
        self.assertEqual(dt.datetime.fromisoformat(next_scan_due(now,0.25)).astimezone(now.tzinfo).strftime('%H:%M'),'02:32')
        now=now.replace(minute=32,second=0)
        self.assertEqual(dt.datetime.fromisoformat(next_scan_due(now,0.25)).astimezone(now.tzinfo).strftime('%H:%M'),'02:47')
    def test_persist_interval_preserve_enabled_and_offset_and_hourly_B(self):
        with tempfile.TemporaryDirectory() as tmp:
            now=dt.datetime(2026,10,20,2,23,tzinfo=dt.timezone.utc)
            service=TaskService(tmp,lambda:{},clock=lambda:now)
            before=service.settings()
            before=service.save_settings(dict(version=before['version'],scan=dict(before['scan'],enabled=True),timing=dict(before['timing'],closeOffsetSec=45),observe=dict(before['observe'],enabled=True)))
            after=service.save_settings(dict(version=before['version'],scan=dict(before['scan'],intervalHours=.25)))
            self.assertEqual(after['timing']['closeOffsetSec'],45)
            self.assertEqual(dt.datetime.fromisoformat(after['next_due']).strftime('%H:%M:%S'),'02:30:45')
            self.assertEqual(after['observe_next_due'],before['observe_next_due'])
            service.close()
            reopened=TaskService(tmp,lambda:{},clock=lambda:now)
            self.assertEqual(reopened.settings()['scan']['intervalHours'],.25)
            reopened.close()


class RecoveryRoot(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.ledger=TrackingLedger(Path(self.tmp.name)/'isolated.sqlite3');self.recovery=Recovery(self.ledger)
        self.ck='c-controller|USDT-M|TESTUSDT|c-1015-v1.1'
        self.live=json.dumps(dict(t0=None,terminal=None,armed=True,last_seen_close_ms=B+H4,evaluated_close_ms=B+H4))
        self.ledger.set_cursor(self.ck,self.live)
        self.inputs=dict(raw_bars=[dict(open_ms=(120000+i)*H4,o=100+i,h=102+i,l=99+i,c=101+i,q=100,q_unit='USD nominal quote') for i in range(100,240)],oi_samples=[dict(effective_at_ms=B,available_at_ms=NOW,value=50.,unit='USD')],lsr_samples=[dict(effective_at_ms=B,available_at_ms=NOW,value=1.,unit='ratio')],caps=[dict(effective_at_ms=B,available_at_ms=NOW,value=100.,unit='USD',trusted=True)])
        self.job=self.recovery.enqueue(source='oi',market='USDT-M',symbol='TESTUSDT',timeframe='4h',start_ms=B,target_ms=B,provider='fixture',endpoint='fixture',period_ms=H4,time_semantics='effective_at_ms',unit_identity='USD',adapter_version='fixture-v1',clock_ms=NOW)
        sample=self.inputs['oi_samples'][0];text=_canonical([sample]);version=hashlib.sha256(text.encode()).hexdigest()
        with self.recovery.transaction() as db:
            db.execute('INSERT INTO tracking_recovery_chunks VALUES(?,?,?,?)',(self.job,version,text,NOW))
            db.execute('INSERT INTO tracking_recovery_samples VALUES(?,?,?,?,?)',(self.job,B,version,NOW,_canonical(sample)))
        self.dep=self.recovery.depend(self.job,consumer='C_strategy',rule_version='c-1015-v1.1',start_ms=B,target_ms=B,step_ms=H4,clock_ms=NOW)
    def tearDown(self):self.ledger.close();self.tmp.cleanup()
    def test_complete_negative_old_root_ack_preserves_latest_controller(self):
        result=replay_step(self.ledger,self.dep,inputs=self.inputs,clock_ms=NOW)
        self.assertTrue(result['ok'],result)
        self.assertEqual(result['recovery_effect_ack']['evaluated_ms'],B)
        self.assertEqual(self.ledger.get_cursor(self.ck),self.live)
        self.assertEqual(self.ledger._read('SELECT COUNT(*) FROM event_revisions')[0][0],0)
    def test_missing_cap_is_pending_and_no_false_ack_or_watermark(self):
        self.inputs['caps']=[]
        result=replay_step(self.ledger,self.dep,inputs=self.inputs,clock_ms=NOW)
        self.assertTrue(result['ok']);self.assertTrue(result['pending']);self.assertIn('oi_cap',result['missing_checks'])
        self.assertEqual(self.ledger._read('SELECT next_ms FROM recovery_dependencies WHERE dependency_id=?',(self.dep,))[0][0],B)
        self.assertEqual(self.ledger.get_cursor(self.ck),self.live)
    def test_future_cap_cannot_complete_old_root(self):
        self.inputs['caps'][0]['effective_at_ms']=B+1
        result=replay_step(self.ledger,self.dep,inputs=self.inputs,clock_ms=NOW)
        self.assertTrue(result['pending']);self.assertFalse(result['evaluation_acked'])
    def test_drain_missing_root_reports_waiting_and_keeps_need(self):
        self.inputs['caps']=[];register_context(self.recovery,self.dep,inputs=self.inputs,clock_ms=NOW)
        result=drain(self.ledger,clock_ms=NOW,limit=1)
        self.assertTrue(result['ok']);self.assertTrue(result['items'][0]['pending'])
        self.assertEqual(result['items'][0]['waiting_reason'],'C_historical_root_missing_evidence')
        self.assertEqual(self.ledger._read('SELECT COUNT(*) FROM recovery_effects')[0][0],0)
    def test_tampered_proof_rejected(self):
        step=prepare_step(self.ledger,self.dep);dep=dict(self.ledger._read('SELECT * FROM recovery_dependencies WHERE dependency_id=?',(self.dep,))[0])
        payload=dict(self.inputs,recovery_step=step,c_recovery_no_cycle=True)
        with self.assertRaises(LedgerReject):validate_no_cycle_effect(payload,{proof_key(step):'{}'},dep,B,NOW)
    def test_late_positive_does_not_overwrite_another_live_cycle(self):
        self.inputs['raw_bars']=[dict(open_ms=(120000+i)*H4,o=500-min(i,226),h=501-min(i,226),l=498-min(i,226),c=499-min(i,226),q=100,q_unit='USD nominal quote') for i in range(100,240)]
        self.assertEqual(root_checks(self.inputs,B,NOW)['c0'],'pass')
        state=json.loads(self.live);state['t0']=B;live=json.dumps(state);self.ledger.set_cursor(self.ck,live)
        result=replay_step(self.ledger,self.dep,inputs=self.inputs,clock_ms=NOW)
        self.assertTrue(result['ok'],result);self.assertEqual(result['recovery_effect_ack']['evaluated_ms'],B)
        self.assertEqual(self.ledger.get_cursor(self.ck),live)
        self.assertEqual(self.ledger._read('SELECT COUNT(*) FROM recovery_effects')[0][0],1)
    def test_waiting_becomes_complete_only_after_real_missing_input_arrives(self):
        incomplete=dict(self.inputs,caps=[])
        self.assertTrue(replay_step(self.ledger,self.dep,inputs=incomplete,clock_ms=NOW)['pending'])
        result=replay_step(self.ledger,self.dep,inputs=self.inputs,clock_ms=NOW)
        self.assertTrue(result['ok']);self.assertEqual(result['recovery_effect_ack']['evaluated_ms'],B)
    def test_stale_epoch_rejected_atomically(self):
        step=prepare_step(self.ledger,self.dep);step['epoch']+=1
        dep=dict(self.ledger._read('SELECT d.*,j.contract FROM recovery_dependencies d JOIN tracking_recovery j USING(job_key) WHERE dependency_id=?',(self.dep,))[0])
        result=recover_no_cycle(self.ledger,dep,step,self.inputs,clock_ms=NOW,source_version='test')
        self.assertFalse(result['ok']);self.assertEqual(self.ledger._read('SELECT COUNT(*) FROM recovery_effects')[0][0],0)
        self.assertEqual(self.ledger.get_cursor(self.ck),self.live)

if __name__=='__main__':unittest.main()
