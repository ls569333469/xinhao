# -*- coding: utf-8 -*-
"""R4 offline: RES-17 usage parsing + v1.2 CON stubs. No paid AI calls (fake vendor)."""
from __future__ import annotations

import json
import tempfile
import threading
import time
import unittest
from pathlib import Path

from ai_research import AiResearch, ResearchError, parse_vendor_usage
from task_service import TaskService
from test_r2b_retrieve_then_generate import FakeAi, _templates

SYMS = ("AAAUSDT", "BBBUSDT", "CCCUSDT", "DDDUSDT")


class VendorStub(FakeAi):
    """Fake vendor: per-symbol delay, usage, optional failure; tracks concurrency."""

    def __init__(self, delays=None, fail_syms=(), usage=True):
        super().__init__(content="# 报告\n" + "\n".join("## " + s for s in
                          ("核心催化剂", "价格与市场", "风险与机会", "技术面", "合约结构", "基本面", "支撑压力", "综合判断")))
        self.delays = delays or {}
        self.fail_syms = set(fail_syms)
        self.usage = usage
        self.lock = threading.Lock()
        self.inflight = 0
        self.peak = 0
        self.calls = []

    def _request_json(self, method, url, headers, body, timeout):
        user = json.loads(body["messages"][1]["content"])
        syms = [r.get("symbol") for r in user.get("contracts") or []]
        with self.lock:
            self.inflight += 1
            self.peak = max(self.peak, self.inflight)
            self.calls.append(syms)
        try:
            time.sleep(max([self.delays.get(s, 0.05) for s in syms] or [0.05]))
        finally:
            with self.lock:
                self.inflight -= 1
        if any(s in self.fail_syms for s in syms):
            return 500, b'{"error":"stub"}', {}
        payload = {"choices": [{"message": {"content": self._content + "\n" + ",".join(syms)}}]}
        if self.usage:
            payload["usage"] = {"prompt_tokens": 100 * len(syms), "completion_tokens": 50,
                                "total_tokens": 100 * len(syms) + 50,
                                "completion_tokens_details": {"reasoning_tokens": 20}}
        return 200, json.dumps(payload).encode(), {}


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        (self.root / "design").mkdir(parents=True)
        (self.root / "design" / "prompt-templates.json").write_text(json.dumps(_templates(), ensure_ascii=False), encoding="utf-8")
        self.write_scan(1)

    def tearDown(self):
        self.tmp.cleanup()

    def write_scan(self, revision):
        rows = [{"symbol": s, "market": "USDⓈ-M", "asset": s[:-4], "price": 1.0 + revision, "missing": []} for s in SYMS]
        (self.root / "scan.json").write_text(json.dumps({"scan_id": "scan-r4-%d" % revision, "revision": revision,
                                                         "available_at": "2026-10-14T12:00:00+00:00", "rows": rows},
                                                        ensure_ascii=False), encoding="utf-8")

    def prep(self, research, syms, sid, tid):
        req = research.prepare({"mode": "snapshot", "depth": "standard", "submission_id": sid, "requested_search": False,
                                "contracts": [{"symbol": s, "market": "USDⓈ-M", "asset": s[:-4]} for s in syms]})
        req["task_id"] = tid
        return req

    def report(self, rid):
        return json.loads((self.root / "runtime" / "reports" / rid / "report.json").read_text(encoding="utf-8"))


class Res17UsageTests(unittest.TestCase):
    def test_chat_usage_reasoning_not_added(self):
        u = parse_vendor_usage({"usage": {"prompt_tokens": 1000, "completion_tokens": 300, "total_tokens": 1300,
                                          "completion_tokens_details": {"reasoning_tokens": 120}}})
        self.assertEqual((u["status"], u["input_tokens"], u["output_tokens"], u["total_tokens"], u["reasoning_tokens"]),
                         ("known", 1000, 300, 1300, 120))
        self.assertTrue(u["reasoning_included_in_output"])

    def test_responses_usage_total_computed_without_reasoning(self):
        u = parse_vendor_usage({"usage": {"input_tokens": 10, "output_tokens": 7, "output_tokens_details": {"reasoning_tokens": 5}}})
        self.assertEqual(u["total_tokens"], 17)
        self.assertEqual(u["status"], "known")

    def test_gemini_usage_metadata(self):
        u = parse_vendor_usage({"usageMetadata": {"promptTokenCount": 8, "candidatesTokenCount": 4, "thoughtsTokenCount": 3, "totalTokenCount": 15}})
        self.assertEqual((u["input_tokens"], u["output_tokens"], u["reasoning_tokens"], u["total_tokens"]), (8, 4, 3, 15))
        self.assertFalse(u["reasoning_included_in_output"])

    def test_missing_usage_stays_unknown_not_zero(self):
        for p in ({"choices": []}, None, "x", {"usage": None}, {"usage": {}}):
            u = parse_vendor_usage(p)
            self.assertEqual(u["status"], "unknown")
            self.assertIsNone(u["input_tokens"])
            self.assertIsNone(u["total_tokens"])

    def test_partial_and_bad_values(self):
        u = parse_vendor_usage({"usage": {"prompt_tokens": 5, "completion_tokens": "7", "total_tokens": -1}})
        self.assertEqual(u["status"], "partial")
        self.assertEqual(u["input_tokens"], 5)
        self.assertIsNone(u["output_tokens"])
        self.assertIsNone(u["total_tokens"])


