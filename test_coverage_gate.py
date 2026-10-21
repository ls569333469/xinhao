"""Offline DATA-01 coverage gate tests: partial/unknown, no OI/mcap complete claim."""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from coverage_gate import (
    catalog,
    evaluate,
    evaluate_scan_fields,
    field_known_on_row,
    _status,
)


class CoverageGateTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def _write_scan(self, rows, **extra):
        scan = {
            'scan_id': 'scan-gate-test',
            'revision': 'rev-gate',
            'total': len(rows),
            'received': len(rows),
            'rows': rows,
            'quality': {'status': 'partial_success'},
        }
        scan.update(extra)
        (self.root / 'scan.json').write_text(json.dumps(scan), encoding='utf-8')

    def test_status_helpers(self):
        self.assertEqual(_status(0, 10), 'unknown')
        self.assertEqual(_status(3, 10), 'partial')
        self.assertEqual(_status(10, 10), 'covered')
        self.assertEqual(_status(0, 0), 'unknown')

    def test_partial_and_unknown_fields(self):
        rows = [
            {
                'symbol': 'AAAUSDT', 'market': 'USDⓈ-M',
                'price': 1.0, 'markPrice': 1.0,
                'oiUsd': 100.0, 'capUsd': None, 'accountRatio': 0.8,
                'fundingAprPct': -10.0, 'volumeRatio': 2.0,
                'closed4h': 10, 'ema4h': [1.0, 1.1, 1.2],
            },
            {
                'symbol': 'BBBUSDT', 'market': 'USDⓈ-M',
                'price': 2.0, 'markPrice': 2.0,
                # oi / cap / account missing
                'fundingAprPct': -5.0, 'volumeRatio': 1.5,
                'closed4h': 0, 'ema4h': {},
            },
        ]
        self._write_scan(rows)
        report = evaluate_scan_fields(self.root)
        by_id = {f['id']: f for f in report['fields']}
        self.assertEqual(by_id['price']['status'], 'covered')
        self.assertEqual(by_id['oiUsd']['status'], 'partial')
        self.assertEqual(by_id['oiUsd']['known'], 1)
        self.assertEqual(by_id['oiUsd']['missing'], 1)
        self.assertFalse(by_id['oiUsd']['history_complete'])
        self.assertFalse(by_id['oiUsd']['completeness_claim'])
        # capUsd all missing on usable finite → BBB None, AAA None → wait AAA has None
        self.assertEqual(by_id['capUsd']['status'], 'unknown')
        self.assertEqual(by_id['capUsd']['known'], 0)
        self.assertFalse(report['claims']['oi_history_complete'])
        self.assertFalse(report['claims']['market_cap_history_complete'])
        self.assertFalse(report['claims']['account_history_complete'])
        self.assertEqual(report['aggregate']['status'], 'partial')

    def test_all_missing_unknown_not_fake(self):
        rows = [
            {'symbol': 'X', 'market': 'USDⓈ-M'},
            {'symbol': 'Y', 'market': 'USDⓈ-M'},
        ]
        self._write_scan(rows)
        report = evaluate_scan_fields(self.root)
        by_id = {f['id']: f for f in report['fields']}
        self.assertEqual(by_id['price']['status'], 'unknown')
        self.assertEqual(by_id['oiUsd']['status'], 'unknown')
        # symbol covered
        self.assertEqual(by_id['symbol']['status'], 'covered')
        self.assertIn(report['aggregate']['status'], ('partial', 'unknown'))

    def test_catalog_forbids_complete_claims(self):
        cat = catalog()
        self.assertIn('oi_history_complete', cat['forbidden_global_claims'])
        self.assertIn('market_cap_history_complete', cat['forbidden_global_claims'])
        ids = {f['id'] for f in cat['fields']}
        self.assertIn('oiUsd', ids)
        self.assertIn('ohlcv_4h', ids)

    def test_field_known_rejects_nan(self):
        self.assertFalse(field_known_on_row('price', {'price': float('nan')}))
        self.assertTrue(field_known_on_row('price', {'price': 1.25}))
        self.assertFalse(field_known_on_row('ema4h', {'ema4h': {}}))
        self.assertFalse(field_known_on_row('ema4h', {'ema4h': {'ema7': 1.0}}))
        self.assertTrue(field_known_on_row('ema4h', {'ema4h': [1.0, 2.0, 3.0]}))

    def test_evaluate_combined_shape(self):
        self._write_scan([
            {
                'symbol': 'BTCUSDT', 'market': 'USDⓈ-M',
                'price': 100, 'markPrice': 100, 'oiUsd': 1e6,
                'capUsd': 1e9, 'accountRatio': 0.9, 'oiCapPct': 0.1,
                'fundingAprPct': 1.0, 'volumeRatio': 1.2,
                'closed4h': 20, 'ema4h': [99, 98, 97],
            }
        ])
        out = evaluate(self.root, candle_limit=5)
        self.assertEqual(out['schema'], 'coverage-gate-v1')
        self.assertFalse(out['claims']['oi_history_complete'])
        self.assertIn(out['aggregate']['status'], ('covered', 'partial', 'unknown'))



    def test_f07_expected_not_collapsed_to_received(self):
        # total=10 but only 1 row received with price → price must NOT be covered 100%
        rows = [
            {'symbol': 'AAAUSDT', 'market': 'USDⓈ-M', 'price': 1.0, 'markPrice': 1.0},
        ]
        self._write_scan(rows, total=10, received=1)
        report = evaluate_scan_fields(self.root)
        by_id = {f['id']: f for f in report['fields']}
        self.assertEqual(by_id['price']['expected'], 10)
        self.assertEqual(by_id['price']['received'], 1)
        self.assertEqual(by_id['price']['known'], 1)
        self.assertNotEqual(by_id['price']['status'], 'covered')
        self.assertIn(by_id['price']['status'], ('partial', 'unknown'))

class CoverageDeepenTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def _write_scan(self, rows, **extra):
        scan = {
            'scan_id': 'scan-deepen',
            'revision': 'rev-deepen',
            'total': extra.pop('total', len(rows)),
            'received': extra.pop('received', len(rows)),
            'rows': rows,
            'quality': {'status': 'partial_success'},
        }
        scan.update(extra)
        (self.root / 'scan.json').write_text(json.dumps(scan), encoding='utf-8')

    def test_gaps_and_backfill_hints(self):
        from coverage_gate import build_field_gaps, evaluate, coverage_fingerprint
        rows = [
            {'symbol': 'AAAUSDT', 'market': 'USDⓈ-M', 'price': 1.0, 'markPrice': 1.0, 'oiUsd': 10.0},
            {'symbol': 'BBBUSDT', 'market': 'USDⓈ-M', 'price': 2.0, 'markPrice': 2.0},
        ]
        self._write_scan(rows)
        gaps = build_field_gaps(self.root)
        self.assertGreaterEqual(gaps['gap_count'], 1)
        by = {g['field_id']: g for g in gaps['gaps']}
        self.assertIn('oiUsd', by)
        self.assertIn('BBBUSDT', by['oiUsd']['gap_symbols_sample'])
        self.assertTrue(by['oiUsd']['backfill_hint'])
        self.assertNotIn('二手日历', json.dumps(gaps, ensure_ascii=False))
        rep = evaluate(self.root, candle_limit=5)
        self.assertEqual(rep['slice'], 'DATA-01-coverage-deepen')
        self.assertIn('fingerprint', rep)
        self.assertTrue(rep['fingerprint']['hex'])
        fp2 = coverage_fingerprint(rep)
        self.assertEqual(fp2['hex'], rep['fingerprint']['hex'])

    def test_baseline_and_scan_research_gate(self):
        from coverage_gate import assert_baseline_coverage, assert_scan_research_gate
        # no scan
        base = assert_baseline_coverage(self.root)
        self.assertFalse(base['ok'])
        # shortfall universe
        self._write_scan(
            [{'symbol': 'AAAUSDT', 'market': 'USDⓈ-M', 'price': 1.0, 'markPrice': 1.0}],
            total=10, received=1, quality={'status': 'partial_success'},
        )
        gate = assert_scan_research_gate(self.root, {'total': 10, 'received': 1, 'quality': {'status': 'partial_success'}})
        self.assertFalse(gate['ok'])
        self.assertIn('一半', gate['reason'])
        # healthy enough
        rows = [
            {'symbol': f'S{i}USDT', 'market': 'USDⓈ-M', 'price': float(i), 'markPrice': float(i)}
            for i in range(6)
        ]
        self._write_scan(rows, total=6, received=6, quality={'status': 'success'})
        gate2 = assert_scan_research_gate(self.root, {'total': 6, 'received': 6, 'quality': {'status': 'success'}})
        self.assertTrue(gate2['ok'])

    def test_empty_deps_require_baseline(self):
        from rule_coverage_gate import assert_deps_covered
        # no scan → fail
        gate = assert_deps_covered(self.root, [])
        self.assertFalse(gate['ok'])
        self.assertIn('baseline_scan_coverage', gate['missing_dependencies'])
        # with baseline scan → ok
        self._write_scan([
            {'symbol': 'BTCUSDT', 'market': 'USDⓈ-M', 'price': 100.0, 'markPrice': 100.0},
        ])
        gate2 = assert_deps_covered(self.root, [])
        self.assertTrue(gate2['ok'])

    def test_autofill_symbol_gate_missing(self):
        from coverage_gate import assert_autofill_symbol_gate
        self._write_scan([{'symbol': 'ETHUSDT', 'market': 'USDⓈ-M', 'price': 1, 'markPrice': 1}])
        gate = assert_autofill_symbol_gate(self.root, 'MISSINGUSDT', warmup_bars=20)
        self.assertFalse(gate['ok'])
        self.assertTrue(gate.get('backfill_hint'))



