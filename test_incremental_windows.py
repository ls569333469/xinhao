import tempfile,unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch
from b_observe_runner import run_b_observe_round
from market_candle_cache import MarketCandleCache
from observe_config import OBSERVE_DEFAULT,TIMING_DEFAULT
from source_resume import H

class Windows(unittest.TestCase):
    def test_one_hour_48_hours_internal_gap_and_revision(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);target=10000*H;calls=[];changes={}
            def fetch(m,s,start_time=None,end_time=None,limit=500):
                start=target-500*H if start_time is None else start_time
                calls.append((start_time,end_time))
                return [[t,100,101,99,100,1,t+H-1,changes.get(t,10)] for t in range(start,min(end_time,start+limit*H),H)]
            def run(end):return run_b_observe_round(root,symbols=[dict(market='USDⓈ-M',symbol='TEST',bg4h=dict(ema7=2,ema25=1))],observe={**OBSERVE_DEFAULT,'enabled':True},timing=TIMING_DEFAULT,now_ms=end+120000,target_close_boundary=end,fetch_1h=fetch,fetch_evidence=lambda *a:dict(changed_timestamps=[]))
            self.assertEqual(run(target)['written'],500)
            read_sizes=[];original=MarketCandleCache.get_bars
            def read(cache,**kwargs):
                rows=original(cache,**kwargs);read_sizes.append(len(rows));return rows
            with patch.object(MarketCandleCache,'get_bars',read):result=run(target+H)
            self.assertEqual(result['written'],1);self.assertEqual(read_sizes,[171]);self.assertEqual(calls[-1],(target-2*H,target+H))
            result=run(target+49*H);self.assertEqual(result['written'],48)
            cache=MarketCandleCache(root)
            with closing(cache._connect()) as db:db.execute('DELETE FROM bars WHERE open_time=?',(target-200*H,));db.commit()
            self.assertEqual(run(target+49*H)['written'],0) # immutable block reused, logical key restored
            self.assertEqual(cache.coverage(market='USDⓈ-M',symbol='TEST',target=target+49*H)['gaps'],[])
            changes[target+48*H]=80
            self.assertEqual(run(target+49*H)['written'],1)

if __name__=='__main__':unittest.main()
