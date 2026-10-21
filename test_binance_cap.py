import copy
import unittest

from asset_identity import MAPPINGS, ALPHA_URL, resolve_alpha_cap
from scan_verification import verify_rows
from test_scan_verification import row


def context(asset='CT'):
    binding = MAPPINGS[asset]['alpha']
    return {'retrieved_at': '2026-10-15T07:00:00+00:00', 'data': {'code': '000000', 'success': True, 'data': [
        {'name': asset, 'symbol': asset, 'chainId': binding['chain_id'], 'contractAddress': binding['address'],
         'price': '2', 'circulatingSupply': '100', 'marketCap': '200', 'fdv': '2000'}]}}


class BinanceCapTests(unittest.TestCase):
    def test_chain_address_and_circulating_value_are_required(self):
        r = row('CT'); c = context()
        self.assertEqual(resolve_alpha_cap(r, c)['capUsd'], 200.)
        for field, value in [('chainId', '1'), ('contractAddress', 'wrong'), ('marketCap', '0'),
                             ('marketCap', 'NaN'), ('marketCap', '2000'), ('circulatingSupply', None)]:
            with self.subTest(field=field, value=value):
                bad = copy.deepcopy(c); bad['data']['data'][0][field] = value
                self.assertIsNone(resolve_alpha_cap(r, bad))

    def test_duplicate_and_bad_source_payload_do_not_fill(self):
        c = context(); c['data']['data'].append(copy.deepcopy(c['data']['data'][0]))
        self.assertIsNone(resolve_alpha_cap(row('CT'), c))
        c = context(); c['data']['success'] = False
        self.assertIsNone(resolve_alpha_cap(row('CT'), c))
        self.assertIsNone(resolve_alpha_cap(row('CT', underlyingType='INDEX'), context()))

    def test_retrieval_time_is_not_claimed_as_source_observation(self):
        cap = resolve_alpha_cap(row('CT'), context())
        self.assertIsNone(cap['capObservedAt'])
        self.assertEqual(cap['capTimestampKind'], 'retrieved_at')
        self.assertEqual(cap['capSource'], 'Binance Alpha')
        self.assertIn('获取时间', cap['capTimeNote'])

    def test_dataip_does_not_need_wrong_coingecko_id(self):
        self.assertIsNone(MAPPINGS['DATAIP']['coingecko_id'])
        r = row('DATAIP')
        calls = []
        def fetch(url, params):
            calls.append(url)
            self.assertEqual(url, ALPHA_URL)
            return context('DATAIP')['data'], None
        summary = verify_rows([r], ([{'id': 'unrelated', 'symbol': 'data'}], [], [{'symbol': 'UNRELATED'}]), read=fetch)
        self.assertEqual(calls, [ALPHA_URL])
        self.assertEqual(r['capSource'], 'Binance Alpha')
        self.assertTrue(r['capTrusted'])
        self.assertNotIn('市值', r['dataVerification']['unresolved'])
        self.assertEqual(summary['recovered_fields'], 1)

    def test_coingecko_rejection_can_fall_back_to_independent_alpha(self):
        r = row('CT'); healthy = row('CT', symbol='CTUSDC', capUsd=999., capTrusted=True)
        calls = []
        def fetch(url, params):
            calls.append(url)
            return (context()['data'], None) if url == ALPHA_URL else (None, 'HTTP 429: rate limited')
        summary = verify_rows([r, healthy], ([{'id': 'concrete', 'symbol': 'ct'}], [], [{'symbol': 'UNRELATED'}]), read=fetch)
        self.assertEqual(len(calls), 2)
        self.assertEqual(r['capUsd'], 200.)
        self.assertEqual(r['oiCapPct'], 5.)
        self.assertEqual(healthy['capUsd'], 999.)
        self.assertEqual(summary['requests'], 2)

    def test_captured_context_causes_no_duplicate_alpha_request(self):
        r = row('DATAIP')
        verify_rows([r], ([{'id': 'other', 'symbol': 'other'}], [], [{'symbol': 'UNRELATED'}]),
                    alpha_context=context('DATAIP'), read=lambda *args: self.fail('Captured Alpha data must not be fetched again'))
        self.assertEqual(r['capUsd'], 200.)
        self.assertEqual(r['fieldAvailableAt']['price'], 'original')


if __name__ == '__main__':
    unittest.main()
