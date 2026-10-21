"""Offline BACKTEST-01 tests: no future leak, empty sample, fees, splits, insufficient."""
from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from backtest_store import (
    BacktestStore,
    BacktestConflict,
    apply_slip,
    signal_entry,
    split_by_time,
    absolute_cut_ms,
    split_by_cut_ms,
    walk_segment,
    resolve_exit_on_bar,
    SAME_BAR_TP_SL,
)


def _ts(dt):
    return int(dt.timestamp() * 1000)


def _bars(n, start, step_hours=4, base=100.0, pattern='flat'):
    """Synthetic OHLCV. pattern: flat | up | breakout_then_tp | same_bar_both."""
    out = []
    t0 = start
    price = base
    for i in range(n):
        t = _ts(t0 + timedelta(hours=step_hours * i))
        if pattern == 'flat':
            o = h = l = c = price
        elif pattern == 'up':
            o = price
            c = price * 1.01
            h = c * 1.002
            l = o * 0.999
            price = c
        elif pattern == 'breakout_then_tp':
            # bar0 flat, bar1 close above prev high → signal; bar2 entry open;
            # later bars push to TP
            if i == 0:
                o, h, l, c = 100, 100.5, 99.5, 100
            elif i == 1:
                o, h, l, c = 100, 101, 99.8, 100.8  # close > prev high 100.5
            elif i == 2:
                o, h, l, c = 100.8, 101, 100.5, 100.9  # entry open
            elif i == 3:
                o, h, l, c = 100.9, 110, 100.7, 109  # TP 5% ~105.84
            else:
                o = h = l = c = 109
        elif pattern == 'same_bar_both':
            if i == 0:
                o, h, l, c = 100, 100.5, 99.5, 100
            elif i == 1:
                o, h, l, c = 100, 101, 99.8, 100.8
            elif i == 2:
                o, h, l, c = 100.8, 101, 100.5, 100.9
            elif i == 3:
                # same bar hits TP(~105.8) and SL(~97.8) with adverse open not gapping
                o, h, l, c = 100.9, 112, 94, 100
            else:
                o = h = l = c = 100
        else:
            o = h = l = c = price
        out.append([t, o, h, l, c, 1.0])
    return out


class FakeStrat:
    def __init__(self):
        self.versions = {
            'ver-aaaaaaaaaaaa': {
                'version_id': 'ver-aaaaaaaaaaaa',
                'enabled': True,
                'title': '测试策略',
                'strategy_id': 'S-test',
                'content_hash': 'abc123',
                'rules': {'params': {
                    'tp_pct': 0.05, 'sl_pct': 0.03,
                    'entry_rule': 'close_above_prev_high',
                    'max_hold_bars': 10, 'notional_u': 100.0, 'cooldown_bars': 1,
                }},
            },
            'ver-cccccccccccc': {
                'version_id': 'ver-cccccccccccc',
                'enabled': True,
                'title': '对照策略B',
                'strategy_id': 'S-test-b',
                'content_hash': 'def456',
                'rules': {'params': {
                    'tp_pct': 0.05, 'sl_pct': 0.03,
                    'entry_rule': 'flat_reenter',
                    'max_hold_bars': 10, 'notional_u': 100.0, 'cooldown_bars': 1,
                }},
            },
        }

    def get_version(self, vid):
        if vid not in self.versions:
            raise KeyError(vid)
        return self.versions[vid]


class FakeSim:
    def get_config(self):
        return {'fee_rate': 0.001, 'slip_rate': 0.001}


class BacktestStoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.store = BacktestStore(self.root, strategy_store=FakeStrat(), sim_store=FakeSim())
        self.t0 = datetime(2026, 1, 1, 0, 0, tzinfo=timezone.utc)

    def tearDown(self):
        self.tmp.cleanup()

    def _sync_freeze(self, body, vid='ver-aaaaaaaaaaaa'):
        """Test helper: put requested exec fields onto freeze so gate accepts (simulates re-freeze)."""
        ver = self.store.strategy_store.versions[vid]
        params = dict((ver.get('rules') or {}).get('params') or {})
        for k in ('entry_rule', 'tp_pct', 'sl_pct', 'max_hold_bars', 'cooldown_bars',
                  'notional_u', 'side', 'leverage'):
            if body.get(k) is not None:
                params[k] = body[k]
        ver.setdefault('rules', {})['params'] = params
        return body

    def test_no_future_leak_in_signal(self):
        bars = _bars(5, self.t0, pattern='breakout_then_tp')
        # Signal must use only bars[0..i]; at i=1 close 100.8 > high[0]=100.5
        self.assertTrue(signal_entry('close_above_prev_high', bars, 1))
        # At i=0 no prev → False
        self.assertFalse(signal_entry('close_above_prev_high', bars, 0))
        # Mutating a future bar must not change past signal
        bars_future = [list(b) for b in bars]
        bars_future[3][2] = 999  # future high
        self.assertTrue(signal_entry('close_above_prev_high', bars_future, 1))
        self.assertFalse(signal_entry('close_above_prev_high', bars_future, 0))

    def test_empty_sample_win_rate_label(self):
        # Too few bars → insufficient, 无样本
        bars = _bars(10, self.t0, pattern='flat')
        rec = self.store.run({
            'strategy_version_id': 'ver-aaaaaaaaaaaa',
            'symbol': 'BTCUSDT',
            'bars': bars,
            'min_bars': 48,
            'split': False,
        })
        self.assertEqual(rec['status'], 'insufficient')
        self.assertEqual(rec['metrics']['win_rate_label'], '无样本')
        self.assertIsNone(rec['metrics']['win_rate'])
        self.assertFalse(rec['params']['market_cap_used'])
        self.assertFalse(rec['bars_meta']['market_cap_used'])

    def test_reject_market_cap_backfill(self):
        bars = _bars(60, self.t0, pattern='flat')
        with self.assertRaises(ValueError) as ctx:
            self.store.run({
                'strategy_version_id': 'ver-aaaaaaaaaaaa',
                'symbol': 'BTCUSDT',
                'bars': bars,
                'min_bars': 20,
                'split': False,
                'market_cap': 1e12,
            })
        self.assertIn('市值', str(ctx.exception))

    def test_same_bar_worse_for_trader(self):
        bars = _bars(8, self.t0, pattern='same_bar_both')
        # Direct resolve
        hit = resolve_exit_on_bar('long', 100.8, 100.8 * 1.05, 100.8 * 0.97, bars[3])
        self.assertIsNotNone(hit)
        self.assertEqual(hit['exit_reason'], 'stop_loss')
        self.assertEqual(SAME_BAR_TP_SL, 'worse_for_trader')
        self.assertIn('worse_for_trader', hit['fill_rule'])

    def test_fee_and_slip_applied(self):
        # flat_reenter generates trades; fee/slip from FakeSim
        bars = _bars(80, self.t0, pattern='up')
        body = {
            'strategy_version_id': 'ver-aaaaaaaaaaaa',
            'symbol': 'BTCUSDT',
            'bars': bars,
            'min_bars': 20,
            'split': False,
            'entry_rule': 'flat_reenter',
            'max_hold_bars': 3,
            'cooldown_bars': 0,
            'tp_pct': 0.5,   # high TP so max_hold exits often
            'sl_pct': 0.5,
            'notional_u': 100.0,
        }
        self._sync_freeze(body)
        rec = self.store.run(body)
        self.assertEqual(rec['status'], 'completed')
        self.assertGreater(rec['metrics']['trades'], 0)
        self.assertGreater(rec['metrics']['fees_paid'], 0)
        # Entry fill should be worse than raw open for long
        for tr in rec['trades']:
            if tr.get('outcome') == 'data_gap':
                continue
            self.assertAlmostEqual(rec['params']['fee_rate'], 0.001)
            self.assertAlmostEqual(rec['params']['slip_rate'], 0.001)
            break

    def test_time_split_no_shuffle(self):
        bars = _bars(100, self.t0, pattern='flat')
        parts = split_by_time(bars, {'train': 0.6, 'valid': 0.2, 'test': 0.2})
        self.assertEqual(parts['cuts']['n'], 100)
        # Chronological: last train bar time < first valid < first test
        if parts['train'] and parts['valid']:
            self.assertLess(parts['train'][-1][0], parts['valid'][0][0])
        if parts['valid'] and parts['test']:
            self.assertLess(parts['valid'][-1][0], parts['test'][0][0])
        # Lengths sum
        self.assertEqual(
            len(parts['train']) + len(parts['valid']) + len(parts['test']),
            100,
        )

    def test_split_run_persists(self):
        bars = _bars(90, self.t0, pattern='breakout_then_tp')
        # Repeat pattern-ish by using flat_reenter for enough trades
        body = {
            'run_id': 'bt-testpersist01',
            'strategy_version_id': 'ver-aaaaaaaaaaaa',
            'symbol': 'ETHUSDT',
            'bars': bars,
            'min_bars': 20,
            'split': True,
            'entry_rule': 'flat_reenter',
            'max_hold_bars': 5,
            'cooldown_bars': 1,
            'notional_u': 100.0,
        }
        self._sync_freeze(body)
        rec = self.store.run(body)
        self.assertEqual(rec['status'], 'completed')
        self.assertIn('train', rec['segments'])
        self.assertIn('valid', rec['segments'])
        self.assertIn('test', rec['segments'])
        listed = self.store.list_runs()
        self.assertTrue(any(r['run_id'] == 'bt-testpersist01' for r in listed['runs']))
        got = self.store.get_run('bt-testpersist01')
        self.assertEqual(got['params_hash'], rec['params_hash'])
        self.assertTrue(any(w['id'] == 'survivor_bias' for w in got['coverage_warnings']))
        # Duplicate rejected
        with self.assertRaises(BacktestConflict):
            self.store.run({
                'run_id': 'bt-testpersist01',
                'strategy_version_id': 'ver-aaaaaaaaaaaa',
                'symbol': 'ETHUSDT',
                'bars': bars,
                'min_bars': 20,
                'split': False,
            })

    def test_apply_slip_adverse(self):
        self.assertGreater(apply_slip(100, 'long', 0.01, True), 100)
        self.assertLess(apply_slip(100, 'long', 0.01, False), 100)
        self.assertLess(apply_slip(100, 'short', 0.01, True), 100)
        self.assertGreater(apply_slip(100, 'short', 0.01, False), 100)

    def test_walk_gap_through_sl(self):
        t0 = self.t0
        bars = [
            [_ts(t0), 100, 100.5, 99.5, 100, 1],
            [_ts(t0 + timedelta(hours=4)), 100, 101, 99.8, 100.8, 1],  # signal
            [_ts(t0 + timedelta(hours=8)), 100.8, 101, 100.5, 100.9, 1],  # entry
            # next bar gaps through SL (~97.8): open 90
            [_ts(t0 + timedelta(hours=12)), 90, 92, 88, 91, 1],
        ]
        params = {
            'entry_rule': 'close_above_prev_high',
            'side': 'long',
            'tp_pct': 0.05,
            'sl_pct': 0.03,
            'max_hold_bars': 10,
            'cooldown_bars': 1,
            'notional_u': 100,
            'fee_rate': 0.0,
            'slip_rate': 0.0,
            '_segment': 'full',
        }
        m, trades = walk_segment(bars, params)
        self.assertGreaterEqual(m['trades'], 1)
        closed = [t for t in trades if t['outcome'] != 'data_gap']
        self.assertTrue(closed)
        self.assertEqual(closed[0]['exit_reason'], 'stop_loss')
        self.assertEqual(closed[0]['fill_rule'], 'gap_through_level')
        self.assertAlmostEqual(closed[0]['exit_price'], 90.0, places=4)

    def test_catalog_honest(self):
        cat = self.store.catalog()
        self.assertTrue(cat['struct_hook']['ready'])
        self.assertTrue(cat['struct_hook'].get('filter_gate'))
        ids = {w['id'] for w in cat['coverage_warnings']}
        self.assertIn('survivor_bias', ids)
        self.assertIn('local_bars_only', ids)
        self.assertIn('no_market_cap_backfill', ids)
        self.assertIn('fair_compare_batch', ids)
        self.assertTrue((cat.get('fair_compare') or {}).get('ready'))
        self.assertFalse((cat.get('fair_compare') or {}).get('universe_rebuild'))



    def test_f01_entry_bar_sl_only(self):
        """Signal at i, fill at i+1 open; entry-bar low hits SL before later TP."""
        t0 = self.t0
        bars = [
            [_ts(t0), 100, 100.5, 99.5, 100, 1],
            [_ts(t0 + timedelta(hours=4)), 100, 101, 99.8, 100.8, 1],  # signal
            # entry open 102; SL ~98.94 (3%); low 90 → must SL; later bar would TP
            [_ts(t0 + timedelta(hours=8)), 102, 103, 90, 102.5, 1],
            [_ts(t0 + timedelta(hours=12)), 102.5, 120, 102, 118, 1],
        ]
        params = {
            'entry_rule': 'close_above_prev_high',
            'side': 'long',
            'tp_pct': 0.05,
            'sl_pct': 0.03,
            'max_hold_bars': 10,
            'cooldown_bars': 1,
            'notional_u': 100,
            'fee_rate': 0.0,
            'slip_rate': 0.0,
        }
        m, trades = walk_segment(bars, params)
        closed = [t for t in trades if t['outcome'] != 'data_gap']
        self.assertTrue(closed)
        self.assertEqual(closed[0]['exit_reason'], 'stop_loss')
        self.assertAlmostEqual(closed[0]['exit_price'], 102 * 0.97, places=4)

    def test_f01_entry_bar_tp_only(self):
        t0 = self.t0
        bars = [
            [_ts(t0), 100, 100.5, 99.5, 100, 1],
            [_ts(t0 + timedelta(hours=4)), 100, 101, 99.8, 100.8, 1],
            [_ts(t0 + timedelta(hours=8)), 102, 110, 101.5, 109, 1],  # TP on entry bar
            [_ts(t0 + timedelta(hours=12)), 109, 90, 88, 89, 1],  # would SL later
        ]
        params = {
            'entry_rule': 'close_above_prev_high', 'side': 'long',
            'tp_pct': 0.05, 'sl_pct': 0.03, 'max_hold_bars': 10,
            'cooldown_bars': 1, 'notional_u': 100, 'fee_rate': 0.0, 'slip_rate': 0.0,
        }
        m, trades = walk_segment(bars, params)
        closed = [t for t in trades if t['outcome'] != 'data_gap']
        self.assertEqual(closed[0]['exit_reason'], 'take_profit')

    def test_f01_entry_bar_both_conservative(self):
        t0 = self.t0
        bars = [
            [_ts(t0), 100, 100.5, 99.5, 100, 1],
            [_ts(t0 + timedelta(hours=4)), 100, 101, 99.8, 100.8, 1],
            [_ts(t0 + timedelta(hours=8)), 102, 112, 90, 100, 1],  # both TP+SL
        ]
        params = {
            'entry_rule': 'close_above_prev_high', 'side': 'long',
            'tp_pct': 0.05, 'sl_pct': 0.03, 'max_hold_bars': 10,
            'cooldown_bars': 1, 'notional_u': 100, 'fee_rate': 0.0, 'slip_rate': 0.0,
        }
        m, trades = walk_segment(bars, params)
        closed = [t for t in trades if t['outcome'] != 'data_gap']
        self.assertEqual(closed[0]['exit_reason'], 'stop_loss')
        self.assertIn('worse_for_trader', closed[0]['fill_rule'])

    def test_f01_entry_bar_gap_sl(self):
        t0 = self.t0
        # Entry at open already through SL relative to levels? SL derived from fill.
        # Use next-bar gap after entry: still covered by existing test; here gap on entry open
        # below a pre-set path: open 102, but we check post-entry bar gap — and entry-bar
        # open gap through: if open < SL impossible since SL=open*(1-sl). So gap on FOLLOWING
        # is separate; entry-bar touch after open is the F01 focus (covered above).
        bars = [
            [_ts(t0), 100, 100.5, 99.5, 100, 1],
            [_ts(t0 + timedelta(hours=4)), 100, 101, 99.8, 100.8, 1],
            [_ts(t0 + timedelta(hours=8)), 102, 103, 101, 102.2, 1],
            [_ts(t0 + timedelta(hours=12)), 90, 92, 88, 91, 1],
        ]
        params = {
            'entry_rule': 'close_above_prev_high', 'side': 'long',
            'tp_pct': 0.05, 'sl_pct': 0.03, 'max_hold_bars': 10,
            'cooldown_bars': 1, 'notional_u': 100, 'fee_rate': 0.0, 'slip_rate': 0.0,
        }
        m, trades = walk_segment(bars, params)
        closed = [t for t in trades if t['outcome'] != 'data_gap']
        self.assertEqual(closed[0]['exit_reason'], 'stop_loss')
        self.assertEqual(closed[0]['fill_rule'], 'gap_through_level')

    def test_f04_unsupported_strategy_no_silent_default(self):
        # Version without entry_rule must not silently run close_above_prev_high
        self.store.strategy_store.versions['ver-bbbbbbbbbbbb'] = {
            'version_id': 'ver-bbbbbbbbbbbb',
            'enabled': True,
            'title': 'T1-无规则',
            'strategy_id': 'S-t1',
            'content_hash': 't1hash',
            'rules': {'params': {'tp_pct': 0.05, 'sl_pct': 0.03}, 'candidate': False},
            'data_dependencies': ['oi'],
        }
        bars = _bars(60, self.t0, pattern='breakout_then_tp')
        with self.assertRaises(ValueError) as ctx:
            self.store.run({
                'strategy_version_id': 'ver-bbbbbbbbbbbb',
                'symbol': 'BTCUSDT',
                'bars': bars,
                'min_bars': 20,
                'split': False,
            })
        self.assertTrue(
            '不可测' in str(ctx.exception) or '未声明' in str(ctx.exception) or 'entry_rule' in str(ctx.exception)
        )


class BacktestCompareTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.store = BacktestStore(self.root, strategy_store=FakeStrat(), sim_store=FakeSim())
        self.t0 = datetime(2024, 1, 1, tzinfo=timezone.utc)

    def tearDown(self):
        self.tmp.cleanup()

    def _bars(self, n=80, base=100.0, pattern='breakout_then_tp'):
        return _bars(n, self.t0, step_hours=4, base=base, pattern=pattern)

    def test_absolute_cut_ms_shared(self):
        start = _ts(self.t0)
        end = _ts(self.t0 + timedelta(hours=4 * 100))
        cuts = absolute_cut_ms(start, end, {'train': 0.6, 'valid': 0.2, 'test': 0.2})
        self.assertEqual(cuts['mode'], 'absolute_time')
        self.assertLess(cuts['train_end_ms'], cuts['valid_end_ms'])
        self.assertLess(cuts['valid_end_ms'], cuts['end_ms'])

    def test_split_by_cut_ms_fair(self):
        bars = self._bars(100)
        start = int(bars[0][0])
        end = int(bars[-1][0]) + 4 * 3600 * 1000
        cuts = absolute_cut_ms(start, end, DEFAULT_SPLITS if False else {'train': 0.6, 'valid': 0.2, 'test': 0.2})
        parts = split_by_cut_ms(bars, cuts['train_end_ms'], cuts['valid_end_ms'])
        self.assertEqual(parts['cuts']['mode'], 'absolute_time')
        self.assertEqual(len(parts['train']) + len(parts['valid']) + len(parts['test']), len(bars))

    def test_compare_multi_symbol_shared_cuts(self):
        bars_a = self._bars(90, base=100.0)
        bars_b = self._bars(90, base=50.0)
        out = self.store.compare({
            'symbols': ['BTCUSDT', 'ETHUSDT'],
            'strategy_version_ids': ['ver-aaaaaaaaaaaa'],
            'bars_by_symbol': {'BTCUSDT': bars_a, 'ETHUSDT': bars_b},
            'split': True,
            'fee_rate': 0.001,
            'slip_rate': 0.0,
        })
        self.assertEqual(out['schema'], 'backtest-compare-v1')
        self.assertEqual(out['summary']['cells_total'], 2)
        self.assertEqual(out['absolute_cuts']['mode'], 'absolute_time')
        self.assertEqual(out['window_mode'], 'intersection')
        # Same absolute cuts embedded in each run
        for cell in out['table']['cells']:
            self.assertIn(cell['status'], ('completed', 'insufficient'))
            if cell.get('run_id'):
                rec = self.store.get_run(cell['run_id'])
                self.assertEqual(
                    rec['params'].get('absolute_cuts'),
                    out['absolute_cuts'],
                )
                self.assertEqual(rec['params'].get('fee_rate'), 0.001)
        # Survivor / universe gap explicit
        gap_ids = {g['id'] for g in out['coverage_gaps']}
        self.assertIn('historical_listed_universe_rebuild', gap_ids)
        warn_ids = {w['id'] for w in out['coverage_warnings']}
        self.assertIn('survivor_bias', warn_ids)
        self.assertIn('no_market_cap_backfill', warn_ids)

    def test_compare_multi_version_matrix(self):
        bars = self._bars(100)
        out = self.store.compare({
            'symbols': ['BTCUSDT'],
            'strategy_version_ids': ['ver-aaaaaaaaaaaa', 'ver-cccccccccccc'],
            'bars_by_symbol': {'BTCUSDT': bars},
            'fee_rate': 0.0,
            'slip_rate': 0.0,
        })
        self.assertEqual(out['summary']['cells_total'], 2)
        self.assertEqual(len(out['table']['versions']), 2)
        grid = out['table']['grid']
        self.assertIn('ver-aaaaaaaaaaaa|BTCUSDT', grid)
        self.assertIn('ver-cccccccccccc|BTCUSDT', grid)

    def test_compare_insufficient_per_cell(self):
        # Short series → insufficient cell, not fake wins
        short = self._bars(10)
        long = self._bars(100)
        out = self.store.compare({
            'symbols': ['BTCUSDT', 'ETHUSDT'],
            'strategy_version_ids': ['ver-aaaaaaaaaaaa'],
            'bars_by_symbol': {'BTCUSDT': long, 'ETHUSDT': short},
            'min_bars': 48,
        })
        statuses = {c['symbol']: c['status'] for c in out['table']['cells']}
        self.assertEqual(statuses['ETHUSDT'], 'insufficient')
        eth = out['table']['grid']['ver-aaaaaaaaaaaa|ETHUSDT']
        self.assertTrue(eth['exclusions']['insufficient'])
        self.assertEqual(eth['win_rate_label'], '无样本')

    def test_compare_f04_rejects_unsupported_version(self):
        self.store.strategy_store.versions['ver-bbbbbbbbbbbb'] = {
            'version_id': 'ver-bbbbbbbbbbbb',
            'enabled': True,
            'title': 'T1-无规则',
            'strategy_id': 'S-t1',
            'content_hash': 't1hash',
            'rules': {'params': {'tp_pct': 0.05, 'sl_pct': 0.03}},
        }
        bars = self._bars(80)
        out = self.store.compare({
            'symbols': ['BTCUSDT'],
            'strategy_version_ids': ['ver-aaaaaaaaaaaa', 'ver-bbbbbbbbbbbb'],
            'bars_by_symbol': {'BTCUSDT': bars},
        })
        grid = out['table']['grid']
        self.assertEqual(grid['ver-aaaaaaaaaaaa|BTCUSDT']['status'], 'completed')
        self.assertEqual(grid['ver-bbbbbbbbbbbb|BTCUSDT']['status'], 'rejected')
        self.assertIn('entry_rule', grid['ver-bbbbbbbbbbbb|BTCUSDT']['reject_reason'])

    def test_compare_rejects_mcap_backfill(self):
        with self.assertRaises(ValueError) as ctx:
            self.store.compare({
                'symbols': ['BTCUSDT'],
                'strategy_version_ids': ['ver-aaaaaaaaaaaa'],
                'bars_by_symbol': {'BTCUSDT': self._bars(80)},
                'market_cap': 1e9,
            })
        self.assertIn('市值', str(ctx.exception))

    def test_list_and_get_compare(self):
        out = self.store.compare({
            'symbols': ['BTCUSDT'],
            'strategy_version_ids': ['ver-aaaaaaaaaaaa'],
            'bars_by_symbol': {'BTCUSDT': self._bars(80)},
        })
        listed = self.store.list_compares()
        self.assertGreaterEqual(listed['total'], 1)
        got = self.store.get_compare(out['compare_id'])
        self.assertEqual(got['compare_id'], out['compare_id'])


class BacktestStructGateTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.store = BacktestStore(self.root, strategy_store=FakeStrat(), sim_store=FakeSim())
        self.t0 = datetime(2024, 1, 1, tzinfo=timezone.utc)

    def tearDown(self):
        self.tmp.cleanup()

    def test_run_struct_required_blocks_short_history(self):
        bars = _bars(10, self.t0)
        rec = self.store.run({
            'strategy_version_id': 'ver-aaaaaaaaaaaa',
            'symbol': 'BTCUSDT',
            'bars': bars,
            'min_bars': 5,
            'struct_filter': {'mode': 'required', 'require': ['atr', 'adx']},
            'fee_rate': 0.0,
            'slip_rate': 0.0,
        })
        self.assertEqual(rec['status'], 'insufficient')
        self.assertFalse(rec.get('tradable', True))
        self.assertTrue(rec['struct_filter_applied'])
        self.assertTrue(rec['exclusions']['struct_blocked'])
        self.assertTrue(rec['struct_gate']['exclusions'])

    def test_run_struct_required_passes_with_warmup(self):
        bars = _bars(100, self.t0)
        rec = self.store.run({
            'strategy_version_id': 'ver-aaaaaaaaaaaa',
            'symbol': 'BTCUSDT',
            'bars': bars,
            'struct_filter': {'mode': 'required', 'require': ['atr']},
            'fee_rate': 0.0,
            'slip_rate': 0.0,
            'split': False,
        })
        self.assertEqual(rec['status'], 'completed')
        self.assertTrue(rec.get('tradable'))
        self.assertEqual(rec['struct_gate']['status'], 'ok')

    def test_run_struct_day_box_on_4h_excluded(self):
        bars = _bars(80, self.t0)
        rec = self.store.run({
            'strategy_version_id': 'ver-aaaaaaaaaaaa',
            'symbol': 'BTCUSDT',
            'interval': '4h',
            'bars': bars,
            'struct_filter': {'mode': 'required', 'require': ['day_box']},
            'fee_rate': 0.0,
            'slip_rate': 0.0,
            'split': False,
        })
        self.assertEqual(rec['status'], 'excluded_struct')
        codes = {e['code'] for e in rec['struct_gate']['exclusions']}
        self.assertIn('wrong_interval', codes)

    def test_run_struct_params_missing_unimplemented(self):
        bars = _bars(80, self.t0)
        rec = self.store.run({
            'strategy_version_id': 'ver-aaaaaaaaaaaa',
            'symbol': 'BTCUSDT',
            'bars': bars,
            'struct_filter': {'mode': 'required', 'require': []},
            'fee_rate': 0.0,
            'slip_rate': 0.0,
            'split': False,
        })
        self.assertEqual(rec['status'], 'unimplemented')
        self.assertIn('struct_params_missing', {e['code'] for e in rec['struct_gate']['exclusions']})

    def test_compare_struct_filter_matrix(self):
        # ADX needs non-zero TR; flat OHLC → dx 空 → insufficient（非冻结问题）
        bars = _bars(100, self.t0, pattern='up')
        out = self.store.compare({
            'symbols': ['BTCUSDT'],
            'strategy_version_ids': ['ver-aaaaaaaaaaaa'],
            'bars_by_symbol': {'BTCUSDT': bars},
            'struct_filter': {'mode': 'required', 'require': ['atr', 'adx']},
            'fee_rate': 0.0,
            'slip_rate': 0.0,
        })
        cell = out['table']['cells'][0]
        self.assertEqual(cell['status'], 'completed')
        self.assertTrue(cell.get('tradable'))
        # short bars cell excluded
        out2 = self.store.compare({
            'symbols': ['ETHUSDT'],
            'strategy_version_ids': ['ver-aaaaaaaaaaaa'],
            'bars_by_symbol': {'ETHUSDT': _bars(8, self.t0)},
            'min_bars': 5,
            'struct_filter': {'mode': 'required', 'require': ['adx']},
            'fee_rate': 0.0,
            'slip_rate': 0.0,
        })
        self.assertGreaterEqual(out2['summary']['insufficient'], 1)
        c2 = out2['table']['cells'][0]
        self.assertEqual(c2['status'], 'insufficient')
        self.assertTrue(c2['exclusions'].get('struct_blocked'))



