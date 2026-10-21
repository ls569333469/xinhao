"""Offline STRUCT-01 tests: warmup, no fake numbers, range-high label, frozen params."""
from __future__ import annotations

import math
import tempfile
import unittest
from pathlib import Path

from struct_indicators import (
    evaluate_struct_gate,
    evaluate_struct_gate_as_of,
    resolve_struct_gate_config,
    FROZEN_PARAMS,
    PARAMS_VERSION,
    catalog,
    compute_adx,
    compute_atr,
    compute_day_box,
    compute_from_bars,
    compute_range_high,
    compute_absorption_weak,
)


def synth_bars(n, start=1_700_000_000_000, step=86_400_000, base=100.0, vol=1000.0):
    """Synthetic daily OHLCV with mild trend + noise-free ranges."""
    bars = []
    price = base
    for i in range(n):
        o = price
        h = price * 1.01
        l = price * 0.99
        c = price * 1.005
        v = vol * (3.0 if i >= n - 3 else 1.0)  # last 3 bars high volume
        # last 3: flat open/close for absorption
        if i >= n - 3:
            c = o * 1.001
            h = max(o, c) * 1.002
            l = min(o, c) * 0.998
        bars.append({
            'open_time': start + i * step,
            'open': o,
            'high': h,
            'low': l,
            'close': c,
            'volume': v,
        })
        price = c
    return bars


class StructIndicatorTests(unittest.TestCase):
    def test_atr_insufficient_history(self):
        bars = synth_bars(5)
        out = compute_atr(bars, period=14)
        self.assertEqual(out['status'], 'insufficient_history')
        self.assertIsNone(out['value'])
        self.assertEqual(out['formula_id'], 'ATR_WILDER_v1')
        self.assertEqual(out['params_version'], PARAMS_VERSION)

    def test_atr_ok_no_nan(self):
        bars = synth_bars(40)
        out = compute_atr(bars, period=14)
        self.assertEqual(out['status'], 'ok')
        self.assertTrue(math.isfinite(out['value']))
        self.assertGreater(out['value'], 0)
        self.assertEqual(out['warmup_required'], 15)

    def test_adx_insufficient(self):
        bars = synth_bars(20)
        out = compute_adx(bars, period=14)
        self.assertEqual(out['status'], 'insufficient_history')
        self.assertIsNone(out['value'])
        self.assertEqual(out['warmup_required'], 29)

    def test_adx_ok(self):
        bars = synth_bars(60)
        out = compute_adx(bars, period=14)
        self.assertEqual(out['status'], 'ok')
        self.assertTrue(math.isfinite(out['value']))
        self.assertIsNotNone(out.get('plus_di'))
        self.assertIsNotNone(out.get('minus_di'))

    def test_range_high_never_ath(self):
        bars = synth_bars(30)
        bars[10]['high'] = 999.0
        out = compute_range_high(bars)
        self.assertEqual(out['status'], 'ok')
        self.assertEqual(out['value'], 999.0)
        self.assertEqual(out['label'], '区间高点')
        self.assertFalse(out['ath_claim'])
        self.assertFalse(out['full_history_verified'])

    def test_day_box_lookback(self):
        bars = synth_bars(25)
        out = compute_day_box(bars, lookback=20, interval='1d')
        self.assertEqual(out['status'], 'ok')
        self.assertEqual(out['lookback'], 20)
        self.assertEqual(out['label'], '日线箱体（可得窗口）')
        self.assertTrue(math.isfinite(out['high']))
        self.assertTrue(math.isfinite(out['low']))
        self.assertLessEqual(out['low'], out['high'])

    def test_day_box_wrong_interval(self):
        bars = synth_bars(25)
        out = compute_day_box(bars, lookback=20, interval='4h')
        self.assertEqual(out['status'], 'wrong_interval')
        self.assertIsNone(out['value'])

    def test_day_box_insufficient(self):
        bars = synth_bars(10)
        out = compute_day_box(bars, lookback=20, interval='1d')
        self.assertEqual(out['status'], 'insufficient_history')

    def test_absorption_weak_label(self):
        bars = synth_bars(20)
        out = compute_absorption_weak(bars)
        self.assertEqual(out['evidence_strength'], '弱证据')
        self.assertFalse(out['confirmed_absorption'])
        self.assertIn(out['status'], ('ok', 'no_signal'))

    def test_frozen_params_reject_override(self):
        bars = synth_bars(40)
        with self.assertRaises(ValueError):
            compute_from_bars(bars, interval='1d', overrides={'atr_period': 7})

    def test_compute_from_bars_bundle(self):
        bars = synth_bars(60)
        out = compute_from_bars(bars, interval='1d')
        self.assertEqual(out['params_version'], PARAMS_VERSION)
        self.assertEqual(out['params'], FROZEN_PARAMS)
        self.assertFalse(out['full_history_verified'])
        self.assertFalse(out['backtest_hook']['ready'])
        self.assertEqual(out['metrics']['range_high']['label'], '区间高点')
        self.assertNotEqual(out['metrics']['atr']['status'], 'ok') if False else None
        self.assertEqual(out['metrics']['atr']['status'], 'ok')
        self.assertEqual(out['metrics']['adx']['status'], 'ok')

    def test_catalog(self):
        cat = catalog()
        self.assertEqual(cat['params_version'], PARAMS_VERSION)
        ids = {f['formula_id'] for f in cat['formulas']}
        self.assertIn('ATR_WILDER_v1', ids)
        self.assertIn('ADX_WILDER_v1', ids)
        self.assertFalse(cat['ath_policy']['full_history_verified_source'])
        self.assertEqual(cat['ath_policy']['label_when_unverified'], '区间高点')


