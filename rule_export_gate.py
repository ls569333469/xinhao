"""RULE-01 export fingerprint gate (deepen): strategy hash + coverage fingerprint.

Export / freeze / bindings must carry joint fingerprint aligned with
coverage_gate expected/received fingerprint. Import/enable refuse stale or
mismatched fingerprints with clear 409 reasons.
"""
from __future__ import annotations

import hashlib
import json

from rule_bindings import BindingConflict, DISCLAIMER

PREFER_SURFACES = ('reports', 'market')
JOINT_SCHEMA = 'rule-export-fingerprint-v2'


def canonical_unified_key(strategy_id, version, content_hash, registry_hash):
    """Single encoding for bindings/export/freeze/enable joint keys.

    Uses FULL content_hash and registry_hash (never truncated). Same version
    + same coverage must yield the same joint_hash across pages.
    """
    return '|'.join([
        str(strategy_id or ''),
        str(version or ''),
        str(content_hash or ''),
        str(registry_hash or ''),
    ])



def _sha(payload):
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode('utf-8')
    return hashlib.sha256(raw).hexdigest()


def _slot_ref(listed, surface):
    slot = (listed.get('surfaces') or {}).get(surface) or {}
    if slot.get('bound') and slot.get('ref'):
        return dict(slot['ref']), surface
    return None, surface


def capture_coverage_slice(root, candle_limit=40):
    """Align with DATA-01 coverage_gate fingerprint (expected/received/status)."""
    try:
        from coverage_gate import evaluate, coverage_fingerprint
    except Exception as exc:  # pragma: no cover
        return {
            'ok': False,
            'coverage_fingerprint': None,
            'expected': None,
            'received': None,
            'aggregate_status': 'unknown',
            'reason': f'coverage_gate 不可用: {exc}',
        }
    try:
        report = evaluate(root, candle_limit=candle_limit, include_gaps=False)
    except FileNotFoundError as exc:
        return {
            'ok': False,
            'coverage_fingerprint': None,
            'expected': None,
            'received': None,
            'aggregate_status': 'unknown',
            'reason': f'缺少扫描快照: {exc}',
        }
    totals = (report.get('scan') or {}).get('scan_totals') or {}
    fp = coverage_fingerprint(report)
    return {
        'ok': True,
        'coverage_fingerprint': fp.get('hex'),
        'coverage_fingerprint_meta': fp,
        'expected': totals.get('total'),
        'received': totals.get('received'),
        'aggregate_status': (report.get('aggregate') or {}).get('status'),
        'scan_id': (report.get('scan') or {}).get('scan_id'),
        'revision': (report.get('scan') or {}).get('revision'),
        'slice': report.get('slice'),
    }


def build_joint_fingerprint(strategy_ref, coverage_slice, surface=None):
    """Combine strategy version hash + coverage fingerprint into joint record."""
    if not isinstance(strategy_ref, dict):
        raise ValueError('strategy_ref 须为对象')
    cov = coverage_slice if isinstance(coverage_slice, dict) else {}
    core = {
        'strategy_id': strategy_ref.get('strategy_id'),
        'version_id': strategy_ref.get('version_id'),
        'version': strategy_ref.get('version'),
        'content_hash': strategy_ref.get('content_hash'),
        'registry_hash': strategy_ref.get('registry_hash'),
        'unified_key': strategy_ref.get('unified_key'),
        'coverage_fingerprint': cov.get('coverage_fingerprint'),
        'coverage_expected': cov.get('expected'),
        'coverage_received': cov.get('received'),
        'coverage_aggregate': cov.get('aggregate_status'),
    }
    joint_hash = _sha(core)
    out = {
        'schema': JOINT_SCHEMA,
        **core,
        'id_version_hash': strategy_ref.get('id_version_hash') or (
            f"{core['strategy_id']}@{core['version']}#{str(core['content_hash'] or '')[:12]}"
            if core.get('strategy_id') and core.get('content_hash') else None
        ),
        'joint_hash': joint_hash,
        'surface': surface,
        'coverage_ok': bool(cov.get('ok', True) and cov.get('coverage_fingerprint')),
        'disclaimer': DISCLAIMER,
    }
    return out


