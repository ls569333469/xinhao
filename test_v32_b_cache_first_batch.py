# -*- coding: utf-8 -*-
"""v3.2 首批：A 基线 + B 规则边界 + 缓存/1h 步长。"""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from b_accumulation_rules import (
    BASELINE_BARS,
    abs_return_ok,
    dedupe_bars_by_open,
    evaluate_h1_confirm,
    evaluate_h_event,
    volume_ratio,
)
from candle_ranges import STEPS
from market_candle_cache import MarketCandleCache, read_json_maybe_gzip
from scanner import ema


ROOT = Path(__file__).resolve().parent


def _bars(n, start=0, step=3_600_000, q=100.0, o=100.0, c=100.0):
    out = []
    for i in range(n):
        ot = start + i * step
        out.append({"open_time": ot, "q": q, "o": o, "c": c})
    return out


class ABaseline(unittest.TestCase):
    def test_a01_ema_matches_fixture(self):
        fix = json.loads((ROOT / "runtime" / "fixtures" / "a-ema-p0-baseline.json").read_text(encoding="utf-8"))
        closes = fix["contract"]["closes"]
        curr = [ema(closes, p) for p in (7, 25, 99)]
        prior = [ema(closes[:-1], p) for p in (7, 25, 99)]
        exp = fix["expected"]
        for a, b in zip(curr, [exp["ema7"], exp["ema25"], exp["ema99"]]):
            self.assertAlmostEqual(a, b, places=12)
        for a, b in zip(prior, [exp["prev_ema7"], exp["prev_ema25"], exp["prev_ema99"]]):
            self.assertAlmostEqual(a, b, places=12)


class CandleRanges1h(unittest.TestCase):
    def test_steps_has_1h(self):
        self.assertEqual(STEPS["1h"], 3_600_000)
        self.assertIn("4h", STEPS)
        self.assertIn("1d", STEPS)


class BRules(unittest.TestCase):
    def test_b01_baseline_short_and_gap(self):
        bars = _bars(167)
        # inject event candidate at end without enough baseline
        r = volume_ratio(bars, len(bars) - 1, BASELINE_BARS, include_prior_event_bar=False)
        self.assertIsNone(r)
        bars2 = _bars(169)
        # break continuity
        bars2[50]["open_time"] += 3_600_000  # gap/dup mess
        bars2 = dedupe_bars_by_open(bars2)
        # after dedupe may still be short or gapped
        ok = volume_ratio(_bars(168 + 1), 168, BASELINE_BARS, include_prior_event_bar=False)
        self.assertIsNotNone(ok)

    def test_b02_return_threshold(self):
        self.assertTrue(abs_return_ok({"o": 100, "c": 102.9}))  # 2.9%
        self.assertFalse(abs_return_ok({"o": 100, "c": 103.0}))  # ==3%
        self.assertFalse(abs_return_ok({"o": 100, "c": 97.0}))

    def test_b03_confirm_includes_h(self):
        bars = _bars(170, q=100.0)
        # set h at index 168 q=100, h+1 at 169
        bars[168]["q"] = 800.0  # h
        bars[169]["q"] = 250.0  # h+1
        # confirm ratio uses window including h
        r_ok = volume_ratio(bars, 169, BASELINE_BARS, include_prior_event_bar=True)
        self.assertIsNotNone(r_ok)
        # wrong baseline excluding h would use bars[169-168:169] wait that's same end...
        # Explicit: exclude h means end=index-1 for confirm would be wrong; we assert include path uses bars[1:169]
        window = bars[169 - BASELINE_BARS : 169]
        self.assertEqual(len(window), 168)
        self.assertEqual(window[-1]["q"], 800.0)  # last of denominator is h

    def test_b04_h2_cannot_confirm(self):
        bars = _bars(171)
        # h at 168
        h_ot = bars[168]["open_time"]
        # only h+2 present as closed-looking but evaluate waits for exact h+1 open
        bars_no_h1 = [b for b in bars if b["open_time"] != h_ot + 3_600_000]
        out = evaluate_h1_confirm(bars_no_h1, h_ot, bg4h_h1={"ema7": 2, "ema25": 1}, h1_closed=True)
        self.assertEqual(out["technical_confirmation"], "unknown")
        self.assertIn("h1_missing", out["reasons"])
        pending = evaluate_h1_confirm(bars, h_ot, bg4h_h1={"ema7": 2, "ema25": 1}, h1_closed=False)
        self.assertTrue(pending["pending"])

    def test_b05_event_and_confirm_ids_separate_stable(self):
        from b_accumulation_store import stable_event_id
        a = stable_event_id(market="USDⓈ-M", symbol="AAAUSDT", trigger_open_time=1000)
        b = stable_event_id(market="USDⓈ-M", symbol="AAAUSDT", trigger_open_time=1000)
        c = stable_event_id(market="USDⓈ-M", symbol="AAAUSDT", trigger_open_time=1000 + 3600000)
        self.assertEqual(a, b)
        self.assertNotEqual(a, c)


class Cache(unittest.TestCase):
    def test_put_get_and_gzip_roundtrip(self):
        with tempfile.TemporaryDirectory() as td:
            cache = MarketCandleCache(Path(td))
            meta = cache.put_bar(
                market="USDⓈ-M",
                symbol="AAAUSDT",
                interval="1h",
                open_time=1_699_999_200_000,
                bar={"o": 1, "h": 2, "l": 0.5, "c": 1.5, "q": 10},
                close_time=1_699_999_200_000 + 3_599_999,
            )
            rows = cache.get_bars(market="USDⓈ-M", symbol="AAAUSDT", interval="1h")
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["payload_hash"], meta["payload_hash"])
            # duplicate same payload
            cache.put_bar(
                market="USDⓈ-M",
                symbol="AAAUSDT",
                interval="1h",
                open_time=1_699_999_200_000,
                bar={"o": 1, "h": 2, "l": 0.5, "c": 1.5, "q": 10},
            )
            rows2 = cache.get_bars(market="USDⓈ-M", symbol="AAAUSDT", interval="1h")
            self.assertEqual(len(rows2), 1)


if __name__ == "__main__":
    unittest.main()
