"""RULE-01 export fingerprint deepen: joint strategy+coverage FP; import/enable 409."""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from strategy_store import StrategyStore
from rule_bindings import RuleBindings, BindingConflict
from rule_export_gate import (
    resolve_export_fingerprint,
    stamp_export_payload,
    capture_coverage_slice,
    build_joint_fingerprint,
    assert_fingerprint_match,
    verify_enable_or_import,
    JOINT_SCHEMA,
)


class RuleExportFingerprintTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        rows = [{
            'symbol': 'AAAUSDT', 'market': 'USDⓈ-M', 'price': 10.0, 'markPrice': 10.0,
            'oiUsd': 1.0, 'capUsd': 1.0, 'accountRatio': 0.5, 'fundingAprPct': 1.0,
            'volumeRatio': 1.0, 'closed4h': 9.0, 'ema4h': [1, 2, 3],
        }]
        (self.root / 'scan.json').write_text(json.dumps({
            'scan_id': 's', 'revision': 'r1', 'total': 1, 'received': 1, 'rows': rows,
        }), encoding='utf-8')
        self.strategies = StrategyStore(self.root)
        self.bindings = RuleBindings(self.root, self.strategies)

    def tearDown(self):
        self.tmp.cleanup()

    def _freeze_ok(self, sid='S-fp', version='0.1.0'):
        draft = self.strategies.create_draft({
            'strategy_id': sid,
            'title': '指纹测',
            'data_dependencies': ['snapshot', 'funding'],
            'rules': {'entry_text': 'e', 'exit_text': 'x'},
        })
        return self.strategies.freeze(draft['draft_id'], {'version': version})

    def _honest_repackage(self, pkg, strategy_id, version):
        """Remap identity + recompute declared hashes so only the intended gate is tested."""
        from strategy_store import content_hash
        pkg = dict(pkg)
        ver = dict(pkg['version'])
        ver['strategy_id'] = strategy_id
        ver['version'] = version
        meta = {
            'title': ver.get('title') or strategy_id,
            'strategy_id': strategy_id,
            'version': version,
            'source_report_id': ver.get('source_report_id'),
        }
        h = content_hash(ver['rules'], ver.get('data_dependencies') or [], meta)
        ver['content_hash'] = h
        pkg['version'] = ver
        pkg['content_hash'] = h
        for key in ('rule_fingerprint', 'joint_fingerprint'):
            if isinstance(pkg.get(key), dict):
                fp = dict(pkg[key])
                fp['content_hash'] = h
                fp['strategy_id'] = strategy_id
                fp['version'] = version
                pkg[key] = fp
        return pkg

    def test_export_rejects_when_unbound(self):
        with self.assertRaises(BindingConflict) as cm:
            resolve_export_fingerprint(self.bindings, require_bound=True)
        self.assertIn('导出硬闸', str(cm.exception))

    def test_export_ok_after_reports_bind(self):
        ver = self._freeze_ok()
        self.bindings.bind_surface('reports', {'version_id': ver['version_id']})
        gate = resolve_export_fingerprint(self.bindings, root=self.root)
        self.assertTrue(gate['bound'])
        self.assertEqual(gate['surface'], 'reports')
        self.assertEqual(gate['fingerprint']['version_id'], ver['version_id'])
        self.assertIn('strategy_fingerprint', gate['header_md'])
        self.assertIn('unified_key=', gate['header_csv'])
        self.assertTrue(gate['fingerprint'].get('coverage_fingerprint'))
        self.assertTrue(gate['fingerprint'].get('joint_hash'))
        self.assertEqual(gate['joint_fingerprint']['schema'], JOINT_SCHEMA)

    def test_export_falls_back_to_market(self):
        ver = self._freeze_ok('S-m')
        self.bindings.bind_surface('market', {'version_id': ver['version_id']})
        gate = resolve_export_fingerprint(self.bindings, root=self.root)
        self.assertEqual(gate['surface'], 'market')
        self.assertTrue(gate['fingerprint']['unified_key'])
        self.assertTrue(gate['fingerprint'].get('coverage_fingerprint'))

    def test_stamp_payload_attaches_fingerprint(self):
        ver = self._freeze_ok('S-st')
        self.bindings.bind_surface('reports', {'version_id': ver['version_id']})
        stamped, gate = stamp_export_payload(
            self.bindings,
            {'content': '# hi', 'content_format': 'markdown', 'contracts': ['AAAUSDT']},
            root=self.root,
        )
        self.assertEqual(stamped['unified_key'], gate['fingerprint']['unified_key'])
        self.assertIn('strategy_fingerprint', stamped['content'])
        self.assertEqual(stamped['strategy_fingerprint']['version_id'], ver['version_id'])
        self.assertTrue(stamped.get('coverage_fingerprint'))
        self.assertEqual(stamped['rule_export_gate']['schema'], JOINT_SCHEMA)

    def test_stamp_rejects_unbound(self):
        with self.assertRaises(BindingConflict):
            stamp_export_payload(self.bindings, {'content': 'x', 'contracts': []})

    def test_market_filter_read_only_matches_bind(self):
        ver = self._freeze_ok('S-mf')
        unbound = self.bindings.market_filter()
        self.assertFalse(unbound['bound'])
        self.assertTrue(unbound['read_only'])
        self.bindings.bind_surface('market', {'version_id': ver['version_id']})
        flt = self.bindings.market_filter()
        self.assertTrue(flt['bound'])
        self.assertTrue(flt['read_only'])
        self.assertTrue(flt.get('forced'))
        self.assertEqual(flt['filter']['content_hash'], ver['content_hash'])

    def test_freeze_carries_rule_fingerprint(self):
        ver = self._freeze_ok('S-fr')
        fp = ver.get('rule_fingerprint') or {}
        self.assertEqual(fp.get('schema'), JOINT_SCHEMA)
        self.assertEqual(fp.get('content_hash'), ver['content_hash'])
        self.assertTrue(fp.get('coverage_fingerprint'))
        self.assertTrue(fp.get('joint_hash'))
        self.assertEqual(fp.get('coverage_expected'), 1)
        self.assertEqual(fp.get('coverage_received'), 1)

    def test_bind_persists_coverage_fingerprint(self):
        ver = self._freeze_ok('S-bd')
        listed = self.bindings.bind_surface('reports', {'version_id': ver['version_id']})
        slot = listed['surfaces']['reports']
        self.assertTrue(slot.get('coverage_fingerprint'))
        self.assertTrue(slot.get('joint_hash'))
        self.assertEqual(
            (slot.get('rule_fingerprint') or {}).get('content_hash'),
            ver['content_hash'],
        )

    def test_strategy_export_version_package(self):
        ver = self._freeze_ok('S-ex')
        pkg = self.strategies.export_version(ver['version_id'])
        self.assertEqual(pkg['schema'], 'strategy-export-v1')
        self.assertEqual(pkg['fingerprint_schema'], JOINT_SCHEMA)
        self.assertEqual(pkg['content_hash'], ver['content_hash'])
        self.assertTrue(pkg['coverage_fingerprint'])
        self.assertTrue(pkg['joint_hash'])
        self.assertEqual(pkg['rule_fingerprint']['version_id'], ver['version_id'])

    def test_import_ok_matching_fingerprint(self):
        ver = self._freeze_ok('S-im')
        pkg = self._honest_repackage(
            self.strategies.export_version(ver['version_id']), 'S-im2', '0.2.0-imp',
        )
        imported = self.strategies.import_version(pkg)
        self.assertEqual(imported['status'], 'imported')
        self.assertTrue(imported['fingerprint_verified'])
        self.assertEqual(imported['source_content_hash'], pkg['version']['content_hash'])
        self.assertTrue((imported['version'].get('rule_fingerprint') or {}).get('coverage_fingerprint'))

    def test_import_refuses_missing_coverage_fingerprint(self):
        ver = self._freeze_ok('S-miss')
        from strategy_store import content_hash
        meta = {
            'title': 'bare',
            'strategy_id': 'S-miss2',
            'version': '9.9.9',
            'source_report_id': None,
        }
        h = content_hash(ver['rules'], ver['data_dependencies'], meta)
        bare = {
            'version': {
                'strategy_id': 'S-miss2',
                'version': '9.9.9',
                'content_hash': h,
                'rules': ver['rules'],
                'data_dependencies': ver['data_dependencies'],
                'title': 'bare',
            },
        }
        with self.assertRaises(BindingConflict) as cm:
            self.strategies.import_version(bare)
        self.assertIn('coverage_fingerprint', str(cm.exception))

    def test_import_refuses_stale_coverage_fingerprint(self):
        ver = self._freeze_ok('S-stale')
        pkg = self._honest_repackage(
            self.strategies.export_version(ver['version_id']), 'S-stale2', '0.3.0',
        )
        # Tamper coverage fingerprint only (rules hash stays honest)
        pkg['coverage_fingerprint'] = 'deadbeef' * 8
        pkg['rule_fingerprint'] = dict(pkg['rule_fingerprint'])
        pkg['rule_fingerprint']['coverage_fingerprint'] = 'deadbeef' * 8
        pkg['joint_fingerprint'] = pkg['rule_fingerprint']
        with self.assertRaises(BindingConflict) as cm:
            self.strategies.import_version(pkg)
        msg = str(cm.exception)
        self.assertTrue('coverage_fingerprint' in msg or '指纹核验' in msg, msg)

    def test_import_refuses_content_hash_mismatch(self):
        ver = self._freeze_ok('S-ch')
        pkg = self._honest_repackage(
            self.strategies.export_version(ver['version_id']), 'S-ch2', '0.4.0',
        )
        pkg['version'] = dict(pkg['version'])
        pkg['version']['content_hash'] = '0' * 64  # declared lies; rules unchanged
        with self.assertRaises(BindingConflict) as cm:
            self.strategies.import_version(pkg)
        self.assertIn('content_hash', str(cm.exception))

    def test_enable_refuses_coverage_drift(self):
        ver = self._freeze_ok('S-en')
        # Mutate scan so coverage fingerprint changes
        rows = [{
            'symbol': 'BBBUSDT', 'market': 'USDⓈ-M', 'price': 20.0, 'markPrice': 20.0,
            'oiUsd': 2.0, 'capUsd': 2.0, 'accountRatio': 0.6, 'fundingAprPct': 2.0,
            'volumeRatio': 2.0, 'closed4h': 19.0, 'ema4h': [4, 5, 6],
        }]
        (self.root / 'scan.json').write_text(json.dumps({
            'scan_id': 's2', 'revision': 'r2', 'total': 2, 'received': 1, 'rows': rows,
        }), encoding='utf-8')
        pub = self.strategies.set_enabled(ver['version_id'], True, body={})
        self.assertTrue(pub.get('enable_refused'))
        self.assertIn('覆盖指纹', pub.get('fingerprint_refusal') or pub.get('enable_block_reason') or '')

    def test_enable_ok_with_acknowledge_drift(self):
        ver = self._freeze_ok('S-ack')
        rows = [{
            'symbol': 'CCCUSDT', 'market': 'USDⓈ-M', 'price': 30.0, 'markPrice': 30.0,
            'oiUsd': 3.0, 'capUsd': 3.0, 'accountRatio': 0.7, 'fundingAprPct': 3.0,
            'volumeRatio': 3.0, 'closed4h': 29.0, 'ema4h': [7, 8, 9],
        }]
        (self.root / 'scan.json').write_text(json.dumps({
            'scan_id': 's3', 'revision': 'r3', 'total': 1, 'received': 1, 'rows': rows,
        }), encoding='utf-8')
        pub = self.strategies.set_enabled(
            ver['version_id'], True,
            body={'acknowledge_coverage_drift': True},
        )
        # May still refuse on dependency coverage if funding etc. — but fingerprint gate passed
        if pub.get('enable_refused'):
            self.assertNotIn('覆盖指纹', pub.get('fingerprint_refusal') or '')
            # dependency refusal is OK path after fingerprint ack
            self.assertTrue(pub.get('enable_block_reason'))
        else:
            self.assertTrue(pub.get('enabled'))
            self.assertTrue(pub.get('last_enable_fingerprint'))

    def test_enable_refuses_mismatched_expected_content_hash(self):
        ver = self._freeze_ok('S-ech')
        pub = self.strategies.set_enabled(
            ver['version_id'], True,
            body={'expected_content_hash': 'f' * 64},
        )
        self.assertTrue(pub.get('enable_refused'))
        self.assertIn('content_hash', pub.get('fingerprint_refusal') or '')

    def test_assert_fingerprint_match_clear_409_reason(self):
        cov = capture_coverage_slice(self.root)
        self.assertTrue(cov['ok'])
        live = build_joint_fingerprint({
            'strategy_id': 'S', 'version_id': 'ver-aaaaaaaaaaaa',
            'version': '1', 'content_hash': 'a' * 64, 'registry_hash': 'b' * 64,
            'unified_key': 'uk',
        }, cov)
        with self.assertRaises(BindingConflict) as cm:
            assert_fingerprint_match(
                {'coverage_fingerprint': 'zzzz', 'content_hash': 'a' * 64},
                live,
            )
        self.assertIn('coverage_fingerprint', str(cm.exception))

    def test_capture_aligns_with_coverage_gate_expected_received(self):
        cov = capture_coverage_slice(self.root)
        self.assertTrue(cov['ok'])
        self.assertEqual(cov['expected'], 1)
        self.assertEqual(cov['received'], 1)
        self.assertTrue(cov['coverage_fingerprint'])


    # ----- P1 / P2 audit regressions (must fail before fix) -----

    def test_p1_import_refuses_tampered_rules_keeping_declared_hash(self):
        """RULE-IMPORT-HASH: mutate rules.params but keep content_hash → 409, never verified."""
        ver = self._freeze_ok('S-p1')
        pkg = self.strategies.export_version(ver['version_id'])
        pkg['version'] = dict(pkg['version'])
        rules = dict(pkg['version']['rules'] or {})
        params = dict(rules.get('params') or {})
        # Tamper entry rule while freezing declared hashes
        params['entry_rule'] = 'breakout' if params.get('entry_rule') != 'breakout' else 'flat_reenter'
        if not params.get('entry_rule'):
            params['entry_rule'] = 'breakout'
        rules['params'] = params
        rules['entry_text'] = (rules.get('entry_text') or 'e') + '|TAMPERED'
        pkg['version']['rules'] = rules
        # Keep pkg['version']['content_hash'], rule_fingerprint, joint_fingerprint as-is
        with self.assertRaises(BindingConflict) as cm:
            self.strategies.import_version(pkg)
        msg = str(cm.exception)
        self.assertIn('content_hash', msg)
        self.assertTrue(
            '不一致' in msg or '篡改' in msg or 'RULE-IMPORT-HASH' in msg,
            msg,
        )
        # Must not have written a verified import under the tampered strategy
        # (no successful return with fingerprint_verified)

    def test_p2_joint_hash_stable_across_freeze_and_bindings(self):
        """RULE-JOINT-KEY: freeze joint_from_version == bind path for same version+coverage."""
        from rule_export_gate import (
            capture_coverage_slice, joint_from_version, build_joint_fingerprint,
            canonical_unified_key,
        )
        ver = self._freeze_ok('S-p2')
        cov = capture_coverage_slice(self.root)
        self.assertTrue(cov.get('ok'))
        # Path A: freeze-time style (joint_from_version)
        j_freeze = joint_from_version(ver, cov, surface='freeze')
        # Path B: bindings canonical_ref style (full hashes)
        ref = self.bindings.canonical_ref(ver['version_id'])
        j_bind = build_joint_fingerprint(ref, cov, surface='market')
        self.assertEqual(
            ref['unified_key'],
            canonical_unified_key(
                ver['strategy_id'], ver['version'],
                ver['content_hash'], ref['registry_hash'],
            ),
        )
        self.assertEqual(
            j_freeze['unified_key'], ref['unified_key'],
            'unified_key truncation mismatch (P2)',
        )
        self.assertEqual(
            j_freeze['joint_hash'], j_bind['joint_hash'],
            'joint_hash must match across freeze/export vs bindings for same coverage',
        )
        # enable must not need acknowledge_coverage_drift solely due to encoding
        pub = self.strategies.set_enabled(ver['version_id'], True, body={})
        if pub.get('enable_refused'):
            reason = pub.get('fingerprint_refusal') or pub.get('enable_block_reason') or ''
            self.assertNotIn('joint_hash', reason)
            # dependency refusal OK; encoding mismatch not OK
            self.assertNotIn('不匹配', reason) if '覆盖指纹' not in reason else None


if __name__ == '__main__':
    unittest.main()
