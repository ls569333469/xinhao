"""RULE-01 deepen: unified cross-surface strategy bindings (same ID/version/hash).

Surfaces (market / reports / basket / experiment / validation) must reference one
frozen version via the same strategy_id + version + content_hash + registry_hash.
Mismatch is rejected — no silent drift between pages.
"""
from __future__ import annotations

import json
import threading
from datetime import datetime, timezone
from pathlib import Path

from snapshot_store import atomic, encoded

SURFACES = ('market', 'reports', 'basket', 'experiment', 'validation')
SURFACE_TITLES = {
    'market': '市场扫描',
    'reports': '投研报告',
    'basket': '模拟篮子',
    'experiment': '实验/模拟账户',
    'validation': '验证与复盘',
}
DISCLAIMER = (
    '统一引用绑定：市场/报告/篮子/实验/验证须共用同一冻结版本的 ID/version/hash；'
    '仅引用，不自动下单。'
)


def _utcnow():
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


class BindingConflict(Exception):
    """Hash / ID mismatch or unknown surface."""


class RuleBindings:
    def __init__(self, root, strategy_store):
        self.root = Path(root)
        self.strategies = strategy_store
        self.dir = self.root / 'runtime' / 'strategies'
        self.dir.mkdir(parents=True, exist_ok=True)
        self.path = self.dir / 'bindings.json'
        self._lock = threading.RLock()
        self._ensure()

    def _ensure(self):
        with self._lock:
            if not self.path.exists():
                atomic(self.path, encoded(self._blank()))

    def _blank(self):
        return {
            'schema': 'rule-bindings-v1',
            'surfaces': {s: None for s in SURFACES},
            'updated_at': _utcnow(),
            'disclaimer': DISCLAIMER,
        }

    def _read(self):
        return json.loads(self.path.read_text(encoding='utf-8'))

    def _save(self, state):
        state['updated_at'] = _utcnow()
        state['disclaimer'] = DISCLAIMER
        atomic(self.path, encoded(state))
        return state

    def canonical_ref(self, version_id):
        v = self.strategies.get_version(version_id)
        reg = self.strategies.registry_ref()
        content_hash = v['content_hash']
        id_version_hash = v.get('id_version_hash') or (
            f"{v['strategy_id']}@{v['version']}#{content_hash[:12]}"
        )
        from rule_export_gate import canonical_unified_key
        unified_key = canonical_unified_key(
            v['strategy_id'], v['version'], content_hash, reg['hash'],
        )
        return {
            'strategy_id': v['strategy_id'],
            'version_id': v['version_id'],
            'version': v['version'],
            'title': v.get('title'),
            'content_hash': content_hash,
            'id_version_hash': id_version_hash,
            'enabled': bool(v.get('enabled')),
            'registry_version': reg['version'],
            'registry_hash': reg['hash'],
            'unified_key': unified_key,
            'executable': False,
            'note': '统一引用指纹；非自动交易',
        }

    def validate_ref(self, body):
        if not isinstance(body, dict):
            raise ValueError('请求体须为对象')
        version_id = body.get('version_id')
        if not version_id or not isinstance(version_id, str):
            raise ValueError('须提供 version_id')
        try:
            ref = self.canonical_ref(version_id)
        except KeyError as exc:
            raise ValueError(f'冻结版本不存在: {version_id}') from exc

        mismatches = []
        for field, expected in (
            ('strategy_id', ref['strategy_id']),
            ('version', ref['version']),
            ('content_hash', ref['content_hash']),
            ('registry_hash', ref['registry_hash']),
        ):
            if field in body and body[field] is not None and str(body[field]) != str(expected):
                mismatches.append({
                    'field': field,
                    'provided': body[field],
                    'expected': expected,
                })
        if mismatches:
            raise BindingConflict(
                '引用指纹不一致：' + '; '.join(
                    f"{m['field']} 提供 {m['provided']} ≠ 冻结 {m['expected']}"
                    for m in mismatches
                )
            )
        return {
            'ok': True,
            'ref': ref,
            'mismatches': [],
            'disclaimer': DISCLAIMER,
        }

    def list_bindings(self):
        with self._lock:
            state = self._read()
            surfaces = {}
            keys = set()
            for s in SURFACES:
                slot = state['surfaces'].get(s)
                if not slot:
                    surfaces[s] = {
                        'surface': s,
                        'title': SURFACE_TITLES[s],
                        'bound': False,
                        'ref': None,
                    }
                    continue
                try:
                    ref = self.canonical_ref(slot['version_id'])
                    drift = []
                    for field in ('content_hash', 'strategy_id', 'version', 'registry_hash'):
                        if slot.get(field) and slot.get(field) != ref.get(field):
                            drift.append(field)
                    surfaces[s] = {
                        'surface': s,
                        'title': SURFACE_TITLES[s],
                        'bound': True,
                        'ref': ref,
                        'bound_at': slot.get('bound_at'),
                        'drift_fields': drift,
                        'coherent_with_store': not drift,
                        'rule_fingerprint': slot.get('rule_fingerprint'),
                        'coverage_fingerprint': slot.get('coverage_fingerprint'),
                        'joint_hash': slot.get('joint_hash'),
                    }
                    keys.add(ref['unified_key'])
                except (KeyError, ValueError) as exc:
                    surfaces[s] = {
                        'surface': s,
                        'title': SURFACE_TITLES[s],
                        'bound': True,
                        'ref': None,
                        'broken': True,
                        'error': str(exc)[:200],
                        'stale': slot,
                    }
            all_bound = all(
                surfaces[s].get('bound') and surfaces[s].get('ref') and not surfaces[s].get('drift_fields')
                for s in SURFACES
            )
            coherent = all_bound and len(keys) == 1
            partial = len(keys) <= 1 and any(surfaces[s].get('bound') for s in SURFACES)
            return {
                'schema': 'rule-bindings-v1',
                'surfaces': surfaces,
                'surface_order': list(SURFACES),
                'unified': coherent,
                'same_key_when_bound': len(keys) <= 1,
                'active_unified_key': next(iter(keys)) if len(keys) == 1 else None,
                'bound_count': sum(
                    1 for s in SURFACES if surfaces[s].get('bound') and surfaces[s].get('ref')
                ),
                'updated_at': state.get('updated_at'),
                'disclaimer': DISCLAIMER,
                'note': (
                    '全部页面已绑定且指纹一致' if coherent
                    else (
                        '已绑定页共用同一指纹（仍有未绑定页）'
                        if partial and len(keys) <= 1
                        else '存在未绑定页或指纹不一致'
                    )
                ),
            }


    def _coverage_hard_gate(self, version_id):
        """Reject bind when frozen version declared deps are not coverage-covered."""
        ver = self.strategies.get_version(version_id)
        deps = ver.get('data_dependencies') or []
        gate = self.strategies.check_dependencies(deps)
        if not gate.get('ok'):
            raise BindingConflict(
                '覆盖硬闸拒绝绑定: 缺 '
                + ', '.join(gate.get('missing_dependencies') or [])
                + '（须 covered；不宣称 ATH/六指标齐全）'
            )
        return gate, ver

    def market_filter(self):
        """Read-only market filter fingerprint from market surface binding."""
        listed = self.list_bindings()
        slot = (listed.get('surfaces') or {}).get('market') or {}
        if not slot.get('bound') or not slot.get('ref'):
            return {
                'bound': False,
                'read_only': True,
                'filter': None,
                'note': '市场页尚未绑定冻结版本；筛选不应假装已对齐指纹',
                'disclaimer': DISCLAIMER,
            }
        ref = slot['ref']
        return {
            'bound': True,
            'read_only': True,
            'filter': {
                'strategy_id': ref['strategy_id'],
                'version_id': ref['version_id'],
                'version': ref['version'],
                'content_hash': ref['content_hash'],
                'registry_hash': ref['registry_hash'],
                'unified_key': ref['unified_key'],
                'id_version_hash': ref.get('id_version_hash'),
            },
            'forced': True,
            'note': '市场筛选应只读消费此冻结指纹，禁止手改 hash',
            'disclaimer': DISCLAIMER,
        }

    def bind_surface(self, surface, body):
        if surface not in SURFACES:
            raise ValueError(f'未知页面: {surface}；仅支持 {", ".join(SURFACES)}')
        if not isinstance(body, dict):
            raise ValueError('请求体须为对象')
        version_id = body.get('version_id')
        if not version_id:
            raise ValueError('须提供 version_id')
        self._coverage_hard_gate(version_id)
        self.validate_ref({
            'version_id': version_id,
            'strategy_id': body.get('strategy_id'),
            'version': body.get('version'),
            'content_hash': body.get('content_hash'),
            'registry_hash': body.get('registry_hash'),
        })
        ref = self.canonical_ref(version_id)
        try:
            from rule_export_gate import capture_coverage_slice, build_joint_fingerprint
            cov = capture_coverage_slice(self.root)
            if not cov.get('ok'):
                raise BindingConflict(
                    '绑定硬闸拒绝：无法采集 coverage_fingerprint；'
                    + (cov.get('reason') or '')
                )
            rule_fp = build_joint_fingerprint(ref, cov, surface=surface)
        except BindingConflict:
            raise
        except Exception as exc:
            raise BindingConflict(f'绑定硬闸拒绝：指纹采集失败: {exc}') from exc
        with self._lock:
            state = self._read()
            state['surfaces'][surface] = {
                'version_id': ref['version_id'],
                'strategy_id': ref['strategy_id'],
                'version': ref['version'],
                'content_hash': ref['content_hash'],
                'registry_hash': ref['registry_hash'],
                'unified_key': ref['unified_key'],
                'bound_at': _utcnow(),
                'rule_fingerprint': rule_fp,
                'coverage_fingerprint': rule_fp.get('coverage_fingerprint'),
                'joint_hash': rule_fp.get('joint_hash'),
            }
            self._save(state)
        return self.list_bindings()

    def bind_all(self, body):
        """Bind every surface to the same frozen version (unified closed loop)."""
        if not isinstance(body, dict):
            raise ValueError('请求体须为对象')
        version_id = body.get('version_id')
        if not version_id:
            raise ValueError('须提供 version_id')
        self._coverage_hard_gate(version_id)
        self.validate_ref({
            'version_id': version_id,
            'strategy_id': body.get('strategy_id'),
            'version': body.get('version'),
            'content_hash': body.get('content_hash'),
            'registry_hash': body.get('registry_hash'),
        })
        ref = self.canonical_ref(version_id)
        try:
            from rule_export_gate import capture_coverage_slice, build_joint_fingerprint
            cov = capture_coverage_slice(self.root)
            if not cov.get('ok'):
                raise BindingConflict(
                    '绑定硬闸拒绝：无法采集 coverage_fingerprint；'
                    + (cov.get('reason') or '')
                )
            rule_fp = build_joint_fingerprint(ref, cov, surface='bind-all')
        except BindingConflict:
            raise
        except Exception as exc:
            raise BindingConflict(f'绑定硬闸拒绝：指纹采集失败: {exc}') from exc
        with self._lock:
            state = self._read()
            slot = {
                'version_id': ref['version_id'],
                'strategy_id': ref['strategy_id'],
                'version': ref['version'],
                'content_hash': ref['content_hash'],
                'registry_hash': ref['registry_hash'],
                'unified_key': ref['unified_key'],
                'bound_at': _utcnow(),
                'rule_fingerprint': rule_fp,
                'coverage_fingerprint': rule_fp.get('coverage_fingerprint'),
                'joint_hash': rule_fp.get('joint_hash'),
            }
            for s in SURFACES:
                state['surfaces'][s] = dict(slot)
            self._save(state)
        out = self.list_bindings()
        out['action'] = 'bind_all'
        out['ref'] = ref
        return out

    def unbind_surface(self, surface):
        if surface not in SURFACES:
            raise ValueError(f'未知页面: {surface}')
        with self._lock:
            state = self._read()
            state['surfaces'][surface] = None
            self._save(state)
        return self.list_bindings()

    def catalog(self):
        return {
            'schema': 'rule-bindings-catalog-v1',
            'slice': 'RULE-01-bindings',
            'surfaces': [{'id': s, 'title': SURFACE_TITLES[s]} for s in SURFACES],
            'disclaimer': DISCLAIMER,
            'fields': [
                'strategy_id', 'version', 'version_id',
                'content_hash', 'registry_hash', 'unified_key',
                'coverage_fingerprint', 'joint_hash', 'rule_fingerprint',
            ],
            'fingerprint_schema': 'rule-export-fingerprint-v2',
            'note': '绑定落盘须携带 coverage_fingerprint + strategy version hash；导入/启用核验陈旧指纹→409',
        }
