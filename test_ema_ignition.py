# -*- coding: utf-8 -*-
"""Tests for EMA stack / barVolumeRatio7 / ignition grades (I4 Stage1 + I4b Stage2)."""
import unittest
from scanner import rsi, sustained_new_lows, ignition_fields, classify
from strategy_registry import evaluate, REGISTRY


def series_trend(n=300, start=100.0, step=0.15):
    return [start + i * step for i in range(n)]


def series_below(n=300, start=100.0, step=-0.2):
    return [start + i * step for i in range(n)]


class TestEmaIgnition(unittest.TestCase):
    def test_rsi_bounds(self):
        self.assertGreater(rsi(series_trend(50, 10, 0.5)), 70)
        self.assertLess(rsi(series_below(50, 100, -0.5)), 30)
        self.assertIsNone(rsi([1, 2, 3]))

    def test_sustained_new_lows(self):
        closes = []
        price = 100.0
        for day in range(7):
            for _ in range(6):
                closes.append(price)
                price -= 0.05
            price -= 1.0
        self.assertTrue(sustained_new_lows(closes))
        self.assertFalse(sustained_new_lows([50.0] * 50))
        self.assertIsNone(sustained_new_lows([1.0] * 10))

    def test_bar_volume_ratio7(self):
        fields = ignition_fields(series_trend(200), [10.0] * 199 + [70.0])
        self.assertAlmostEqual(fields["barVolumeRatio7"], 7.0, places=6)

    def test_short_window_quality(self):
        fields = ignition_fields(series_trend(130), [1.0] * 130)
        self.assertEqual(fields["emaQuality"], "short_window")

    def test_grade_strong_deterministic(self):
        import scanner
        closes = [100.0] * 150
        volumes = [10.0] * 149 + [25.0]

        def fake_ema(values, period):
            is_prior = len(values) == len(closes) - 1
            if is_prior:
                return {7: 10.0, 25: 11.0, 99: 12.0}[period]
            return {7: 13.0, 25: 12.0, 99: 11.0}[period]

        orig = scanner.ema
        scanner.ema = fake_ema
        try:
            fields = ignition_fields(closes, volumes)
        finally:
            scanner.ema = orig
        self.assertTrue(fields["emaStack"])
        self.assertFalse(fields["emaStackPrev"])
        self.assertTrue(fields["fullCross"])
        self.assertGreaterEqual(fields["barVolumeRatio7"], 2)
        self.assertEqual(fields["ignitionGrade"], "strong")

    def test_grade_medium_deterministic(self):
        import scanner
        closes = [100.0] * 150
        volumes = [10.0] * 149 + [25.0]

        def fake_ema(values, period):
            is_prior = len(values) == len(closes) - 1
            if is_prior:
                return {7: 9.0, 25: 10.0, 99: 12.0}[period]
            return {7: 10.5, 25: 10.0, 99: 12.0}[period]

        orig = scanner.ema
        scanner.ema = fake_ema
        try:
            fields = ignition_fields(closes, volumes)
        finally:
            scanner.ema = orig
        self.assertTrue(fields["ema7Cross25"])
        self.assertTrue(fields["priceAboveEma99"])
        self.assertFalse(fields["emaStack"])
        self.assertEqual(fields["ignitionGrade"], "medium")

    def test_grade_weak_deterministic(self):
        import scanner
        closes = [100.0] * 150
        volumes = [10.0] * 150

        def fake_ema(values, period):
            return {7: 90.0, 25: 95.0, 99: 80.0}[period]

        orig = scanner.ema
        scanner.ema = fake_ema
        try:
            fields = ignition_fields(closes, volumes)
        finally:
            scanner.ema = orig
        self.assertTrue(fields["priceAboveEma99"])
        self.assertTrue(fields["ema7Below25"])
        self.assertEqual(fields["ignitionGrade"], "weak")

    def test_classify_funding_bonus_not_hard_gate(self):
        row = {
            "oiCapPct": 40, "accountRatio": 0.5, "fundingAprPct": 10,
            "fundingIntervalStatus": "configured", "closed4h": 1, "ema4h": [2, 3, 4],
            "volume24Usd": 10_000_000, "ignitionGrade": "none",
            "belowAllEma": True, "sidewaysOk": False,
        }
        classify(row)
        self.assertTrue(row["state"].startswith("杠杆异动候选"))
        self.assertNotIn("收盘突破线索", row["state"])

    def test_classify_strong_ignition_replaces_old_breakout(self):
        row = {
            "oiCapPct": 40, "accountRatio": 0.5, "fundingAprPct": -80,
            "fundingIntervalStatus": "configured", "closed4h": 10, "ema4h": [9, 8, 7],
            "volume24Usd": 10_000_000, "ignitionGrade": "strong",
            "crossEma99": True, "barVolumeRatio": 3, "belowAllEma": False, "sidewaysOk": False,
        }
        classify(row)
        self.assertTrue(row["state"].startswith("点火强信号·观察"))
        self.assertNotIn("收盘突破线索", row["state"])

    def test_classify_weak_flags_avoid(self):
        row = {
            "oiCapPct": 40, "accountRatio": 0.5, "fundingAprPct": -80,
            "fundingIntervalStatus": "configured", "closed4h": 10, "ema4h": [9, 8, 7],
            "volume24Usd": 10_000_000, "ignitionGrade": "weak",
            "crossEma99": True, "barVolumeRatio": 3, "belowAllEma": False,
        }
        classify(row)
        self.assertTrue(any("弱穿线" in f for f in row["riskFlags"]))
        self.assertNotIn("收盘突破线索", row["state"])

    def test_registry_i4_title_and_i4b_extra(self):
        i4 = next(i for i in REGISTRY["indicators"] if i["id"] == "I4")
        self.assertEqual("EMA三线金叉", i4["title"])
        self.assertIn("非入场", i4["formula"])
        extras = REGISTRY.get("extraIndicators") or []
        self.assertTrue(any(x["id"] == "I4b" for x in extras))
        rule = extras[0]["rule"]
        self.assertTrue(evaluate(rule, {"ignitionGrade": "strong"}))
        self.assertFalse(evaluate(rule, {"ignitionGrade": "weak"}))

    def test_grade_enum_only(self):
        f = ignition_fields(series_trend(400, 20, 0.05), [3.0] * 399 + [10.0])
        self.assertIn(f["ignitionGrade"], ("strong", "medium", "weak", "none"))
        self.assertIsNotNone(f["barVolumeRatio7"])


if __name__ == "__main__":
    unittest.main()
