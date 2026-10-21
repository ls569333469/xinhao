import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import scanner
from request_pacer import RequestPacer
from scan_verification import verify_rows, verify_latest
from snapshot_store import ScanArchive


def row(asset='BTC', **kw):
    data = {'market':'USDⓈ-M','symbol':asset+'USDT','asset':asset,'pair':asset+'USDT',
            'underlyingType':'COIN', 'quote':'USDT', 'price':2., 'markPrice':2.,
            'oiUsd':10., 'oiAsOf':1_800_000_000_000, 'accountRatio':.6,
            'closed4h':3., 'ema4h':[4.,5.,6.], 'fundingAprPct':-70., 'fundingPct':-.02,
            'fundingHours':8, 'volumeRatio':1., 'priceChangeWindowPct':0.,
            'capUsd':None, 'capTrusted':False, 'chainFlowUsd':None,
            'errors':{}, 'fieldAvailableAt':{'price':'original', 'oiUsd':'original'},
            'asOf':'2026-10-15T00:00:00+00:00', 'observed_at':'2026-10-15T00:00:00+00:00'}
    data.update(kw)
    return data


def universe(r):
    return ({'symbol':r['symbol'],'baseAsset':r['asset'],'quoteAsset':'USDT','pair':r['pair'],
             'underlyingType':'COIN'}, 'https://fapi.binance.com',
            {'lastPrice':'2','quoteVolume':'100'}, {'markPrice':'2','lastFundingRate':'-.0002'},
            {'__source_ok__':True})


