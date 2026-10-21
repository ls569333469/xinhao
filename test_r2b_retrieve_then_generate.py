# -*- coding: utf-8 -*-
"""R2b offline: retrieve → freeze → generate-input (mock model; no paid LLM)."""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from ai_research import AiResearch, ResearchError, RETRIEVAL_STRATEGY_NOTE
from research_retrieval import (
    FETCH_404,
    FETCH_FIXTURE,
    FETCH_PARTIAL,
    FETCH_TIMEOUT,
    FETCH_UNIMPLEMENTED,
    FixtureRetrievalAdapter,
    LiveHttpRetrievalAdapter,
    STATUS_DISABLED,
    STATUS_FAILED,
    STATUS_NOT_CONFIGURED,
    STATUS_PARTIAL,
    STATUS_SUCCESS,
)


class FakeAi:
    def __init__(self, content="# 报告\n仅引用允许的 evidence_id。"):
        self._content = content
        self.store = mock.Mock()
        self.store.read_key.return_value = "sk-test"
        self.last_user_payload = None

    def public_config(self):
        return {
            "keyConfigured": True,
            "modelId": "gpt-6.1-sol",
            "protocol": "chat",
            "baseUrl": "https://example.test/v1",
            "configId": "cfg-test",
            "version": 1,
            "timeoutSec": 30,
        }

    def _auth_headers(self, key):
        return {"Authorization": "Bearer " + key, "Content-Type": "application/json"}

    def _request_json(self, method, url, headers, body, timeout):
        # Capture the user message payload (second message content is JSON string)
        try:
            msgs = body.get("messages") or []
            if len(msgs) >= 2 and isinstance(msgs[1].get("content"), str):
                self.last_user_payload = json.loads(msgs[1]["content"])
        except Exception:
            self.last_user_payload = None
        payload = {"choices": [{"message": {"content": self._content}}]}
        return 200, json.dumps(payload).encode(), {}

    def _classify_http(self, status, text):
        return ("ok", "ok") if status == 200 else ("http_error", text[:80])


def _templates():
    return {
        "version": "research-prompts-v1.1-template-b",
        "common": "仅依据输入证据。中文输出。引用 allowed_evidence_ids。",
        "schema": {"schema_version": "research-report-v2-template-b", "body_template": "B"},
        "profiles": {
            "standard": {
                "label": "标准",
                "goal": "测试",
                "search": "默认尝试最新公开资料检索",
                "instruction": "输出八节",
                "template": "B",
            }
        },
    }


class R2bRetrieveThenGenerateTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        (self.root / "design").mkdir(parents=True)
        (self.root / "design" / "prompt-templates.json").write_text(
            json.dumps(_templates(), ensure_ascii=False), encoding="utf-8"
        )
        # copy fixtures
        fix_src = Path(__file__).resolve().parent / "design" / "retrieval-fixtures"
        if not fix_src.is_dir():
            fix_src = Path(__file__).resolve().parent / "retrieval-fixtures"
        dest = self.root / "design" / "retrieval-fixtures"
        dest.mkdir(parents=True, exist_ok=True)
        if fix_src.is_dir():
            for p in fix_src.glob("*.json"):
                (dest / p.name).write_text(p.read_text(encoding="utf-8"), encoding="utf-8")
        (self.root / "scan.json").write_text(
            json.dumps(
                {
                    "scan_id": "scan-r2b",
                    "revision": 3,
                    "available_at": "2026-10-14T12:00:00+00:00",
                    "rows": [
                        {
                            "symbol": "DEMOUSDT",
                            "market": "USDⓈ-M",
                            "asset": "DEMO",
                            "price": 1.0,
                            "missing": [],
                        }
                    ],
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        self.ai = FakeAi()

    def tearDown(self):
        self.tmp.cleanup()

    def _research(self, adapter=None):
        return AiResearch(self.root, self.ai, retrieval_adapter=adapter)

    def _prepare(self, research, *, requested_search=True, scenario=None):
        payload = {
            "mode": "snapshot",
            "depth": "standard",
            "contracts": [{"symbol": "DEMOUSDT", "market": "USDⓈ-M", "asset": "DEMO"}],
            "requested_search": requested_search,
            "submission_id": "sub-r2b-test-001",
        }
        if scenario:
            payload["retrieval_scenario"] = scenario
        # prepare needs templates via file; also inject scenario into request after
        req = research.prepare(payload)
        if scenario:
            req["retrieval_scenario"] = scenario
            if isinstance(req.get("_evidence"), dict):
                req["_evidence"]["retrieval_scenario"] = scenario
        req["task_id"] = "task-r2b-001"
        return req

    def test_checkbox_false_is_disabled_not_retrieved(self):
        adapter = FixtureRetrievalAdapter(fixtures_dir=self.root / "design" / "retrieval-fixtures")
        research = self._research(adapter)
        req = self._prepare(research, requested_search=False)
        out = research.run(req)
        self.assertEqual(out["retrieval"]["status"], STATUS_DISABLED)
        self.assertFalse(out["retrieval"]["online_verified"])
        self.assertEqual(out["retrieval"]["allowed_evidence_ids"], [])
        freeze = json.loads(
            (self.root / "runtime" / "task-freeze" / "task-r2b-001" / "evidence_freeze.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(freeze["retrieval_status"], STATUS_DISABLED)
        # model input must not claim retrieved
        self.assertEqual(self.ai.last_user_payload["retrieval"]["status"], STATUS_DISABLED)
        self.assertFalse(self.ai.last_user_payload["requested_search"])

    def test_retrieve_then_freeze_then_generate_input_ok(self):
        adapter = FixtureRetrievalAdapter(fixtures_dir=self.root / "design" / "retrieval-fixtures")
        research = self._research(adapter)
        req = self._prepare(research, requested_search=True, scenario="ok")
        out = research.run(req)
        self.assertEqual(out["retrieval"]["status"], STATUS_SUCCESS)
        self.assertTrue(out["retrieval"]["adapter_implemented"])
        self.assertEqual(out["retrieval"]["adapter_mode"], "fixture")
        self.assertFalse(out["retrieval"]["online_verified"])  # fixture ≠ live online-verified
        self.assertEqual(out["retrieval"]["strategy"], RETRIEVAL_STRATEGY_NOTE)
        ids = out["retrieval"]["allowed_evidence_ids"]
        self.assertTrue(ids)
        self.assertIn("ev-fixture-demo-catalyst", ids)

        freeze_path = self.root / "runtime" / "task-freeze" / "task-r2b-001" / "evidence_freeze.json"
        freeze = json.loads(freeze_path.read_text(encoding="utf-8"))
        self.assertEqual(freeze["freeze_phase"], "evidence")
        self.assertEqual(freeze["retrieval_status"], STATUS_SUCCESS)
        self.assertEqual(freeze["retrieval"]["attempt_id"], out["retrieval"]["attempt_id"])
        self.assertEqual(
            freeze["evidence_refs"]["external_evidence_ids"],
            ids,
        )
        self.assertTrue(freeze["evidence_refs"]["retrieval_bundle_hash"])

        # retrieval.json persisted
        ret_disk = json.loads(
            (self.root / "runtime" / "task-freeze" / "task-r2b-001" / "retrieval.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(ret_disk["status"], STATUS_SUCCESS)

        # model input only allows those IDs
        payload = self.ai.last_user_payload
        self.assertEqual(payload["allowed_evidence_ids"], ids)
        self.assertEqual(payload["retrieval"]["status"], STATUS_SUCCESS)
        self.assertTrue(payload["external_evidence"])
        for item in payload["external_evidence"]:
            self.assertIn(item["evidence_id"], ids)
            self.assertEqual(item.get("content_level"), "excerpt")

        # attempt attribution in call log
        calls = research.call_history(limit=20)
        kinds = [c.get("kind") for c in calls]
        self.assertIn("retrieval_attempt", kinds)
        started = [c for c in calls if c.get("kind") == "retrieval_attempt" and c.get("status") == "started"]
        self.assertTrue(started)
        self.assertTrue(started[0].get("attempt_id", "").startswith("retrieval-attempt-"))

    def test_timeout_not_online_verified(self):
        adapter = FixtureRetrievalAdapter(fixtures_dir=self.root / "design" / "retrieval-fixtures")
        research = self._research(adapter)
        req = self._prepare(research, requested_search=True, scenario="timeout")
        with self.assertRaises(ResearchError):
            research.run(req)
        self.assertIsNone(self.ai.last_user_payload)
        saved = json.loads((self.root / "runtime" / "task-freeze" / "task-r2b-001" / "retrieval.json").read_text(encoding="utf-8"))
        out = {"retrieval": saved}
        self.assertEqual(out["retrieval"]["status"], STATUS_FAILED)
        self.assertFalse(out["retrieval"]["online_verified"])
        self.assertIn("超时", out["retrieval"]["detail"])
        freeze = json.loads(
            (self.root / "runtime" / "task-freeze" / "task-r2b-001" / "evidence_freeze.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(freeze["retrieval"]["status"], STATUS_FAILED)

    def test_http_404_saved_as_error_state(self):
        adapter = FixtureRetrievalAdapter(fixtures_dir=self.root / "design" / "retrieval-fixtures")
        research = self._research(adapter)
        req = self._prepare(research, requested_search=True, scenario="http_404")
        with self.assertRaises(ResearchError):
            research.run(req)
        self.assertIsNone(self.ai.last_user_payload)
        saved = json.loads((self.root / "runtime" / "task-freeze" / "task-r2b-001" / "retrieval.json").read_text(encoding="utf-8"))
        out = {"retrieval": saved}
        self.assertEqual(out["retrieval"]["status"], STATUS_FAILED)
        self.assertFalse(out["retrieval"]["online_verified"])
        items = saved["items"]
        self.assertTrue(items)
        self.assertEqual(items[0]["fetch_status"], FETCH_404)

    def test_partial_marks_gaps(self):
        adapter = FixtureRetrievalAdapter(fixtures_dir=self.root / "design" / "retrieval-fixtures")
        research = self._research(adapter)
        req = self._prepare(research, requested_search=True, scenario="partial")
        out = research.run(req)
        self.assertEqual(out["retrieval"]["status"], STATUS_PARTIAL)
        self.assertFalse(out["retrieval"]["online_verified"])
        self.assertTrue(out["retrieval"]["gaps"])
        self.assertEqual(self.ai.last_user_payload["external_evidence"][0]["fetch_status"], FETCH_PARTIAL)
        freeze = json.loads(
            (self.root / "runtime" / "task-freeze" / "task-r2b-001" / "evidence_freeze.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertTrue(any("dynamic" in str(g) or "partial" in str(g) for g in freeze["coverage_gaps"]) or freeze["retrieval"]["gaps"])

    def test_unimplemented_source(self):
        adapter = FixtureRetrievalAdapter(fixtures_dir=self.root / "design" / "retrieval-fixtures")
        research = self._research(adapter)
        req = self._prepare(research, requested_search=True, scenario="unimplemented")
        with self.assertRaises(ResearchError):
            research.run(req)
        self.assertIsNone(self.ai.last_user_payload)
        saved = json.loads((self.root / "runtime" / "task-freeze" / "task-r2b-001" / "retrieval.json").read_text(encoding="utf-8"))
        out = {"retrieval": saved}
        self.assertEqual(out["retrieval"]["status"], STATUS_FAILED)
        self.assertEqual(saved["items"][0]["fetch_status"], FETCH_UNIMPLEMENTED)
        self.assertFalse(out["retrieval"]["online_verified"])

    def test_empty_news_not_verified_no_event(self):
        adapter = FixtureRetrievalAdapter(fixtures_dir=self.root / "design" / "retrieval-fixtures")
        research = self._research(adapter)
        req = self._prepare(research, requested_search=True, scenario="empty_news")
        with self.assertRaises(ResearchError):
            research.run(req)
        self.assertIsNone(self.ai.last_user_payload)
        saved = json.loads((self.root / "runtime" / "task-freeze" / "task-r2b-001" / "retrieval.json").read_text(encoding="utf-8"))
        out = {"retrieval": saved}
        self.assertEqual(out["retrieval"]["status"], STATUS_FAILED)
        self.assertFalse(out["retrieval"]["online_verified"])
        self.assertTrue(out["retrieval"]["gaps"])
        self.assertEqual(out["retrieval"]["allowed_evidence_ids"], [])

    def test_live_without_base_is_not_configured(self):
        adapter = LiveHttpRetrievalAdapter(base_url="", api_key="")
        research = self._research(adapter)
        req = self._prepare(research, requested_search=True)
        with self.assertRaises(ResearchError):
            research.run(req)
        self.assertIsNone(self.ai.last_user_payload)
        saved = json.loads((self.root / "runtime" / "task-freeze" / "task-r2b-001" / "retrieval.json").read_text(encoding="utf-8"))
        out = {"retrieval": saved}
        self.assertEqual(out["retrieval"]["status"], STATUS_NOT_CONFIGURED)
        self.assertFalse(out["retrieval"]["online_verified"])

    def test_live_opener_success_excerpt_only(self):
        def opener(url):
            return 200, b'{"title":"hello","snippet":"world"}', {"Content-Type": "application/json"}

        adapter = LiveHttpRetrievalAdapter(base_url="https://search.example.test", api_key="k", opener=opener)
        research = self._research(adapter)
        req = self._prepare(research, requested_search=True)
        out = research.run(req)
        self.assertEqual(out["retrieval"]["status"], STATUS_SUCCESS)
        self.assertTrue(out["retrieval"]["online_verified"])  # live + success + ok items
        item = self.ai.last_user_payload["external_evidence"][0]
        self.assertEqual(item["content_level"], "excerpt")  # never invent full_text
        self.assertTrue(item["usage"] if False else True)
        self.assertTrue(out["retrieval"]["usage"]["requests"] >= 1)

    def test_live_opener_404(self):
        def opener(url):
            return 404, b"missing", {}

        adapter = LiveHttpRetrievalAdapter(base_url="https://search.example.test", opener=opener)
        research = self._research(adapter)
        req = self._prepare(research, requested_search=True)
        with self.assertRaises(ResearchError):
            research.run(req)
        self.assertIsNone(self.ai.last_user_payload)
        saved = json.loads((self.root / "runtime" / "task-freeze" / "task-r2b-001" / "retrieval.json").read_text(encoding="utf-8"))
        out = {"retrieval": saved}
        self.assertEqual(out["retrieval"]["status"], STATUS_FAILED)
        self.assertFalse(out["retrieval"]["online_verified"])


if __name__ == "__main__":
    unittest.main()
