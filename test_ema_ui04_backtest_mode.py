# -*- coding: utf-8 -*-
"""UI04 offline: backtest must not submit old substitute rules for ema99 frozen versions."""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from frozen_exec import require_frozen_exec_params, FrozenExecConflict
from backtest_store import BacktestStore
from strategy_store import StrategyStore


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
        "oi_exit_ratio": 0.70,
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
        "max_hold_bars": 42,
        "side": "long",
        "entry_rule": "close_above_prev_high",
        "notional_u": 100.0,
    }
    p.update(extra)
    return p


class Ui04FrozenBacktestTests(unittest.TestCase):
    def test_ema99_refuse_body_old_tp_sl_entry(self):
        ver = {
            "version_id": "ver-x",
            "content_hash": "h",
            "rules": {"params": _ema99_params(), "base_template_id": "T2"},
        }
        with self.assertRaises(FrozenExecConflict):
            require_frozen_exec_params(
                ver,
                {
                    "entry_rule": "close_above_prev_high",
                    "tp_pct": 0.05,
                    "sl_pct": 0.03,
                    "max_hold_bars": 42,
                },
                context="backtest",
            )

    def test_ema99_ok_without_old_fields(self):
        ver = {
            "version_id": "ver-x",
            "content_hash": "h",
            "rules": {"params": _ema99_params(), "base_template_id": "T2"},
        }
        out = require_frozen_exec_params(ver, {}, context="backtest")
        self.assertEqual(out["execution_mode"], "ema99_close_trail")
        self.assertIsNone(out.get("tp_pct"))
        self.assertIsNone(out.get("sl_pct"))
        self.assertEqual(out.get("entry_rule"), "ema_squeeze_confirmed")


class Ui04BacktestStoreTests(unittest.TestCase):
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
        draft = self.strategies.create_draft({
            "strategy_id": "S-ema99-ui04",
            "title": "EMA三线金叉",
            "data_dependencies": ["snapshot"],
            "rules": {
                "entry_text": "ema_squeeze_confirmed",
                "exit_text": "ema99_close_trail",
                "base_template_id": "T2",
                "params": _ema99_params(),
            },
        })
        self.ver = self.strategies.freeze(draft["draft_id"], {"version": "4.2.0-ui04"})
        path = self.strategies._version_path(self.ver["version_id"])
        raw = json.loads(path.read_text(encoding="utf-8"))
        raw["enabled"] = True
        path.write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")
        self.ver = self.strategies.get_version(self.ver["version_id"])

        draft2 = self.strategies.create_draft({
            "strategy_id": "S-pct-ui04",
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

        self.bt = BacktestStore(self.root, strategy_store=self.strategies)

    def tearDown(self):
        self.tmp.cleanup()

    def _bars(self, n=80):
        start = 1_700_000_000_000
        return [[start + i * 14_400_000, 100, 101, 99, 100.5] for i in range(n)]

    def test_full_strategy_missing_evidence_insufficient(self):
        rec = self.bt.run({
            "strategy_version_id": self.ver["version_id"],
            "symbol": "BTCUSDT",
            "interval": "4h",
            "bars": self._bars(),
            "backtest_scope": "full_strategy",
            # intentionally no historical_oi / mcap / account
        })
        self.assertEqual(rec["status"], "insufficient")
        self.assertEqual(rec.get("execution_mode"), "ema99_close_trail")
        self.assertEqual(rec.get("backtest_scope"), "full_strategy")
        self.assertTrue(rec.get("not_full_strategy_pnl"))
        self.assertIn("historical_oi", rec.get("missing_evidence") or [])
        self.assertIn("完整策略", rec.get("status_note") or "")
        # must not look like completed pct_touch with invented rules
        self.assertNotEqual(rec.get("status"), "completed")

    def test_tech_slice_named_not_full_pnl(self):
        rec = self.bt.run({
            "strategy_version_id": self.ver["version_id"],
            "symbol": "BTCUSDT",
            "interval": "4h",
            "bars": self._bars(),
            "backtest_scope": "ema_tech_slice",
            "experiment_kind": "ema_tech_slice",
        })
        self.assertIn(rec["status"], ("unimplemented", "insufficient"))
        self.assertEqual(rec.get("backtest_scope"), "ema_tech_slice")
        self.assertEqual(rec.get("experiment_kind"), "ema_tech_slice")
        self.assertTrue(rec.get("not_full_strategy_pnl"))
        self.assertIn("技术切片", rec.get("status_note") or "")
        self.assertIn("非完整策略", rec.get("status_note") or "")

    def test_old_inject_still_rejected_by_frozen(self):
        with self.assertRaises(ValueError) as cm:
            self.bt.run({
                "strategy_version_id": self.ver["version_id"],
                "symbol": "BTCUSDT",
                "interval": "4h",
                "bars": self._bars(),
                "entry_rule": "close_above_prev_high",
                "tp_pct": 0.05,
                "sl_pct": 0.03,
                "max_hold_bars": 42,
                "allow_generic_price_rule": True,
            })
        msg = str(cm.exception)
        # conflict message from frozen_exec
        self.assertTrue(
            "不一致" in msg or "禁止" in msg or "entry_rule" in msg or "tp_pct" in msg or "sl_pct" in msg,
            msg,
        )

    def test_compare_batch_no_shared_old_defaults(self):
        # FE-style: no tp/sl/entry in compare body
        cmp_rec = self.bt.compare({
            "strategy_version_ids": [self.ver["version_id"], self.ver_pct["version_id"]],
            "symbols": ["BTCUSDT"],
            "interval": "4h",
            "bars_by_symbol": {"BTCUSDT": self._bars()},
            "backtest_scope": "full_strategy",
            "split": False,
        })
        table = cmp_rec.get("table") or {}
        cells = table.get("cells") or cmp_rec.get("cells") or []
        if not cells and isinstance(table, list):
            cells = table
        # flatten matrix rows if needed
        if not cells and isinstance(table, dict):
            for v in table.values():
                if isinstance(v, list) and v and isinstance(v[0], dict) and "status" in v[0]:
                    cells = v
                    break
        self.assertGreaterEqual(len(cells), 1, msg=str(cmp_rec.get("summary")))
        # summary already proves ema path counted as insufficient
        summary = cmp_rec.get("summary") or {}
        self.assertGreaterEqual(int(summary.get("insufficient") or 0), 1)
        self.assertEqual(int(summary.get("cells_total") or 0), 2)
        ema_cells = [c for c in cells if c.get("strategy_version_id") == self.ver["version_id"]]
        if ema_cells:
            self.assertIn(ema_cells[0].get("status"), ("insufficient", "rejected", "unimplemented"))


class Ui04FeStaticTests(unittest.TestCase):
    def test_backtest_runtime_no_default_inject(self):
        t = Path("design/backtest-runtime.js").read_text(encoding="utf-8")
        self.assertIn("buildRunBodyFromFrozen", t)
        self.assertIn("executionModeFromParams", t)
        self.assertIn("ema_tech_slice", t)
        self.assertIn("btFrozenNote", t)
        self.assertNotIn("allow_generic_price_rule: true", t)
        # no UI default 5/3/42 submit
        self.assertNotIn("|| 5) / 100", t)
        self.assertNotIn("|| 3) / 100", t)
        self.assertNotIn("|| 42)", t)
        self.assertNotIn("close_above_prev_high',", t)
        self.assertIn("不传 allow_generic_price_rule", t)


if __name__ == "__main__":
    unittest.main()
