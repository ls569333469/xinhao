"""Offline CONFIG-01 tests: persist, secrets strip, conflict, backup wizard."""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from config_store import (
    BACKUP_SCHEMA,
    ConfigStore,
    ConfigConflict,
    _strip_secrets,
)


class ConfigStoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.store = ConfigStore(self.root)

    def tearDown(self):
        self.tmp.cleanup()

    def test_pool_persist_restart(self):
        self.store.set_observation_pool({'symbols': ['btcusdt', {'symbol': 'ETHUSDT', 'market': 'USDⓈ-M'}]})
        pool = self.store.get_observation_pool()
        self.assertEqual(len(pool['symbols']), 2)
        self.assertEqual(pool['symbols'][0]['symbol'], 'BTCUSDT')
        again = ConfigStore(self.root)
        pool2 = again.get_observation_pool()
        self.assertEqual([s['symbol'] for s in pool2['symbols']], ['BTCUSDT', 'ETHUSDT'])

    def test_prompt_edits_and_hash_conflict(self):
        st = self.store.set_prompt_edits({'overrides': {'common': '只做研究，不下单'}, 'replace': True})
        h = st['meta']['content_hash']
        self.store.set_prompt_edits({'overrides': {'common': '第二版'}, 'expected_content_hash': h})
        with self.assertRaises(ConfigConflict):
            self.store.set_prompt_edits({'overrides': {'common': '撞车'}, 'expected_content_hash': h})

    def test_export_strips_secrets_and_refuses_credentials_flag(self):
        self.store.set_observation_pool({'symbols': ['BTCUSDT']})
        self.store.set_prompt_edits({'overrides': {'common': 'ok'}, 'replace': True})
        with self.assertRaises(ValueError):
            self.store.set_prompt_edits({'overrides': {'api_key': 'x'}})
        with self.assertRaises(ValueError):
            self.store.set_prompt_edits({'overrides': {'api_key_should_never': 'LEAK'}})
        bundle = self.store.export_bundle()
        self.assertFalse(bundle['credentials_included'])
        raw = json.dumps(bundle)
        self.assertNotIn('LEAK', raw)
        cleaned, removed = _strip_secrets({'api_key': 'LEAK', 'keep': 1})
        self.assertNotIn('api_key', cleaned)
        self.assertTrue(removed)
        with self.assertRaises(ValueError):
            self.store.export_bundle(include_credentials=True)

    def test_strip_secrets_helper(self):
        cleaned, removed = _strip_secrets({'ok': 1, 'api_key': 'x', 'nested': {'secretToken': 'y', 'keep': 2}})
        self.assertEqual(cleaned['ok'], 1)
        self.assertEqual(cleaned['nested']['keep'], 2)
        self.assertNotIn('api_key', cleaned)
        self.assertTrue(any('secret' in p.lower() or 'api' in p.lower() for p in removed))

    def test_import_conflict_without_force(self):
        self.store.set_observation_pool({'symbols': ['AAAUSDT']})
        bundle = self.store.export_bundle()
        self.store.set_observation_pool({'symbols': ['BBBUSDT']})
        with self.assertRaises(ConfigConflict):
            self.store.import_bundle(bundle)
        out = self.store.import_bundle({**bundle, 'allow_divergent': True})
        self.assertEqual(out['status'], 'imported')
        self.assertEqual(self.store.get_observation_pool()['symbols'][0]['symbol'], 'AAAUSDT')


class BackupWizardTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.store = ConfigStore(self.root)
        # seed core + sim config + strategies + plans
        self.store.set_observation_pool({'symbols': ['BTCUSDT', 'ETHUSDT']})
        self.store.set_prompt_edits({'overrides': {'common': '研究专用'}, 'replace': True})
        sim_dir = self.root / 'runtime' / 'sim'
        sim_dir.mkdir(parents=True, exist_ok=True)
        (sim_dir / 'config.json').write_text(json.dumps({
            'mode': 'shared',
            'fee_rate': 0.0004,
            'slip_rate': 0.0002,
            'budget_t1_u': 1000,
            'budget_t2_u': 500,
            'budget_t3_u': 200,
            'api_key': 'SHOULD_STRIP',
        }, ensure_ascii=False), encoding='utf-8')
        (sim_dir / 'events.jsonl').write_text(
            json.dumps({'event_id': 'ev-1', 'kind': 'stub', 'secret': 'NOPE'}) + '\n',
            encoding='utf-8',
        )
        drafts = self.root / 'runtime' / 'strategies' / 'drafts'
        vers = self.root / 'runtime' / 'strategies' / 'versions'
        drafts.mkdir(parents=True, exist_ok=True)
        vers.mkdir(parents=True, exist_ok=True)
        (drafts / 'draft-abc.json').write_text(json.dumps({
            'draft_id': 'draft-abc', 'strategy_id': 'strat-1', 'title': '草案A', 'body': 'x',
        }), encoding='utf-8')
        (vers / 'ver-001.json').write_text(json.dumps({
            'version_id': 'ver-001', 'strategy_id': 'strat-1', 'version': 1,
            'title': '冻结1', 'content_hash': 'hash-aaa', 'enabled': True,
        }), encoding='utf-8')
        eval_dir = self.root / 'runtime' / 'eval'
        eval_dir.mkdir(parents=True, exist_ok=True)
        (eval_dir / 'state.json').write_text(json.dumps({
            'plans': {'eplan-1': {'plan_id': 'eplan-1', 'enabled': True, 'label': '日观察'}},
            'observation_count': 0, 'settlement_count': 0, 'open_ids': [],
        }), encoding='utf-8')
        rev_dir = self.root / 'runtime' / 'reviews'
        rev_dir.mkdir(parents=True, exist_ok=True)
        (rev_dir / 'state.json').write_text(json.dumps({
            'plans': {'rplan-1': {'plan_id': 'rplan-1', 'enabled': False}},
            'pause_new_entries': False,
        }), encoding='utf-8')

    def tearDown(self):
        self.tmp.cleanup()

    def test_catalog_exposes_wizard(self):
        cat = self.store.catalog()
        self.assertEqual(cat['backup_schema'], BACKUP_SCHEMA)
        self.assertTrue(cat['wizard']['ready'])
        self.assertEqual(cat['wizard']['steps'], ['manifest', 'export', 'review', 'import'])
        self.assertFalse(cat['wizard']['include_sim_ledger_default'])
        self.assertTrue(cat['wizard']['credentials_always_excluded'])

    def test_manifest_and_export_exclude_ledger_and_secrets(self):
        with self.assertRaises(ValueError):
            self.store.backup_manifest({'include_credentials': True})
        man = self.store.backup_manifest({})
        self.assertEqual(man['schema'], 'workbench-backup-manifest-v1')
        self.assertFalse(man['include_sim_ledger'])
        self.assertFalse(man['credentials_included'])
        ids = [s['id'] for s in man['sections']]
        self.assertEqual(ids, ['config_core', 'strategies', 'sim', 'eval_plans', 'review_plans'])
        sim_sec = next(s for s in man['sections'] if s['id'] == 'sim')
        self.assertIs(sim_sec['ledger_included'], False)

        pkg = self.store.backup_export({})
        self.assertEqual(pkg['schema'], BACKUP_SCHEMA)
        self.assertFalse(pkg['credentials_included'])
        self.assertFalse(pkg['include_sim_ledger'])
        self.assertIsNone(pkg['sections']['sim'].get('ledger'))
        raw = json.dumps(pkg)
        self.assertNotIn('SHOULD_STRIP', raw)
        self.assertNotIn('NOPE', raw)
        sim_cfg = (pkg['sections']['sim'].get('config') or {})
        self.assertNotIn('api_key', sim_cfg)
        # file written
        backups = list((self.root / 'runtime' / 'config' / 'backups').glob('backup-*.json'))
        self.assertTrue(backups)

    def test_export_with_ledger_flag(self):
        pkg = self.store.backup_export({'include_sim_ledger': True})
        self.assertTrue(pkg['include_sim_ledger'])
        ledger = pkg['sections']['sim']['ledger']
        self.assertEqual(ledger['events'][0]['event_id'], 'ev-1')
        self.assertNotIn('secret', ledger['events'][0])

    def test_review_then_import_roundtrip_fresh_target(self):
        pkg = self.store.backup_export({})
        # mutate local so review sees conflict on config_core
        self.store.set_observation_pool({'symbols': ['SOLUSDT']})
        rev = self.store.backup_review(pkg)
        self.assertGreaterEqual(rev['conflict_count'], 1)
        self.assertFalse(rev['can_import_without_force'])
        codes = {c['code'] for c in rev['conflicts']}
        self.assertIn('config_hash_fork', codes)

        with self.assertRaises(ConfigConflict):
            self.store.backup_import(pkg)

        out = self.store.backup_import({**pkg, 'force': True})
        self.assertEqual(out['status'], 'imported')
        self.assertFalse(out['sim_ledger_imported'])
        self.assertEqual(
            [s['symbol'] for s in self.store.get_observation_pool()['symbols']],
            ['BTCUSDT', 'ETHUSDT'],
        )

    def test_version_hash_conflict_refuses_silent_overwrite(self):
        pkg = self.store.backup_export({'sections': ['strategies']})
        # fork local frozen version hash
        vpath = self.root / 'runtime' / 'strategies' / 'versions' / 'ver-001.json'
        local = json.loads(vpath.read_text(encoding='utf-8'))
        local['content_hash'] = 'hash-FORKED'
        vpath.write_text(json.dumps(local), encoding='utf-8')
        rev = self.store.backup_review(pkg)
        codes = {c['code'] for c in rev['conflicts']}
        self.assertIn('version_hash_conflict', codes)
        with self.assertRaises(ConfigConflict):
            self.store.backup_import(pkg)

    def test_sim_ledger_skipped_by_default_on_import(self):
        pkg = self.store.backup_export({'include_sim_ledger': True})
        # wipe ledger then import without flag
        events = self.root / 'runtime' / 'sim' / 'events.jsonl'
        events.write_text('', encoding='utf-8')
        # also diverge config so we need force for sim_config_differs? empty config after wipe of file content - rewrite config different
        (self.root / 'runtime' / 'sim' / 'config.json').write_text(json.dumps({
            'mode': 'shared', 'fee_rate': 0.001, 'budget_t1_u': 1,
        }), encoding='utf-8')
        with self.assertRaises(ConfigConflict):
            self.store.backup_import(pkg)
        out = self.store.backup_import({**pkg, 'force': True})
        self.assertFalse(out['sim_ledger_imported'])
        skipped_reasons = ' '.join(s.get('reason', '') for s in out.get('skipped') or [])
        self.assertIn('账本', skipped_reasons)
        self.assertEqual(events.read_text(encoding='utf-8').strip(), '')

        out2 = self.store.backup_import({**pkg, 'force': True, 'import_sim_ledger': True})
        self.assertTrue(out2['sim_ledger_imported'])
        self.assertIn('ev-1', events.read_text(encoding='utf-8'))

    def test_eval_review_plans_import(self):
        pkg = self.store.backup_export({'sections': ['eval_plans', 'review_plans']})
        # clear plans
        (self.root / 'runtime' / 'eval' / 'state.json').write_text(json.dumps({
            'plans': {}, 'observation_count': 0, 'settlement_count': 0, 'open_ids': [],
        }), encoding='utf-8')
        (self.root / 'runtime' / 'reviews' / 'state.json').write_text(json.dumps({
            'plans': {}, 'pause_new_entries': False,  # match package; pause diverge covered elsewhere
        }), encoding='utf-8')
        rev = self.store.backup_review(pkg)
        self.assertEqual(rev['conflict_count'], 0)
        self.assertTrue((rev.get('integrity') or {}).get('verified'))
        out = self.store.backup_import(pkg)
        self.assertEqual(out['status'], 'imported')
        eval_st = json.loads((self.root / 'runtime' / 'eval' / 'state.json').read_text(encoding='utf-8'))
        rev_st = json.loads((self.root / 'runtime' / 'reviews' / 'state.json').read_text(encoding='utf-8'))
        self.assertIn('eplan-1', eval_st['plans'])
        self.assertIn('rplan-1', rev_st['plans'])




class SettingsBindTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.store = ConfigStore(self.root)
        self.store.set_observation_pool({'symbols': ['BTCUSDT']})
        self.store.set_prompt_edits({'overrides': {'common': 'bind-test'}, 'replace': True})

    def tearDown(self):
        self.tmp.cleanup()

    def test_settings_bind_snapshot_no_secrets(self):
        snap = self.store.settings_bind_snapshot({
            'scan_settings': {'version': 3, 'scan': {'enabled': False, 'intervalHours': 4, 'timezone': 'Asia/Shanghai', 'recoverMissed': True}},
            'ai_public': {'keyConfigured': True, 'provider': 'apikey', 'modelId': 'gpt-x', 'api_key': 'LEAK_ME', 'configId': 'ai-1'},
            'sim_config': {'fee_rate': 0.0004, 'budget_t1_u': 100, 'api_key': 'SIMLEAK', 'updated_at': 't1'},
            'eval_plans': {'plans': [{'plan_id': 'eplan-1', 'enabled': False, 'auto_enqueue_backfill': False}]},
            'review_plans': {'plans': [{'plan_id': 'plan-1', 'enabled': True}]},
        })
        self.assertEqual(snap['schema'], 'settings-bind-v1')
        self.assertTrue(snap['bind']['bidirectional'])
        self.assertFalse(snap['bind']['credentials_surfaced'])
        self.assertEqual(snap['bind']['conflict_http'], 409)
        raw = json.dumps(snap)
        self.assertNotIn('LEAK_ME', raw)
        self.assertNotIn('SIMLEAK', raw)
        self.assertTrue(snap['ai']['keyConfigured'])
        self.assertNotIn('api_key', snap['ai'])
        self.assertEqual(snap['config']['observation_pool']['symbols'][0]['symbol'], 'BTCUSDT')
        ids = [f['id'] for f in snap['bind']['fields']]
        self.assertIn('observation_pool', ids)
        self.assertIn('sim_costs_budgets', ids)
        self.assertIn('eval_plan_toggles', ids)

    def test_catalog_settings_bind_ready(self):
        cat = self.store.catalog()
        self.assertTrue(cat['settings_bind']['ready'])
        self.assertEqual(cat['settings_bind']['api'], 'GET /api/config/settings-bind')


