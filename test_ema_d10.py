"""D.10 behavioural acceptance: no network, only disposable candidate stores."""
import datetime as dt
import json
import tempfile
import unittest
from pathlib import Path
from ema_observations import observe, BAR_MS, enrich_rows, states, bind_identity
from ema_squeeze_store import EmaSqueezeStore, hydrate_rows_from_store
from scanner import ignition_fields

BASE = 1767225600000


def iso(ms):
    return dt.datetime.fromtimestamp(ms/1000, dt.timezone.utc).isoformat()


def candles(closes):
    return [[BASE+i*BAR_MS, c, c+1, max(.1,c-1), c, 10,
             BASE+(i+1)*BAR_MS-1, 100, 1, 0, 0, 0] for i,c in enumerate(closes)]


class Observations(unittest.TestCase):
    def test_new_cross_equal_reset_and_reentry(self):
        raw=candles([100]*120+[101,102])
        a=observe(raw[:-1]); b=observe(raw)
        self.assertIs(a['weak_new_cross'],True)
        self.assertEqual(a['above99_count'],1)
        self.assertTrue(a['above99_count_exact'])
        self.assertEqual(b['above99_count'],2)
        self.assertIs(b['weak_new_cross'],False)
        self.assertEqual(observe(candles([100]*121))['above99_count'],0)
        raw=candles([100]*120+[101,102,80,120])
        c=observe(raw)
        self.assertTrue(c['weak_new_cross'])
        self.assertEqual(c['above99_count'],1)

    def test_technical_identity_has_no_future_cycle(self):
        o=observe(candles([100]*120+[101]))
        a=bind_identity(o,'USDⓈ-M','TESTUSDT')
        b=bind_identity(dict(o,candidate_cycle_id='future'),'USDⓈ-M','TESTUSDT')
        self.assertEqual(a['technical_events'],b['technical_events'])
        self.assertTrue(a['technical_events'])
        self.assertTrue(all(e['technical_only'] and not e['automatic_fill'] for e in a['technical_events']))
        self.assertNotEqual(a['technical_events'],bind_identity(o,'COIN-M','TESTUSDT')['technical_events'])

    def test_medium_technical_cross_keeps_formal_above99_path(self):
        closes=[100]*130+[150]*30+[90]*10+[300]
        raw=candles(closes);o=observe(raw);ss=states(raw)
        self.assertGreater(ss[-1]['ema'][1],ss[-1]['ema'][2])
        self.assertLessEqual(ss[-2]['ema'][0],ss[-2]['ema'][1])
        self.assertTrue(ignition_fields(closes,[100]*len(closes))['mediumCrossEvent'])
        self.assertTrue(o['technical_crosses']['medium_ema7_25_cross'])
        self.assertTrue(any(e['kind']=='medium_ema7_25_cross' for e in bind_identity(o,'USDⓈ-M','TESTUSDT')['technical_events']))
        self.assertEqual(o['schema'],'ema-observation-v1.2')

    def test_warmup_and_lower_bound(self):
        self.assertEqual(observe(candles([100]*119))['quality'],'insufficient_or_gap')
        a=observe(candles(list(range(1,121))))
        self.assertIsNone(a['weak_new_cross'])
        self.assertEqual(a['above99_count'],1)
        self.assertFalse(a['above99_count_exact'])
        self.assertIsNone(a['history60']['stack_seen'])
        b=observe(candles(list(range(1,123))))
        self.assertTrue(b['history60']['stack_seen'])
        self.assertLess(b['history60']['coverage'],60)

    def test_history_excludes_current_and_new_high_drawdown_zero(self):
        o=observe(candles([100]*180+[200]))
        self.assertFalse(o['history60']['stack_seen'])
        self.assertEqual(o['history60']['coverage'],60)
        self.assertEqual(o['drawdown60_pct'],0)
        self.assertAlmostEqual(o['return5_pct'],100)

    def test_gap_does_not_stitch(self):
        raw=candles(list(range(1,245)))
        del raw[150]
        o=observe(raw)
        self.assertEqual(o['quality'],'insufficient_or_gap')
        self.assertIsNone(o['above99_count'])
        self.assertEqual(o['valid_bars'],93)

    def test_zero_atr_body_and_bad_volume(self):
        raw=candles([100]*125)
        for k in raw:k[2]=k[3]=100
        o=observe(raw)
        self.assertEqual(o['atr14'],0)
        self.assertIsNone(o['distance_ema99_atr'])
        self.assertIsNone(o['candle_shape']['upper_body_ratio'])
        self.assertIsNone(states(raw,[0]*125)[-1]['volume_ratio'])
        self.assertIsNone(states(raw,[100]*123+[-1,300])[-1]['volume_ratio'])
        self.assertIsNone(ignition_fields([100]*125,[10]*123+[-1,300])['barVolumeRatio7'])

    def test_wilder_atr_units_and_five_period_return(self):
        raw=candles(list(range(1,131)))
        o=observe(raw)
        # Every TR=2 (high-low), constant across seed and Wilder recursion.
        self.assertAlmostEqual(o['atr14'],2)
        self.assertAlmostEqual(o['return5_pct'],4)
        self.assertAlmostEqual(o['distance_ema99_atr'],(130-states(raw)[-1]['ema'][2])/2)
        self.assertFalse(o['execution_thresholds_enabled'])


