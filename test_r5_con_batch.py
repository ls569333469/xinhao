# -*- coding: utf-8 -*-
"""R5 offline: CON-04 (429/timeout), CON-08+REV-10 batch partial retry, live retrieval wiring default. No paid calls."""
from __future__ import annotations

import json
import os
import time
import unittest
from unittest import mock

from ai_research import AiResearch, ResearchError
from research_retrieval import resolve_adapter, FixtureRetrievalAdapter, LiveHttpRetrievalAdapter
from task_service import TaskService
from test_r4_con_usage import Base, VendorStub, SYMS


class SeqVendor(VendorStub):
    """Returns scripted outcomes in order: 'ok' | 429 | ('429', retry_after) | 'timeout' | 500."""

    def __init__(self, script, **kw):
        super().__init__(**kw)
        self.script = list(script)

    def _request_json(self, method, url, headers, body, timeout):
        step = self.script.pop(0) if self.script else "ok"
        user = json.loads(body["messages"][1]["content"])
        self.calls.append([r.get("symbol") for r in user.get("contracts") or []])
        if step == "timeout":
            raise TimeoutError("The read operation timed out")
        if isinstance(step, tuple):
            return 429, b'{"error":"rate"}', {"Retry-After": str(step[1])}
        if step == 429:
            return 429, b'{"error":"rate"}', {}
        if step == 500:
            return 500, b'{"error":"x"}', {}
        payload = {"choices": [{"message": {"content": self._content + "\n" + ",".join(self.calls[-1])}}]}
        return 200, json.dumps(payload).encode(), {}


class Con04Tests(Base):
    def mk(self, script):
        r = AiResearch(self.root, SeqVendor(script))
        self.slept = []
        r._sleep = self.slept.append
        return r

    def log(self):
        txt = "".join(p.read_text(encoding="utf-8") for p in (self.root / "runtime" / "ai-calls").glob("*.jsonl"))
        return [json.loads(l) for l in txt.splitlines() if l.strip()]

    def test_429_backoff_then_success(self):
        r = self.mk([429, ("429", 7), "ok"])
        out = r.run(self.prep(r, ["AAAUSDT"], "sub-429", "task-429"))
        self.assertEqual(out["status"], "success")
        self.assertEqual(self.slept, [2.0, 7.0])  # exponential base, then Retry-After honored
        kinds = [(e.get("kind"), e.get("status")) for e in self.log()]
        self.assertEqual(kinds.count(("vendor_attempt", "rate_limited")), 2)
        self.assertEqual(sum(1 for e in self.log() if e.get("kind") == "vendor_backoff"), 2)
        att = (self.root / "runtime" / "task-freeze" / "task-429" / "attempts.jsonl").read_text(encoding="utf-8")
        self.assertEqual(att.count("rate_limited"), 2)
        self.assertEqual(r.inflight_status()["inflight"], 0)  # slot released while waiting

    def test_429_exhausted_fails_clear(self):
        r = self.mk([429, 429, 429, "ok"])
        with self.assertRaises(ResearchError) as cm:
            r.run(self.prep(r, ["AAAUSDT"], "sub-429x", "task-429x"))
        self.assertIn("429", str(cm.exception))
        self.assertEqual(len(r.ai.calls), 3)
        self.assertNotIn("result_unknown", json.dumps(self.log()))

    def test_timeout_never_resent(self):
        r = self.mk(["timeout", "ok"])
        with self.assertRaises(ResearchError) as cm:
            r.run(self.prep(r, ["AAAUSDT"], "sub-to", "task-to"))
        self.assertIn("未知", str(cm.exception))
        self.assertEqual(len(r.ai.calls), 1)
        self.assertEqual(self.slept, [])
        self.assertIn("result_unknown_may_be_billed", json.dumps(self.log()))

    def test_429_in_a_does_not_pollute_b(self):
        r = self.mk([429, "ok", "ok"])
        oa = r.run(self.prep(r, ["AAAUSDT"], "sub-a4", "task-a4"))
        ob = r.run(self.prep(r, ["BBBUSDT"], "sub-b4", "task-b4"))
        fb = (self.root / "runtime" / "task-freeze" / "task-b4" / "attempts.jsonl").read_text(encoding="utf-8")
        self.assertNotIn("rate_limited", fb)
        self.assertNotEqual(oa["report_id"], ob["report_id"])


