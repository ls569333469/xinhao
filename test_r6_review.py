# -*- coding: utf-8 -*-
"""R6 offline: RES-14 checks, REV-07 cutoffs, REV-09 auto/human split, RES-16 revisions. No paid calls."""
from __future__ import annotations

import datetime as dt
import json
import unittest

import research_review as rr
from ai_research import AiResearch
from test_r4_con_usage import Base, VendorStub

NOW = dt.datetime(2026, 10, 14, 12, 0, tzinfo=dt.timezone.utc)


class CheckTests(unittest.TestCase):
    def auto(self, content, **kw):
        args = dict(content=content, contracts=[{"symbol": "AAAUSDT"}], allowed_evidence_ids=["ev-ok-1"],
                    drafts=[], evidence_rows=[{"symbol": "AAAUSDT", "price": 1, "fundingPct": 0.01}], now=NOW)
        args.update(kw)
        return rr.automatic_checks(**args)

    def test_valid_passes_but_is_only_automatic(self):
        a = self.auto("AAA 催化见 ev-ok-1")
        self.assertEqual(a["status"], "passed")
        self.assertEqual(a["kind"], "automatic")
        self.assertIn("不代表内容充分", a["scope_note"])

    def test_opening_current_price_flagged(self):
        # Old Template B style (R7 failure): bare 当前价格 without cutoff on same line
        a = self.auto("# AAAUSDT 合约分析报告\n当前价格：$1 | 24h -3%\n\n## 1. 核心催化剂\n见 ev-ok-1 AAAUSDT")
        self.assertFalse(a["items"]["opening_not_current_price"]["ok"])
        self.assertIn("当前价格", a["items"]["opening_not_current_price"]["detail"])
        # R8 preferred: 快照价格（截至…） on the same line
        b = self.auto("# AAAUSDT 合约分析报告\n快照价格（截至 2026-10-13 12:02 UTC）：$1 | 24h -3%\n\n## 1. 核心催化剂\n见 ev-ok-1 AAAUSDT")
        self.assertTrue(b["items"]["opening_not_current_price"]["ok"])
        # Also accept 当前价格 if cutoff is on the same line
        c = self.auto("# AAA 报告\n当前价格：$1（快照截至 2026-10-13）| 24h -3%\nAAAUSDT ev-ok-1")
        self.assertTrue(c["items"]["opening_not_current_price"]["ok"])

    def test_invalid_citation(self):
        a = self.auto("AAAUSDT 见 ev-ok-1 与 ev-made-up-9")
        self.assertEqual(a["status"], "failed")
        self.assertEqual(a["items"]["citations_exist"]["invalid"], ["ev-made-up-9"])

    def test_missing_contract_and_empty(self):
        a = self.auto("只讲 BBB", contracts=[{"symbol": "AAAUSDT"}, {"symbol": "BBBUSDT"}])
        self.assertEqual(a["items"]["contracts_covered"]["missing"], ["AAAUSDT"])
        self.assertFalse(self.auto("  ")["items"]["non_empty"]["ok"])

    def test_wrong_strategy_dependency(self):
        drafts = [{"draft_id": "d1", "depends_on": ["fundingPct", "whaleFlow"], "symbol": "ZZZUSDT"}]
        a = self.auto("AAAUSDT", drafts=drafts)
        issues = a["items"]["strategy_dependencies"]["issues"]
        self.assertEqual([i["field"] for i in issues], ["whaleFlow", "ZZZUSDT"])

    def test_checks_block_separate(self):
        b = rr.checks_block(self.auto("AAAUSDT"))
        self.assertEqual(b["human"]["status"], "not_reviewed")
        self.assertEqual(b["content_review"]["status"], "pending")
        self.assertEqual(b["automatic"]["status"], "passed")


