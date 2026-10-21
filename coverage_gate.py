"""DATA-01 deepen: multi-field coverage gate (expected / received / known).

Only gates fields that actually exist on scan rows or local candle archives.
Partial → mark partial; all-missing → unknown (never fake certainty).
Does NOT claim OI / market-cap / account-history completeness.
"""
from __future__ import annotations

import json
import math
from pathlib import Path

DISCLAIMER = (
    '多字段覆盖闸门：只统计扫描行/本地K线归档里真实出现的字段；'
    '部分可得标 partial；全缺标 unknown；按合约/字段列出缺口与可执行补查提示；'
    '不宣称 OI/市值/账户历史齐全；不重建全历史宇宙。'
)

# V1 hard floors — request must never silently lower frozen/gate warmup
MIN_WARMUP_BARS = 20
DEFAULT_WARMUP_BARS = 20


def resolve_warmup_bars(requested=None, frozen_min=None):
    """Effective warmup for hard gates: floor + frozen min; request may only raise.

    Returns (effective, meta). meta.refused_lower is True when request tried to go below floor/frozen.
    """
    floor = int(MIN_WARMUP_BARS)
    try:
        frozen = int(frozen_min) if frozen_min is not None else floor
    except (TypeError, ValueError):
        frozen = floor
    frozen = max(floor, frozen)
    refused_lower = False
    req_val = None
    if requested is not None and requested != '':
        try:
            req_val = int(requested)
        except (TypeError, ValueError) as exc:
            raise ValueError(f'warmup_bars 须为整数: {requested}') from exc
        if req_val < frozen:
            refused_lower = True
            effective = frozen
        else:
            effective = req_val
    else:
        effective = frozen
    return effective, {
        'floor': floor,
        'frozen_min': frozen,
        'requested': req_val,
        'effective': effective,
        'refused_lower': refused_lower,
    }


# Fields that exist on archived scan rows or candle files.
# completeness_claim=False → even 100% known still not "history complete".
GATE_FIELDS = [
    {'id':'smartMoneyNetInflowUsd', 'title':'聪明钱4h净流入USD', 'source':'scan_row',
     'completeness_claim':False, 'note':'币安标记钱包榜单；当期覆盖不代表历史完整'},
    {
        'id': 'symbol',
        'title': '合约代码',
        'source': 'scan_row',
        'completeness_claim': True,
        'note': '结构性字段',
    },
    {
        'id': 'price',
        'title': '最新价',
        'source': 'scan_row',
        'completeness_claim': True,
        'note': '当前扫描价',
    },
    {
        'id': 'markPrice',
        'title': '标记价',
        'source': 'scan_row',
        'completeness_claim': True,
        'note': '当前扫描标记价',
    },
    {
        'id': 'oiUsd',
        'title': '合约 OI（USD）',
        'source': 'scan_row',
        'completeness_claim': False,
        'forbidden_claim': 'oi_history_complete',
        'note': '仅当前扫描可得 OI；不宣称历史 OI 齐全',
    },
    {
        'id': 'oiCapPct',
        'title': 'OI/市值',
        'source': 'scan_row',
        'completeness_claim': False,
        'forbidden_claim': 'oi_mcap_ratio_complete',
        'note': '依赖市值匹配；不宣称 OI 或市值历史齐全',
    },
    {
        'id': 'capUsd',
        'title': '流通市值',
        'source': 'scan_row',
        'completeness_claim': False,
        'forbidden_claim': 'market_cap_history_complete',
        'note': '仅当前匹配市值；不宣称历史市值/全市场市值齐全',
    },
    {
        'id': 'accountRatio',
        'title': '账户多空比',
        'source': 'scan_row',
        'completeness_claim': False,
        'forbidden_claim': 'account_history_complete',
        'note': '仅当前快照；不宣称账户历史齐全',
    },
    {
        'id': 'fundingAprPct',
        'title': '资金费率年化',
        'source': 'scan_row',
        'completeness_claim': True,
        'note': '当前扫描可得费率推算',
    },
    {
        'id': 'volumeRatio',
        'title': '量倍数',
        'source': 'scan_row',
        'completeness_claim': True,
        'note': '依赖已收盘窗口量',
    },
    {
        'id': 'closed4h',
        'title': '已收盘 4h K',
        'source': 'scan_row',
        'completeness_claim': True,
        'note': 'EMA/结构预热用已收盘根数',
    },
    {
        'id': 'ema4h',
        'title': '4h EMA 组',
        'source': 'scan_row',
        'completeness_claim': True,
        'note': '对象非空即计可得',
    },
    {
        'id': 'ohlcv_4h',
        'title': '本地 4h OHLCV 归档',
        'source': 'candle_archive',
        'interval': '4h',
        'completeness_claim': True,
        'note': '只读合并本地扫描窗+区间补查；缺档不填零',
    },
    {
        'id': 'ohlcv_1d',
        'title': '本地 1d OHLCV 归档',
        'source': 'candle_archive',
        'interval': '1d',
        'completeness_claim': True,
        'note': '只读合并本地扫描窗+区间补查；缺档不填零',
    },
]


