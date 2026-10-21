import copy
import datetime as dt
import unittest
from cex_reserves import derive


class ReserveQualityTest(unittest.TestCase):
    def setUp(self):
        self.at=dt.datetime(2026,10,13,4,tzinfo=dt.timezone.utc)
        self.end=int(self.at.replace(hour=0).timestamp())
        self.series=[{'date':self.end-86400*(7-i),'tokens':{'BTC':100-i}} for i in range(8)]
        self.source={'chainTvls':{'Bitcoin':{'tokens':self.series}}}
        self.scan={'rows':[{'asset':'BTC'},{'asset':'BTC'},{'asset':'MAGIC'}]}

    def test_quantities_and_full_days_only(self):
        self.series.append({'date':self.end+3600,'tokens':{'BTC':1}})
        result=derive(self.source,self.scan,self.at)
        r=result['records'][0]
        self.assertEqual(r['reserveTokens'],93)
        self.assertAlmostEqual(r['weekChangePct'],-7)
        self.assertEqual(r['consecutiveDecreaseDays'],7)
        self.assertEqual(result['freshAssets'],1)
        self.assertEqual(result['contractReferences'],2)
        self.assertIs(r['eligibleForExactNetflow'],False)

    def test_gaps_do_not_become_zero(self):
        self.series.pop(3)
        result=derive(self.source,self.scan,self.at)
        self.assertEqual(result['records'],[])
        self.assertTrue(result['failures'])

    def test_stale_data_is_not_fresh(self):
        result=derive(self.source,self.scan,self.at+dt.timedelta(days=2))
        self.assertEqual(result['freshAssets'],0)
        self.assertEqual(result['contractReferences'],0)

    def test_price_changes_cannot_create_quantity_flow(self):
        for point in self.series:point['tokens']['BTC']=100
        self.source['tokensInUsd']=[{'date':self.end-86400,'tokens':{'BTC':10}},{'date':self.end,'tokens':{'BTC':10000}}]
        r=derive(self.source,self.scan,self.at)['records'][0]
        self.assertEqual(r['dayChangePct'],0)
        self.assertEqual(r['weekChangePct'],0)


if __name__=='__main__':unittest.main()
