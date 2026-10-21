import copy
import json
import tempfile
import unittest
from pathlib import Path

import scanner
from asset_identity import MAPPINGS, MAPPING_REVISION, cap_coverage, price_multiplier
from cap_identity_refresh import refresh_market_caps
from scan_verification import refresh_derived
from snapshot_store import ScanArchive
from test_scan_verification import row, universe


def market(coin_id, **kw):
    result={'id':coin_id,'name':coin_id,'current_price':2.,'market_cap':200.,
            'circulating_supply':100.,'last_updated':'2026-10-15T00:00:00Z'}
    result.update(kw)
    return result


class FullIdentityTests(unittest.TestCase):
    def test_exact_identity_bindings_and_native_assets(self):
        for asset, expected in [('BTR','bitlayer-bitvm'),('RONIN','ronin'),('MON','monad'),
                                ('BAN','comedian'),('EDGE','edgex'),('PHAROS','pharos-network'),
                                ('BROCCOLIF3B','broccoli-3'),('SLX','solstice'),('ZRX','0x')]:
            self.assertEqual(MAPPINGS[asset]['coingecko_id'], expected)

    def test_zero_or_inconsistent_supply_never_becomes_cap_or_fdv(self):
        for kw in ({'circulating_supply':0}, {'circulating_supply':None},
                   {'circulating_supply':10000}, {'current_price':0}):
            cap=scanner.resolve_cap(row(), [], [market('bitcoin',**kw)], [])
            self.assertIsNone(cap['capUsd'])
        cap=scanner.resolve_cap(row('RATS'), [], [market('rats',market_cap=0,circulating_supply=0,total_supply=1e12,fully_diluted_valuation=2e12)], [])
        self.assertIsNone(cap['capUsd'])

    def test_reviewed_independent_id_fallback_is_allowed_but_homonym_is_not(self):
        item={'id':'ach-alchemy-pay','symbol':'ACH','name':'Alchemy Pay','quotes':{'USD':{'market_cap':200,'price':2}}}
        cap=scanner.resolve_cap(row('ACH'), [], [], [item])
        self.assertEqual(cap['capId'],item['id']);self.assertTrue(cap['capTrusted'])
        item['id']='wrong-ach'
        self.assertIsNone(scanner.resolve_cap(row('ACH'), [], [], [item])['capUsd'])

    def test_contract_multiplier_and_non_dollar_price_guard(self):
        self.assertEqual(price_multiplier('1MBABYDOGEUSDT'),1_000_000)
        self.assertEqual(scanner.normalize('1MBABYDOGE'),'BABYDOGE')
        r=row('1MBABYDOGE',markPrice=2,capUsd=200,capPrice=.000002,capTrusted=True)
        refresh_derived(r);self.assertEqual(r['capPriceGapPct'],0);self.assertTrue(r['capTrusted'])
        r=row('ETH',symbol='ETHBTC',quote='BTC',capUsd=200,markPrice=.029,capPrice=2300,capTrusted=True)
        refresh_derived(r);self.assertIsNone(r['capPriceGapPct']);self.assertTrue(r['capTrusted'])

    def test_different_time_price_is_not_a_failed_identity_check(self):
        r=row(capUsd=200,capPrice=20,capTrusted=True,capAsOf='2026-10-15T03:00:00Z')
        refresh_derived(r);self.assertTrue(r['capTrusted']);self.assertIn('时点',r['capPriceCheckStatus'])
        r['capAsOf']='2026-10-15T00:01:00Z'
        refresh_derived(r);self.assertFalse(r['capTrusted'])

    def test_non_usd_contract_does_not_publish_quote_amounts_as_dollars(self):
        r=row('ETH',symbol='ETHBTC',quote='BTC');u=list(universe(r));u[0]['quoteAsset']='BTC'
        result=scanner.fetch_contract(*u,only={'oi'},requester=lambda *args:({'openInterest':'7','time':1800000000000},None),count_progress=False)
        self.assertIsNone(result['oiUsd']);self.assertEqual(result['oiQuote'],14.)
        self.assertIn('BTC',result['quoteUnitStatus'])

    def test_entire_reviewed_universe_is_requested_without_rank_pages(self):
        from unittest.mock import patch
        calls=[]
        def safe(url,params=None):
            calls.append((url,params));return [],None
        with patch.object(scanner,'safe',side_effect=safe):scanner.load_caps()
        batches=[p for u,p in calls if u.endswith('/coins/markets')]
        ids=[i for p in batches for i in p['ids'].split(',')]
        self.assertEqual(set(ids),{e['coingecko_id'] for e in MAPPINGS.values() if e.get('coingecko_id')})
        self.assertEqual(len(ids),len(set(ids)));self.assertTrue(all(p['page']==1 for p in batches))

    def test_explicit_revision_corrects_wrong_cap_and_preserves_old_evidence(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);archive=ScanArchive(root)
            r=row('BTR',capUsd=999,capId='btr-bitrue-coin',capName='Bitrue Coin',capSource='CoinPaprika')
            archive.record(r);before=archive.publish({'rows':[r],'total':1,'finishedAt':'original','expectedAssetContracts':{'BTR':1}})
            old=(archive.path/'revision-0001.json').read_bytes()
            args=dict(cap_context=([],[market('bitlayer-bitvm')],[]),alpha_context=None,
                      expected_scan_id=archive.scan_id,expected_revision=1,mapping_revision=MAPPING_REVISION)
            updated=refresh_market_caps(root,**args)
            self.assertEqual(updated['rows'][0]['capId'],'bitlayer-bitvm')
            self.assertEqual(updated['rows'][0]['capUsd'],200)
            self.assertEqual(updated['rows'][0]['oiUsd'],before['rows'][0]['oiUsd'])
            self.assertEqual(updated['rows'][0]['price'],before['rows'][0]['price'])
            self.assertEqual(updated['finishedAt'],'original')
            self.assertEqual((archive.path/'revision-0001.json').read_bytes(),old)
            self.assertTrue(updated['capCoverage']['all_applicable_trusted'])
            with self.assertRaisesRegex(ValueError,'漂移'):refresh_market_caps(root,**args)
            args['expected_revision']=2
            self.assertEqual(refresh_market_caps(root,**args)['revision'],2)

    def test_missing_cap_is_not_hidden_by_coverage_or_an_old_number(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);archive=ScanArchive(root);r=row('RATS',capUsd=999,capId='unproven')
            archive.record(r);archive.publish({'rows':[r],'total':1})
            updated=refresh_market_caps(root,cap_context=([],[market('rats',market_cap=0,circulating_supply=0)],[]),alpha_context=None,
                expected_scan_id=archive.scan_id,expected_revision=1,mapping_revision=MAPPING_REVISION)
            self.assertIsNone(updated['rows'][0]['capUsd'])
            self.assertFalse(updated['capCoverage']['all_applicable_trusted'])
            self.assertEqual(updated['capCoverage']['with_value'],0)
            self.assertIn('市值',updated['rows'][0]['dataVerification']['unresolved'])


if __name__=='__main__':unittest.main()
