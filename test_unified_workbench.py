import copy, datetime as dt, json, tempfile, unittest
from pathlib import Path
from unified_workbench import load_payload, available_event

class UnifiedEvidence(unittest.TestCase):
    def fixture(self,root):
        (root/'runtime').mkdir()
        (root/'scan.json').write_text(json.dumps(dict(scan_id='frozen',revision=1,finishedAt='2026-10-16T10:00:00Z',available_at='2026-10-16T10:01:00Z',rows=[dict(market='USDⓈ-M',symbol='TESTUSDT',quote='USDT',oiCapPct=40,accountRatio=.6,capUsd=20000000,capTrusted=True,fieldAvailableAt={'oiCapPct':'2026-10-16T11:00:00Z'})])),encoding='utf8')
        (root/'runtime/b-accumulation-events.json').write_text(json.dumps(dict(schema='b-accumulation-events-v2',events={'one':dict(event_id='one',market='USDⓈ-M',symbol='TESTUSDT',trigger_open_time=1,schema_version=2,first_seen_at='2026-10-16T11:00:00Z',last_evaluated_at='2026-10-16T11:00:00Z',event_revision=1,technical_event='pass',technical_confirmation='pending')})),encoding='utf8')
    def test_no_future_or_current_pool_in_snapshot(self):
        with tempfile.TemporaryDirectory() as d:
            r=Path(d);self.fixture(r)
            historical=load_payload(r,'snapshot');current=load_payload(r)
            self.assertEqual(historical['event_count'],0)
            self.assertIsNone(historical['rows'][0].get('oiCapPct'))
            self.assertEqual(current['event_count'],1)
            self.assertNotEqual(current['generation'],historical['generation'])
    def test_no_mutations_and_stable_generation(self):
        with tempfile.TemporaryDirectory() as d:
            r=Path(d);self.fixture(r);before={str(p):p.read_bytes() for p in r.rglob('*') if p.is_file()}
            a=load_payload(r);b=load_payload(r)
            self.assertEqual(a['generation'],b['generation'])
            self.assertEqual(before,{str(p):p.read_bytes() for p in r.rglob('*') if p.is_file()})
            self.assertFalse(a['simulation_enabled']);self.assertEqual(a['c_status'],'connected_disabled')
    def test_exact_market_identity_archive_only(self):
        with tempfile.TemporaryDirectory() as d:
            r=Path(d);self.fixture(r);p=r/'runtime/b-accumulation-events.json';v=json.loads(p.read_text());v['events']['one']['market']='COIN-M';p.write_text(json.dumps(v))
            a=load_payload(r);self.assertEqual(a['contract_count'],2)
            self.assertEqual(a['rows'][1]['unifiedListing'],'unknown')
            self.assertEqual(a['rows'][1]['legacySixStates'],[None]*6)
    def test_revision_asof(self):
        a=dict(first_seen_at='2026-10-16T09:00:00Z',last_evaluated_at='2026-10-16T09:00:00Z',event_revision=1)
        b=dict(first_seen_at=a['first_seen_at'],last_evaluated_at='2026-10-16T11:00:00Z',event_revision=2,revisions=[{'snapshot':a}])
        cutoff=dt.datetime(2026,10,16,10,tzinfo=dt.timezone.utc).timestamp()*1000
        self.assertEqual(available_event(b,cutoff)['event_revision'],1)
    def test_invalid_context(self):
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaises(ValueError):load_payload(d,'future')
    def test_exact_published_observation_required_for_no_signal(self):
        import sqlite3
        from contextlib import closing
        with tempfile.TemporaryDirectory() as d:
            r=Path(d);self.fixture(r)
            target=360000000
            (r/'runtime/b-observe-last-round.json').write_text(json.dumps({'target_close_boundary':target}))
            with closing(sqlite3.connect(r/'runtime/b-observations.sqlite3')) as db, db:
                db.execute('CREATE TABLE round_observations(market,symbol,target,payload)')
                db.execute('INSERT INTO round_observations VALUES(?,?,?,?)',('USDⓈ-M','TESTUSDT',target,json.dumps(dict(target_close_boundary=target,open_time=target-3600000,technical='fail'))))
            current=load_payload(r)
            self.assertEqual(current['rows'][0]['bMonitoring'],'none')
            historical=load_payload(r,'snapshot')
            self.assertEqual(historical['rows'][0]['bMonitoring'],'unmonitored')
            with closing(sqlite3.connect(r/'runtime/b-observations.sqlite3')) as db, db:
                db.execute('UPDATE round_observations SET payload=?',(json.dumps(dict(target_close_boundary=target,open_time=target-7200000,technical='fail')),))
            later=load_payload(r)
            self.assertNotEqual(current['generation'],later['generation'])
            self.assertNotEqual(later['rows'][0]['bMonitoring'],'none')

class UnifiedResearch(unittest.TestCase):
    def test_rejected_small_writes_are_consistently_delivered_without_tasks(self):
        from test_task_service import HttpTests
        fixture=HttpTests();fixture.setUp();self.addCleanup(fixture.tearDown)
        for _ in range(20):
            self.assertEqual(fixture.request('/api/scans','POST',{}, {'Origin':'https://evil.test'})[0],403)
            self.assertEqual(fixture.request('/api/scans','POST',{}, {'Content-Type':'text/plain'})[0],415)
        self.assertEqual(fixture.service.tasks(),[])
    def test_generation_gate_and_frozen_b(self):
        from test_ai_research import AiResearchTests
        from ai_research import ResearchError
        fixture=AiResearchTests();fixture.setUp();self.addCleanup(fixture.tearDown)
        p=load_payload(fixture.root)
        body={'contracts':[{'market':'USDⓈ-M','symbol':'BTCUSDT'}],
              'unified_context':{'schema':'unified-signals-v33r5','generation':p['generation'],'context':'latest','query':{'signals':['b']}}}
        request=fixture.research.prepare(body)
        evidence=request['_evidence']['rows'][0]['unifiedEvidence']
        self.assertEqual(evidence['generation'],p['generation'])
        self.assertEqual(evidence['C_status'],'not_connected')
        self.assertEqual(evidence['query_description'],{'signals':['b']})
        body['unified_context']['generation']='stale'
        with self.assertRaises(ResearchError):fixture.research.prepare(body)
    def test_exact_market_report_identity(self):
        from test_ai_research import AiResearchTests
        fixture=AiResearchTests();fixture.setUp();self.addCleanup(fixture.tearDown)
        snap=fixture.research._load_snapshot()
        same=dict(snap['rows'][0],market='COIN-M',price=77)
        snap['rows'].append(same)
        result=fixture.research._slim_evidence_rows([{'market':'USDⓈ-M','symbol':'BTCUSDT'}],snap)
        self.assertEqual(result[0]['market'],'USDⓈ-M')
        self.assertEqual(result[0]['price'],1)

if __name__=='__main__':unittest.main()
