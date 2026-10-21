import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import scanner
from asset_identity import MAPPINGS, MAPPING_REVISION, load_registry
from scan_verification import verify_latest, verify_rows
from snapshot_store import ScanArchive
from test_scan_verification import row


class AssetIdentityTests(unittest.TestCase):
    def market(self, coin_id, price=2):
        return {'id': coin_id, 'market_cap': 200., 'current_price': price, 'last_updated': '2026-10-15T00:00:00Z'}

    def test_homonym_and_alias_use_reviewed_id_not_first_candidate(self):
        for asset, expected in [('BONK', 'bonk'), ('ALT', 'altlayer'), ('BABY', 'babylon'), ('AIGENSYN', 'gensyn')]:
            with self.subTest(asset=asset):
                r = row(asset)
                verify_rows([r], ([{'id': 'wrong', 'symbol': asset}, {'id': expected, 'symbol': 'ai' if asset == 'AIGENSYN' else asset}], [], [dict(symbol='OTHER')]),
                            read=lambda url, params: ([self.market(expected)], None))
                self.assertEqual(r['capId'], expected)
                self.assertTrue(r['capTrusted'])

    def test_candidate_address_match_does_not_become_trusted(self):
        r = row('AIN')
        self.addCleanup(patch.stopall)
        patch.dict(MAPPINGS, {'AIN': dict(MAPPINGS['AIN'], trusted=False)}).start()
        verify_rows([r], ([], [], [dict(symbol='OTHER')]), read=lambda url, params: (
            [{'id': 'infinity-ground', 'symbol': 'ain'}] if url.endswith('/coins/list') else [self.market('infinity-ground')], None))
        self.assertEqual(r['capId'], 'infinity-ground')
        self.assertFalse(r['capTrusted'])
        self.assertIn('资产身份', r['dataVerification']['unresolved'])

    def test_known_alias_never_falls_back_to_other_provider_homonym(self):
        cap = scanner.resolve_cap(row('AIGENSYN'), [], [], [{'symbol': 'AIGENSYN', 'id': 'impostor', 'name': 'Wrong', 'quotes': {'USD': {'market_cap': 999., 'price': 2.}}}])
        self.assertIsNone(cap['capUsd'])
        self.assertIsNone(cap['capSource'])

    def test_stock_and_index_never_use_crypto_homonym_market_cap(self):
        for kind in ('INDEX', 'STOCK'):
            r = row('BONK', underlyingType=kind)
            self.assertIsNone(scanner.resolve_cap(r, [], [self.market('bonk')], [])['capUsd'])
        r = row('ALL', underlyingType='INDEX')
        verify_rows([r], ([{'id': 'wrong', 'symbol': 'all'}], [], [dict(symbol='OTHER')]), read=lambda *args: self.fail('Index must not fetch cap'))
        self.assertFalse(r['capApplicable'])
        self.assertNotIn('市值', r['dataVerification']['unresolved'])
        self.assertIn('不适用', r['capStatus'])

    def test_multiplier_price_guard_still_rejects_bad_identity(self):
        r = row('BONK', symbol='1000BONKUSDT', markPrice=2.)
        verify_rows([r], ([{'id': 'bonk', 'symbol': 'bonk'}], [], [dict(symbol='OTHER')]), read=lambda *args: ([self.market('bonk', .002)], None))
        self.assertTrue(r['capTrusted'])
        self.assertEqual(r['capPriceGapPct'], 0.)
        r = row('BONK', symbol='1000BONKUSDT', markPrice=2.)
        verify_rows([r], ([{'id': 'bonk', 'symbol': 'bonk'}], [], [dict(symbol='OTHER')]), read=lambda *args: ([self.market('bonk', 2.)], None))
        self.assertFalse(r['capTrusted'])

    def test_mapping_refresh_once_preserves_observations_and_old_revision(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            archive = ScanArchive(root)
            missing, healthy = row('BONK'), row('BTC', capUsd=999., capTrusted=True)
            for r in (missing, healthy): archive.record(r)
            before = archive.publish({'rows': [missing, healthy], 'total': 2, 'finishedAt': 'old', 'expectedAssetContracts': {'BONK': 1, 'BTC': 1}, 'verification': {'rounds': 1}})
            old_bytes = (archive.path / 'revision-0001.json').read_bytes()
            def fetch(url, params):
                self.assertEqual(params['ids'], 'bonk')
                return [self.market('bonk')], None
            updated = verify_latest(root, mapping_revision=MAPPING_REVISION, cap_context=([{'id': 'bonk', 'symbol': 'bonk'}], [], [dict(symbol='OTHER')]), read=fetch)
            self.assertEqual(updated['revision'], 2)
            self.assertEqual(updated['rows'][1]['capUsd'], 999.)
            self.assertEqual(updated['finishedAt'], 'old')
            for old, new in zip(before['rows'], updated['rows']):
                for field in ('price', 'markPrice', 'oiUsd', 'accountRatio', 'asOf', 'observed_at', 'ema4h', 'closed4h'):
                    self.assertEqual(old.get(field), new.get(field))
            self.assertEqual(old_bytes, (archive.path / 'revision-0001.json').read_bytes())
            repeated = verify_latest(root, mapping_revision=MAPPING_REVISION, read=lambda *args: self.fail('No duplicate requests'))
            self.assertEqual(repeated['revision'], 2)
            with self.assertRaisesRegex(ValueError, '版本'):
                verify_latest(root, mapping_revision='arbitrary-retry', read=fetch)

    def test_interrupted_mapping_refresh_blocks_blind_retry(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            archive = ScanArchive(root)
            r = row('BONK'); archive.record(r); archive.publish({'rows': [r], 'total': 1})
            (archive.path / ('verification-mapping-' + MAPPING_REVISION[:16])).mkdir()
            with self.assertRaisesRegex(ValueError, '未发布'):
                verify_latest(root, mapping_revision=MAPPING_REVISION, read=lambda *args: self.fail('Interrupted retry'))

    def test_registry_requires_explicit_boolean_and_evidence(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'registry.json'
            data = {'schema': 'asset-identity-v1', 'entries': {'X': {'coingecko_id': 'x', 'trusted': 'false', 'evidence': ['https://example.com']}}}
            path.write_text(json.dumps(data))
            with self.assertRaises(ValueError): load_registry(path)


if __name__ == '__main__':
    unittest.main()
