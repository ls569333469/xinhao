"""Public interval ingestion, evidence and mixed queue contracts; all sources fake."""
import datetime as dt
import json
import sqlite3
import tempfile
import threading
import unittest
from pathlib import Path

from candle_ranges import CandleRanges, STEPS, date, timestamp
from snapshot_store import ScanLease
from task_service import TaskService
import test_task_service as task_tests

CONTRACT = {'market': 'USDⓈ-M', 'symbol': 'TESTUSDT'}
COIN = {'market': 'COIN-M', 'symbol': 'TESTUSD_PERP'}
START = timestamp('2026-01-01T00:00:00Z')
NOW = dt.datetime(2026, 10, 13, tzinfo=dt.timezone.utc)


def bar(t, step=STEPS['4h'], price=100):
    return [t, str(price), str(price + 1), str(price - 1), str(price), '10',
            t + step - 1, '1000', 3, '6', '600', '0']


def source(url, params):
    step = STEPS[params['interval']]
    return [bar(t, step) for t in range(params['startTime'], params['endTime'] + 1, step)]


def payload(days=1, contracts=None, interval='4h', start=START):
    return {'contracts': contracts or [CONTRACT], 'interval': interval,
            'start': date(start), 'end': date(start + days * STEPS['1d'])}


def snapshot(root):
    (root / 'scan.json').write_text(json.dumps({'rows': [CONTRACT, COIN], 'total': 2}), encoding='utf-8')


class RangeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        snapshot(self.root)
        self.calls = []
        self.ranges = CandleRanges(self.root, self.fetch, lambda: NOW)

    def tearDown(self):
        self.tmp.cleanup()

    def fetch(self, url, params):
        self.calls.append((url, dict(params)))
        return source(url, params)

    def run_range(self, value=None):
        return self.ranges.run(self.ranges.prepare(value or payload()))

    def test_preflight_is_read_only_and_no_network(self):
        result = self.ranges.preflight(payload())
        self.assertFalse(result['complete'])
        self.assertEqual(result['series'][0]['missing'], 6)
        self.assertEqual(self.calls, [])
        self.assertFalse((self.root / 'candle-history').exists())

    def test_unicode_contracts_supported_only_when_in_snapshot(self):
        chinese = {'market': 'USDⓈ-M', 'symbol': '龙虾USDT'}
        (self.root / 'scan.json').write_text(json.dumps({'rows': [chinese]}, ensure_ascii=False), encoding='utf-8')
        self.assertEqual(self.ranges.prepare(payload(contracts=[chinese]))['contracts'], [chinese])
        self.assertEqual(self.ranges.preflight(payload(contracts=[chinese]))['series'][0]['expected'], 6)
        with self.assertRaises(ValueError):
            self.ranges.prepare(payload(contracts=[{**chinese, 'symbol': '../龙虾USDT'}]))

    def test_pagination_exact_bounds_200_day_coin_and_no_skipped_bars(self):
        result = self.run_range(payload(200))
        self.assertEqual(result['status'], 'success')
        self.assertEqual(result['known'], 1200)
        self.assertEqual(len(self.calls), 3)
        self.assertTrue(all(p['limit'] == 500 and p['endTime'] - p['startTime'] < 500 * STEPS['4h'] for _, p in self.calls))
        self.calls.clear()
        result = self.run_range(payload(250, [COIN], '1d'))
        self.assertEqual(result['known'], 250)
        self.assertEqual(len(self.calls), 2)
        self.assertTrue(all(url.startswith('https://dapi.binance.com/') and p['endTime'] - p['startTime'] < 200 * STEPS['1d'] for url, p in self.calls))

    def test_unaligned_requested_bounds_exclude_partial_bars(self):
        value = payload(start=START + 1)
        result = self.run_range(value)
        self.assertEqual(result['known'], 5)
        self.assertEqual(result['request']['effective_start'], date(START + STEPS['4h']))
        self.assertEqual(result['request']['effective_end'], date(START + STEPS['1d']))

    def test_gaps_empty_pages_partial_no_zero_filling(self):
        self.ranges.request = lambda url, p: source(url, p)[1:-1]
        result = self.run_range()
        item = result['series'][0]
        self.assertEqual(result['status'], 'partial_success')
        self.assertEqual(item['known'], 4)
        self.assertEqual(item['gap_count'], 2)
        self.ranges.request = lambda url, p: []
        result = self.run_range(payload(200))
        self.assertEqual(result['status'], 'failed')
        self.assertEqual(result['known'], 0)
        self.assertEqual(result['series'][0]['gaps'][0]['bars'], 1200)

    def test_invalid_nonfinite_unclosed_outside_bars_excluded(self):
        raw = source('', {'interval': '4h', 'startTime': START, 'endTime': START + STEPS['1d'] - 1})
        raw[0][2] = 'NaN'
        raw[1][3] = '200'  # low above close
        raw[2][6] += 1
        raw[3][0] += 1
        raw.append(bar(START - STEPS['4h']))
        self.ranges.request = lambda u, p: raw
        result = self.run_range()
        self.assertEqual(result['known'], 2)
        self.assertEqual(result['status'], 'partial_success')
        self.assertTrue(result['series'][0]['errors'])

    def test_identical_duplicates_dedup_conflicts_unknown(self):
        self.ranges.request = lambda u, p: [*source(u, p), *source(u, p)]
        self.assertEqual(self.run_range()['known'], 6)
        self.ranges.request = lambda u, p: [*source(u, p), bar(START, price=200)]
        result = self.run_range()
        self.assertEqual(result['known'], 5)
        self.assertEqual(result['series'][0]['conflicts'], 1)
        checked = self.ranges.preflight(payload())['series'][0]
        self.assertEqual(checked['known'], 5)
        self.assertEqual(checked['conflicts'], 1)

    def test_source_error_and_rate_limit_stop_subsequent_requests(self):
        def failing(url, p):
            self.calls.append(p)
            raise RuntimeError('HTTP 429: rate limited')
        self.ranges.request = failing
        result = self.run_range(payload(250, [CONTRACT, COIN]))
        self.assertEqual(result['status'], 'failed')
        self.assertEqual(len(self.calls), 1)
        self.assertTrue(all(i['missing'] == 1500 for i in result['series']))

    def test_lock_rejects_cli_overlap_without_archive_or_lock_steal(self):
        with ScanLease(self.root):
            with self.assertRaises(RuntimeError):
                self.run_range()
            self.assertTrue((self.root / 'scan.lock').exists())
        self.assertEqual(self.calls, [])

    def test_immutable_evidence_and_current_availability_no_snapshot_mutation(self):
        before = (self.root / 'scan.json').read_bytes()
        result = self.run_range()
        manifest = self.root / 'candle-history' / result['range_id'] / 'manifest.json'
        original = manifest.read_bytes()
        self.run_range()
        self.assertEqual(manifest.read_bytes(), original)
        self.assertEqual((self.root / 'scan.json').read_bytes(), before)
        self.assertEqual(result['available_at'], NOW.isoformat())
        self.assertGreater(timestamp(result['available_at']), START + STEPS['1d'])
        self.assertEqual(len(self.ranges.history()), 2)

    def test_overlapping_archive_union_and_conflicts_not_double_counted(self):
        self.run_range()
        self.run_range(payload(start=START + STEPS['1d']))
        result = self.ranges.preflight(payload(2))
        self.assertTrue(result['complete'])
        self.assertEqual(result['series'][0]['known'], 12)
        self.run_range()
        self.assertEqual(self.ranges.preflight(payload())['series'][0]['known'], 6)
        self.ranges.request = lambda u, p: [bar(t, price=200) for t in range(p['startTime'], p['endTime'] + 1, STEPS['4h'])]
        self.run_range()
        result = self.ranges.preflight(payload())['series'][0]
        self.assertEqual(result['conflicts'], 6)
        self.assertEqual(result['known'], 0)

    def test_corrupted_archive_not_counted(self):
        self.run_range()
        path = next((self.root / 'candle-history').glob('range-*/*-4h.json'))
        path.write_text('{}', encoding='utf-8')
        result = self.ranges.preflight(payload())['series'][0]
        self.assertEqual(result['known'], 0)
        self.assertTrue(result['errors'])

    def test_bad_input_types_limits_unknown_symbols_and_future(self):
        for changes in ({'interval': []}, {'interval': '1m'}, {'contracts': []}, {'contracts': [CONTRACT] * 21},
                        {'contracts': [CONTRACT, CONTRACT]}, {'contracts': [{'market': [], 'symbol': 'TESTUSDT'}]},
                        {'contracts': [{'market': 'USDⓈ-M', 'symbol': 'NOPEUSDT'}]},
                        {'start': '2026-01-01T00:00:00'}, {'end': '2099-01-01T00:00:00Z'},
                        {'end': date(START)}, {'start': date(START - 400 * STEPS['1d'])}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                self.ranges.prepare({**payload(), **changes})
        self.assertEqual(self.calls, [])


class MixedQueueTests(unittest.TestCase):
    setUp, tearDown, fetch = RangeTests.setUp, RangeTests.tearDown, RangeTests.fetch

    def test_old_sqlite_schema_migrates_without_changing_scan_settings(self):
        path = self.root / 'runtime' / 'workbench.sqlite3'
        path.parent.mkdir()
        from contextlib import closing
        with closing(sqlite3.connect(path)) as db, db:
            db.executescript('''CREATE TABLE tasks (
                id TEXT PRIMARY KEY, kind TEXT NOT NULL, status TEXT NOT NULL, reason TEXT NOT NULL,
                created_at TEXT NOT NULL, started_at TEXT, finished_at TEXT, progress TEXT NOT NULL,
                result TEXT, error TEXT, settings_version INTEGER NOT NULL);
                CREATE UNIQUE INDEX one_active_scan ON tasks(kind) WHERE status IN ('queued','running');''')
        service = TaskService(self.root, lambda: None, clock=lambda: NOW, ranges=self.ranges)
        try:
            before = service.settings()
            a = service.submit_range(payload())
            b = service.submit_range(payload(2))
            self.assertNotEqual(a['task']['id'], b['task']['id'])
            self.assertEqual(service.settings()['version'], before['version'])
            self.assertFalse(service.settings()['scan']['enabled'])
        finally:
            service.close()

    def test_concurrent_identical_range_requests_reuse_one_task(self):
        import concurrent.futures
        service = TaskService(self.root, lambda: None, clock=lambda: NOW, ranges=self.ranges)
        try:
            with concurrent.futures.ThreadPoolExecutor(max_workers=10) as pool:
                tasks = list(pool.map(lambda _: service.submit_range(payload()), range(20)))
            self.assertEqual(len({t['task']['id'] for t in tasks}), 1)
            self.assertEqual(sum(not t['reused'] for t in tasks), 1)
        finally:
            service.close()

    def test_mixed_serial_queue_dedupe_payload_and_cancel(self):
        service = TaskService(self.root, lambda: {'quality': {'status': 'success'}}, clock=lambda: NOW, ranges=self.ranges)
        gate = threading.Event()
        scan_started = threading.Event()
        def scan():
            scan_started.set()
            gate.wait(3)
            return {'quality': {'status': 'success'}, 'received': 1, 'total': 1}
        service.runner = scan
        try:
            scan_task = service.submit()['task']
            a = service.submit_range(payload())
            self.assertEqual(a['task']['kind'], 'candle_range')
            self.assertTrue(service.submit_range(payload())['reused'])
            b = service.submit_range(payload(2))
            self.assertFalse(b['reused'])
            service.cancel(b['task']['id'])
            service.tick()
            self.assertTrue(scan_started.wait(1))
            self.assertEqual(service.task(a['task']['id'])['status'], 'queued')
            self.assertEqual(self.calls, [])
            gate.set()
            service.worker.join(3)
            service.tick()
            service.worker.join(3)
            self.assertEqual(service.task(a['task']['id'])['status'], 'success')
            self.assertEqual(service.task(scan_task['id'])['status'], 'success')
            self.assertEqual(service.task(b['task']['id'])['status'], 'cancelled')
        finally:
            gate.set()
            service.close()
            if service.worker:
                service.worker.join(3)

    def test_restart_keeps_payload_marks_range_failed_no_automatic_requery(self):
        service = TaskService(self.root, lambda: None, clock=lambda: NOW, ranges=self.ranges)
        task = service.submit_range(payload())['task']
        service.close()
        reopened = TaskService(self.root, lambda: None, clock=lambda: NOW, ranges=self.ranges)
        try:
            self.assertEqual(reopened.task(task['id'])['status'], 'failed')
            self.assertEqual(reopened.task(task['id'])['request'], task['request'])
            reopened.tick()
            self.assertEqual(self.calls, [])
        finally:
            reopened.close()


class RangeHttpTests(unittest.TestCase):
    setUp, tearDown, request = task_tests.HttpTests.setUp, task_tests.HttpTests.tearDown, task_tests.HttpTests.request

    def test_preflight_submit_status_and_history(self):
        snapshot(self.root)
        self.service.ranges.request = source
        self.assertEqual(self.request('/api/candles/preflight', 'POST', payload())[0], 200)
        self.assertEqual(self.service.tasks(), [])
        status, raw = self.request('/api/candles/backfills', 'POST', payload())
        self.assertEqual(status, 202)
        task_id = json.loads(raw)['task']['id']
        self.service.tick()
        self.service.worker.join(3)
        self.assertEqual(self.service.task(task_id)['status'], 'success')
        self.assertEqual(json.loads(self.request('/api/candles/preflight', 'POST', payload())[1])['complete'], True)
        self.assertEqual(len(json.loads(self.request('/api/candles/history')[1])['ranges']), 1)

    def test_range_api_same_origin_private_archive_and_bad_shapes(self):
        snapshot(self.root)
        self.assertEqual(self.request('/api/candles/backfills', 'POST', payload(), {'Origin': 'https://evil.test'})[0], 403)
        for value in (None, [], {}, {**payload(), 'interval': {}}, {**payload(), 'contracts': [{'market': {}, 'symbol': 'X'}]}):
            self.assertEqual(self.request('/api/candles/preflight', 'POST', json.dumps(value))[0], 400)
        self.assertEqual(self.request('/candle-history/anything/manifest.json')[0], 404)
        self.assertEqual(self.service.tasks(), [])


if __name__ == '__main__':
    unittest.main()
