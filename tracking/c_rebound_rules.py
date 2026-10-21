"""1015 v1.1 C rules. Pure, no IO, no use of B's 0.80 qualification.

The S1 frozen cycle evaluator handles invalidation before confirmation, the real
T+1..T+10 window and unresolved earlier roots. This module supplies independently
qualified raw inputs, and owns rearming and post-cycle observations.
"""
from __future__ import annotations

import math
from .chain_validate import judge

H4 = 14_400_000
RULE_VERSION = 'c-1015-v1.1'
EVIDENCE_PROFILE = 'c-4h-contemporaneous-v1'
ALGORITHM = 'ema-first-close-wilder-rsi14-window1499-v1'
MAX_BARS = 1499


def finite(x):
    return isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(x)


def tri(*states):
    return 'fail' if 'fail' in states else 'unknown' if 'unknown' in states else 'pass'


def strict_new_lows(bars, at_open):
    return judge.CSeries(bars).new_lows14(at_open, judge.FROZEN)


def ratio7(bars, at_open):
    return judge.CSeries(bars).vol_ratio7(at_open, judge.FROZEN)


def sample_at(samples, boundary, field, *, lag=300_000, unit=None):
    """Never fill historical roots with a future/current or unspecified-unit value."""
    eligible = [s for s in samples if isinstance(s, dict)
                and type(s.get('effective_at_ms')) is int
                and 0 <= boundary - s['effective_at_ms'] <= lag
                and finite(s.get(field)) and s[field] >= 0
                and (unit is None or s.get('unit') == unit)
                and type(s.get('available_at_ms')) is int]
    if not eligible:
        return None
    return max(eligible, key=lambda s: (s['effective_at_ms'], s['available_at_ms']))


def structure_at(boundary, *, oi_samples=(), lsr_samples=(), caps=()):
    oi = sample_at(oi_samples, boundary, 'value', unit='USD')
    lsr = sample_at(lsr_samples, boundary, 'value', unit='ratio')
    cap = sample_at([s for s in caps if s.get('trusted') is True], boundary,
                    'value', lag=3_600_000, unit='USD')
    pct = oi['value'] / cap['value'] * 100 if oi and cap and cap['value'] > 0 else None
    ratio = lsr['value'] if lsr else None
    return dict(boundary_ms=boundary, oi_cap_pct=pct, lsr=ratio,
                oi_state='unknown' if pct is None else 'pass' if pct > 30 else 'fail',
                lsr_state='unknown' if ratio is None else 'pass' if ratio < 1.5 else 'fail',
                oi_sample=oi, lsr_sample=lsr, cap_sample=cap,
                evidence_profile=EVIDENCE_PROFILE)


def prepare_bars(raw, *, target_close_ms, oi_samples=(), lsr_samples=(), caps=()):
    """Normalize a caller-declared, bounded raw 4H window. No silent warmup change.

    Raw rows use open_ms,o,h,l,c,q, q_unit='USD nominal quote'. COIN-M callers
    must convert contracts using the archived contract multiplier before entry.
    A gap resets indicator warmup, instead of pretending the closes are adjacent.
    """
    if len(raw) > MAX_BARS:
        raise ValueError('C_window_exceeds_1499; page recovery with frozen warmup')
    by = {}
    for b in raw:
        o = b.get('open_ms')
        if type(o) is not int or o % H4 or o + H4 > target_close_ms:
            raise ValueError('C_unclosed_or_misaligned_bar')
        if o in by and by[o] != b:
            raise ValueError('C_conflicting_duplicate_bar')
        if b.get('q_unit') != 'USD nominal quote':
            raise ValueError('C_quote_volume_unit_required')
        if any(not finite(b.get(k)) for k in ('o', 'h', 'l', 'c', 'q')):
            raise ValueError('C_nonfinite_bar')
        if min(b['o'], b['h'], b['l'], b['c']) <= 0 or b['q'] < 0 or not (b['l'] <= min(b['o'], b['c']) <= max(b['o'], b['c']) <= b['h']):
            raise ValueError('C_invalid_ohlcv')
        by[o] = b
    result, closes, emas, gain, loss, deltas = [], [], None, None, None, []
    previous = None
    for o, raw_bar in sorted(by.items()):
        if previous is None or o != previous + H4:
            closes, emas, gain, loss, deltas = [], None, None, None, []
        c = raw_bar['c']
        if closes:
            delta = c - closes[-1]
            deltas.append((max(delta, 0), max(-delta, 0)))
            if len(deltas) == 14:
                gain = sum(x[0] for x in deltas) / 14
                loss = sum(x[1] for x in deltas) / 14
            elif len(deltas) > 14:
                gain = (gain * 13 + deltas[-1][0]) / 14
                loss = (loss * 13 + deltas[-1][1]) / 14
        closes.append(c)
        emas = [c, c, c] if emas is None else [v + 2 / (p + 1) * (c - v) for v, p in zip(emas, (7, 25, 99))]
        ready = len(closes) >= 120
        rsi = None if gain is None else 100 if loss == 0 and gain > 0 else 50 if loss == 0 else 100 - 100 / (1 + gain / loss)
        st = structure_at(o + H4, oi_samples=oi_samples, lsr_samples=lsr_samples, caps=caps)
        result.append(dict(raw_bar, ema7=emas[0] if ready else None,
                           ema25=emas[1] if ready else None, ema99=emas[2] if ready else None,
                           rsi14=rsi if ready else None, oi_cap_pct=st['oi_cap_pct'], lsr=st['lsr'],
                           structure=st, algorithm=ALGORITHM, warmup_start_ms=o-(len(closes)-1)*H4,
                           warmup_count=len(closes)))
        previous = o
    return result


