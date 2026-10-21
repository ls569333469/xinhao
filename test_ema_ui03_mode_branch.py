# -*- coding: utf-8 -*-
"""UI03 offline: observe/sim branch by frozen execution_mode; ema99 must not invent tp/sl/3%."""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from frozen_exec import require_frozen_exec_params, FrozenExecConflict
from eval_store import EvalStore, EvalConflict
from strategy_store import StrategyStore
from sim_store import SimStore


def _ema99_params(**extra):
    p = {
        "execution_mode": "ema99_close_trail",
        "entry_rule": "ema_squeeze_confirmed",
        "candidate_max_days": 30,
        "initial_stop": "cross_bar_ema99",
        "trail_activation": "first_close_above_entry",
        "intrabar_stop": False,
        "side": "long",
        "notional_u": 100.0,
        "params_schema_version": "ema99-close-trail-v1",
        "user_rules_version": "ema-squeeze-v4",
    }
    p.update(extra)
    return p


def _pct_params(**extra):
    p = {
        "execution_mode": "pct_touch",
        "tp_pct": 0.05,
        "sl_pct": 0.03,
        "max_hold_days": 42,
        "side": "long",
        "entry_rule": "close_above_prev_high",
        "notional_u": 100.0,
    }
    p.update(extra)
    return p


def _version(params, vid="ver-test"):
    return {
        "version_id": vid,
        "content_hash": "abc123",
        "enabled": True,
        "rules": {"base_template_id": "T2", "params": params},
    }


class FrozenModeBranchTests(unittest.TestCase):
    def test_ema99_no_tp_sl_required(self):
        out = require_frozen_exec_params(_version(_ema99_params()), {}, context="eval")
        self.assertEqual(out["execution_mode"], "ema99_close_trail")
        self.assertIsNone(out.get("tp_pct"))
        self.assertIsNone(out.get("sl_pct"))
        self.assertIsNone(out.get("max_hold_days"))
        self.assertEqual(out.get("initial_stop"), "cross_bar_ema99")

    def test_ema99_refuse_body_invent_sl(self):
        with self.assertRaises(FrozenExecConflict) as cm:
            require_frozen_exec_params(
                _version(_ema99_params()),
                {"sl_pct": 0.03},
                context="eval",
            )
        self.assertIn("禁止请求体补旧默认", str(cm.exception))

    def test_pct_touch_still_requires_tp_sl_hold(self):
        with self.assertRaises(FrozenExecConflict):
            require_frozen_exec_params(
                _version({"execution_mode": "pct_touch", "side": "long"}),
                {},
                context="eval",
            )
        out = require_frozen_exec_params(_version(_pct_params()), {}, context="eval")
        self.assertEqual(out["execution_mode"], "pct_touch")
        self.assertEqual(out["tp_pct"], 0.05)
        self.assertEqual(out["sl_pct"], 0.03)


class EvalStartModeBranchTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        (self.root / "scan.json").write_text(
            json.dumps({
                "scan_id": "s", "revision": "r", "total": 1, "received": 1,
                "rows": [{
                    "symbol": "BTCUSDT", "market": "USDⓈ-M",
                    "price": 100.0, "markPrice": 100.0, "fundingAprPct": 1.0,
                }],
            }),
            encoding="utf-8",
        )
        self.strategies = StrategyStore(self.root)
        self.sim = SimStore(self.root)

        draft = self.strategies.create_draft({
            "strategy_id": "S-ema99-ui03",
            "title": "EMA三线金叉",
            "data_dependencies": ["snapshot"],
            "rules": {
                "entry_text": "ema_squeeze_confirmed",
                "exit_text": "ema99_close_trail",
                "base_template_id": "T2",
                "params": _ema99_params(),
            },
        })
        self.ver = self.strategies.freeze(draft["draft_id"], {"version": "4.2.0-ui03"})
        path = self.strategies._version_path(self.ver["version_id"])
        raw = json.loads(path.read_text(encoding="utf-8"))
        raw["enabled"] = True
        path.write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")
        self.ver = self.strategies.get_version(self.ver["version_id"])

        draft2 = self.strategies.create_draft({
            "strategy_id": "S-pct-ui03",
            "title": "旧触价",
            "data_dependencies": ["snapshot"],
            "rules": {
                "entry_text": "e", "exit_text": "x",
                "base_template_id": "T1",
                "params": _pct_params(),
            },
        })
        self.ver_pct = self.strategies.freeze(draft2["draft_id"], {"version": "1.0.0"})
        path2 = self.strategies._version_path(self.ver_pct["version_id"])
        raw2 = json.loads(path2.read_text(encoding="utf-8"))
        raw2["enabled"] = True
        path2.write_text(json.dumps(raw2, ensure_ascii=False), encoding="utf-8")
        self.ver_pct = self.strategies.get_version(self.ver_pct["version_id"])

        self.eval = EvalStore(self.root, strategy_store=self.strategies, sim_store=self.sim)
        import coverage_gate as _cg
        self._orig = getattr(_cg, "assert_autofill_symbol_gate", None)
        _cg.assert_autofill_symbol_gate = lambda *a, **k: {"ok": True, "disclaimer": "test-bypass"}

    def tearDown(self):
        import coverage_gate as _cg
        if self._orig:
            _cg.assert_autofill_symbol_gate = self._orig
        self.tmp.cleanup()

    def test_ema99_reject_missing_confirm(self):
        with self.assertRaises(EvalConflict) as cm:
            self.eval.start_observation({
                "strategy_version_id": self.ver["version_id"],
                "symbol": "BTCUSDT",
                "entry_price": 100.0,
                "cross_ema99": 95.0,
                "observation_horizon_days": 30,
            })
        self.assertIn("确认事件", str(cm.exception))

    def test_ema99_reject_missing_cross_ema99(self):
        with self.assertRaises(EvalConflict) as cm:
            self.eval.start_observation({
                "strategy_version_id": self.ver["version_id"],
                "symbol": "BTCUSDT",
                "entry_price": 100.0,
                "confirm_event_id": "cfm-test-1",
                "observation_horizon_days": 30,
            })
        self.assertIn("cross_ema99", str(cm.exception))

    def test_ema99_start_ok_no_invented_tp_sl(self):
        obs = self.eval.start_observation({
            "strategy_version_id": self.ver["version_id"],
            "symbol": "BTCUSDT",
            "entry_price": 100.0,
            "confirm_event_id": "cfm-test-2",
            "cross_ema99": 95.5,
            "observation_horizon_days": 30,
        })
        self.assertEqual(obs.get("execution_mode"), "ema99_close_trail")
        self.assertIsNone(obs.get("tp_pct"))
        self.assertIsNone(obs.get("sl_pct"))
        self.assertIsNone(obs.get("max_hold_days"))
        self.assertIsNone(obs.get("tp_price"))
        self.assertAlmostEqual(float(obs["initial_stop"]), 95.5, places=4)
        self.assertAlmostEqual(float(obs["current_stop"]), 95.5, places=4)
        self.assertIsNotNone(obs.get("trail_state"))
        self.assertEqual(obs.get("confirm_event_id"), "cfm-test-2")
        # must not look like default 3% stop
        self.assertNotAlmostEqual(float(obs["sl_price"] or obs["initial_stop"]), 97.0, places=2)

    def test_settle_routes_by_mode(self):
        obs = self.eval.start_observation({
            "strategy_version_id": self.ver["version_id"],
            "symbol": "BTCUSDT",
            "entry_price": 100.0,
            "confirm_event_id": "cfm-settle",
            "cross_ema99": 95.0,
            "observation_horizon_days": 30,
        })
        # synthetic bars: close never breaks stop → pending / open, not pct_touch tp
        start_ms = 1_700_000_000_000
        bars = []
        for i in range(5):
            t = start_ms + i * 14_400_000
            bars.append([t, 100, 101, 99, 100.5])  # o h l c
        # force started_at alignment via direct settle on loaded obs
        loaded = None
        for row in self.eval._read_jsonl(self.eval.observations_path):
            if row.get("observation_id") == obs["observation_id"]:
                loaded = row
                break
        self.assertIsNotNone(loaded)
        # rewrite started_at so bars fall after start
        loaded["started_at"] = "2023-11-14T22:00:00+00:00"
        # use _settle_rules routing
        result = self.eval._settle_rules(loaded, {
            "bars": [[start_ms + i * 14_400_000, 100, 102, 99, 100.5] for i in range(3)],
            "ema99_by_time": {str(start_ms + i * 14_400_000): 96.0 for i in range(3)},
            "as_of": "2023-11-15T12:00:00+00:00",
        })
        # ema99 path: should not invent pct_touch tp hit
        detail = result.get("detail") or {}
        self.assertNotEqual(result.get("exit_reason"), "tp")
        # mode preserved in obs path
        self.assertEqual(loaded.get("execution_mode"), "ema99_close_trail")

    def test_pct_touch_still_validates(self):
        obs = self.eval.start_observation({
            "strategy_version_id": self.ver_pct["version_id"],
            "symbol": "BTCUSDT",
            "entry_price": 100.0,
            "observation_horizon_days": 30,
        })
        self.assertEqual(obs.get("execution_mode") or "pct_touch", "pct_touch")
        self.assertIsNotNone(obs.get("tp_price"))
        self.assertIsNotNone(obs.get("sl_price"))
        self.assertEqual(obs.get("max_hold_days"), 42)


class FeStaticUi03Tests(unittest.TestCase):
    def test_eval_runtime_mode_gate(self):
        t = Path("design/eval-runtime.js").read_text(encoding="utf-8")
        self.assertIn("executionModeFromParams", t)
        self.assertIn("ema99_close_trail", t)
        self.assertIn("evalConfirmId", t)
        self.assertIn("evalCrossEma99", t)
        # pct_touch still checks tp/sl/hold; ema99 must not require them
        self.assertIn("pct_touch 冻结版缺少止盈/止损/持仓天数", t)
        self.assertNotIn(
            "if (!p.tp_pct || !p.sl_pct || p.max_hold_days == null)",
            t,
        )
        self.assertIn("trailing_activated", t)
        self.assertIn("pending_exit", t)
        self.assertIn("不向 body 塞 tp_pct", t)

    def test_sim_runtime_mode_note(self):
        t = Path("design/sim-runtime.js").read_text(encoding="utf-8")
        self.assertIn("executionModeFromParams", t)
        self.assertIn("ema99_close_trail", t)
        self.assertIn("无固定tp/sl", t)


if __name__ == "__main__":
    unittest.main()
