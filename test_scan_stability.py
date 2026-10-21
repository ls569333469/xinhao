import copy
import datetime as dt
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import scanner
from asset_identity import cap_coverage, MAPPINGS
from scan_data_policy import validated_markets, cap_required, usd_exempt
from scan_verification import verify_rows
from snapshot_store import ScanArchive, assess
from test_scan_verification import row, universe


def market(identity='bitcoin', **fields):
    data = dict(id=identity, market_cap=200., current_price=2., circulating_supply=100., last_updated=scanner.now())
    data.update(fields)
    return data


class LiveCapValidationTests(unittest.TestCase):
    def check(self, data):
        return validated_markets(data, ['bitcoin'], scanner.now())

    def test_good_response_retains_source_and_distinct_retrieval_time(self):
        c = market(); got, errors = self.check([c])
        self.assertEqual(errors, [])
        self.assertEqual(got[0]['last_updated'], c['last_updated'])
        self.assertIn('capRetrievedAt', got[0])

    def test_invalid_values_never_fill(self):
        for field in ('market_cap', 'current_price', 'circulating_supply'):
            for bad in (None, 0, -1, 'nan', 'inf', True, {}, []):
                with self.subTest(field=field, bad=bad):
                    got, errors = self.check([market(**{field:bad})])
                    self.assertEqual(got, [])
                    self.assertTrue(errors)

    def test_fdv_or_total_supply_does_not_replace_circulating_supply(self):
        c = market(); del c['circulating_supply']; c.update(total_supply=100., fully_diluted_valuation=200.)
        self.assertFalse(self.check([c])[0])

    def test_time_limits_and_timezone(self):
        now = dt.datetime.now(dt.timezone.utc)
        for value in (None, 'invalid', now.replace(tzinfo=None).isoformat(),
                      (now-dt.timedelta(hours=2)).isoformat(), (now+dt.timedelta(minutes=5)).isoformat()):
            self.assertFalse(self.check([market(last_updated=value)])[0])

    def test_inconsistent_duplicate_wrong_id_and_bad_schema(self):
        for data in ([market(market_cap=300.)], [market(),market()], [market('wrong')],
                     [None, [], {'id':[]}, {}], {'error':'rate limit'}, None):
            self.assertFalse(self.check(data)[0])

    def test_duplicate_resolution_can_use_reviewed_paprika_only(self):
        fallback = dict(id=MAPPINGS['ACH']['coinpaprika_id'], name='Alchemy Pay', last_updated=scanner.now(), quotes={'USD':{'market_cap':200., 'price':2.}})
        c = market(MAPPINGS['ACH']['coingecko_id'], circulating_supply=0)
        result = scanner.resolve_cap(row('ACH'), [], [c], [fallback])
        self.assertEqual(result['capSource'], 'CoinPaprika')
        self.assertTrue(result['capTrusted'])
        result = scanner.resolve_cap(row('ACH'), [], [c], [dict(fallback, id='homonym')])
        self.assertIsNone(result['capUsd'])

    def test_malformed_primary_and_backup_do_not_crash(self):
        for data in (None, {}, [None, {}, {'id':None}, {'id':'x', 'symbol':12, 'quotes':[]}] , ['error']):
            self.assertIsNone(scanner.resolve_cap(row(), data, data, data)['capUsd'])


class CoveragePolicyTests(unittest.TestCase):
    def test_exclusions_do_not_remove_contracts_or_exempt_five_market_caps(self):
        rats = row('RATS', symbol='1000RATSUSDT')
        fx = [row('ETH' if symbol.startswith('ETH') else 'BTC', symbol=symbol, quote='BTC' if symbol=='ETHBTC' else 'U', capUsd=200., capTrusted=True, oiUsd=None)
              for symbol in ('ETHBTC','ETHU','BTCU','ETHUSD1','BTCUSD1')]
        rows = [rats, *fx, row(capUsd=200.,capTrusted=True), row('ALL',underlyingType='INDEX')]
        coverage = cap_coverage(rows)
        self.assertEqual(coverage['required_contracts'], 6)
        self.assertEqual(coverage['required_trusted_value'], 6)
        self.assertEqual(coverage['applicable_contracts'], 7)
        self.assertTrue(coverage['all_required_trusted'])
        self.assertFalse(coverage['all_applicable_trusted'])
        self.assertEqual(len(coverage['excluded_contracts']), 1)
        self.assertEqual(len(coverage['usd_conversion_exclusions']), 5)
        self.assertFalse(cap_required(rats))
        self.assertTrue(all(cap_required(r) for r in fx))
        verify_rows(rows, read=lambda *args:self.fail('No excluded fields may be queried'))
        self.assertIn('市值', rats['dataVerification']['excluded'])
        self.assertNotIn('市值', rats['dataVerification']['unresolved'])
        self.assertTrue(all('oiUsd' not in r['dataVerification']['unresolved'] for r in fx))
        self.assertTrue(all(r['oiUsd'] is None and r['oiCapPct'] is None for r in fx))
        self.assertEqual(assess(rows,len(rows))['ratios']['oiUsd'],1.)

    def test_missing_observation_stays_in_required_denominator(self):
        r = row(capUsd=200.,capTrusted=True)
        coverage = cap_coverage([r],['USDⓈ-M|BTCUSDT','USDⓈ-M|ETHUSDT'])
        self.assertEqual(coverage['required_contracts'],2)
        self.assertEqual(coverage['required_missing'],['USDⓈ-M|ETHUSDT'])
        self.assertFalse(coverage['all_required_trusted'])

    def test_new_contract_never_inherits_exemption(self):
        r = row('NEW', oiUsd=None)
        self.assertTrue(cap_required(r))
        self.assertFalse(usd_exempt(r))
        self.assertFalse(assess([r],1)['accepted'])
        self.assertFalse(cap_coverage([r])['all_required_trusted'])

    def test_missing_cap_publishes_only_partial_and_old_revision_unchanged(self):
        with tempfile.TemporaryDirectory() as directory:
            archive = ScanArchive(directory); r = row(); archive.record(r)
            first = archive.publish({'rows':[r], 'total':1})
            path = archive.path/'revision-0001.json'; old = path.read_bytes()
            self.assertEqual(first['quality']['status'],'partial_success')
            new = archive.revise([dict(market=r['market'],symbol=r['symbol'],capUsd=200.,capTrusted=True)])
            self.assertEqual(new['quality']['status'],'success')
            self.assertEqual(path.read_bytes(), old)


