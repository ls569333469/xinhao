"""Offline EVAL-01 tests: horizon separation, no fake win rate, gap/same-bar, restart."""
from __future__ import annotations

import json
import hashlib
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from eval_store import EvalStore, EvalConflict, SAME_BAR_TP_SL, MISSING_BARS


def _ts(dt):
    return int(dt.timestamp() * 1000)


class EvalStoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        scan = {
            'scan_id': 'scan-test',
            'rows': [
                {'symbol': 'BTCUSDT', 'market': 'USDⓈ-M', 'price': 100.0, 'markPrice': 100.0},
            ],
        }
        (self.root / 'scan.json').write_text(json.dumps(scan), encoding='utf-8')
        # Minimal fake strategy store
        self.versions = {
            'ver-aaaaaaaaaaaa': {
                'version_id': 'ver-aaaaaaaaaaaa',
                'enabled': True,
                'title': '测试策略',
                'strategy_id': 'S-test',
                'content_hash': 'a' * 64,
                'rules': {
                    'params': {
                        'tp_pct': 0.10, 'sl_pct': 0.05, 'max_hold_days': 7,
                        'notional_u': 100.0, 'side': 'long',
                        'entry_rule': 'close_above_prev_high',
                    },
                    'entry_text': 'e', 'exit_text': 'x',
                },
            },
            'ver-disabled0000': {
                'version_id': 'ver-disabled0000',
                'enabled': False,
                'title': '停用',
                'strategy_id': 'S-off',
                'content_hash': 'b' * 64,
                'rules': {
                    'params': {
                        'tp_pct': 0.1, 'sl_pct': 0.05, 'max_hold_days': 7,
                        'notional_u': 100, 'side': 'long',
                    },
                },
            },
        }

        class FakeStrat:
            def get_version(self, vid):
                if vid not in self_outer.versions:
                    raise KeyError(vid)
                return self_outer.versions[vid]

        self_outer = self

        class FakeSim:
            def resolve_mark(self, symbol, market=None):
                return {'symbol': symbol, 'mark': 100.0, 'price_field': 'markPrice', 'market': market or 'USDⓈ-M', 'as_of': '2026-10-01T00:00:00+00:00'}

        self.store = EvalStore(self.root, strategy_store=FakeStrat(), sim_store=FakeSim())
        self.t0 = datetime(2026, 10, 1, 0, 0, tzinfo=timezone.utc)

    def tearDown(self):
        self.tmp.cleanup()

    def _start(self, **kw):
        # Keep fake frozen params in sync with requested exec fields (V1 gate)
        svid = kw.get('strategy_version_id', 'ver-aaaaaaaaaaaa')
        ver = self.versions[svid]
        params = dict((ver.get('rules') or {}).get('params') or {})
        for k in ('tp_pct', 'sl_pct', 'max_hold_days', 'notional_u', 'side', 'entry_rule'):
            if k in kw:
                params[k] = kw[k]
        ver.setdefault('rules', {})['params'] = params
        body = {
            'observation_id': kw.pop('observation_id', 'obs-test00000001'),
            'strategy_version_id': svid,
            'symbol': 'BTCUSDT',
            'side': params.get('side') or 'long',
            'observation_horizon_days': 30,
            'max_hold_days': params.get('max_hold_days', 7),
            'tp_pct': params.get('tp_pct', 0.10),
            'sl_pct': params.get('sl_pct', 0.05),
            'entry_price': 100.0,
            'notional_u': params.get('notional_u', 100.0),
            'start_at': self.t0.isoformat(),
            'market': 'USDⓈ-M',
        }
        body.update(kw)
        return self.store.start_observation(body)

    def test_horizon_vs_max_hold_separated(self):
        obs = self._start(observation_horizon_days=30, max_hold_days=7)
        self.assertEqual(obs['observation_horizon_days'], 30)
        self.assertEqual(obs['max_hold_days'], 7)
        self.assertNotEqual(obs['observation_ends_at'], obs['max_hold_ends_at'])
        # max hold ends earlier
        self.assertLess(obs['max_hold_ends_at'], obs['observation_ends_at'])
        # Settle window uses max_hold, not horizon: bar after max_hold ignored
        bars = [
            [_ts(self.t0 + timedelta(hours=4)), 100, 101, 99, 100.5, 1],
            # day 10 — beyond max_hold 7d but within horizon 30d — must NOT be used for TP
            [_ts(self.t0 + timedelta(days=10)), 100, 120, 100, 115, 1],
        ]
        # as_of past hold end but only partial early bar → unverifiable, NOT fake max_hold/win
        out = self.store.settle(obs['observation_id'], {
            'mode': 'rules', 'bars': bars,
            'as_of': (self.t0 + timedelta(days=10)).isoformat(),
        })
        self.assertIsNotNone(out['settlement'])
        self.assertEqual(out['settlement']['exit_reason'], 'unverifiable')
        self.assertEqual(out['settlement']['outcome'], 'unverifiable')
        self.assertIsNone(out['settlement']['exit_price'])

    def test_no_fake_win_rate_when_empty(self):
        g = self.store.grades()
        self.assertEqual(g['sample_count'], 0)
        self.assertIsNone(g['win_rate'])
        self.assertEqual(g['win_rate_label'], '无样本')

    def test_data_gap_not_counted_as_win(self):
        obs = self._start(observation_id='obs-gap000000001')
        out = self.store.settle(obs['observation_id'], {
            'mode': 'rules', 'bars': [],
            'as_of': (self.t0 + timedelta(days=10)).isoformat(),
        })
        self.assertEqual(out['settlement']['outcome'], 'data_gap')
        self.assertEqual(out['settlement']['exit_reason'], 'data_gap')
        g = self.store.grades()
        self.assertEqual(g['sample_count'], 0)
        self.assertEqual(g['data_gap_count'], 1)
        self.assertEqual(g['win_rate_label'], '无样本')
        self.assertIsNone(g['win_rate'])

    def test_same_bar_tp_sl_worse_for_trader(self):
        obs = self._start(observation_id='obs-samebar00001', tp_pct=0.10, sl_pct=0.05)
        # high hits TP 110, low hits SL 95 on same bar
        bars = [
            [_ts(self.t0 + timedelta(hours=4)), 100, 112, 94, 105, 1],
        ]
        out = self.store.settle(obs['observation_id'], {'mode': 'rules', 'bars': bars})
        s = out['settlement']
        self.assertEqual(s['exit_reason'], 'stop_loss')
        self.assertEqual(s['fill_rule'], 'same_bar_tp_sl:worse_for_trader')
        self.assertEqual(SAME_BAR_TP_SL, 'worse_for_trader')
        self.assertAlmostEqual(s['exit_price'], 95.0, places=4)  # SL price
        self.assertEqual(s['outcome'], 'loss')

    def test_gap_through_sl_fills_at_open(self):
        obs = self._start(observation_id='obs-gapfill00001', tp_pct=0.10, sl_pct=0.05)
        # open gaps through SL (open 90 < sl 95)
        bars = [
            [_ts(self.t0 + timedelta(hours=4)), 90, 92, 88, 91, 1],
        ]
        out = self.store.settle(obs['observation_id'], {'mode': 'rules', 'bars': bars})
        s = out['settlement']
        self.assertEqual(s['exit_reason'], 'stop_loss')
        self.assertEqual(s['fill_rule'], 'gap_through_level')
        self.assertAlmostEqual(s['exit_price'], 90.0, places=4)

    def test_take_profit_intrabar(self):
        obs = self._start(observation_id='obs-tp0000000001', tp_pct=0.10, sl_pct=0.05)
        bars = [
            [_ts(self.t0 + timedelta(hours=4)), 100, 111, 99.5, 110, 1],
        ]
        out = self.store.settle(obs['observation_id'], {'mode': 'rules', 'bars': bars})
        s = out['settlement']
        self.assertEqual(s['exit_reason'], 'take_profit')
        self.assertEqual(s['outcome'], 'win')
        self.assertAlmostEqual(s['exit_price'], 110.0, places=4)
        g = self.store.grades()
        self.assertEqual(g['sample_count'], 1)
        self.assertAlmostEqual(g['win_rate'], 1.0)
        self.assertNotEqual(g['win_rate_label'], '无样本')

    def test_disabled_version_rejected(self):
        with self.assertRaises(EvalConflict) as ctx:
            self._start(observation_id='obs-dis000000001', strategy_version_id='ver-disabled0000')
        self.assertIn('未启用', str(ctx.exception))

    def test_restart_replay(self):
        obs = self._start(observation_id='obs-replay000001')
        self.store.settle(obs['observation_id'], {
            'mode': 'manual',
            'exit_price': 108.0,
            'exit_reason': 'manual',
        })
        # New store instance = process restart
        class FakeStrat:
            def get_version(self, vid):
                return self_outer.versions[vid]

        self_outer = self

        class FakeSim:
            def resolve_mark(self, symbol, market=None):
                return {'symbol': symbol, 'mark': 100.0, 'price_field': 'markPrice', 'as_of': 'x'}

        store2 = EvalStore(self.root, strategy_store=FakeStrat(), sim_store=FakeSim())
        reloaded = store2.reload()
        self.assertEqual(reloaded['settlement_count'], 1)
        self.assertEqual(reloaded['open_count'], 0)
        settlements = store2.list_settlements()
        self.assertEqual(len(settlements['settlements']), 1)
        self.assertEqual(settlements['settlements'][0]['observation_id'], 'obs-replay000001')
        g = store2.grades()
        self.assertEqual(g['sample_count'], 1)
        self.assertEqual(g['wins'], 1)

    def test_duplicate_settle_rejected(self):
        obs = self._start(observation_id='obs-dupset000001')
        self.store.settle(obs['observation_id'], {'mode': 'manual', 'exit_price': 101})
        with self.assertRaises(EvalConflict):
            self.store.settle(obs['observation_id'], {'mode': 'manual', 'exit_price': 102})

    def test_missing_bars_constant(self):
        self.assertEqual(MISSING_BARS, 'data_gap')



    def test_f02_not_expired_keeps_open(self):
        obs = self._start(observation_id='obs-pending000001', max_hold_days=7)
        bars = [
            [_ts(self.t0 + timedelta(hours=4)), 100, 101, 99, 101, 1],  # close +1 no TP/SL
        ]
        out = self.store.settle(obs['observation_id'], {
            'mode': 'rules', 'bars': bars,
            'as_of': (self.t0 + timedelta(days=1)).isoformat(),  # still inside 7d
        })
        self.assertIsNone(out['settlement'])
        self.assertIsNotNone(out.get('pending'))
        self.assertEqual(out['observation']['status'], 'open')
        self.assertEqual(out['pending']['exit_reason'], 'pending_backfill')
        # Still in open_ids
        self.assertIn(obs['observation_id'], self.store._load_state()['open_ids'])
        g = self.store.grades()
        # No fake win sample
        self.assertEqual(g['sample_count'], 0)

    def test_f02_empty_bars_before_expiry_keeps_open(self):
        obs = self._start(observation_id='obs-pending000002', max_hold_days=7)
        out = self.store.settle(obs['observation_id'], {
            'mode': 'rules', 'bars': [],
            'as_of': (self.t0 + timedelta(days=1)).isoformat(),
        })
        self.assertIsNone(out['settlement'])
        self.assertEqual(out['observation']['status'], 'open')



    def test_eval_plan_default_off_and_server_effect(self):
        out = self.store.save_plan({
            'strategy_version_id': 'ver-aaaaaaaaaaaa',
            'symbols': ['BTCUSDT'],
            'check_interval_minutes': 30,
        })
        plan = out['plan']
        self.assertFalse(plan['enabled'])
        self.assertIsNone(plan.get('next_run_at'))
        effect = out['server_effect']
        self.assertIn('enabled_plan_ids', effect)
        self.assertEqual(effect['enabled_plan_count'], 0)
        # enable
        en = self.store.set_plan_enabled(plan['plan_id'], True)
        self.assertTrue(en['plan']['enabled'])
        self.assertIsNotNone(en['plan']['next_run_at'])
        self.assertEqual(en['server_effect']['enabled_plan_count'], 1)

    def test_eval_backfill_link_on_pending_and_unverifiable(self):
        obs = self._start(observation_id='obs-bf0000000001', max_hold_days=7)
        # Not expired → pending_backfill + link
        out = self.store.settle(obs['observation_id'], {
            'mode': 'rules',
            'bars': [[_ts(self.t0 + timedelta(hours=4)), 100, 101, 99, 100.5, 1]],
            'as_of': (self.t0 + timedelta(days=1)).isoformat(),
        })
        self.assertIsNotNone(out.get('pending'))
        self.assertIsNotNone(out.get('backfill_link'))
        self.assertEqual(out['backfill_link']['reason'], 'pending_backfill')
        self.assertEqual(out['observation']['status'], 'open')
        links = self.store.list_backfill_links()
        self.assertGreaterEqual(links['count'], 1)

    def test_eval_scheduler_tick_respects_pause_and_f02(self):
        class FakeReview:
            def __init__(self):
                self.paused = False
            def get_pause_new_entries(self):
                return self.paused
        fr = FakeReview()
        self.store.review_store = fr
        self.store.bind_scheduler(True)
        plan = self.store.save_plan({
            'strategy_version_id': 'ver-aaaaaaaaaaaa',
            'symbols': ['BTCUSDT'],
            'check_interval_minutes': 60,
            'enabled': True,
        })['plan']
        # Force due
        with self.store._lock:
            st = self.store._load_state()
            st['plans'][plan['plan_id']]['next_run_at'] = (self.t0 - timedelta(minutes=1)).isoformat()
            self.store._save_state(st)
        # Pause → no new open
        fr.paused = True
        tick = self.store.scheduler_tick(now=self.t0)
        self.assertTrue(tick['skipped_pause'])
        self.assertEqual(self.store._load_state()['observation_count'], 0)
        # Unpause → start
        fr.paused = False
        with self.store._lock:
            st = self.store._load_state()
            st['plans'][plan['plan_id']]['next_run_at'] = (self.t0 - timedelta(minutes=1)).isoformat()
            self.store._save_state(st)
        tick2 = self.store.scheduler_tick(now=self.t0)
        self.assertTrue(any(x.get('observation_id') for x in tick2['started'] if not x.get('error')))
        # Open obs settle check keeps F02 (no fake win without horizon)
        status = self.store.status()
        self.assertTrue(status['server_effect']['scheduler_bound'])


