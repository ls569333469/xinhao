# -*- coding: utf-8 -*-
"""R2a offline checks: template B schema, REV-08 legacy, migration, preview IDs."""
from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from ai_research import (
    AiResearch,
    LEGACY_STANDARD_INSTRUCTION,
    LEGACY_STANDARD_INSTRUCTION_SHA256,
    SCHEMA_V2_B,
    TEMPLATE_B_SECTIONS,
)


class FakeAi:
    def __init__(self, content="# 报告\n证据充足。"):
        self._content = content
        self.store = mock.Mock()
        self.store.read_key.return_value = "sk-test"

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
        payload = {"choices": [{"message": {"content": self._content}}]}
        return 200, json.dumps(payload).encode(), {}

    def _classify_http(self, status, text):
        return ("ok", "ok") if status == 200 else ("http_error", text[:80])


B_MD = """# DEMOUSDT 分析报告
当前价格：$1.00 | 24h +1.0%

## 核心催化剂
主催化：测试

## 价格与市场
价格与流动性简述

## 风险与机会
利多1；风险1

## 技术面
4h 站上均线；ATR=0 先核对精度后再判定，不自动无效

## 合约结构
账户比与持仓比分开；六指标未知保留

## 基本面
产品简述

## 支撑压力
支撑|1.0|依据A

## 综合判断
短结论；163m 与 127m 口径差异待核对
"""


class R2aTemplateBTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        (self.root / "design").mkdir()
        # Use production-like B templates from out/
        root = Path(__file__).resolve().parent
        prod = root / "design" / "prompt-templates.json"
        if not prod.exists():
            prod = root / "prompt-templates.json"
        if not prod.exists():
            prod = Path(r"C:/Users/ls569/.grokbot/r2a-stage/out/prompt-templates.json")
        (self.root / "design" / "prompt-templates.json").write_text(
            prod.read_text(encoding="utf-8"), encoding="utf-8"
        )
        (self.root / "scan.json").write_text(json.dumps({
            "scan_id": "scan-1",
            "revision": 1,
            "available_at": "2026-10-14T00:00:00+00:00",
            "rows": [{
                "symbol": "BTCUSDT", "market": "USDⓈ-M", "asset": "BTC", "price": 1,
                "fundingPct": -0.01, "fieldAvailableAt": {"price": "2026-10-14T00:00:00+00:00"},
                "asOf": "2026-10-14T00:00:00+00:00",
            }],
        }, ensure_ascii=False), encoding="utf-8")

    def tearDown(self):
        self.tmp.cleanup()

    def test_templates_standard_is_b(self):
        r = AiResearch(self.root, FakeAi())
        t = r._templates()
        self.assertTrue(t["version"].startswith("research-prompts-"))
        self.assertEqual(set(t["profiles"]), {"standard"})
        self.assertEqual(t["profiles"]["standard"].get("template"), "B")
        self.assertEqual(t["schema"]["schema_version"], SCHEMA_V2_B)
        self.assertEqual(t["schema"]["sections"], list(TEMPLATE_B_SECTIONS))
        self.assertIn("ATR=0", t["profiles"]["standard"]["instruction"])
        self.assertNotIn("163m", t["profiles"]["standard"]["instruction"])
        self.assertNotIn("127m", t["common"])
        self.assertIn("不要用「二手日历」", t["profiles"]["standard"]["instruction"])

    def test_legacy_sha_constant(self):
        self.assertEqual(
            hashlib.sha256(LEGACY_STANDARD_INSTRUCTION.strip().encode()).hexdigest(),
            LEGACY_STANDARD_INSTRUCTION_SHA256,
        )

    def test_migrate_unmodified_legacy_standard(self):
        r = AiResearch(self.root, FakeAi())
        t = r._templates()
        profile = t["profiles"]["standard"]
        instr, search, focus, mig = r._effective_prompt_bits(
            profile, depth_template=LEGACY_STANDARD_INSTRUCTION
        )
        self.assertEqual(mig["action"], "migrated_default_to_template_b")
        self.assertIn("模板 B", instr)
        self.assertNotEqual(instr.strip(), LEGACY_STANDARD_INSTRUCTION.strip())

    def test_keep_user_custom_prompt(self):
        r = AiResearch(self.root, FakeAi())
        t = r._templates()
        profile = t["profiles"]["standard"]
        custom = "只关注资金费率与解锁时间，自定义关注点不要覆盖。"
        instr, _, _, mig = r._effective_prompt_bits(profile, depth_template=custom)
        self.assertEqual(instr, custom)
        self.assertEqual(mig["action"], "keep_user_custom")

    def test_schema_b_markdown_ok(self):
        r = AiResearch(self.root, FakeAi())
        t = r._templates()
        sv = r._validate_report_schema(B_MD, t["schema"])
        self.assertIn(sv["status"], ("success", "partial"))
        self.assertEqual(sv["body_kind"], "template_b_markdown")
        self.assertEqual(len(sv.get("missing_sections") or []), 0)

    def test_schema_b_json_sections_ok(self):
        r = AiResearch(self.root, FakeAi())
        t = r._templates()
        payload = {
            "schema_version": SCHEMA_V2_B,
            "body_template": "B",
            "metadata": {},
            "contracts": [{"symbol": "BTCUSDT", "sections": {s: "x" for s in TEMPLATE_B_SECTIONS}}],
            "sections": {s: "x" for s in TEMPLATE_B_SECTIONS},
            "basket": {},
            "sources": [{"evidence_id": "e1"}],
        }
        sv = r._validate_report_schema(json.dumps(payload, ensure_ascii=False), t["schema"])
        self.assertEqual(sv["status"], "success")
        self.assertTrue(sv["ok"])

    def test_rev08_old_v1_not_forced_as_b(self):
        r = AiResearch(self.root, FakeAi())
        t = r._templates()
        old = {
            "schema_version": "research-report-v1",
            "metadata": {"snapshot_id": "s"},
            "contracts": [{"symbol": "BTCUSDT", "facts_with_evidence_ids": []}],
            "basket": {"shared_risks": []},
            "sources": [{"evidence_id": "e1"}],
        }
        sv = r._validate_report_schema(json.dumps(old, ensure_ascii=False), t["schema"])
        self.assertTrue(sv.get("legacy_compatible"))
        self.assertEqual(sv["status"], "success")
        # Must NOT list Chinese B section titles as missing
        for s in TEMPLATE_B_SECTIONS:
            self.assertNotIn(s, sv.get("missing_sections") or [])

    def test_preview_returns_submission_and_intent(self):
        r = AiResearch(self.root, FakeAi())
        prev = r.preview({"contracts": [{"symbol": "BTCUSDT", "market": "USDⓈ-M"}], "depth": "standard"})
        self.assertTrue(str(prev.get("submission_id") or "").startswith("sub-"))
        self.assertTrue(prev.get("intent_hash"))
        self.assertEqual(prev.get("body_template"), "B")
        self.assertEqual(prev.get("schema_version"), SCHEMA_V2_B)

    def test_run_stamps_body_template(self):
        r = AiResearch(self.root, FakeAi(content=B_MD))
        spec = r.prepare({"contracts": [{"symbol": "BTCUSDT", "market": "USDⓈ-M"}], "depth": "standard"})
        out = r.run(spec)
        rep = r.get_report(out["report_id"])
        self.assertEqual(rep.get("body_template"), "B")
        self.assertEqual(rep.get("schema_version"), SCHEMA_V2_B)
        self.assertEqual((out.get("schema_validation") or {}).get("body_kind"), "template_b_markdown")


if __name__ == "__main__":
    unittest.main()