class EventTiming(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name);self.store=EmaSqueezeStore(self.root)
        self.t=BASE+BAR_MS-1
        self.store.enter_or_keep(market='M',symbol='X',oi_usd=100,now=iso(BASE-1))

    def row(self,offset=0,**kw):
        r=dict(market='M',symbol='X',signals=[True,True],sidewaysOk=True,
               belowAllEma=False,oiUsd=100,deathTrend=False,fullCross=offset==0,
               mediumCrossEvent=False,ema4h=[13,12,11],closed4h=13.5,
               candleAsOf=self.t+offset*BAR_MS,barVolumeRatio7=.5,emaQuality='long_window')
        r.update(kw);return r

    def runrow(self,offset=0,**kw):
        return self.store.evaluate_row(self.row(offset,**kw),now=iso(self.t+(offset+1)*BAR_MS))

    def test_same_bar_confirm_has_distinct_times_and_one_event(self):
        a=self.runrow(barVolumeRatio7=2)
        ev=a['crossConfirm']
        self.assertEqual(ev['cross_bar_time'],self.t)
        self.assertEqual(ev['confirm_bar_time'],self.t)
        self.assertNotEqual(ev['confirmed_at'],ev['confirm_bar_time'])
        self.assertIsNone(ev['next_open_reference'])
        self.assertIn(a['candidateCycleId'],ev['event_id'])
        b=self.runrow(barVolumeRatio7=2)
        self.assertIsNone(b['crossConfirm'])
        self.assertEqual(len(b['confirmedEvents']),1)
        self.assertEqual(b['latestConfirm']['first_seen_at'],ev['first_seen_at'])

    def test_next_bar_and_restart(self):
        a=self.runrow();p=a['pendingCross']
        self.store=EmaSqueezeStore(self.root)
        same=self.runrow()
        self.assertEqual(same['pendingCross']['first_seen_at'],p['first_seen_at'])
        b=self.runrow(1,barVolumeRatio7=2.1)
        self.assertEqual(b['crossConfirm']['confirm_bar_time'],self.t+BAR_MS)
        self.assertEqual(b['crossConfirm']['cross_bar_time'],self.t)
        self.assertEqual(b['crossConfirm']['first_seen_at'],p['first_seen_at'])
        self.assertIsNone(b['pendingCross'])

    def test_gap_does_not_confirm_t_plus_two(self):
        self.runrow()
        b=self.runrow(2,barVolumeRatio7=9)
        self.assertTrue(b['crossConfirm']['void'])
        self.assertEqual(b['crossConfirm']['reason'],'gap_missing_next_bar')
        self.assertFalse(b['confirmedEvents'])
        self.assertIsNone(b['pendingCross'])

    def test_terminal_not_rearmed_and_older_bar_ignored(self):
        self.runrow();self.runrow(1)
        before=self.store.path.read_bytes()
        old=self.runrow(0,oiUsd=1,deathTrend=True)
        self.assertEqual(old['ignoredReason'],'older_bar')
        self.assertEqual(before,self.store.path.read_bytes())
        now=self.runrow(1,fullCross=False,barVolumeRatio7=9)
        self.assertFalse(now['confirmedEvents'])
        self.assertIsNone(now['pendingCross'])

    def test_unknown_and_structure_failure_are_not_volume_failures(self):
        for fields,reason in [({'barVolumeRatio7':None},'next_bar_volume_unknown'),
                              ({'ema4h':[]},'next_bar_structure_unknown'),
                              ({'ema4h':[10,12,11]},'structure_broken'),
                              ({'oiUsd':None,'barVolumeRatio7':3},'next_bar_oi_unknown')]:
            with self.subTest(reason=reason):
                self.setUp()
                self.runrow();b=self.runrow(1,**fields)
                self.assertEqual(b['crossConfirm']['reason'],reason)
                self.assertFalse(b['confirmedEvents'])

    def test_pending_does_not_swallow_independent_strong_event(self):
        self.runrow(fullCross=False,mediumCrossEvent=True)
        b=self.runrow(1,fullCross=True,barVolumeRatio7=3)
        self.assertEqual([e['level'] for e in b['eventTransitions']],['medium','strong'])
        self.assertEqual(len(b['confirmedEvents']),2)
        self.assertEqual(b['latestConfirm']['level'],'strong')

    def test_candidate_cannot_be_created_retroactively(self):
        row=self.row(candleAsOf=BASE-2,barVolumeRatio7=3)
        b=self.store.evaluate_row(row,now=iso(BASE+BAR_MS))
        self.assertEqual(b['crossConfirm']['reason'],'cross_precedes_candidate_qualification')
        self.assertFalse(b['confirmedEvents'])

    def test_missing_oi_nan_and_future_cannot_confirm(self):
        for f in ({'oiUsd':float('nan')},{'ema4h':[13,float('inf'),11]},{'candleAsOf':self.t+10*BAR_MS}):
            with self.subTest(f=f):
                b=self.runrow(barVolumeRatio7=3,**f)
                self.assertTrue(b['crossConfirm']['blocked'])
                self.assertFalse(b['confirmedEvents'])

    def test_hydration_does_not_mix_candidate_cycles(self):
        row=self.row(emaSqueezeStage=dict(candidateCycleId='old',crossConfirm=dict(event_id='old',candidate_cycle_id='old'),pendingCross=dict(level='strong')))
        hydrate_rows_from_store([row],self.store)
        self.assertIsNone(row['emaSqueezeStage'].get('crossConfirm'))
        self.assertIsNone(row['emaSqueezeStage'].get('pendingCross'))
        self.assertFalse(row['emaSqueezeStage']['confirmedEvents'])

    def test_malformed_store_preserved_and_write_failure_preserves_original(self):
        from unittest.mock import patch
        before=self.store.path.read_bytes()
        with patch('ema_squeeze_store.atomic',side_effect=OSError('disk full')):
            with self.assertRaises(OSError):self.runrow(barVolumeRatio7=3)
        self.assertEqual(before,self.store.path.read_bytes())
        self.store.path.write_text('[]',encoding='utf-8')
        with self.assertRaises(ValueError):self.store.persist()
        self.assertEqual(self.store.path.read_text(encoding='utf-8'),'[]')