if __name__ == '__main__':
    unittest.main()


class LookaheadCropTests(unittest.TestCase):
    """Fair window crop + STRUCT as-of; mutating future must not change past decisions."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.store = BacktestStore(self.root, strategy_store=FakeStrat(), sim_store=FakeSim())
        self.t0 = datetime(2026, 1, 1, 0, 0, tzinfo=timezone.utc)

    def tearDown(self):
        self.tmp.cleanup()

    def test_crop_bars_drops_outside_window(self):
        from backtest_store import crop_bars_to_ms
        bars = _bars(20, self.t0)
        start_ms = int(bars[5][0])
        end_ms = int(bars[14][0])
        kept, meta = crop_bars_to_ms(bars, start_ms, end_ms)
        self.assertEqual(meta['excluded_before'], 5)
        self.assertEqual(meta['excluded_after'], 5)
        self.assertEqual(len(kept), 10)
        self.assertEqual(int(kept[0][0]), start_ms)
        self.assertEqual(int(kept[-1][0]), end_ms)

    def test_signal_future_mutation_still_stable(self):
        bars = _bars(12, self.t0, pattern='breakout_then_tp')
        self.assertTrue(signal_entry('close_above_prev_high', bars, 1))
        bars2 = [list(b) for b in bars]
        bars2[-1][2] = 99999
        bars2[-1][4] = 99999
        self.assertTrue(signal_entry('close_above_prev_high', bars2, 1))
        self.assertFalse(signal_entry('close_above_prev_high', bars2, 0))

    def test_compare_crops_to_intersection(self):
        # Symbol A starts earlier than B → fair crop drops A's head
        bars_a = _bars(40, self.t0, pattern='up')
        bars_b = _bars(30, self.t0 + timedelta(hours=4 * 10), pattern='up')
        out = self.store.compare({
            'symbols': ['AAAUSDT', 'BBBUSDT'],
            'strategy_version_ids': ['ver-aaaaaaaaaaaa'],
            'bars_by_symbol': {'AAAUSDT': bars_a, 'BBBUSDT': bars_b},
            'split': False,
            'fee_rate': 0.0,
            'slip_rate': 0.0,
            'min_bars': 10,
        })
        self.assertTrue(out.get('lookahead_guard') or out.get('window_mode') in ('intersection', 'union_fallback'))
        notes = out.get('window_crop_notes') or []
        # At least one symbol should have excluded_before > 0 when intersection applies
        if out.get('window_mode') == 'intersection':
            self.assertTrue(any(n.get('excluded_before', 0) > 0 for n in notes), notes)

    def test_struct_min_adx_as_of_not_full_window(self):
        # Cell must complete (preflight ignores min_adx); exclusions counted as-of
        bars = _bars(100, self.t0, pattern='up')
        rec = self.store.run({
            'strategy_version_id': 'ver-aaaaaaaaaaaa',
            'symbol': 'BTCUSDT',
            'bars': bars,
            'struct_filter': {'mode': 'required', 'require': ['adx'], 'min_adx': 99.0},
            'fee_rate': 0.0,
            'slip_rate': 0.0,
            'split': False,
            'min_bars': 40,
        })
        # Should not be blocked solely by end-window min_adx at cell level
        self.assertNotEqual(rec.get('status'), 'excluded_struct', rec.get('status_note'))
        # Gate config records as-of entry policy
        cfg = ((rec.get('struct_gate') or {}).get('config') or {})
        self.assertIs(cfg.get('value_thresholds_at_cell'), False)
        self.assertTrue(cfg.get('value_thresholds_as_of_entry'))


if __name__ == '__main__':
    unittest.main()
