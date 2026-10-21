import datetime as dt,hashlib,json,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
from b_repair_store import RepairStore,H,missing_evidence
from b_evidence_repair import run_repairs,Budget
from b_cap_history import fetch_historical
from b_evidence import structure_at
from b_event_sqlite import SQLiteBStore
from b_observe_evaluate import calculate_event,bars_from_cache
from b_1h_ingest import ingest_symbol_1h
from market_candle_cache import MarketCandleCache
from durable_io import atomic_json

def setup(root):
    ot=1000*H;target=ot+2*H;now=target+120_000
    item=dict(market='USDⓈ-M',symbol='TESTUSDT',capTrusted=True,capSource='CoinGecko',capId='test-coin',capUsd=1000,capObservedAt=target+H)
    raw=[[t,100,102,99,101,1,t+H-1,80 if t==ot else 25 if t==ot+H else 10] for t in range(ot-168*H,ot+3*H,H)]
    cache=MarketCandleCache(root);ingest_symbol_1h(cache,market=item['market'],symbol=item['symbol'],raw_klines=raw,target_close_boundary=target)
    bars=bars_from_cache(cache,market=item['market'],symbol=item['symbol'])
    evidence=dict(oi_value_unit='usd',oi_samples=[dict(timestamp=t,sumOpenInterestValue=500,sumOpenInterest=5) for t in (ot+H,target)],lsr_samples=[dict(timestamp=t,longShortRatio=.5) for t in (ot+H,target)])
    folder=root/'runtime/b-source'/hashlib.sha256(('USDⓈ-M|TESTUSDT').encode()).hexdigest()
    atomic_json(folder/'structure.json',evidence);atomic_json(root/'runtime/b-observe-universe.json',dict(symbols=[item]))
    store=SQLiteBStore(root,initialize=True);store.ensure_observation_start('1970-01-01T00:00:00+00:00')
    payload=calculate_event(bars,168,target=target,background=lambda t:dict(ema7=2,ema25=1,ema99=.5),structure=lambda t:structure_at(t,oi_samples=evidence['oi_samples'],lsr_samples=evidence['lsr_samples'],cap={}),reference_open=dict(open_time=target,price=100))
    e=store.commit_symbol(item['market'],item['symbol'],[(ot,payload)],dict(last_processed_boundary=target,evidence_unresolved_opens=[ot]),None)[0]
    queue=RepairStore(root);queue.discover([e],now)
    return item,e,queue,store,now

