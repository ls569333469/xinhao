"""Offline EXT-01 tests: source slots, authorize gates, refuse substitutes + auth bypass."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from ext_store import ExtStore, ExtConflict, SLOT_IDS


class ExtStoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = ExtStore(Path(self.tmp.name))

    def tearDown(self):
        self.tmp.cleanup()

    def test_catalog_defaults_unauthorized(self):
        cat = self.store.catalog()
        self.assertEqual(cat['slice'], 'EXT-01-precise-flow-deepen')
        self.assertFalse(cat['any_ready'])
        engine = next(i for i in cat['items'] if i['id'] == 'multi_market_sim_engine')
        self.assertFalse(engine['ready'])
        self.assertEqual(engine['status'], 'not_ready')
        self.assertEqual(cat['policy']['usdc_coinm_engine'], 'catalog_only_not_ready')
        self.assertIn('第三方解锁网站上的时间', cat['policy']['unlock_wording'])
        # no banned phrase in catalog dump
        raw = str(cat)
        self.assertNotIn('二手日历', raw)
        for slot in cat['source_slots']:
            self.assertIn(slot['status'], ('unauthorized', 'unverified', 'authorized', 'revoked'))
            if slot['slot_id'] in SLOT_IDS:
                # fresh store → unauthorized
                self.assertEqual(slot['status'], 'unauthorized')
                self.assertFalse(slot['claim_ready'])

    def test_refuse_reserves_as_precise_5(self):
        with self.assertRaises(ExtConflict):
            self.store.claim_precise_flow({'source': 'cex-reserves', 'as_precise_5': True})
        with self.assertRaises(ExtConflict):
            self.store.claim_precise_flow({'label_as': 'precise_5', 'source': 'chain-flows-technical'})
        st = self.store.precise_flow_status()
        self.assertFalse(st['ready'])
        self.assertEqual(st['status'], 'unauthorized')

    def test_claim_fails_when_slot_unauthorized(self):
        with self.assertRaises(ExtConflict) as ctx:
            self.store.claim_precise_flow({'as_precise_5': True, 'claim_ready': True})
        self.assertIn('unauthorized', str(ctx.exception).lower() + str(ctx.exception))

    def test_register_unverified_then_authorize_then_claim(self):
        # metadata only → unverified, not claimable
        slot = self.store.upsert_source('precise_indicator_5', {
            'url': 'https://example.invalid/precise-flow.json',
            'content_hash': 'abcd1234deadbeef',
            'as_of': '2026-10-13T00:00:00+00:00',
            '口径': 'exchange_net_flow_usd',
        })
        self.assertEqual(slot['status'], 'unverified')
        self.assertFalse(slot['claim_ready'])
        with self.assertRaises(ExtConflict):
            self.store.claim_precise_flow({'as_precise_5': True})

        # authorize without attestation → error
        with self.assertRaises(ValueError):
            self.store.upsert_source('precise_indicator_5', {
                'authorize': True,
                'url': 'https://example.invalid/precise-flow.json',
                'content_hash': 'abcd1234deadbeef',
                'as_of': '2026-10-13T00:00:00+00:00',
                '口径': 'exchange_net_flow_usd',
            })

        auth = self.store.upsert_source('precise_indicator_5', {
            'authorize': True,
            'url': 'https://example.invalid/precise-flow.json',
            'content_hash': 'abcd1234deadbeef',
            'as_of': '2026-10-13T00:00:00+00:00',
            '口径': 'exchange_net_flow_usd',
            'operator_attestation': '本地对照授权源文档核验（测试夹具，非生产真源）',
        })
        self.assertEqual(auth['status'], 'authorized')
        self.assertTrue(auth['claim_ready'])
        st = self.store.precise_flow_status()
        self.assertTrue(st['ready'])

        # time/口径 mismatch → 409
        with self.assertRaises(ExtConflict):
            self.store.claim_precise_flow({
                'as_precise_5': True,
                'expected_as_of': '2020-01-01T00:00:00+00:00',
            })
        with self.assertRaises(ExtConflict):
            self.store.claim_precise_flow({
                'as_precise_5': True,
                'expected_口径': 'wrong_caliber',
            })
        with self.assertRaises(ExtConflict):
            self.store.claim_precise_flow({
                'as_precise_5': True,
                'expected_content_hash': 'ffffffff',
            })

        ok = self.store.claim_precise_flow({
            'as_precise_5': True,
            'expected_as_of': '2026-10-13T00:00:00+00:00',
            'expected_口径': 'exchange_net_flow_usd',
            'expected_content_hash': 'abcd1234deadbeef',
        })
        self.assertTrue(ok['accepted'])
        self.assertTrue(ok['ready'])

        # substitutes still refused even when authorized
        with self.assertRaises(ExtConflict):
            self.store.claim_precise_flow({'source': 'cex-reserves', 'as_precise_5': True})

    def test_unlock_claim_and_banned_phrase(self):
        with self.assertRaises(ValueError):
            self.store.upsert_source('unlock', {'note': '这是二手日历'})
        self.store.upsert_source('unlock', {
            'authorize': True,
            'url': 'https://example.invalid/unlocks',
            'content_hash': 'u111111111111111',
            'as_of': '2026-10-01T00:00:00+00:00',
            '口径': 'third_party_unlock_site_time',
            'operator_attestation': '仅作第三方解锁网站上的时间；未在官网上核对',
            'note': '第三方解锁网站上的时间',
        })
        ok = self.store.claim_capability({
            'capability': 'unlock',
            'claim_ready': True,
            'expected_as_of': '2026-10-01T00:00:00+00:00',
            'expected_口径': 'third_party_unlock_site_time',
            'expected_content_hash': 'u111111111111111',
        })
        self.assertTrue(ok['accepted'])
        self.assertIn('第三方解锁网站上的时间', ok['note'])

    def test_engine_claim_always_rejected(self):
        with self.assertRaises(ExtConflict):
            self.store.claim_capability({'capability': 'multi_market_sim_engine'})
        with self.assertRaises(ExtConflict):
            self.store.claim_capability({'capability': 'usdc'})

    def test_revoke_blocks_claim(self):
        self.store.upsert_source('supply', {
            'authorize': True,
            'url': 'https://example.invalid/supply',
            'content_hash': 's222222222222222',
            'as_of': '2026-10-13T12:00:00+00:00',
            '口径': 'circulating_supply',
            'operator_attestation': 'test',
        })
        self.store.revoke_source('supply')
        with self.assertRaises(ExtConflict):
            self.store.claim_capability({'capability': 'supply'})


class ExtAuthBypassHardeningTests(unittest.TestCase):
    """Each bypass attempt must raise ExtConflict (HTTP 409 via server)."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = ExtStore(Path(self.tmp.name))

    def tearDown(self):
        self.tmp.cleanup()

    def _authorize_precise(self, **overrides):
        body = {
            'authorize': True,
            'url': 'https://example.invalid/precise-flow.json',
            'content_hash': 'abcd1234deadbeef',
            'as_of': '2026-10-13T00:00:00+00:00',
            '口径': 'exchange_net_flow_usd',
            'operator_attestation': '本地对照授权源文档核验（测试夹具，非生产真源）',
        }
        body.update(overrides)
        return self.store.upsert_source('precise_indicator_5', body)

    def test_skip_verification_flag_on_claim_409(self):
        self._authorize_precise()
        with self.assertRaises(ExtConflict) as ctx:
            self.store.claim_precise_flow({
                'as_precise_5': True,
                'skip_verification': True,
                'expected_as_of': '2026-10-13T00:00:00+00:00',
                'expected_口径': 'exchange_net_flow_usd',
                'expected_content_hash': 'abcd1234deadbeef',
            })
        self.assertIn('skip_verification', str(ctx.exception))

    def test_force_bypass_flags_on_authorize_409(self):
        for flag in ('bypass', 'force', 'force_authorize', 'ignore_hash', 'no_verify'):
            with self.subTest(flag=flag):
                store = ExtStore(Path(tempfile.mkdtemp()))
                with self.assertRaises(ExtConflict) as ctx:
                    store.upsert_source('precise_indicator_5', {
                        'authorize': True,
                        flag: True,
                        'url': 'https://example.invalid/p',
                        'content_hash': 'abcd1234deadbeef',
                        'as_of': '2026-10-13T00:00:00+00:00',
                        '口径': 'exchange_net_flow_usd',
                        'operator_attestation': 'test',
                    })
                self.assertIn(flag, str(ctx.exception))

    def test_claim_without_echo_fields_409(self):
        self._authorize_precise()
        with self.assertRaises(ExtConflict) as ctx:
            self.store.claim_precise_flow({'as_precise_5': True, 'claim_ready': True})
        msg = str(ctx.exception)
        self.assertIn('expected_as_of', msg)
        self.assertIn('不可省略核验', msg)

    def test_future_as_of_authorize_409(self):
        with self.assertRaises(ExtConflict) as ctx:
            self.store.upsert_source('precise_indicator_5', {
                'authorize': True,
                'url': 'https://example.invalid/p',
                'content_hash': 'abcd1234deadbeef',
                'as_of': '2099-01-01T00:00:00+00:00',
                '口径': 'exchange_net_flow_usd',
                'operator_attestation': 'test',
            })
        self.assertIn('未来', str(ctx.exception))

    def test_future_as_of_claim_echo_409(self):
        self._authorize_precise()
        with self.assertRaises(ExtConflict) as ctx:
            self.store.claim_precise_flow({
                'as_precise_5': True,
                'expected_as_of': '2099-06-01T00:00:00+00:00',
                'expected_口径': 'exchange_net_flow_usd',
                'expected_content_hash': 'abcd1234deadbeef',
            })
        self.assertTrue('未来' in str(ctx.exception) or '时间核验' in str(ctx.exception))

    def test_alternate_source_cannot_impersonate_precise_on_authorize(self):
        with self.assertRaises(ExtConflict) as ctx:
            self.store.upsert_source('precise_indicator_5', {
                'authorize': True,
                'url': 'https://example.invalid/cex-reserves.json',
                'content_hash': 'abcd1234deadbeef',
                'as_of': '2026-10-13T00:00:00+00:00',
                '口径': 'local_reserve_proxy',
                'operator_attestation': 'trying to use reserves',
            })
        self.assertIn('替代源', str(ctx.exception))

        with self.assertRaises(ExtConflict):
            self.store.upsert_source('precise_indicator_5', {
                'authorize': True,
                'url': 'https://example.invalid/ok.json',
                'content_hash': 'abcd1234deadbeef',
                'as_of': '2026-10-13T00:00:00+00:00',
                '口径': 'tech_flow_approx',
                'source_kind': 'chain-flows-technical',
                'operator_attestation': 'tech',
            })

    def test_authorized_slot_still_refuses_substitute_claim(self):
        self._authorize_precise()
        for src in ('cex-reserves', 'tech_flow', 'local_reserve', 'chain-flows-technical'):
            with self.subTest(src=src):
                with self.assertRaises(ExtConflict):
                    self.store.claim_precise_flow({
                        'as_precise_5': True,
                        'source': src,
                        'expected_as_of': '2026-10-13T00:00:00+00:00',
                        'expected_口径': 'exchange_net_flow_usd',
                        'expected_content_hash': 'abcd1234deadbeef',
                    })

    def test_events_slot_cannot_label_as_precise_5(self):
        self.store.upsert_source('events', {
            'authorize': True,
            'url': 'https://example.invalid/events',
            'content_hash': 'e333333333333333',
            'as_of': '2026-10-13T00:00:00+00:00',
            '口径': 'event_feed',
            'operator_attestation': 'test',
        })
        with self.assertRaises(ExtConflict) as ctx:
            self.store.claim_capability({
                'capability': 'events',
                'as_precise_5': True,
                'expected_as_of': '2026-10-13T00:00:00+00:00',
                'expected_口径': 'event_feed',
                'expected_content_hash': 'e333333333333333',
            })
        self.assertIn('精确⑤', str(ctx.exception))

    def test_historical_mcap_today_backfill_flag_409(self):
        self.store.upsert_source('historical_mcap', {
            'authorize': True,
            'url': 'https://example.invalid/mcap',
            'content_hash': 'm444444444444444',
            'as_of': '2026-10-13T00:00:00+00:00',
            '口径': 'historical_mcap_series',
            'operator_attestation': 'test',
        })
        with self.assertRaises(ExtConflict) as ctx:
            self.store.claim_capability({
                'capability': 'historical_mcap',
                'use_today_mcap': True,
                'expected_as_of': '2026-10-13T00:00:00+00:00',
                'expected_口径': 'historical_mcap_series',
                'expected_content_hash': 'm444444444444444',
            })
        self.assertIn('回填', str(ctx.exception))

    def test_happy_claim_still_works_with_echo(self):
        self._authorize_precise()
        ok = self.store.claim_precise_flow({
            'as_precise_5': True,
            'expected_as_of': '2026-10-13T00:00:00+00:00',
            'expected_口径': 'exchange_net_flow_usd',
            'expected_content_hash': 'abcd1234deadbeef',
        })
        self.assertTrue(ok['accepted'])


if __name__ == '__main__':
    unittest.main()
