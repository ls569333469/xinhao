# -*- coding: utf-8 -*-
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from b_accumulation_rules import BAR_MS
from b_contemporaneous import contemporaneous_ok, pick_sample_at_or_before
from b_observe_evaluate import evaluate_symbol_from_cache
from b_1h_ingest import ingest_symbol_1h
from market_candle_cache import MarketCandleCache
from b_process_order import process_bars_in_order


class B07Contemporaneous(unittest.TestCase):
    def test_gate_edges(self):
        t = 1_000_000
        self.assertEqual(contemporaneous_ok(t, t), "pass")
        self.assertEqual(contemporaneous_ok(t, t - 300_000), "pass")
        self.assertEqual(contemporaneous_ok(t, t - 300_001), "fail")
        self.assertEqual(contemporaneous_ok(t, t + 1), "fail")
        self.assertEqual(contemporaneous_ok(t, None), "unknown")

    def test_pick_nearest(self):
        t = 1_000_000
        samples = [
            {"timestamp": t - 400_000, "v": 1},
            {"timestamp": t - 100_000, "v": 2},
            {"timestamp": t + 10, "v": 3},
        ]
        samp, q, delta = pick_sample_at_or_before(t, samples)
        self.assertEqual(samp["v"], 2)
        self.assertEqual(q, "pass")
        self.assertEqual(delta, 100_000)


class ProcessOrder(unittest.TestCase):
    def test_confirm_before_new_h(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            base = 1_699_999_200_000
            bars = []
            for i in range(170):
                q = 10.0 if i < 168 else (90.0 if i == 168 else 30.0)
                ot = base + i * BAR_MS
                bars.append({"open_time": ot, "o": 100.0, "h": 101.0, "l": 99.0, "c": 100.5, "q": q})
            def bg(_ot):
                return {"ema7": 2.0, "ema25": 1.0}
            out = process_bars_in_order(root, market="USDⓈ-M", symbol="EEEUSDT", bars_asc=bars, bg4h_for=bg)
            self.assertGreaterEqual(out["new_events"], 1)


class StructureWithSamples(unittest.TestCase):
    def test_oi_lsr_gate_in_evaluate(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            cache = MarketCandleCache(root)
            base = 1_699_999_200_000
            raw = []
            for i in range(170):
                q = 10.0 if i < 168 else 90.0
                ot = base + i * 3_600_000
                raw.append([ot, "100", "101", "99", "100.5", "1", ot + 3_599_999, str(q)])
            ingest_symbol_1h(cache, market="USDⓈ-M", symbol="FFFUSDT", raw_klines=raw)
            h_open = base + 168 * 3_600_000
            boundary = h_open + 3_600_000
            oi_samples = [{"timestamp": boundary, "sumOpenInterestValue": "40000000"}]
            lsr_samples = [{"timestamp": boundary - 60_000, "longShortRatio": "0.5"}]
            out = evaluate_symbol_from_cache(
                root,
                market="USDⓈ-M",
                symbol="FFFUSDT",
                target_close_boundary=boundary,
                bg4h={"ema7": 2, "ema25": 1},
                bg4h_h1={"ema7": 2, "ema25": 1},
                oi_samples=oi_samples,
                lsr_samples=lsr_samples,
                market_cap=100_000_000.0,
                cap_quality="pass",
            )
            self.assertTrue(out["evaluated"])


if __name__ == "__main__":
    unittest.main()
