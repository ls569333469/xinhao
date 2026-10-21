"""Offline regressions: cadence, transactional imports, reviewed methods, actual executor overlap."""
import concurrent.futures
import copy
import json
import threading
import time
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from coverage_gate import evaluate_symbol_coverage, assert_autofill_symbol_gate
from candle_ranges import date, STEPS
from test_price_index import write_scan_candles, bar, START, CONTRACT
from research_experience import ExperienceStore, ExperienceConflict
from ai_research import AiResearch
from task_service import TaskService
from strategy_store import StrategyStore
from test_r4_con_usage import Base, VendorStub


METHOD = {'title':'区分计划与落地','trigger':'重大迁移叙事','action':'核查官方提案与执行状态','boundary':'历史案例不作当前事实','counterexample':'没有正式决议','sources':['https://example.test/reference']}


class BoundaryTests(unittest.TestCase):
    def test_sparse_and_missing_edges_refused_real_archives(self):
        for interval in ('4h', '1d'):
            step = STEPS[interval]
            for indices, span, expected in [(range(0,40,2),40,False),(range(10,30),40,False),(range(20),20,True)]:
                with self.subTest(interval=interval,indices=str(indices)), TemporaryDirectory() as td:
                    root=Path(td)
                    (root/'scan.json').write_text(json.dumps({'rows':[{**CONTRACT,'price':100}],'total':1}),encoding='utf-8')
                    write_scan_candles(root,'scan-'+'e'*32,CONTRACT,interval,[bar(START+i*step,step=step) for i in indices])
                    verdict=evaluate_symbol_coverage(root,CONTRACT['symbol'],interval=interval,start=date(START),end=date(START+span*step))
                    self.assertEqual(verdict['ok'],expected,verdict)
                    if len(indices)==20 and indices.step==2:
                        self.assertFalse(assert_autofill_symbol_gate(root,CONTRACT['symbol'],interval=interval)['ok'])

    def test_duplicate_import_has_no_orphan_draft(self):
        with TemporaryDirectory() as td:
            root=Path(td)
            (root/'scan.json').write_text(json.dumps({'rows':[{**CONTRACT,'price':1}],'total':1,'received':1}),encoding='utf-8')
            store=StrategyStore(root)
            d=store.create_draft({'title':'import-test','strategy_id':'S-test','rules':{'entry_text':'a'},'data_dependencies':['snapshot']})
            v=store.freeze(d['draft_id'],{'version':'1.0'})
            pkg=store.export_version(v['version_id'])
            store.import_version(pkg)
            drafts=list((root/'runtime/strategies/drafts').glob('*.json'))
            versions=list((root/'runtime/strategies/versions').glob('*.json'))
            with self.assertRaises(ValueError):store.import_version(pkg)
            self.assertEqual(list((root/'runtime/strategies/drafts').glob('*.json')),drafts)
            self.assertEqual(list((root/'runtime/strategies/versions').glob('*.json')),versions)


