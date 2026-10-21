# -*- coding: utf-8 -*-
"""P2 offline static checks: SCREEN/UI06/UI07/counts/CSV/header markers."""
from __future__ import annotations
import unittest
from pathlib import Path

class P2StaticTests(unittest.TestCase):
    def test_screen_critical_css(self):
        t = Path("design/a-shell.css").read_text(encoding="utf-8")
        self.assertIn("coin-sub-critical", t)

    def test_pro_table_stage_critical(self):
        t = Path("design/pro-table-runtime.js").read_text(encoding="utf-8")
        shared = Path("design/market-evidence.js").read_text(encoding="utf-8")
        self.assertIn("MarketEvidence.emaSummaryHtml", t)
        self.assertIn("coin-sub-critical", shared)
        self.assertIn("前7根点火量倍数", t)
        self.assertIn("barVolumeRatio7", t)

    def test_ui07_no_executable_banner(self):
        t = Path("design/strategy-runtime.js").read_text(encoding="utf-8")
        self.assertNotIn("executable 仍为 false", t)
        self.assertIn("未启用原因", t)

    def test_ui06_help(self):
        idx = Path("index.html").read_text(encoding="utf-8")
        self.assertIn("最新10根4H至少3次严格新低", idx)
        self.assertIn("首次", idx)
        me = Path("design/market-evidence.js").read_text(encoding="utf-8")
        self.assertIn("首次上穿", me)
        self.assertNotIn("emaStack===true&&r.emaStackPrev===false", me)

    def test_counts_and_ui08(self):
        t = Path("design/prototype.html").read_text(encoding="utf-8", errors="replace")
        self.assertIn("人数口径", t)
        self.assertIn("①②＋三线下方入池线索", t)
        self.assertIn("①②宽候选", t)
        self.assertIn("旧①②③组合", t)
        self.assertIn("持久池活跃", t)
        self.assertIn("scanMetaLine", t)
        self.assertIn("updateScanMetaLine", t)
        self.assertIn("10根4H", t)

    def test_ui05_csv_keys(self):
        me = Path("design/market-evidence.js").read_text(encoding="utf-8")
        for k in ["fullCross", "barVolumeRatio7", "newLowCount10", "amplitudeRatio20",
                  "pendingCross", "crossConfirm", "rules_version", "csvSchema"]:
            self.assertIn("'" + k + "'", me)
        self.assertIn("market-evidence-csv-v5-smart-money", me)

if __name__ == "__main__":
    unittest.main()