def catalog():
    return {
        'schema': 'coverage-gate-catalog-v1',
        'slice': 'DATA-01-coverage-deepen',
        'fields': GATE_FIELDS,
        'statuses': {
            'covered': '该字段在统计范围内全部可得',
            'partial': '部分可得——必须标覆盖缺口，不能当齐全',
            'unknown': '全部缺失或无法判定——禁止伪装成确定值',
        },
        'forbidden_global_claims': [
            'oi_history_complete',
            'market_cap_history_complete',
            'account_history_complete',
            'six_historical_indicators_complete',
        ],
        'disclaimer': DISCLAIMER,
    }


def _finite_number(value):
    if isinstance(value, bool):
        return False
    if isinstance(value, (int, float)):
        return math.isfinite(float(value))
    if isinstance(value, str) and value.strip():
        try:
            return math.isfinite(float(value))
        except ValueError:
            return False
    return False


def field_known_on_row(field_id, row):
    """Return True if field is present & usable on one scan row."""
    if not isinstance(row, dict):
        return False
    if field_id in ('symbol', 'market'):
        v = row.get(field_id)
        return isinstance(v, str) and bool(v.strip())
    if field_id in ('price', 'markPrice', 'oiUsd', 'oiCapPct', 'capUsd',
                    'accountRatio', 'fundingAprPct', 'volumeRatio',
                    'priceChangeWindowPct', 'indexPrice'):
        return _finite_number(row.get(field_id))
    if field_id == 'smartMoneyNetInflowUsd':
        from strategy_registry import indicator_states
        return indicator_states(row)[4] is not None
    if field_id == 'closed4h':
        v = row.get('closed4h')
        if isinstance(v, bool):
            return v
        if isinstance(v, (int, float)):
            return v > 0
        if isinstance(v, list):
            return len(v) > 0
        return v is not None
    if field_id == 'ema4h':
        # scanner writes [ema7, ema25, ema99]; require all three finite — one value ≠ covered
        v = row.get('ema4h')
        if isinstance(v, dict):
            keys = ('ema7', 'ema25', 'ema99', 7, 25, 99, '7', '25', '99')
            vals = []
            for k in keys:
                if k in v:
                    vals.append(v[k])
            if len(vals) >= 3:
                return all(_finite_number(x) for x in vals[:3])
            # fallback: all provided values must be finite and at least 3
            nums = [x for x in v.values() if _finite_number(x) or x is not None]
            return len(v) >= 3 and all(_finite_number(x) for x in v.values())
        if isinstance(v, (list, tuple)):
            return len(v) >= 3 and all(_finite_number(x) for x in v[:3])
        return False
    # unknown field id
    return False


def _status(known, expected):
    if expected <= 0:
        return 'unknown'
    if known <= 0:
        return 'unknown'
    if known < expected:
        return 'partial'
    return 'covered'


def _load_scan(root, revision=None):
    root = Path(root)
    if revision:
        # try archive path scan-history/scan-<id>/scan.json or revision folder
        candidates = [
            root / 'scan-history' / revision / 'scan.json',
            root / 'scan-history' / f'scan-{revision}' / 'scan.json',
            root / revision / 'scan.json',
        ]
        # also list_snapshot style: scan-history/scan-<id>.json?
        for c in candidates:
            if c.exists():
                return json.loads(c.read_text(encoding='utf-8')), str(c.relative_to(root)).replace('\\', '/')
        raise FileNotFoundError(f'找不到 revision 扫描归档: {revision}')
    path = root / 'scan.json'
    if not path.exists():
        raise FileNotFoundError('缺少 scan.json')
    return json.loads(path.read_text(encoding='utf-8')), 'scan.json'


