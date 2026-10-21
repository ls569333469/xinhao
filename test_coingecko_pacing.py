import concurrent.futures
import datetime as dt
import io
import threading
import time
import unittest
import urllib.error
from email.utils import format_datetime
from unittest.mock import patch

import scanner
from request_pacer import RequestPacer


class FakeClock:
    def __init__(self):
        self.now = 0.
        self.wall = 1_800_000_000.

    def sleep(self, seconds):
        self.now += seconds

    def pacer(self):
        return RequestPacer(5, .9, clock=lambda: self.now, sleep=self.sleep,
                            wall_clock=lambda: self.wall)


class PacingTests(unittest.TestCase):
    def test_seven_calls_cannot_burst_in_a_minute(self):
        clock = FakeClock()
        pacer = clock.pacer()
        starts = []
        for _ in range(7):
            pacer.wait()
            starts.append(clock.now)
        self.assertAlmostEqual(starts[-1], 80.)
        self.assertTrue(all(b-a >= 13.333333333 - 1e-8 for a, b in zip(starts, starts[1:])))
        self.assertLessEqual(max(sum(t <= v < t+60 for v in starts) for t in starts), 5)

    def test_long_idle_does_not_accumulate_burst_tokens(self):
        clock = FakeClock()
        pacer = clock.pacer()
        pacer.wait()
        clock.now = 1000
        pacer.wait()
        pacer.wait()
        self.assertAlmostEqual(clock.now, 1013.333333333333)

    def test_retry_after_seconds_and_lower_future_rate(self):
        clock = FakeClock()
        pacer = clock.pacer()
        pacer.wait()
        self.assertEqual(pacer.rate_limited('90'), 90)
        pacer.wait()
        self.assertEqual(clock.now, 90)
        pacer.wait()
        self.assertAlmostEqual(clock.now, 116.66666666666667)

    def test_retry_after_http_date(self):
        clock = FakeClock()
        pacer = clock.pacer()
        date = dt.datetime.fromtimestamp(clock.wall+150, dt.timezone.utc)
        self.assertEqual(pacer.rate_limited(format_datetime(date, usegmt=True)), 150)
        pacer.wait()
        self.assertEqual(clock.now, 150)

    def test_bad_past_and_nonfinite_retry_after_use_safe_cooldown(self):
        for value in (None, 'nonsense', '-9', 'nan', 'inf', 'Wed, 21 Oct 2015 07:28:00 GMT'):
            with self.subTest(value=value):
                clock = FakeClock()
                pacer = clock.pacer()
                self.assertEqual(pacer.rate_limited(value), 60)
                pacer.wait()
                self.assertEqual(clock.now, 60)

    def test_new_cooldown_while_waiting_is_rechecked(self):
        clock = FakeClock()
        pacer = clock.pacer()
        pacer.wait()
        def sleep(seconds):
            clock.sleep(seconds)
            pacer._sleep = clock.sleep
            pacer.rate_limited('120')
        pacer._sleep = sleep
        pacer.wait()
        self.assertAlmostEqual(clock.now, 133.33333333333334)

    def test_concurrent_dispatches_are_spaced(self):
        pacer = RequestPacer(6000, 1)
        starts = []
        lock = threading.Lock()
        def run(_):
            pacer.wait()
            with lock:
                starts.append(time.monotonic())
        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
            list(pool.map(run, range(12)))
        starts.sort()
        self.assertGreaterEqual(starts[-1] - starts[0], .105)


class ScannerIntegrationTests(unittest.TestCase):
    def test_requests_and_server_error_retry_share_provider_pacing(self):
        clock = FakeClock()
        starts = []
        def response(req, **kwargs):
            starts.append(clock.now)
            if len(starts) == 1:
                raise urllib.error.HTTPError(req.full_url, 500, 'failed', {}, None)
            return io.StringIO('[]')
        with patch.object(scanner, 'COINGECKO_PACER', clock.pacer()), \
                patch.object(scanner.urllib.request, 'urlopen', side_effect=response), \
                patch.object(scanner.time, 'sleep', side_effect=clock.sleep):
            scanner.request('https://api.coingecko.com/api/v3/coins/list')
            scanner.request('https://api.coingecko.com/api/v3/coins/markets')
        self.assertEqual(len(starts), 3)
        self.assertAlmostEqual(starts[1], 60/4.5)
        self.assertAlmostEqual(starts[2], 120/4.5)

    def test_429_does_not_retry_and_blocks_later_calls(self):
        clock = FakeClock()
        calls = []
        def response(req, **kwargs):
            calls.append(clock.now)
            if len(calls) == 1:
                raise urllib.error.HTTPError(req.full_url, 429, 'limited', {'Retry-After': '120'}, None)
            return io.StringIO('[]')
        with patch.object(scanner, 'COINGECKO_PACER', clock.pacer()), \
                patch.object(scanner.urllib.request, 'urlopen', side_effect=response):
            with self.assertRaisesRegex(RuntimeError, 'cooldown 120s'):
                scanner.request('https://api.coingecko.com/api/v3/coins/list')
            self.assertEqual(len(calls), 1)
            scanner.request('https://api.coingecko.com/api/v3/coins/list')
        self.assertEqual(calls, [0, 120])

    def test_binance_does_not_wait_for_coingecko_cooldown(self):
        clock = FakeClock()
        pacer = clock.pacer()
        pacer.rate_limited('120')
        with patch.object(scanner, 'COINGECKO_PACER', pacer), \
                patch.object(scanner, 'NEXT', 0), \
                patch.object(scanner.urllib.request, 'urlopen', return_value=io.StringIO('[]')):
            scanner.request('https://fapi.binance.com/fapi/v1/exchangeInfo')
        self.assertEqual(clock.now, 0)

    def test_coin_list_rate_limit_skips_remaining_provider_and_uses_fallback(self):
        urls = []
        def safe(url, params=None):
            urls.append(url)
            if 'coingecko' in url:
                return None, 'HTTP 429: rate limited; cooldown 60s'
            return [{'id': 'fallback', 'last_updated': scanner.now()}], None
        with patch.object(scanner, 'safe', side_effect=safe):
            _, markets, paprika, errors = scanner.load_caps()
        self.assertEqual(len(urls), 2)
        self.assertEqual(markets, [])
        self.assertEqual([c['id'] for c in paprika], ['fallback'])
        self.assertIn('429', errors[0])

    def test_page_rate_limit_keeps_completed_pages_and_fallback(self):
        pages = []
        def safe(url, params=None):
            if url.endswith('/coins/list'):
                return [{'id':'coin'}], None
            if params:
                pages.append(params['ids'])
                if len(pages) == 2:
                    return None, 'HTTP 429: rate limited; cooldown 60s'
                return [dict(id=i, market_cap=200., current_price=2., circulating_supply=100., last_updated=scanner.now()) for i in params['ids'].split(',')], None
            return [{'id':'fallback', 'last_updated':scanner.now()}], None
        with patch.object(scanner, 'safe', side_effect=safe):
            coins, markets, paprika, errors = scanner.load_caps()
        self.assertEqual(len(pages), 2)
        self.assertFalse(set(pages[0].split(',')) & set(pages[1].split(',')))
        self.assertEqual(len(markets), 250)
        self.assertEqual(len(paprika), 1)
        self.assertIn('batch 2', errors[0])


if __name__ == '__main__':
    unittest.main()