class ExperienceTests(Base):
    def adopted(self,r):
        e=r.experiences.create(METHOD)
        e=r.experiences.update(e['id'],{'operation':'review','expected_version':e['version'],'review_note':'方法有依据，历史事实需重查'})
        e=r.experiences.update(e['id'],{'operation':'adopt','expected_version':e['version'],'acknowledge_methods_only':True})
        return e

    def test_candidate_cannot_apply_adopt_requires_review(self):
        store=ExperienceStore(self.root);e=store.create(METHOD)
        ref={k:e[k] for k in ('id','version','content_hash')};ref['selection_reason']='本次有迁移消息'
        with self.assertRaises(ExperienceConflict):store.resolve([ref])
        with self.assertRaises(ExperienceConflict):store.update(e['id'],{'operation':'adopt','expected_version':1,'acknowledge_methods_only':True})
        self.assertEqual(len(store.get(e['id'])['versions']),1)

    def test_feedback_edit_review_disable_and_history(self):
        r=AiResearch(self.root,VendorStub());store=r.experiences
        f=store.feedback('report-'+'a'*32,{'note':'区分计划和完成'})
        e=store.create(dict(METHOD,feedback_id=f['id']))
        self.assertEqual(e['report_id'],f['report_id'])
        e=store.update(e['id'],dict(METHOD,expected_version=1,operation='edit',action='检查提案及投票'))
        old=copy.deepcopy(e)
        e=store.update(e['id'],{'operation':'review','expected_version':2,'review_note':'确认适用边界'})
        with self.assertRaises(ExperienceConflict):store.update(e['id'],{'operation':'disable','expected_version':2,'review_note':'旧版本'})
        e=store.update(e['id'],{'operation':'adopt','expected_version':3,'acknowledge_methods_only':True})
        ref={k:e[k] for k in ('id','version','content_hash')};ref['selection_reason']='本次核验治理'
        self.assertEqual(store.resolve([ref])[0]['id'],e['id'])
        e=store.update(e['id'],{'operation':'disable','expected_version':4,'review_note':'适用边界调整'})
        with self.assertRaises(ExperienceConflict):store.resolve([ref])
        self.assertEqual(store.get(e['id'])['versions'][1],old)
        self.assertEqual(ExperienceStore(self.root).get(e['id'])['entry']['status'],'disabled')

    def test_explicit_selection_identity_model_input_and_freeze(self):
        class CapturingVendor(VendorStub):
            def _request_json(self, method, url, headers, body, timeout):
                self.user_payload=json.loads(body['messages'][1]['content'])
                return super()._request_json(method,url,headers,body,timeout)
        v=CapturingVendor();r=AiResearch(self.root,v);e=self.adopted(r)
        base={'contracts':[{'symbol':'AAAUSDT'}],'requested_search':False}
        no=r.prepare(base);self.assertEqual(no['experiences'],[])
        ref={k:e[k] for k in ('id','version','content_hash')};ref['selection_reason']='迁移计划核验'
        yes=r.prepare(dict(base,experiences=[ref]));yes['task_id']='task-method'
        self.assertNotEqual(no['request_hash'],yes['request_hash'])
        self.assertNotEqual(no['intent_hash'],yes['intent_hash'])
        self.assertEqual(r.preview(dict(base,experiences=[ref]))['experiences'][0]['version'],e['version'])
        # An already confirmed immutable method remains attributable after later edits.
        r.experiences.update(e['id'],dict(METHOD,operation='edit',expected_version=e['version'],action='新版核验'))
        result=r.run(yes)
        rep=r.get_report(result['report_id'])
        self.assertEqual(rep['request']['experiences'][0]['action'],METHOD['action'])
        self.assertEqual(v.user_payload['research_methods']['entries'][0]['action'],METHOD['action'])
        self.assertIn('不是当前事实',v.user_payload['research_methods']['policy'])
        frozen=json.loads((self.root/'runtime/task-freeze/task-method/evidence_freeze.json').read_bytes())
        self.assertEqual(frozen['experiences'][0]['content_hash'],e['content_hash'])
        with self.assertRaises(ExperienceConflict):r.prepare(dict(base,experiences=[ref]))

    def test_concurrent_edit_only_one_version_commits(self):
        store=ExperienceStore(self.root);e=store.create(METHOD)
        def attempt(_):
            try:return store.update(e['id'],dict(METHOD,expected_version=1))['version']
            except ExperienceConflict:return 'conflict'
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:out=list(pool.map(attempt,range(2)))
        self.assertEqual(out.count(2),1);self.assertEqual(out.count('conflict'),1)
        self.assertEqual(len(store.get(e['id'])['versions']),2)

    def test_invalid_and_over_limit_selection(self):
        store=ExperienceStore(self.root)
        for body in ([],None,{'title':'empty'}):
            with self.assertRaises(ValueError):store.create(body)
        for invalid in ([], {}, 12, ''):
            with self.assertRaises(ValueError):store.create(dict(METHOD,feedback_id=invalid))
            with self.assertRaises(ValueError):store.resolve([{'id':invalid}])
        with self.assertRaises(ValueError):store.resolve([{}]*6)


class ExecutorTests(Base):
    def test_real_executor_overlap_progress_cancel_and_scan_lane(self):
        v=VendorStub(delays={'AAAUSDT':.5,'BBBUSDT':.08,'CCCUSDT':.4});r=AiResearch(self.root,v)
        r.max_inflight=2;r._inflight_sem=threading.BoundedSemaphore(2)
        scanned=threading.Event()
        service=TaskService(self.root,lambda:(scanned.set() or {'quality':{'status':'success'},'total':1,'received':1}))
        service.set_research(r)
        def submit(sym,sid):return service.submit_research({'contracts':[{'symbol':sym}],'submission_id':sid})['task']
        try:
            a=submit('AAAUSDT','sub-A');b=submit('BBBUSDT','sub-B');c=submit('CCCUSDT','sub-C');cancel=submit('AAAUSDT','sub-cancel')
            service.cancel(cancel['id']);scan=service.submit()['task']
            with concurrent.futures.ThreadPoolExecutor(max_workers=3) as pool:list(pool.map(lambda _:service.tick(),range(3)))
            self.assertTrue(scanned.wait(2),'long AI blocked data lane')
            deadline=time.monotonic()+2
            while service.task(b['id'])['status']=='running' and time.monotonic()<deadline:time.sleep(.01)
            self.assertEqual(service.task(b['id'])['status'],'success')
            self.assertEqual(service.task(a['id'])['status'],'running')
            self.assertEqual(service.task(c['id'])['status'],'queued')
            self.assertEqual(service.task(cancel['id'])['status'],'cancelled')
            service.tick()  # freed research slot may claim C while A still runs
            self.assertEqual(service.task(c['id'])['status'],'running')
            for _ in range(200):
                if all(service.task(t['id'])['status'] not in ('queued','running') for t in (a,b,c)):break
                service.tick();time.sleep(.01)
            self.assertEqual(v.peak,2)
            self.assertEqual(len(v.calls),3)
            reports=[r.get_report(service.task(t['id'])['result']['report_id']) for t in (a,b,c)]
            self.assertEqual([x['submission_id'] for x in reports],['sub-A','sub-B','sub-C'])
            self.assertEqual([x['contracts'][0]['symbol'] for x in reports],['AAAUSDT','BBBUSDT','CCCUSDT'])
            self.assertEqual(len({x['report_id'] for x in reports}),3)
            self.assertEqual(service.task(scan['id'])['status'],'success')
        finally:service.close()


