# -*- coding: utf-8 -*-
"""Offline UI01 filter predicates + store confirmedEvents."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from ema_squeeze_store import EmaSqueezeStore, hydrate_rows_from_store


class Ui01PoolFilterTests(unittest.TestCase):
    def test_store_attaches_confirmed_events_after_confirm(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            store = EmaSqueezeStore(root)
            # 先入池（三线下方）
            r0 = {
                "market": "USDⓈ-M",
                "symbol": "AAAUSDT",
                "signals": [True, True],
                "sidewaysOk": True,
                "belowAllEma": True,
                "oiUsd": 5000,
                "fullCross": False,
                "mediumCrossEvent": False,
            }
            s0 = store.evaluate_row(r0, now="2026-10-01T00:00:00+00:00")
            self.assertEqual(s0["candidateStatus"], "active")
            self.assertEqual(s0.get("confirmedEvents"), [])

            # 同周期：已突破三线 + 当根完全金叉且放量 → 确认
            r1 = {
                "market": "USDⓈ-M",
                "symbol": "AAAUSDT",
                "signals": [True, True],
                "sidewaysOk": True,
                "belowAllEma": False,
                "oiUsd": 4800,
                "fullCross": True,
                "mediumCrossEvent": False,
                "barVolumeRatio7": 2.2,
                "ema4h": [3.0, 2.0, 1.0],
                "closed4h": 3.1,
                "candleAsOf": "2026-10-02T00:00:00+00:00",
            }
            s1 = store.evaluate_row(r1, now="2026-10-02T00:00:01+00:00")
            self.assertEqual(s1["candidateStatus"], "active")
            self.assertTrue(s1.get("brokeAboveThree"))
            self.assertIsInstance(s1.get("crossConfirm"), dict)
            self.assertIn("event_id", s1["crossConfirm"])
            self.assertTrue(s1.get("confirmedEvents"))
            ev = s1["confirmedEvents"][-1]
            for k in ("event_id", "cross_time", "confirm_time", "candidate_cycle_id", "rules_version", "cross_ema99"):
                self.assertIn(k, ev)

            # 下一根：本根不再是强，仍应通过 hydrate / evaluate 看到确认列表
            r2 = dict(r1)
            r2.update(fullCross=False, barVolumeRatio7=0.8, candleAsOf="2026-10-02T04:00:00+00:00", ignitionGrade="none")
            s2 = store.evaluate_row(r2, now="2026-10-02T04:00:01+00:00")
            self.assertEqual(s2["candidateStatus"], "active")
            self.assertTrue(s2.get("confirmedEvents"))
            # 模拟旧扫描行无 stage，仅 hydrate
            bare = {"market": "USDⓈ-M", "symbol": "AAAUSDT", "oiUsd": 4800, "belowAllEma": False, "ignitionGrade": "none"}
            n = hydrate_rows_from_store([bare], store=store)
            self.assertEqual(n, 1)
            self.assertEqual(bare["emaSqueezeStage"]["candidateStatus"], "active")
            self.assertTrue(bare["emaSqueezeStage"]["confirmedEvents"])
            self.assertEqual(bare["ignitionGrade"], "none")
            self.assertIs(bare["belowAllEma"], False)


if __name__ == "__main__":
    unittest.main()