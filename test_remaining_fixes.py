import copy
import datetime as dt
import json
import os
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import patch
import tempfile
import contextlib
import io

import scanner
from asset_identity import MAPPINGS
from scan_verification import verify_rows, apply_cap
from test_scan_verification import row, universe
from test_binance_cap import context
from strategy_registry import indicator_states
import smart_money as sm


class RemainingFixTests(unittest.TestCase):
    def test_timezone_works_without_system_database_or_site_packages(self):
        env = dict(os.environ, PYTHONTZPATH='')
        code = '''
from timezone_support import ZoneInfo, ensure_timezone_data
from datetime import datetime
ensure_timezone_data()
assert datetime(2026,1,1,tzinfo=ZoneInfo('Asia/Shanghai')).utcoffset().total_seconds()==28800
assert datetime(2026,1,1,tzinfo=ZoneInfo('America/New_York')).utcoffset().total_seconds()==-18000
assert datetime(2026,7,1,tzinfo=ZoneInfo('America/New_York')).utcoffset().total_seconds()==-14400
'''
        result = subprocess.run([sys.executable, '-B', '-S', '-c', code], env=env, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_price_conflict_triggers_alpha_and_counts_recovery_once(self):
        r = row('CT', capTimeNote='old-provider-note', capCirculatingSupply=999)
        c = context(); c['retrieved_at'] = r['asOf']
        def read(url, params):
            return ([{'id':'concrete', 'market_cap':2000., 'current_price':20.,
                      'circulating_supply':100., 'last_updated':r['asOf']}], None)
        summary = verify_rows([r], ([],[],[{'id':'irrelevant'}]), read=read, alpha_context=c)
        self.assertEqual(r['capSource'], 'Binance Alpha')
        self.assertTrue(r['capTrusted'])
        self.assertEqual(r['capCirculatingSupply'], 100)
        self.assertEqual(summary['recovered_fields'], 1)
        self.assertNotIn('资产身份', r['dataVerification']['unresolved'])
        self.assertNotIn('市值价格一致性', r['dataVerification']['unresolved'])

    def test_switching_provider_clears_all_previous_cap_metadata(self):
        r = row(capCirculatingSupply=999, capTimeNote='old')
        apply_cap(r, {'capUsd':100., 'capPrice':2., 'capTrusted':True})
        self.assertNotIn('capCirculatingSupply', r)
        self.assertNotIn('capTimeNote', r)

    def test_short_contract_history_has_independent_volume_gate(self):
        step = 14_400_000
        end = int(dt.datetime.now(dt.timezone.utc).timestamp()*1000)//step*step
        for n in (47,48,90,119,120):
            with self.subTest(n=n):
                bars = [[end-(n-i)*step,2,2.1,1.9,2,10,end-(n-i-1)*step-1,20,1,0,0,0] for i in range(n)]
                u = list(universe(row('CT'))); u[0]['onboardDate'] = bars[0][0]
                result = scanner.fetch_contract(*u, only={'candles'}, requester=lambda *a:(bars,None), count_progress=False)
                self.assertEqual(result.get('volumeRatio'), 1. if n>=48 else None)
                self.assertEqual(result.get('closed4h'), 2. if n>=120 else None)
                self.assertEqual(result['emaWarmupRemaining'], max(0,120-n))
                self.assertEqual(result['candleHistoryStatus'], 'ready' if n>=120 else 'listing_warmup')
                if n<120: self.assertEqual(result['emaWarmupExpectedAt'], end-1+(120-n)*step)
                if n==90:
                    bars.pop(20)
                    bad = scanner.fetch_contract(*u, only={'candles'}, requester=lambda *a:(bars,None), count_progress=False)
                    self.assertIsNone(bad.get('volumeRatio'))
                    self.assertEqual(bad['candleHistoryStatus'], 'gap')


class SmartMoneyTests(unittest.TestCase):
    def setUp(self):
        self.address = MAPPINGS['CT']['alpha']['address']
        self.at = dt.datetime.now(dt.timezone.utc)
        self.record = {'chain':'56','period':'4h','tagType':2,'status':'ok','retrieved_at':self.at.isoformat(),
                       'response':{'code':'000000','success':True,'data':[{'ca':self.address,'tokenName':'NOT_CT','inflow':15.,'traders':2}]}}
        self.ctx = {'index':{sm.address_key('56',self.address):{'CT'}},'records':[self.record],
                    'identity_hash':'test','finished_at':self.at.isoformat()}

    def apply(self, records=None, **kw):
        r = row('CT', chainFlowUsd=-123)
        if records is not None: self.ctx['records']=records
        summary=sm.apply([r],self.ctx,at=kw.get('at',self.at))
        return r,summary

    def test_address_identity_and_old_exchange_flow_are_independent(self):
        r,summary=self.apply()
        self.assertEqual(r['smartMoneyNetInflowUsd'],15.)
        self.assertEqual(r['chainFlowUsd'],-123)
        self.assertTrue(indicator_states(r)[4])
        self.assertEqual(summary['covered_assets'],1)
        self.assertIsNone(r['smartMoneyEvidence']['windowEnd'])

    def test_negative_and_explicit_zero_are_known_but_missing_is_unknown(self):
        for value in (-2,0,2):
            self.record['response']['data'][0]['inflow']=value
            r,_=self.apply();self.assertIs(indicator_states(r)[4],value>0)
        self.record['response']['data']=[]
        r,_=self.apply();self.assertIsNone(r['smartMoneyNetInflowUsd']);self.assertIsNone(indicator_states(r)[4])

    def test_ticker_match_wrong_address_does_not_match(self):
        self.record['response']['data'][0].update(ca='0x'+'1'*40,tokenName='CT')
        r,_=self.apply();self.assertIsNone(r['smartMoneyNetInflowUsd'])

    def test_duplicate_address_invalid_number_or_multi_chain_is_unknown(self):
        for value in (None,True,'NaN','Infinity'):
            self.record['response']['data'][0]['inflow']=value
            r,_=self.apply();self.assertIsNone(indicator_states(r)[4])
        self.record['response']['data'][0]['inflow']=2
        self.record['response']['data']*=2
        r,_=self.apply();self.assertEqual(r['smartMoneyStatus'],'ambiguous')
        self.record['response']['data']=self.record['response']['data'][:1]
        second=copy.deepcopy(self.record);second['chain']='1'
        self.ctx['index'][sm.address_key('1',self.address)]={'CT'}
        r,_=self.apply([self.record,second]);self.assertEqual(r['smartMoneyStatus'],'ambiguous')

    def test_stale_source_never_uses_old_flow_or_changes_to_zero(self):
        r,_=self.apply(at=self.at+dt.timedelta(minutes=16))
        self.assertIsNone(r['smartMoneyNetInflowUsd'])
        self.assertEqual(r['smartMoneyStatus'],'source_error')
        self.record['status']='source_error'
        r,_=self.apply();self.assertIsNone(indicator_states(r)[4])

    def test_business_errors_and_four_request_bound(self):
        calls=[]
        def read(chain):
            calls.append(chain)
            return {'code':'13107001','success':False,'data':[]}
        c=sm.collect(Path(__file__).parent,read=read)
        self.assertEqual(sorted(calls), sorted(sm.CHAINS))
        self.assertTrue(all(r['status']=='source_error' for r in c['records']))

    def test_missing_or_forged_evidence_does_not_enable_i5(self):
        r,_=self.apply();r['smartMoneyEvidence']=None
        self.assertIsNone(indicator_states(r)[4])
        r,_=self.apply();r['smartMoneyNetInflowUsd']=999
        self.assertIsNone(indicator_states(r)[4])

    def test_scan_applies_one_asset_flow_to_two_contracts_without_double_count(self):
        c = copy.deepcopy(self.ctx)
        c['index'] = {sm.address_key('56',self.address):{'BTC'}}
        def request(url, *args, **kwargs):
            if url.endswith('exchangeInfo'):
                return {'symbols':[{'symbol':'BTCUSDT' if 'fapi' in url else 'BTCUSD_PERP',
                                    'baseAsset':'BTC','quoteAsset':'USDT','pair':'BTCUSDT',
                                    'underlyingType':'COIN','contractType':'PERPETUAL','status':'TRADING'}]}
            return []
        def contract(meta,host,*args):
            r=row('BTC',symbol=meta['symbol'],market='USDⓈ-M' if 'fapi' in host else 'COIN-M')
            r['_candles']={'4h':[[0,3,4,2,3,2,1,6,1,1,3,0]]}
            return r
        with tempfile.TemporaryDirectory() as directory, \
                patch.object(scanner,'ROOT',Path(directory)), \
                patch.object(scanner,'request',side_effect=request), \
                patch.object(scanner,'load_caps',return_value=([],[],[],[])), \
                patch.object(scanner,'fetch_contract',side_effect=contract), \
                patch.object(sm,'collect',return_value=c) as collect, \
                patch('reserve_refresh.fetch_source',side_effect=TimeoutError('offline')), \
                contextlib.redirect_stdout(io.StringIO()):
            result=scanner.scan()
        collect.assert_called_once()
        self.assertEqual(result['smartMoneyCoverage']['covered_contracts'],2)
        self.assertEqual(result['smartMoneyCoverage']['covered_assets'],1)
        self.assertTrue(all(r['signals'][4] is True for r in result['rows']))
        self.assertTrue(all('精确净流' not in r['dataVerification']['unresolved'] for r in result['rows']))


if __name__=='__main__':unittest.main()