def c0(bars, open_ms):
    b = next((b for b in bars if b['open_ms'] == open_ms), {})
    return judge.c_c0_check(dict(rsi14=b.get('rsi14'), close=b.get('c'),
        ema7=b.get('ema7'), ema25=b.get('ema25'), ema99=b.get('ema99'),
        oi_cap_pct=b.get('oi_cap_pct'), lsr=b.get('lsr'),
        new_lows14=strict_new_lows(bars, open_ms)), judge.FROZEN)


def evaluate_cycle(bars, t0, target_close_ms):
    return judge.c_run(bars, t0, target_close_ms, judge.FROZEN)


def can_rearm(evaluations):
    # A complete, explicitly false C0 is needed. Unknown and partial fail do not arm.
    armed = False
    for e in sorted(evaluations, key=lambda e: e['open_ms']):
        states = e['checks']
        complete = all(states.get(k) in ('pass', 'fail') for k in ('rsi','below_all_ema','oi_cap','lsr','new_lows14'))
        if complete and states['c0'] == 'fail':
            armed = True
        elif complete and states['c0'] == 'pass' and armed:
            return e['open_ms']
    return None


def observe_cycle(bars, t0, cycle, *, human_stop_open=None):
    by = {b['open_ms']: b for b in bars}
    terminal = t0 + cycle['terminal_offset'] * H4
    return judge.c_observation(dict(origin=cycle['status'], t0_open_ms=t0,
        terminal_open_ms=terminal, cycle_bars=[b for b in bars if t0 <= b['open_ms'] <= terminal],
        t0_low=by.get(t0, {}).get('l'), after_bars=[b for b in bars if b['open_ms'] > terminal],
        human_stop_at_open_ms=human_stop_open), judge.FROZEN)


def research_exit(*, close, actual_research_entry, volume_ratio):
    """Separate simulation study fact; never issues orders or changes tracking."""
    reasons = []
    if finite(close) and finite(actual_research_entry) and actual_research_entry > 0 and close < actual_research_entry * .85:
        reasons.append('research_stop_15pct')
    if finite(volume_ratio) and volume_ratio < 1:
        reasons.append('research_volume_below_1')
    return dict(reasons=reasons, primary=reasons[0] if reasons else None,
                reference='next_real_open', orders_enabled=False)


def expired_followup(bars, *, t0_open_ms, terminal_close_ms, evaluated_close_ms, previous=None):
    """Bounded progress facts outside C1's window, never another confirmation.

    Only roots actually consumed by the observation may contribute. A gap or
    human/natural end therefore cannot be skipped by a later rebound.
    """
    progress=dict(previous or {})
    t0=next((b for b in bars if b['open_ms']==t0_open_ms),None)
    for b in bars:
        close=b['open_ms']+H4
        if not terminal_close_ms<close<=evaluated_close_ms:continue
        ratio=ratio7(bars,b['open_ms'])
        fact=dict(bar_close_ms=close,close=b['c'],bullish=finite(b.get('o')) and b['c']>b['o'],
            volume_ratio7=ratio,offset_from_t0=(b['open_ms']-t0_open_ms)//H4,
            return_from_t0_pct=(b['c']/t0['c']-1)*100 if t0 and t0['c']>0 else None)
        if not progress.get('latest') or progress['latest']['bar_close_ms']<=close:progress['latest']=fact
        if fact['bullish'] and ratio is not None and ratio>3:
            if not progress.get('first_rebound') or close<progress['first_rebound']['bar_close_ms']:progress['first_rebound']=fact
    return dict(progress,changes_original_confirmation=False,original_cycle_status='expired')
