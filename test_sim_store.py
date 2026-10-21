"""Offline SIM-01 tests: restart, duplicate, over-budget, conservation, same-symbol."""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from sim_store import Conflict, SimStore


class SimStoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        # Minimal scan.json so mark resolution works
        scan = {
            'scan_id': 'scan-test',
            'rows': [
                {'symbol': 'BTCUSDT', 'market': 'USDⓈ-M', 'price': 100.0, 'markPrice': 100.0},
                {'symbol': 'ETHUSDT', 'market': 'USDⓈ-M', 'price': 50.0, 'markPrice': 50.0},
            ],
        }
        (self.root / 'scan.json').write_text(json.dumps(scan), encoding='utf-8')
        self.store = SimStore(self.root)

    def tearDown(self):
        self.tmp.cleanup()

    def test_bootstrap_empty_ok(self):
        data = self.store.list_accounts()
        self.assertTrue(data['conservation_ok'])
        self.assertEqual(len(data['accounts']), 1)
        acc = data['accounts'][0]
        self.assertEqual(acc['account_id'], 'acc-shared')
        self.assertEqual(acc['cash'], 1000.0)
        self.assertEqual(acc['position_count'], 0)
        self.assertEqual(data['config']['same_symbol_policy'], 'reject')

    def test_open_close_conservation(self):
        out = self.store.open_position({
            'event_id': 'evt-open-1',
            'symbol': 'BTCUSDT',
            'side': 'long',
            'notional_u': 200,
            'leverage': 2,
        })
        acc = out['account']
        self.assertTrue(acc['conservation_ok'])
        self.assertEqual(acc['position_count'], 1)
        # margin = 200/2 = 100; fee = 200 * 0.0005 = 0.1
        self.assertAlmostEqual(acc['margin_locked'], 100.0, places=5)
        self.assertAlmostEqual(acc['fees_paid'], 0.1, places=5)
        self.assertAlmostEqual(acc['cash'], 1000 - 100 - 0.1, places=5)

        out2 = self.store.close_position({
            'event_id': 'evt-close-1',
            'symbol': 'BTCUSDT',
            'mark': 110.0,  # +10%
        })
        acc2 = out2['account']
        self.assertTrue(acc2['conservation_ok'])
        self.assertEqual(acc2['position_count'], 0)
        self.assertAlmostEqual(acc2['margin_locked'], 0.0, places=5)
        # qty = 200/100 = 2; gross = (110-100)*2 = 20; exit fee = 220*0.0005 = 0.11
        self.assertAlmostEqual(acc2['realized_pnl_gross'], 20.0, places=5)
        self.assertAlmostEqual(acc2['fees_paid'], 0.1 + 0.11, places=5)

    def test_duplicate_event_rejected(self):
        self.store.open_position({
            'event_id': 'evt-dup',
            'symbol': 'BTCUSDT',
            'notional_u': 50,
            'leverage': 1,
        })
        with self.assertRaises(Conflict) as ctx:
            self.store.open_position({
                'event_id': 'evt-dup',
                'symbol': 'ETHUSDT',
                'notional_u': 50,
                'leverage': 1,
            })
        self.assertIn('重复事件', str(ctx.exception))

    def test_over_budget_rejected(self):
        with self.assertRaises(Conflict) as ctx:
            self.store.open_position({
                'event_id': 'evt-ob',
                'symbol': 'BTCUSDT',
                'notional_u': 5000,
                'leverage': 1,
            })
        self.assertIn('预算不足', str(ctx.exception))
        acc = self.store.get_account('acc-shared')
        self.assertEqual(acc['position_count'], 0)
        self.assertEqual(acc['cash'], 1000.0)

    def test_same_symbol_conflict_reject(self):
        self.store.open_position({
            'event_id': 'evt-s1',
            'symbol': 'BTCUSDT',
            'notional_u': 100,
            'leverage': 1,
        })
        with self.assertRaises(Conflict) as ctx:
            self.store.open_position({
                'event_id': 'evt-s2',
                'symbol': 'BTCUSDT',
                'notional_u': 50,
                'leverage': 1,
            })
        self.assertIn('同币冲突', str(ctx.exception))

    def test_missing_price_clear_error(self):
        with self.assertRaises(ValueError) as ctx:
            self.store.open_position({
                'event_id': 'evt-np',
                'symbol': 'NOPEUSDT',
                'notional_u': 10,
                'leverage': 1,
            })
        self.assertIn('无法取价', str(ctx.exception) + str(ctx.exception.__cause__ or ''))
        msg = str(ctx.exception)
        self.assertTrue('无法取价' in msg or '无合约' in msg)

    def test_restart_restore(self):
        self.store.open_position({
            'event_id': 'evt-rs',
            'symbol': 'ETHUSDT',
            'notional_u': 100,
            'leverage': 2,
        })
        before = self.store.get_account('acc-shared')
        # New store instance = process restart
        store2 = SimStore(self.root)
        after = store2.reload()
        acc = [a for a in after['accounts'] if a['account_id'] == 'acc-shared'][0]
        self.assertEqual(acc['cash'], before['cash'])
        self.assertEqual(acc['margin_locked'], before['margin_locked'])
        self.assertEqual(acc['position_count'], 1)
        self.assertTrue(acc['conservation_ok'])
        events = store2.list_events(limit=10)
        self.assertTrue(any(e['event_id'] == 'evt-rs' for e in events['events']))
        # Duplicate still rejected after restart
        with self.assertRaises(Conflict):
            store2.open_position({
                'event_id': 'evt-rs',
                'symbol': 'BTCUSDT',
                'notional_u': 10,
                'leverage': 1,
            })

    def test_independent_account_contrast(self):
        a = self.store.ensure_independent_account('ver-aaaaaaaaaaaa', label='对照A')
        self.assertEqual(a['kind'], 'independent')
        self.assertEqual(a['cash'], 1000.0)
        out = self.store.open_position({
            'event_id': 'evt-ind',
            'account_id': a['account_id'],
            'symbol': 'BTCUSDT',
            'notional_u': 100,
            'leverage': 1,
            'strategy_version_id': 'ver-aaaaaaaaaaaa',
        })
        self.assertEqual(out['account']['account_id'], a['account_id'])
        shared = self.store.get_account('acc-shared')
        self.assertEqual(shared['cash'], 1000.0)
        self.assertEqual(shared['position_count'], 0)

    def test_config_persist(self):
        cfg = self.store.save_config({'fee_rate': 0.001, 'max_leverage': 2})
        self.assertEqual(cfg['fee_rate'], 0.001)
        store2 = SimStore(self.root)
        self.assertEqual(store2.get_config()['fee_rate'], 0.001)



    def test_f05_crash_after_event_before_state_replays(self):
        """Append event then delete/corrupt state → rebuild must restore position, not 1000U empty."""
        out = self.store.open_position({
            'event_id': 'evt-crash-open',
            'symbol': 'BTCUSDT',
            'side': 'long',
            'notional_u': 100,
            'leverage': 1,
            'mark': 100.0,
        })
        self.assertEqual(out['event']['type'], 'open')
        # Simulate crash: events kept, state wiped
        self.store.state_path.unlink()
        store2 = SimStore(self.root)
        acc = store2.get_account('acc-shared')
        self.assertEqual(acc['position_count'], 1)
        self.assertLess(acc['cash'], 1000.0)
        # Must not silently reset to pristine 1000U with orphan events
        self.assertNotEqual(acc['cash'], 1000.0)
        # Duplicate event still rejected (ledger intact)
        with self.assertRaises(Conflict):
            store2.open_position({
                'event_id': 'evt-crash-open',
                'symbol': 'ETHUSDT',
                'side': 'long',
                'notional_u': 50,
                'leverage': 1,
                'mark': 50.0,
            })

    def test_f05_event_count_mismatch_replays(self):
        self.store.open_position({
            'event_id': 'evt-mis-1',
            'symbol': 'BTCUSDT',
            'side': 'long',
            'notional_u': 100,
            'leverage': 1,
            'mark': 100.0,
        })
        state = self.store._load_state()
        # Stale state: pretend event_count behind ledger
        state['event_count'] = 0
        state['accounts']['acc-shared']['cash'] = 1000.0
        state['accounts']['acc-shared']['positions'] = {}
        state['accounts']['acc-shared']['margin_locked'] = 0.0
        state['accounts']['acc-shared']['fees_paid'] = 0.0
        state['accounts']['acc-shared']['realized_pnl_gross'] = 0.0
        self.store._save_state(state)
        # Force reconcile on new instance
        store2 = SimStore(self.root)
        acc = store2.get_account('acc-shared')
        self.assertEqual(acc['position_count'], 1)
        self.assertLess(acc['cash'], 1000.0)


