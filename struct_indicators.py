"""STRUCT-01 first slice: ATR / ADX / day-box / range-high / weak absorption.

Research only. Never claims 可信ATH without an explicit verified full-history
source flag (none exists today). Insufficient history → status, not a fake number.
Silent NaN is refused: non-finite values become null with an explicit status.
BACKTEST-01 filter gate is exposed via evaluate_struct_gate for run/compare cells.
"""
from __future__ import annotations

import math
from price_index import merge_series, summarize_price_index

PARAMS_VERSION = 'struct-params-v1'
MODULE_SCOPE = (
    'STRUCT-01 首刀：基于 price_index 合并 OHLCV 计算 ATR/ADX/日线箱体/区间高点；'
    '无全历史核验源时不得宣称可信ATH；吸筹仅为弱证据启发式；可通过 evaluate_struct_gate 挂接 BACKTEST 对照格过滤'
)

# Explicit frozen rule params (acceptance: 规则参数显式冻结)
FROZEN_PARAMS = {
    'atr_period': 14,
    'adx_period': 14,
    'day_box_lookback': 20,
    'day_box_min_width_pct': 0.5,
    'absorption_vol_mult': 3.0,
    'absorption_price_change_pct': 2.0,
    'absorption_baseline_bars': 7,
    'absorption_min_streak': 3,
    'full_history_verified_source': False,
}

FORMULA_CATALOG = [
    {
        'formula_id': 'ATR_WILDER_v1',
        'title': 'ATR（Wilder）',
        'period_field': 'atr_period',
        'warmup_rule': 'period+1 根已收盘K（首根无前收，其后 period 根 TR）',
        'depends_on': 'price_index 合并 OHLCV',
    },
    {
        'formula_id': 'ADX_WILDER_v1',
        'title': 'ADX（Wilder）',
        'period_field': 'adx_period',
        'warmup_rule': '2*period+1 根已收盘K（DI 预热 period，ADX 再平滑 period）',
        'depends_on': 'price_index 合并 OHLCV',
    },
    {
        'formula_id': 'RANGE_HIGH_WINDOW_v1',
        'title': '区间高点（可得窗口）',
        'period_field': None,
        'warmup_rule': '至少 1 根有效高点',
        'depends_on': 'price_index 合并 OHLCV；非可信ATH',
    },
    {
        'formula_id': 'DAY_BOX_LOOKBACK_v1',
        'title': '日线箱体（触发前 lookback 根）',
        'period_field': 'day_box_lookback',
        'warmup_rule': 'lookback 根已收盘日线',
        'depends_on': 'interval=1d 的合并序列；24h高低点不可替代',
    },
    {
        'formula_id': 'ABSORPTION_VOL_PRICE_WEAK_v1',
        'title': '量增价平弱证据（非持续吸筹结论）',
        'period_field': 'absorption_min_streak',
        'warmup_rule': 'baseline_bars + min_streak',
        'depends_on': '合约量；不能替代现货吸筹',
        'evidence_strength': '弱证据',
    },
]


