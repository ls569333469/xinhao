"""Offline tests for RULE-01 strategy drafts / freeze / enable gate."""
import json
import tempfile
import unittest
from pathlib import Path

from strategy_store import StrategyStore, content_hash, mutate_frozen_file_forbidden


class StrategyStoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        from test_price_index import write_scan_candles, bar, START, CONTRACT
        (self.root / 'scan.json').write_text(json.dumps({
            'rows': [{**CONTRACT, 'price':100,'oiUsd':1000}], 'total':1,'received':1,
        }), encoding='utf-8')
        write_scan_candles(self.root,'scan-'+'f'*32,CONTRACT,'4h',[bar(START+i*14400000) for i in range(20)])
        self.store = StrategyStore(self.root)

    def tearDown(self):
        self.tmp.cleanup()

    def test_create_save_freeze_immutable_and_hash(self):
        d = self.store.create_draft({
            'title': '空头拥挤试验',
            'strategy_id': 'S-squeeze1',
            'rules': {
                'entry_text': '上穿EMA99',
                'exit_text': '结构止损',
                'whitelist_trigger': {'mode': 'allow', 'symbols': ['btcusdt', 'ethusdt']},
                'whitelist_exit': {'mode': 'deny', 'symbols': ['BADUSDT']},
                'idea': '验证草案持久化',
            },
            'data_dependencies': ['price_4h', 'snapshot'],
        })
        self.assertTrue(d['draft_id'].startswith('draft-'))
        self.assertEqual(d['strategy_id'], 'S-squeeze1')
        self.assertEqual(d['rules']['whitelist_trigger']['symbols'], ['BTCUSDT', 'ETHUSDT'])
        self.assertFalse(d['executable'])

        d2 = self.store.update_draft(d['draft_id'], {
            'title': '空头拥挤试验·改',
            'rules': {
                'entry_text': '上穿EMA99并放量',
                'exit_text': '结构止损',
                'whitelist_trigger': {'mode': 'allow', 'symbols': ['BTCUSDT']},
                'whitelist_exit': {'mode': 'unset', 'symbols': []},
            },
        })
        self.assertEqual(d2['title'], '空头拥挤试验·改')
        self.assertNotEqual(d2['content_hash'], d['content_hash'])

        v = self.store.freeze(d['draft_id'], {'version': '1.0.0', 'change_note': '首冻'})
        self.assertTrue(v['immutable'])
        self.assertEqual(v['version'], '1.0.0')
        self.assertTrue(v['content_hash'])
        self.assertIn('S-squeeze1@1.0.0#', v['id_version_hash'])
        self.assertFalse(v['enabled'])
        self.assertIn('SIM', v['execution_note'])

        # frozen file must not be rewritten via API helpers
        with self.assertRaises(RuntimeError):
            mutate_frozen_file_forbidden(self.store, v['version_id'], {'title': 'hack'})

        # freeze again same version label → error
        with self.assertRaises(ValueError):
            self.store.freeze(d['draft_id'], {'version': '1.0.0'})

        # edit frozen → new draft → new version
        d3 = self.store.create_draft_from_version(v['version_id'], {'title': '空头拥挤试验·v2草案'})
        self.assertEqual(d3['parent_version_id'], v['version_id'])
        d3 = self.store.update_draft(d3['draft_id'], {
            'rules': {
                'entry_text': '新触发',
                'exit_text': '新退出',
                'whitelist_trigger': v['rules']['whitelist_trigger'],
                'whitelist_exit': v['rules']['whitelist_exit'],
            }
        })
        v2 = self.store.freeze(d3['draft_id'], {'version': '1.1.0'})
        self.assertEqual(v2['version'], '1.1.0')
        self.assertNotEqual(v2['content_hash'], v['content_hash'])
        # original frozen unchanged
        still = self.store.get_version(v['version_id'])
        self.assertEqual(still['content_hash'], v['content_hash'])
        self.assertEqual(still['rules']['entry_text'], '上穿EMA99并放量')

    def test_enable_refused_when_oi_missing(self):
        d = self.store.create_draft({
            'title': '依赖OI',
            'strategy_id': 'S-oi',
            'data_dependencies': ['price_4h', 'oi'],
            'rules': {'entry_text': 'x', 'exit_text': 'y'},
        })
        v = self.store.freeze(d['draft_id'], {'version': '0.1.0'})
        # Freeze with coverage, then remove OI to test enable-time dependency loss.
        snapshot = json.loads((self.root / 'scan.json').read_bytes())
        snapshot['rows'][0].pop('oiUsd')
        (self.root / 'scan.json').write_text(json.dumps(snapshot), encoding='utf-8')
        # Explicitly acknowledge the changed snapshot to exercise the independent dependency gate.
        out = self.store.set_enabled(v['version_id'], True, {'acknowledge_coverage_drift': True})
        self.assertTrue(out.get('enable_refused'))
        self.assertIn('oi', out['missing_dependencies'])
        self.assertFalse(out['enabled'])
        # still on disk disabled
        again = self.store.get_version(v['version_id'])
        self.assertFalse(again['enabled'])

    def test_enable_ok_with_price_and_scan_json(self):
        d = self.store.create_draft({
            'title': '仅价格',
            'strategy_id': 'S-price',
            'data_dependencies': ['snapshot'],
            'rules': {'entry_text': 'a', 'exit_text': 'b'},
        })
        v = self.store.freeze(d['draft_id'], {'version': '0.1.0'})
        out = self.store.set_enabled(v['version_id'], True)
        self.assertFalse(out.get('enable_refused'))
        self.assertTrue(out['enabled'])
        out2 = self.store.set_enabled(v['version_id'], False)
        self.assertFalse(out2['enabled'])

    def test_list_and_registry_ref(self):
        self.store.create_draft({'title': 'A', 'strategy_id': 'S-a', 'rules': {}})
        catalog = self.store.list_all()
        self.assertEqual(len(catalog['drafts']), 1)
        self.assertIn('version', catalog['registry'])
        self.assertIn('hash', catalog['registry'])
        self.assertTrue(len(catalog['registry']['hash']) == 64)

    def test_content_hash_stable(self):
        rules = {'entry_text': 'x', 'exit_text': 'y', 'whitelist_trigger': {'mode': 'unset', 'symbols': []}}
        a = content_hash(rules, ['snapshot'], {'title': 't'})
        b = content_hash(rules, ['snapshot'], {'title': 't'})
        self.assertEqual(a, b)
        c = content_hash(rules, ['snapshot', 'oi'], {'title': 't'})
        self.assertNotEqual(a, c)

    def test_from_report_excerpt_requires_verifiable_report(self):
        with self.assertRaisesRegex(ValueError, '无法核验来源报告'):
            self.store.create_draft_from_report({
                'title': '报告草案', 'report_id': 'report-deadbeef',
                'excerpt': '客户端摘录不能绕过校验', 'data_dependencies': ['snapshot'],
            })


if __name__ == '__main__':
    unittest.main()