def joint_from_version(version, coverage_slice, surface=None):
    content_hash = version.get('content_hash')
    registry_hash = (
        (version.get('registry_ref') or {}).get('hash')
        or version.get('registry_hash')
    )
    ref = {
        'strategy_id': version.get('strategy_id'),
        'version_id': version.get('version_id'),
        'version': version.get('version'),
        'content_hash': content_hash,
        'registry_hash': registry_hash,
        # Always rebuild with FULL hashes — never trust truncated unified_key
        'unified_key': canonical_unified_key(
            version.get('strategy_id'),
            version.get('version'),
            content_hash,
            registry_hash,
        ),
        'id_version_hash': version.get('id_version_hash'),
    }
    return build_joint_fingerprint(ref, coverage_slice, surface=surface)


def resolve_export_fingerprint(bindings, prefer_surfaces=PREFER_SURFACES, require_bound=True, root=None):
    """Return strategy fingerprint (+ coverage joint when root given)."""
    listed = bindings.list_bindings()
    chosen = None
    surface_used = None
    for surface in prefer_surfaces:
        ref, _ = _slot_ref(listed, surface)
        if ref:
            chosen = ref
            surface_used = surface
            break
    if not chosen:
        if require_bound:
            raise BindingConflict(
                '导出硬闸拒绝：报告/市场页均未绑定冻结版本；'
                '须先绑定后再导出（禁止无 fingerprint 落盘）'
            )
        return {
            'bound': False,
            'require_bound': require_bound,
            'fingerprint': None,
            'joint_fingerprint': None,
            'surface': None,
            'header_md': '',
            'header_csv': '',
            'note': '未绑定；require_bound=false 时仅提示',
            'disclaimer': DISCLAIMER,
        }

    coverage_slice = None
    joint = None
    root = root if root is not None else getattr(bindings, 'root', None)
    if root is not None:
        coverage_slice = capture_coverage_slice(root)
        if not coverage_slice.get('ok'):
            if require_bound:
                raise BindingConflict(
                    '导出硬闸拒绝：无法采集 coverage_fingerprint（'
                    + (coverage_slice.get('reason') or '未知')
                    + '）；禁止无覆盖指纹落盘'
                )
        else:
            joint = build_joint_fingerprint(chosen, coverage_slice, surface=surface_used)

    fp = {
        'strategy_id': chosen['strategy_id'],
        'version_id': chosen['version_id'],
        'version': chosen['version'],
        'content_hash': chosen['content_hash'],
        'registry_hash': chosen['registry_hash'],
        'unified_key': chosen['unified_key'],
        'id_version_hash': chosen.get('id_version_hash'),
        'surface': surface_used,
    }
    if joint:
        fp['coverage_fingerprint'] = joint.get('coverage_fingerprint')
        fp['coverage_expected'] = joint.get('coverage_expected')
        fp['coverage_received'] = joint.get('coverage_received')
        fp['coverage_aggregate'] = joint.get('coverage_aggregate')
        fp['joint_hash'] = joint.get('joint_hash')

    short = (fp['content_hash'] or '')[:12]
    cov_short = str(fp.get('coverage_fingerprint') or '')[:12]
    header_md = (
        f"<!-- strategy_fingerprint "
        f"surface={surface_used} "
        f"strategy_id={fp['strategy_id']} "
        f"version={fp['version']} "
        f"version_id={fp['version_id']} "
        f"content_hash={fp['content_hash']} "
        f"registry_hash={fp['registry_hash']} "
        f"unified_key={fp['unified_key']} "
        f"coverage_fingerprint={fp.get('coverage_fingerprint') or ''} "
        f"coverage_expected={fp.get('coverage_expected')} "
        f"coverage_received={fp.get('coverage_received')} "
        f"joint_hash={fp.get('joint_hash') or ''} "
        f"-->\n"
        f"## 策略指纹（RULE-01 强制 · 含覆盖指纹）\n\n"
        f"- 页面：{surface_used}\n"
        f"- 策略：`{fp['strategy_id']}` @{fp['version']}\n"
        f"- version_id：`{fp['version_id']}`\n"
        f"- content_hash：`{short}…`\n"
        f"- coverage_fingerprint：`{cov_short or '—'}…` "
        f"(expected={fp.get('coverage_expected')} / received={fp.get('coverage_received')})\n"
        f"- joint_hash：`{str(fp.get('joint_hash') or '')[:12] or '—'}…`\n"
        f"- unified_key：`{fp['unified_key']}`\n"
        f"- 说明：只读冻结引用 + DATA 覆盖指纹；不宣称六指标/ATH 齐全\n"
    )
    header_csv = (
        f"# strategy_fingerprint surface={surface_used} "
        f"strategy_id={fp['strategy_id']} version={fp['version']} "
        f"version_id={fp['version_id']} content_hash={fp['content_hash']} "
        f"registry_hash={fp['registry_hash']} unified_key={fp['unified_key']} "
        f"coverage_fingerprint={fp.get('coverage_fingerprint') or ''} "
        f"joint_hash={fp.get('joint_hash') or ''}"
    )
    return {
        'bound': True,
        'require_bound': require_bound,
        'fingerprint': fp,
        'joint_fingerprint': joint,
        'coverage_slice': coverage_slice,
        'surface': surface_used,
        'forced': True,
        'read_only': True,
        'header_md': header_md,
        'header_csv': header_csv,
        'note': '导出须写入策略 hash + coverage_fingerprint；禁止静默漂移',
        'disclaimer': DISCLAIMER,
    }