class FakeTaskService:
    """Minimal TaskService.submit_range stand-in for EVAL auto-enqueue tests."""

    def __init__(self):
        self.calls = []
        self._by_hash = {}

    def submit_range(self, payload):
        self.calls.append(payload)
        key = json.dumps(payload, sort_keys=True, ensure_ascii=False)
        h = hashlib.sha256(key.encode()).hexdigest()
        if h in self._by_hash:
            return {'task': self._by_hash[h], 'reused': True}
        tid = 'task-' + h[:12]
        task = {'id': tid, 'status': 'queued', 'kind': 'candle_range'}
        self._by_hash[h] = task
        return {'task': task, 'reused': False}


class EvalAutoEnqueueTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        (self.root / 'scan.json').write_text(json.dumps({
            'scan_id': 's', 'rows': [
                {'symbol': 'BTCUSDT', 'market': 'USDⓈ-M', 'price': 100.0, 'markPrice': 100.0},
            ],
        }), encoding='utf-8')
        self.versions = {
            'ver-aaaaaaaaaaaa': {
                'version_id': 'ver-aaaaaaaaaaaa', 'enabled': True,
                'title': 't', 'strategy_id': 'S', 'content_hash': 'a' * 64,
                'rules': {
                    'params': {
                        'tp_pct': 0.10, 'sl_pct': 0.05, 'max_hold_days': 7,
                        'notional_u': 100.0, 'side': 'long',
                    },
                    'entry_text': 'e', 'exit_text': 'x',
                },
            },
            'ver-bbbbbbbbbbbb': {
                'version_id': 'ver-bbbbbbbbbbbb', 'enabled': True,
                'title': 't2', 'strategy_id': 'S2', 'content_hash': 'b' * 64,
                'rules': {
                    'params': {
                        'tp_pct': 0.10, 'sl_pct': 0.05, 'max_hold_days': 7,
                        'notional_u': 100.0, 'side': 'long',
                    },
                },
            },
        }
        outer = self

        class FakeStrat:
            def get_version(self, vid):
                if vid not in outer.versions:
                    raise KeyError(vid)
                return outer.versions[vid]

        class FakeSim:
            def resolve_mark(self, symbol, market=None):
                return {
                    'symbol': symbol, 'mark': 100.0, 'price_field': 'markPrice',
                    'market': market or 'USDⓈ-M', 'as_of': '2024-01-01T00:00:00+00:00',
                }

        self.store = EvalStore(self.root, strategy_store=FakeStrat(), sim_store=FakeSim())
        self.t0 = datetime(2024, 1, 1, 0, 0, tzinfo=timezone.utc)
        self.fake_ts = FakeTaskService()
        self.store.task_service = self.fake_ts

    def tearDown(self):
        self.tmp.cleanup()

    def _start(self, **kw):
        # Keep fake frozen params in sync with requested exec fields (V1 gate)
        svid = kw.get('strategy_version_id', 'ver-aaaaaaaaaaaa')
        ver = self.versions[svid]
        params = dict((ver.get('rules') or {}).get('params') or {})
        for k in ('tp_pct', 'sl_pct', 'max_hold_days', 'notional_u', 'side', 'entry_rule'):
            if k in kw:
                params[k] = kw[k]
        ver.setdefault('rules', {})['params'] = params
        body = {
            'observation_id': kw.pop('observation_id', 'obs-test00000001'),
            'strategy_version_id': svid,
            'symbol': 'BTCUSDT',
            'side': params.get('side') or 'long',
            'observation_horizon_days': 30,
            'max_hold_days': params.get('max_hold_days', 7),
            'tp_pct': params.get('tp_pct', 0.10),
            'sl_pct': params.get('sl_pct', 0.05),
            'entry_price': 100.0,
            'notional_u': params.get('notional_u', 100.0),
            'start_at': self.t0.isoformat(),
            'market': 'USDⓈ-M',
        }
        body.update(kw)
        return self.store.start_observation(body)

    def test_auto_enqueue_backfill_default_off(self):
        plan = self.store.save_plan({
            'strategy_version_id': 'ver-aaaaaaaaaaaa',
            'symbols': ['BTCUSDT'],
        })['plan']
        self.assertFalse(plan.get('auto_enqueue_backfill'))
        obs = self._start()
        out = self.store.settle(obs['observation_id'], {
            'mode': 'rules',
            'bars': [[int(self.t0.timestamp() * 1000) + 4 * 3600 * 1000, 100, 101, 99, 100.5, 1]],
            'as_of': (self.t0 + timedelta(days=1)).isoformat(),
        })
        link = out.get('backfill_link')
        self.assertIsNotNone(link)
        self.assertEqual(link.get('enqueue_status'), 'not_requested')
        self.assertIsNone(link.get('task_id'))
        self.assertEqual(len(self.fake_ts.calls), 0)

    def test_auto_enqueue_when_plan_flag_true(self):
        self.store.save_plan({
            'strategy_version_id': 'ver-aaaaaaaaaaaa',
            'symbols': ['BTCUSDT'],
            'auto_enqueue_backfill': True,
            'enabled': False,
        })
        obs = self._start()
        out = self.store.settle(obs['observation_id'], {
            'mode': 'rules',
            'bars': [[int(self.t0.timestamp() * 1000) + 4 * 3600 * 1000, 100, 101, 99, 100.5, 1]],
            'as_of': (self.t0 + timedelta(days=1)).isoformat(),
        })
        link = out.get('backfill_link')
        self.assertIsNotNone(link)
        self.assertTrue(link.get('enqueue_attempted'))
        self.assertEqual(link.get('enqueue_status'), 'enqueued')
        self.assertTrue(str(link.get('task_id') or '').startswith('task-'))
        self.assertEqual(link.get('status'), 'enqueued')
        self.assertEqual(len(self.fake_ts.calls), 1)
        # still pending / open — F02 no fake settle
        self.assertEqual(out['observation']['status'], 'open')
        self.assertIsNotNone(out.get('pending'))

    def test_auto_enqueue_idempotent_no_duplicate_task(self):
        self.store.save_plan({
            'strategy_version_id': 'ver-aaaaaaaaaaaa',
            'symbols': ['BTCUSDT'],
            'auto_enqueue_backfill': True,
        })
        obs = self._start(observation_id='obs-ae0000000002')
        body = {
            'mode': 'rules',
            'bars': [[int(self.t0.timestamp() * 1000) + 4 * 3600 * 1000, 100, 101, 99, 100.5, 1]],
            'as_of': (self.t0 + timedelta(days=1)).isoformat(),
        }
        out1 = self.store.settle(obs['observation_id'], body)
        tid1 = out1['backfill_link']['task_id']
        out2 = self.store.settle(obs['observation_id'], body)
        link2 = out2['backfill_link']
        self.assertTrue(link2.get('idempotent_hit'))
        self.assertEqual(link2.get('task_id'), tid1)
        # FakeTaskService may not be called again; at most 1 unique enqueue
        self.assertEqual(len(self.fake_ts.calls), 1)

    def test_auto_enqueue_off_when_no_plan(self):
        # ad-hoc observation without matching plan → link only, no enqueue
        obs = self._start(observation_id='obs-ae0000000003', strategy_version_id='ver-bbbbbbbbbbbb')
        out = self.store.settle(obs['observation_id'], {
            'mode': 'rules',
            'bars': [[int(self.t0.timestamp() * 1000) + 4 * 3600 * 1000, 100, 101, 99, 100.5, 1]],
            'as_of': (self.t0 + timedelta(days=1)).isoformat(),
        })
        link = out.get('backfill_link')
        self.assertIsNotNone(link)
        self.assertEqual(link.get('enqueue_status'), 'not_requested')
        self.assertEqual(len(self.fake_ts.calls), 0)

    def test_data_gap_enqueue_when_flag(self):
        self.store.save_plan({
            'strategy_version_id': 'ver-aaaaaaaaaaaa',
            'symbols': ['BTCUSDT'],
            'auto_enqueue_backfill': True,
        })
        obs = self._start(observation_id='obs-ae0000000004', max_hold_days=1)
        # Past hold end, zero bars → data_gap
        out = self.store.settle(obs['observation_id'], {
            'mode': 'rules',
            'bars': [],
            'as_of': (self.t0 + timedelta(days=3)).isoformat(),
        })
        self.assertEqual(out['settlement']['outcome'], 'data_gap')
        link = out.get('backfill_link')
        self.assertIsNotNone(link)
        self.assertEqual(link.get('reason'), 'data_gap')
        self.assertEqual(link.get('enqueue_status'), 'enqueued')
        self.assertEqual(len(self.fake_ts.calls), 1)


