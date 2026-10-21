# -*- coding: utf-8 -*-
"""UI02 offline: T2 save/load/freeze keeps ema99_close_trail fields, not 3 old T1 params."""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from strategy_store import StrategyStore


def t2_ema99_payload():
    params = {
        "execution_mode": "ema99_close_trail",
        "entry_rule": "ema_squeeze_confirmed",
        "candidate_max_days": 30,
        "oi_exit_ratio": 0.70,
        "vol_confirm_ratio": 2.0,
        "volume_confirm_window": "next_bar_only",
        "initial_stop": "cross_bar_ema99",
        "trail_activation": "first_close_above_entry",
        "intrabar_stop": False,
        "side": "long",
        "params_schema_version": "ema99-close-trail-v1",
        "user_rules_version": "ema-squeeze-v4",
        "sim_engine": "未完成",
        "eval_engine": "未完成",
    }
    return {
        "title": "EMA三线金叉",
        "strategy_id": "S-T2",
        "rules": {
            "base_template_id": "T2",
            "editor_mode": "t2_ema99",
            "registry_version": "1.0.1-ema-squeeze-v4",
            "registry_hash": "deadbeef",
            "idea": "指导草案：①②④入池后等确认；executable=false",
            "entry_text": "entry_rule=ema_squeeze_confirmed",
            "exit_text": "initial_stop=cross_bar_ema99",
            "implementation_status": {
                "executable": False,
                "unfinished": ["sim_engine", "eval_engine", "完整结构K线"],
            },
            "params": params,
        },
        "data_dependencies": ["price_4h", "snapshot"],
    }


class Ui02T2RoundTripTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        from test_price_index import write_scan_candles, bar, START, CONTRACT
        (self.root / "scan.json").write_text(json.dumps({
            "rows": [{**CONTRACT, "price": 100, "oiUsd": 1000}],
            "total": 1,
            "received": 1,
        }), encoding="utf-8")
        write_scan_candles(
            self.root, "scan-" + "f" * 32, CONTRACT, "4h",
            [bar(START + i * 14400000) for i in range(20)],
        )
        self.store = StrategyStore(self.root)

    def tearDown(self):
        self.tmp.cleanup()

    def test_save_load_keeps_ema99_not_t1_triple(self):
        created = self.store.create_draft(t2_ema99_payload())
        self.assertFalse(created["executable"])
        loaded = self.store.get_draft(created["draft_id"])
        params = loaded["rules"]["params"]
        self.assertEqual(params.get("execution_mode"), "ema99_close_trail")
        self.assertEqual(params.get("entry_rule"), "ema_squeeze_confirmed")
        self.assertEqual(params.get("candidate_max_days"), 30)
        self.assertEqual(params.get("initial_stop"), "cross_bar_ema99")
        self.assertEqual(params.get("trail_activation"), "first_close_above_entry")
        self.assertEqual(params.get("volume_confirm_window"), "next_bar_only")
        self.assertEqual(params.get("oi_exit_ratio"), 0.70)
        self.assertEqual(params.get("vol_confirm_ratio"), 2.0)
        self.assertIs(params.get("intrabar_stop"), False)
        for k in ("oiCapPct", "accountRatio", "fundingAprPct"):
            self.assertNotIn(k, params)
        self.assertEqual(loaded["rules"].get("base_template_id"), "T2")
        self.assertEqual(loaded["rules"].get("editor_mode"), "t2_ema99")
        self.assertEqual(loaded["title"], "EMA三线金叉")
        st = loaded["rules"].get("implementation_status") or {}
        self.assertIs(st.get("executable"), False)

    def test_update_then_freeze_preserves_structured_params(self):
        created = self.store.create_draft(t2_ema99_payload())
        body = t2_ema99_payload()
        body["rules"]["params"]["vol_confirm_ratio"] = 2.5
        body["rules"]["idea"] = "更新后的想法"
        updated = self.store.update_draft(created["draft_id"], {
            "title": body["title"],
            "rules": body["rules"],
            "data_dependencies": ["price_4h", "snapshot"],
        })
        self.assertEqual(updated["rules"]["params"]["vol_confirm_ratio"], 2.5)
        frozen = self.store.freeze(created["draft_id"], {"version": "0.1.0-ui02", "change_note": "UI02首冻"})
        self.assertTrue(frozen["immutable"])
        self.assertFalse(frozen["executable"])
        fp = frozen["rules"]["params"]
        self.assertEqual(fp["execution_mode"], "ema99_close_trail")
        self.assertEqual(fp["entry_rule"], "ema_squeeze_confirmed")
        self.assertEqual(fp["vol_confirm_ratio"], 2.5)
        self.assertEqual(frozen["rules"]["base_template_id"], "T2")
        fork = self.store.create_draft_from_version(frozen["version_id"], {"title": "EMA三线金叉·改"})
        self.assertNotEqual(fork["draft_id"], created["draft_id"])
        self.assertEqual(fork["rules"]["params"]["execution_mode"], "ema99_close_trail")
        again = self.store.get_version(frozen["version_id"])
        self.assertEqual(again["rules"]["params"]["vol_confirm_ratio"], 2.5)

    def test_t1_legacy_still_three_params(self):
        d = self.store.create_draft({
            "title": "空头拥挤突破",
            "strategy_id": "S-T1",
            "rules": {
                "base_template_id": "T1",
                "editor_mode": "t1_generic",
                "params": {"oiCapPct": 30, "accountRatio": 0.8, "fundingAprPct": -50},
                "entry_text": "上穿",
                "exit_text": "止损",
            },
            "data_dependencies": ["price_4h", "snapshot"],
        })
        p = d["rules"]["params"]
        self.assertEqual(p.get("oiCapPct"), 30)
        self.assertNotEqual(p.get("execution_mode"), "ema99_close_trail")


if __name__ == "__main__":
    unittest.main()