class RunUsageTests(Base):
    def test_report_and_log_carry_structured_usage_and_raw(self):
        r = AiResearch(self.root, VendorStub())
        out = r.run(self.prep(r, ["AAAUSDT"], "sub-u1", "task-u1"))
        rep = self.report(out["report_id"])
        self.assertEqual(rep["usage"]["status"], "known")
        self.assertEqual(rep["usage"]["total_tokens"], 150)
        self.assertEqual(rep["usage"]["reasoning_tokens"], 20)
        self.assertTrue(rep["vendor_attempt_id"].startswith("attempt-"))
        raw = json.loads((self.root / "runtime" / "reports" / out["report_id"] / "vendor_response.json").read_text(encoding="utf-8"))
        self.assertEqual(raw["payload"]["usage"]["total_tokens"], 150)
        self.assertNotIn("sk-test", json.dumps(raw))

    def test_no_usage_from_vendor_is_unknown(self):
        r = AiResearch(self.root, VendorStub(usage=False))
        out = r.run(self.prep(r, ["AAAUSDT"], "sub-u2", "task-u2"))
        self.assertEqual(self.report(out["report_id"])["usage"]["status"], "unknown")
        self.assertIsNone(self.report(out["report_id"])["usage"]["total_tokens"])


class ConTests(Base):
    def test_con01_isolation_b_finishes_first(self):
        stub = VendorStub(delays={"AAAUSDT": 0.4, "BBBUSDT": 0.05})
        r = AiResearch(self.root, stub)
        ra = self.prep(r, ["AAAUSDT"], "sub-a", "task-a")
        rb = self.prep(r, ["BBBUSDT"], "sub-b", "task-b")
        res = {}
        ta = threading.Thread(target=lambda: res.__setitem__("a", r.run(ra)))
        tb = threading.Thread(target=lambda: res.__setitem__("b", r.run(rb)))
        ta.start(); time.sleep(0.02); tb.start(); ta.join(5); tb.join(5)
        a, b = self.report(res["a"]["report_id"]), self.report(res["b"]["report_id"])
        self.assertLess(b["created_at"], a["created_at"])
        self.assertIn("AAAUSDT", a["content"]); self.assertNotIn("BBBUSDT", a["content"])
        self.assertIn("BBBUSDT", b["content"]); self.assertNotIn("AAAUSDT", b["content"])
        self.assertEqual(a["submission_id"], "sub-a"); self.assertEqual(b["submission_id"], "sub-b")
        self.assertNotEqual(a["vendor_attempt_id"], b["vendor_attempt_id"])
        fa = (self.root / "runtime" / "task-freeze" / "task-a" / "attempts.jsonl").read_text(encoding="utf-8")
        self.assertNotIn("sub-b", fa)

    def test_con02_same_coin_two_confirms_two_reports(self):
        r = AiResearch(self.root, VendorStub())
        o1 = r.run(self.prep(r, ["AAAUSDT"], "sub-s1", "task-s1"))
        o2 = r.run(self.prep(r, ["AAAUSDT"], "sub-s2", "task-s2"))
        self.assertNotEqual(o1["report_id"], o2["report_id"])
        self.assertFalse(o2["cache_hit"])
        self.assertEqual(self.report(o1["report_id"])["submission_id"], "sub-s1")

    def test_con03_global_inflight_cap_default_2(self):
        stub = VendorStub(delays={s: 0.25 for s in SYMS})
        r = AiResearch(self.root, stub)
        self.assertEqual(r.max_inflight, 2)
        reqs = [self.prep(r, [s], "sub-c%d" % i, "task-c%d" % i) for i, s in enumerate(SYMS)]
        outs = []
        ts = [threading.Thread(target=lambda q=q: outs.append(r.run(q))) for q in reqs]
        [t.start() for t in ts]; [t.join(10) for t in ts]
        self.assertEqual(len(outs), 4)
        self.assertLessEqual(stub.peak, 2)
        self.assertLessEqual(r.inflight_status()["peak"], 2)
        self.assertEqual(r.inflight_status()["inflight"], 0)

    def test_con05_snapshot_binding_immutable(self):
        r = AiResearch(self.root, VendorStub())
        req = self.prep(r, ["AAAUSDT"], "sub-snap", "task-snap")
        self.write_scan(2)  # global scan republished meanwhile
        out = r.run(req)
        rep = self.report(out["report_id"])
        self.assertEqual(rep["request"]["revision"], 1)
        pi = json.loads((self.root / "runtime" / "reports" / out["report_id"] / "prompt_input.json").read_text(encoding="utf-8"))
        self.assertEqual(pi["snapshot"]["revision"], 1)
        self.assertEqual(pi["contracts"][0]["price"], 2.0)  # revision-1 price, not 3.0

    def test_con06_backend_a_unchanged_after_b(self):
        r = AiResearch(self.root, VendorStub())
        oa = r.run(self.prep(r, ["AAAUSDT"], "sub-r1", "task-r1"))
        before = (self.root / "runtime" / "reports" / oa["report_id"] / "report.json").read_bytes()
        r.run(self.prep(r, ["BBBUSDT"], "sub-r2", "task-r2"))
        self.assertEqual(before, (self.root / "runtime" / "reports" / oa["report_id"] / "report.json").read_bytes())
        self.assertEqual(r.get_report(oa["report_id"])["submission_id"], "sub-r1")

    def test_con07_two_workers_one_claim(self):
        runs = []
        class CountingResearch(AiResearch):
            def run(self, request):
                runs.append(request.get("task_id"))
                time.sleep(0.2)
                return {"status": "success", "report_id": "report-x", "contract_count": 1}
        s1 = TaskService(self.root, lambda: {})
        s2 = TaskService(self.root, lambda: {})
        try:
            res = CountingResearch(self.root, VendorStub())
            s1.set_research(res); s2.set_research(res)
            task = s1.submit_research({"mode": "snapshot", "depth": "standard", "submission_id": "sub-w1",
                                       "contracts": [{"symbol": "AAAUSDT", "market": "USDⓈ-M", "asset": "AAA"}]})["task"]
            barrier = threading.Barrier(2)
            def go(s):
                barrier.wait(); s.tick()
            ts = [threading.Thread(target=go, args=(s,)) for s in (s1, s2)]
            [t.start() for t in ts]; [t.join(5) for t in ts]
            for s in (s1, s2):
                if s.worker: s.worker.join(5)
            self.assertEqual(runs, [task["id"]])
            self.assertEqual(s1.task(task["id"])["status"], "success")
        finally:
            s1.close(); s2.close()

    def test_con08_failed_coin_does_not_pollute_success_and_retry_is_only_failed(self):
        stub = VendorStub(fail_syms={"BBBUSDT"})
        r = AiResearch(self.root, stub)
        ok = r.run(self.prep(r, ["AAAUSDT"], "sub-p1", "task-p1"))
        with self.assertRaises(ResearchError):
            r.run(self.prep(r, ["BBBUSDT"], "sub-p2", "task-p2"))
        stub.fail_syms.clear()
        retry = r.run(self.prep(r, ["BBBUSDT"], "sub-p3", "task-p3"))
        self.assertEqual(stub.calls, [["AAAUSDT"], ["BBBUSDT"], ["BBBUSDT"]])  # success coin not re-billed
        self.assertEqual(self.report(ok["report_id"])["usage"]["total_tokens"], 150)
        self.assertEqual(self.report(retry["report_id"])["usage"]["total_tokens"], 150)

    def test_con09_shared_batch_usage_once(self):
        r = AiResearch(self.root, VendorStub())
        out = r.run(self.prep(r, ["AAAUSDT", "BBBUSDT", "CCCUSDT"], "sub-batch", "task-batch"))
        rep = self.report(out["report_id"])
        self.assertEqual(rep["contract_count"], 3)
        self.assertEqual(rep["usage"]["total_tokens"], 350)  # one vendor call, counted once
        for c in rep["contracts"]:
            self.assertNotIn("usage", c)  # no fabricated per-coin split
        logs = "".join(p.read_text(encoding="utf-8") for p in (self.root / "runtime" / "ai-calls").glob("*.jsonl"))
        research_lines = [json.loads(l) for l in logs.splitlines() if '"kind": "research"' in l]
        self.assertEqual(len(research_lines), 1)


if __name__ == "__main__":
    unittest.main()