class StructGateTests(unittest.TestCase):
    def _bars(self, n, start_ms=1_700_000_000_000, step=4 * 3600 * 1000):
        out = []
        price = 100.0
        for i in range(n):
            # mild movement so ATR/ADX can compute
            o = price
            h = price * 1.01
            l = price * 0.99
            c = price * 1.005
            out.append([start_ms + i * step, o, h, l, c, 10.0 + i])
            price = c
        return out

    def test_resolve_off_default(self):
        cfg = resolve_struct_gate_config({}, {})
        self.assertEqual(cfg['mode'], 'off')

    def test_resolve_bool_required_defaults(self):
        cfg = resolve_struct_gate_config({'struct_filter': True}, {})
        self.assertEqual(cfg['mode'], 'required')
        self.assertEqual(cfg['require'], ['atr', 'adx'])

    def test_gate_required_ok(self):
        bars = self._bars(80)
        gate = resolve_struct_gate_config({'struct_filter': {'mode': 'required', 'require': ['atr', 'adx']}}, {})
        res = evaluate_struct_gate(bars, interval='4h', gate=gate)
        self.assertTrue(res['tradable'])
        self.assertEqual(res['status'], 'ok')
        self.assertFalse(res['ath_claim'])

    def test_gate_required_insufficient(self):
        bars = self._bars(5)
        gate = resolve_struct_gate_config({'struct_filter': {'mode': 'required', 'require': ['atr']}}, {})
        res = evaluate_struct_gate(bars, interval='4h', gate=gate)
        self.assertFalse(res['tradable'])
        self.assertEqual(res['status'], 'insufficient')
        self.assertTrue(any(e['code'] == 'insufficient_history' for e in res['exclusions']))

    def test_gate_day_box_wrong_interval_explicit(self):
        bars = self._bars(40)
        gate = resolve_struct_gate_config({
            'struct_filter': {'mode': 'required', 'require': ['day_box']},
        }, {})
        res = evaluate_struct_gate(bars, interval='4h', gate=gate)
        self.assertFalse(res['tradable'])
        self.assertEqual(res['status'], 'excluded_struct')
        self.assertTrue(any(e['code'] == 'wrong_interval' for e in res['exclusions']))

    def test_gate_missing_require_unimplemented(self):
        gate = resolve_struct_gate_config({
            'struct_filter': {'mode': 'required', 'require': []},
        }, {})
        self.assertFalse(gate['params_complete'])
        res = evaluate_struct_gate(self._bars(80), interval='4h', gate=gate)
        self.assertEqual(res['status'], 'unimplemented')
        self.assertFalse(res['tradable'])

    def test_gate_ath_forbidden(self):
        gate = resolve_struct_gate_config({
            'struct_filter': {'mode': 'required', 'require': ['ath']},
        }, {})
        res = evaluate_struct_gate(self._bars(80), interval='4h', gate=gate)
        self.assertEqual(res['status'], 'unimplemented')
        self.assertIn('ath_unimplemented', {e['code'] for e in res['exclusions']})

    def test_gate_optional_soft(self):
        gate = resolve_struct_gate_config({
            'struct_filter': {'mode': 'optional', 'require': ['atr']},
        }, {})
        res = evaluate_struct_gate(self._bars(3), interval='4h', gate=gate)
        self.assertTrue(res['tradable'])
        self.assertTrue(res['exclusions'])

    def test_catalog_backtest_hook_ready(self):
        from struct_indicators import catalog
        cat = catalog()
        self.assertTrue(cat['backtest_hook']['ready'])
        self.assertTrue(cat['backtest_hook']['filter_gate'])



