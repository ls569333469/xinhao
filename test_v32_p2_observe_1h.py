# -*- coding: utf-8 -*-
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from b_1h_ingest import closed_1h_bars, ingest_symbol_1h
from market_candle_cache import MarketCandleCache
from observe_config import migrate_observe, migrate_timing, target_close_boundary_ms, validate_observe
from task_service import TaskService, validate_scan


class ObserveConfigTests(unittest.TestCase):
    def test_defaults_disabled(self):
        o = migrate_observe(None)
        self.assertFalse(o["enabled"])
        t = migrate_timing(None)
        self.assertEqual(t["closeOffsetSec"], 120)

    def test_boundary_with_offset(self):
        # 10:00:30 with offset 120s → still before 10:00+120, so boundary=09:00
        now = 10 * 3600_000 + 30_000
        b = target_close_boundary_ms(now, offset_sec=120)
        self.assertEqual(b, 9 * 3600_000)
        # 10:03 → boundary 10:00
        now2 = 10 * 3600_000 + 180_000
        self.assertEqual(target_close_boundary_ms(now2, offset_sec=120), 10 * 3600_000)


class IngestTests(unittest.TestCase):
    def test_closed_filter_and_ingest(self):
        open_t = 1_699_999_200_000
        raw = [[open_t, "1", "2", "0.5", "1.5", "10", open_t + 3_599_999, "100"]]
        # unclosed
        raw.append([open_t + 3_600_000, "1", "2", "0.5", "1.5", "10", open_t + 3_600_000 + 1000, "100"])
        bars = closed_1h_bars(raw)
        self.assertEqual(len(bars), 1)
        with tempfile.TemporaryDirectory() as td:
            cache = MarketCandleCache(Path(td))
            meta = ingest_symbol_1h(cache, market="USDⓈ-M", symbol="AAAUSDT", raw_klines=raw, target_close_boundary=open_t + 3_600_000)
            self.assertEqual(meta["written"], 1)
            rows = cache.get_bars(market="USDⓈ-M", symbol="AAAUSDT", interval="1h")
            self.assertEqual(len(rows), 1)


class TaskSettingsMigration(unittest.TestCase):
    def test_settings_expose_observe_off(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "runtime").mkdir()
            svc = TaskService(root, runner=lambda: None)
            s = svc.settings()
            self.assertIn("observe", s)
            self.assertFalse(s["observe"]["enabled"])
            self.assertEqual(s["timing"]["closeOffsetSec"], 120)
            # saving scan alone must not enable observe
            out = svc.save_settings({"version": s["version"], "scan": s["scan"]})
            self.assertFalse(out["observe"]["enabled"])
            self.assertFalse(out["scan"]["enabled"])


if __name__ == "__main__":
    unittest.main()


class ScheduleFireTests(unittest.TestCase):
    def test_disabled_never_enqueues(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "runtime").mkdir()
            svc = TaskService(root, runner=lambda: None)
            # observe off
            for _ in range(3):
                svc.tick()
            tasks = svc.tasks()
            self.assertFalse(any(x["kind"] == "b_observe_1h" for x in tasks))

    def test_enable_sets_next_due_without_scan(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "runtime").mkdir()
            svc = TaskService(root, runner=lambda: None)
            s = svc.settings()
            out = svc.save_settings({
                "version": s["version"],
                "scan": s["scan"],
                "observe": {**s["observe"], "enabled": True},
            })
            self.assertTrue(out["observe"]["enabled"])
            self.assertFalse(out["scan"]["enabled"])
            self.assertIsNotNone(out["observe_next_due"])

