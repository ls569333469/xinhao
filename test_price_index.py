"""Offline tests for DATA-01 price index merge, hash, conflict, continuity, snapshots."""
import datetime as dt
import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from candle_ranges import STEPS, CandleRanges, date, series_name
from price_index import (
    continuity,
    list_snapshot_revisions,
    merge_series,
    preflight_merged,
    summarize_price_index,
)
from snapshot_store import encoded, atomic

CONTRACT = {'market': 'USDⓈ-M', 'symbol': 'TESTUSDT'}
START = int(dt.datetime(2026, 1, 1, tzinfo=dt.timezone.utc).timestamp() * 1000)
NOW = dt.datetime(2026, 10, 13, tzinfo=dt.timezone.utc)


def bar(t, step=STEPS['4h'], price=100):
    return [t, str(price), str(price + 1), str(price - 1), str(price), '10',
            t + step - 1, '1000', 3, '6', '600', '0']


def write_scan_candles(root, scan_id, contract, interval, bars, revision=1):
    folder = root / 'scan-history' / scan_id
    (folder / 'candles').mkdir(parents=True)
    available = '2026-06-01T00:00:00+00:00'
    payload = {
        **contract, 'interval': interval, 'source': 'Binance public REST',
        'retrieved_at': available, 'available_at': available,
        'source_time': bars[-1][6] if bars else None, 'count': len(bars),
        'scope': 'returned closed window; not verified lifetime ATH', 'raw': bars,
    }
    raw = encoded(payload)
    name = hashlib.sha256((contract['market'] + '|' + contract['symbol']).encode()).hexdigest() + f'-{interval}.json'
    atomic(folder / 'candles' / name, raw)
    atomic(folder / 'status.json', encoded({
        'scan_id': scan_id, 'status': 'success', 'revision': revision,
        'startedAt': available, 'updatedAt': available,
        'quality': {'accepted': True, 'status': 'success', 'ratios': {'rows': 1.0}},
    }))
    rev = {
        'scan_id': scan_id, 'revision': revision, 'available_at': available,
        'startedAt': available, 'finishedAt': available, 'received': 1, 'total': 1,
        'rows': [contract], 'quality': {'accepted': True, 'status': 'success', 'ratios': {'rows': 1.0}},
        'schemaVersion': 2, 'candidateCount': 0,
    }
    with (folder / f'revision-{revision:04d}.json').open('xb') as out:
        out.write(encoded(rev))
    return _sha(raw)


def _sha(raw):
    return hashlib.sha256(raw).hexdigest()


class ContinuityTests(unittest.TestCase):
    def test_continuity_gaps_and_trailing(self):
        step = STEPS['4h']
        times = [START, START + step, START + 3 * step]
        result = continuity(times, step)
        self.assertFalse(result['continuous'])
        self.assertEqual(result['gap_slots'], 1)
        self.assertEqual(result['trailing_continuous'], 1)
        self.assertEqual(result['longest_continuous'], 2)
        full = [START + i * step for i in range(5)]
        ok = continuity(full, step)
        self.assertTrue(ok['continuous'])
        self.assertEqual(ok['trailing_continuous'], 5)
        self.assertEqual(continuity([], step)['span_bars'], 0)


class PriceIndexTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        (self.root / 'scan.json').write_text(json.dumps({'rows': [CONTRACT], 'total': 1}), encoding='utf-8')

    def tearDown(self):
        self.tmp.cleanup()

    def test_merge_scan_and_range_with_hash_and_conflict(self):
        step = STEPS['4h']
        scan_bars = [bar(START + i * step) for i in range(4)]
        write_scan_candles(self.root, 'scan-' + 'a' * 32, CONTRACT, '4h', scan_bars)

        ranges = CandleRanges(self.root, lambda u, p: [bar(t, price=100) for t in range(p['startTime'], p['endTime'] + 1, step)],
                              lambda: NOW)
        # range covers bars 2..5 (overlap 2,3 with scan; add 4,5)
        spec = ranges.prepare({
            'contracts': [CONTRACT], 'interval': '4h',
            'start': date(START + 2 * step), 'end': date(START + 6 * step),
        })
        manifest = ranges.run(spec)
        detail = merge_series(self.root, CONTRACT['market'], CONTRACT['symbol'], '4h',
                              start=date(START), end=date(START + 6 * step))
        self.assertEqual(detail['known'], 6)
        self.assertTrue(detail['complete'])
        self.assertEqual(detail['conflicts'], 0)
        self.assertTrue(any(e['source_kind'] == 'scan_window' for e in detail['evidence']))
        self.assertTrue(any(e['source_kind'] == 'candle_range' for e in detail['evidence']))
        self.assertEqual(detail['label_current'], '当前可得')
        self.assertTrue(detail['continuity']['continuous'])

        # corrupt range hash by rewriting file without updating manifest expectation path:
        # write conflicting prices into a second range
        ranges.request = lambda u, p: [bar(t, price=200) for t in range(p['startTime'], p['endTime'] + 1, step)]
        ranges.run(spec)
        conflicted = merge_series(self.root, CONTRACT['market'], CONTRACT['symbol'], '4h',
                                  start=date(START + 2 * step), end=date(START + 6 * step))
        self.assertGreater(conflicted['conflicts'], 0)
        self.assertEqual(conflicted['known'], 0)

        # hash failure: tamper file bytes after success archive
        folder = self.root / 'candle-history' / manifest['range_id']
        target = folder / series_name(CONTRACT, '4h')
        target.write_bytes(b'{"raw":[]}')
        broken = merge_series(self.root, CONTRACT['market'], CONTRACT['symbol'], '4h',
                              start=date(START), end=date(START + 6 * step))
        self.assertTrue(any('归档校验失败' in e.get('error', '') for e in broken['errors']))

    def test_missing_history_not_marked_available(self):
        detail = merge_series(self.root, CONTRACT['market'], CONTRACT['symbol'], '4h',
                              start=date(START), end=date(START + STEPS['1d']))
        self.assertEqual(detail['known'], 0)
        self.assertEqual(detail['label_current'], '当前不可得')
        self.assertFalse(detail['complete'])
        summary = summarize_price_index(self.root)
        self.assertEqual(summary['total_series'], 0)
        self.assertIn('不证明', summary['scope'])

    def test_snapshot_revision_browse_real_disk_only(self):
        write_scan_candles(self.root, 'scan-' + 'b' * 32, CONTRACT, '1d',
                           [bar(START + i * STEPS['1d'], STEPS['1d']) for i in range(3)], revision=1)
        # second revision file
        folder = self.root / 'scan-history' / ('scan-' + 'b' * 32)
        rev2 = {
            'scan_id': 'scan-' + 'b' * 32, 'revision': 2,
            'available_at': '2026-07-01T00:00:00+00:00', 'received': 1, 'total': 1,
            'rows': [CONTRACT], 'quality': {'accepted': True, 'status': 'partial_success', 'ratios': {'rows': 1.0}},
            'schemaVersion': 2,
        }
        with (folder / 'revision-0002.json').open('xb') as out:
            out.write(encoded(rev2))
        listed = list_snapshot_revisions(self.root)
        self.assertEqual(listed['total_found'], 2)
        ids = {(s['scan_id'], s['revision']) for s in listed['snapshots']}
        self.assertIn(('scan-' + 'b' * 32, 1), ids)
        self.assertIn(('scan-' + 'b' * 32, 2), ids)
        self.assertTrue(all(s.get('content_sha256') for s in listed['snapshots']))
        # empty root invents nothing
        empty = Path(tempfile.mkdtemp())
        try:
            none = list_snapshot_revisions(empty)
            self.assertEqual(none['snapshots'], [])
        finally:
            import shutil
            shutil.rmtree(empty)

    def test_summarize_and_preflight_merged(self):
        step = STEPS['4h']
        write_scan_candles(self.root, 'scan-' + 'c' * 32, CONTRACT, '4h',
                           [bar(START + i * step) for i in range(6)])
        summary = summarize_price_index(self.root, interval='4h')
        self.assertEqual(summary['total_series'], 1)
        self.assertEqual(summary['series'][0]['label_current'], '当前可得')
        self.assertEqual(summary['series'][0]['approx_bars_scan'], 6)
        result = preflight_merged(self.root, {
            'contracts': [CONTRACT], 'interval': '4h',
            'start': date(START), 'end': date(START + STEPS['1d']),
        }, clock=lambda: NOW)
        self.assertTrue(result['complete'])
        self.assertEqual(result['series'][0]['known'], 6)
        self.assertIn('扫描窗口', result['scope'])


if __name__ == '__main__':
    unittest.main()
