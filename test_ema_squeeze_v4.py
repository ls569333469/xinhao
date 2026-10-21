# -*- coding: utf-8 -*-
"""EMA-Squeeze v4 acceptance samples (offline, no network)."""
import json
import tempfile
import unittest
from pathlib import Path

from ema_squeeze_rules import (
    full_cross,
    death_trend_new_lows,
    new_low_count,
    amplitude_ok,
    oi_contraction,
    candidate_expired,
    initial_stop_state,
    process_close_for_trail,
    resolve_ema99_exit_fill,
    VOL_CONFIRM_RATIO,
)
from ema_squeeze_store import EmaSqueezeStore
from frozen_exec import require_frozen_exec_params, FrozenExecConflict
from scanner import ignition_fields, sustained_new_lows
from backtest_store import resolve_exit_on_bar, resolve_ema99_trail_bar


class TestFullCross(unittest.TestCase):
    def test_exact_definition(self):
        # EMA25 just crossed 99, stack complete
        self.assertTrue(full_cross(13, 12, 11, 10, 11, 12))
        # EMA25 already above 99 previously — NOT full cross even if stack just formed
        self.assertFalse(full_cross(13, 12, 11, 10, 12, 11))
        # Equal prev EMA25==EMA99 allowed
        self.assertTrue(full_cross(13, 12, 11, 10, 11, 11))
        # Current equal stack fails
        self.assertFalse(full_cross(12, 12, 11, 10, 10, 11))

    def test_ignition_rejects_false_strong(self):
        """Audit P1-01: not emaStackPrev alone must not yield strong when EMA25 already >99."""
        import scanner
        closes = [100.0] * 150
        volumes = [10.0] * 149 + [25.0]

        def fake_ema(values, period):
            is_prior = len(values) == len(closes) - 1
            if is_prior:
                # prior stack incomplete but EMA25 already > EMA99
                return {7: 10.0, 25: 12.0, 99: 11.0}[period]
            return {7: 13.0, 25: 12.5, 99: 11.5}[period]

        orig = scanner.ema
        scanner.ema = fake_ema
        try:
            fields = ignition_fields(closes, volumes)
        finally:
            scanner.ema = orig
        self.assertTrue(fields["emaStack"])
        self.assertFalse(fields["emaStackPrev"])
        self.assertFalse(fields["fullCross"])
        self.assertNotEqual(fields["ignitionGrade"], "strong")


class TestVolumeWait(unittest.TestCase):
    def test_store_same_bar_confirm_and_next_bar(self):
        with tempfile.TemporaryDirectory() as td:
            store = EmaSqueezeStore(Path(td))
            # Enter candidate
            store.enter_or_keep(market="USDⓈ-M", symbol="AAAUSDT", oi_usd=20_000_000, now="2025-12-31T20:00:00+00:00")
            # Cross bar insufficient volume → pending
            row = {
                "market": "USDⓈ-M", "symbol": "AAAUSDT",
                "signals": [True, True, False, True],
                "sidewaysOk": True, "belowAllEma": False,
                "oiUsd": 20_000_000, "fullCross": True,
                "mediumCrossEvent": False, "barVolumeRatio7": 0.5,
                "ema4h": [13.0, 12.0, 11.0], "closed4h": 13.5,
                "candleAsOf": 1767239999999,
            }
            s1 = store.evaluate_row(row, now="2026-01-01T04:00:01+00:00")
            self.assertEqual(s1["candidateStatus"], "active")
            self.assertIsNotNone(s1["pendingCross"])
            self.assertIsNone(s1.get("crossConfirm") or None if not s1.get("crossConfirm") else s1["crossConfirm"])
            # Next bar volume OK + structure holds → confirm
            row2 = dict(row)
            row2["fullCross"] = False  # not a new cross
            row2["barVolumeRatio7"] = 3.0
            row2["candleAsOf"] = 1767239999999 + 14_400_000
            s2 = store.evaluate_row(row2, now="2026-01-01T08:00:01+00:00")
            self.assertTrue(isinstance(s2["crossConfirm"], dict))
            self.assertIn("event_id", s2["crossConfirm"])
            # Third bar volume would not revive a voided event — create new pending then void
            store2 = EmaSqueezeStore(Path(td))
            store2.enter_or_keep(market="USDⓈ-M", symbol="BBBUSDT", oi_usd=1_000_000, now="2026-01-31T20:00:00+00:00")
            r = {
                "market": "USDⓈ-M", "symbol": "BBBUSDT",
                "signals": [True, True], "sidewaysOk": True, "belowAllEma": True,
                "oiUsd": 1_000_000, "fullCross": True, "barVolumeRatio7": 0.4,
                "ema4h": [3, 2, 1], "closed4h": 3, "candleAsOf": 1769918399999,
            }
            store2.evaluate_row(r, now="2026-02-01T04:00:01+00:00")
            r2 = dict(r)
            r2.update(fullCross=False, barVolumeRatio7=0.5, candleAsOf=1769918399999 + 14_400_000, belowAllEma=False)
            s_void = store2.evaluate_row(r2, now="2026-02-01T08:00:01+00:00")
            self.assertTrue(s_void["crossConfirm"].get("void"))
            r3 = dict(r2)
            r3.update(barVolumeRatio7=5.0, candleAsOf=1769918399999 + 28_800_000)
            s3 = store2.evaluate_row(r3, now="2026-02-01T12:00:01+00:00")
            # No pending left; third-bar volume must not confirm the voided cross
            conf = s3.get("crossConfirm")
            self.assertTrue(conf is None or conf.get("void") or conf.get("blocked") or not conf.get("event_id"))