class Con08BatchTests(Base):
    def setUp(self):
        super().setUp()

    def mk(self, fail=(), size=2):
        stub = VendorStub(fail_syms=set(fail))
        r = AiResearch(self.root, stub)
        r.batch_size = size
        return r, stub

    def test_partial_then_retry_only_failed(self):
        r, stub = self.mk(fail={"CCCUSDT"})
        out = r.run(self.prep(r, list(SYMS), "sub-batch1", "task-batch1"))
        self.assertEqual(out["status"], "partial_success")
        self.assertEqual(out["batch_status"]["success"], 1)
        self.assertEqual(out["batch_status"]["failed_symbols"], ["CCCUSDT", "DDDUSDT"])
        self.assertEqual(stub.calls, [["AAAUSDT", "BBBUSDT"], ["CCCUSDT", "DDDUSDT"]])
        first_ok = out["report_ids"][0]
        before = (self.root / "runtime" / "reports" / first_ok / "report.json").read_bytes()
        stub.fail_syms.clear()
        again = r.retry_batch_failed(out["batch_id"])
        self.assertEqual(again["status"], "success")
        self.assertEqual(again["retried_groups"], 1)
        self.assertEqual(stub.calls[2:], [["CCCUSDT", "DDDUSDT"]])  # success group never re-run
        self.assertEqual(before, (self.root / "runtime" / "reports" / first_ok / "report.json").read_bytes())
        b = r.get_batch(out["batch_id"])
        self.assertEqual(b["submission_id"], "sub-batch1")  # same confirm
        self.assertEqual(b["retry_rounds"], 1)
        self.assertEqual(b["target_symbols"], list(SYMS))
        rep = self.report(again["report_ids"][1])
        self.assertEqual([c["symbol"] for c in rep["contracts"]], ["CCCUSDT", "DDDUSDT"])
        self.assertEqual(rep["submission_id"], "sub-batch1")
        # nothing left to retry → no vendor call
        n = len(stub.calls)
        self.assertEqual(r.retry_batch_failed(out["batch_id"])["retried_groups"], 0)
        self.assertEqual(len(stub.calls), n)

    def test_group_evidence_is_only_its_coins(self):
        r, stub = self.mk()
        out = r.run(self.prep(r, list(SYMS), "sub-batch2", "task-batch2"))
        pi = json.loads((self.root / "runtime" / "reports" / out["report_ids"][1] / "prompt_input.json").read_text(encoding="utf-8"))
        self.assertEqual([c["symbol"] for c in pi["contracts"]], ["CCCUSDT", "DDDUSDT"])

    def test_unknown_group_not_blind_retried(self):
        r = AiResearch(self.root, SeqVendor(["ok", "timeout", "ok"]))
        r.batch_size = 2
        out = r.run(self.prep(r, list(SYMS), "sub-batch3", "task-batch3"))
        self.assertEqual(out["status"], "partial_success")
        b = r.get_batch(out["batch_id"])
        self.assertEqual(b["groups"][1]["status"], "result_unknown_may_be_billed")
        again = r.retry_batch_failed(out["batch_id"])
        self.assertEqual(again["retried_groups"], 0)
        self.assertEqual(len(r.ai.calls), 2)
        again = r.retry_batch_failed(out["batch_id"], include_unknown=True)  # explicit user choice
        self.assertEqual(again["status"], "success")
        self.assertEqual(len(r.ai.calls), 3)

    def test_all_failed_returns_failed_with_batch(self):
        r, _ = self.mk(fail=set(SYMS))
        out = r.run(self.prep(r, list(SYMS), "sub-batch4", "task-batch4"))
        self.assertEqual(out["status"], "failed")
        self.assertIsNone(out["report_id"])
        self.assertTrue(out["batch_id"].startswith("batch-"))

    def test_small_selection_is_single_call_unchanged(self):
        r, stub = self.mk(size=5)
        out = r.run(self.prep(r, ["AAAUSDT", "BBBUSDT"], "sub-small", "task-small"))
        self.assertNotIn("batch_id", out)
        self.assertEqual(len(stub.calls), 1)

    def test_task_service_batch_retry_task(self):
        stub = VendorStub(fail_syms={"DDDUSDT"})
        r = AiResearch(self.root, stub)
        r.batch_size = 2
        svc = TaskService(self.root, lambda: {})
        try:
            svc.set_research(r)
            t = svc.submit_research({"mode": "snapshot", "depth": "standard", "submission_id": "sub-ts1",
                                     "requested_search": False,
                                     "contracts": [{"symbol": s, "market": "USDⓈ-M", "asset": s[:-4]} for s in SYMS]})["task"]
            svc.tick(); svc.worker.join(10)
            done = svc.task(t["id"])
            self.assertEqual(done["status"], "partial_success")
            bid = done["result"]["batch_id"]
            stub.fail_syms.clear()
            q = svc.submit_batch_retry(bid)
            self.assertTrue(svc.submit_batch_retry(bid)["reused"])  # no duplicate retry task
            svc.tick(); svc.worker.join(10)
            rt = svc.task(q["task"]["id"])
            self.assertEqual(rt["status"], "success")
            self.assertEqual(rt["result"]["retried_groups"], 1)
            self.assertEqual(stub.calls, [["AAAUSDT", "BBBUSDT"], ["CCCUSDT", "DDDUSDT"], ["CCCUSDT", "DDDUSDT"]])
            with self.assertRaises(KeyError):
                svc.submit_batch_retry("batch-" + "0" * 32)
        finally:
            svc.close()

    def test_empty_contracts_rejected_no_call(self):
        r, stub = self.mk()
        with self.assertRaises(ResearchError):
            r.run({"contracts": [], "selected_count": 0})
        self.assertEqual(stub.calls, [])


class LiveRetrievalWiringTests(unittest.TestCase):
    def test_default_does_not_enable_fixture(self):
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("RESEARCH_RETRIEVAL_MODE", None)
            self.assertIsInstance(resolve_adapter(), LiveHttpRetrievalAdapter)

    def test_live_without_base_is_not_configured_and_not_verified(self):
        with mock.patch.dict(os.environ, {"RESEARCH_RETRIEVAL_MODE": "live", "RESEARCH_RETRIEVAL_BASE_URL": "",
                                          "RESEARCH_RETRIEVAL_API_KEY": ""}):
            a = resolve_adapter()
            self.assertIsInstance(a, LiveHttpRetrievalAdapter)


if __name__ == "__main__":
    unittest.main()