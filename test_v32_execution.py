"""v3.2 r2 adversarial acceptance: temporal leakage, revisions, recovery and retention."""
import copy,datetime as dt,gzip,hashlib,json,os,tempfile,unittest
from pathlib import Path
from b_accumulation_rules import *
from b_accumulation_store import BAccumulationStore,stable_event_id
from b_process_order import process_bars_in_order
from b_evidence import background_at,structure_at
from b_observe_runner import run_b_observe_round
from b_observe_api import events_payload,events_csv
from b_1h_ingest import closed_1h_bars,ingest_symbol_1h
from market_candle_cache import MarketCandleCache,read_json_maybe_gzip
from observe_config import OBSERVE_DEFAULT,TIMING_DEFAULT,validate_observe
from b_public_adapter import PublicAdapters
from b_observe_universe import fetch_universe,load_universe
from cache_retention import maintain,references,resume,safe_path,backup_removals
from snapshot_store import ScanArchive,encoded
from price_index import merge_series,summarize_price_index
H=3600000;T=int(dt.datetime(2026,10,16,12,tzinfo=dt.timezone.utc).timestamp()*1000)

def bars(n=170):
    start=T-170*H
    return [dict(open_time=start+i*H,o=100.,h=102.,l=99.,c=101.,q=10. if i<168 else 80. if i==168 else 25.) for i in range(n)]
def raw(rows):return [[b['open_time'],b['o'],b['h'],b['l'],b['c'],1,b['open_time']+H-1,b['q'],1,1,1,0] for b in rows]
def background():
    return [[T-(1600-i)*4*H,100+i,102+i,99+i,101+i,1,T-(1600-i-1)*4*H-1,100,1,1,1,0] for i in range(1602)]
def evidence(ls=.5):
    return dict(oi_samples=[dict(timestamp=t,sumOpenInterestValue=40,sumOpenInterest=4) for t in (T-H,T)],lsr_samples=[dict(timestamp=t,longShortRatio=ls) for t in (T-H,T)])
def run(root,target=T,ls=.5):
    item=dict(market='USDⓈ-M',symbol='TESTUSDT',capUsd=100,capTrusted=True,capObservedAt=T-H)
    return run_b_observe_round(root,symbols=[item],observe=dict(OBSERVE_DEFAULT,enabled=True),timing=TIMING_DEFAULT,now_ms=T+600000,target_close_boundary=target,fetch_1h=lambda m,s,**kw:raw(bars()),fetch_background=lambda i,t:background(),fetch_evidence=lambda i,t:evidence(ls),fetch_reference=lambda i,t:dict(open_time=t,price=101),fetch_quote=lambda i,t:dict(price=102,retrieved_at=T+600000))