class VerificationTests(unittest.TestCase):
    def test_known_id_targeted_cap_recovery_preserves_healthy_values(self):
        a=row();b=row('ETH',capUsd=888.,capTrusted=True)
        calls=[]
        def read(url, params):
            calls.append((url,params))
            return [{'id':'bitcoin','name':'Bitcoin','market_cap':200.,'current_price':2.,'last_updated':'2026-10-15T05:00:00Z'}],None
        s=verify_rows([a,b], ([{'id':'bitcoin','symbol':'btc'}],[],[]), read=read)
        self.assertEqual(len(calls),1)
        self.assertEqual(calls[0][1]['ids'],'bitcoin')
        self.assertEqual(a['capUsd'],200.)
        self.assertTrue(a['capTrusted'])
        self.assertEqual(a['oiCapPct'],5.)
        self.assertEqual(b['capUsd'],888.)
        self.assertEqual(a['fieldAvailableAt']['price'],'original')
        self.assertEqual(a['fieldAvailableAt']['oiUsd'],'original')
        self.assertIn('聪明钱流入',a['dataVerification']['unresolved'])
        self.assertEqual(s['status'],'recovered_some')

    def test_unique_symbol_gets_data_without_becoming_trusted(self):
        a=row('UNIQUE')
        s=verify_rows([a],([{'id':'one','symbol':'unique'}],[],[]),read=lambda *args:([
            {'id':'one','name':'One','market_cap':30.,'current_price':2.}],None))
        self.assertEqual(a['capUsd'],30.)
        self.assertFalse(a['capTrusted'])
        self.assertIn('资产身份',a['dataVerification']['unresolved'])

    def test_ambiguous_identity_does_not_guess_or_query(self):
        a=row('SAME')
        def fail(*args):self.fail('Ambiguous ID must not cause a request')
        verify_rows([a],([{'id':'x','symbol':'same'},{'id':'y','symbol':'same'}],[],[{'symbol':'OTHER'}]),read=fail)
        self.assertIsNone(a['capUsd'])
        self.assertIn('歧义',a['capStatus'])

    def test_identity_price_mismatch_stays_untrusted(self):
        a=row()
        verify_rows([a],([{'id':'bitcoin','symbol':'btc'}],[],[]),read=lambda *args:([
            {'id':'bitcoin','market_cap':100.,'current_price':20.}],None))
        self.assertFalse(a['capTrusted'])
        self.assertIn('20%',a['capStatus'])
        self.assertIn('市值价格一致性',a['dataVerification']['unresolved'])
        self.assertNotIn('资产身份',a['dataVerification']['unresolved'])

    def test_rate_limit_stops_provider_instead_of_retry_loop(self):
        rows=[row('X'+str(i)) for i in range(81)]
        coins=[{'id':'id'+str(i),'symbol':'X'+str(i)} for i in range(81)]
        calls=[]
        def read(*args):
            calls.append(args)
            return None,'HTTP 429: rate limited; cooldown 90s'
        s=verify_rows(rows,(coins,[],[]),read=read)
        self.assertEqual(sum('coingecko' in c[0] for c in calls),1)
        self.assertEqual(s['requests'],2)
        self.assertTrue(all(r['capUsd'] is None for r in rows))

    def test_request_budget_is_global_and_defers_remaining_rows(self):
        rows=[row('X'+str(i),oiUsd=None) for i in range(5)]
        calls=[]
        def read(*args):
            calls.append(args)
            return None,'temporary failure'
        s=verify_rows(rows, universes=[universe(r) for r in rows], max_requests=2, read=read)
        self.assertEqual(len(calls),2)
        self.assertEqual(s['requests'],2)
        self.assertTrue(all(r['oiUsd'] is None for r in rows))

    def test_targeted_oi_recovery_does_not_refresh_healthy_fields(self):
        a=row(capUsd=100.,capTrusted=True,oiUsd=None,errors={'OI':'timeout'})
        calls=[]
        def read(url, params):
            calls.append(url)
            return {'openInterest':'7','time':1_800_000_000_000},None
        scanner.PROGRESS['completed']=123
        verify_rows([a], universes=[universe(a)], read=read)
        self.assertEqual(len(calls),1)
        self.assertTrue(calls[0].endswith('openInterest'))
        self.assertEqual(a['oiUsd'],14.)
        self.assertEqual(a['accountRatio'],.6)
        self.assertEqual(a['asOf'],'2026-10-15T00:00:00+00:00')
        self.assertEqual(a['fieldAvailableAt']['price'],'original')
        self.assertNotIn('OI',a['errors'])
        self.assertEqual(scanner.PROGRESS['completed'],123)

    def test_failed_recovery_does_not_overwrite_good_fields(self):
        a=row(capUsd=100.,oiUsd=None,errors={'OI':'old timeout'})
        verify_rows([a],universes=[universe(a)],read=lambda *args:(None,'new timeout'))
        self.assertEqual(a['capUsd'],100.)
        self.assertEqual(a['accountRatio'],.6)
        self.assertIsNone(a['oiUsd'])
        self.assertIn('new timeout',a['dataVerification']['unresolved']['OI'])

    def test_short_history_and_unsupported_netflow_not_retried(self):
        a=row(capUsd=100.,capTrusted=True,closed4h=None,errors={})
        def fail(*args):self.fail('Short history/netflow cannot be repaired by retry')
        s=verify_rows([a],universes=[universe(a)],read=fail)
        self.assertEqual(s['requests'],0)
        self.assertIn('120',a['dataVerification']['unresolved']['4h指标'])
        self.assertIn('聪明钱流入',a['dataVerification']['unresolved'])

    def test_failed_candles_recovered_with_original_warmup_gate(self):
        raw=[[i*14_400_000,'2','3','1','2','10',(i+1)*14_400_000-1,'20',1,'1','3','0'] for i in range(121)]
        a=row(capUsd=100.,capTrusted=True,closed4h=None,errors={'K线':'timeout'})
        calls=[]
        def read(url,params):
            calls.append(params)
            return raw,None
        verify_rows([a],universes=[universe(a)],read=read)
        self.assertEqual(len(calls),1)
        self.assertEqual(a['closed4h'],2.)
        self.assertEqual(a['emaCandleCount'],121)
        self.assertEqual(len(a['_candles']['4h']),121)
        self.assertNotIn('K线',a['errors'])

    def test_gap_recovery_does_not_lower_gate(self):
        raw=[[i*14_400_000,'2','3','1','2','10',(i+1)*14_400_000-1,'20',1,'1','3','0'] for i in range(121)]
        raw.pop(50)
        a=row(capUsd=100.,capTrusted=True,closed4h=None,errors={'K线':'timeout'})
        verify_rows([a],universes=[universe(a)],read=lambda *args:(raw,None))
        self.assertIsNone(a['closed4h'])

    def test_funding_default_only_when_configuration_endpoint_succeeds(self):
        for failed in (False, True):
            a=row(capUsd=100.,fundingHours=None,fundingAprPct=None)
            verify_rows([a],universes=[universe(a)],read=lambda *args:(None,'timeout') if failed else ([],None))
            self.assertEqual(a['fundingHours'],None if failed else 8)

    def test_provider_wait_budget_does_not_send_early(self):
        clock=[0.]
        pacer=RequestPacer(5,clock=lambda:clock[0],sleep=lambda n:clock.__setitem__(0,clock[0]+n))
        pacer.rate_limited('3600')
        with self.assertRaises(TimeoutError):pacer.wait(max_wait=100)
        self.assertEqual(clock[0],0.)

    def test_exhausted_time_budget_sends_no_requests(self):
        a=row()
        s=verify_rows([a],max_seconds=0,read=lambda *args:self.fail('Budget exhausted'))
        self.assertEqual(s['requests'],0)
        self.assertIsNone(a['capUsd'])

    def test_fallback_has_one_attempt_and_keeps_source_untrusted(self):
        a=row('OTHER')
        calls=[]
        def read(url, params):
            calls.append(url)
            if 'coingecko' in url:return None,'HTTP 429: rate limited'
            return [{'id':'other-coin','symbol':'OTHER','name':'Other','quotes':{'USD':{'market_cap':50.,'price':2.}}}],None
        verify_rows([a],read=read)
        self.assertEqual(len(calls),2)
        self.assertEqual(a['capUsd'],50.)
        self.assertEqual(a['capSource'],'CoinPaprika')
        self.assertFalse(a['capTrusted'])

    def test_actual_request_uses_pacer_once_not_twice(self):
        a=row()
        now=[0.]
        pacer=RequestPacer(5,clock=lambda:now[0],sleep=lambda n:now.__setitem__(0,now[0]+n))
        import io
        payload=[{'id':'bitcoin','market_cap':100.,'current_price':2.}]
        with patch.object(scanner,'COINGECKO_PACER',pacer),patch.object(scanner.urllib.request,'urlopen',return_value=io.StringIO(json.dumps(payload))):
            verify_rows([a],([{'id':'bitcoin','symbol':'btc'}],[],[]))
        self.assertEqual(a['capUsd'],100.)
        self.assertEqual(now[0],0.)

    def test_latest_revision_is_immutable_once_and_timestamps_preserved(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            archive=ScanArchive(root)
            a=row()
            archive.record(a)
            archive.publish({'rows':[a],'total':1,'finishedAt':'old-finish','expectedAssetContracts':{'BTC':1}})
            original=(archive.path/'revision-0001.json').read_bytes()
            def read(url,params):
                if url.endswith('/coins/list'):return [{'id':'bitcoin','symbol':'btc'}],None
                return [{'id':'bitcoin','market_cap':100.,'current_price':2.}],None
            updated=verify_latest(root,read=read)
            self.assertEqual(updated['revision'],2)
            self.assertEqual(updated['finishedAt'],'old-finish')
            self.assertEqual(updated['rows'][0]['fieldAvailableAt']['price'],'original')
            self.assertEqual(updated['rows'][0]['capUsd'],100.)
            self.assertEqual((archive.path/'revision-0001.json').read_bytes(),original)
            self.assertTrue((archive.path/'verification/request-001.json').exists())
            self.assertEqual(verify_latest(root,read=lambda *args:self.fail('Repeated verification'))['revision'],2)

    def test_interrupted_verification_does_not_blindly_resend(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            archive=ScanArchive(root)
            a=row()
            archive.record(a)
            archive.publish({'rows':[a],'total':1})
            (archive.path/'verification').mkdir()
            with self.assertRaisesRegex(ValueError,'未发布'):
                verify_latest(root,read=lambda *args:self.fail('Interrupted attempt must be checked'))


if __name__=='__main__':unittest.main()
