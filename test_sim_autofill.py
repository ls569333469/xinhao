"""Offline SIM-01 autofill tests: preview, confirm fill, whitelist, same-symbol, gates."""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from sim_store import Conflict, SimStore
from strategy_store import StrategyStore


class SimAutofillTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        scan = {
            'scan_id': 'scan-af',
            'rows': [
                {'symbol': 'BTCUSDT', 'market': 'USDⓈ-M', 'price': 100.0, 'markPrice': 100.0},
                {'symbol': 'ETHUSDT', 'market': 'USDⓈ-M', 'price': 50.0, 'markPrice': 50.0},
                {'symbol': 'SOLUSDT', 'market': 'USDⓈ-M', 'price': 20.0, 'markPrice': 20.0},
            ],
        }
        (self.root / 'scan.json').write_text(json.dumps(scan), encoding='utf-8')
        self.store = SimStore(self.root)
        self.strategies = StrategyStore(self.root)
        draft = self.strategies.create_draft({
            'strategy_id': 'S-af',
            'title': '自动填仓测',
            'data_dependencies': ['snapshot'],
            'rules': {
                'entry_text': '确认信号',
                'exit_text': '止损',
                'base_template_id': 'T1',
                'tier': 'T1',
                'params': {
                    'tp_pct': 0.10,
                    'sl_pct': 0.05,
                    'max_hold_days': 7,
                    'side': 'long',
                    'notional_u': 50.0,
                    'entry_rule': 'close_above_prev_high',
                },
                'whitelist_trigger': {
                    'mode': 'allow',
                    'symbols': ['BTCUSDT', 'ETHUSDT'],
                    'rules_text': '',
                    'note': '测试白名单',
                },
            },
        })
        self.ver = self.strategies.freeze(draft['draft_id'], {'version': '0.1.0'})
        # F03: autofill requires enabled
        self.strategies.set_enabled(self.ver['version_id'], True)
        self.ver = self.strategies.get_version(self.ver['version_id'])
        self.trigger = {'signal_id': 'sig-test-1', 'kind': 'manual_confirm'}
        import coverage_gate as _cg
        self._orig_af_gate = _cg.assert_autofill_symbol_gate
        _cg.assert_autofill_symbol_gate = lambda *a, **k: {'ok': True, 'disclaimer': 'test-bypass-warmup'}

    def tearDown(self):
        import coverage_gate as _cg
        if getattr(self, '_orig_af_gate', None):
            _cg.assert_autofill_symbol_gate = self._orig_af_gate
        self.tmp.cleanup()

    def test_preview_does_not_write(self):
        before = self.store.list_events(limit=10)['events']
        plan = self.store.plan_autofill({
            'symbol': 'BTCUSDT',
            'side': 'long',
            'strategy_version_id': self.ver['version_id'],
            'trigger_evidence': self.trigger,
        }, strategy_store=self.strategies)
        self.assertTrue(plan['dry_run'])
        self.assertFalse(plan['will_write'])
        self.assertFalse(plan['inventory_fiction'])
        self.assertGreater(plan['notional_u'], 0)
        self.assertTrue(plan['planned_event_id'].startswith('evt-'))
        after = self.store.list_events(limit=10)['events']
        self.assertEqual(len(before), len(after))
        acc = self.store.get_account('acc-shared')
        self.assertEqual(acc['cash'], 1000.0)
        self.assertEqual(acc['position_count'], 0)

    def test_autofill_requires_confirm(self):
        with self.assertRaises(ValueError):
            self.store.autofill({
                'symbol': 'BTCUSDT',
                'side': 'long',
                'strategy_version_id': self.ver['version_id'],
                'trigger_evidence': self.trigger,
            }, strategy_store=self.strategies)

    def test_autofill_writes_real_open(self):
        out = self.store.autofill({
            'symbol': 'BTCUSDT',
            'side': 'long',
            'strategy_version_id': self.ver['version_id'],
            'confirm': True,
            'event_id': 'evt-af-1',
            'trigger_evidence': self.trigger,
        }, strategy_store=self.strategies)
        self.assertEqual(out['event']['type'], 'open')
        self.assertTrue(out['autofill']['plan']['notional_u'] > 0)
        acc = self.store.get_account('acc-shared')
        self.assertEqual(acc['position_count'], 1)
        self.assertLess(acc['cash'], 1000.0)
        self.assertTrue(acc['conservation_ok'])

    def test_whitelist_blocks_non_allow(self):
        with self.assertRaises(Conflict):
            self.store.plan_autofill({
                'symbol': 'SOLUSDT',
                'side': 'long',
                'strategy_version_id': self.ver['version_id'],
                'trigger_evidence': self.trigger,
            }, strategy_store=self.strategies)

    def test_same_symbol_reject_on_second_fill(self):
        self.store.autofill({
            'symbol': 'ETHUSDT',
            'side': 'long',
            'strategy_version_id': self.ver['version_id'],
            'confirm': True,
            'event_id': 'evt-af-eth1',
            'trigger_evidence': self.trigger,
        }, strategy_store=self.strategies)
        plan = self.store.plan_autofill({
            'symbol': 'ETHUSDT',
            'side': 'long',
            'strategy_version_id': self.ver['version_id'],
            'trigger_evidence': self.trigger,
        }, strategy_store=self.strategies)
        self.assertTrue(plan['same_symbol_blocked'])
        with self.assertRaises(Conflict):
            self.store.autofill({
                'symbol': 'ETHUSDT',
                'side': 'long',
                'strategy_version_id': self.ver['version_id'],
                'confirm': True,
                'event_id': 'evt-af-eth2',
                'trigger_evidence': self.trigger,
            }, strategy_store=self.strategies)

    def test_f03_disabled_strategy_cannot_autofill(self):
        self.strategies.set_enabled(self.ver['version_id'], False)
        with self.assertRaises(Conflict) as ctx:
            self.store.autofill({
                'symbol': 'BTCUSDT',
                'side': 'long',
                'strategy_version_id': self.ver['version_id'],
                'confirm': True,
                'trigger_evidence': self.trigger,
            }, strategy_store=self.strategies)
        self.assertIn('未启用', str(ctx.exception))
        acc = self.store.get_account('acc-shared')
        self.assertEqual(acc['position_count'], 0)

    def test_f03_preview_confirm_stable_idempotency(self):
        body = {
            'symbol': 'BTCUSDT',
            'side': 'long',
            'strategy_version_id': self.ver['version_id'],
            'trigger_evidence': {'signal_id': 'sig-stable', 'kind': 'manual_confirm'},
            'mark': 100.0,
        }
        plan1 = self.store.plan_autofill(body, strategy_store=self.strategies)
        plan2 = self.store.plan_autofill(body, strategy_store=self.strategies)
        self.assertEqual(plan1['planned_event_id'], plan2['planned_event_id'])
        out = self.store.autofill({**body, 'confirm': True}, strategy_store=self.strategies)
        self.assertEqual(out['event']['event_id'], plan1['planned_event_id'])
        # Repeat confirm with same key → duplicate rejected
        with self.assertRaises(Conflict):
            self.store.autofill({**body, 'confirm': True}, strategy_store=self.strategies)

    def test_f03_missing_trigger_rejected(self):
        with self.assertRaises(Conflict) as ctx:
            self.store.plan_autofill({
                'symbol': 'BTCUSDT',
                'side': 'long',
                'strategy_version_id': self.ver['version_id'],
            }, strategy_store=self.strategies)
        self.assertIn('触发', str(ctx.exception))



    def test_autofill_tier_resolved_from_template(self):
        plan = self.store.plan_autofill({
            'symbol': 'BTCUSDT',
            'side': 'long',
            'strategy_version_id': self.ver['version_id'],
            'trigger_evidence': self.trigger,
            'notional_u': 50,
        }, strategy_store=self.strategies)
        self.assertEqual(plan['strategy_tier'], 'T1')
        self.assertTrue(plan['tier_budget']['ok'])

    def test_autofill_tier_budget_over_rejects(self):
        self.store.save_config({'budget_t1_u': 80.0, 'fee_rate': 0.0})
        # First fill uses 50 margin
        self.store.autofill({
            'symbol': 'BTCUSDT',
            'side': 'long',
            'strategy_version_id': self.ver['version_id'],
            'confirm': True,
            'event_id': 'evt-af-tier1',
            'trigger_evidence': self.trigger,
            'notional_u': 50,
            'leverage': 1,
        }, strategy_store=self.strategies)
        with self.assertRaises(Conflict) as ctx:
            # Body notional must match freeze (50); 50+50 > budget 80 → 超限
            self.store.plan_autofill({
                'symbol': 'ETHUSDT',
                'side': 'long',
                'strategy_version_id': self.ver['version_id'],
                'trigger_evidence': {'signal_id': 'sig-2'},
                'notional_u': 50,
                'leverage': 1,
            }, strategy_store=self.strategies)
        self.assertIn('超限', str(ctx.exception))
        self.assertIn('T1', str(ctx.exception))

    def test_autofill_missing_tier_rejects(self):
        # Version without tier metadata
        draft = self.strategies.create_draft({
            'strategy_id': 'S-notier',
            'title': '无档位',
            'data_dependencies': ['snapshot'],
            'rules': {
                'entry_text': 'x',
                'exit_text': 'y',
                'params': {'tp_pct': 0.1, 'sl_pct': 0.05, 'side': 'long'},
                'whitelist_trigger': {'mode': 'unset', 'symbols': [], 'rules_text': '', 'note': ''},
            },
        })
        ver = self.strategies.freeze(draft['draft_id'], {'version': '0.0.1'})
        self.strategies.set_enabled(ver['version_id'], True)
        with self.assertRaises(Conflict) as ctx:
            self.store.plan_autofill({
                'symbol': 'BTCUSDT',
                'side': 'long',
                'strategy_version_id': ver['version_id'],
                'trigger_evidence': self.trigger,
            }, strategy_store=self.strategies)
        self.assertIn('档位', str(ctx.exception))



if __name__ == '__main__':
    unittest.main()
