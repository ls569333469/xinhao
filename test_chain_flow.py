import datetime as dt
import copy
import json
import unittest
from chain_flow import ROOT, normalize_series


class FlowEvidenceTest(unittest.TestCase):
    def test_missing_days_and_null_are_unknown(self):
        start=dt.datetime(2026,9,5,tzinfo=dt.timezone.utc)
        end=start+dt.timedelta(days=2)
        first={"datetime":"2026-09-05T00:00:00Z","value":0}
        second={"datetime":"2026-09-06T00:00:00Z","value":2}
        self.assertIsNone(normalize_series([first],start,end))
        self.assertIsNone(normalize_series([first,first,second],start,end))
        self.assertIsNone(normalize_series([first,{**second,"value":None}],start,end))
        self.assertIsNone(normalize_series([first,{**second,"value":float('nan')}],start,end))
        self.assertEqual(normalize_series([first,second],start,end)[first['datetime']],0)

    def test_supplement_never_becomes_current_signal(self):
        data=json.loads((ROOT/'chain-flows.json').read_text(encoding='utf-8'))
        scan=json.loads((ROOT/'scan.json').read_text(encoding='utf-8'))
        self.assertEqual(data['liveSignalCoverage'],0)
        self.assertTrue(data['currentProbe']['response'].get('errors'))
        self.assertTrue(all(r.get('chainFlowUsd') is None for r in scan['rows']))
        # Current smart-money evidence is a separately approved source. Historical
        # netflow must not enable signal 5 after that independent source is removed.
        from strategy_registry import indicator_states
        for row in scan['rows']:
            historical_only=copy.deepcopy(row)
            for key in ('smartMoneyEvidence','smartMoneyNetInflowUsd','smartMoneyStatus'):
                historical_only.pop(key,None)
            self.assertIsNone(indicator_states(historical_only)[4])
        for record in data['records']:
            self.assertIs(record['eligibleForLiveSignals'],False)
            self.assertEqual(len(record['days']),7)
            for day in record['days']:
                self.assertAlmostEqual(day['netInflowUsd'],day['inflowUsd']-day['outflowUsd'])
                self.assertAlmostEqual(day['netInflowTokens'],day['inflowTokens']-day['outflowTokens'])


if __name__=='__main__':
    unittest.main()
