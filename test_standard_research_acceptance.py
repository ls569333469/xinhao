"""No network: native search proof, fee boundaries, quality/status and source-version gates."""
import json
from pathlib import Path
from unittest import mock
from ai_research import AiResearch, ResearchError
from research_web_search import ModelWebSearchAdapter
from config_store import ConfigStore, ConfigConflict
from strategy_store import StrategyStore
from task_service import TaskService
from test_r4_con_usage import Base, VendorStub


class NativeVendor(VendorStub):
    def __init__(self, mode="ok"):
        super().__init__()
        self.mode = mode
        self.requests = []

    def _request_json(self, method, url, headers, body, timeout):
        self.requests.append({"url": url, "body": body})
        if body.get("tools"):
            if self.mode == "timeout":
                raise TimeoutError("uncertain search result")
            if self.mode == "disconnect":
                raise ConnectionResetError("after send")
            if self.mode == "unsupported":
                return 400, b'{"error":"unsupported tool"}', {}
            output = [{"type": "message", "content": [{"type": "output_text", "text": "AAA 项目近期公告与风险摘要",
                "annotations": [{"type": "url_citation", "url": "https://official.example/a", "title": "公告"}]}]}]
            if self.mode != "no_tool":
                output.insert(0, {"type": "web_search_call", "id": "ws-1", "status": "completed",
                    "action": {"type": "search", "queries": ["AAA official news"], "sources": [
                        {"url": "https://official.example/a", "title": "公告"},
                        {"url": "https://official.example/b", "title": "治理"}]}})
            return 200, json.dumps({"output": output, "usage": {"input_tokens": 50, "output_tokens": 20, "total_tokens": 70}}).encode(), {}
        user = json.loads(body["messages"][1]["content"])
        syms = [r["symbol"] for r in user["contracts"]]
        text = self._content + "\n" + ",".join(syms) + "\n" + ",".join(user["allowed_evidence_ids"])
        if self.mode == 'no_citations':
            text = self._content + "\n" + ",".join(syms)
        return 200, json.dumps({"choices": [{"message": {"content": text}}],
            "usage": {"prompt_tokens": 80, "completion_tokens": 30, "total_tokens": 110}}).encode(), {}