def evaluate_scan_fields(root, revision=None, limit_rows=None):
    """Compute expected/received/known per gateable scan-row field."""
    scan, source_path = _load_scan(root, revision=revision)
    rows = [r for r in (scan.get('rows') or []) if isinstance(r, dict)]
    if limit_rows is not None:
        rows = rows[: int(limit_rows)]

    # F07: expected ≠ received. total=应有；received=实收；known=字段有效。
    # 目录/局部行齐全不能把 expected 压成 received 后伪装 covered。
    rows_n = len(rows)
    try:
        scan_total = int(scan['total']) if scan.get('total') is not None else None
    except (TypeError, ValueError):
        scan_total = None
    try:
        scan_received = int(scan['received']) if scan.get('received') is not None else None
    except (TypeError, ValueError):
        scan_received = None

    if scan_total is not None and scan_total >= 0:
        expected = scan_total
    elif scan_received is not None:
        expected = scan_received
    else:
        expected = rows_n

    if scan_received is not None:
        received = scan_received
    else:
        received = rows_n
    # Rows on hand may be truncated; never claim received > rows we can inspect for known
    received_for_known = min(received, rows_n) if rows_n else 0
    if limit_rows is not None:
        # Limited window: expected stays universe if known; note truncation
        pass

    field_reports = []
    for spec in GATE_FIELDS:
        if spec['source'] != 'scan_row':
            continue
        known = sum(1 for r in rows if field_known_on_row(spec['id'], r))
        # Status vs expected universe: short receive or short known → not covered
        # Also: if received < expected, cannot be covered even if known==rows_n
        st = _status(known, expected)
        if received < expected and st == 'covered':
            st = 'partial'
        if received_for_known < expected and known >= received_for_known and expected > 0:
            # got fewer rows than universe — at best partial
            if st == 'covered':
                st = 'partial'
        field_reports.append({
            'id': spec['id'],
            'title': spec['title'],
            'source': 'scan_row',
            'expected': expected,
            'received': received,
            'known': known,
            'missing': max(0, expected - known),
            'coverage_pct': round(100.0 * known / expected, 4) if expected else 0.0,
            'status': st,
            'completeness_claim': bool(spec.get('completeness_claim')),
            'forbidden_claim': spec.get('forbidden_claim'),
            'note': spec.get('note'),
            # Even covered + completeness_claim False → not history-complete
            'history_complete': False if not spec.get('completeness_claim') else (st == 'covered'),
            'directory_presence_only': False,
        })

    return {
        'schema': 'coverage-gate-scan-v1',
        'source_path': source_path,
        'scan_id': scan.get('scan_id') or scan.get('revision'),
        'revision': scan.get('revision') or scan.get('scan_id'),
        'scan_totals': {
            'total': scan.get('total'),
            'received': scan.get('received'),
            'rows_evaluated': len(rows),
            'quality_status': (scan.get('quality') or {}).get('status'),
        },
        'fields': field_reports,
        'aggregate': _aggregate(field_reports),
        'claims': {
            'oi_history_complete': False,
            'market_cap_history_complete': False,
            'account_history_complete': False,
            'six_historical_indicators_complete': False,
            'note': '即使 oiUsd/capUsd/accountRatio 当前行全可得，也不升格为历史齐全',
        },
        'disclaimer': DISCLAIMER,
    }


def _aggregate(field_reports):
    if not field_reports:
        return {
            'status': 'unknown',
            'covered_fields': 0,
            'partial_fields': 0,
            'unknown_fields': 0,
            'note': '无字段可评估',
        }
    covered = sum(1 for f in field_reports if f['status'] == 'covered')
    partial = sum(1 for f in field_reports if f['status'] == 'partial')
    unknown = sum(1 for f in field_reports if f['status'] == 'unknown')
    if covered == 0 and partial == 0:
        status = 'unknown'
        note = '全部字段 unknown——禁止伪装成齐全'
    elif partial > 0 or unknown > 0:
        status = 'partial'
        note = '存在 partial/unknown 字段——聚合只能标部分覆盖'
    else:
        status = 'covered'
        note = '闸门内扫描字段均 covered（仍不宣称 OI/市值/账户历史齐全）'
    return {
        'status': status,
        'covered_fields': covered,
        'partial_fields': partial,
        'unknown_fields': unknown,
        'field_count': len(field_reports),
        'note': note,
    }


