"""BACKTEST-01: local bar-walk + multi-symbol/version fair compare (first knife).

Research/simulation only. Local merged OHLCV only — no live Binance mass download,
no paid AI, no future data, no today's market-cap backfill into the past.

Depends on:
- SIM-01 fee_rate / slip_rate (config defaults)
- EVAL-01 same_bar / gap / missing_bars ambiguity rules
- RULE-01 frozen strategy versions
- STRUCT-01 optional (hook only; not required for first-slice entry)

Acceptance (first vertical slice):
- One frozen strategy version + one symbol + requested window
- Compare knife: multi-symbol × multi-version under shared fees/ambiguity/absolute splits
- Optional train/valid/test by time (no shuffle leak)
- Metrics: trades, PnL, win_rate only if samples>0 else 无样本
- Persist run with params hash, data range, exclusions
- Honest coverage warnings (survivor bias / local bars only)
"""
from __future__ import annotations

from durable_io import protected
import hashlib
import json
import math
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path

from snapshot_store import atomic, encoded
from struct_indicators import (
    evaluate_struct_gate,
    evaluate_struct_gate_as_of,
    resolve_struct_gate_config,
)

# Mirror EVAL-01 fixed ambiguity rules (do not change silently).
SAME_BAR_TP_SL = 'worse_for_trader'
GAP_THROUGH_LEVEL = 'fill_at_open'
MISSING_BARS = 'data_gap'

AMBIGUITY_RULES = {
    'same_bar_tp_sl': SAME_BAR_TP_SL,
    'same_bar_tp_sl_note': (
        '同根同时触及止盈止损 → worse_for_trader：按止损出场，不按止盈记赢。'
    ),
    'gap_through_level': GAP_THROUGH_LEVEL,
    'gap_through_level_note': (
        '跳空穿过价位 → 以该根可得K线开盘价成交。'
    ),
    'missing_bars': MISSING_BARS,
    'missing_bars_note': (
        '持仓窗口内无足够K线/断档 → data_gap；不计胜率样本，禁止假赢。'
    ),
}

ENTRY_RULES = {
    'close_above_prev_high': {
        'side': 'long',
        'note': '收盘价高于前一根最高价 → 下一根开盘做多（无未来函数）',
    },
    'close_below_prev_low': {
        'side': 'short',
        'note': '收盘价低于前一根最低价 → 下一根开盘做空（无未来函数）',
    },
    'flat_reenter': {
        'side': 'long',
        'note': '空仓即在下一根开盘再入场（仅烟雾/覆盖测试；会密集交易）',
    },
}


def _normalize_backtest_scope(body, frozen):
    """full_strategy vs named ema tech slice (never claim slice as full PnL)."""
    raw = (
        (body or {}).get('backtest_scope')
        or (body or {}).get('experiment_kind')
        or (body or {}).get('scope')
        or ''
    )
    raw = str(raw or '').strip().lower()
    if raw in ('ema_tech_slice', 'ema99_tech_slice', 'tech_slice', 'price_ema_slice'):
        return 'ema_tech_slice'
    mode = (frozen or {}).get('execution_mode') or 'pct_touch'
    if mode == 'ema99_close_trail':
        return 'full_strategy' if not raw else ('ema_tech_slice' if 'slice' in raw else 'full_strategy')
    return 'full_strategy'


def _historical_evidence_gaps(body, version_meta):
    """Detect missing historical OI / mcap / account evidence for full-strategy claims.

    Client may explicitly attach evidence flags/objects; absent → gap.
    Never invent or backfill today's mcap/OI.
    """
    body = body if isinstance(body, dict) else {}
    gaps = []
    # Explicit evidence payloads
    oi = body.get('historical_oi') or body.get('oi_evidence') or body.get('oi_series')
    mcap = body.get('historical_mcap') or body.get('mcap_evidence') or body.get('mcap_series')
    acct = body.get('account_evidence') or body.get('historical_account') or body.get('account_series')
    flags = body.get('evidence_flags') if isinstance(body.get('evidence_flags'), dict) else {}
    if flags.get('historical_oi') is True:
        oi = oi or True
    if flags.get('historical_mcap') is True:
        mcap = mcap or True
    if flags.get('account_evidence') is True:
        acct = acct or True
    # Declared deps on frozen version
    deps = []
    if isinstance(version_meta, dict):
        deps = list(version_meta.get('data_dependencies') or [])
        rules = version_meta.get('rules') or {}
        deps.extend(list(rules.get('data_dependencies') or []))
    deps_l = {str(d).lower() for d in deps}
    needs_oi = any(x in deps_l for x in ('oi', 'open_interest', 'historical_oi', 'oi_usd'))
    needs_mcap = any(x in deps_l for x in ('mcap', 'market_cap', 'historical_mcap'))
    needs_acct = any(x in deps_l for x in ('account', 'account_evidence', 'balance'))
    params = ((version_meta or {}).get('rules') or {}).get('params') or {}
    mode = str(params.get('execution_mode') or params.get('exit_mode') or params.get('mode') or '').lower()
    # EMA99 full-strategy product rules always require historical OI evidence
    if params.get('oi_exit_ratio') is not None or mode in (
        'ema99_close_trail', 'ema99', 'ema99_trail', 'ema99_close', 'close_trail',
    ):
        needs_oi = True
    if needs_oi and not oi:
        gaps.append('historical_oi')
    if needs_mcap and not mcap:
        gaps.append('historical_mcap')
    if needs_acct and not acct:
        gaps.append('account_evidence')
    return gaps


DISCLAIMER = (
    '研究/模拟历史回测：本地合并K线；支持单跑与多合约/多版本公平对照；'
    '非全市场宇宙重建；非实盘；不下载币安全量；不回填当今市值。'
)

COVERAGE_WARNINGS = [
    {
        'id': 'local_bars_only',
        'title': '仅本地可得K线',
        'note': '只使用本机 price_index 合并后的 OHLCV；缺档不填零、不联网补全。',
    },
    {
        'id': 'survivor_bias',
        'title': '生存者偏差',
        'note': (
            '首刀不重建历史币池/退市名单；对已下架合约的覆盖未知。'
            '报告不得宣称完整历史宇宙回测。'
        ),
    },
    {
        'id': 'no_market_cap_backfill',
        'title': '禁止当今市值回填',
        'note': '回测路径不读取、不写入流通市值；禁止把今天的市值填进过去。',
    },
    {
        'id': 'not_full_universe',
        'title': '非全市场 / 非历史宇宙重建',
        'note': (
            '对照批次在用户给定合约×版本矩阵上公平行走；'
            '不重建历史上市/退市币池（historical listed-universe rebuild 仍为缺口）。'
        ),
    },
    {
        'id': 'fair_compare_batch',
        'title': '公平对照批次',
        'note': (
            '同批共用费用/滑点/歧义规则与绝对时间训/验/测切点；'
            '各格独立报告 insufficient/排除数；缺档格不得记为0收益胜出。'
        ),
    },
]

DEFAULT_SPLITS = {'train': 0.6, 'valid': 0.2, 'test': 0.2}
MIN_BARS_DEFAULT = {'4h': 48, '1d': 30}  # ~8d / 30d — clear insufficient if shorter


def _utcnow():
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _new_id(prefix):
    return f'{prefix}-{uuid.uuid4().hex[:12]}'


def _finite(name, value):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f'{name}须为有限数字')
    v = float(value)
    if not math.isfinite(v):
        raise ValueError(f'{name}须为有限数字')
    return v


def _round_u(v):
    return round(float(v), 8)


def _parse_iso(ts):
    if not ts or not isinstance(ts, str):
        raise ValueError('时间须为 ISO8601 字符串')
    s = ts.strip().replace('Z', '+00:00')
    try:
        dt = datetime.fromisoformat(s)
    except ValueError as exc:
        raise ValueError(f'无法解析时间: {ts}') from exc
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _ms(dt):
    return int(dt.timestamp() * 1000)


def _sha256_obj(obj):
    raw = json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)
    return hashlib.sha256(raw.encode('utf-8')).hexdigest()


def apply_slip(price, side, slip_rate, is_entry):
    """Adverse slip matching SIM-01: entry pays worse, exit receives worse."""
    p = float(price)
    s = float(slip_rate or 0)
    if s < 0:
        raise ValueError('slip_rate 不可为负')
    if side == 'long':
        return p * (1 + s) if is_entry else p * (1 - s)
    return p * (1 - s) if is_entry else p * (1 + s)


def fee_on(notional, fee_rate):
    return abs(float(notional)) * float(fee_rate or 0)


