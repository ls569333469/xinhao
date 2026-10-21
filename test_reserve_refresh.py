import datetime as dt
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from ai_research import AiResearch
from reserve_refresh import refresh, for_row
from snapshot_store import ScanArchive
from test_scan_contract import row

UTC = dt.timezone.utc


def source(at):
    end = int(at.replace(hour=0, minute=0, second=0, microsecond=0).timestamp())
    return {'name': 'Binance CEX', 'chainTvls': {'Bitcoin': {'tokens': [
        {'date': end - (7-i)*86400, 'tokens': {'BTC': 100-i}} for i in range(8)]}}}


class RefreshTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.at = dt.datetime(2026, 10, 15, 10, tzinfo=UTC)
        self.scan = {'rows': [row(), row('BTCUSDC'), dict(row('MAGICUSDT'), asset='MAGIC')], 'total': 3}
        self.fetch = Mock(return_value=source(self.at))

    def tearDown(self):
        self.temp.cleanup()

    def run_refresh(self, at=None, fetch=None):
        return refresh(self.root, self.scan, at=at or self.at, fetch=fetch or self.fetch)

    def test_one_request_serves_multiple_contracts_and_never_fills_exact_flow(self):
        result = self.run_refresh()
        self.assertEqual(self.fetch.call_count, 1)
        self.assertEqual(result['status'], 'success')
        self.assertEqual(result['contractReferences'], 2)
        self.assertEqual(result['freshAssets'], 1)
        self.assertEqual(result['uncoveredAssets'], ['MAGIC'])
        self.assertEqual(result['exactNetflowCoverage'], 0)
        self.assertFalse(result['records'][0]['eligibleForExactNetflow'])
        self.assertNotIn('chainFlowUsd', self.scan['rows'][0])

    def test_same_complete_day_reuses_original_retrieval_time(self):
        first = self.run_refresh()
        second = self.run_refresh(self.at + dt.timedelta(hours=5))
        self.assertEqual(self.fetch.call_count, 1)
        self.assertTrue(second['cacheUsed'])
        self.assertFalse(second['attemptedThisScan'])
        self.assertEqual(second['retrievedAt'], first['retrievedAt'])
        self.assertNotEqual(second['checkedAt'], first['checkedAt'])

    def test_cross_midnight_refreshes_even_inside_retry_window(self):
        before = self.at.replace(hour=23, minute=55)
        self.run_refresh(before)
        after = before + dt.timedelta(minutes=10)
        self.fetch.return_value = source(after)
        result = self.run_refresh(after)
        self.assertEqual(self.fetch.call_count, 2)
        self.assertEqual(result['records'][0]['asOf'], int(after.replace(hour=0, minute=0).timestamp()))

    def test_failure_keeps_last_evidence_but_expires_and_throttles(self):
        first = self.run_refresh()
        later = self.at + dt.timedelta(days=2)
        fail = Mock(side_effect=TimeoutError('offline timeout'))
        second = self.run_refresh(later, fail)
        self.assertEqual(second['status'], 'update_failed')
        self.assertIn('timeout', second['updateError'])
        self.assertEqual(second['retrievedAt'], first['retrievedAt'])
        self.assertEqual(second['freshAssets'], 0)
        self.assertFalse(second['records'][0]['freshNow'])
        third = self.run_refresh(later + dt.timedelta(minutes=5), fail)
        self.assertEqual(fail.call_count, 1)
        self.assertFalse(third['attemptedThisScan'])
        self.assertEqual(third['updateError'], second['updateError'])
        self.run_refresh(later + dt.timedelta(hours=1), fail)
        self.assertEqual(fail.call_count, 2)

    def test_initial_failure_and_schema_failure_do_not_fabricate_coverage(self):
        for data in (None, {}, {'name': 'homonym', 'chainTvls': {}}, {'name': 'Binance CEX', 'chainTvls': {}}):
            with self.subTest(data=data), tempfile.TemporaryDirectory() as tmp:
                result = refresh(tmp, self.scan, at=self.at, fetch=lambda: data)
                self.assertEqual(result['status'], 'failed')
                self.assertEqual(result['records'], [])
                self.assertEqual(result['contractReferences'], 0)

    def test_corrupt_cache_is_ignored_and_write_failure_is_visible(self):
        path = self.root/'runtime/reserve-refresh.json'
        path.parent.mkdir()
        path.write_text('{"lastSuccess":["bad schema"]}', encoding='utf-8')
        with patch('reserve_refresh.atomic', side_effect=OSError('read only cache')):
            result = self.run_refresh()
        self.assertEqual(result['status'], 'success')
        self.assertEqual(result['freshAssets'], 1)
        self.assertIn('read only cache', result['cacheError'])

    def test_missing_and_duplicate_days_rejected(self):
        for duplicate in (True, False):
            data = source(self.at)
            points = data['chainTvls']['Bitcoin']['tokens']
            if duplicate:
                points.append(dict(points[-1]))
            else:
                points.pop(3)
            with tempfile.TemporaryDirectory() as tmp:
                result = refresh(tmp, self.scan, at=self.at, fetch=lambda: data)
                self.assertEqual(result['status'], 'failed')
                self.assertFalse(result['records'])

    def test_future_partial_day_ignored_and_zero_not_used(self):
        data = source(self.at)
        points = data['chainTvls']['Bitcoin']['tokens']
        points.append({'date': int(self.at.timestamp())+86400, 'tokens': {'BTC': 1}})
        result = self.run_refresh(fetch=lambda: data)
        self.assertEqual(result['records'][0]['reserveTokens'], 93)
        points[-2]['tokens']['BTC'] = 0
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(refresh(tmp, self.scan, at=self.at, fetch=lambda: data)['status'], 'failed')

    def test_provider_date_regression_keeps_previous(self):
        first = self.run_refresh()
        later = self.at + dt.timedelta(days=1)
        self.fetch.return_value = source(self.at - dt.timedelta(days=1))
        second = self.run_refresh(later)
        self.assertEqual(second['status'], 'update_failed')
        self.assertEqual(second['records'][0]['asOf'], first['records'][0]['asOf'])

    def test_changed_universe_rechecks_and_counts_current_contracts(self):
        self.run_refresh()
        self.scan['rows'].append(dict(row('SOLUSDT'), asset='SOL'))
        result = self.run_refresh(self.at + dt.timedelta(minutes=5))
        self.assertEqual(self.fetch.call_count, 2)
        self.assertEqual(result['status'], 'partial_success')
        self.assertIn('SOL', result['uncoveredAssets'])

    def test_failed_scan_does_not_publish_evidence_or_mutate_old_revision(self):
        archive = ScanArchive(self.root)
        archive.record(row())
        evidence = self.run_refresh()
        archive.publish({'rows': [row()], 'total': 1, 'reserveEvidence': evidence})
        before = (self.root/'scan.json').read_bytes()
        original = (archive.path/'revision-0001.json').read_bytes()
        new = ScanArchive(self.root)
        with self.assertRaises(ValueError):
            new.publish({'rows': [], 'total': 100, 'reserveEvidence': {'records': []}})
        self.assertEqual((self.root/'scan.json').read_bytes(), before)
        self.assertEqual((archive.path/'revision-0001.json').read_bytes(), original)

    def test_backend_evidence_matches_snapshot_and_hash_changes_with_reserves(self):
        snapshot = dict(self.scan, reserveEvidence=self.run_refresh())
        research = object.__new__(AiResearch)
        research.clock = lambda: self.at
        cleaned = [{'symbol': 'BTCUSDT', 'market': 'USDⓈ-M'}]
        first = research._slim_evidence_rows(cleaned, snapshot)
        ev = first[0]['reserveEvidence']
        self.assertAlmostEqual(ev['record']['weekChangePct'], -7)
        self.assertFalse(ev['exactNetflow'])
        research.clock = lambda: self.at + dt.timedelta(seconds=5)
        second = research._slim_evidence_rows(cleaned, snapshot)
        self.assertEqual(research._evidence_hash(first), research._evidence_hash(second))
        snapshot['reserveEvidence']['records'][0]['dayChangePct'] = -5
        third = research._slim_evidence_rows(cleaned, snapshot)
        self.assertNotEqual(research._evidence_hash(first), research._evidence_hash(third))
        expired = for_row(snapshot['reserveEvidence'], row(), self.at+dt.timedelta(days=2))
        self.assertFalse(expired['freshNow'])


if __name__ == '__main__':
    unittest.main()