def evaluate_candle_fields(root, limit=40):
    """Gate local OHLCV archive presence via price_index summarize (no network)."""
    root = Path(root)
    try:
        from price_index import summarize_price_index
    except ImportError:
        return {
            'schema': 'coverage-gate-candles-v1',
            'fields': [],
            'aggregate': {'status': 'unknown', 'note': 'price_index 不可用'},
            'disclaimer': DISCLAIMER,
        }

    reports = []
    for spec in GATE_FIELDS:
        if spec['source'] != 'candle_archive':
            continue
        interval = spec.get('interval') or '4h'
        try:
            summary = summarize_price_index(root, interval=interval, limit=limit)
        except Exception as exc:
            reports.append({
                'id': spec['id'],
                'title': spec['title'],
                'source': 'candle_archive',
                'interval': interval,
                'expected': 0,
                'received': 0,
                'known': 0,
                'missing': 0,
                'coverage_pct': 0.0,
                'status': 'unknown',
                'completeness_claim': True,
                'history_complete': False,
                'note': f'汇总失败: {str(exc)[:120]}',
            })
            continue
        series = summary.get('series') or summary.get('items') or []
        # summarize returns list-like under various keys
        if not series and isinstance(summary.get('rows'), list):
            series = summary['rows']
        # Directory listing length is NOT "expected full coverage".
        # Treat listed series as received; known only if bars suggest usable archive.
        # history_complete always False — local archive ≠ full history / warmup.
        received = len(series)
        expected = received  # without a declared universe, expected==received but still not history_complete
        # Prefer explicit universe hints if summarize provides them
        for key in ('universe_total', 'expected_symbols', 'symbol_universe'):
            if isinstance(summary.get(key), (int, float)) and summary[key] > 0:
                expected = int(summary[key])
                break
        known = 0
        for item in series:
            if not isinstance(item, dict):
                continue
            avail = item.get('current_available')
            bars = (
                item.get('approx_bars_scan')
                or item.get('approx_bars_range_known')
                or item.get('known')
                or item.get('bars')
                or item.get('bar_count')
            )
            pct = item.get('coveragePct')
            # Require more than "file exists": prefer explicit availability or bar count
            if avail is True and isinstance(bars, (int, float)) and bars > 0:
                known += 1
            elif isinstance(bars, (int, float)) and bars >= 10:
                # mild warmup floor — directory presence alone (bars unknown) does not count
                known += 1
            elif avail is True and bars is None and pct is None:
                # presence flag without bar evidence → do not count as known
                pass
            elif isinstance(pct, (int, float)) and pct >= 80:
                known += 1
        st = _status(known, expected) if expected else 'unknown'
        if received < expected and st == 'covered':
            st = 'partial'
        reports.append({
            'id': spec['id'],
            'title': spec['title'],
            'source': 'candle_archive',
            'interval': interval,
            'expected': expected,
            'received': received,
            'known': known,
            'missing': max(0, expected - known),
            'coverage_pct': round(100.0 * known / expected, 4) if expected else 0.0,
            'status': st,
            'completeness_claim': True,
            'history_complete': False,  # local archive ≠ full history
            'directory_presence_only': True,
            'note': (spec.get('note') or '') + f'；目录样本 limit={limit}；目录存在≠完整覆盖/预热',
            'summary_scope': summary.get('scope') or summary.get('label_current'),
        })

    return {
        'schema': 'coverage-gate-candles-v1',
        'fields': reports,
        'aggregate': _aggregate(reports),
        'claims': {
            'full_market_ohlcv_complete': False,
            'note': '本地目录覆盖 ≠ 全市场/全历史 K 线齐全',
        },
        'disclaimer': DISCLAIMER,
    }



