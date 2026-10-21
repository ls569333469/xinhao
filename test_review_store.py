"""Offline REVIEW-01 tests: data review no AI; plan dedupe; pause flag; restart."""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from review_store import ReviewStore, ReviewConflict, _period_key


class FakeEval:
    def grades(self, strategy_version_id=None):
        return {
            'sample_count': 2,
            'wins': 1,
            'losses': 1,
            'win_rate': 0.5,
            'win_rate_label': '50.00%',
            'settlement_count': 2,
        }

    def list_settlements(self, limit=50):
        return {'settlements': [{'settlement_id': 'set-1', 'outcome': 'win'}]}

    def list_observations(self, status=None, limit=50):
        return {
            'observations': [
                {'observation_id': 'obs-1', 'symbol': 'BTCUSDT', 'status': 'open', 'strategy_version_id': 'ver-aaa'},
            ]
        }


class FakeSim:
    def list_accounts(self):
        return {
            'accounts': [{'account_id': 'acc-shared', 'cash': 1000.0, 'equity_book': 1000.0}],
            'conservation_ok': True,
        }


class FakeAiConfigured:
    def public_config(self):
        return {'keyConfigured': True, 'modelId': 'm'}


class FakeAiMissing:
    def public_config(self):
        return {'keyConfigured': False, 'modelId': None}


class ReviewStoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        (self.root / 'runtime').mkdir(parents=True, exist_ok=True)
        self.store = ReviewStore(
            self.root,
            eval_store=FakeEval(),
            sim_store=FakeSim(),
            ai_service=FakeAiMissing(),
        )

    def tearDown(self):
        self.tmp.cleanup()

    def test_data_review_never_calls_ai(self):
        rec = self.store.create_data_review({'note': '数值快照'})
        self.assertEqual(rec['kind'], 'data')
        self.assertFalse(rec['ai_called'])
        self.assertEqual(rec['ai_status'], 'not_applicable')
        self.assertFalse(rec['numerical']['ai_called'])
        self.assertEqual(rec['numerical']['grades']['sample_count'], 2)
        self.assertEqual(rec['numerical']['open_observation_count'], 1)
        self.assertIn('accounts', rec['numerical'])
        # Ensure no ai_service was required
        bare = ReviewStore(self.root / 'bare', eval_store=FakeEval(), sim_store=FakeSim(), ai_service=None)
        rec2 = bare.create_data_review({})
        self.assertFalse(rec2['ai_called'])

    def test_ai_manual_rejects_without_key(self):
        with self.assertRaises(ValueError) as ctx:
            self.store.create_ai_manual_review({})
        self.assertIn('尚未配置 API Key', str(ctx.exception))

    def test_ai_manual_key_ok_no_model_call(self):
        store = ReviewStore(
            self.root / 'withkey',
            eval_store=FakeEval(),
            sim_store=FakeSim(),
            ai_service=FakeAiConfigured(),
        )
        rec = store.create_ai_manual_review({'note': '手动'})
        self.assertEqual(rec['kind'], 'ai_manual')
        self.assertFalse(rec['ai_called'])
        self.assertEqual(rec['ai_status'], 'key_ok_not_invoked')
        with self.assertRaises(ValueError) as ctx:
            store.create_ai_manual_review({'invoke_ai': True})
        self.assertIn('未接通付费', str(ctx.exception))

    def test_plan_dedupe_same_kind(self):
        r1 = self.store.save_plan({
            'kind': 'daily',
            'time_of_day': '21:00',
            'timezone': 'Asia/Shanghai',
            'enabled': True,
        })
        self.assertTrue(r1['plan']['enabled'])
        self.assertIsNotNone(r1['plan']['next_run_at'])
        with self.assertRaises(ReviewConflict):
            self.store.save_plan({
                'kind': 'daily',
                'time_of_day': '22:00',
                'enabled': False,
            })
        # weekly is separate
        r2 = self.store.save_plan({'kind': 'weekly', 'time_of_day': '21:00', 'weekday': 1, 'enabled': False})
        self.assertEqual(r2['plan']['kind'], 'weekly')
        self.assertIsNone(r2['plan']['next_run_at'])

    def test_period_key_dedupe_on_data_review(self):
        self.store.create_data_review({'period_kind': 'daily'})
        with self.assertRaises(ReviewConflict):
            self.store.create_data_review({'period_kind': 'daily'})
        # force allows
        rec = self.store.create_data_review({'period_kind': 'daily', 'force': True})
        self.assertTrue(rec['period_key'].startswith('daily:'))

    def test_pause_flag_persistence_and_restart(self):
        out = self.store.set_pause_new_entries(True)
        self.assertTrue(out['pause_new_entries'])
        self.assertTrue(out['server_effect']['pause_new_entries_active'])
        self.assertIn('不影响原仓', out['pause_note'])
        # New store instance = restart
        again = ReviewStore(
            self.root,
            eval_store=FakeEval(),
            sim_store=FakeSim(),
            ai_service=FakeAiMissing(),
        )
        self.assertTrue(again.get_pause_new_entries())
        st = again.status()
        self.assertTrue(st['pause_new_entries'])
        self.assertTrue(st['server_effect']['pause_new_entries_active'])
        # Pausing must not imply closing positions — flag only
        again.set_pause_new_entries(False)
        self.assertFalse(again.get_pause_new_entries())

    def test_plan_enable_pause_and_server_effect(self):
        saved = self.store.save_plan({
            'kind': 'daily',
            'time_of_day': '21:00',
            'enabled': False,
        })
        pid = saved['plan']['plan_id']
        self.assertNotIn(pid, saved['server_effect']['enabled_plan_ids'])
        enabled = self.store.set_plan_enabled(pid, True)
        self.assertTrue(enabled['plan']['enabled'])
        self.assertIn(pid, enabled['server_effect']['enabled_plan_ids'])
        self.assertIsNotNone(enabled['plan']['next_run_at'])
        paused = self.store.set_plan_enabled(pid, False)
        self.assertFalse(paused['plan']['enabled'])
        self.assertIsNone(paused['plan']['next_run_at'])
        # Restart restores plans
        again = ReviewStore(self.root, eval_store=FakeEval(), sim_store=FakeSim())
        plans = again.list_plans()['plans']
        self.assertEqual(len(plans), 1)
        self.assertEqual(plans[0]['plan_id'], pid)
        self.assertFalse(plans[0]['enabled'])

    def test_list_reviews_and_get(self):
        rec = self.store.create_data_review({})
        listed = self.store.list_reviews()
        self.assertGreaterEqual(listed['count'], 1)
        self.assertNotIn('numerical', listed['reviews'][0])
        self.assertIn('numerical_summary', listed['reviews'][0])
        full = self.store.get_review(rec['review_id'])
        self.assertIn('numerical', full)

    def test_period_key_helpers(self):
        from datetime import datetime, timezone
        dt = datetime(2026, 10, 13, 12, 0, tzinfo=timezone.utc)
        self.assertEqual(_period_key('daily', when=dt), 'daily:2026-10-13')
        self.assertTrue(_period_key('weekly', when=dt).startswith('weekly:2026-W'))



    def test_review_plan_default_off_and_use_ai_flag(self):
        out = self.store.save_plan({'kind': 'daily', 'time_of_day': '21:00'})
        self.assertFalse(out['plan']['enabled'])
        self.assertFalse(out['plan'].get('use_ai'))
        self.assertFalse(out['plan'].get('allow_paid_ai'))
        self.assertFalse(out['server_effect']['scheduler_bound'])

    def test_review_scheduler_tick_fires_data_not_paid_ai(self):
        from datetime import datetime, timezone, timedelta
        # Key configured so AI gate path can run; still must not bill
        self.store.ai_service = FakeAiConfigured()
        self.store.bind_scheduler(True)
        plan = self.store.save_plan({
            'kind': 'daily', 'time_of_day': '09:00', 'enabled': True, 'use_ai': False,
        })['plan']
        past = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
        with self.store._lock:
            st = self.store._load_state()
            st['plans'][plan['plan_id']]['next_run_at'] = past
            self.store._save_state(st)
        tick = self.store.scheduler_tick()
        self.assertGreaterEqual(tick['plans_due'], 1)
        fired = [f for f in tick['fired'] if not f.get('error')]
        self.assertTrue(fired)
        self.assertEqual(fired[0]['kind'], 'data')
        self.assertFalse(fired[0]['ai_called'])
        plan2 = self.store.save_plan({
            'kind': 'weekly', 'time_of_day': '10:00', 'weekday': 1, 'enabled': True, 'use_ai': True,
        })['plan']
        with self.store._lock:
            st = self.store._load_state()
            st['plans'][plan2['plan_id']]['next_run_at'] = past
            self.store._save_state(st)
        tick2 = self.store.scheduler_tick()
        ai_fired = [f for f in tick2['fired'] if f.get('plan_id') == plan2['plan_id'] and not f.get('error')]
        self.assertTrue(ai_fired)
        self.assertEqual(ai_fired[0]['kind'], 'ai_manual')
        self.assertFalse(ai_fired[0]['ai_called'])
        self.assertTrue(self.store.status()['server_effect']['scheduler_bound'])


if __name__ == '__main__':
    unittest.main()