class CutoffTests(unittest.TestCase):
    def test_separate_cutoffs_fixed_clock(self):
        c = rr.data_cutoffs(now=NOW, snapshot_available_at="2026-10-14T10:00:00+00:00",
                            rows=[{"candleAsOf": "2026-10-14T00:00:00+00:00"}],
                            retrieval={"retrieved_at": "2026-10-14T11:00:00+00:00",
                                       "items": [{"published_at": "2025-01-01T00:00:00+00:00"}]})
        self.assertEqual(c["market"]["status"], "fresh")
        self.assertEqual(c["technical"]["status"], "stale")  # 12h > 8h
        self.assertEqual(c["news"]["status"], "as_retrieved")  # old publish date not auto-expired
        self.assertEqual(c["news"]["latest_published_at"], "2025-01-01T00:00:00+00:00")
        self.assertIn("知识时点", c["label"])
        self.assertEqual(c["policy_version"], rr.FRESHNESS_POLICY_VERSION)

    def test_epoch_ms_candle(self):
        c = rr.data_cutoffs(now=NOW, snapshot_available_at=None, rows=[{"candleAsOf": 1791878399999}], retrieval={})
        self.assertTrue(c["technical"]["cutoff"].startswith("2026-"))
        self.assertNotEqual(c["technical"]["status"], "unknown")

    def test_boundary_and_unknown(self):
        c = rr.data_cutoffs(now=NOW, snapshot_available_at="2026-10-14T04:00:00+00:00", rows=[], retrieval={})
        self.assertEqual(c["market"]["status"], "fresh")  # exactly 8h is not stale
        c2 = rr.data_cutoffs(now=NOW, snapshot_available_at="2026-10-14T03:59:00+00:00", rows=[], retrieval={})
        self.assertEqual(c2["market"]["status"], "stale")
        self.assertEqual(c["technical"]["status"], "unknown")
        self.assertEqual(c["news"]["status"], "unknown")


class ReportIntegrationTests(Base):
    def run_one(self):
        r = AiResearch(self.root, VendorStub(), clock=lambda: NOW)
        req = self.prep(r, ["AAAUSDT"], "sub-r6", "task-r6")
        return r, r.run(req)

    def test_report_has_cutoffs_and_checks(self):
        r, out = self.run_one()
        rep = r.get_report(out["report_id"])
        self.assertIn("market", rep["data_cutoffs"])
        self.assertEqual(rep["checks"]["automatic"]["kind"], "automatic")
        self.assertEqual(rep["checks"]["human"]["status"], "not_reviewed")
        self.assertEqual(rep["current_version"], 0)

    def test_revision_keeps_original_and_input(self):
        r, out = self.run_one()
        rid = out["report_id"]
        d = self.root / "runtime" / "reports" / rid
        before_rep, before_in = (d / "report.json").read_bytes(), (d / "prompt_input.json").read_bytes()
        rv = r.revisions()
        with self.assertRaises(ValueError):
            rv.add_revision(rid, {"content": "x"})  # editor/reason required
        meta = rv.add_revision(rid, {"editor": "雪球", "reason": "补官方公告", "content": "# 修订版",
                                     "added_evidence": [{"title": "公告", "source_url": "https://example.test/a"}]})
        self.assertEqual(meta["revision"], 1)
        self.assertFalse(meta["model_saw_added_evidence"])
        self.assertTrue(meta["added_evidence"][0]["evidence_id"].startswith("ev-post-"))
        self.assertEqual(before_rep, (d / "report.json").read_bytes())
        self.assertEqual(before_in, (d / "prompt_input.json").read_bytes())
        rep = r.get_report(rid)
        self.assertEqual(rep["current_version"], 1)
        self.assertEqual(rep["final_content"], "# 修订版")
        self.assertNotEqual(rep["content"], "# 修订版")  # original model output preserved
        rv.add_revision(rid, {"editor": "雪球", "reason": "改措辞", "content": "# v2"})
        self.assertEqual(r.get_report(rid)["revisions"][1]["base"], 1)

    def test_human_review_separate_from_automatic(self):
        r, out = self.run_one()
        rid = out["report_id"]
        rv = r.revisions()
        with self.assertRaises(ValueError):
            rv.add_human_review(rid, {"reviewer": "雪球", "result": "AI通过"})
        with self.assertRaises(ValueError):
            rv.add_human_review(rid, {"reviewer": "雪球", "result": "通过", "revision": 3})
        auto_before = r.get_report(rid)["checks"]["automatic"]
        rv.add_human_review(rid, {"reviewer": "雪球", "result": "部分通过", "note": "催化不全"})
        rep = r.get_report(rid)
        self.assertEqual(rep["checks"]["human"]["status"], "reviewed")
        self.assertEqual(rep["checks"]["human"]["records"][0]["kind"], "human")
        self.assertEqual(rep["checks"]["automatic"], auto_before)
        self.assertEqual(rep["checks"]["content_review"]["status"], "pending")

    def test_bad_ids(self):
        r, _ = self.run_one()
        with self.assertRaises(KeyError):
            r.revisions().add_revision("report-" + "0" * 32, {})
        with self.assertRaises(KeyError):
            r.revisions().add_revision("../x", {})


if __name__ == "__main__":
    unittest.main()