BACKFILL_HINTS = {
    'symbol': '结构性字段缺失通常表示扫描行损坏；重新全市场扫描',
    'price': '重新全市场扫描以刷新最新价',
    'markPrice': '重新全市场扫描以刷新标记价',
    'oiUsd': '重新全市场扫描刷新当前 OI；不可用补查伪造历史 OI',
    'oiCapPct': '需同时具备 oiUsd 与 capUsd；重新扫描，禁止市值回填',
    'capUsd': '重新扫描以匹配流通市值；禁止用当今市值回填过去',
    'accountRatio': '重新扫描刷新账户多空比；不宣称账户历史可补',
    'fundingAprPct': '重新扫描刷新资金费率',
    'volumeRatio': '重新扫描或等待已收盘窗口量写入',
    'closed4h': '本地 4h 预热不足：POST /api/candles/backfills（interval=4h）或等待扫描窗',
    'ema4h': '需足够已收盘 4h K；先补查/扫描 closed4h 再算 EMA',
    'ohlcv_4h': '区间补查 POST /api/candles/backfills，interval=4h，选择缺口合约；目录存在≠预热齐全',
    'ohlcv_1d': '区间补查 POST /api/candles/backfills，interval=1d，选择缺口合约；目录存在≠预热齐全',
}


def _sha_fingerprint(payload):
    import hashlib
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode('utf-8')
    return hashlib.sha256(raw).hexdigest()


def coverage_fingerprint(report):
    """Stable fingerprint of expected/received/known/status — for snapshot browse & gates."""
    fields = []
    for f in (report.get('fields') or []):
        fields.append({
            'id': f.get('id'),
            'status': f.get('status'),
            'expected': f.get('expected'),
            'received': f.get('received'),
            'known': f.get('known'),
            'missing': f.get('missing'),
        })
    payload = {
        'schema': report.get('schema'),
        'aggregate': (report.get('aggregate') or {}).get('status'),
        'scan_totals': report.get('scan_totals') or (report.get('scan') or {}).get('scan_totals'),
        'fields': fields,
        'claims_oi': (report.get('claims') or {}).get('oi_history_complete', False),
    }
    return {
        'algorithm': 'sha256',
        'hex': _sha_fingerprint(payload),
        'field_count': len(fields),
        'aggregate_status': payload['aggregate'],
    }


def _gap_symbols_for_field(rows, field_id, limit=15):
    gaps = []
    for r in rows:
        if field_known_on_row(field_id, r):
            continue
        sym = r.get('symbol')
        if isinstance(sym, str) and sym.strip():
            gaps.append(sym.strip().upper())
        if len(gaps) >= limit:
            break
    return gaps


def build_field_gaps(root, revision=None, limit_rows=None, per_field_symbol_limit=15):
    """Per-field + sample per-symbol gaps with actionable backfill hints."""
    scan, source_path = _load_scan(root, revision=revision)
    rows = [r for r in (scan.get('rows') or []) if isinstance(r, dict)]
    if limit_rows is not None:
        rows = rows[: int(limit_rows)]
    scan_report = evaluate_scan_fields(root, revision=revision, limit_rows=limit_rows)
    gaps = []
    for fr in scan_report.get('fields') or []:
        if fr.get('status') == 'covered':
            continue
        fid = fr['id']
        syms = _gap_symbols_for_field(rows, fid, limit=per_field_symbol_limit)
        hint = BACKFILL_HINTS.get(fid, '检查扫描/本地归档；勿伪造齐全')
        # Universe shortfall hint
        if (fr.get('received') or 0) < (fr.get('expected') or 0):
            hint = (
                f'实收 {fr.get("received")} < 应有 {fr.get("expected")}；'
                '先完成/重跑全市场扫描补目录缺口。' + hint
            )
        gaps.append({
            'field_id': fid,
            'title': fr.get('title'),
            'status': fr.get('status'),
            'expected': fr.get('expected'),
            'received': fr.get('received'),
            'known': fr.get('known'),
            'missing': fr.get('missing'),
            'gap_symbols_sample': syms,
            'gap_symbol_count_sample': len(syms),
            'backfill_hint': hint,
            'history_complete': False,
        })
    # Candle archive gaps (no per-symbol without price_index detail — hint only)
    candle_report = evaluate_candle_fields(root, limit=40)
    for fr in candle_report.get('fields') or []:
        if fr.get('status') == 'covered':
            continue
        fid = fr['id']
        gaps.append({
            'field_id': fid,
            'title': fr.get('title'),
            'status': fr.get('status'),
            'expected': fr.get('expected'),
            'received': fr.get('received'),
            'known': fr.get('known'),
            'missing': fr.get('missing'),
            'gap_symbols_sample': [],
            'gap_symbol_count_sample': 0,
            'backfill_hint': BACKFILL_HINTS.get(fid, fr.get('note') or '区间补查或等待扫描窗'),
            'history_complete': False,
            'directory_presence_only': fr.get('directory_presence_only'),
        })
    return {
        'schema': 'coverage-gaps-v1',
        'source_path': source_path,
        'scan_id': scan.get('scan_id') or scan.get('revision'),
        'revision': scan.get('revision') or scan.get('scan_id'),
        'gaps': gaps,
        'gap_count': len(gaps),
        'disclaimer': DISCLAIMER,
    }