class BackupIntegrityHardeningTests(unittest.TestCase):
    """CONFIG backup: payload hash, path escape, pause/concurrency refuse."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.store = ConfigStore(self.root)
        self.store.set_observation_pool({'symbols': ['BTCUSDT']})
        self.store.set_prompt_edits({'overrides': {'common': 'hash-harden'}, 'replace': True})
        drafts = self.root / 'runtime' / 'strategies' / 'drafts'
        vers = self.root / 'runtime' / 'strategies' / 'versions'
        drafts.mkdir(parents=True, exist_ok=True)
        vers.mkdir(parents=True, exist_ok=True)
        (drafts / 'draft-abc.json').write_text(json.dumps({
            'draft_id': 'draft-abc', 'strategy_id': 's1', 'title': 'A',
        }), encoding='utf-8')
        (vers / 'ver-001.json').write_text(json.dumps({
            'version_id': 'ver-001', 'strategy_id': 's1', 'version': 1,
            'title': 'V1', 'content_hash': 'hash-aaa', 'enabled': True,
        }), encoding='utf-8')
        rev = self.root / 'runtime' / 'reviews'
        rev.mkdir(parents=True, exist_ok=True)
        (rev / 'state.json').write_text(json.dumps({
            'plans': {'rplan-1': {'plan_id': 'rplan-1', 'enabled': True}},
            'pause_new_entries': False,
        }), encoding='utf-8')

    def tearDown(self):
        self.tmp.cleanup()

    def test_export_stamps_payload_and_package_hash(self):
        pkg = self.store.backup_export({'sections': ['config_core', 'strategies']})
        man = pkg['manifest']
        self.assertEqual(man.get('integrity'), 'manifest+payload_hash')
        self.assertTrue(man.get('package_hash'))
        for sec in man['sections']:
            self.assertTrue(sec.get('payload_hash'), sec)
            self.assertEqual(len(sec['payload_hash']), 64)
        # package_hash binds section meta including payload_hash
        from config_store import _sha
        self.assertEqual(man['package_hash'], _sha(man['sections']))

    def test_mutated_section_content_refused(self):
        pkg = self.store.backup_export({'sections': ['config_core']})
        # Tamper payload but leave hashes — must 409
        pkg['sections']['config_core']['observation_pool'] = {
            'symbols': [{'symbol': 'HACKUSDT', 'market': 'USDⓈ-M'}],
            'note': 'tampered',
        }
        with self.assertRaises(ConfigConflict) as ctx:
            self.store.backup_review(pkg)
        self.assertIn('篡改', str(ctx.exception))
        with self.assertRaises(ConfigConflict):
            self.store.backup_import(pkg)

    def test_missing_payload_hash_refused(self):
        pkg = self.store.backup_export({'sections': ['config_core']})
        for sec in pkg['manifest']['sections']:
            sec.pop('payload_hash', None)
        # package_hash still old — either package_hash mismatch OR missing payload_hash
        with self.assertRaises(ConfigConflict) as ctx:
            self.store.backup_import(pkg)
        msg = str(ctx.exception)
        self.assertTrue('payload_hash' in msg or 'package_hash' in msg, msg)

    def test_stripped_package_hash_refused(self):
        pkg = self.store.backup_export({'sections': ['config_core']})
        pkg['manifest'].pop('package_hash', None)
        with self.assertRaises(ConfigConflict) as ctx:
            self.store.backup_import(pkg)
        self.assertIn('package_hash', str(ctx.exception))

    def test_path_escape_draft_refused(self):
        pkg = self.store.backup_export({'sections': ['strategies']})
        # Inject escaping draft id into package and re-seal hashes so integrity passes
        from config_store import _payload_hash, _sha
        evil = {
            'draft_id': '../evil-outside',
            'strategy_id': 'x',
            'title': 'escape',
        }
        pkg['sections']['strategies']['drafts'] = [evil]
        # also poison refs
        pkg['sections']['strategies']['refs'] = [{'kind': 'draft', 'draft_id': evil['draft_id']}]
        # recompute seals so only path check fires (not hash)
        for meta in pkg['manifest']['sections']:
            if meta['id'] == 'strategies':
                meta['payload_hash'] = _payload_hash(pkg['sections']['strategies'])
                meta['content_hash'] = meta['payload_hash']
        pkg['manifest']['package_hash'] = _sha(pkg['manifest']['sections'])
        with self.assertRaises(ConfigConflict) as ctx:
            self.store.backup_import({**pkg, 'force': True, 'overwrite_drafts': True})
        self.assertIn('逃逸', str(ctx.exception))
        # nothing written outside strategies/drafts
        drafts = self.root / 'runtime' / 'strategies' / 'drafts'
        self.assertFalse((self.root / 'runtime' / 'strategies' / 'evil-outside.json').exists())
        self.assertFalse(any('evil' in p.name for p in drafts.glob('*.json')))

    def test_pause_conflict_refuses_silent_overwrite(self):
        pkg = self.store.backup_export({'sections': ['review_plans']})
        # Flip local pause
        rev_path = self.root / 'runtime' / 'reviews' / 'state.json'
        st = json.loads(rev_path.read_text(encoding='utf-8'))
        st['pause_new_entries'] = True
        rev_path.write_text(json.dumps(st), encoding='utf-8')
        rev = self.store.backup_review(pkg)
        codes = {c['code'] for c in rev['conflicts']}
        self.assertIn('pause_conflict', codes)
        with self.assertRaises(ConfigConflict):
            self.store.backup_import(pkg)
        # force may apply
        out = self.store.backup_import({**pkg, 'force': True})
        self.assertEqual(out['status'], 'imported')
        st2 = json.loads(rev_path.read_text(encoding='utf-8'))
        self.assertFalse(st2['pause_new_entries'])

    def test_expected_local_revision_concurrency_guard(self):
        pkg = self.store.backup_export({'sections': ['config_core']})
        rev_now = self.store.get()['meta']['revision']
        # Stale expected revision
        with self.assertRaises(ConfigConflict) as ctx:
            self.store.backup_import({
                **pkg,
                'expected_local_revision': rev_now - 1 if rev_now > 0 else 999999,
            })
        self.assertIn('revision', str(ctx.exception))
        # Matching revision + matching package hash ok when no fork
        out = self.store.backup_import({
            **pkg,
            'expected_local_revision': rev_now,
            'expected_package_hash': pkg['manifest']['package_hash'],
        })
        self.assertEqual(out['status'], 'imported')

    def test_wrong_expected_package_hash_refused(self):
        pkg = self.store.backup_export({'sections': ['config_core']})
        with self.assertRaises(ConfigConflict) as ctx:
            self.store.backup_import({
                **pkg,
                'expected_package_hash': '0' * 64,
            })
        self.assertIn('package_hash', str(ctx.exception))


if __name__ == '__main__':
    unittest.main()