class Recovery(unittest.TestCase):
    def test_research_reads_current_schema_without_rewriting_old_snapshot(self):
        from ai_research import AiResearch
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);raw=candles([100]*130+[150]*30+[90]*10+[300])
            archive=root/'scan-history/test/4h.json';archive.parent.mkdir(parents=True)
            archive.write_text(json.dumps(dict(market='USDⓈ-M',symbol='X',interval='4h',raw=raw)),encoding='utf-8')
            row=dict(market='USDⓈ-M',symbol='X',candleAsOf=raw[-1][6],candleArchives={'4h':'scan-history/test/4h.json'},
                     emaObservation=dict(schema='ema-observation-v1',technical_events=[]))
            before=json.dumps(row,sort_keys=True)
            research=AiResearch.__new__(AiResearch);research.root=root
            evidence=research._slim_evidence_rows([dict(symbol='X',market='USDⓈ-M')],dict(rows=[row]))
            self.assertEqual(evidence[0]['emaObservation']['schema'],'ema-observation-v1.2')
            self.assertTrue(evidence[0]['emaObservation']['technical_crosses']['medium_ema7_25_cross'])
            self.assertEqual(json.dumps(row,sort_keys=True),before)

    def test_actual_archived_next_bar_replays_as_history_only(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);store=EmaSqueezeStore(root)
            raw=candles(list(range(100,243)))
            raw[141][7]=300
            cross=raw[140][6];current=raw[142][6]
            store.enter_or_keep(market='USDⓈ-M',symbol='X',oi_usd=100,now=iso(cross-BAR_MS))
            row=dict(market='USDⓈ-M',symbol='X',oiUsd=100,belowAllEma=False,deathTrend=False,
                     fullCross=True,ema4h=[230,220,200],closed4h=240,candleAsOf=cross,barVolumeRatio7=.5)
            store.evaluate_row(row,now=iso(cross+1))
            archive=root/'scan-history'/'test'/'4h.json';archive.parent.mkdir(parents=True)
            archive.write_text(json.dumps(dict(market='USDⓈ-M',symbol='X',interval='4h',raw=raw)),encoding='utf-8')
            row.update(candleAsOf=current,fullCross=False,candleArchives={'4h':'scan-history/test/4h.json'})
            b=store.evaluate_row(row,now=iso(current+1))
            ev=b['confirmedEvents'][0]
            self.assertEqual(ev['confirm_bar_time'],cross+BAR_MS)
            self.assertTrue(ev['historical'])
            self.assertFalse(ev['automatic_fill'])
            self.assertEqual(ev['entry_eligibility'],'unverified_historical')
            self.assertEqual(ev['next_open_reference']['open_time'],cross+BAR_MS+1)
            enrich_rows([row],root)
            self.assertEqual(row['emaObservation']['bar_time'],current)
            self.assertTrue(row['emaObservation']['derived_at_read'])
            # Wrong snapshot time cannot borrow this archive.
            bad=dict(row,candleAsOf=current+BAR_MS);bad.pop('emaObservation')
            self.assertEqual(enrich_rows([bad],root),0)


if __name__=='__main__':unittest.main()