def assert_baseline_coverage(root, critical_fields=('symbol', 'price', 'markPrice')):
    """Hard gate soft-spot fix: empty deps / research paths still need baseline scan coverage."""
    try:
        report = evaluate_scan_fields(root)
    except FileNotFoundError as exc:
        return {
            'ok': False,
            'reason': f'基线覆盖失败: {exc}',
            'critical_failures': list(critical_fields),
            'disclaimer': DISCLAIMER,
        }
    by_id = {f['id']: f for f in report.get('fields') or []}
    failures = []
    for fid in critical_fields:
        fr = by_id.get(fid)
        st = (fr or {}).get('status') or 'unknown'
        # V1 harden: critical must be covered — F07 partial/断档不得放行
        if not fr or st != 'covered':
            failures.append(fid if st != 'partial' else f'{fid}:partial')
    # Also refuse when received << expected for price (universe soft spot)
    price = by_id.get('price') or {}
    if price and (price.get('received') or 0) < max(1, int((price.get('expected') or 0) * 0.5)):
        if 'price_universe_shortfall' not in failures:
            failures.append('price_universe_shortfall')
    # F07: any expected>received on critical → explicit shortfall tag
    for fid in critical_fields:
        fr = by_id.get(fid) or {}
        try:
            exp, rec = int(fr.get('expected') or 0), int(fr.get('received') or 0)
        except (TypeError, ValueError):
            continue
        if exp > 0 and rec < exp and f'{fid}:expected_gt_received' not in failures:
            failures.append(f'{fid}:expected_gt_received')
    ok = not failures
    return {
        'ok': ok,
        'reason': None if ok else (
            '基线覆盖硬闸未通过: ' + ', '.join(failures)
            + '（critical 须 covered；F07 expected>received / partial 拒绝；实收不足应先重扫）'
        ),
        'critical_failures': failures,
        'scan_totals': report.get('scan_totals'),
        'fingerprint': coverage_fingerprint({**report, 'fields': report.get('fields') or []}),
        'disclaimer': DISCLAIMER,
    }


def assert_scan_research_gate(root, scan_result=None):
    """Harder gate after scan_and_research scan phase — before AI research."""
    # Prefer freshly written scan.json
    try:
        report = evaluate_scan_fields(root)
    except FileNotFoundError as exc:
        return {'ok': False, 'reason': f'扫描后无 scan.json: {exc}', 'disclaimer': DISCLAIMER}
    totals = report.get('scan_totals') or {}
    try:
        total = int(totals['total']) if totals.get('total') is not None else None
        received = int(totals['received']) if totals.get('received') is not None else None
    except (TypeError, ValueError):
        total, received = None, None
    if scan_result and isinstance(scan_result, dict):
        total = scan_result.get('total', total)
        received = scan_result.get('received', received)
        qstatus = ((scan_result.get('quality') or {}).get('status')
                   or (totals.get('quality_status')))
    else:
        qstatus = totals.get('quality_status')
    if qstatus not in ('success', 'partial_success'):
        return {
            'ok': False,
            'reason': f'扫描质量 {qstatus} 未过门槛；拒绝继续投研（不用旧快照）',
            'quality_status': qstatus,
            'disclaimer': DISCLAIMER,
        }
    if total and received is not None and received < max(1, int(total * 0.5)):
        return {
            'ok': False,
            'reason': f'实收 {received} < 应有一半（total={total}）；拒绝在严重缺口上继续投研',
            'expected': total,
            'received': received,
            'disclaimer': DISCLAIMER,
        }
    baseline = assert_baseline_coverage(root)
    if not baseline.get('ok'):
        return {
            'ok': False,
            'reason': baseline.get('reason'),
            'baseline': baseline,
            'disclaimer': DISCLAIMER,
        }
    # Refuse F07-style partial on critical scan fields even when 50% received threshold passed
    by_id = {f['id']: f for f in (report.get('fields') or [])}
    critical_partial = []
    for fid in ('symbol', 'price', 'markPrice'):
        fr = by_id.get(fid) or {}
        st = fr.get('status') or 'unknown'
        if st != 'covered':
            critical_partial.append(f'{fid}:{st}')
        try:
            if int(fr.get('expected') or 0) > int(fr.get('received') or 0):
                tag = f'{fid}:expected_gt_received'
                if tag not in critical_partial:
                    critical_partial.append(tag)
        except (TypeError, ValueError):
            pass
    if critical_partial:
        return {
            'ok': False,
            'reason': (
                '扫描投研硬闸拒绝：critical 字段未 covered 或存在 F07 expected>received（'
                + ', '.join(critical_partial)
                + '）；断档/缺口不得继续投研'
            ),
            'critical_failures': critical_partial,
            'quality_status': qstatus,
            'expected': total,
            'received': received,
            'disclaimer': DISCLAIMER,
        }
    gaps = build_field_gaps(root)
    return {
        'ok': True,
        'quality_status': qstatus,
        'expected': total,
        'received': received,
        'fingerprint': coverage_fingerprint({**report, 'scan_totals': totals}),
        'gap_count': gaps.get('gap_count'),
        'disclaimer': DISCLAIMER,
    }