def resolve_exit_on_bar(side, entry, tp, sl, bar):
    """EVAL-compatible single-bar exit resolution. Returns hit dict or None."""
    t, o, h, l, c = int(bar[0]), float(bar[1]), float(bar[2]), float(bar[3]), float(bar[4])
    if side == 'long':
        gap_sl = o <= sl and o < entry
        gap_tp = o >= tp and o > entry
        touch_sl = l <= sl
        touch_tp = h >= tp
    else:
        gap_sl = o >= sl and o > entry
        gap_tp = o <= tp and o < entry
        touch_sl = h >= sl
        touch_tp = l <= tp

    if gap_sl and gap_tp:
        return {
            'exit_price': o, 'exit_reason': 'stop_loss',
            'fill_rule': 'gap_through_level+same_bar_worse', 'bar_time': t,
        }
    if gap_sl:
        return {
            'exit_price': o, 'exit_reason': 'stop_loss',
            'fill_rule': 'gap_through_level', 'bar_time': t,
        }
    if gap_tp:
        return {
            'exit_price': o, 'exit_reason': 'take_profit',
            'fill_rule': 'gap_through_level', 'bar_time': t,
        }
    if touch_sl and touch_tp:
        return {
            'exit_price': sl, 'exit_reason': 'stop_loss',
            'fill_rule': 'same_bar_tp_sl:worse_for_trader', 'bar_time': t,
        }
    if touch_sl:
        return {
            'exit_price': sl, 'exit_reason': 'stop_loss',
            'fill_rule': 'intrabar_level', 'bar_time': t,
        }
    if touch_tp:
        return {
            'exit_price': tp, 'exit_reason': 'take_profit',
            'fill_rule': 'intrabar_level', 'bar_time': t,
        }
    return None



def resolve_ema99_trail_bar(pos, bar, ema99_value):
    """Apply one closed bar to an ema99_close_trail position state.

    pos must carry trail_state from ema_squeeze_rules.initial_stop_state.
    Returns (updated_pos, exit_hit_or_None). Exit fill is deferred to next open
    via pos['pending_exit']; caller should fill on subsequent bar open.
    """
    from ema_squeeze_rules import process_close_for_trail, resolve_ema99_exit_fill
    t, o, h, l, c = int(bar[0]), float(bar[1]), float(bar[2]), float(bar[3]), float(bar[4])
    # If previous bar set pending_exit, fill at this open
    if pos.get('pending_exit'):
        hit = resolve_ema99_exit_fill(pending_exit=True, next_open=o)
        if hit is not None:
            hit = dict(hit)
            hit['bar_time'] = t
        return pos, hit
    state = pos.get('trail_state')
    if not isinstance(state, dict):
        raise ValueError('ema99_close_trail 仓位缺少 trail_state')
    new_state = process_close_for_trail(state, close=c, ema99=ema99_value, bar_time=t)
    pos = dict(pos)
    pos['trail_state'] = new_state
    pos['stop'] = new_state.get('stop')
    pos['trailing_activated'] = new_state.get('trailing_activated')
    if new_state.get('pending_exit'):
        pos['pending_exit'] = True
        pos['pending_exit_reason'] = new_state.get('pending_exit_reason')
        # Do not exit on this bar's close price; wait next open
        return pos, None
    return pos, None


def signal_entry(rule, bars, i):
    """Return True if bar i produces an entry signal for fill at open of i+1.

    Never peeks at bars after i (no future leak).
    """
    if i < 0 or i >= len(bars):
        return False
    if rule == 'flat_reenter':
        return True
    if rule == 'close_above_prev_high':
        if i < 1:
            return False
        return float(bars[i][4]) > float(bars[i - 1][2])
    if rule == 'close_below_prev_low':
        if i < 1:
            return False
        return float(bars[i][4]) < float(bars[i - 1][3])
    if rule in ('ema_squeeze_confirmed', 'ema_squeeze_full_cross_vol', 'candidate_cross_vol'):
        # Full candidate/OI/history path cannot be synthesized from OHLC alone.
        # Callers must precompute signal flags on bars[i] (dict with confirmed=True)
        # or pass parallel signal series via bar metadata; bare OHLC → False (no invent).
        bar = bars[i]
        if isinstance(bar, dict):
            return bool(bar.get('ema_squeeze_confirmed') or bar.get('confirmed'))
        if isinstance(bar, (list, tuple)) and len(bar) > 6 and isinstance(bar[6], dict):
            meta = bar[6]
            return bool(meta.get('ema_squeeze_confirmed') or meta.get('confirmed'))
        return False
    raise ValueError(f'不支持的 entry_rule: {rule}')


def split_by_time(bars, ratios):
    """Split bars into train/valid/test by chronological index (no shuffle)."""
    n = len(bars)
    train_r = float(ratios.get('train', 0.6))
    valid_r = float(ratios.get('valid', 0.2))
    test_r = float(ratios.get('test', 0.2))
    total = train_r + valid_r + test_r
    if abs(total - 1.0) > 1e-9:
        raise ValueError('train+valid+test 比例之和须为 1')
    if any(x < 0 for x in (train_r, valid_r, test_r)):
        raise ValueError('分割比例不可为负')
    i1 = int(n * train_r)
    i2 = i1 + int(n * valid_r)
    # Remainder goes to test so lengths sum to n
    return {
        'train': bars[:i1],
        'valid': bars[i1:i2],
        'test': bars[i2:],
        'cuts': {'train_end': i1, 'valid_end': i2, 'n': n, 'mode': 'index_ratio'},
    }


def absolute_cut_ms(start_ms, end_ms, ratios):
    """Shared absolute train/valid cut timestamps for fair multi-series splits."""
    start_ms = int(start_ms)
    end_ms = int(end_ms)
    if end_ms <= start_ms:
        raise ValueError('对照窗口结束须晚于开始')
    train_r = float(ratios.get('train', 0.6))
    valid_r = float(ratios.get('valid', 0.2))
    test_r = float(ratios.get('test', 0.2))
    total = train_r + valid_r + test_r
    if abs(total - 1.0) > 1e-9:
        raise ValueError('train+valid+test 比例之和须为 1')
    if any(x < 0 for x in (train_r, valid_r, test_r)):
        raise ValueError('分割比例不可为负')
    span = end_ms - start_ms
    train_end = start_ms + int(span * train_r)
    valid_end = train_end + int(span * valid_r)
    return {
        'start_ms': start_ms,
        'end_ms': end_ms,
        'train_end_ms': train_end,
        'valid_end_ms': valid_end,
        'ratios': {'train': train_r, 'valid': valid_r, 'test': test_r},
        'mode': 'absolute_time',
    }


def split_by_cut_ms(bars, train_end_ms, valid_end_ms):
    """Split bars by absolute timestamps (fair across symbols)."""
    train, valid, test = [], [], []
    for bar in bars:
        if not isinstance(bar, (list, tuple)) or len(bar) < 1:
            continue
        t = int(bar[0])
        if t < int(train_end_ms):
            train.append(bar)
        elif t < int(valid_end_ms):
            valid.append(bar)
        else:
            test.append(bar)
    return {
        'train': train,
        'valid': valid,
        'test': test,
        'cuts': {
            'train_end_ms': int(train_end_ms),
            'valid_end_ms': int(valid_end_ms),
            'n': len(bars),
            'mode': 'absolute_time',
        },
    }


def crop_bars_to_ms(bars, start_ms, end_ms):
    """Crop OHLCV to [start_ms, end_ms]; drop outsiders with counts (no silent keep)."""
    if start_ms is None and end_ms is None:
        return list(bars or []), {
            'excluded_before': 0, 'excluded_after': 0,
            'kept': len(bars or []), 'cropped': False,
        }
    start_ms = int(start_ms) if start_ms is not None else None
    end_ms = int(end_ms) if end_ms is not None else None
    kept, before, after = [], 0, 0
    for bar in bars or []:
        if not isinstance(bar, (list, tuple)) or len(bar) < 1:
            continue
        tt = int(bar[0])
        if start_ms is not None and tt < start_ms:
            before += 1
            continue
        if end_ms is not None and tt > end_ms:
            after += 1
            continue
        kept.append(list(bar))
    return kept, {
        'excluded_before': before,
        'excluded_after': after,
        'kept': len(kept),
        'start_ms': start_ms,
        'end_ms': end_ms,
        'cropped': True,
    }


def empty_metrics():
    return {
        'trades': 0,
        'wins': 0,
        'losses': 0,
        'flats': 0,
        'data_gap_count': 0,
        'sample_count': 0,
        'win_rate': None,
        'win_rate_label': '无样本',
        'realized_pnl_net': 0.0,
        'realized_pnl_gross': 0.0,
        'fees_paid': 0.0,
        'excluded_bars': 0,
        'gap_skips': 0,
        'struct_signal_exclusions': 0,
    }


def finalize_metrics(m):
    samples = int(m['wins']) + int(m['losses']) + int(m['flats'])
    m['sample_count'] = samples
    if samples <= 0:
        m['win_rate'] = None
        m['win_rate_label'] = '无样本'
    else:
        m['win_rate'] = _round_u(m['wins'] / samples)
        m['win_rate_label'] = f'{m["win_rate"] * 100:.2f}%'
    m['realized_pnl_net'] = _round_u(m['realized_pnl_net'])
    m['realized_pnl_gross'] = _round_u(m['realized_pnl_gross'])
    m['fees_paid'] = _round_u(m['fees_paid'])
    return m


