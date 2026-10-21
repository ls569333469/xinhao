# -*- coding: utf-8 -*-
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from b_1h_ingest import ingest_symbol_1h
from b_observe_evaluate import evaluate_symbol_from_cache
from market_candle_cache import MarketCandleCache


def _bar(open_t, q, o=100.0, c=100.5):
    # closed 1h
    return [open_t, str(o), str(o + 1), str(o - 1), str(c), "1", open_t + 3_599_999, str(q)]


class P3EvaluateStore(unittest.TestCase):
    def test_warmup_and_event_write(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            cache = MarketCandleCache(root)
            base = 1_699_999_200_000
            raw = []
            # 170 bars: 168 baseline + h + h1
            for i in range(170):
                q = 10.0 if i < 168 else (80.0 if i == 168 else 25.0)
                # keep abs return < 3%
                raw.append(_bar(base + i * 3_600_000, q, o=100.0, c=101.0))
            ingest_symbol_1h(cache, market="USDⓈ-M", symbol="BBBUSDT", raw_klines=raw)
            h_open = base + 168 * 3_600_000
            boundary = h_open + 3_600_000
            # bg4h pass ema7>ema25
            out = evaluate_symbol_from_cache(
                root,
                market="USDⓈ-M",
                symbol="BBBUSDT",
                target_close_boundary=boundary,
                bg4h={"ema7": 2.0, "ema25": 1.0},
                bg4h_h1={"ema7": 2.0, "ema25": 1.0},
            )
            self.assertTrue(out["evaluated"])
            self.assertEqual(out["technical_event"], "pass")
            store_path = root / "runtime" / "b-accumulation-events.json"
            self.assertTrue(store_path.exists())


if __name__ == "__main__":
    unittest.main()