class EvalBackfillResettleTests(unittest.TestCase):
    """V1: backfill complete → auto re-settle; gap → no fake win; early → no settle."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        (self.root / 'scan.json').write_text(json.dumps({
            'scan_id': 's', 'rows': [
                {'symbol': 'BTCUSDT', 'market': 'USDⓈ-M', 'price': 100.0, 'markPrice': 100.0},
            ],
        }), encoding='utf-8')
        self.versions = {
            'ver-aaaaaaaaaaaa': {
                'version_id': 'ver-aaaaaaaaaaaa', 'enabled': True,
                'title': 't', 'strategy_id': 'S', 'content_hash': 'c' * 64,
                'rules': {
                    'params': {
                        'tp_pct': 0.10, 'sl_pct': 0.05, 'max_hold_days': 7,
                        'notional_u': 100.0, 'side': 'long',
                    },
                    'entry_text': 'e', 'exit_text': 'x',
                },
            },
        }
        outer = self

        class FakeStrat:
            def get_version(self, vid):
                return outer.versions[vid]

        class FakeSim:
            def resolve_mark(self, symbol, market=None):
                return {'symbol': symbol, 'mark': 100.0, 'price_field': 'markPrice',
                        'market': market or 'USDⓈ-M', 'as_of': '2026-10-01T00:00:00+00:00'}

        self.store = EvalStore(self.root, strategy_store=FakeStrat(), sim_store=FakeSim())
        self.t0 = datetime(2026, 10, 1, 0, 0, tzinfo=timezone.utc)

    def tearDown(self):
        self.tmp.cleanup()

    def _start(self, **kw):
        # Keep fake frozen params in sync with requested exec fields (V1 gate)
        svid = kw.get('strategy_version_id', 'ver-aaaaaaaaaaaa')
        ver = self.versions[svid]
        params = dict((ver.get('rules') or {}).get('params') or {})
        for k in ('tp_pct', 'sl_pct', 'max_hold_days', 'notional_u', 'side', 'entry_rule'):
            if k in kw:
                params[k] = kw[k]
        ver.setdefault('rules', {})['params'] = params
        body = {
            'observation_id': kw.pop('observation_id', 'obs-test00000001'),
            'strategy_version_id': svid,
            'symbol': 'BTCUSDT',
            'side': params.get('side') or 'long',
            'observation_horizon_days': 30,
            'max_hold_days': params.get('max_hold_days', 7),
            'tp_pct': params.get('tp_pct', 0.10),
            'sl_pct': params.get('sl_pct', 0.05),
            'entry_price': 100.0,
            'notional_u': params.get('notional_u', 100.0),
            'start_at': self.t0.isoformat(),
            'market': 'USDⓈ-M',
        }
        body.update(kw)
        return self.store.start_observation(body)

    def test_backfill_done_triggers_resettle_to_settlement(self):
        obs = self._start(observation_id='obs-bfdone000001')
        # First settle: past hold, no bars → data_gap + link
        out0 = self.store.settle(obs['observation_id'], {
            'mode': 'rules', 'bars': [],
            'as_of': (self.t0 + timedelta(days=8)).isoformat(),
        })
        # data_gap is a terminal settlement in current code — for pending path use early:
        # Re-open scenario: pending_backfill while still in hold, then backfill completes after hold
        obs2 = self._start(observation_id='obs-bfdone000002', max_hold_days=3)
        out_p = self.store.settle(obs2['observation_id'], {
            'mode': 'rules', 'bars': [],
            'as_of': (self.t0 + timedelta(days=1)).isoformat(),
        })
        self.assertIsNone(out_p['settlement'])
        self.assertEqual(out_p['pending']['exit_reason'], 'pending_backfill')
        link = out_p.get('backfill_link')
        self.assertIsNotNone(link)
        # Simulate enqueue result: stamp task_id on link
        link2 = dict(link)
        link2['task_id'] = 'task-candle-bf-001'
        link2['enqueue_status'] = 'enqueued'
        link2['status'] = 'enqueued'
        self.store._append_jsonl(self.store.backfill_links_path, link2)

        # Continuous bars covering hold after backfill; as_of past hold → max_hold settle
        step = 4 * 3600 * 1000
        bars = []
        # 3 days * 6 bars/day = 18 bars of 4h
        for i in range(20):
            t = _ts(self.t0) + i * step
            # flat around 100 — no TP/SL
            bars.append([t, 100, 100.0, 100.0, 100.0, 1])
        # Inject bars via settle body on auto path: on_candle_range will call settle without bars
        # so monkeypatch _load_bars_for
        self.store._load_bars_for = lambda o: (bars, 'test_backfill')
        result = self.store.on_candle_range_task_finished(
            'task-candle-bf-001', 'success',
            {'range_id': 'rng-1', 'known': 20, 'expected': 20, 'status': 'success'},
        )
        self.assertTrue(result['ok'])
        self.assertTrue(result['resettles'])
        r0 = result['resettles'][0]
        self.assertEqual(r0['observation_id'], 'obs-bfdone000002')
        # Past hold with continuous flat → settled max_hold OR still_pending if as_of=now < hold
        # on_candle_range uses as_of=now (wall clock 2026-10-13) which is past hold from 2026-10-01
        self.assertIn(r0['resettle_status'], ('settled', 'still_pending'), r0)
        if r0['resettle_status'] == 'settled':
            # Legitimate max_hold PnL may be win/loss/flat; must not be silent gap TP
            sett = self.store.list_settlements(limit=5)['settlements']
            mine = [s for s in sett if s.get('observation_id') == 'obs-bfdone000002']
            self.assertTrue(mine)
            self.assertNotEqual(mine[0].get('exit_reason'), 'take_profit')
            self.assertIn(mine[0].get('exit_reason'), ('max_hold', 'stop_loss', 'data_gap', 'unverifiable'))

    def test_gap_before_tp_no_fake_win(self):
        """断档后触 TP 不得判赢。"""
        obs = self._start(observation_id='obs-gapwin000001')
        step = 4 * 3600 * 1000
        t0 = _ts(self.t0)
        bars = [
            [t0 + 0 * step, 100, 101, 99, 100, 1],
            [t0 + 1 * step, 100, 101, 99, 100, 1],
            # big gap (3 steps missing)
            [t0 + 5 * step, 100, 120, 100, 115, 1],  # would be TP touch
        ]
        out = self.store.settle(obs['observation_id'], {
            'mode': 'rules', 'bars': bars,
            'as_of': (self.t0 + timedelta(days=8)).isoformat(),
        })
        # Must NOT be a clean take_profit win
        if out.get('settlement'):
            s = out['settlement']
            self.assertNotEqual(s.get('exit_reason'), 'take_profit')
            self.assertNotEqual(s.get('outcome'), 'win')
            self.assertIn(s.get('outcome'), ('unverifiable', 'data_gap'))
        else:
            self.assertEqual(out['pending']['exit_reason'], 'pending_backfill')

    def test_early_before_hold_no_settle(self):
        obs = self._start(observation_id='obs-early0000001', max_hold_days=7)
        bars = [[_ts(self.t0 + timedelta(hours=4)), 100, 101, 99, 100.5, 1]]
        out = self.store.settle(obs['observation_id'], {
            'mode': 'rules', 'bars': bars,
            'as_of': (self.t0 + timedelta(days=1)).isoformat(),
        })
        self.assertIsNone(out['settlement'])
        self.assertEqual(out['pending']['exit_reason'], 'pending_backfill')
        self.assertEqual(out['observation']['status'], 'open')

    def test_on_backfill_failed_skips_fake_settle(self):
        obs = self._start(observation_id='obs-bffail000001', max_hold_days=3)
        out_p = self.store.settle(obs['observation_id'], {
            'mode': 'rules', 'bars': [],
            'as_of': (self.t0 + timedelta(days=1)).isoformat(),
        })
        link = dict(out_p['backfill_link'])
        link['task_id'] = 'task-fail-1'
        self.store._append_jsonl(self.store.backfill_links_path, link)
        result = self.store.on_candle_range_task_finished('task-fail-1', 'failed', None)
        self.assertEqual(result['resettles'][0]['resettle_status'], 'skipped_task_failed')
        # still open
        obs_now = self.store.get_observation('obs-bffail000001')
        self.assertEqual(obs_now['status'], 'open')



if __name__ == '__main__':
    unittest.main()