class AcquisitionResilienceTests(unittest.TestCase):
    def fake(self, fail=None):
        calls=[]
        def read(url, params=None):
            calls.append((url,params))
            if 'coinpaprika' in url:
                return [], None
            self.assertTrue(url.endswith('/coins/markets'))
            if fail and len(calls)==1:
                return fail
            return [market(i) for i in params['ids'].split(',')], None
        return calls, read

    def test_temporary_batch_failure_does_not_skip_independent_batches(self):
        calls, read = self.fake((None,'timeout'))
        mapping={str(i):{'coingecko_id':'id'+str(i)} for i in range(522)}
        ids={a:e['coingecko_id'] for a,e in mapping.items()}
        with patch.object(scanner,'MAPPINGS',mapping), patch.object(scanner,'CG_IDS',ids), patch.object(scanner,'safe',side_effect=read):
            _, markets, _, errors=scanner.load_caps()
        self.assertEqual(len(calls),4)
        self.assertEqual(len(markets),272)
        self.assertTrue(errors)

    def test_initial_rate_limit_stops_source_for_entire_scan(self):
        calls, read = self.fake((None,'HTTP 429: rate limited'))
        with patch.object(scanner,'safe',side_effect=read):
            context=scanner.load_caps(rows=[row()])
        self.assertEqual(len(calls),2)
        observed=[]
        def recovery(url, params):
            observed.append(url)
            self.assertNotIn('coingecko',url)
            return [],None
        verify_rows([row()],context[:3],read=recovery,cap_errors=context[3],enforce_fresh_caps=True)
        self.assertTrue(all('coingecko' not in url for url in observed))

    def test_targeted_recovery_covers_more_than_120_before_oi_budget(self):
        rows=[row('X'+str(i), oiUsd=None) for i in range(301)]
        ids={r['asset']:'id'+str(i) for i,r in enumerate(rows)}
        coins=[dict(id=ids[r['asset']],symbol=r['asset']) for r in rows]
        calls=[]
        def read(url,params):
            calls.append((url,params))
            self.assertTrue(url.endswith('/coins/markets'))
            return [market(i) for i in params['ids'].split(',')],None
        with patch.dict(scanner.CG_IDS,ids):
            summary=verify_rows(rows,(coins,[],[]),universes=[universe(r) for r in rows],read=read,max_requests=2,enforce_fresh_caps=True)
        self.assertEqual(summary['requests'],2)
        self.assertEqual(len(calls),2)
        self.assertTrue(all(r['capUsd']==200. for r in rows))
        self.assertTrue(all(r['oiUsd'] is None for r in rows))

    def test_excluded_rats_and_noncoins_do_not_enter_acquisition_ids(self):
        calls, read=self.fake()
        with patch.object(scanner,'safe',side_effect=read):
            scanner.load_caps(rows=[row('RATS',symbol='1000RATSUSDT'),row('ALL',underlyingType='INDEX'),row()])
        self.assertEqual(calls[0][1]['ids'],'bitcoin')
        self.assertEqual(len(calls),2)

    def test_source_failure_never_imports_previous_cap_values(self):
        r=row(); original=copy.deepcopy(r)
        verify_rows([r],([],[],[]),read=lambda *a:(None,'offline'),enforce_fresh_caps=True)
        self.assertIsNone(r['capUsd'])
        self.assertEqual(r['price'],original['price'])
        self.assertEqual(r['asOf'],original['asOf'])
        self.assertIn('市值',r['dataVerification']['unresolved'])


if __name__ == '__main__':
    unittest.main()
