# -*- coding: utf-8 -*-
"""AI-03 unit tests: prepare validation, report history, no live billing."""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import ai_research
from ai_research import AiResearch, ResearchError


class FakeAi:
    def __init__(self, key=True, model="gpt-6.1-sol"):
        self._key = key
        self.model = model
        self.store = mock.Mock()
        self.store.read_key.return_value = "sk-test"

    def public_config(self):
        return {
            "keyConfigured": self._key,
            "modelId": self.model if self._key else "",
            "protocol": "chat",
            "baseUrl": "https://example.test/v1",
            "configId": "cfg-test",
            "version": 1,
            "timeoutSec": 30,
        }

    def _auth_headers(self, key):
        return {"Authorization": "Bearer " + key, "Content-Type": "application/json"}

    def _request_json(self, method, url, headers, body, timeout):
        payload = {
            "choices": [{"message": {"content": json.dumps({"metadata": {}, "contracts": [{"symbol": "BTCUSDT"}], "basket": {}, "sources": [], "note": "报告"}, ensure_ascii=False)}}]
        }
        return 200, json.dumps(payload).encode(), {}

    def _classify_http(self, status, text):
        return ("ok", "ok") if status == 200 else ("http_error", text[:80])


class AiResearchTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        (self.root / "design").mkdir()
        (self.root / "design" / "prompt-templates.json").write_text(json.dumps({
            "version": "research-prompts-v1.0",
            "common": "只用证据。",
            "schema": {"type": "object"},
            "profiles": {
                "quick": {"label": "快", "instruction": "短", "goal": "g", "search": {}},
                "standard": {"label": "标准", "instruction": "中", "goal": "g", "search": {}},
                "deep": {"label": "深", "instruction": "长", "goal": "g", "search": {}},
            }
        }, ensure_ascii=False), encoding="utf-8")
        (self.root / "scan.json").write_text(json.dumps({
            "scan_id": "scan-1",
            "revision": 1,
            "available_at": "2026-10-13T00:00:00+00:00",
            "rows": [{
                "symbol": "BTCUSDT", "market": "USDⓈ-M", "asset": "BTC", "price": 1,
                "fundingPct": -0.01, "fundingAprPct": -10.0,
                "priceChangeWindowPct": 2.5, "ema4h": [1.0, 1.1, 1.2],
                "fieldAvailableAt": {"price": "2026-10-13T00:00:00+00:00", "fundingPct": "2026-10-13T00:00:00+00:00"},
                "asOf": "2026-10-13T00:00:00+00:00",
            }],
        }, ensure_ascii=False), encoding="utf-8")
        self.ai = FakeAi()
        self.research = AiResearch(self.root, self.ai)

    def tearDown(self):
        self.tmp.cleanup()

    def test_prepare_requires_contracts_and_key(self):
        with self.assertRaises(ResearchError):
            self.research.prepare({})
        with self.assertRaises(ResearchError):
            self.research.prepare({"contracts": []})
        bad = FakeAi(key=False)
        r = AiResearch(self.root, bad)
        with self.assertRaises(ResearchError):
            r.prepare({"contracts": [{"symbol": "BTCUSDT"}]})

    def test_prepare_and_run(self):
        spec = self.research.prepare({"contracts": [{"symbol": "BTCUSDT", "market": "USDⓈ-M"}], "depth": "standard"})
        self.assertEqual(spec["selected_count"], 1)
        self.assertIn("request_hash", spec)
        self.assertIn("_evidence", spec)
        result = self.research.run(spec)
        self.assertEqual(result["status"], "success")
        report = self.research.get_report(result["report_id"])
        self.assertIn("报告", report["content"])
        hist = self.research.history()
        self.assertEqual(hist[0]["report_id"], result["report_id"])



    def test_preview_and_cache(self):
        body = {"contracts": [{"symbol": "BTCUSDT", "market": "USDⓈ-M"}], "depth": "standard"}
        prev = self.research.preview(body)
        self.assertEqual(prev["billing"], "preview_only_no_model_call")
        self.assertFalse(prev["cache_hit"])
        self.assertIn("submission_id", prev)
        self.assertIn("intent_hash", prev)
        spec = self.research.prepare(body)
        first = self.research.run(spec)
        self.assertFalse(first.get("cache_hit"))
        # R1: independent second prepare/run must NOT reuse old AI body via content hash
        spec2 = self.research.prepare(body)
        second = self.research.run(spec2)
        self.assertFalse(second.get("cache_hit"))
        self.assertNotEqual(second["report_id"], first["report_id"])
        calls = self.research.call_history()
        self.assertTrue(any(c.get("kind") == "research_cache_ignored" for c in calls))
        # Explicit opt-in still allowed for legacy/debug paths
        spec3 = self.research.prepare(body)
        spec3["reuse_completed_report"] = True
        third = self.research.run(spec3)
        self.assertTrue(third.get("cache_hit"))

    def test_r1_intent_hash_stable_under_contract_reorder(self):
        a = {"contracts": [{"symbol": "BTCUSDT", "market": "USDⓈ-M"}, {"symbol": "ETHUSDT", "market": "USDⓈ-M"}], "depth": "standard"}
        b = {"contracts": [{"symbol": "ETHUSDT", "market": "USDⓈ-M"}, {"symbol": "BTCUSDT", "market": "USDⓈ-M"}], "depth": "standard"}
        sa = self.research.prepare(a)
        sb = self.research.prepare(b)
        self.assertEqual(sa["intent_hash"], sb["intent_hash"])

    def test_r1_prompt_change_invalidates_intent(self):
        body = {"contracts": [{"symbol": "BTCUSDT", "market": "USDⓈ-M"}], "depth": "standard", "user_focus": "A"}
        s1 = self.research.prepare(body)
        s2 = self.research.prepare({**body, "user_focus": "B"})
        self.assertNotEqual(s1["intent_hash"], s2["intent_hash"])

    def test_r1_unique_report_paths_same_symbol(self):
        body = {"contracts": [{"symbol": "BTCUSDT", "market": "USDⓈ-M"}], "depth": "standard"}
        r1 = self.research.run(self.research.prepare(body))
        r2 = self.research.run(self.research.prepare(body))
        self.assertNotEqual(r1["report_id"], r2["report_id"])
        p1 = self.research.dir / r1["report_id"] / "report.json"
        p2 = self.research.dir / r2["report_id"] / "report.json"
        self.assertTrue(p1.exists() and p2.exists())
        self.assertNotEqual(p1.read_text(encoding="utf-8"), "")
        # Both reports retain same symbol but distinct identities
        self.assertEqual(json.loads(p1.read_text(encoding="utf-8"))["contracts"][0]["symbol"], "BTCUSDT")
        self.assertEqual(json.loads(p2.read_text(encoding="utf-8"))["contracts"][0]["symbol"], "BTCUSDT")

    def test_r1_attempt_id_logged(self):
        body = {"contracts": [{"symbol": "BTCUSDT", "market": "USDⓈ-M"}], "depth": "standard"}
        spec = self.research.prepare(body)
        spec["task_id"] = "task-test-attempt"
        spec["submission_id"] = "sub-test-attempt"
        out = self.research.run(spec)
        self.assertEqual(out["status"], "success")
        calls = self.research.call_history()
        attempts = [c for c in calls if c.get("kind") == "vendor_attempt"]
        self.assertTrue(attempts)
        self.assertTrue(str(attempts[0].get("attempt_id") or "").startswith("attempt-"))


    def test_save_local_report(self):
        saved = self.research.save_local_report({
            "title": "本地数据报告 · 1份",
            "content": "<article class=\"report-item\"><h3>BTCUSDT</h3><p>ok</p></article>",
            "contracts": [{"symbol": "BTCUSDT", "market": "USDⓈ-M"}],
        })
        self.assertTrue(saved["report_id"].startswith("report-"))
        rep = self.research.get_report(saved["report_id"])
        self.assertEqual(rep["source"], "local")
        hist = self.research.history()
        self.assertTrue(any(h.get("source") == "local" and h.get("report_id") == saved["report_id"] for h in hist))



    def test_prepare_scan_and_research_no_stub(self):
        body = {"mode": "scan_and_research", "contracts": [{"symbol": "BTCUSDT", "market": "USDⓈ-M"}], "depth": "standard"}
        spec = self.research.prepare(body)
        self.assertEqual(spec["mode"], "scan_and_research")
        self.assertTrue(spec.get("needs_scan"))
        self.assertIsNone(spec.get("scan_id"))
        self.assertIsNone(spec.get("revision"))
        self.assertIn("request_hash", spec)
        prev = self.research.preview(body)
        self.assertTrue(prev.get("needs_scan"))
        self.assertFalse(prev.get("cache_hit"))

    def test_prepare_scan_and_research_without_snapshot(self):
        (self.root / "scan.json").unlink()
        body = {"mode": "scan_and_research", "contracts": [{"symbol": "ETHUSDT"}], "depth": "standard"}
        spec = self.research.prepare(body)
        self.assertTrue(spec["needs_scan"])
        self.assertEqual(spec["selected_count"], 1)
        rows = (spec.get("_evidence") or {}).get("rows") or []
        self.assertTrue(rows and rows[0].get("missing"))

    def test_bind_snapshot_uses_new_revision(self):
        frozen = self.research.prepare({
            "mode": "scan_and_research",
            "contracts": [{"symbol": "BTCUSDT", "market": "USDⓈ-M"}],
            "depth": "standard",
        })
        pre_hash = frozen["request_hash"]
        (self.root / "scan.json").write_text(json.dumps({
            "scan_id": "scan-new",
            "revision": 9,
            "available_at": "2026-10-13T12:00:00+00:00",
            "rows": [{"symbol": "BTCUSDT", "market": "USDⓈ-M", "asset": "BTC", "price": 2}],
        }, ensure_ascii=False), encoding="utf-8")
        bound = self.research.bind_snapshot(frozen)
        self.assertFalse(bound.get("needs_scan"))
        self.assertEqual(bound["scan_id"], "scan-new")
        self.assertEqual(bound["revision"], 9)
        self.assertNotEqual(bound["request_hash"], pre_hash)
        self.assertEqual(bound["mode"], "snapshot")
        self.assertEqual(bound.get("origin_mode"), "scan_and_research")




    def test_f09_evidence_uses_real_fields_not_fiction(self):
        spec = self.research.prepare({"contracts": [{"symbol": "BTCUSDT", "market": "USDⓈ-M"}], "depth": "standard"})
        rows = (spec.get("_evidence") or {}).get("rows") or []
        self.assertTrue(rows)
        row = rows[0]
        self.assertIn("fundingPct", row)
        self.assertIn("priceChangeWindowPct", row)
        self.assertIn("ema4h", row)
        self.assertIn("fieldAvailableAt", row)
        self.assertNotIn("fundingRate", row)
        self.assertNotIn("changePct", row)
        claims = (spec.get("_evidence") or {}).get("claims") or {}
        self.assertFalse(claims.get("six_evidence_complete"))
        self.assertIn("evidence_hash", spec)
        self.assertIn("prompt_hash", spec)
        self.assertEqual(spec.get("baseUrl"), "https://example.test/v1")
        self.assertEqual(spec.get("config_version"), 1)

    def test_f09_cache_identity_includes_baseurl_evidence_prompt(self):
        body = {"contracts": [{"symbol": "BTCUSDT", "market": "USDⓈ-M"}], "depth": "standard"}
        spec1 = self.research.prepare(body)
        cm = spec1.get("cache_material") or {}
        for key in ("baseUrl", "config_version", "evidence_hash", "prompt_hash", "prompt_version", "configId"):
            self.assertIn(key, cm)
        # Change evidence → hash must change
        scan = json.loads((self.root / "scan.json").read_text(encoding="utf-8"))
        scan["rows"][0]["fundingPct"] = -0.99
        (self.root / "scan.json").write_text(json.dumps(scan, ensure_ascii=False), encoding="utf-8")
        spec2 = self.research.prepare(body)
        self.assertNotEqual(spec1["evidence_hash"], spec2["evidence_hash"])
        self.assertNotEqual(spec1["request_hash"], spec2["request_hash"])
        # Change baseUrl in public config → cache identity changes
        self.ai = FakeAi()
        orig = self.ai.public_config

        def pub_alt():
            d = orig()
            d = dict(d)
            d["baseUrl"] = "https://other.example/v1"
            return d
        self.ai.public_config = pub_alt
        self.research = AiResearch(self.root, self.ai)
        # restore scan funding for isolation — baseUrl alone should change hash
        scan["rows"][0]["fundingPct"] = -0.01
        (self.root / "scan.json").write_text(json.dumps(scan, ensure_ascii=False), encoding="utf-8")
        spec3 = self.research.prepare(body)
        self.assertNotEqual(spec2["request_hash"], spec3["request_hash"])
        self.assertEqual(spec3["baseUrl"], "https://other.example/v1")

    def test_f09_run_rejects_mismatched_current_config(self):
        body = {"contracts": [{"symbol": "BTCUSDT", "market": "USDⓈ-M"}], "depth": "standard"}
        spec = self.research.prepare(body)
        # Mutate live config after prepare
        orig = self.ai.public_config
        def pub_drift():
            d = dict(orig())
            d["baseUrl"] = "https://drifted.example/v1"
            d["configId"] = "cfg-drifted"
            return d
        self.ai.public_config = pub_drift
        with self.assertRaises(ResearchError) as ctx:
            self.research.run(spec)
        self.assertIn("不一致", str(ctx.exception))
        # force=true may proceed using frozen request fields
        spec_force = dict(spec)
        spec_force["force"] = True
        out = self.research.run(spec_force)
        self.assertEqual(out["status"], "success")
        rep = self.research.get_report(out["report_id"])
        self.assertEqual(rep.get("baseUrl"), spec["baseUrl"])  # frozen, not drifted
        self.assertEqual(rep.get("retrieval", {}).get("status"), "disabled")  # R2b: no requested_search → disabled, not fake-unverified
        self.assertFalse(rep.get("claims", {}).get("six_evidence_complete"))

    def test_f09_schema_validation_unverified_on_markdown(self):
        spec = self.research.prepare({"contracts": [{"symbol": "BTCUSDT", "market": "USDⓈ-M"}], "depth": "standard"})
        # Use rich schema like production
        spec["_evidence"] = dict(spec["_evidence"])
        spec["_evidence"]["schema"] = {
            "schema_version": "research-report-v1",
            "metadata": ["snapshot_id"],
            "contracts": ["symbol"],
            "basket": ["shared_risks"],
            "sources": ["evidence_id"],
        }
        self.ai._request_json = lambda *a, **k: (200, json.dumps({"choices": [{"message": {"content": "# BTCUSDT 报告\n不完整"}}]}).encode(), {})
        out = self.research.run(spec)
        self.assertEqual(out["status"], "failed")
        self.assertTrue(out["validation_failed"])
        sv = out.get("schema_validation") or {}
        self.assertEqual(sv.get("status"), "unverified")
        self.assertFalse(sv.get("ok"))
        self.assertEqual((out.get("retrieval") or {}).get("status"), "disabled")  # R2b: checkbox off ≠ retrieved

    def test_f09_schema_validation_success_on_structured_json(self):
        class JsonAi(FakeAi):
            def _request_json(self, method, url, headers, body, timeout):
                report = {
                    "metadata": {"snapshot_id": "scan-1"},
                    "contracts": [{"symbol": "BTCUSDT"}],
                    "basket": {"shared_risks": []},
                    "sources": [{"evidence_id": "e1"}],
                }
                payload = {"choices": [{"message": {"content": json.dumps(report, ensure_ascii=False)}}]}
                return 200, json.dumps(payload).encode(), {}
        self.research = AiResearch(self.root, JsonAi())
        spec = self.research.prepare({"contracts": [{"symbol": "BTCUSDT", "market": "USDⓈ-M"}], "depth": "standard"})
        spec["_evidence"] = dict(spec["_evidence"])
        spec["_evidence"]["schema"] = {
            "schema_version": "research-report-v1",
            "metadata": ["snapshot_id"],
            "contracts": ["symbol"],
            "basket": ["shared_risks"],
            "sources": ["evidence_id"],
        }
        # Recompute hashes not required for validation path
        out = self.research.run(spec)
        sv = out.get("schema_validation") or {}
        self.assertEqual(sv.get("status"), "success")
        self.assertTrue(sv.get("ok"))
        # Still must not claim retrieval success
        self.assertEqual(out["retrieval"]["status"], "disabled")  # R2b: adapter path present; search not requested

    def test_f09_edited_prompt_changes_cache_key(self):
        body = {"contracts": [{"symbol": "BTCUSDT", "market": "USDⓈ-M"}], "depth": "standard"}
        spec1 = self.research.prepare(body)
        templates = json.loads((self.root / "design" / "prompt-templates.json").read_text(encoding="utf-8"))
        templates["common"] = "只用证据。已编辑提示词。"
        (self.root / "design" / "prompt-templates.json").write_text(
            json.dumps(templates, ensure_ascii=False), encoding="utf-8"
        )
        spec2 = self.research.prepare(body)
        self.assertNotEqual(spec1["prompt_hash"], spec2["prompt_hash"])
        self.assertNotEqual(spec1["request_hash"], spec2["request_hash"])


if __name__ == "__main__":
    unittest.main()