def walk_segment(bars, params, step_ms=None, struct_gate=None, interval='4h'):
    """Bar-walk one segment. Returns metrics + trade list + exclusion counts.

    STRUCT value filters evaluated as-of signal bar only (no future leak).
    Insufficient as-of → skip entry + exclusion count (never silent fill).
    """
    rule = params['entry_rule']
    side_default = ENTRY_RULES[rule]['side']
    side = params.get('side') or side_default
    tp_pct = float(params['tp_pct'])
    sl_pct = float(params['sl_pct'])
    max_hold = int(params['max_hold_bars'])
    cooldown = int(params.get('cooldown_bars') or 1)
    notional_u = float(params['notional_u'])
    fee_rate = float(params['fee_rate'])
    slip_rate = float(params['slip_rate'])

    metrics = empty_metrics()
    trades = []
    pos = None  # open position state
    cooldown_until = -1
    gap_skips = 0
    excluded = 0
    struct_signal_exclusions = 0
    struct_gate = struct_gate if isinstance(struct_gate, dict) else None
    struct_mode = (struct_gate or {}).get('mode') or 'off'

    # Detect sequence gaps if step known
    if step_ms and len(bars) >= 2:
        for i in range(1, len(bars)):
            dt = int(bars[i][0]) - int(bars[i - 1][0])
            if dt > step_ms * 1.5:
                # count missing expected bars roughly
                missing = int(dt // step_ms) - 1
                if missing > 0:
                    gap_skips += missing

    i = 0
    while i < len(bars):
        bar = bars[i]
        if not isinstance(bar, (list, tuple)) or len(bar) < 5:
            excluded += 1
            i += 1
            continue

        if pos is not None:
            # Evaluate exit on this bar (includes entry bar after open fill)
            bars_held = i - pos['entry_index']
            hit = resolve_exit_on_bar(pos['side'], pos['entry_price'], pos['tp'], pos['sl'], bar)
            force_max = bars_held >= max_hold
            if hit is None and force_max:
                hit = {
                    'exit_price': float(bar[4]),
                    'exit_reason': 'max_hold',
                    'fill_rule': 'max_hold_last_close',
                    'bar_time': int(bar[0]),
                }
            if hit is not None:
                raw_exit = float(hit['exit_price'])
                fill_exit = apply_slip(raw_exit, pos['side'], slip_rate, is_entry=False)
                qty = pos['qty']
                entry = pos['entry_price']
                if pos['side'] == 'long':
                    gross = (fill_exit - entry) * qty
                else:
                    gross = (entry - fill_exit) * qty
                exit_fee = fee_on(qty * fill_exit, fee_rate)
                net = gross - exit_fee - pos['entry_fee']
                if hit['exit_reason'] == 'data_gap':
                    outcome = 'data_gap'
                elif gross > 1e-12:
                    outcome = 'win'
                elif gross < -1e-12:
                    outcome = 'loss'
                else:
                    outcome = 'flat'
                trade = {
                    'trade_id': _new_id('tr'),
                    'side': pos['side'],
                    'entry_bar_time': pos['entry_bar_time'],
                    'exit_bar_time': hit['bar_time'],
                    'entry_price': _round_u(entry),
                    'exit_price': _round_u(fill_exit),
                    'raw_exit_price': _round_u(raw_exit),
                    'qty': _round_u(qty),
                    'realized_pnl_gross': _round_u(gross),
                    'entry_fee': _round_u(pos['entry_fee']),
                    'exit_fee': _round_u(exit_fee),
                    'realized_pnl_net': _round_u(net),
                    'exit_reason': hit['exit_reason'],
                    'fill_rule': hit['fill_rule'],
                    'outcome': outcome,
                    'segment': params.get('_segment'),
                }
                trades.append(trade)
                metrics['trades'] += 1
                metrics['fees_paid'] += pos['entry_fee'] + exit_fee
                metrics['realized_pnl_gross'] += gross
                metrics['realized_pnl_net'] += net
                if outcome == 'win':
                    metrics['wins'] += 1
                elif outcome == 'loss':
                    metrics['losses'] += 1
                elif outcome == 'flat':
                    metrics['flats'] += 1
                elif outcome == 'data_gap':
                    metrics['data_gap_count'] += 1
                pos = None
                cooldown_until = i + cooldown
            i += 1
            continue

        # Flat: look for signal on this bar → fill at next open
        if i < cooldown_until:
            i += 1
            continue
        if i + 1 >= len(bars):
            break
        if signal_entry(rule, bars, i):
            # STRUCT as-of decision bar i — never peeks bars after i
            if struct_gate and struct_mode in ('required', 'optional'):
                gate_now = evaluate_struct_gate_as_of(
                    bars, i, interval=interval, gate=struct_gate,
                )
                if struct_mode == 'required' and not gate_now.get('tradable', True):
                    struct_signal_exclusions += 1
                    i += 1
                    continue
            next_bar = bars[i + 1]
            if not isinstance(next_bar, (list, tuple)) or len(next_bar) < 5:
                excluded += 1
                i += 1
                continue
            raw_entry = float(next_bar[1])  # next open
            if raw_entry <= 0:
                excluded += 1
                i += 1
                continue
            fill_entry = apply_slip(raw_entry, side, slip_rate, is_entry=True)
            qty = notional_u / fill_entry
            entry_fee = fee_on(notional_u, fee_rate)
            if side == 'long':
                tp = fill_entry * (1 + tp_pct)
                sl = fill_entry * (1 - sl_pct)
            else:
                tp = fill_entry * (1 - tp_pct)
                sl = fill_entry * (1 + sl_pct)
            pos = {
                'side': side,
                'entry_index': i + 1,
                'entry_bar_time': int(next_bar[0]),
                'entry_price': fill_entry,
                'raw_entry_price': raw_entry,
                'qty': qty,
                'entry_fee': entry_fee,
                'tp': tp,
                'sl': sl,
            }
            # Stay on entry bar (i+1): evaluate same-bar high/low for exits
            # before advancing. Same-bar TP+SL → resolve_exit_on_bar conservative.
            i = i + 1
            continue
        i += 1

    # Open position at end of segment without exit bars → data_gap (not fake win)
    if pos is not None:
        metrics['trades'] += 1
        metrics['data_gap_count'] += 1
        trades.append({
            'trade_id': _new_id('tr'),
            'side': pos['side'],
            'entry_bar_time': pos['entry_bar_time'],
            'exit_bar_time': None,
            'entry_price': _round_u(pos['entry_price']),
            'exit_price': None,
            'qty': _round_u(pos['qty']),
            'realized_pnl_gross': None,
            'entry_fee': _round_u(pos['entry_fee']),
            'exit_fee': None,
            'realized_pnl_net': None,
            'exit_reason': 'data_gap',
            'fill_rule': 'missing_bars',
            'outcome': 'data_gap',
            'segment': params.get('_segment'),
            'note': '分段结束仍持仓且无更多可得K线 → data_gap，不计胜率',
        })
        metrics['fees_paid'] += pos['entry_fee']
        # entry fee already paid; no pnl counted

    metrics['excluded_bars'] = excluded
    metrics['gap_skips'] = gap_skips
    metrics['struct_signal_exclusions'] = struct_signal_exclusions
    return finalize_metrics(metrics), trades


class BacktestConflict(Exception):
    """Business conflict (duplicate run id, etc.)."""


class BacktestStore:
    """Historical bar-walk runs under runtime/backtests/."""

    def __init__(self, root, strategy_store=None, sim_store=None):
        self.root = Path(root)
        self.dir = self.root / 'runtime' / 'backtests'
        self.runs_dir = self.dir / 'runs'
        self.compares_dir = self.dir / 'compares'
        self.dir.mkdir(parents=True, exist_ok=True)
        self.runs_dir.mkdir(parents=True, exist_ok=True)
        self.compares_dir.mkdir(parents=True, exist_ok=True)
        self.index_path = self.dir / 'index.json'
        self.compare_index_path = self.dir / 'compare_index.json'
        self.strategy_store = strategy_store
        self.sim_store = sim_store
        self._lock = threading.RLock()
        self._ensure_bootstrapped()

    def _ensure_bootstrapped(self):
        with self._lock:
            if not self.index_path.exists():
                atomic(self.index_path, encoded({
                    'schema': 'backtest-index-v1',
                    'runs': [],
                    'updated_at': _utcnow(),
                    'disclaimer': DISCLAIMER,
                }))
            if not self.compare_index_path.exists():
                atomic(self.compare_index_path, encoded({
                    'schema': 'backtest-compare-index-v1',
                    'compares': [],
                    'updated_at': _utcnow(),
                    'disclaimer': DISCLAIMER,
                }))

    def _read_json(self, path):
        return json.loads(path.read_text(encoding='utf-8'))

    def _load_index(self):
        return self._read_json(self.index_path)

    @protected
    def _persist_run(self, record):
        """Write one run JSON + index row (used by early insufficient/unimplemented exits)."""
        run_id = record.get('run_id')
        if not run_id:
            raise ValueError('record 缺 run_id')
        with self._lock:
            index = self._load_index()
            existing_ids = {r.get('run_id') for r in index.get('runs') or []}
            if run_id in existing_ids or (self.runs_dir / f'{run_id}.json').exists():
                raise BacktestConflict(f'重复 run_id 已拒绝: {run_id}')
            metrics = record.get('metrics') or {}
            atomic(self.runs_dir / f'{run_id}.json', encoded(record))
            index.setdefault('runs', []).insert(0, {
                'run_id': run_id,
                'status': record.get('status'),
                'symbol': record.get('symbol'),
                'strategy_version_id': record.get('strategy_version_id'),
                'params_hash': record.get('params_hash'),
                'bars_count': (record.get('data_range') or {}).get('bars_count') or 0,
                'created_at': record.get('created_at'),
                'win_rate_label': metrics.get('win_rate_label') or '无样本',
                'trades': metrics.get('trades') or 0,
                'execution_mode': record.get('execution_mode'),
                'backtest_scope': record.get('backtest_scope'),
                'not_full_strategy_pnl': record.get('not_full_strategy_pnl'),
            })
            self._save_index(index)
        return record

    def _save_index(self, index):
        index['updated_at'] = _utcnow()
        atomic(self.index_path, encoded(index))

    def catalog(self):
        return {
            'schema': 'backtest-catalog-v1',
            'slice': 'BACKTEST-01-compare-knife',
            'fair_compare': {
                'ready': True,
                'api': 'POST /api/backtest/compare',
                'max_symbols': 10,
                'max_versions': 5,
                'absolute_time_splits': True,
                'universe_rebuild': False,
                'note': '多合约×多版本批次对照；历史上市宇宙重建仍为缺口',
            },
            'entry_rules': ENTRY_RULES,
            'ambiguity_rules': AMBIGUITY_RULES,
            'coverage_warnings': COVERAGE_WARNINGS,
            'default_splits': DEFAULT_SPLITS,
            'min_bars_default': MIN_BARS_DEFAULT,
            'lookahead_guard': True,
            'fair_window_crop': True,
            'struct_as_of_entry': True,
            'struct_hook': {
                'ready': True,
                'filter_gate': True,
                'modes': ['off', 'optional', 'required'],
                'note': (
                    'STRUCT 过滤闸已挂接 run/compare；'
                    'required 失败 → excluded_struct/insufficient/unimplemented；'
                    '不宣称可信ATH；非完整结构策略引擎。'
                ),
            },
            'disclaimer': DISCLAIMER,
            'forbidden': [
                '不宣称全市场/全历史宇宙回测',
                '不回填当今市值',
                '不用未来K线入场',
                '不联网批量拉盘',
                '不付费AI',
                '无样本不填胜率0%',
            ],
        }

    def rules(self):
        return self.catalog()

    def _sim_fees(self):
        fee_rate = 0.0005
        slip_rate = 0.0
        if self.sim_store is not None:
            try:
                cfg = self.sim_store.get_config()
                fee_rate = float(cfg.get('fee_rate') or fee_rate)
                slip_rate = float(cfg.get('slip_rate') or slip_rate)
            except Exception:
                pass
        return fee_rate, slip_rate

    def _load_bars(self, market, symbol, interval, start, end, client_bars):
        if client_bars is not None:
            if not isinstance(client_bars, list):
                raise ValueError('bars 须为数组')
            clean = []
            for bar in client_bars:
                if isinstance(bar, (list, tuple)) and len(bar) >= 5:
                    clean.append(list(bar))
            meta = {
                'bars_source': 'client',
                'conflicts': 0,
                'conflicted_excluded': 0,
                'gap_count': 0,
                'coveragePct': None,
                'current_available': bool(clean),
                'market_cap_used': False,
                'note': '合成/客户端K线（离线测试或显式传入）；非全市场',
            }
            if start or end:
                s_ms = _ms(_parse_iso(start)) if start else None
                e_ms = _ms(_parse_iso(end)) if end else None
                clean, crop_meta = crop_bars_to_ms(clean, s_ms, e_ms)
                meta['window_crop'] = crop_meta
                meta['current_available'] = bool(clean)
                meta['note'] = (
                    meta['note']
                    + f'；已裁剪至请求窗 before={crop_meta["excluded_before"]} '
                    + f'after={crop_meta["excluded_after"]}'
                )
            return clean, meta

        try:
            from price_index import merge_series, STEPS
        except ImportError as exc:
            raise ValueError('price_index 不可用，无法加载本地合并K线') from exc

        detail = merge_series(
            self.root, market, symbol, interval,
            start=start, end=end, include_bars=True,
        )
        bars = detail.get('bars') or []
        meta = {
            'bars_source': 'price_index',
            'conflicts': int(detail.get('conflicts') or 0),
            'conflicted_excluded': int(detail.get('conflicts') or 0),
            'gap_count': int((detail.get('continuity') or {}).get('gap_count')
                             or detail.get('gap_count') or 0),
            'coveragePct': detail.get('coveragePct'),
            'missing': detail.get('missing'),
            'window': detail.get('window'),
            'current_available': bool(detail.get('current_available')),
            'historical_availability': detail.get('historical_availability'),
            'evidence_count': len(detail.get('evidence') or []),
            'market_cap_used': False,
            'label_current': detail.get('label_current'),
            'scope': detail.get('scope'),
            'step_ms': STEPS.get(interval),
        }
        return bars, meta


    def _apply_struct_gate(self, bars, interval, body, version_rules):
        """Cell preflight STRUCT: warmup/implementability only (no full-window min_adx).

        Value thresholds apply as-of each entry inside walk_segment.
        """
        gate_cfg = resolve_struct_gate_config(body, version_rules)
        gate_result = evaluate_struct_gate(
            bars, interval=interval, gate=gate_cfg, value_thresholds=False,
        )
        gate_result = dict(gate_result)
        gate_result['config'] = {
            'mode': gate_cfg.get('mode'),
            'require': list(gate_cfg.get('require') or []),
            'min_adx': gate_cfg.get('min_adx'),
            'source': gate_cfg.get('source'),
            'params_complete': gate_cfg.get('params_complete'),
            'value_thresholds_at_cell': False,
            'value_thresholds_as_of_entry': True,
        }
        prev = gate_result.get('note') or ''
        gate_result['note'] = (
            (prev + '；' if prev else '')
            + '单元格仅预检预热/可实现性，min_adx 等在入场 as-of 判定'
        )
        return gate_cfg, gate_result

    def _struct_blocked_record(
        self, *, run_id, svid, version_meta, symbol, market, interval,
        params_for_hash, params_hash, start, end, bars, bars_meta,
        gate_result, version_rules=None,
    ):
        """Persist a non-tradable cell blocked by STRUCT (explicit, not silent)."""
        status = gate_result.get('status') or 'excluded_struct'
        if status not in ('insufficient', 'unimplemented', 'excluded_struct'):
            status = 'excluded_struct'
        excl_list = gate_result.get('exclusions') or []
        reasons = [e.get('reason') or e.get('code') for e in excl_list]
        record = {
            'run_id': run_id,
            'schema': 'backtest-run-v1',
            'status': status,
            'status_note': (
                'STRUCT 过滤未通过，单元格不可交易：' + '；'.join(str(r) for r in reasons[:5])
            )[:500],
            'tradable': False,
            'strategy_version_id': svid,
            'strategy_title': (version_meta or {}).get('title'),
            'strategy_enabled': (version_meta or {}).get('enabled'),
            'symbol': symbol,
            'market': market,
            'interval': interval,
            'params': params_for_hash,
            'params_hash': params_hash,
            'data_range': {
                'start': start,
                'end': end,
                'bars_count': len(bars),
                'first_bar_time': int(bars[0][0]) if bars else None,
                'last_bar_time': int(bars[-1][0]) if bars else None,
            },
            'bars_meta': bars_meta,
            'exclusions': {
                'conflicted_bars': bars_meta.get('conflicted_excluded', 0),
                'gap_count': bars_meta.get('gap_count', 0),
                'excluded_malformed': 0,
                'insufficient': status == 'insufficient',
                'struct_blocked': True,
                'struct_exclusions': excl_list,
            },
            'struct_gate': gate_result,
            'struct_filter_applied': True,
            'coverage_warnings': COVERAGE_WARNINGS,
            'metrics': finalize_metrics(empty_metrics()),
            'headline_metrics': finalize_metrics(empty_metrics()),
            'segments': {},
            'trades': [],
            'trades_total': 0,
            'ambiguity_rules': AMBIGUITY_RULES,
            'created_at': _utcnow(),
            'disclaimer': DISCLAIMER,
        }
        return record

    def run(self, body):
        """Execute one backtest and persist the run record."""
        if not isinstance(body, dict):
            raise ValueError('请求体须为对象')

        svid = body.get('strategy_version_id')
        if not svid or not isinstance(svid, str) or not svid.startswith('ver-'):
            raise ValueError('须提供已冻结的 strategy_version_id（ver-…）')
        symbol = str(body.get('symbol') or '').strip().upper()
        if not symbol:
            raise ValueError('须指定 symbol')
        market = body.get('market') or 'USDⓈ-M'
        interval = body.get('interval') or '4h'
        if interval not in ('4h', '1d'):
            raise ValueError('interval 仅支持 4h / 1d')

        version_meta = None
        version_hash = None
        version_rules = {}
        if self.strategy_store is not None:
            try:
                version_meta = self.strategy_store.get_version(svid)
            except KeyError as exc:
                raise ValueError(f'冻结版本不存在: {svid}') from exc
            # Allow run even if not enabled (historical research), but note it
            version_hash = version_meta.get('content_hash')
            version_rules = version_meta.get('rules') or {}
        elif body.get('version_stub'):
            version_meta = body['version_stub']
            version_hash = version_meta.get('content_hash') or 'stub'
            version_rules = version_meta.get('rules') or {}
        else:
            raise ValueError('未接入 strategy_store，且未提供 version_stub（仅测试）')

        # V1 + F04: exec params only from frozen rules.params — no silent defaults / breakout stand-in
        from frozen_exec import require_frozen_exec_params, FrozenExecConflict
        allow_generic = bool(body.get('allow_generic_price_rule') or body.get('experiment_kind') == 'generic_price_rule')
        # Optional generic-price experiment: inject entry_rule onto a stub copy only when explicitly opted in
        version_for_exec = version_meta
        if allow_generic and body.get('entry_rule') and not ((version_rules.get('params') or {}).get('entry_rule')):
            # Explicit independent experiment — still must supply full params in body matching stub
            stub = dict(version_meta or {})
            stub_rules = dict(version_rules or {})
            stub_params = dict(stub_rules.get('params') or {})
            stub_params['entry_rule'] = body['entry_rule']
            for k in ('tp_pct', 'sl_pct', 'max_hold_bars', 'notional_u', 'cooldown_bars', 'side'):
                if body.get(k) is not None and stub_params.get(k) is None:
                    stub_params[k] = body[k]
            stub_rules['params'] = stub_params
            stub['rules'] = stub_rules
            version_for_exec = stub
        try:
            frozen = require_frozen_exec_params(version_for_exec, body, context='backtest')
        except FrozenExecConflict as exc:
            raise ValueError(str(exc)) from exc

        exec_mode = frozen.get('execution_mode') or 'pct_touch'
        backtest_scope = _normalize_backtest_scope(body, frozen)

        # --- EMA99 mode: never fall through into old pct_touch entry/tp/sl path ---
        if exec_mode == 'ema99_close_trail':
            run_id = body.get('run_id') or _new_id('bt')
            if not isinstance(run_id, str) or not run_id.startswith('bt-'):
                raise ValueError('run_id 须以 bt- 开头')
            gaps = _historical_evidence_gaps(body, version_meta)
            base_rec = {
                'run_id': run_id,
                'schema': 'backtest-run-v1',
                'strategy_version_id': svid,
                'version_content_hash': version_hash,
                'symbol': symbol,
                'market': market,
                'interval': interval,
                'execution_mode': 'ema99_close_trail',
                'backtest_scope': backtest_scope,
                'frozen_exec': {
                    'execution_mode': 'ema99_close_trail',
                    'entry_rule': frozen.get('entry_rule'),
                    'initial_stop': frozen.get('initial_stop'),
                    'trail_activation': frozen.get('trail_activation'),
                    'candidate_max_days': frozen.get('candidate_max_days'),
                    'tp_pct': None,
                    'sl_pct': None,
                    'max_hold_bars': None,
                },
                'entry_rule': frozen.get('entry_rule'),
                'created_at': _utcnow(),
                'disclaimer': DISCLAIMER,
            }
            if backtest_scope == 'full_strategy':
                note = (
                    '完整策略回测（ema99_close_trail）缺历史证据: '
                    + (', '.join(gaps) if gaps else '引擎路径未完备')
                    + '；拒绝用旧突破/固定tp/sl顶替，亦不以技术切片收益冒充全策略验收'
                )
                rec = {
                    **base_rec,
                    'status': 'insufficient',
                    'status_note': note,
                    'not_full_strategy_pnl': True,
                    'missing_evidence': gaps,
                    'metrics': {'trades': 0, 'sample_count': 0, 'win_rate_label': '无样本'},
                }
                self._persist_run(rec)
                return rec

            # Named tech slice — separate label; never claim as full-strategy PnL acceptance
            slice_note = (
                'EMA技术切片（ema_tech_slice）：仅名义技术路径；'
                '非完整策略收益验收；缺结构确认/OI/市值/账户时不得宣称全策略 PnL'
            )
            status = 'unimplemented' if frozen.get('entry_rule') not in ENTRY_RULES else 'insufficient'
            extra = (
                f'；entry_rule={frozen.get("entry_rule")!r} 尚未接入通用回测撮合'
                if status == 'unimplemented'
                else '；本刀不跑旧固定触价引擎'
            )
            rec = {
                **base_rec,
                'status': status,
                'status_note': slice_note + extra,
                'not_full_strategy_pnl': True,
                'experiment_kind': 'ema_tech_slice',
                'metrics': {'trades': 0, 'sample_count': 0, 'win_rate_label': '无样本'},
            }
            self._persist_run(rec)
            return rec

        entry_rule = frozen['entry_rule']
        if entry_rule not in ENTRY_RULES:
            raise ValueError(
                f'entry_rule={entry_rule!r} 不在已实现集合 {list(ENTRY_RULES)}；'
                '不支持的策略 → insufficient/unimplemented'
            )
        tp_pct = frozen['tp_pct']
        sl_pct = frozen['sl_pct']
        max_hold_bars = frozen['max_hold_bars']
        if tp_pct is None or sl_pct is None or max_hold_bars is None:
            raise ValueError(
                'pct_touch 回测冻结包缺少 tp_pct/sl_pct/max_hold_bars；'
                '拒绝用页面默认 5%/3%/42 顶替'
            )
        cooldown_bars = frozen['cooldown_bars'] if frozen.get('cooldown_bars') is not None else 1
        if cooldown_bars < 0 or cooldown_bars > 500:
            raise ValueError('cooldown_bars 须在 0–500')
        notional_u = frozen['notional_u'] if frozen.get('notional_u') is not None else body.get('notional_u')
        if notional_u is None:
            raise ValueError('冻结版本未声明 notional_u 且请求未提供；拒绝发明默认 100U')
        notional_u = _finite('notional_u', notional_u)
        if notional_u <= 0:
            raise ValueError('notional_u 须 > 0')

        fee_default, slip_default = self._sim_fees()
        fee_rate = _finite('fee_rate', body.get('fee_rate', fee_default))
        slip_rate = _finite('slip_rate', body.get('slip_rate', slip_default))
        if fee_rate < 0 or fee_rate > 0.05:
            raise ValueError('fee_rate 须在 0–0.05')
        if slip_rate < 0 or slip_rate > 0.05:
            raise ValueError('slip_rate 须在 0–0.05')

        # Reject any market-cap fields silently sneaking in
        for banned in ('market_cap', 'capUsd', 'circulating_mcap', 'today_cap'):
            if banned in body and body[banned] is not None:
                raise ValueError(f'禁止传入 {banned}：回测不得回填当今/任意市值')

        do_split = body.get('split', True)
        if isinstance(do_split, str):
            do_split = do_split.lower() not in ('0', 'false', 'no')
        ratios = body.get('splits') or dict(DEFAULT_SPLITS)
        if not isinstance(ratios, dict):
            raise ValueError('splits 须为对象')

        min_bars = body.get('min_bars')
        if min_bars is None:
            min_bars = MIN_BARS_DEFAULT.get(interval, 30)
        min_bars = int(_finite('min_bars', min_bars))
        if min_bars < 5:
            raise ValueError('min_bars 过小（<5）')

        start = body.get('start')
        end = body.get('end')
        if start:
            _parse_iso(start)
        if end:
            _parse_iso(end)

        side = frozen.get('side') or body.get('side')  # frozen side wins; else entry-rule default

        bars, bars_meta = self._load_bars(
            market, symbol, interval, start, end, body.get('bars'),
        )
        fair_window = body.get('fair_window_ms') if isinstance(body.get('fair_window_ms'), dict) else None
        if fair_window and fair_window.get('start_ms') is not None and fair_window.get('end_ms') is not None:
            bars, crop_meta = crop_bars_to_ms(
                bars, fair_window['start_ms'], fair_window['end_ms'],
            )
            bars_meta = dict(bars_meta or {})
            bars_meta['fair_window_crop'] = crop_meta
            bars_meta['market_cap_used'] = False

        run_id = body.get('run_id') or _new_id('bt')
        if not isinstance(run_id, str) or not run_id.startswith('bt-'):
            raise ValueError('run_id 须以 bt- 开头')

        params_for_hash = {
            'strategy_version_id': svid,
            'version_content_hash': version_hash,
            'symbol': symbol,
            'market': market,
            'interval': interval,
            'start': start,
            'end': end,
            'entry_rule': entry_rule,
            'side': side or ENTRY_RULES[entry_rule]['side'],
            'tp_pct': tp_pct,
            'sl_pct': sl_pct,
            'max_hold_bars': max_hold_bars,
            'cooldown_bars': cooldown_bars,
            'notional_u': notional_u,
            'fee_rate': fee_rate,
            'slip_rate': slip_rate,
            'split': bool(do_split),
            'splits': ratios if do_split else None,
            'min_bars': min_bars,
            'market_cap_used': False,
            'absolute_cuts': body.get('absolute_cuts') if isinstance(body.get('absolute_cuts'), dict) else None,
            'compare_id': body.get('compare_id'),
        }
        params_hash = _sha256_obj(params_for_hash)

        # STRUCT-01 filter gate (optional/required); missing params → unimplemented
        gate_cfg, gate_result = self._apply_struct_gate(
            bars, interval, body, version_rules,
        )
        params_for_hash = dict(params_for_hash)
        params_for_hash['struct_filter'] = gate_result.get('config')
        params_hash = _sha256_obj(params_for_hash)

        base_params = {
            'entry_rule': entry_rule,
            'side': side or ENTRY_RULES[entry_rule]['side'],
            'tp_pct': tp_pct,
            'sl_pct': sl_pct,
            'max_hold_bars': max_hold_bars,
            'cooldown_bars': cooldown_bars,
            'notional_u': notional_u,
            'fee_rate': fee_rate,
            'slip_rate': slip_rate,
        }

        step_ms = bars_meta.get('step_ms')
        if step_ms is None:
            step_ms = 4 * 3600 * 1000 if interval == '4h' else 24 * 3600 * 1000

        with self._lock:
            index = self._load_index()
            existing_ids = {r.get('run_id') for r in index.get('runs') or []}
            if run_id in existing_ids or (self.runs_dir / f'{run_id}.json').exists():
                raise BacktestConflict(f'重复 run_id 已拒绝: {run_id}')

            if len(bars) < min_bars:
                record = {
                    'run_id': run_id,
                    'schema': 'backtest-run-v1',
                    'status': 'insufficient',
                    'status_note': (
                        f'可得K线 {len(bars)} 根 < 最小要求 {min_bars}；'
                        '拒绝用不足样本编造成绩。'
                    ),
                    'strategy_version_id': svid,
                    'strategy_title': (version_meta or {}).get('title'),
                    'strategy_enabled': (version_meta or {}).get('enabled'),
                    'symbol': symbol,
                    'market': market,
                    'interval': interval,
                    'params': params_for_hash,
                    'params_hash': params_hash,
                    'data_range': {
                        'start': start,
                        'end': end,
                        'bars_count': len(bars),
                        'first_bar_time': int(bars[0][0]) if bars else None,
                        'last_bar_time': int(bars[-1][0]) if bars else None,
                    },
                    'bars_meta': bars_meta,
                    'exclusions': {
                        'conflicted_bars': bars_meta.get('conflicted_excluded', 0),
                        'gap_count': bars_meta.get('gap_count', 0),
                        'excluded_malformed': 0,
                        'insufficient': True,
                    },
                    'coverage_warnings': COVERAGE_WARNINGS,
                    'metrics': finalize_metrics(empty_metrics()),
                    'segments': {},
                    'trades': [],
                    'ambiguity_rules': AMBIGUITY_RULES,
                    'struct_filter_applied': False,
                    'created_at': _utcnow(),
                    'disclaimer': DISCLAIMER,
                }
                atomic(self.runs_dir / f'{run_id}.json', encoded(record))
                index.setdefault('runs', []).insert(0, {
                    'run_id': run_id,
                    'status': 'insufficient',
                    'symbol': symbol,
                    'strategy_version_id': svid,
                    'params_hash': params_hash,
                    'bars_count': len(bars),
                    'created_at': record['created_at'],
                    'win_rate_label': '无样本',
                    'trades': 0,
                })
                self._save_index(index)
                return record

            # STRUCT required gate — block before walk (explicit exclusion, not silent)
            if gate_cfg.get('mode') == 'required' and not gate_result.get('tradable', True):
                record = self._struct_blocked_record(
                    run_id=run_id,
                    svid=svid,
                    version_meta=version_meta,
                    symbol=symbol,
                    market=market,
                    interval=interval,
                    params_for_hash=params_for_hash,
                    params_hash=params_hash,
                    start=start,
                    end=end,
                    bars=bars,
                    bars_meta=bars_meta,
                    gate_result=gate_result,
                )
                atomic(self.runs_dir / f'{run_id}.json', encoded(record))
                index.setdefault('runs', []).insert(0, {
                    'run_id': run_id,
                    'status': record['status'],
                    'symbol': symbol,
                    'strategy_version_id': svid,
                    'params_hash': params_hash,
                    'bars_count': len(bars),
                    'created_at': record['created_at'],
                    'win_rate_label': '无样本',
                    'trades': 0,
                    'struct_blocked': True,
                })
                index['runs'] = index['runs'][:200]
                self._save_index(index)
                return record

            segments_out = {}
            all_trades = []
            overall = empty_metrics()

            if do_split:
                abs_cuts = body.get('absolute_cuts')
                if isinstance(abs_cuts, dict) and abs_cuts.get('train_end_ms') is not None:
                    parts = split_by_cut_ms(
                        bars,
                        abs_cuts['train_end_ms'],
                        abs_cuts.get('valid_end_ms') or abs_cuts['train_end_ms'],
                    )
                else:
                    parts = split_by_time(bars, ratios)
                # Each non-empty segment must still be long enough to be meaningful;
                # if any requested segment is empty → note it, don't invent fills.
                for name in ('train', 'valid', 'test'):
                    seg_bars = parts[name]
                    p = dict(base_params)
                    p['_segment'] = name
                    if len(seg_bars) < max(5, min_bars // 5):
                        segments_out[name] = {
                            'status': 'insufficient',
                            'bars_count': len(seg_bars),
                            'metrics': finalize_metrics(empty_metrics()),
                            'trades': [],
                            'note': f'{name} 分段过短（{len(seg_bars)} 根），跳过行走',
                        }
                        continue
                    m, tr = walk_segment(
                        seg_bars, p, step_ms=step_ms,
                        struct_gate=gate_cfg, interval=interval,
                    )
                    segments_out[name] = {
                        'status': 'ok',
                        'bars_count': len(seg_bars),
                        'metrics': m,
                        'trades_count': len(tr),
                        # Keep trade ids in segment summary; full list at top level
                        'trade_ids': [t['trade_id'] for t in tr],
                    }
                    all_trades.extend(tr)
                    overall['trades'] += m['trades']
                    overall['wins'] += m['wins']
                    overall['losses'] += m['losses']
                    overall['flats'] += m['flats']
                    overall['data_gap_count'] += m['data_gap_count']
                    overall['realized_pnl_net'] += m['realized_pnl_net']
                    overall['realized_pnl_gross'] += m['realized_pnl_gross']
                    overall['fees_paid'] += m['fees_paid']
                    overall['excluded_bars'] += m['excluded_bars']
                    overall['gap_skips'] += m['gap_skips']
                segments_out['_cuts'] = parts['cuts']
            else:
                p = dict(base_params)
                p['_segment'] = 'full'
                m, tr = walk_segment(
                    bars, p, step_ms=step_ms,
                    struct_gate=gate_cfg, interval=interval,
                )
                segments_out['full'] = {
                    'status': 'ok',
                    'bars_count': len(bars),
                    'metrics': m,
                    'trades_count': len(tr),
                    'trade_ids': [t['trade_id'] for t in tr],
                }
                all_trades = tr
                overall = m

            finalize_metrics(overall)
            # Prefer test-segment metrics headline when split, else overall
            headline = overall
            if do_split and segments_out.get('test', {}).get('status') == 'ok':
                headline_note = 'headline=test（训练/验证仅供对照，不把验证当终局）'
            else:
                headline_note = 'headline=overall'

            record = {
                'run_id': run_id,
                'schema': 'backtest-run-v1',
                'status': 'completed',
                'status_note': headline_note,
                'strategy_version_id': svid,
                'strategy_title': (version_meta or {}).get('title'),
                'strategy_enabled': (version_meta or {}).get('enabled'),
                'symbol': symbol,
                'market': market,
                'interval': interval,
                'params': params_for_hash,
                'params_hash': params_hash,
                'data_range': {
                    'start': start,
                    'end': end,
                    'bars_count': len(bars),
                    'first_bar_time': int(bars[0][0]) if bars else None,
                    'last_bar_time': int(bars[-1][0]) if bars else None,
                },
                'bars_meta': bars_meta,
                'exclusions': {
                    'conflicted_bars': bars_meta.get('conflicted_excluded', 0),
                    'gap_count': bars_meta.get('gap_count', 0),
                    'excluded_malformed': overall.get('excluded_bars', 0),
                    'gap_skips_estimated': overall.get('gap_skips', 0),
                    'data_gap_trades': overall.get('data_gap_count', 0),
                    'insufficient': False,
                },
                'coverage_warnings': COVERAGE_WARNINGS,
                'metrics': overall,
                'headline_metrics': (
                    segments_out['test']['metrics']
                    if do_split and segments_out.get('test', {}).get('status') == 'ok'
                    else overall
                ),
                'segments': {
                    k: {kk: vv for kk, vv in seg.items() if kk != 'trades'}
                    if isinstance(seg, dict) else seg
                    for k, seg in segments_out.items()
                },
                'trades': all_trades[:500],
                'trades_truncated': len(all_trades) > 500,
                'trades_total': len(all_trades),
                'ambiguity_rules': AMBIGUITY_RULES,
                'struct_filter_applied': bool(gate_result.get('applied')),
                'struct_gate': gate_result,
                'tradable': True,
                'created_at': _utcnow(),
                'disclaimer': DISCLAIMER,
            }
            atomic(self.runs_dir / f'{run_id}.json', encoded(record))
            index.setdefault('runs', []).insert(0, {
                'run_id': run_id,
                'status': 'completed',
                'symbol': symbol,
                'strategy_version_id': svid,
                'params_hash': params_hash,
                'bars_count': len(bars),
                'created_at': record['created_at'],
                'win_rate_label': overall.get('win_rate_label'),
                'trades': overall.get('trades'),
                'realized_pnl_net': overall.get('realized_pnl_net'),
            })
            # Keep index bounded
            index['runs'] = index['runs'][:200]
            self._save_index(index)
            return record


    def _load_compare_index(self):
        return self._read_json(self.compare_index_path)

    def _save_compare_index(self, index):
        index['updated_at'] = _utcnow()
        atomic(self.compare_index_path, encoded(index))

    def _cell_summary(self, rec):
        """Compact matrix cell from a run record."""
        m = rec.get('headline_metrics') or rec.get('metrics') or {}
        excl = rec.get('exclusions') or {}
        return {
            'run_id': rec.get('run_id'),
            'status': rec.get('status'),
            'status_note': rec.get('status_note'),
            'strategy_version_id': rec.get('strategy_version_id'),
            'strategy_title': rec.get('strategy_title'),
            'symbol': rec.get('symbol'),
            'market': rec.get('market'),
            'bars_count': (rec.get('data_range') or {}).get('bars_count'),
            'params_hash': rec.get('params_hash'),
            'trades': m.get('trades'),
            'wins': m.get('wins'),
            'losses': m.get('losses'),
            'win_rate_label': m.get('win_rate_label'),
            'realized_pnl_net': m.get('realized_pnl_net'),
            'data_gap_count': m.get('data_gap_count'),
            'exclusions': {
                'conflicted_bars': excl.get('conflicted_bars', 0),
                'gap_count': excl.get('gap_count', 0),
                'excluded_malformed': excl.get('excluded_malformed', 0),
                'data_gap_trades': excl.get('data_gap_trades', 0),
                'insufficient': bool(excl.get('insufficient') or rec.get('status') == 'insufficient'),
                'struct_blocked': bool(excl.get('struct_blocked')),
                'struct_exclusions': excl.get('struct_exclusions') or (rec.get('struct_gate') or {}).get('exclusions') or [],
            },
            'tradable': rec.get('tradable', rec.get('status') == 'completed'),
            'struct_gate_status': (rec.get('struct_gate') or {}).get('status'),
            'reject_reason': rec.get('reject_reason') or (
                rec.get('status_note') if rec.get('status') in ('excluded_struct', 'unimplemented', 'insufficient') and excl.get('struct_blocked') else None
            ),
        }

    def compare(self, body):
        """Fair multi-symbol × multi-version batch under shared economics.

        Shared across cells: train/val/test ratios via absolute time cuts, fee/slip,
        ambiguity (EVAL), entry params. Per-cell: bars coverage / insufficient / exclusions.
        Does NOT rebuild historical listed universe — coverage gap stays explicit.
        """
        if not isinstance(body, dict):
            raise ValueError('请求体须为对象')

        # Symbols
        symbols = body.get('symbols')
        if symbols is None and body.get('symbol'):
            symbols = [body.get('symbol')]
        if isinstance(symbols, str):
            symbols = [symbols]
        if not isinstance(symbols, list) or not symbols:
            raise ValueError('须提供 symbols 列表（至少 1 个）')
        cleaned_syms = []
        for s in symbols:
            if not isinstance(s, str) or not s.strip():
                raise ValueError('symbol 须为非空字符串')
            cleaned_syms.append(s.strip().upper())
        if len(cleaned_syms) > 10:
            raise ValueError('单次对照最多 10 个合约（首刀）')
        cleaned_syms = list(dict.fromkeys(cleaned_syms))

        # Versions
        versions = body.get('strategy_version_ids')
        if versions is None and body.get('strategy_version_id'):
            versions = [body.get('strategy_version_id')]
        if isinstance(versions, str):
            versions = [versions]
        if not isinstance(versions, list) or not versions:
            raise ValueError('须提供 strategy_version_ids 列表（至少 1 个冻结版本）')
        cleaned_vers = []
        for v in versions:
            if not isinstance(v, str) or not v.startswith('ver-'):
                raise ValueError('strategy_version_id 须为 ver-…')
            cleaned_vers.append(v)
        if len(cleaned_vers) > 5:
            raise ValueError('单次对照最多 5 个冻结版本（首刀）')
        cleaned_vers = list(dict.fromkeys(cleaned_vers))

        market = body.get('market') or 'USDⓈ-M'
        interval = body.get('interval') or '4h'
        if interval not in ('4h', '1d'):
            raise ValueError('interval 仅支持 4h / 1d')

        # Reject mcap on compare too
        for banned in ('market_cap', 'capUsd', 'circulating_mcap', 'today_cap'):
            if banned in body and body[banned] is not None:
                raise ValueError(f'禁止传入 {banned}：回测不得回填当今/任意市值')

        do_split = body.get('split', True)
        if isinstance(do_split, str):
            do_split = do_split.lower() not in ('0', 'false', 'no')
        ratios = body.get('splits') or dict(DEFAULT_SPLITS)
        if not isinstance(ratios, dict):
            raise ValueError('splits 须为对象')

        start = body.get('start')
        end = body.get('end')
        client_bars_map = body.get('bars_by_symbol') if isinstance(body.get('bars_by_symbol'), dict) else {}
        # Fallback: single bars only when one symbol
        if body.get('bars') is not None and len(cleaned_syms) == 1:
            client_bars_map.setdefault(cleaned_syms[0], body.get('bars'))

        # Probe ranges per symbol to build intersection window for absolute cuts
        range_notes = []
        firsts, lasts = [], []
        for sym in cleaned_syms:
            bars, meta = self._load_bars(
                market, sym, interval, start, end, client_bars_map.get(sym),
            )
            if bars:
                firsts.append(int(bars[0][0]))
                lasts.append(int(bars[-1][0]))
                range_notes.append({
                    'symbol': sym,
                    'bars_count': len(bars),
                    'first_bar_time': int(bars[0][0]),
                    'last_bar_time': int(bars[-1][0]),
                    'gaps': meta.get('gap_count', 0),
                    'conflicted_excluded': meta.get('conflicted_excluded', 0),
                })
            else:
                range_notes.append({
                    'symbol': sym,
                    'bars_count': 0,
                    'first_bar_time': None,
                    'last_bar_time': None,
                    'gaps': meta.get('gap_count', 0),
                    'conflicted_excluded': meta.get('conflicted_excluded', 0),
                    'note': '无可得K线',
                })

        absolute_cuts = None
        window_mode = None
        coverage_gaps = [
            {
                'id': 'historical_listed_universe_rebuild',
                'title': '历史上市宇宙未重建',
                'note': (
                    '本刀只对请求矩阵做公平对照，不重建当时全市场上市/退市名单；'
                    '生存者偏差警告仍适用。'
                ),
            }
        ]

        if do_split:
            if start and end:
                start_ms = _ms(_parse_iso(start))
                end_ms = _ms(_parse_iso(end))
                window_mode = 'request_window'
            elif firsts and lasts:
                # Intersection of available series — fair overlapping window
                start_ms = max(firsts)
                end_ms = min(lasts)
                window_mode = 'intersection'
                if end_ms <= start_ms:
                    coverage_gaps.append({
                        'id': 'no_overlapping_window',
                        'title': '无重叠时间窗',
                        'note': '各合约K线无交集；绝对切分无法建立，各格将 likely insufficient',
                    })
                    # Fall back to union so cuts still exist; cells may be insufficient
                    start_ms = min(firsts)
                    end_ms = max(lasts)
                    window_mode = 'union_fallback'
            else:
                raise ValueError('对照批次无任何合约可得K线；拒绝空矩阵')
            if end_ms > start_ms:
                absolute_cuts = absolute_cut_ms(start_ms, end_ms, ratios)
            else:
                raise ValueError('对照时间窗无效（结束≤开始）')
        elif firsts and lasts:
            # Even without split: crop all cells to intersection (fair window, no extra history)
            start_ms = max(firsts)
            end_ms = min(lasts)
            window_mode = 'intersection'
            if end_ms <= start_ms:
                start_ms = min(firsts)
                end_ms = max(lasts)
                window_mode = 'union_fallback'
            absolute_cuts = {
                'start_ms': start_ms,
                'end_ms': end_ms,
                'train_end_ms': start_ms,
                'valid_end_ms': start_ms,
                'ratios': None,
                'mode': 'window_only',
            }

        window_crop_notes = []
        compare_id = body.get('compare_id') or _new_id('btc')
        if not isinstance(compare_id, str) or not compare_id.startswith('btc-'):
            raise ValueError('compare_id 须以 btc- 开头')

        shared = {
            'market': market,
            'interval': interval,
            'start': start,
            'end': end,
            'split': bool(do_split),
            'splits': ratios if do_split else None,
            'absolute_cuts': absolute_cuts,
            'window_crop_notes': window_crop_notes,
            'lookahead_guard': True,
            'window_mode': window_mode,
            'tp_pct': body.get('tp_pct'),
            'sl_pct': body.get('sl_pct'),
            'max_hold_bars': body.get('max_hold_bars'),
            'cooldown_bars': body.get('cooldown_bars'),
            'notional_u': body.get('notional_u'),
            'fee_rate': body.get('fee_rate'),
            'slip_rate': body.get('slip_rate'),
            'min_bars': body.get('min_bars'),
            'entry_rule': body.get('entry_rule'),
            'allow_generic_price_rule': body.get('allow_generic_price_rule'),
            'experiment_kind': body.get('experiment_kind'),
            'backtest_scope': body.get('backtest_scope'),
            'side': body.get('side'),
            'version_stub': body.get('version_stub'),  # tests only
            'compare_id': compare_id,
            'market_cap_used': False,
            'struct_filter': body.get('struct_filter'),
        }
        # Drop Nones so run() defaults apply
        shared = {k: v for k, v in shared.items() if v is not None}

        fair_start_ms = absolute_cuts.get('start_ms') if absolute_cuts else None
        fair_end_ms = absolute_cuts.get('end_ms') if absolute_cuts else None
        if fair_start_ms is not None and fair_end_ms is not None:
            shared['fair_window_ms'] = {
                'start_ms': fair_start_ms,
                'end_ms': fair_end_ms,
                'window_mode': window_mode,
            }

        cells = []
        n_ok = n_insufficient = n_rejected = n_excluded_struct = n_unimplemented = 0
        for svid in cleaned_vers:
            for sym in cleaned_syms:
                cell_body = dict(shared)
                cell_body['strategy_version_id'] = svid
                cell_body['symbol'] = sym
                cell_body['run_id'] = _new_id('bt')
                raw_bars = client_bars_map.get(sym)
                if raw_bars is not None and fair_start_ms is not None and fair_end_ms is not None:
                    cropped, crop_meta = crop_bars_to_ms(raw_bars, fair_start_ms, fair_end_ms)
                    cell_body['bars'] = cropped
                    if svid == cleaned_vers[0]:
                        window_crop_notes.append({'symbol': sym, **crop_meta})
                elif raw_bars is not None:
                    cell_body['bars'] = raw_bars
                # Per-version stub map for tests
                stubs = body.get('version_stubs') if isinstance(body.get('version_stubs'), dict) else None
                if stubs and svid in stubs:
                    cell_body['version_stub'] = stubs[svid]
                try:
                    rec = self.run(cell_body)
                    cell = self._cell_summary(rec)
                    st = rec.get('status')
                    if st == 'insufficient':
                        n_insufficient += 1
                    elif st == 'excluded_struct':
                        n_excluded_struct += 1
                    elif st == 'unimplemented':
                        n_unimplemented += 1
                    elif st == 'completed':
                        n_ok += 1
                    else:
                        n_ok += 1  # other completed-like
                except (ValueError, BacktestConflict) as exc:
                    n_rejected += 1
                    cell = {
                        'run_id': None,
                        'status': 'rejected',
                        'status_note': str(exc)[:400],
                        'reject_reason': str(exc)[:400],
                        'strategy_version_id': svid,
                        'symbol': sym,
                        'market': market,
                        'bars_count': 0,
                        'trades': None,
                        'win_rate_label': '无样本',
                        'realized_pnl_net': None,
                        'exclusions': {'insufficient': False, 'rejected': True},
                    }
                cells.append(cell)

        # Comparison table: rows=versions, cols=symbols
        table = {
            'versions': cleaned_vers,
            'symbols': cleaned_syms,
            'cells': cells,
            'grid': {
                f'{c["strategy_version_id"]}|{c["symbol"]}': c
                for c in cells
            },
        }

        record = {
            'compare_id': compare_id,
            'schema': 'backtest-compare-v1',
            'status': 'completed',
            'created_at': _utcnow(),
            'symbols': cleaned_syms,
            'strategy_version_ids': cleaned_vers,
            'shared': {
                **shared,
                # don't persist huge stubs
                'version_stub': None,
            },
            'absolute_cuts': absolute_cuts,
            'window_mode': window_mode,
            'window_crop_notes': window_crop_notes,
            'lookahead_guard': True,
            'symbol_ranges': range_notes,
            'table': table,
            'summary': {
                'cells_total': len(cells),
                'completed': n_ok,
                'insufficient': n_insufficient,
                'excluded_struct': n_excluded_struct,
                'unimplemented': n_unimplemented,
                'rejected': n_rejected,
            },
            'coverage_warnings': COVERAGE_WARNINGS,
            'coverage_gaps': coverage_gaps,
            'ambiguity_rules': AMBIGUITY_RULES,
            'struct_filter_applied': bool(shared.get('struct_filter')),
            'disclaimer': DISCLAIMER,
            'forbidden': [
                '不宣称全市场/历史上市宇宙回测',
                '不回填当今市值',
                '缺档/不可测格不得记假赢',
            ],
        }

        with self._lock:
            if (self.compares_dir / f'{compare_id}.json').exists():
                raise BacktestConflict(f'重复 compare_id 已拒绝: {compare_id}')
            atomic(self.compares_dir / f'{compare_id}.json', encoded(record))
            index = self._load_compare_index()
            index.setdefault('compares', []).insert(0, {
                'compare_id': compare_id,
                'created_at': record['created_at'],
                'symbols': cleaned_syms,
                'strategy_version_ids': cleaned_vers,
                'cells_total': len(cells),
                'completed': n_ok,
                'insufficient': n_insufficient,
                'excluded_struct': n_excluded_struct,
                'unimplemented': n_unimplemented,
                'rejected': n_rejected,
                'window_mode': window_mode,
            })
            index['compares'] = index['compares'][:100]
            self._save_compare_index(index)
        return record

    def list_compares(self, limit=30):
        limit = max(1, min(int(limit), 100))
        with self._lock:
            index = self._load_compare_index()
            rows = list(index.get('compares') or [])[:limit]
            return {
                'compares': rows,
                'total': len(index.get('compares') or []),
                'coverage_warnings': COVERAGE_WARNINGS,
                'disclaimer': DISCLAIMER,
            }

    def get_compare(self, compare_id):
        path = self.compares_dir / f'{compare_id}.json'
        if not path.exists():
            raise KeyError(compare_id)
        return self._read_json(path)

    def list_runs(self, limit=50):
        limit = max(1, min(int(limit), 200))
        with self._lock:
            index = self._load_index()
            runs = list(index.get('runs') or [])[:limit]
            return {
                'runs': runs,
                'total': len(index.get('runs') or []),
                'coverage_warnings': COVERAGE_WARNINGS,
                'disclaimer': DISCLAIMER,
            }

    def get_run(self, run_id):
        path = self.runs_dir / f'{run_id}.json'
        if not path.exists():
            raise KeyError(run_id)
        return self._read_json(path)