def _finite(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _f(value):
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _parse_bars(raw_bars):
    """Extract OHLCV from Binance-style raw lists. Skip invalid rows."""
    out = []
    for bar in raw_bars or []:
        if not isinstance(bar, (list, tuple)) or len(bar) < 6:
            continue
        open_time = bar[0]
        o, h, l, c, volume = (_f(bar[1]), _f(bar[2]), _f(bar[3]), _f(bar[4]), _f(bar[5]))
        if not all(_finite(x) for x in (o, h, l, c, volume)):
            continue
        if min(o, h, l, c) <= 0 or l > min(o, c) or h < max(o, c) or volume < 0:
            continue
        if not isinstance(open_time, int):
            try:
                open_time = int(open_time)
            except (TypeError, ValueError):
                continue
        out.append({
            'open_time': open_time,
            'open': o,
            'high': h,
            'low': l,
            'close': c,
            'volume': volume,
        })
    out.sort(key=lambda row: row['open_time'])
    return out


def _metric_insufficient(formula_id, warmup_required, bars_available, extra=None):
    row = {
        'formula_id': formula_id,
        'params_version': PARAMS_VERSION,
        'status': 'insufficient_history',
        'value': None,
        'warmup_required': warmup_required,
        'warmup_used': bars_available,
        'bars_available': bars_available,
        'note': f'可用K线 {bars_available} < 预热 {warmup_required}；不返回伪数值',
    }
    if extra:
        row.update(extra)
    return row


def _metric_ok(formula_id, value, warmup_required, bars_available, **extra):
    payload = {
        'formula_id': formula_id,
        'params_version': PARAMS_VERSION,
        'status': 'ok',
        'value': value if _finite(value) else None,
        'warmup_required': warmup_required,
        'warmup_used': warmup_required,
        'bars_available': bars_available,
    }
    if not _finite(value):
        payload['status'] = 'non_finite'
        payload['value'] = None
        payload['note'] = '计算结果非有限数，已拒绝输出'
    payload.update(extra)
    return payload


def compute_atr(bars, period=None):
    """Wilder ATR. First ATR = SMA of first `period` TRs."""
    period = int(period if period is not None else FROZEN_PARAMS['atr_period'])
    warmup = period + 1
    n = len(bars)
    if n < warmup:
        return _metric_insufficient('ATR_WILDER_v1', warmup, n, {'period': period})

    trs = []
    for i in range(1, n):
        high, low, prev_close = bars[i]['high'], bars[i]['low'], bars[i - 1]['close']
        tr = max(high - low, abs(high - prev_close), abs(low - prev_close))
        trs.append(tr)

    if len(trs) < period:
        return _metric_insufficient('ATR_WILDER_v1', warmup, n, {'period': period})

    atr = sum(trs[:period]) / period
    for tr in trs[period:]:
        atr = (atr * (period - 1) + tr) / period

    return _metric_ok(
        'ATR_WILDER_v1', atr, warmup, n,
        period=period,
        method='Wilder RMA；首值=前period根TR的SMA',
        coverage_note='仅基于当前合并窗口，非全历史',
    )


def compute_adx(bars, period=None):
    """Wilder ADX with +DI/-DI. Warmup = 2*period+1 bars."""
    period = int(period if period is not None else FROZEN_PARAMS['adx_period'])
    warmup = 2 * period + 1
    n = len(bars)
    if n < warmup:
        return _metric_insufficient(
            'ADX_WILDER_v1', warmup, n,
            {'period': period, 'plus_di': None, 'minus_di': None},
        )

    plus_dm, minus_dm, trs = [], [], []
    for i in range(1, n):
        up = bars[i]['high'] - bars[i - 1]['high']
        down = bars[i - 1]['low'] - bars[i]['low']
        plus_dm.append(up if up > down and up > 0 else 0.0)
        minus_dm.append(down if down > up and down > 0 else 0.0)
        high, low, prev_close = bars[i]['high'], bars[i]['low'], bars[i - 1]['close']
        trs.append(max(high - low, abs(high - prev_close), abs(low - prev_close)))

    # Wilder: seed with sum of first period, then smooth = smooth - smooth/period + cur
    atr = sum(trs[:period])
    smooth_plus = sum(plus_dm[:period])
    smooth_minus = sum(minus_dm[:period])
    dx_values = []

    def _di(atr_s, p_s, m_s):
        if atr_s <= 0:
            return None, None, None
        pdi = 100.0 * p_s / atr_s
        mdi = 100.0 * m_s / atr_s
        denom = pdi + mdi
        dx = 100.0 * abs(pdi - mdi) / denom if denom > 0 else None
        return pdi, mdi, dx

    pdi, mdi, dx = _di(atr, smooth_plus, smooth_minus)
    if dx is not None:
        dx_values.append(dx)

    for i in range(period, len(trs)):
        atr = atr - (atr / period) + trs[i]
        smooth_plus = smooth_plus - (smooth_plus / period) + plus_dm[i]
        smooth_minus = smooth_minus - (smooth_minus / period) + minus_dm[i]
        pdi, mdi, dx = _di(atr, smooth_plus, smooth_minus)
        if dx is not None:
            dx_values.append(dx)

    if len(dx_values) < period:
        return _metric_insufficient(
            'ADX_WILDER_v1', warmup, n,
            {'period': period, 'plus_di': None, 'minus_di': None},
        )

    adx = sum(dx_values[:period]) / period
    for dx in dx_values[period:]:
        adx = (adx * (period - 1) + dx) / period

    return _metric_ok(
        'ADX_WILDER_v1', adx, warmup, n,
        period=period,
        plus_di=pdi if _finite(pdi) else None,
        minus_di=mdi if _finite(mdi) else None,
        method='Wilder；DX 序列再 Wilder 平滑为 ADX',
        coverage_note='仅基于当前合并窗口',
    )


def compute_range_high(bars):
    """Max high in available window — always 区间高点, never 可信ATH."""
    if not bars:
        return _metric_insufficient(
            'RANGE_HIGH_WINDOW_v1', 1, 0,
            {
                'label': '区间高点',
                'ath_claim': False,
                'full_history_verified': False,
            },
        )
    highs = [b['high'] for b in bars if _finite(b['high'])]
    if not highs:
        return {
            'formula_id': 'RANGE_HIGH_WINDOW_v1',
            'params_version': PARAMS_VERSION,
            'status': 'non_finite',
            'value': None,
            'label': '区间高点',
            'ath_claim': False,
            'full_history_verified': bool(FROZEN_PARAMS['full_history_verified_source']),
            'warmup_required': 1,
            'warmup_used': 0,
            'bars_available': len(bars),
            'note': '无有效高点',
        }
    peak = max(highs)
    peak_bar = next(b for b in bars if b['high'] == peak)
    verified = bool(FROZEN_PARAMS['full_history_verified_source'])
    return _metric_ok(
        'RANGE_HIGH_WINDOW_v1', peak, 1, len(bars),
        label='区间高点' if not verified else '可信ATH',
        ath_claim=verified,
        full_history_verified=verified,
        open_time=peak_bar['open_time'],
        window_bars=len(bars),
        note=(
            '全历史核验源未接入；仅标区间高点，禁止当作可信ATH'
            if not verified else '已核验全历史来源'
        ),
    )


def compute_day_box(bars, lookback=None, interval='1d'):
    """Day-box from last `lookback` closed bars of the available window only."""
    lookback = int(lookback if lookback is not None else FROZEN_PARAMS['day_box_lookback'])
    min_width = float(FROZEN_PARAMS['day_box_min_width_pct'])
    if interval != '1d':
        return {
            'formula_id': 'DAY_BOX_LOOKBACK_v1',
            'params_version': PARAMS_VERSION,
            'status': 'wrong_interval',
            'value': None,
            'lookback': lookback,
            'warmup_required': lookback,
            'warmup_used': len(bars),
            'bars_available': len(bars),
            'note': '日线箱体仅在 interval=1d 计算；4h 不冒充日线箱体',
            'label': '日线箱体（可得窗口）',
        }
    if len(bars) < lookback:
        return _metric_insufficient(
            'DAY_BOX_LOOKBACK_v1', lookback, len(bars),
            {'lookback': lookback, 'label': '日线箱体（可得窗口）', 'high': None, 'low': None},
        )
    window = bars[-lookback:]
    box_high = max(b['high'] for b in window)
    box_low = min(b['low'] for b in window)
    mid = (box_high + box_low) / 2.0
    width_pct = ((box_high - box_low) / mid * 100.0) if mid > 0 else None
    last = window[-1]
    above = last['close'] > box_high
    inside = box_low <= last['close'] <= box_high
    return _metric_ok(
        'DAY_BOX_LOOKBACK_v1', box_high, lookback, len(bars),
        lookback=lookback,
        high=box_high,
        low=box_low,
        width_pct=width_pct if _finite(width_pct) else None,
        min_width_pct=min_width,
        width_ok=_finite(width_pct) and width_pct >= min_width,
        last_close=last['close'],
        position=('above' if above else ('inside' if inside else 'below')),
        label='日线箱体（可得窗口）',
        note='上沿/下沿取触发前 lookback 根可得日线；非全历史箱体；参数已冻结',
    )


def compute_absorption_weak(bars):
    """Volume-up / price-flat streak — explicitly weak evidence, not 持续吸筹 confirmation."""
    mult = float(FROZEN_PARAMS['absorption_vol_mult'])
    price_pct = float(FROZEN_PARAMS['absorption_price_change_pct'])
    baseline = int(FROZEN_PARAMS['absorption_baseline_bars'])
    min_streak = int(FROZEN_PARAMS['absorption_min_streak'])
    warmup = baseline + min_streak
    n = len(bars)
    if n < warmup:
        return _metric_insufficient(
            'ABSORPTION_VOL_PRICE_WEAK_v1', warmup, n,
            {
                'evidence_strength': '弱证据',
                'streak': 0,
                'note_extra': '合约量启发式，不能确认持续吸筹或现货买盘',
            },
        )

    streak = 0
    hits = []
    for i in range(baseline, n):
        prior = bars[i - baseline:i]
        mean_vol = sum(b['volume'] for b in prior) / baseline
        bar = bars[i]
        if mean_vol <= 0:
            streak = 0
            continue
        price_change = abs(bar['close'] - bar['open']) / bar['open'] * 100.0
        vol_ok = bar['volume'] >= mult * mean_vol
        price_ok = price_change <= price_pct
        if vol_ok and price_ok:
            streak += 1
            hits.append({
                'open_time': bar['open_time'],
                'volume_ratio': bar['volume'] / mean_vol if mean_vol else None,
                'price_change_pct': price_change,
            })
        else:
            streak = 0

    # trailing streak
    trailing = 0
    for i in range(n - 1, baseline - 1, -1):
        prior = bars[i - baseline:i]
        mean_vol = sum(b['volume'] for b in prior) / baseline
        bar = bars[i]
        if mean_vol <= 0:
            break
        price_change = abs(bar['close'] - bar['open']) / bar['open'] * 100.0
        if bar['volume'] >= mult * mean_vol and price_change <= price_pct:
            trailing += 1
        else:
            break

    ok = trailing >= min_streak
    return {
        'formula_id': 'ABSORPTION_VOL_PRICE_WEAK_v1',
        'params_version': PARAMS_VERSION,
        'status': 'ok' if ok else 'no_signal',
        'value': trailing if ok else None,
        'warmup_required': warmup,
        'warmup_used': warmup if n >= warmup else n,
        'bars_available': n,
        'evidence_strength': '弱证据',
        'trailing_streak': trailing,
        'min_streak': min_streak,
        'vol_mult': mult,
        'price_change_pct_max': price_pct,
        'baseline_bars': baseline,
        'recent_hits': hits[-5:],
        'note': (
            '量倍数与价变阈值命中的连续根数仅为弱证据；'
            '合约量不能替代现货吸筹，不能宣称持续吸筹已验证'
        ),
        'confirmed_absorption': False,
    }


def compute_from_bars(bars, interval='1d', overrides=None):
    """Compute all STRUCT-01 first-slice metrics from parsed OHLCV bars."""
    if overrides:
        for key, value in overrides.items():
            if key in FROZEN_PARAMS and value != FROZEN_PARAMS[key]:
                raise ValueError(f'参数已冻结，不可覆盖: {key}')
    frozen = dict(FROZEN_PARAMS)
    if not bars:
        parsed = []
    elif isinstance(bars[0], (list, tuple)):
        parsed = _parse_bars(bars)
    else:
        parsed = []
        for bar in bars:
            o, h, l, c, volume = (_f(bar.get('open')), _f(bar.get('high')), _f(bar.get('low')),
                                  _f(bar.get('close')), _f(bar.get('volume')))
            if not all(_finite(x) for x in (o, h, l, c, volume)):
                continue
            ot = bar.get('open_time')
            if not isinstance(ot, int):
                try:
                    ot = int(ot)
                except (TypeError, ValueError):
                    continue
            parsed.append({'open_time': ot, 'open': o, 'high': h, 'low': l, 'close': c, 'volume': volume})
        parsed.sort(key=lambda row: row['open_time'])

    atr = compute_atr(parsed, frozen['atr_period'])
    adx = compute_adx(parsed, frozen['adx_period'])
    range_high = compute_range_high(parsed)
    day_box = compute_day_box(parsed, frozen['day_box_lookback'], interval=interval)
    absorption = compute_absorption_weak(parsed)

    return {
        'params_version': PARAMS_VERSION,
        'params': dict(FROZEN_PARAMS),
        'interval': interval,
        'bars_used': len(parsed),
        'full_history_verified': bool(FROZEN_PARAMS['full_history_verified_source']),
        'metrics': {
            'atr': atr,
            'adx': adx,
            'range_high': range_high,
            'day_box': day_box,
            'absorption': absorption,
        },
        'backtest_hook': {
            'ready': False,
            'module': 'BACKTEST-01',
            'filter_gate': True,
            'note': 'STRUCT 指标包本身不是完整回测引擎；过滤闸由 backtest_store 挂接；无可信ATH',
        },
        'scope': MODULE_SCOPE,
    }



# Metrics that STRUCT gate may require (never 可信ATH).
STRUCT_GATE_METRICS = {
    'atr': 'atr',
    'adx': 'adx',
    'day_box': 'day_box',
    'range_high': 'range_high',
    'absorption': 'absorption',
}
ATH_FORBIDDEN_KEYS = {
    'ath', '可信ath', 'verified_ath', 'full_ath', 'trusted_ath', 'true_ath',
}


def resolve_struct_gate_config(body=None, version_rules=None):
    """Resolve struct_filter from request body or frozen version rules.

    Modes: off | optional | required
    Missing required params (mode=required but empty require) → caller marks unimplemented.
    """
    body = body if isinstance(body, dict) else {}
    rules = version_rules if isinstance(version_rules, dict) else {}
    raw = body.get('struct_filter')
    source = 'body'
    if raw is None:
        raw = body.get('struct_gate')
    if raw is None:
        raw = rules.get('struct_filter') or rules.get('struct_gate')
        source = 'version_rules'
    if raw is None or raw is False or raw == 'off':
        return {
            'mode': 'off',
            'require': [],
            'min_adx': None,
            'source': source if raw is not None else 'default_off',
            'params_complete': True,
        }
    if raw is True or raw == 'required':
        return {
            'mode': 'required',
            'require': ['atr', 'adx'],
            'min_adx': None,
            'source': source,
            'params_complete': True,
            'note': 'bool/required → 默认要求 ATR+ADX 可算',
        }
    if not isinstance(raw, dict):
        raise ValueError('struct_filter 须为对象、布尔或 off')
    mode = str(raw.get('mode') or 'required').strip().lower()
    if mode not in ('off', 'optional', 'required'):
        raise ValueError('struct_filter.mode 仅支持 off / optional / required')
    require = raw.get('require') or raw.get('metrics') or []
    if isinstance(require, str):
        require = [require]
    if not isinstance(require, list):
        raise ValueError('struct_filter.require 须为字符串列表')
    cleaned = []
    for item in require:
        if not isinstance(item, str) or not item.strip():
            raise ValueError('struct_filter.require 项须为非空字符串')
        cleaned.append(item.strip())
    min_adx = raw.get('min_adx')
    if min_adx is not None:
        try:
            min_adx = float(min_adx)
        except (TypeError, ValueError) as exc:
            raise ValueError('struct_filter.min_adx 须为数字') from exc
        if not math.isfinite(min_adx) or min_adx < 0:
            raise ValueError('struct_filter.min_adx 须为非负有限数')
    params_complete = True
    if mode == 'required' and not cleaned:
        params_complete = False
    return {
        'mode': mode,
        'require': cleaned,
        'min_adx': min_adx,
        'source': source,
        'params_complete': params_complete,
        'params_version': PARAMS_VERSION,
    }


def evaluate_struct_gate(bars, interval='4h', gate=None, *, as_of_index=None, value_thresholds=True):
    """Evaluate STRUCT filter for a backtest/compare cell or as-of a bar.

    - mode=off → not applied; tradable unchanged
    - mode=optional → compute + soft exclusions; still tradable
    - mode=required → must pass; failures → explicit exclusions (never silent)
    - as_of_index: only bars[0:as_of_index+1] (no future leak). None = full series.
    - value_thresholds: if False, only warmup/status checks (cell preflight);
      min_adx etc. skipped so end-window values cannot gate the whole run.
    Never claims 可信ATH. Unknown/ATH require keys → unimplemented.
    Never silent-fills missing as-of history from future bars.
    """
    gate = gate if isinstance(gate, dict) else {'mode': 'off', 'require': []}
    mode = gate.get('mode') or 'off'
    as_of_meta = None
    if as_of_index is not None:
        try:
            as_of_index = int(as_of_index)
        except (TypeError, ValueError) as exc:
            raise ValueError('as_of_index 须为整数') from exc
        if as_of_index < 0:
            return {
                'applied': True,
                'mode': mode,
                'ok': False,
                'tradable': False,
                'status': 'insufficient',
                'exclusions': [{
                    'code': 'as_of_out_of_range',
                    'metric': None,
                    'reason': f'as_of_index={as_of_index} 无效；拒绝用未来/空窗填补',
                }],
                'as_of_index': as_of_index,
                'lookahead_guard': True,
                'params_version': PARAMS_VERSION,
                'ath_claim': False,
            }
        if not bars or as_of_index >= len(bars):
            return {
                'applied': True,
                'mode': mode,
                'ok': False,
                'tradable': False,
                'status': 'insufficient',
                'exclusions': [{
                    'code': 'as_of_insufficient',
                    'metric': None,
                    'reason': (
                        f'as_of_index={as_of_index} 超出可得K线 {len(bars) if bars else 0}；'
                        '拒绝静默借用未来K线'
                    ),
                    'bars_available': len(bars) if bars else 0,
                }],
                'as_of_index': as_of_index,
                'lookahead_guard': True,
                'params_version': PARAMS_VERSION,
                'ath_claim': False,
            }
        bars = bars[: as_of_index + 1]
        as_of_meta = {'as_of_index': as_of_index, 'bars_used_prefix': len(bars), 'lookahead_guard': True}
    if mode == 'off':
        return {
            'applied': False,
            'mode': 'off',
            'ok': True,
            'tradable': True,
            'status': 'skipped',
            'exclusions': [],
            'params_version': PARAMS_VERSION,
            'ath_claim': False,
            'note': '未启用 STRUCT 过滤',
        }

    if mode == 'required' and not gate.get('params_complete', True):
        return {
            'applied': True,
            'mode': mode,
            'ok': False,
            'tradable': False,
            'status': 'unimplemented',
            'exclusions': [{
                'code': 'struct_params_missing',
                'metric': None,
                'reason': (
                    'struct_filter.mode=required 但未声明 require 指标；'
                    '拒绝默认可交易，也不回落通用价格规则'
                ),
            }],
            'params_version': PARAMS_VERSION,
            'ath_claim': False,
            'full_history_verified': False,
        }

    require = list(gate.get('require') or [])
    exclusions = []
    for key in require:
        low = key.lower()
        if low in ATH_FORBIDDEN_KEYS or '可信' in key:
            exclusions.append({
                'code': 'ath_unimplemented',
                'metric': key,
                'reason': (
                    '禁止要求可信ATH：当前无全历史核验源；'
                    '仅可要求 range_high（区间高点），不得伪装 ATH 完备'
                ),
            })
            continue
        if low not in STRUCT_GATE_METRICS and key not in STRUCT_GATE_METRICS:
            exclusions.append({
                'code': 'unknown_struct_metric',
                'metric': key,
                'reason': f'未知结构指标 {key!r}；未实现，不静默忽略',
            })

    if any(e['code'] in ('ath_unimplemented', 'unknown_struct_metric') for e in exclusions):
        return {
            'applied': True,
            'mode': mode,
            'ok': False,
            'tradable': False if mode == 'required' else True,
            'status': 'unimplemented',
            'exclusions': exclusions,
            'params_version': PARAMS_VERSION,
            'ath_claim': False,
            'full_history_verified': False,
        }

    bundle = compute_from_bars(bars, interval=interval)
    metrics = bundle.get('metrics') or {}
    metric_summaries = {}

    for key in require:
        mk = STRUCT_GATE_METRICS.get(key) or STRUCT_GATE_METRICS.get(key.lower())
        metric = metrics.get(mk) or {}
        st = metric.get('status')
        metric_summaries[mk] = {
            'status': st,
            'value': metric.get('value'),
            'formula_id': metric.get('formula_id'),
            'label': metric.get('label'),
            'ath_claim': bool(metric.get('ath_claim')),
        }
        if st == 'insufficient_history':
            exclusions.append({
                'code': 'insufficient_history',
                'metric': mk,
                'reason': metric.get('note') or f'{mk} 预热不足',
                'warmup_required': metric.get('warmup_required'),
                'bars_available': metric.get('bars_available'),
            })
        elif st == 'wrong_interval':
            exclusions.append({
                'code': 'wrong_interval',
                'metric': mk,
                'reason': metric.get('note') or f'{mk} 周期不匹配（interval={interval}）',
            })
        elif st != 'ok':
            exclusions.append({
                'code': st or 'struct_failed',
                'metric': mk,
                'reason': metric.get('note') or f'{mk} 状态={st}，不可交易',
            })
        else:
            if value_thresholds and mk == 'adx' and gate.get('min_adx') is not None:
                val = metric.get('value')
                if not _finite(val) or float(val) < float(gate['min_adx']):
                    exclusions.append({
                        'code': 'below_min_adx',
                        'metric': 'adx',
                        'reason': (
                            f'ADX={val} < min_adx={gate["min_adx"]}；结构过滤未通过'
                            + ('（as-of 前缀）' if as_of_meta else '')
                        ),
                        'value': val,
                        'min_adx': gate['min_adx'],
                        'as_of': as_of_meta,
                    })
            if mk == 'range_high' and metric.get('ath_claim'):
                # defensive — compute_range_high never sets ath_claim True
                exclusions.append({
                    'code': 'ath_claim_refused',
                    'metric': 'range_high',
                    'reason': '区间高点不得升格为可信ATH',
                })

    hard_fail = bool(exclusions)
    if mode == 'optional':
        return {
            'applied': True,
            'mode': mode,
            'ok': True,
            'tradable': True,
            'status': 'optional_noted' if hard_fail else 'ok',
            'exclusions': exclusions,  # soft — explicit but not blocking
            'blocking': False,
            'metrics': metric_summaries,
            'params_version': PARAMS_VERSION,
            'ath_claim': False,
            'full_history_verified': bool(bundle.get('full_history_verified')),
            'bars_used': bundle.get('bars_used'),
            'note': 'optional：排除原因已记录，不阻断行走',
            'as_of': as_of_meta,
            'value_thresholds': bool(value_thresholds),
            'lookahead_guard': True,
        }

    # required
    if not hard_fail:
        status = 'ok'
    elif any(e['code'] == 'insufficient_history' for e in exclusions):
        status = 'insufficient'
    elif any(e['code'] in ('ath_unimplemented', 'unknown_struct_metric', 'struct_params_missing') for e in exclusions):
        status = 'unimplemented'
    else:
        status = 'excluded_struct'

    return {
        'applied': True,
        'mode': mode,
        'ok': not hard_fail,
        'tradable': not hard_fail,
        'status': status,
        'exclusions': exclusions,
        'blocking': hard_fail,
        'metrics': metric_summaries,
        'params_version': PARAMS_VERSION,
        'ath_claim': False,
        'full_history_verified': bool(bundle.get('full_history_verified')),
        'bars_used': bundle.get('bars_used'),
        'note': (
            'required：结构过滤未通过，单元格不可交易'
            if hard_fail else 'required：结构过滤通过'
        ),
        'as_of': as_of_meta,
        'value_thresholds': bool(value_thresholds),
        'lookahead_guard': True,
    }


def evaluate_struct_gate_as_of(bars, as_of_index, interval='4h', gate=None):
    """STRUCT as-of bar i: only bars[0..i]; never peeks at i+1..end."""
    return evaluate_struct_gate(
        bars, interval=interval, gate=gate,
        as_of_index=as_of_index, value_thresholds=True,
    )


def compute_for_symbol(root, market, symbol, interval='1d', start=None, end=None):
    """Merge via price_index then compute. No live Binance fetch."""
    if interval not in ('4h', '1d'):
        raise ValueError('仅支持4h或1d')
    if not market or not symbol:
        raise ValueError('须提供 market 与 symbol')

    merged = merge_series(root, market, symbol, interval, start=start, end=end, include_bars=True)
    bars = merged.get('bars') or []
    result = compute_from_bars(bars, interval=interval)
    result.update({
        'market': market,
        'symbol': symbol,
        'window': merged.get('window'),
        'coveragePct': merged.get('coveragePct'),
        'known': merged.get('known'),
        'expected': merged.get('expected'),
        'complete': merged.get('complete'),
        'gap_count': merged.get('gap_count'),
        'continuity': merged.get('continuity'),
        'label_current': merged.get('label_current'),
        'conflicts': merged.get('conflicts'),
        'price_scope': merged.get('scope'),
        'evidence_count': len(merged.get('evidence') or []),
        'source_errors': merged.get('errors') or [],
    })
    # coverage summary per metric
    result['coverage'] = {
        'bars_known': merged.get('known'),
        'coveragePct': merged.get('coveragePct'),
        'continuous': (merged.get('continuity') or {}).get('continuous'),
        'trailing_continuous': (merged.get('continuity') or {}).get('trailing_continuous'),
        'warmup_ready_hint': (merged.get('continuity') or {}).get('warmup_ready_hint'),
        'note': '覆盖来自 price_index 合并窗口；局部覆盖不等于可信ATH或全历史箱体',
    }
    return result


def catalog():
    return {
        'params_version': PARAMS_VERSION,
        'lookahead_guard': True,
        'as_of_api': 'evaluate_struct_gate_as_of',
        'params': dict(FROZEN_PARAMS),
        'formulas': FORMULA_CATALOG,
        'ath_policy': {
            'full_history_verified_source': bool(FROZEN_PARAMS['full_history_verified_source']),
            'label_when_unverified': '区间高点',
            'label_when_verified': '可信ATH',
            'note': '当前无核验全历史来源标志；一律输出区间高点',
        },
        'backtest_hook': {
            'ready': True,
            'filter_gate': True,
            'api': 'evaluate_struct_gate / backtest struct_filter',
            'note': 'STRUCT 过滤闸已挂接 BACKTEST；历史宇宙/可信ATH 仍未齐',
        },
        'scope': MODULE_SCOPE,
    }


def list_struct_ready(root, interval='1d', limit=50):
    """List symbols from price_index with warmup readiness hints (no compute spam)."""
    summary = summarize_price_index(root, interval=interval, limit=limit)
    atr_w = FROZEN_PARAMS['atr_period'] + 1
    adx_w = 2 * FROZEN_PARAMS['adx_period'] + 1
    box_w = FROZEN_PARAMS['day_box_lookback']
    rows = []
    for item in summary.get('series') or []:
        approx = int(item.get('approx_bars_scan') or 0) + int(item.get('approx_bars_range_known') or 0)
        rows.append({
            'market': item.get('market'),
            'symbol': item.get('symbol'),
            'interval': interval,
            'approx_bars': approx,
            'label_current': item.get('label_current'),
            'atr_warmup_ok': approx >= atr_w,
            'adx_warmup_ok': approx >= adx_w,
            'day_box_warmup_ok': interval == '1d' and approx >= box_w,
            'ath_claim_allowed': False,
            'note': '目录级近似根数；精确覆盖/缺口以 /api/struct/compute 为准',
        })
    return {
        'params_version': PARAMS_VERSION,
        'interval': interval,
        'count': len(rows),
        'series': rows,
        'scope': summary.get('scope') or MODULE_SCOPE,
        'ath_policy': catalog()['ath_policy'],
    }
