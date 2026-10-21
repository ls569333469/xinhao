"""Offline RULE-01 bindings tests: same ID/version/hash, mismatch reject, persist."""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from rule_bindings import RuleBindings, BindingConflict, SURFACES
from strategy_store import StrategyStore


class RuleBindingsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        # minimal registry file expected by StrategyStore
        # StrategyStore loads from project; in tests it uses ROOT/strategy-registry.json
        # Copy behavior: StrategyStore imports REGISTRY from strategy_registry module
        (self.root / 'scan.json').write_text(json.dumps({
            'rows': [{'symbol':'TESTUSDT','market':'USDⓈ-M','price':100}], 'total':1,'received':1,
        }), encoding='utf-8')
        self.store = StrategyStore(self.root)
        draft = self.store.create_draft({
            'strategy_id': 'S-bind',
            'title': '绑定测试',
            'entry': '突破',
            'exit': '止损',
            'params': {'tp_pct': 0.05},
            'data_dependencies': ['snapshot'],
        })
        # Freeze is gated too: use a genuine covered local scan fixture.
        self.ver = self.store.freeze(draft['draft_id'], {'version': '0.1.0', 'change_note': 'bind'})
        self.bindings = RuleBindings(self.root, self.store)

    def tearDown(self):
        self.tmp.cleanup()

    def test_canonical_ref_fields(self):
        ref = self.bindings.canonical_ref(self.ver['version_id'])
        self.assertEqual(ref['strategy_id'], 'S-bind')
        self.assertEqual(ref['version'], '0.1.0')
        self.assertEqual(ref['content_hash'], self.ver['content_hash'])
        self.assertIn(ref['registry_hash'], ref['unified_key'])
        self.assertTrue(ref['unified_key'].startswith('S-bind|0.1.0|'))

    def test_validate_mismatch_rejected(self):
        with self.assertRaises(BindingConflict):
            self.bindings.validate_ref({
                'version_id': self.ver['version_id'],
                'content_hash': 'deadbeef' * 8,
            })
        with self.assertRaises(BindingConflict):
            self.bindings.validate_ref({
                'version_id': self.ver['version_id'],
                'strategy_id': 'WRONG',
            })
        ok = self.bindings.validate_ref({
            'version_id': self.ver['version_id'],
            'strategy_id': 'S-bind',
            'version': '0.1.0',
            'content_hash': self.ver['content_hash'],
        })
        self.assertTrue(ok['ok'])

    def test_bind_all_unifies_surfaces(self):
        out = self.bindings.bind_all({'version_id': self.ver['version_id']})
        self.assertTrue(out['unified'])
        self.assertEqual(out['bound_count'], len(SURFACES))
        self.assertTrue(out['same_key_when_bound'])
        key = out['active_unified_key']
        for s in SURFACES:
            self.assertEqual(out['surfaces'][s]['ref']['unified_key'], key)

    def test_bind_surface_persist_restart(self):
        self.bindings.bind_surface('market', {'version_id': self.ver['version_id']})
        again = RuleBindings(self.root, self.store)
        listed = again.list_bindings()
        self.assertTrue(listed['surfaces']['market']['bound'])
        self.assertEqual(
            listed['surfaces']['market']['ref']['version_id'],
            self.ver['version_id'],
        )
        self.assertFalse(listed['surfaces']['reports']['bound'])
        self.assertTrue(listed['same_key_when_bound'])

    def test_two_versions_break_same_key(self):
        d2 = self.store.create_draft({
            'strategy_id': 'S-other',
            'title': '另一版',
            'entry': '回踩',
            'exit': '失效',
            'data_dependencies': ['snapshot'],
        })
        v2 = self.store.freeze(d2['draft_id'], {'version': '0.2.0'})
        self.bindings.bind_surface('market', {'version_id': self.ver['version_id']})
        self.bindings.bind_surface('reports', {'version_id': v2['version_id']})
        listed = self.bindings.list_bindings()
        self.assertFalse(listed['same_key_when_bound'])
        self.assertIsNone(listed['active_unified_key'])


if __name__ == '__main__':
    unittest.main()
