import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import scanner
from snapshot_store import ScanArchive, ScanLease, assess, aggregate_oi, clean
from strategy_registry import indicator_states


def row(symbol='BTCUSDT'):
    return {'market':'USDⓈ-M','symbol':symbol,'asset':'BTC','pair':symbol,'quote':'USDT',
            'oiUsd':10.0,'accountRatio':0.6,'closed4h':3.0,'ema4h':[4.0,5.0,6.0],
            'fundingAprPct':-70.0,'fundingPct':-0.02,'fundingHours':8,'volumeRatio':1.0,
            'priceChangeWindowPct':0.0,'errors':{},'fieldAvailableAt':{},'underlyingType':'COIN'}


class ScanContractTest(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.root=Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def test_failed_quality_preserves_previous_bytes(self):
        previous=b'{"previous":true}'
        (self.root/'scan.json').write_bytes(previous)
        archive=ScanArchive(self.root)
        archive.record(row())
        with self.assertRaises(ValueError): archive.publish({'rows':[row()],'total':20})
        self.assertEqual((self.root/'scan.json').read_bytes(),previous)
        self.assertEqual(json.loads((archive.path/'status.json').read_bytes())['status'],'failed')

    def test_duplicate_observations_are_idempotent_per_scan(self):
        first=ScanArchive(self.root)
        first.record(row());first.record(row())
        self.assertEqual(len((first.path/'observations.jsonl').read_text(encoding='utf-8').splitlines()),1)
        first.publish({'rows':[row()],'total':1})
        with self.assertRaises(RuntimeError):first.record(row())
        second=ScanArchive(self.root);second.record(row());second.publish({'rows':[row()],'total':1})
        self.assertNotEqual(first.scan_id,second.scan_id)
        self.assertEqual(len((self.root/'observations.jsonl').read_text(encoding='utf-8').splitlines()),1)
        self.assertTrue((first.path/'revision-0001.json').exists())

    def test_revisions_never_rewrite_history_or_availability(self):
        archive=ScanArchive(self.root);archive.record(row())
        archive.publish({'rows':[row()],'total':1,'finishedAt':'2026-10-12T12:07:37+00:00'})
        original=(archive.path/'revision-0001.json').read_bytes()
        reopened=ScanArchive.open(self.root,archive.scan_id)
        result=reopened.revise([{'market':'USDⓈ-M','symbol':'BTCUSDT','capUsd':50.0}])
        self.assertEqual((archive.path/'revision-0001.json').read_bytes(),original)
        self.assertEqual(result['revision'],2)
        self.assertEqual(result['rows'][0]['oiCapPct'],20.0)
        self.assertGreaterEqual(result['rows'][0]['fieldAvailableAt']['capUsd'],json.loads(original)['available_at'])
        self.assertEqual(result['finishedAt'],'2026-10-12T12:07:37+00:00')
        with self.assertRaises(ValueError): archive.revise([{'market':'USDⓈ-M','symbol':'OTHER','capUsd':50}])

    def test_missing_asset_oi_is_not_zero_or_complete(self):
        r=row();r['oiUsd']=None
        aggregate_oi([r],[{'asset':'BTC'}])
        self.assertIsNone(r['binanceAssetOiUsd'])
        self.assertFalse(r['assetOiCoverage']['complete'])
        r=row();aggregate_oi([r],[{'asset':'BTC'},{'asset':'BTC'}])
        self.assertEqual(r['binanceAssetOiPartialUsd'],10)
        self.assertIsNone(r['binanceAssetOiUsd'])

    def test_nonfinite_and_unknown_funding_are_unknown(self):
        for bad in ('nan','inf','-inf',float('nan')): self.assertIsNone(scanner.number(bad))
        self.assertEqual(clean({'oi':float('inf')}),{'oi':None})
        r=row();r['fundingIntervalStatus']='unknown'
        self.assertIsNone(indicator_states(r)[2])
        r['fundingIntervalStatus']='provider_default'
        self.assertTrue(indicator_states(r)[2])

    def test_lock_blocks_duplicate_and_releases_after_error(self):
        with self.assertRaises(RuntimeError):
            with ScanLease(self.root):
                with ScanLease(self.root): pass
        self.assertFalse((self.root/'scan.lock').exists())

    def test_directory_failure_resets_progress_and_keeps_latest(self):
        previous=b'{"old":true}';(self.root/'scan.json').write_bytes(previous)
        with patch.object(scanner,'ROOT',self.root),patch.object(scanner,'request',side_effect=OSError('offline')):
            with self.assertRaises(OSError):scanner.scan()
        self.assertFalse(scanner.PROGRESS['running'])
        self.assertEqual((self.root/'scan.json').read_bytes(),previous)
        self.assertFalse((self.root/'scan.lock').exists())

    def test_empty_directory_rejected(self):
        def fake_request(url,*args,**kwargs):
            return {'symbols':[]} if url.endswith('exchangeInfo') else []
        with patch.object(scanner,'ROOT',self.root),patch.object(scanner,'request',side_effect=fake_request):
            with self.assertRaises(ValueError):scanner.scan()
        self.assertFalse((self.root/'scan.json').exists())
        self.assertFalse(scanner.PROGRESS['running'])

    def test_offline_full_scan_produces_archived_and_latest_versions(self):
        def fake_request(url,*args,**kwargs):
            if url.endswith('exchangeInfo'):
                return {'symbols':[{'symbol':'BTCUSDT' if 'fapi' in url else 'BTCUSD_PERP','baseAsset':'BTC','quoteAsset':'USDT','pair':'BTCUSDT','underlyingType':'COIN','contractType':'PERPETUAL','status':'TRADING'}]}
            return []
        def fake_fetch(meta,host,*args):
            r=row(meta['symbol']);r['market']='USDⓈ-M' if 'fapi' in host else 'COIN-M'
            r['_candles']={'4h':[[0,'3','4','2','3','2',1,'6',1,'1','3','0']]}
            return r
        with patch.object(scanner,'ROOT',self.root),patch.object(scanner,'request',side_effect=fake_request),patch.object(scanner,'load_caps',return_value=([],[],[],[])),patch.object(scanner,'fetch_contract',side_effect=fake_fetch), patch('reserve_refresh.fetch_source',side_effect=TimeoutError('offline reserve probe')):
            result=scanner.scan()
        self.assertEqual(result['received'],2)
        self.assertEqual(result['reserveEvidence']['status'],'failed')
        self.assertIn('offline reserve probe',result['reserveEvidence']['updateError'])
        self.assertEqual(result['reserveEvidence']['exactNetflowCoverage'],0)
        self.assertEqual(result['quality']['status'],'partial_success')
        self.assertFalse(result['quality']['cap_complete'])
        self.assertTrue((self.root/'DOCS/report.md').exists())
        self.assertTrue(all((self.root/r['candleArchives']['4h']).exists() for r in result['rows']))
        self.assertTrue(all(r['assetOiCoverage']['complete'] for r in result['rows']))
        self.assertFalse(scanner.PROGRESS['running'])

    def test_fetch_period_provenance_and_percent_units(self):
        meta={'symbol':'BTCUSDT','baseAsset':'BTC','quoteAsset':'USDT','pair':'BTCUSDT','underlyingType':'COIN'}
        def fake_safe(url,params=None): return [],None
        with patch.object(scanner,'safe',side_effect=fake_safe):
            unknown=scanner.fetch_contract(meta,'https://fapi.binance.com',{}, {'lastFundingRate':'-0.001'},{})
            default=scanner.fetch_contract(meta,'https://fapi.binance.com',{}, {'lastFundingRate':'-0.001'},{'__source_ok__':True})
        self.assertAlmostEqual(unknown['fundingPct'],-0.1)
        self.assertIsNone(unknown['fundingAprPct'])
        self.assertEqual(default['fundingIntervalStatus'],'provider_default')
        self.assertAlmostEqual(default['fundingAprPct'],-109.5)

    def test_discontinuous_candles_archive_without_derived_values(self):
        meta={'symbol':'BTCUSDT','baseAsset':'BTC','quoteAsset':'USDT','pair':'BTCUSDT','underlyingType':'COIN'}
        raw=[[i*14_400_000,'3','4','2','3','2',(i+1)*14_400_000-1,'6',1,'1','3','0'] for i in range(121)]
        raw.pop(50)
        def fake_safe(url,params=None):return (raw if params and params.get('interval')=='4h' else []),None
        with patch.object(scanner,'safe',side_effect=fake_safe):
            result=scanner.fetch_contract(meta,'https://fapi.binance.com',{}, {},{})
        self.assertEqual(len(result['_candles']['4h']),120)
        self.assertNotIn('ema4h',result)
        self.assertIn('K线连续性',result['errors'])

    def test_old_run_cannot_override_new_latest(self):
        first=ScanArchive(self.root);first.record(row());first.publish({'rows':[row()],'total':1})
        second=ScanArchive(self.root);second.record(row());second.publish({'rows':[row()],'total':1})
        before=(self.root/'scan.json').read_bytes()
        with self.assertRaises(ValueError):first.revise([{'market':'USDⓈ-M','symbol':'BTCUSDT','capUsd':1}])
        self.assertEqual((self.root/'scan.json').read_bytes(),before)

    def test_first_publication_preserves_legacy_observations(self):
        old_scan=b'{"rows":[],"total":0}'
        old_observations=b'{"symbol":"legacy","asOf":"2026-10-12"}\n'
        (self.root/'scan.json').write_bytes(old_scan)
        (self.root/'observations.jsonl').write_bytes(old_observations)
        archive=ScanArchive(self.root);archive.record(row());archive.publish({'rows':[row()],'total':1})
        legacy_files=list((self.root/'scan-history/legacy').glob('*.jsonl'))
        self.assertEqual(len(legacy_files),1)
        self.assertEqual(legacy_files[0].read_bytes(),old_observations)

    def test_partial_core_field_coverage_rejected(self):
        rows=[row(str(i)) for i in range(10)]
        for r in rows[:2]:r['accountRatio']=None
        self.assertFalse(assess(rows,10)['accepted'])
        rows[1]['accountRatio']=0.6
        self.assertEqual(assess(rows,10)['status'],'partial_success')


if __name__=='__main__':unittest.main()
