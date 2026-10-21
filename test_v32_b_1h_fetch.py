# -*- coding: utf-8 -*-
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from b_1h_fetch import fetch_1h_klines, fetch_oi_lsr_1h, make_fetch_1h
from b_observe_runner import run_b_observe_round
from b_observe_universe import load_universe
from observe_config import OBSERVE_DEFAULT, TIMING_DEFAULT


class FetchMockTests(unittest.TestCase):
    def test_fetch_1h_and_round(self):
        open_t = 1_699_999_200_000
        raw = []
        for i in range(170):
            q = 10 if i < 168 else 90
            ot = open_t + i * 3_600_000
            raw.append([ot, "100", "101", "99", "100.5", "1", ot + 3_599_999, str(q)])

        def requester(url, params=None):
            if "klines" in url:
                return raw, None
            if "openInterestHist" in url:
                return [{"sumOpenInterestValue": "1e9", "timestamp": open_t}], None
            if "globalLongShortAccountRatio" in url:
                return [{"longShortRatio": "0.5", "timestamp": open_t}], None
            return None, "unexpected"

        data = fetch_1h_klines("USDⓈ-M", "DDDUSDT", requester=requester)
        self.assertEqual(len(data), 170)
        oi = fetch_oi_lsr_1h("USDⓈ-M", "DDDUSDT", requester=requester)
        self.assertEqual(oi["lsr_quality"], "pass")
        self.assertEqual(oi["longShortRatio"], 0.5)

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "runtime").mkdir()
            (root / "runtime" / "b-observe-universe.json").write_text(
                '[{"market":"USDⓈ-M","symbol":"DDDUSDT","bg4h":{"ema7":2,"ema25":1},"bg4h_h1":{"ema7":2,"ema25":1}}]',
                encoding="utf-8",
            )
            observe = {**OBSERVE_DEFAULT, "enabled": True}
            boundary = open_t + 168 * 3_600_000 + 3_600_000
            # now_ms after boundary+offset
            now_ms = boundary + 180_000
            out = run_b_observe_round(
                root,
                symbols=load_universe(root),
                observe=observe,
                timing=TIMING_DEFAULT,
                now_ms=now_ms,
                fetch_1h=make_fetch_1h(requester),
            )
            self.assertFalse(out["skipped"])
            self.assertGreaterEqual(out["written"], 1)


if __name__ == "__main__":
    unittest.main()