class SimTierBudgetTests(unittest.TestCase):
    """SIM-01 T1/T2/T3 margin budget hard gate."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        scan = {
            'scan_id': 'scan-tier',
            'rows': [
                {'symbol': 'BTCUSDT', 'market': 'USDⓈ-M', 'price': 100.0, 'markPrice': 100.0},
                {'symbol': 'ETHUSDT', 'market': 'USDⓈ-M', 'price': 50.0, 'markPrice': 50.0},
                {'symbol': 'SOLUSDT', 'market': 'USDⓈ-M', 'price': 25.0, 'markPrice': 25.0},
            ],
        }
        (self.root / 'scan.json').write_text(json.dumps(scan), encoding='utf-8')
        self.store = SimStore(self.root)
        # Conservative defaults already 400/300/300; shrink T1 for easier over tests
        self.store.save_config({'budget_t1_u': 200.0, 'budget_t2_u': 150.0, 'budget_t3_u': 100.0, 'fee_rate': 0.0})

    def tearDown(self):
        self.tmp.cleanup()

    def test_config_tier_budgets_persist(self):
        cfg = self.store.get_config()
        self.assertEqual(cfg['budget_t1_u'], 200.0)
        self.assertEqual(cfg['tier_budgets']['T1'], 200.0)
        self.assertTrue(cfg['tier_budgets']['hard_gate'])
        # reload
        store2 = SimStore(self.root)
        self.assertEqual(store2.get_config()['budget_t2_u'], 150.0)

    def test_tier_budget_under_allows_open(self):
        # margin = notional/leverage = 100/1 = 100 < 200
        out = self.store.open_position({
            'event_id': 'evt-t1-under',
            'symbol': 'BTCUSDT',
            'notional_u': 100,
            'leverage': 1,
            'tier': 'T1',
        })
        self.assertEqual(out['event']['strategy_tier'], 'T1')
        self.assertTrue(out['tier_budget']['ok'])
        self.assertEqual(out['tier_budget']['tier_margin_used'], 0.0)
        self.assertEqual(out['account']['position_count'], 1)

    def test_tier_budget_at_limit_allows(self):
        # exactly 200 margin on T1
        out = self.store.open_position({
            'event_id': 'evt-t1-at',
            'symbol': 'BTCUSDT',
            'notional_u': 200,
            'leverage': 1,
            'tier': 'T1',
        })
        self.assertTrue(out['tier_budget']['ok'])
        tb = out['tier_budget']
        self.assertAlmostEqual(tb['tier_margin_projected'], 200.0, places=6)

    def test_tier_budget_over_rejects_with_reason(self):
        self.store.open_position({
            'event_id': 'evt-t1-a',
            'symbol': 'BTCUSDT',
            'notional_u': 150,
            'leverage': 1,
            'tier': 'T1',
        })
        with self.assertRaises(Conflict) as ctx:
            self.store.open_position({
                'event_id': 'evt-t1-over',
                'symbol': 'ETHUSDT',
                'notional_u': 60,  # margin 60 → used 150+60=210 > 200
                'leverage': 1,
                'tier': 'T1',
            })
        msg = str(ctx.exception)
        self.assertIn('T1', msg)
        self.assertIn('超限', msg)
        self.assertIn('budget_t1_u', msg)
        acc = self.store.get_account('acc-shared')
        self.assertEqual(acc['position_count'], 1)  # second rejected

    def test_tiers_independent_budgets(self):
        self.store.open_position({
            'event_id': 'evt-t1',
            'symbol': 'BTCUSDT',
            'notional_u': 200,
            'leverage': 1,
            'tier': 'T1',
        })
        # T2 has its own 150 budget; should accept
        out = self.store.open_position({
            'event_id': 'evt-t2',
            'symbol': 'ETHUSDT',
            'notional_u': 100,
            'leverage': 1,
            'tier': 'T2',
        })
        self.assertEqual(out['event']['strategy_tier'], 'T2')
        self.assertEqual(self.store.get_account('acc-shared')['position_count'], 2)

    def test_open_without_tier_skips_tier_gate(self):
        # legacy manual path: no tier → cash gate only
        out = self.store.open_position({
            'event_id': 'evt-notier',
            'symbol': 'BTCUSDT',
            'notional_u': 250,  # would exceed T1=200 if gated
            'leverage': 1,
        })
        self.assertIsNone(out['event'].get('strategy_tier'))
        self.assertNotIn('tier_budget', out)




class SimConfigBindConflictTests(unittest.TestCase):
    def setUp(self):
        import tempfile
        from pathlib import Path
        from sim_store import SimStore, SimConflict
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.store = SimStore(self.root)
        self.SimConflict = SimConflict

    def tearDown(self):
        self.tmp.cleanup()

    def test_save_config_expected_revision_conflict(self):
        cfg = self.store.save_config({'fee_rate': 0.0004, 'budget_t1_u': 111})
        rev = cfg['revision']
        # another writer
        self.store.save_config({'budget_t1_u': 222})
        with self.assertRaises(self.SimConflict):
            self.store.save_config({'budget_t1_u': 333, 'expected_revision': rev})
        latest = self.store.get_config()
        out = self.store.save_config({
            'budget_t1_u': 444,
            'expected_revision': latest['revision'],
        })
        self.assertEqual(out['budget_t1_u'], 444)
        self.assertEqual(out['revision'], latest['revision'] + 1)

if __name__ == '__main__':
    unittest.main()