class WarmupBypassHardeningTests(unittest.TestCase):
    """V1: request must not lower warmup; F07 partial/断档 must not pass hard gates."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def _write_scan(self, rows, **extra):
        scan = {
            'scan_id': 'scan-w',
            'revision': 'rev-w',
            'total': extra.pop('total', len(rows)),
            'received': extra.pop('received', len(rows)),
            'rows': rows,
            'quality': extra.pop('quality', {'status': 'success'}),
        }
        scan.update(extra)
        (self.root / 'scan.json').write_text(json.dumps(scan), encoding='utf-8')

    def test_resolve_warmup_cannot_lower_below_floor(self):
        from coverage_gate import resolve_warmup_bars, MIN_WARMUP_BARS
        eff, meta = resolve_warmup_bars(1, None)
        self.assertEqual(eff, MIN_WARMUP_BARS)
        self.assertTrue(meta['refused_lower'])
        eff2, meta2 = resolve_warmup_bars(5, 40)
        self.assertEqual(eff2, 40)
        self.assertTrue(meta2['refused_lower'])
        eff3, meta3 = resolve_warmup_bars(50, 40)
        self.assertEqual(eff3, 50)
        self.assertFalse(meta3['refused_lower'])

    def test_autofill_gate_409_when_request_lowers_warmup(self):
        from coverage_gate import assert_autofill_symbol_gate
        self._write_scan([{'symbol': 'ETHUSDT', 'market': 'USDⓈ-M', 'price': 1, 'markPrice': 1}])
        gate = assert_autofill_symbol_gate(
            self.root, 'ETHUSDT', warmup_bars=1, frozen_min_warmup=20,
        )
        self.assertFalse(gate['ok'])
        self.assertIn('降低预热', gate['reason'])
        self.assertEqual(gate.get('http_status_hint'), 409)

    def test_f07_partial_fails_baseline_and_scan_research(self):
        """F07: expected>received → partial must NOT pass baseline / scan_and_research."""
        from coverage_gate import assert_baseline_coverage, assert_scan_research_gate, evaluate_scan_fields
        rows = [
            {'symbol': 'AAAUSDT', 'market': 'USDⓈ-M', 'price': 1.0, 'markPrice': 1.0},
        ]
        self._write_scan(rows, total=10, received=1, quality={'status': 'partial_success'})
        report = evaluate_scan_fields(self.root)
        by_id = {f['id']: f for f in report['fields']}
        self.assertNotEqual(by_id['price']['status'], 'covered')
        base = assert_baseline_coverage(self.root)
        self.assertFalse(base['ok'])
        self.assertTrue(
            any('partial' in x or 'expected_gt_received' in x or 'shortfall' in x
                for x in (base.get('critical_failures') or [])),
            base,
        )
        gate = assert_scan_research_gate(
            self.root,
            {'total': 10, 'received': 6, 'quality': {'status': 'partial_success'}},
        )
        # even if caller lies received=6 (>=50%), scan.json still F07 partial → refuse
        self.assertFalse(gate['ok'])
        self.assertTrue('F07' in gate['reason'] or 'covered' in gate['reason'] or 'expected' in gate['reason'])

    def test_f07_partial_funding_cannot_pass_deps_or_soften_status(self):
        from rule_coverage_gate import assert_deps_covered
        rows = [
            {'symbol': 'AAAUSDT', 'market': 'USDⓈ-M', 'price': 1.0, 'markPrice': 1.0,
             'fundingAprPct': 1.0},
        ]
        # universe 10, only 1 received → fundingAprPct partial
        self._write_scan(rows, total=10, received=1)
        gate = assert_deps_covered(self.root, ['funding', 'snapshot'])
        self.assertFalse(gate['ok'])
        self.assertIn('funding', gate['missing_dependencies'])
        # cannot soften via require_status=partial
        gate2 = assert_deps_covered(self.root, ['funding'], require_status='partial')
        self.assertEqual(gate2.get('require_status'), 'covered')
        self.assertFalse(gate2['ok'])

    def test_continuity_gap_refuses_even_with_enough_bars(self):
        from unittest import mock
        from coverage_gate import assert_autofill_symbol_gate, evaluate_symbol_coverage
        self._write_scan([{'symbol': 'GAPUSDT', 'market': 'USDⓈ-M', 'price': 1, 'markPrice': 1}])
        # 24 bars with a hole in the middle
        step = 4 * 3600 * 1000
        t0 = 1_700_000_000_000
        bars = [[t0 + i * step, 1, 1, 1, 1, 1] for i in range(12)]
        # inject gap: jump 3 steps
        bars += [[t0 + (12 + 3 + i) * step, 1, 1, 1, 1, 1] for i in range(12)]
        fake = {
            'current_available': True,
            'bars': bars,
        }
        with mock.patch('coverage_gate.merge_series', create=True):
            # patch where used — evaluate_symbol_coverage imports merge_series inside
            with mock.patch('price_index.merge_series', return_value=fake):
                with mock.patch('price_index.summarize_price_index', return_value={'series': []}):
                    sym = evaluate_symbol_coverage(
                        self.root, 'GAPUSDT', warmup_bars=20,
                    )
                    self.assertFalse(sym['ok'])
                    self.assertIn('断档', sym['reason'])
                    gate = assert_autofill_symbol_gate(
                        self.root, 'GAPUSDT', warmup_bars=20, frozen_min_warmup=20,
                    )
                    self.assertFalse(gate['ok'])
                    self.assertIn('断档', gate['reason'])



if __name__ == '__main__':
    unittest.main()