class Repairs(unittest.TestCase):
    def test_historical_recovery_preserves_candles_events_and_notifications(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);item,event,queue,store,now=setup(root);before=store.meta()['notifications']
            cache=MarketCandleCache(root,readonly=True);original=cache.get_bars(market=item['market'],symbol=item['symbol'],interval='1h')
            calls=[]
            def fetch(u,p):calls.append((u,p));return dict(market_caps=[[event['trigger_open_time']+H,1000],[event['trigger_open_time']+2*H,1000]])
            result=run_repairs(root,now,request=fetch)
            self.assertEqual(result['processed'],1);self.assertEqual(result['status'],'success')
            updated=store.get_event(event['event_id'])
            self.assertEqual(updated['structure_gate'],'pass');self.assertEqual(updated['event_revision'],2)
            self.assertEqual(updated['first_seen_at'],event['first_seen_at']);self.assertEqual(len(updated['revisions']),1)
            self.assertEqual(store.meta()['notifications'],before)
            self.assertEqual(cache.get_bars(market=item['market'],symbol=item['symbol'],interval='1h'),original)
            self.assertEqual(queue.summary()['counts'],{'resolved':1});self.assertEqual(len(calls),1)
            self.assertEqual(run_repairs(root,now+60_000,request=fetch)['processed'],0)

    def test_401_backoff_and_queue_survive_restart(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);item,event,q,store,now=setup(root);calls=[]
            def denied(*a):calls.append(1);raise RuntimeError('HTTP 401')
            r=run_repairs(root,now,request=denied)
            self.assertEqual(r['status'],'partial_success');self.assertEqual(q.summary()['counts'],{'blocked':1})
            self.assertFalse(RepairStore(root).due(now+H));self.assertEqual(len(calls),1)
            run_repairs(root,now+7*H,request=denied)
            self.assertEqual(len(calls),1) # persistent per-provider window cooldown
            self.assertEqual(store.get_event(event['event_id'])['structure_gate'],'unknown')

    def test_future_cap_cannot_be_backdated_to_clear_unknown(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);item,event,q,store,now=setup(root)
            run_repairs(root,now,request=lambda *a:dict(market_caps=[[now,1000]]))
            self.assertEqual(store.get_event(event['event_id'])['structure_gate'],'unknown')
            self.assertNotEqual(q.summary()['counts'],{'resolved':1})

    def test_source_gap_recovers_without_oi_lsr_shared_max(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);item,event,q,store,now=setup(root)
            folder=root/'runtime/b-source'/hashlib.sha256(('USDⓈ-M|TESTUSDT').encode()).hexdigest();p=folder/'structure.json'
            evidence=json.loads(p.read_text());evidence['oi_samples']=[];atomic_json(p,evidence);calls=[]
            def fetch(url,params):
                calls.append(url)
                if 'openInterestHist' in url:return [dict(timestamp=t,sumOpenInterestValue=500,sumOpenInterest=5) for t in range(params['startTime'],params['endTime']+H,H)]
                if 'market_chart' in url:return dict(market_caps=[[event['trigger_open_time']+H,1000],[event['trigger_open_time']+2*H,1000]])
                self.fail('unexpected fetch '+url)
            result=run_repairs(root,now,request=fetch)
            self.assertEqual(result['status'],'success');self.assertEqual(len(calls),2)
            self.assertFalse(any('globalLong' in u for u in calls))

    def test_commit_succeeds_but_queue_ack_fails_replays_idempotently(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);item,event,q,store,now=setup(root)
            fetch=lambda *a:dict(market_caps=[[event['trigger_open_time']+H,1000],[event['trigger_open_time']+2*H,1000]])
            with patch.object(RepairStore,'finish',side_effect=OSError('disk full')):
                with self.assertRaises(OSError):run_repairs(root,now,request=fetch)
            updated=store.get_event(event['event_id']);notifications=store.meta()['notifications']
            run_repairs(root,now+6*60_000,request=lambda *a:(_ for _ in ()).throw(AssertionError('no network required')))
            self.assertEqual(store.get_event(event['event_id'])['event_revision'],updated['event_revision'])
            self.assertEqual(store.meta()['notifications'],notifications)
            self.assertEqual(q.summary()['counts'],{'resolved':1})

    def test_event_commit_failure_keeps_source_and_job_for_retry(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);item,event,q,store,now=setup(root)
            fetch=lambda *a:dict(market_caps=[[event['trigger_open_time']+H,1000],[event['trigger_open_time']+2*H,1000]])
            with patch.object(SQLiteBStore,'commit_symbol',side_effect=OSError('event disk full')):
                result=run_repairs(root,now,request=fetch)
            self.assertEqual(result['status'],'partial_success');self.assertEqual(store.get_event(event['event_id'])['event_revision'],1)
            self.assertTrue(q.caps(item['market'],item['symbol']))
            run_repairs(root,now+6*60_000,request=lambda *a:(_ for _ in ()).throw(AssertionError('sources retained')))
            self.assertEqual(store.get_event(event['event_id'])['structure_gate'],'pass')

    def test_settings_pause_and_single_data_lane_without_enabling_plans(self):
        from task_service import TaskService
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);item,event,q,store,now=setup(root)
            service=TaskService(root,lambda:self.fail('no market scan'),clock=lambda:dt.datetime.fromtimestamp(now/1000,dt.timezone.utc))
            service._tick_evidence_repair();tasks=service.tasks()
            self.assertEqual([t['kind'] for t in tasks],['b_evidence_repair'])
            service._tick_evidence_repair();self.assertEqual(len(service.tasks()),1)
            self.assertFalse(service.settings()['scan']['enabled']);self.assertFalse(service.settings()['observe']['enabled'])
            q.set_enabled(False);self.assertFalse(q.due(now+24*H));service.close()

    def test_preclose_cap_task_requires_enabled_b_plan(self):
        from task_service import TaskService
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);item,event,q,store,now=setup(root)
            moment=dt.datetime.fromtimestamp((now//H*H+55*60_000)/1000,dt.timezone.utc)
            service=TaskService(root,lambda:None,clock=lambda:moment)
            with service._db() as db:
                config=json.loads(db.execute('SELECT observe FROM settings WHERE id=1').fetchone()[0]);config['enabled']=True
                db.execute('UPDATE settings SET observe=?',(json.dumps(config),))
            service._tick_evidence_repair();self.assertEqual(service.tasks()[0]['kind'],'b_cap_refresh')
            self.assertFalse(service.settings()['scan']['enabled']);service.close()

    def test_budget_rejects_extra_http(self):
        b=Budget(request=lambda *a:{},max_requests=1);b('https://example.test',{})
        with self.assertRaisesRegex(RuntimeError,'budget'):b('https://example.test',{})

    def test_readers_allow_initial_schema_transaction_without_creating_tables(self):
        import sqlite3
        with tempfile.TemporaryDirectory() as td:
            q=RepairStore(td);q.path.parent.mkdir(parents=True)
            db=sqlite3.connect(q.path);db.close()
            self.assertTrue(q.summary()['initializing']);self.assertEqual(q.caps('m','s'),[])
            self.assertFalse(q.due(0));q.initialize();self.assertTrue(q.ready())

    def test_repair_registration_disk_failure_does_not_invalidate_published_scan(self):
        from task_service import TaskService
        with tempfile.TemporaryDirectory() as td:
            s=TaskService(Path(td),lambda:dict(quality=dict(status='success'),scan_id='already-published',received=1,total=1))
            task=s.submit()['task']
            with patch.object(RepairStore,'record_caps',side_effect=OSError('queue disk full')):
                s._run(task['id'])
            saved=s.task(task['id']);s.close()
            self.assertEqual(saved['status'],'success')
            self.assertIn('queue disk full',saved['result']['evidence_repair_registration_error'])

    def test_preclose_no_supported_provider_is_not_full_success(self):
        from b_cap_history import refresh_current
        with tempfile.TemporaryDirectory() as td:
            out=refresh_current(td,[dict(market='m',symbol='s')],0,lambda *a:self.fail('no verified ID'))
            self.assertEqual(out['status'],'partial_success');self.assertEqual(out['unsupported_contracts'],1)

    def test_coinpaprika_history_keeps_source_time_and_freshness(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);item,e,q,store,now=setup(root)
            item.update(capSource='CoinPaprika',capId='test-test-coin')
            target=e['trigger_open_time']+H
            stamp=dt.datetime.fromtimestamp(target/1000,dt.timezone.utc).isoformat()
            r=fetch_historical(root,item,[target],now,lambda u,p:[dict(timestamp=stamp,market_cap=1234)])
            self.assertFalse(r['blocked'])
            cap=q.caps(item['market'],item['symbol'],item)[0]
            self.assertEqual(cap['capUsd'],1234);self.assertEqual(cap['capObservedAt'],target)
            self.assertEqual(cap['capSource'],'CoinPaprika')

    def test_unavailable_source_does_not_create_meaningless_event_revision(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);item,event,q,store,now=setup(root)
            run_repairs(root,now,request=lambda *a:(_ for _ in ()).throw(RuntimeError('HTTP 401')))
            self.assertEqual(store.get_event(event['event_id'])['event_revision'],1)

    def test_historical_background_recovery_cannot_truncate_current_4h_archive(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);item,e,q,store,now=setup(root)
            payload={k:v for k,v in e.items() if k not in ('revisions',)}
            payload.update(bg4h=None,bg4h_h1=None,technical_event='unknown',technical_confirmation='unknown',input_fingerprint='missing-background')
            e=store.upsert_event(market=e['market'],symbol=e['symbol'],trigger_open_time=e['trigger_open_time'],payload=payload)
            q.discover([e],now)
            p=root/'runtime/b-source'/hashlib.sha256(('USDⓈ-M|TESTUSDT').encode()).hexdigest()/'4h.json'
            atomic_json(p,dict(raw=[],protected_current_archive=True));original=p.read_bytes()
            def fetch(url,params):
                if 'market_chart' in url:return dict(market_caps=[[e['trigger_open_time']+H,1000],[e['trigger_open_time']+2*H,1000]])
                self.assertEqual(params['interval'],'4h')
                return [[t,100,102,99,101,1,t+4*H-1,10] for t in list(range(params['startTime'],params['endTime'],4*H))[:500]]
            result=run_repairs(root,now,request=fetch)
            self.assertNotIn('background:',str(result));self.assertEqual(p.read_bytes(),original)

if __name__=='__main__':unittest.main()