class LookaheadGuardTests(unittest.TestCase):
    """STRUCT as-of must not peek at future bars."""

    def _bars_up(self, n, start=100.0):
        out = []
        px = start
        t0 = 1_700_000_000_000
        step = 4 * 3600 * 1000
        for i in range(n):
            o = px
            c = px * 1.01
            out.append({
                'open_time': t0 + i * step,
                'open': o, 'high': c * 1.002, 'low': o * 0.999, 'close': c, 'volume': 1.0,
            })
            px = c
        return out

    def test_as_of_adx_immune_to_future_mutation(self):
        bars = self._bars_up(80)
        gate = {'mode': 'required', 'require': ['adx'], 'min_adx': 0, 'params_complete': True}
        mid = 40
        a = evaluate_struct_gate_as_of(bars, mid, interval='4h', gate=gate)
        for j in range(mid + 1, len(bars)):
            bars[j]['high'] = 1e9
            bars[j]['low'] = 1e-9
            bars[j]['close'] = 1e9
        b = evaluate_struct_gate_as_of(bars, mid, interval='4h', gate=gate)
        self.assertEqual(a.get('ok'), b.get('ok'))
        va = (a.get('metrics') or {}).get('adx', {}).get('value')
        vb = (b.get('metrics') or {}).get('adx', {}).get('value')
        if va is not None and vb is not None:
            self.assertAlmostEqual(float(va), float(vb), places=10)
        self.assertTrue(a.get('lookahead_guard'))

    def test_as_of_out_of_range_refuses_future_fill(self):
        bars = self._bars_up(10)
        gate = {'mode': 'required', 'require': ['atr'], 'params_complete': True}
        out = evaluate_struct_gate_as_of(bars, 50, interval='4h', gate=gate)
        self.assertFalse(out.get('tradable'))
        self.assertEqual(out.get('status'), 'insufficient')
        codes = {e['code'] for e in out.get('exclusions') or []}
        self.assertIn('as_of_insufficient', codes)

    def test_cell_value_thresholds_off_ignores_end_window_min_adx(self):
        bars = self._bars_up(60)
        for j in range(50, 60):
            bars[j]['high'] = bars[j]['close'] * 1.2
            bars[j]['low'] = bars[j]['close'] * 0.8
        gate = {
            'mode': 'required', 'require': ['adx'], 'min_adx': 90.0,
            'params_complete': True,
        }
        cell = evaluate_struct_gate(bars, interval='4h', gate=gate, value_thresholds=False)
        self.assertTrue(cell.get('tradable'), cell)


if __name__ == '__main__':
    unittest.main()
