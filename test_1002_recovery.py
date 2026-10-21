import json,os,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
from b_accumulation_store import BAccumulationStore,JsonBAccumulationStore
from b_event_sqlite import SQLiteBStore
from migrate_b_events import migrate
from export_b_events import export_legacy
from durable_io import replace_atomic,read_bytes_shared
from test_incremental_windows import Windows
from b_observe_runner import run_b_observe_round
from observe_config import OBSERVE_DEFAULT,TIMING_DEFAULT
from source_resume import H

class Recovery(unittest.TestCase):
    def test_migration_interruption_resume_and_incremental_rollback(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);legacy=JsonBAccumulationStore(root);legacy.ensure_observation_start('2026-10-17T00:00:00+00:00')
            params=dict(market='USDⓈ-M',symbol='TEST',trigger_open_time=1792224000000)
            payload=dict(schema_version=2,technical_event='pass',technical_confirmation='pending',bg4h=dict(input_rows=[[1,2,3]],ema7=2,ema25=1),bg4h_h1=None,baseline=[dict(open_time=1,o=2)])
            legacy.upsert_event(**params,payload={**payload,'input_fingerprint':'a'})
            raw=legacy.path.read_bytes();original=SQLiteBStore._write_event
            def failed(store,db,event):
                original(store,db,event);raise OSError('simulated migration power loss')
            with patch.object(SQLiteBStore,'_write_event',failed):
                with self.assertRaises(OSError):migrate(root)
            self.assertFalse((root/'runtime/b-events-v3.sqlite3').exists());self.assertEqual(legacy.path.read_bytes(),raw)
            migrate(root);self.assertTrue(migrate(root)['already_migrated'])
            sql=BAccumulationStore(root)
            for fingerprint in ('b','c','d'):sql.upsert_event(**params,payload={**payload,'input_fingerprint':fingerprint})
            self.assertEqual([r['snapshot']['event_revision'] for r in sql.list_events()[0]['revisions']],[1,2,3])
            params['symbol']='NEW';sql.upsert_event(**params,payload={**payload,'input_fingerprint':'new'})
            exported=root/'rollback-full.json';result=export_legacy(root,exported)
            self.assertEqual(result['event_count'],2);self.assertEqual(result['revisions'],3)
            data=json.loads(exported.read_text(encoding='utf8'))
            snap=sql._load()
            for k in ('events','cursors','rounds','outbox','observation_started_at'):self.assertEqual(data[k],snap[k])
            self.assertEqual(legacy.path.read_bytes(),raw)

    def test_long_offline_budget_preserves_next_slice_and_plans_are_not_needed(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);start=10000*H;calls=[]
            def fetch(m,s,start_time=None,end_time=None,limit=500):
                a=start-500*H if start_time is None else start_time;calls.append(a)
                return [[t,100,101,99,100,1,t+H-1,10] for t in range(a,min(end_time,a+500*H),H)]
            def run(end):return run_b_observe_round(root,symbols=[dict(market='USDⓈ-M',symbol='TEST',bg4h=dict(ema7=2,ema25=1))],observe={**OBSERVE_DEFAULT,'enabled':True},timing=TIMING_DEFAULT,now_ms=end+120000,target_close_boundary=end,fetch_1h=fetch,fetch_evidence=lambda *a:dict(changed_timestamps=[]))
            run(start);one=run(start+2300*H)
            self.assertTrue(one['continuation_required']);last=one['details'][0]['latest_boundary'];calls.clear()
            two=run(start+2300*H)
            self.assertGreaterEqual(calls[0],last-2*H);self.assertFalse(two['continuation_required'])
            self.assertEqual(two['details'][0]['latest_boundary'],start+2300*H)

    def test_sharing_retry_is_bounded_and_acl_failure_is_explicit(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);a=root/'a';b=root/'b';a.write_bytes(b'new');b.write_bytes(b'old')
            original=Path.replace;attempts=[]
            def sharing(path,destination):
                attempts.append(1)
                if len(attempts)<3:
                    exc=OSError('sharing violation');exc.winerror=32;raise exc
                return original(path,destination)
            with patch.object(Path,'replace',sharing):replace_atomic(a,b)
            self.assertEqual(read_bytes_shared(b),b'new');self.assertEqual(len(attempts),3)
            a.write_bytes(b'newer');exc=PermissionError('denied');exc.winerror=5
            with patch.object(Path,'replace',side_effect=exc) as mocked:
                with self.assertRaises(PermissionError):replace_atomic(a,b)
                self.assertEqual(mocked.call_count,1)
            self.assertEqual(b.read_bytes(),b'new');self.assertEqual(a.read_bytes(),b'newer')

    def test_http_telemetry_includes_retries_without_credentials_and_disk_failure(self):
        import request_telemetry as t
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);t.configure(root);t.begin_task('test-task','b_observe_1h',100)
            t.record('https://fapi.binance.com/api/klines?secret=hidden',dict(symbol='TEST',apiKey='hidden'),1,429,.02,retry_after='60')
            t.record('https://fapi.binance.com/api/klines',dict(symbol='TEST'),2,200,.03)
            counts=t.end_task('test-task');self.assertEqual(counts['attempts'],2);self.assertEqual(counts['retries'],1)
            raw=next((root/'runtime/request-telemetry').glob('*.jsonl')).read_text(encoding='utf8')
            self.assertNotIn('hidden',raw);self.assertNotIn('apiKey',raw);self.assertIn('test-task',raw)
            with patch.object(Path,'mkdir',side_effect=OSError('disk full')):t.record('https://fapi.binance.com/api/klines',{},1,200,.02)
            self.assertGreater(t.snapshot()['telemetry_write_errors'],0)
            t._path=None

if __name__=='__main__':unittest.main()