class HttpPreviewTests(Base):
    def test_preview_confirm_roundtrip_and_stale_settings(self):
        from http.server import ThreadingHTTPServer
        from urllib.request import Request, urlopen
        from urllib.error import HTTPError
        from server import handler_factory
        from rule_bindings import RuleBindings
        v=VendorStub();r=AiResearch(self.root,v)
        store=StrategyStore(self.root)
        d=store.create_draft({'title':'HTTP test','rules':{},'data_dependencies':['snapshot']})
        ver=store.freeze(d['draft_id'])
        bindings=RuleBindings(self.root,store);bindings.bind_all({'version_id':ver['version_id']})
        service=TaskService(self.root,lambda:None);service.set_research(r)
        http=ThreadingHTTPServer(('127.0.0.1',0),None)
        origin='http://127.0.0.1:'+str(http.server_port)
        http.RequestHandlerClass=handler_factory(self.root,service,prototype=True,ai=v,research=r,allowed_hosts={origin.split('//')[1]})
        thread=threading.Thread(target=http.serve_forever,daemon=True);thread.start()
        def call(path,body):
            req=Request(origin+path,data=json.dumps(body).encode(),headers={'Origin':origin,'Content-Type':'application/json'})
            try:
                with urlopen(req,timeout=5) as out:return out.status,json.load(out)
            except HTTPError as exc:return exc.code,json.load(exc)
        try:
            for mode in ('snapshot','scan_and_research'):
                body={'mode':mode,'contracts':[{'symbol':'AAAUSDT'}],'requested_search':False}
                code,preview=call('/api/reports/preview',body);self.assertEqual(code,200,preview)
                self.assertEqual(v.calls,[])
                confirmed=dict(body,submission_id=preview['submission_id'],preview_intent_hash=preview['intent_hash'])
                code,task=call('/api/reports',confirmed);self.assertEqual(code,202,task)
                code,retry=call('/api/reports',confirmed);self.assertEqual(code,202,retry)
                self.assertEqual(task['task']['id'],retry['task']['id']);self.assertTrue(retry['reused'])
            code,preview=call('/api/reports/preview',body)
            settings=service.settings();service.save_settings({'version':settings['version'],'scan':settings['scan']})
            code,denied=call('/api/reports',dict(body,submission_id=preview['submission_id'],preview_intent_hash=preview['intent_hash']))
            self.assertEqual(code,409,denied);self.assertEqual(len(service.tasks()),2)
            self.assertEqual(v.calls,[])
        finally:http.shutdown();thread.join(3);http.server_close();service.close()


class FailedResponseTests(Base):
    def test_http_error_retained_redacted_and_not_retried(self):
        from ai_research import ResearchError
        from test_standard_research_acceptance import NativeVendor
        class ErrorVendor(NativeVendor):
            def _request_json(self, method, url, headers, body, timeout):
                self.requests.append(body)
                return 400,json.dumps({'error':{'message':'unsupported parameter, echoed '+self.store.read_key()}}).encode(),{}
        v=ErrorVendor();r=AiResearch(self.root,v)
        req=r.prepare({'contracts':[{'symbol':'AAAUSDT'}],'requested_search':True});req['task_id']='task-http-error'
        with self.assertRaisesRegex(ResearchError,'unsupported parameter'):r.run(req)
        self.assertEqual(len(v.requests),1)
        files=list((self.root/'runtime/vendor-attempts').glob('*/response.json'));self.assertEqual(len(files),1)
        raw=files[0].read_text(encoding='utf-8');saved=json.loads(raw)
        self.assertNotIn(v.store.read_key(),raw);self.assertIn('[REDACTED]',raw)
        self.assertEqual(saved['http_status'],400);self.assertEqual(saved['phase'],'retrieval')
        self.assertEqual(saved['task_id'],'task-http-error');self.assertEqual(list(r.dir.glob('report-*/report.json')),[])
        attempts=(r.freeze_dir('task-http-error')/'attempts.jsonl').read_text(encoding='utf-8')
        self.assertIn('diagnostic_response_ref',attempts)


if __name__=='__main__':unittest.main()