def assert_autofill_symbol_gate(
    root, symbol, interval='4h', warmup_bars=None, market='USDⓈ-M',
    frozen_min_warmup=None, allow_lower=False,
):
    """SIM autofill hard gate: per-symbol warmup + continuity; request cannot lower floor.

    allow_lower is always ignored for hard gates (kept for API clarity — never True effect).
    """
    effective, meta = resolve_warmup_bars(warmup_bars, frozen_min_warmup)
    if meta.get('refused_lower') or (allow_lower is True and meta.get('requested') is not None
                                      and meta['requested'] < meta['frozen_min']):
        # Explicit refuse when caller tried to undercut frozen/floor
        if meta.get('requested') is not None and meta['requested'] < meta['frozen_min']:
            return {
                'ok': False,
                'reason': (
                    f'预热硬闸拒绝：请求 warmup_bars={meta["requested"]} '
                    f'低于冻结/底线 {meta["frozen_min"]}；禁止请求降低预热要求'
                ),
                'warmup': meta,
                'http_status_hint': 409,
                'disclaimer': DISCLAIMER,
            }
    sym = evaluate_symbol_coverage(
        root, symbol, interval=interval, warmup_bars=effective, market=market,
    )
    # Continuity / gap must never pass
    if sym.get('ok') and sym.get('continuity_ok') is False:
        sym = dict(sym)
        sym['ok'] = False
        sym['status'] = 'partial'
        sym['reason'] = '目标区间存在断档；不可当完整覆盖'
    if not sym.get('ok'):
        hint = BACKFILL_HINTS.get(
            'ohlcv_4h' if interval == '4h' else 'ohlcv_1d',
            '区间补查后重试',
        )
        return {
            'ok': False,
            'reason': sym.get('reason') or '目标合约覆盖不足',
            'symbol_coverage': sym,
            'warmup': meta,
            'backfill_hint': hint,
            'http_status_hint': 409,
            'disclaimer': DISCLAIMER,
        }
    return {
        'ok': True,
        'symbol_coverage': sym,
        'warmup': meta,
        'disclaimer': DISCLAIMER,
    }


def evaluate(root, revision=None, candle_limit=40, row_limit=None, include_gaps=True):
    """Combined gate report for UI/API — with gaps, backfill hints, fingerprint."""
    scan_part = evaluate_scan_fields(root, revision=revision, limit_rows=row_limit)
    candle_part = evaluate_candle_fields(root, limit=candle_limit)
    all_fields = list(scan_part['fields']) + list(candle_part['fields'])
    agg = _aggregate(all_fields)
    out = {
        'schema': 'coverage-gate-v1',
        'slice': 'DATA-01-coverage-deepen',
        'scan': scan_part,
        'candles': candle_part,
        'fields': all_fields,
        'aggregate': agg,
        'claims': {
            **scan_part.get('claims', {}),
            **candle_part.get('claims', {}),
        },
        'disclaimer': DISCLAIMER,
    }
    out['fingerprint'] = coverage_fingerprint(out)
    if include_gaps:
        try:
            gaps = build_field_gaps(root, revision=revision, limit_rows=row_limit)
            out['gaps'] = gaps.get('gaps') or []
            out['gap_count'] = gaps.get('gap_count') or 0
        except FileNotFoundError:
            out['gaps'] = []
            out['gap_count'] = 0
    return out