class TestNewLowsAmplitude(unittest.TestCase):
    def test_user_example_five_new_lows(self):
        # prepend warmup so old function has enough bars; new rule uses last 10
        base = [0.04] * 120
        example = [
            0.030, 0.029, 0.028, 0.029, 0.027,
            0.028, 0.026, 0.027, 0.025, 0.026,
        ]
        closes = base + example
        self.assertEqual(new_low_count(closes), 5)
        self.assertTrue(death_trend_new_lows(closes))
        # Old algorithm should still differ (audit: False on this series)
        self.assertFalse(sustained_new_lows(closes))

    def test_boundary_2_vs_3(self):
        # 2 new lows → False; 3 → True
        c2 = [10, 9, 9.5, 8.5, 8.6, 8.7, 8.8, 8.9, 9.0, 9.1]  # new lows at i=1,3 → 2
        self.assertEqual(new_low_count(c2), 2)
        self.assertFalse(death_trend_new_lows(c2))
        c3 = [10, 9, 9.5, 8.5, 8.6, 8.0, 8.1, 8.2, 8.3, 8.4]  # + i=5 → 3
        self.assertEqual(new_low_count(c3), 3)
        self.assertTrue(death_trend_new_lows(c3))
        # repeat min not counted
        crep = [10, 9, 9, 9, 9, 9, 9, 9, 9, 9]
        self.assertEqual(new_low_count(crep), 1)
        self.assertIsNone(death_trend_new_lows([1, 2, 3]))

    def test_amplitude(self):
        highs = [1.0] * 19 + [1.34]
        lows = [1.0] * 20
        self.assertTrue(amplitude_ok(highs, lows))
        highs2 = [1.0] * 19 + [1.35]
        self.assertFalse(amplitude_ok(highs2, lows))  # 1.35 not < 1.35


class TestOIAndExpiry(unittest.TestCase):
    def test_oi_boundaries(self):
        self.assertTrue(oi_contraction(20_000_000, 13_500_000))
        self.assertFalse(oi_contraction(20_000_000, 14_000_000))  # exactly 70%
        self.assertFalse(oi_contraction(20_000_000, 15_000_000))
        self.assertIsNone(oi_contraction(20_000_000, None))

    def test_30d_from_enter_not_cross(self):
        self.assertFalse(candidate_expired("2026-01-01T00:00:00+00:00", "2026-01-30T23:00:00+00:00"))
        self.assertTrue(candidate_expired("2026-01-01T00:00:00+00:00", "2026-01-31T00:00:00+00:00"))

    def test_enter_keeps_baseline(self):
        with tempfile.TemporaryDirectory() as td:
            store = EmaSqueezeStore(Path(td))
            a = store.enter_or_keep(market="M", symbol="X", oi_usd=1000, now="2026-01-01T00:00:00+00:00")
            b = store.enter_or_keep(market="M", symbol="X", oi_usd=999999, now="2026-01-10T00:00:00+00:00")
            self.assertEqual(a["entered_at"], b["entered_at"])
            self.assertEqual(b["entry_oi_usd"], 1000)