def stamp_export_payload(bindings, payload, prefer_surfaces=PREFER_SURFACES, require_bound=True, root=None):
    """Attach joint fingerprint onto report payload; raise BindingConflict if unbound/stale gate."""
    if not isinstance(payload, dict):
        raise ValueError('请求体须为对象')
    root = root if root is not None else getattr(bindings, 'root', None)
    gate = resolve_export_fingerprint(
        bindings, prefer_surfaces=prefer_surfaces, require_bound=require_bound, root=root,
    )
    out = dict(payload)
    fp = gate.get('fingerprint')
    joint = gate.get('joint_fingerprint')
    out['strategy_fingerprint'] = fp
    out['joint_fingerprint'] = joint
    out['unified_key'] = (fp or {}).get('unified_key')
    out['coverage_fingerprint'] = (fp or {}).get('coverage_fingerprint')
    out['rule_export_gate'] = {
        'bound': gate['bound'],
        'surface': gate.get('surface'),
        'forced': True,
        'schema': JOINT_SCHEMA,
        'note': gate.get('note'),
    }
    fmt = (out.get('content_format') or 'html').lower()
    content = out.get('content')
    if isinstance(content, str) and gate.get('header_md') and fmt in ('markdown', 'md', 'text', 'txt'):
        if 'strategy_fingerprint' not in content[:500]:
            out['content'] = gate['header_md'] + '\n' + content
    return out, gate