def evaluate_symbol_coverage(root, symbol, interval='4h', start=None, end=None, warmup_bars=20, market='USDⓈ-M'):
    """Per-symbol / range / warmup coverage — other coins must not greenlight a missing target.

    Returns a gate verdict for one experiment object. Does not mutate global aggregate.
    """
    root = Path(root)
    symbol = str(symbol or '').strip().upper()
    if not symbol:
        raise ValueError('须指定 symbol')
    try:
        from price_index import merge_series, summarize_price_index
    except ImportError:
        return {
            'schema': 'coverage-symbol-v1',
            'ok': False,
            'symbol': symbol,
            'interval': interval,
            'status': 'unknown',
            'reason': 'price_index 不可用',
            'history_complete': False,
            'disclaimer': DISCLAIMER,
        }

    # Sanity: summarize may list many symbols; target absence must fail even if others exist
    others_present = False
    try:
        summary = summarize_price_index(root, interval=interval, limit=80)
        series = summary.get('series') or summary.get('items') or summary.get('rows') or []
        for item in series:
            if not isinstance(item, dict):
                continue
            sym = str(item.get('symbol') or item.get('pair') or '').upper()
            if sym and sym != symbol:
                if item.get('current_available') or item.get('bar_count') or item.get('approx_bars_scan'):
                    others_present = True
    except Exception:
        summary = {}
        series = []

    try:
        detail = merge_series(
            root, market, symbol, interval,
            start=start, end=end, include_bars=True,
        )
    except Exception as exc:
        return {
            'schema': 'coverage-symbol-v1',
            'ok': False,
            'symbol': symbol,
            'interval': interval,
            'status': 'unknown',
            'reason': f'目标合约无法合并本地K线: {exc}',
            'others_present_not_sufficient': others_present,
            'history_complete': False,
            'disclaimer': DISCLAIMER,
        }

    bars = detail.get('bars') or []
    n = len(bars)
    has_target = bool(detail.get('current_available')) or n > 0
    warmup_ok = n >= int(warmup_bars or 0)
    # Use the declared interval, never infer cadence from potentially sparse bars.
    from candle_ranges import STEPS, timestamp
    step = STEPS.get(interval)
    times = [int(b[0]) for b in bars]
    continuity_ok = bool(step) and all(b - a == step for a, b in zip(times, times[1:]))
    range_ok = True
    if step and times:
        if start is not None:
            first = ((timestamp(start) + step - 1) // step) * step
            range_ok = range_ok and times[0] == first
        if end is not None:
            stop = (timestamp(end) // step) * step
            range_ok = range_ok and times[-1] + step == stop
    elif start is not None or end is not None:
        range_ok = False
    # merge_series additionally detects conflicts, missing edges and invalid archives.
    continuity_ok = continuity_ok and (detail.get('continuity') or {}).get('continuous', True)

    if not has_target:
        status = 'unknown'
        ok = False
        reason = '目标合约本地无K线；其他币有数据不能放行'
    elif not warmup_ok:
        status = 'partial'
        ok = False
        reason = f'预热不足: bars={n} < warmup_bars={warmup_bars}'
    elif not range_ok:
        status = 'partial'
        ok = False
        reason = '目标区间首尾覆盖不足；不可当完整覆盖'
    elif not continuity_ok:
        status = 'partial'
        ok = False
        reason = '目标区间存在断档；不可当完整覆盖'
    else:
        status = 'covered'
        ok = True
        reason = '目标合约区间内可得且预热达标（仍不宣称全历史齐全）'

    return {
        'schema': 'coverage-symbol-v1',
        'ok': ok,
        'symbol': symbol,
        'market': market,
        'interval': interval,
        'start': start,
        'end': end,
        'warmup_bars': warmup_bars,
        'bars_in_range': n,
        'status': status,
        'reason': reason,
        'others_present_not_sufficient': others_present,
        'continuity_ok': continuity_ok,
        'range_ok': range_ok,
        'history_complete': False,
        'directory_presence_only': False,
        'disclaimer': DISCLAIMER,
    }