class TestEma99Trail(unittest.TestCase):
    def test_intrabar_breach_no_exit(self):
        st = initial_stop_state(cross_ema99=0.030, actual_entry_price=0.032)
        # User example: low 0.028 close 0.031 — we only see close
        st2 = process_close_for_trail(st, close=0.031, ema99=0.030)
        self.assertFalse(st2["pending_exit"])
        self.assertFalse(st2["trailing_activated"])  # 0.031 < entry 0.032
        st3 = process_close_for_trail(st2, close=0.029, ema99=0.030)
        self.assertTrue(st3["pending_exit"])

    def test_trail_activation_and_ratchet(self):
        st = initial_stop_state(cross_ema99=0.030, actual_entry_price=0.032)
        st = process_close_for_trail(st, close=0.031, ema99=0.0305)
        self.assertFalse(st["trailing_activated"])
        self.assertEqual(st["stop"], 0.030)
        st = process_close_for_trail(st, close=0.033, ema99=0.0305)
        self.assertTrue(st["trailing_activated"])
        self.assertEqual(st["stop"], 0.0305)
        st = process_close_for_trail(st, close=0.034, ema99=0.0308)
        self.assertEqual(st["stop"], 0.0308)
        st = process_close_for_trail(st, close=0.0335, ema99=0.0306)
        self.assertEqual(st["stop"], 0.0308)  # no down

    def test_equal_close_no_exit(self):
        st = initial_stop_state(cross_ema99=0.030, actual_entry_price=0.032)
        st = process_close_for_trail(st, close=0.030, ema99=0.030)
        self.assertFalse(st["pending_exit"])

    def test_old_pct_touch_still_exits_intrabar(self):
        # Old engine behavior preserved
        bar = [0, 0.032, 0.034, 0.028, 0.031]
        hit = resolve_exit_on_bar("long", 0.032, 0.040, 0.030, bar)
        self.assertIsNotNone(hit)
        self.assertEqual(hit["exit_reason"], "stop_loss")
        self.assertEqual(hit["exit_price"], 0.030)

    def test_new_mode_defers_to_next_open(self):
        st = initial_stop_state(cross_ema99=0.030, actual_entry_price=0.032)
        pos = {"trail_state": st, "pending_exit": False}
        bar1 = [1, 0.032, 0.034, 0.028, 0.029]  # close below stop
        pos, hit = resolve_ema99_trail_bar(pos, bar1, 0.030)
        self.assertIsNone(hit)
        self.assertTrue(pos["pending_exit"])
        bar2 = [2, 0.0285, 0.029, 0.027, 0.028]
        pos, hit = resolve_ema99_trail_bar(pos, bar2, 0.029)
        self.assertIsNotNone(hit)
        self.assertEqual(hit["exit_price"], 0.0285)
        self.assertEqual(hit["exit_reason"], "ema99_close_trail")


class TestFrozenExecMode(unittest.TestCase):
    def test_ema99_mode_without_tp_sl(self):
        ver = {
            "version_id": "v-ema99",
            "content_hash": "abc",
            "rules": {
                "params": {
                    "execution_mode": "ema99_close_trail",
                    "entry_rule": "ema_squeeze_confirmed",
                    "side": "long",
                    "candidate_max_days": 30,
                }
            },
        }
        out = require_frozen_exec_params(ver, context="backtest")
        self.assertEqual(out["execution_mode"], "ema99_close_trail")
        self.assertIsNone(out["tp_pct"])
        self.assertIsNone(out["sl_pct"])

    def test_ema99_refuses_missing_mode_fallback(self):
        ver = {
            "version_id": "v-bad",
            "rules": {"params": {"entry_rule": "ema_squeeze_confirmed"}},
        }
        with self.assertRaises(FrozenExecConflict):
            # no tp/sl and no ema99 mode → old path requires tp/sl
            require_frozen_exec_params(ver, context="paper")

    def test_ema99_refuses_mixed_sl_pct(self):
        ver = {
            "version_id": "v-mix",
            "rules": {
                "params": {
                    "execution_mode": "ema99_close_trail",
                    "entry_rule": "ema_squeeze_confirmed",
                    "sl_pct": 0.05,
                }
            },
        }
        with self.assertRaises(FrozenExecConflict):
            require_frozen_exec_params(ver, context="backtest")

    def test_old_pct_still_requires_tp_sl(self):
        ver = {
            "version_id": "v-old",
            "rules": {"params": {"tp_pct": 0.1, "sl_pct": 0.05, "max_hold_days": 7}},
        }
        out = require_frozen_exec_params(ver, context="paper")
        self.assertEqual(out["execution_mode"], "pct_touch")
        self.assertEqual(out["tp_pct"], 0.1)


class TestFingerprint(unittest.TestCase):
    def test_lf_crlf_same_hash(self):
        from strategy_registry import registry_fingerprint
        a = b'{"version": "x"}\n'
        b = b'{"version": "x"}\r\n'
        self.assertEqual(registry_fingerprint(a), registry_fingerprint(b))


if __name__ == "__main__":
    unittest.main()