class Acceptance(unittest.TestCase):
    def test_B01_gap_between_baseline_and_event(self):
        b=bars();b[168]['open_time']+=H
        self.assertIsNone(volume_ratio(b,168,168,include_prior_event_bar=False))
    def test_B02_B03_B09_equalities(self):
        b=bars();self.assertEqual(evaluate_h_event(b,168,bg4h={'ema7':2,'ema25':1})['technical_event'],'pass')
        b[169]['q']=2*sum(x['q'] for x in b[1:169])/168
        self.assertEqual(evaluate_h1_confirm(b,b[168]['open_time'],bg4h_h1={'ema7':2,'ema25':1},h1_closed=True)['technical_confirmation'],'pass')
        self.assertFalse(abs_return_ok({'o':100,'c':103}));self.assertFalse(abs_return_ok({'o':100,'c':97}))
        self.assertEqual(structure_gate(oi_cap_pct=30,lsr=.5),'fail');self.assertEqual(structure_gate(oi_cap_pct=31,lsr=.8),'fail')
        self.assertEqual(risk_theory(p_theory=100,s0=100),'fail')
    def test_B04_B06_no_future(self):
        b=bars();bg=background()
        self.assertEqual(background_at(bg,T-H)['close_boundary'],T-4*H)
        self.assertEqual(background_at(bg,T)['close_boundary'],T)
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);run(root,T-H)
            event=BAccumulationStore(root).list_events()[0]
            self.assertEqual(event['technical_confirmation'],'pending')
            self.assertNotEqual(event['bg4h']['open_time'],background_at(bg,T)['open_time'])
    def test_B05_confirm_and_new_event(self):
        b=bars();b[169]['q']=90
        with tempfile.TemporaryDirectory() as d:
            result=process_bars_in_order(Path(d),market='USDⓈ-M',symbol='X',bars_asc=b,bg4h_for=lambda t:{'ema7':2,'ema25':1},target_close_boundary=T)
            events=BAccumulationStore(Path(d)).list_events();self.assertEqual(len(events),2)
            old=next(e for e in events if e['trigger_open_time']==b[168]['open_time']);self.assertEqual(old['technical_confirmation'],'pass')
    def test_B07_B08_quality_fail_precedence(self):
        for delta,q in [(0,'pass'),(300000,'pass'),(300001,'unknown'),(-1,'unknown')]:
            s=structure_at(T,oi_samples=[dict(timestamp=T-delta,sumOpenInterestValue=40)],lsr_samples=[dict(timestamp=T,longShortRatio=.5)],cap=dict(capUsd=100,capTrusted=True,capObservedAt=T))
            self.assertEqual(s['oi_quality'],q)
        s=structure_at(T,oi_samples=[],lsr_samples=[dict(timestamp=T,longShortRatio=.9)],cap={})
        self.assertEqual(s['state'],'fail')
        s=structure_at(T,oi_samples=evidence()['oi_samples'],lsr_samples=evidence()['lsr_samples'],cap=dict(capUsd=100,capTrusted=True,capObservedAt=T+1));self.assertEqual(s['state'],'unknown')
    def test_B10_B11_B12_B14_end_to_end_revision(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);store=BAccumulationStore(root);store.ensure_observation_start('2026-10-01T00:00:00+00:00')
            run(root,T-H);pending=store.list_events()[0];eid=pending['event_id'];first=pending['first_seen_at']
            run(root,T,.9);failed=store.list_events()[0];self.assertEqual(failed['structure_gate'],'fail')
            run(root,T,.5);confirmed=store.list_events()[0]
            self.assertEqual(confirmed['event_id'],eid);self.assertEqual(confirmed['first_seen_at'],first)
            self.assertEqual(confirmed['structure_gate'],'pass');self.assertEqual(confirmed['P_theory'],101);self.assertEqual(confirmed['P_observed'],102)
            self.assertEqual(confirmed['expected_confirmation_open_time']+H,T)
            rev=confirmed['event_revision'];run(root,T,.5);self.assertEqual(store.list_events()[0]['event_revision'],rev)
            self.assertEqual(len(store.meta()['notifications']),2)
            self.assertEqual(events_payload(root)['events'][0]['stage'],'完整确认');self.assertIn('structure_confirmation',events_csv(root))
    def test_B13_historical_no_notification(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);BAccumulationStore(root).ensure_observation_start('2026-10-17T00:00:00+00:00');run(root)
            self.assertTrue(BAccumulationStore(root).list_events()[0]['historical']);self.assertEqual(BAccumulationStore(root).meta()['notifications'],[])
    def test_B15_market_isolation_and_frozen_profile(self):
        self.assertNotEqual(stable_event_id(market='COIN-M',symbol='X',trigger_open_time=1),stable_event_id(market='USDⓈ-M',symbol='X',trigger_open_time=1))
        with self.assertRaises(ValueError):validate_observe(dict(OBSERVE_DEFAULT,event_ratio=3.0))
    def test_quote_units_and_unclosed(self):
        r=raw(bars(1));r[0][5]=7;r[0][7]=999
        self.assertEqual(closed_1h_bars(r,market='COIN-M',contract_size=100,target_close_boundary=T)[0]['q'],700)
        self.assertEqual(closed_1h_bars(r,market='USDⓈ-M',target_close_boundary=T)[0]['q'],999)
        self.assertEqual(closed_1h_bars(r,market='COIN-M',target_close_boundary=T),[])
        r[0][6]+=1;self.assertEqual(closed_1h_bars(r,target_close_boundary=T),[])
    def test_F01_cache_price_api_continuity(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);cache=MarketCandleCache(root);ingest_symbol_1h(cache,market='USDⓈ-M',symbol='X',raw_klines=raw(bars()),target_close_boundary=T)
            merged=merge_series(root,market='USDⓈ-M',symbol='X',interval='1h',include_bars=True)
            self.assertEqual(merged['known'],170);self.assertEqual(merged['bars'][-1][7],25)
            self.assertEqual(summarize_price_index(root,interval='1h')['total_series'],1)
    def test_F02_A01_shared_roundtrip_dedup_corruption(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);row={'market':'USDⓈ-M','symbol':'X'};a=ScanArchive(root);r=raw(bars())
            rel=a.candles(row,'1h',r);doc=read_json_maybe_gzip(root/rel);self.assertEqual(doc['raw'],r)
            count=len(list((root/'runtime/shared-candles').glob('*.gz')))
            b=ScanArchive(root);b.candles(row,'1h',r)
            self.assertEqual(len(list((root/'runtime/shared-candles').glob('*.gz'))),count)
            manifest=json.loads((root/rel).read_text());p=root/manifest['chunks'][0]['path'];p.write_bytes(b'corrupt')
            with self.assertRaises(ValueError):read_json_maybe_gzip(root/rel)
    def test_F03_directory_failure_no_A_fallback(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);(root/'runtime').mkdir();(root/'runtime/ema-squeeze-candidates.json').write_text('{"symbols":[{"market":"USDⓈ-M","symbol":"A"}]}')
            self.assertEqual(load_universe(root),[])
            with self.assertRaises(RuntimeError):fetch_universe(root,lambda *a:(None,'timeout'))
            out=run_b_observe_round(root,symbols=[],observe=dict(OBSERVE_DEFAULT,enabled=True),timing=TIMING_DEFAULT,now_ms=T);self.assertEqual(out['status'],'failed')
    def test_R01_R02_R04_retention(self):
        now=2000000000
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);(root/'logs').mkdir();p=root/'logs/old.log';p.write_text('old');os.utime(p,(now-30*86400,now-30*86400))
            plan=maintain(root,now=now,dry_run=True);self.assertEqual(plan['items'],[])
            os.utime(p,(now-30*86400-1,now-30*86400-1));plan=maintain(root,now=now,dry_run=False);self.assertFalse(p.exists());self.assertEqual(plan['items'][0]['state'],'deleted')
            for i in range(4):
                folder=root/'scan-history'/('scan-'+str(i)*32);folder.mkdir(parents=True)
                r=folder/'revision-0001.json';r.write_text(json.dumps(dict(available_at='2020-01-01T00:00:00+00:00',quality={'status':'partial_success'})));os.utime(r,(now-100*86400,now-100*86400))
                (folder/'status.json').write_text(json.dumps(dict(status='partial_success',revision=1,updatedAt=str(i))))
            (root/'scan.json').write_text(json.dumps(dict(scan_id='scan-'+'0'*32)))
            plan=maintain(root,now=now,dry_run=False)
            self.assertEqual(len(list((root/'scan-history').glob('scan-*/revision-0001.json'))),4)
            self.assertTrue(all(i['state']=='skipped' for i in plan['items'] if i['category']=='full_snapshot'))
            with self.assertRaises(ValueError):safe_path(root,'../outside.log')
    def test_R06_recovery_types_and_chains(self):
        now=2000000000
        data=[dict(id='data0',type='data',verified=True,status='success',completed_at=now-50*86400)]
        data += [dict(id='config'+str(i),type='config',verified=True,status='success',completed_at=now-(40+i)*86400) for i in range(5)]
        deleted=backup_removals(data,now);self.assertNotIn('data0',deleted);self.assertEqual(len(deleted),2)
        data[-1]['depends_on']=['data0'];self.assertNotIn('data0',backup_removals(data,now))

    def test_S01_S02_inflight_target_and_browser_settings(self):
        from task_service import TaskService
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);clock=lambda:dt.datetime(2026,10,16,12,3,tzinfo=dt.timezone.utc)
            service=TaskService(root,lambda:None,clock=clock)
            settings=service.settings();payload=dict(settings['observe'],event_ratio=8,confirm_ratio=2)
            saved=service.save_settings(dict(version=settings['version'],scan=settings['scan'],observe=payload,timing=settings['timing']))
            self.assertFalse(saved['scan']['enabled']);self.assertFalse(saved['observe']['enabled'])
            with service._db() as db:
                a,reused=service._enqueue_b_observe(db,'manual',1,T)
                b,reused=service._enqueue_b_observe(db,'manual',1,T+H)
            self.assertEqual(a,b);self.assertTrue(reused)
            self.assertEqual(service.task(a)['request']['target_close_boundary'],T);service.close()
    def test_R03_new_reference_blocks_resume(self):
        now=2000000000
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);(root/'logs').mkdir();p=root/'logs/old.log';p.write_text('keep');os.utime(p,(now-40*86400,now-40*86400))
            plan=maintain(root,now=now,dry_run=True)
            (root/'runtime/protected-references.json').write_text(json.dumps({'paths':['logs/old.log']}))
            journal=root/'runtime/cleanup-audit'/(plan['id']+'.json');result=resume(root,journal)
            self.assertFalse(result['resumed']);self.assertTrue(p.exists())
    def test_R05_hourly_prune_respects_frozen_hash(self):
        from cache_retention import prune_hourly
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);cache=MarketCandleCache(root)
            a=cache.put_bar(market='USDⓈ-M',symbol='X',interval='1h',open_time=0,bar={'o':1,'h':1,'l':1,'c':1,'q':1})
            b=cache.put_bar(market='USDⓈ-M',symbol='X',interval='1h',open_time=H,bar={'o':1,'h':1,'l':1,'c':1,'q':1})
            journal=root/'runtime/cleanup-audit/test.json';plan={}
            prune_hourly(root,{'hashes':{a['payload_hash']}},journal,plan,400*86400)
            remaining=cache.get_bars(market='USDⓈ-M',symbol='X',interval='1h')
            self.assertEqual([r['open_time'] for r in remaining],[0])

    def test_repeat_acquisition_and_failed_denominator_do_not_advance_cursor(self):
        import sqlite3
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);cache=MarketCandleCache(root)
            for i in range(2):cache.put_bar(market='USDⓈ-M',symbol='X',interval='1h',open_time=T,bar={'o':1,'h':1,'l':1,'c':1,'q':1})
            with sqlite3.connect(cache.db_path) as db:
                self.assertEqual(db.execute('SELECT count(*) FROM blocks').fetchone()[0],1)
                self.assertEqual(db.execute('SELECT count(*) FROM retrievals').fetchone()[0],2)
            db.close()
            def broken(rows):raise OSError('ledger unavailable')
            with self.assertRaises(OSError):process_bars_in_order(root,market='USDⓈ-M',symbol='X',bars_asc=bars(),target_close_boundary=T,observation_sink=broken)
            self.assertIsNone(BAccumulationStore(root).cursor('USDⓈ-M','X'))
    def test_public_adapter_shared_background_and_repeated_fetch(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);requests=[]
            def requester(url,params):
                requests.append((url,params));return [r for r in background() if params.get('startTime',0)<=r[0]<=params.get('endTime',T)][:500],None
            adapter=PublicAdapters(root,requester);item={'market':'USDⓈ-M','symbol':'TESTUSDT'}
            first=adapter.background(item,T);before=len(requests)
            second=adapter.background(item,T+H)
            self.assertTrue(first);self.assertEqual(first,second);self.assertEqual(len(requests),before+1)
            self.assertEqual(requests[-1][1]['startTime'],int(first[-1][0])) # bounded revision overlap
            doc=json.loads((adapter.folder(item)/'4h.json').read_text());self.assertEqual(doc['storage_format'],'shared-raw-v1')

    def test_missing_optional_quote_preserves_confirmed_event(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d)
            def offline():raise OSError('quote offline')
            process_bars_in_order(root,market='USDⓈ-M',symbol='X',bars_asc=bars(),target_close_boundary=T,bg4h_for=lambda t:{'ema7':2,'ema25':1,'ema99':90},reference_open=offline,observed=offline)
            event=BAccumulationStore(root).list_events()[0]
            self.assertEqual(event['technical_confirmation'],'pass');self.assertIsNone(event['P_theory']);self.assertIsNone(event['P_observed'])
            self.assertEqual(event['risk_observed'],'unknown');self.assertIn('offline',event['reference_error'])

    def test_retention_override_and_log_rotation(self):
        from cache_retention import validate_retention,effective_policy,RETENTION_DEFAULTS
        with self.assertRaises(ValueError):validate_retention(dict(RETENTION_DEFAULTS,backup_min_success=2))
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);(root/'runtime').mkdir();(root/'logs').mkdir();now=2000000000
            (root/'runtime/cleanup-settings.json').write_text(json.dumps({'enabled':True,'retention':dict(RETENTION_DEFAULTS,log_days=40)}))
            p=root/'logs/closed.log';p.write_text('original log');os.utime(p,(now-35*86400,now-35*86400))
            self.assertEqual(maintain(root,now=now,dry_run=True)['items'],[])
            os.utime(p,(now-41*86400,now-41*86400));maintain(root,now=now,dry_run=False)
            compressed=root/'logs/closed.log.gz';self.assertEqual(gzip.decompress(compressed.read_bytes()),b'original log')
            maintain(root,now=now+86400,dry_run=False);self.assertFalse(compressed.exists())

    def test_legacy_records_are_not_promoted_to_r2_complete(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);(root/'runtime').mkdir()
            legacy={'schema':'b-accumulation-events-v1','strategy_id':STRATEGY_ID,'rule_version':RULE_VERSION,'observation_started_at':'2026-10-16T14:00:00+00:00','events':{'old':{'event_id':'old','market':'USDⓈ-M','symbol':'X','trigger_open_time':T-2*H,'technical_event':'pass','technical_confirmation':'pass','structure_gate':'pass'}}}
            p=root/'runtime/b-accumulation-events.json';p.write_text(json.dumps(legacy));before=p.read_bytes()
            result=events_payload(root);self.assertEqual(result['events'][0]['stage'],'旧版待复核');self.assertTrue(result['events'][0]['historical']);self.assertEqual(before,p.read_bytes())
