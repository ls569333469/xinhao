"""RULE-01 coverage hard-gate helpers. Reuses coverage_gate field reports."""
from __future__ import annotations

from pathlib import Path

from coverage_gate import DISCLAIMER, evaluate



# Strategy data_dependencies → coverage_gate field ids (reuse GATE_FIELDS, no loose lists).
# Unmapped deps stay unsatisfied (honest): netflow, ath, etc.
DEP_FIELD_MAP = {
    'price_4h': ['ohlcv_4h'],
    'price_1d': ['ohlcv_1d'],
    'structure_ohlcv': ['ohlcv_4h', 'closed4h'],
    'oi': ['oiUsd'],
    'account_ratio': ['accountRatio'],
    'funding': ['fundingAprPct'],
    'ema': ['ema4h', 'closed4h'],
    'volume_price': ['volumeRatio', 'price'],
    'smart_money': ['smartMoneyNetInflowUsd'],
    # snapshot: special — scan.json / revisions, not a GATE_FIELDS row
    # netflow, ath: no gate field yet → hard fail
}

SPECIAL_DEPS = ('snapshot',)
UNSUPPORTED_DEPS = ('netflow', 'ath')


def dep_field_map():
    return {
        'schema': 'coverage-dep-map-v1',
        'map': {k: list(v) for k, v in DEP_FIELD_MAP.items()},
        'special': list(SPECIAL_DEPS),
        'unsupported': list(UNSUPPORTED_DEPS),
        'note': (
            '策略声明依赖映射到 coverage_gate 字段；'
            'require covered 才放行启用/绑定；partial/unknown 拒绝；'
            '不宣称六历史指标/ATH 齐全。'
        ),
    }


def assert_deps_covered(root, data_dependencies, require_status='covered', candle_limit=80):
    """Hard gate: declared deps must have coverage_gate status == require_status.

    Returns {ok, missing_dependencies, details, coverage_aggregate, claims}.
    Hard paths never accept partial/unknown — require_status other than 'covered' is forced up.
    """
    # V1: cannot lower gate via require_status=partial from callers/request
    if require_status != 'covered':
        require_status = 'covered'
    deps = list(data_dependencies or [])
    missing = []
    details = {}

    if not deps:
        # Soft-spot tighten: empty deps no longer silently skip — require baseline scan coverage.
        from coverage_gate import assert_baseline_coverage
        baseline = assert_baseline_coverage(root)
        return {
            'ok': bool(baseline.get('ok')),
            'missing_dependencies': [] if baseline.get('ok') else ['baseline_scan_coverage'],
            'details': {
                'note': (
                    '未声明 data_dependencies；仍要求基线扫描覆盖（symbol/price），'
                    '禁止在无快照/价全缺时启用或绑定'
                ),
                'baseline': baseline,
            },
            'coverage_aggregate': None,
            'claims': {
                'oi_history_complete': False,
                'market_cap_history_complete': False,
                'account_history_complete': False,
                'six_historical_indicators_complete': False,
                'ath_complete': False,
            },
            'disclaimer': DISCLAIMER,
        }

    # Evaluate once — candle_limit may only raise diagnostic sample, never below 40 for hard gate
    try:
        candle_limit = max(40, int(candle_limit or 40))
    except (TypeError, ValueError):
        candle_limit = 40
    try:
        report = evaluate(root, candle_limit=candle_limit)
    except FileNotFoundError as exc:
        return {
            'ok': False,
            'missing_dependencies': list(deps),
            'details': {'error': str(exc)},
            'coverage_aggregate': None,
            'claims': {
                'oi_history_complete': False,
                'market_cap_history_complete': False,
                'account_history_complete': False,
                'six_historical_indicators_complete': False,
                'ath_complete': False,
            },
        }

    by_id = {f['id']: f for f in (report.get('fields') or [])}

    for dep in deps:
        if dep in SPECIAL_DEPS:
            # snapshot: scan.json or revisions
            try:
                from price_index import list_snapshot_revisions
                snaps = (list_snapshot_revisions(root, limit=5).get('snapshots') or [])
            except Exception:
                snaps = []
            has_scan = (Path(root) / 'scan.json').exists()
            details[dep] = {
                'kind': 'special',
                'snapshots': len(snaps),
                'scan_json': has_scan,
            }
            if not snaps and not has_scan:
                missing.append(dep)
            continue

        if dep in UNSUPPORTED_DEPS:
            details[dep] = {
                'kind': 'unsupported',
                'status': 'unsatisfied',
                'reason': '覆盖闸门无对应字段；禁止静默启用（不宣称 ATH/精确净流齐全）',
            }
            missing.append(dep)
            continue

        fields = DEP_FIELD_MAP.get(dep)
        if not fields:
            details[dep] = {
                'kind': 'unknown_dep',
                'status': 'unsatisfied',
                'reason': f'未映射到 coverage_gate 字段；禁止静默启用',
            }
            missing.append(dep)
            continue

        field_statuses = []
        bad = []
        for fid in fields:
            fr = by_id.get(fid)
            if not fr:
                bad.append(fid)
                field_statuses.append({'id': fid, 'status': 'unknown', 'reason': '闸门无此字段'})
                continue
            st = fr.get('status') or 'unknown'
            field_statuses.append({
                'id': fid,
                'status': st,
                'known': fr.get('known'),
                'expected': fr.get('expected'),
                'missing': fr.get('missing'),
                'coverage_pct': fr.get('coverage_pct'),
                'history_complete': fr.get('history_complete'),
                'completeness_claim': fr.get('completeness_claim'),
            })
            if st != require_status:
                bad.append(fid)

        details[dep] = {
            'kind': 'coverage_fields',
            'fields': field_statuses,
            'require_status': require_status,
            'ok': not bad,
        }
        if bad:
            missing.append(dep)

    return {
        'ok': not missing,
        'missing_dependencies': missing,
        'details': details,
        'coverage_aggregate': report.get('aggregate'),
        'require_status': require_status,
        'claims': {
            **(report.get('claims') or {}),
            'six_historical_indicators_complete': False,
            'ath_complete': False,
            'note': (
                '硬闸只验证当前覆盖状态；即便 oiUsd/capUsd covered，'
                '也不宣称历史 OI/市值/账户/ATH/六指标齐全'
            ),
        },
        'disclaimer': DISCLAIMER,
    }
