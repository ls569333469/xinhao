"""Offline RULE-01 coverage hard-gate tests."""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from coverage_gate import field_known_on_row
from rule_coverage_gate import assert_deps_covered, dep_field_map
from strategy_store import StrategyStore
from rule_bindings import RuleBindings, BindingConflict


class RuleCoverageGateTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        rows = []
        for i, sym in enumerate(['AAAUSDT', 'BBBUSDT']):
            rows.append({
                'symbol': sym,
                'market': 'USDⓈ-M',
                'price': 10.0 + i,
                'markPrice': 10.0 + i,
                'oiUsd': 1000.0 if i == 0 else None,  # partial oi
                'capUsd': None,
                'accountRatio': 0.8,
                'fundingAprPct': -1.0,
                'volumeRatio': 1.2,
                'closed4h': 9.5,
                'ema4h': [9.0, 9.1, 9.2],
                'oiCapPct': None,
            })
        scan = {
            'scan_id': 'scan-rgate',
            'revision': 'rev-rgate',
            'total': 2,
            'received': 2,
            'rows': rows,
        }
        (self.root / 'scan.json').write_text(json.dumps(scan), encoding='utf-8')
        self.strategies = StrategyStore(self.root)

    def tearDown(self):
        self.tmp.cleanup()

    def test_ema_list_known(self):
        self.assertTrue(field_known_on_row('ema4h', {'ema4h': [1.0, 2.0, 3.0]}))
        self.assertFalse(field_known_on_row('ema4h', {'ema4h': []}))

    def test_assert_snapshot_and_funding_ok(self):
        gate = assert_deps_covered(self.root, ['snapshot', 'funding'], candle_limit=5)
        self.assertTrue(gate['ok'], gate)
        self.assertFalse(gate['claims']['ath_complete'])
        self.assertFalse(gate['claims']['six_historical_indicators_complete'])

    def test_assert_oi_partial_rejected(self):
        gate = assert_deps_covered(self.root, ['oi'], candle_limit=5)
        self.assertFalse(gate['ok'])
        self.assertIn('oi', gate['missing_dependencies'])
        detail = gate['details']['oi']
        self.assertEqual(detail['fields'][0]['status'], 'partial')

    def test_assert_ath_unsupported(self):
        gate = assert_deps_covered(self.root, ['ath'], candle_limit=5)
        self.assertFalse(gate['ok'])
        self.assertIn('ath', gate['missing_dependencies'])

    def test_freeze_refused_on_oi_dep(self):
        draft = self.strategies.create_draft({
            'strategy_id': 'S-oi',
            'title': '要OI',
            'data_dependencies': ['snapshot', 'oi'],
            'rules': {'entry_text': 'x', 'exit_text': 'y'},
        })
        with self.assertRaises(ValueError) as cm:
            self.strategies.freeze(draft['draft_id'], {'version': '0.1.0'})
        self.assertIn('覆盖硬闸', str(cm.exception))

    def test_enable_refused_on_legacy_oi_version(self):
        """Legacy frozen version with oi deps: enable must still hard-refuse."""
        draft = self.strategies.create_draft({
            'strategy_id': 'S-legacy-oi',
            'title': '遗留OI',
            'data_dependencies': ['snapshot', 'funding'],
            'rules': {'entry_text': 'x', 'exit_text': 'y'},
        })
        ver = self.strategies.freeze(draft['draft_id'], {'version': '0.1.0'})
        # Mutate on-disk version to declare oi (simulates pre-gate freeze)
        vpath = self.root / 'runtime' / 'strategies' / 'versions' / f"{ver['version_id']}.json"
        if not vpath.exists():
            # find actual layout
            candidates = list((self.root / 'strategies').rglob(f"{ver['version_id']}*.json"))
            self.assertTrue(candidates, 'version file missing')
            vpath = candidates[0]
        data = json.loads(vpath.read_text(encoding='utf-8'))
        data['data_dependencies'] = ['snapshot', 'oi']
        vpath.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
        out = self.strategies.set_enabled(ver['version_id'], True)
        self.assertTrue(out.get('enable_refused'))
        self.assertIn('oi', out.get('missing_dependencies') or [])

    def test_bind_refused_on_ath(self):
        draft = self.strategies.create_draft({
            'strategy_id': 'S-ath',
            'title': '要ATH',
            'data_dependencies': ['snapshot', 'funding'],
            'rules': {'entry_text': 'x', 'exit_text': 'y'},
        })
        ver = self.strategies.freeze(draft['draft_id'], {'version': '0.1.0'})
        vpath = self.root / 'runtime' / 'strategies' / 'versions' / f"{ver['version_id']}.json"
        if not vpath.exists():
            candidates = list((self.root / 'strategies').rglob(f"{ver['version_id']}*.json"))
            self.assertTrue(candidates)
            vpath = candidates[0]
        data = json.loads(vpath.read_text(encoding='utf-8'))
        data['data_dependencies'] = ['snapshot', 'ath']
        vpath.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
        bindings = RuleBindings(self.root, self.strategies)
        with self.assertRaises(BindingConflict) as cm:
            bindings.bind_surface('market', {'version_id': ver['version_id']})
        self.assertIn('覆盖硬闸', str(cm.exception))

    def test_dep_map_reuses_gate_fields(self):
        m = dep_field_map()
        self.assertIn('oi', m['map'])
        self.assertEqual(m['map']['oi'], ['oiUsd'])
        self.assertIn('ath', m['unsupported'])

    def test_market_filter_read_only(self):
        draft = self.strategies.create_draft({
            'strategy_id': 'S-mkt',
            'title': '市场指纹',
            'data_dependencies': ['snapshot', 'funding'],
            'rules': {'entry_text': 'x', 'exit_text': 'y'},
        })
        ver = self.strategies.freeze(draft['draft_id'], {'version': '0.1.0'})
        bindings = RuleBindings(self.root, self.strategies)
        unbound = bindings.market_filter()
        self.assertFalse(unbound['bound'])
        self.assertTrue(unbound['read_only'])
        bindings.bind_surface('market', {'version_id': ver['version_id']})
        flt = bindings.market_filter()
        self.assertTrue(flt['bound'])
        self.assertTrue(flt['read_only'])
        self.assertEqual(flt['filter']['version_id'], ver['version_id'])
        self.assertTrue(flt['filter']['content_hash'])


if __name__ == '__main__':
    unittest.main()