def assert_fingerprint_match(expected, live_joint, *, require_coverage=True):
    """Refuse stale/mismatched fingerprints. expected may be body or stamped export.

    Raises BindingConflict with clear reason (maps to HTTP 409).
    """
    if not isinstance(expected, dict):
        raise BindingConflict('指纹核验拒绝：缺少 expected 指纹对象')
    live = live_joint if isinstance(live_joint, dict) else {}

    exp_hash = (
        expected.get('expected_content_hash')
        or expected.get('content_hash')
        or (expected.get('strategy_fingerprint') or {}).get('content_hash')
        or (expected.get('joint_fingerprint') or {}).get('content_hash')
    )
    live_hash = live.get('content_hash')
    if exp_hash and live_hash and str(exp_hash) != str(live_hash):
        raise BindingConflict(
            f'指纹核验拒绝：策略 content_hash 不匹配 '
            f'(expected={str(exp_hash)[:16]}… ≠ live={str(live_hash)[:16]}…)；拒绝静默覆盖'
        )

    exp_cov = (
        expected.get('expected_coverage_fingerprint')
        or expected.get('coverage_fingerprint')
        or (expected.get('strategy_fingerprint') or {}).get('coverage_fingerprint')
        or (expected.get('joint_fingerprint') or {}).get('coverage_fingerprint')
    )
    live_cov = live.get('coverage_fingerprint')
    if require_coverage and exp_cov:
        if not live_cov:
            raise BindingConflict('指纹核验拒绝：本地无法采集 coverage_fingerprint；拒绝启用/导入')
        if str(exp_cov) != str(live_cov):
            raise BindingConflict(
                f'指纹核验拒绝：coverage_fingerprint 已过期或不匹配 '
                f'(expected={str(exp_cov)[:16]}… ≠ live={str(live_cov)[:16]}…；'
                f'expected/received live='
                f'{live.get("coverage_expected")}/{live.get("coverage_received")}）；'
                f'请重新导出/冻结或传入 acknowledge_coverage_drift=true 且通过覆盖硬闸'
            )

    exp_joint = (
        expected.get('expected_joint_hash')
        or expected.get('joint_hash')
        or (expected.get('joint_fingerprint') or {}).get('joint_hash')
        or (expected.get('strategy_fingerprint') or {}).get('joint_hash')
    )
    live_joint_hash = live.get('joint_hash')
    if exp_joint and live_joint_hash and str(exp_joint) != str(live_joint_hash):
        # If coverage drift acknowledged, joint_hash will differ — caller may skip this
        if not expected.get('acknowledge_coverage_drift'):
            raise BindingConflict(
                f'指纹核验拒绝：joint_hash 不匹配 '
                f'(expected={str(exp_joint)[:16]}… ≠ live={str(live_joint_hash)[:16]}…)；'
                f'拒绝陈旧导出包启用/导入'
            )
    return {'ok': True, 'live': live}


def verify_enable_or_import(root, version, body=None, *, for_enable=False):
    """Shared gate for enable/import paths."""
    body = body if isinstance(body, dict) else {}
    cov = capture_coverage_slice(root)
    if not cov.get('ok'):
        raise BindingConflict(
            '指纹/覆盖硬闸拒绝：' + (cov.get('reason') or '无法采集覆盖指纹')
        )
    live_joint = joint_from_version(version, cov)
    ack = bool(body.get('acknowledge_coverage_drift'))

    exp_hash = (
        body.get('expected_content_hash')
        or body.get('content_hash')
        or (body.get('strategy_fingerprint') or {}).get('content_hash')
        or (body.get('joint_fingerprint') or {}).get('content_hash')
    )
    if exp_hash and str(exp_hash) != str(version.get('content_hash') or ''):
        raise BindingConflict(
            f'指纹核验拒绝：策略 content_hash 不匹配 '
            f'(expected={str(exp_hash)[:16]}… ≠ version={str(version.get("content_hash") or "")[:16]}…)；'
            f'拒绝静默启用/导入'
        )

    has_exp_cov = bool(
        body.get('expected_coverage_fingerprint')
        or body.get('coverage_fingerprint')
        or body.get('expected_joint_hash')
        or body.get('joint_hash')
        or (body.get('joint_fingerprint') or {}).get('coverage_fingerprint')
        or (body.get('strategy_fingerprint') or {}).get('coverage_fingerprint')
    )
    if has_exp_cov:
        assert_fingerprint_match(body, live_joint, require_coverage=not ack)

    frozen = version.get('rule_fingerprint') or {}
    frozen_cov = frozen.get('coverage_fingerprint')
    live_cov = live_joint.get('coverage_fingerprint')
    if (
        for_enable
        and frozen_cov
        and live_cov
        and frozen_cov != live_cov
        and not ack
    ):
        raise BindingConflict(
            '启用硬闸拒绝：覆盖指纹相对冻结时已变化 '
            f'(frozen={str(frozen_cov)[:16]}… ≠ live={str(live_cov)[:16]}…；'
            f'frozen expected/received='
            f'{frozen.get("coverage_expected")}/{frozen.get("coverage_received")}，'
            f'live='
            f'{live_joint.get("coverage_expected")}/{live_joint.get("coverage_received")}）；'
            f'请重新冻结导出或显式 acknowledge_coverage_drift=true'
        )

    return {
        'ok': True,
        'live_joint': live_joint,
        'coverage_slice': cov,
        'acknowledged_drift': ack,
    }
