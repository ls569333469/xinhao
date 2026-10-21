"""V1 frozen-exec: draft mutate after freeze must not affect paper/eval; no silent defaults."""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from strategy_store import StrategyStore
from sim_store import SimStore, Conflict as SimConflict
from eval_store import EvalStore, EvalConflict
from frozen_exec import require_frozen_exec_params, FrozenExecConflict


class FrozenExecTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        (self.root / 'scan.json').write_text(json.dumps({
            'scan_id': 's', 'revision': 'r', 'total': 1, 'received': 1,
            'rows': [{'symbol': 'BTCUSDT', 'market': 'USDⓈ-M', 'price': 100.0, 'markPrice': 100.0,
                       'fundingAprPct': 1.0}],
        }), encoding='utf-8')
        self.strategies = StrategyStore(self.root)
        self.sim = SimStore(self.root)
        draft = self.strategies.create_draft({
            'strategy_id': 'S-froz',
            'title': '冻结执行',
            'data_dependencies': ['snapshot', 'funding'],
            'rules': {
                'entry_text': 'e', 'exit_text': 'x',
                'base_template_id': 'T1',
                'params': {
                    'tp_pct': 0.08, 'sl_pct': 0.04, 'max_hold_days': 5,
                    'notional_u': 80.0, 'side': 'long',
                    'entry_rule': 'close_above_prev_high',
                    'max_hold_bars': 30,
                },
                'whitelist_trigger': {'mode': 'unset', 'symbols': [], 'rules_text': '', 'note': ''},
            },
        })
        self.draft_id = draft['draft_id']
        self.ver = self.strategies.freeze(draft['draft_id'], {'version': '1.0.0'})
        # enable may refuse fingerprint drift — ack if needed
        pub = self.strategies.set_enabled(self.ver['version_id'], True, body={'acknowledge_coverage_drift': True})
        if pub.get('enable_refused'):
            # deps/coverage — still ok for unit tests of param binding if we force enabled in file
            path = self.strategies._version_path(self.ver['version_id'])
            raw = json.loads(path.read_text(encoding='utf-8'))
            raw['enabled'] = True
            path.write_text(json.dumps(raw, ensure_ascii=False), encoding='utf-8')
        self.ver = self.strategies.get_version(self.ver['version_id'])
        self.eval = EvalStore(self.root, strategy_store=self.strategies, sim_store=self.sim)
        # Isolate frozen-param tests from DATA candle warmup (covered elsewhere)
        import coverage_gate as _cg
        self._orig_af_gate = _cg.assert_autofill_symbol_gate
        _cg.assert_autofill_symbol_gate = lambda *a, **k: {'ok': True, 'disclaimer': 'test-bypass-warmup'}

    def tearDown(self):
        import coverage_gate as _cg
        if getattr(self, '_orig_af_gate', None):
            _cg.assert_autofill_symbol_gate = self._orig_af_gate
        self.tmp.cleanup()

    def test_mutate_draft_after_freeze_eval_still_uses_frozen(self):
        # Tamper draft params after freeze
        self.strategies.update_draft(self.draft_id, {
            'rules': {
                'entry_text': 'MUTATED',
                'params': {'tp_pct': 0.99, 'sl_pct': 0.01, 'max_hold_days': 99, 'notional_u': 1.0, 'side': 'short'},
            },
        })
        obs = self.eval.start_observation({
            'strategy_version_id': self.ver['version_id'],
            'symbol': 'BTCUSDT',
            'entry_price': 100.0,
            'start_at': '2026-10-01T00:00:00+00:00',
        })
        self.assertAlmostEqual(obs['tp_pct'], 0.08)
        self.assertAlmostEqual(obs['sl_pct'], 0.04)
        self.assertEqual(obs['max_hold_days'], 5)
        self.assertEqual(obs['side'], 'long')
        self.assertAlmostEqual(obs['tp_price'], 108.0)
        self.assertEqual(obs['frozen_exec']['params_source'], 'frozen_version.rules.params')

    def test_autofill_uses_frozen_notional_not_draft(self):
        self.strategies.update_draft(self.draft_id, {
            'rules': {'params': {'tp_pct': 0.5, 'sl_pct': 0.5, 'notional_u': 999.0, 'side': 'short'}},
        })
        plan = self.sim.plan_autofill({
            'symbol': 'BTCUSDT',
            'strategy_version_id': self.ver['version_id'],
            'trigger_evidence': {'signal_id': 'sig-1'},
        }, strategy_store=self.strategies)
        self.assertEqual(plan['frozen_exec']['notional_u'], 80.0)
        self.assertAlmostEqual(plan['notional_u'], 80.0)
        self.assertEqual(plan['side'], 'long')

    def test_refuse_body_override_divergent_tp(self):
        with self.assertRaises(EvalConflict) as cm:
            self.eval.start_observation({
                'strategy_version_id': self.ver['version_id'],
                'symbol': 'BTCUSDT',
                'entry_price': 100.0,
                'tp_pct': 0.50,  # diverge from frozen 0.08
            })
        self.assertIn('不一致', str(cm.exception))

    def test_refuse_missing_params_no_silent_default(self):
        draft = self.strategies.create_draft({
            'strategy_id': 'S-empty',
            'title': '无参数',
            'data_dependencies': ['snapshot', 'funding'],
            'rules': {'entry_text': 'e', 'exit_text': 'x', 'params': {}},
        })
        # empty params dict may be stored as {} — freeze then require should fail
        ver = self.strategies.freeze(draft['draft_id'], {'version': '0.0.1'})
        with self.assertRaises(FrozenExecConflict) as cm:
            require_frozen_exec_params(ver, {}, context='eval')
        self.assertIn('params', str(cm.exception).lower() + str(cm.exception))

    def test_refuse_silent_entry_rule_substitute(self):
        with self.assertRaises(SimConflict) as cm:
            self.sim.plan_autofill({
                'symbol': 'BTCUSDT',
                'strategy_version_id': self.ver['version_id'],
                'trigger_evidence': {'signal_id': 'sig-2'},
                'entry_rule': 'breakout',  # not frozen value
            }, strategy_store=self.strategies)
        msg = str(cm.exception)
        self.assertTrue('不一致' in msg or 'F04' in msg or 'entry_rule' in msg)


if __name__ == '__main__':
    unittest.main()