class StandardAcceptanceTests(Base):
    def request(self, r, search=False):
        req = r.prepare({"contracts": [{"symbol": "AAAUSDT", "market": "USDⓈ-M"}], "depth": "standard", "requested_search": search})
        req["task_id"] = "task-acceptance"
        return req

    def test_native_preview_has_no_calls_and_exact_planned_count(self):
        v = NativeVendor();r = AiResearch(self.root, v)
        preview = r.preview({"contracts": [{"symbol": "AAAUSDT"}], "requested_search": True})
        self.assertEqual(v.requests, [])
        self.assertEqual(preview["planned_model_calls"], 2)
        self.assertEqual(preview["protocol"], "chat")
        self.assertEqual(preview["retrieval_capability"]["protocol"], "responses")
        self.assertEqual(preview["retrieval_capability"]["status"], "unverified")

    def test_native_search_frozen_sources_then_generation_same_model(self):
        v = NativeVendor();r = AiResearch(self.root, v)
        out = r.run(self.request(r, True));self.assertEqual(out["status"], "success")
        self.assertEqual(len(v.requests), 2)
        a,b = v.requests
        self.assertTrue(a["url"].endswith('/responses'))
        self.assertTrue(b["url"].endswith('/chat/completions'))
        self.assertEqual(a["body"]["model"], "gpt-6.1-sol")
        self.assertEqual(b["body"]["model"], "gpt-6.1-sol")
        self.assertEqual(a["body"]["include"], ["web_search_call.action.sources"])
        self.assertEqual(a["body"]["tools"], [{"type": "web_search"}])
        self.assertEqual(a["body"]["max_tool_calls"], 6)
        p = json.loads(b["body"]["messages"][1]["content"])
        self.assertEqual(len(set(p["allowed_evidence_ids"])), 2)
        self.assertTrue(all(i["content_level"] == "search_summary" for i in p["external_evidence"]))
        rep = r.get_report(out["report_id"])
        self.assertTrue(rep["retrieval"]["actual_search_performed"])
        self.assertFalse(rep["retrieval"]["online_verified"])
        self.assertEqual(rep["usage_total"]["total_tokens"], 180)
        self.assertTrue((self.root / rep["retrieval"]["raw_response_ref"]).exists())
        frozen = json.loads((self.root/'runtime/task-freeze/task-acceptance/evidence_freeze.json').read_text(encoding='utf-8'))
        self.assertEqual(frozen['evidence_refs']['external_evidence_ids'], p['allowed_evidence_ids'])

    def test_tool_absent_does_not_count_urls_as_search(self):
        v = NativeVendor('no_tool');r = AiResearch(self.root,v)
        with self.assertRaises(ResearchError):r.run(self.request(r,True))
        self.assertEqual(len(v.requests),1)
        self.assertEqual(list((self.root/'runtime/reports').glob('report-*/report.json')),[])
        raws = list((self.root/'runtime/retrieval-attempts').glob('*/vendor_response.json'))
        self.assertEqual(len(raws),1)

    def test_generated_report_must_reference_registered_search_evidence(self):
        v=NativeVendor('no_citations');r=AiResearch(self.root,v)
        out=r.run(self.request(r,True))
        self.assertEqual(out['status'],'failed')
        self.assertTrue(out['validation_failed'])
        self.assertEqual(len(v.requests),2)
        report=r.get_report(out['report_id'])
        self.assertFalse(report['checks']['automatic']['items']['external_evidence_cited']['ok'])

    def test_timeout_and_disconnect_not_auto_retried(self):
        for mode in ('timeout','disconnect'):
            v=NativeVendor(mode);r=AiResearch(self.root,v)
            with self.assertRaisesRegex(ResearchError,'未知/可能计费'):r.run(self.request(r,True))
            self.assertEqual(len(v.requests),1)
            self.assertEqual(r.inflight_status()['inflight'],0)

    def test_unsupported_native_tool_no_automatic_route_fallback(self):
        v=NativeVendor('unsupported');r=AiResearch(self.root,v)
        with self.assertRaises(ResearchError):r.run(self.request(r,True))
        self.assertEqual(len(v.requests),1)

    def test_plain_url_in_model_text_is_not_evidence(self):
        tools,sources=ModelWebSearchAdapter.extract_sources({'output':[{'type':'message','content':[{'text':'https://example.com'}]}]})
        self.assertEqual(tools,[]);self.assertEqual(sources,{})

    def test_rejected_output_saved_task_failed_no_strategy_excerpt_bypass(self):
        v=VendorStub();v._content='# 不完整 AAAUSDT'
        r=AiResearch(self.root,v);out=r.run(self.request(r))
        self.assertEqual(out['status'],'failed');self.assertTrue(out['validation_failed'])
        rep=r.get_report(out['report_id']);self.assertIn('不完整',rep['content'])
        self.assertEqual(rep['generation_status'],'completed')
        self.assertEqual(rep['quality']['status'],'rejected')
        self.assertTrue((r.dir/out['report_id']/'vendor_response.json').exists())
        store=StrategyStore(self.root)
        store.research=r
        with self.assertRaisesRegex(ValueError,'未通过校验'):
            store.create_draft_from_report({'report_id':out['report_id'],'excerpt':'client provided valid-looking idea'},research=r)
        with self.assertRaisesRegex(ValueError,'未通过校验'):
            store.create_draft({'title':'generic bypass','source_report_id':out['report_id'],'rules':{}})

    def test_task_result_retains_rejected_report_link(self):
        v=VendorStub();v._content='# incomplete AAAUSDT';r=AiResearch(self.root,v)
        svc=TaskService(self.root, lambda:None)
        svc.set_research(r)
        try:
            submitted=svc.submit_research({'contracts':[{'symbol':'AAAUSDT'}],'depth':'standard'})
            svc.tick();svc.worker.join(3)
            task=svc.task(submitted['task']['id'])
            self.assertEqual(task['status'],'failed')
            self.assertTrue(task['result']['report_id'])
            self.assertTrue(task['result']['validation_failed'])
        finally:svc.close()

    def test_batch_accept_reject_not_counted_success_or_retried(self):
        v=VendorStub();original=v._request_json
        def mixed(*a,**k):
            result=original(*a,**k)
            if len(v.calls)==2:return 200,json.dumps({'choices':[{'message':{'content':'# BBBUSDT broken'}}]}).encode(),{}
            return result
        v._request_json=mixed;r=AiResearch(self.root,v);r.batch_size=1
        req=self.prep(r,['AAAUSDT','BBBUSDT'],'sub-mixed','task-mixed')
        out=r.run(req)
        self.assertEqual(out['status'],'partial_success')
        self.assertEqual(out['batch_status']['success'],1)
        self.assertEqual(out['batch_status']['validation_failed_symbols'],['BBBUSDT'])
        self.assertEqual(out['batch_status']['retryable_symbols'],[])
        before={p:p.read_bytes() for p in r.dir.glob('report-*/*') if p.is_file()}
        retried=r.retry_batch_failed(out['batch_id'])
        self.assertEqual(retried['retried_groups'],0)
        self.assertEqual(len(v.calls),2)
        self.assertTrue(all(p.read_bytes()==data for p,data in before.items()))

    def test_legacy_tasks_rejected_reports_readable(self):
        v=VendorStub();r=AiResearch(self.root,v)
        for depth in ('quick','deep'):
            with self.assertRaises(ResearchError):r.prepare({'contracts':[{'symbol':'AAAUSDT'}],'depth':depth})
        req=self.request(r);req['depth']='quick'
        with self.assertRaises(ResearchError):r.run(req)
        self.assertEqual(v.calls,[])
        rid='report-'+'a'*32;d=r.dir/rid;d.mkdir()
        (d/'report.json').write_text(json.dumps({'report_id':rid,'source':'ai','depth':'quick','content':'旧报告','contracts':[]}),encoding='utf-8')
        self.assertEqual(r.get_report(rid)['depth'],'quick')
        self.assertEqual(r.get_report(rid)['content'],'旧报告')

    def test_historical_recheck_does_not_rewrite_original(self):
        v=VendorStub();r=AiResearch(self.root,v);out=r.run(self.request(r))
        p=r.dir/out['report_id']/'report.json'
        data=json.loads(p.read_text(encoding='utf-8'))
        data['content']='# AAAUSDT\n当前价格：$1'
        p.write_text(json.dumps(data,ensure_ascii=False),encoding='utf-8');before=p.read_bytes()
        rep=r.get_report(out['report_id'])
        self.assertEqual(rep['checks']['automatic']['status'],'passed')
        self.assertEqual(rep['current_rule_review']['quality']['status'],'rejected')
        self.assertEqual(p.read_bytes(),before)

    def test_revision_identity_not_silently_mixed_with_original(self):
        r=AiResearch(self.root,VendorStub());out=r.run(self.request(r));rep=r.get_report(out['report_id'])
        r.assert_strategy_eligible(rep,0)
        store=StrategyStore(self.root);store.research=r
        draft=store.create_draft_from_report({'report_id':out['report_id'],'excerpt':'editable idea'},research=r)
        self.assertEqual(draft['rules']['source_report_version'],0)
        with self.assertRaisesRegex(ValueError,'修订版'):r.assert_strategy_eligible(rep,1)

    def test_persisted_custom_prompt_used_legacy_customs_preserved_conflict_rejected(self):
        store=ConfigStore(self.root);state=store.get();r=AiResearch(self.root,VendorStub())
        first=store.set_prompt_edits({'overrides':{'standard':'CUSTOM B','quick':'OLD QUICK','deep':'OLD DEEP'},
            'expected_content_hash':state['meta']['content_hash']})
        self.assertEqual(self.request(r)['_evidence']['instruction'],'CUSTOM B')
        with self.assertRaises(ConfigConflict):store.set_prompt_edits({'overrides':{'standard':'BAD'},'expected_content_hash':state['meta']['content_hash']})
        store.set_prompt_edits({'overrides':{'standard':'CUSTOM B2'},'expected_content_hash':first['meta']['content_hash']})
        self.assertEqual(store.get_prompt_edits()['overrides']['quick'],'OLD QUICK')
        self.assertEqual(store.get_prompt_edits()['overrides']['deep'],'OLD DEEP')

    def test_no_key_preview_fails_before_any_call(self):
        v=NativeVendor();v._key=False
        v.public_config=lambda: {'keyConfigured':False,'modelId':'gpt-6.1-sol'}
        r=AiResearch(self.root,v)
        with self.assertRaises(ResearchError):r.preview({'contracts':[{'symbol':'AAAUSDT'}],'requested_search':True})
        self.assertEqual(v.requests,[])
