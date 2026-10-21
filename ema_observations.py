"""Read-only EMA observations v1. No entry filters, network, or snapshot writes.

EMA seed: first close of each contiguous valid segment. Historical states need
120 bars too. Timestamps are UTC milliseconds of CLOSED 4h bars.
"""
import datetime as dt
import json
import math
from functools import lru_cache
from pathlib import Path

SCHEMA = 'ema-observation-v1.2'
CROSS_VERSION = 'ema-long-cross-v1'
BAR_MS = 14_400_000
WARMUP = 120


def finite(value, positive=False):
    try:
        n = float(value)
        return n if math.isfinite(n) and (not positive or n > 0) else None
    except (ValueError, TypeError, OverflowError):
        return None


def time_ms(value):
    if value is None or isinstance(value, bool):
        return None
    n = finite(value)
    if n is not None:
        # Numeric candle fields in the public contract are milliseconds, not seconds.
        return int(n) if n >= 100_000_000_000 else None
    try:
        d = dt.datetime.fromisoformat(str(value).replace('Z', '+00:00'))
        return int(d.timestamp() * 1000) if d.tzinfo else None
    except (ValueError, TypeError, OverflowError):
        return None


def states(raw, volumes=None):
    """Linear shared EMA/TR pass. Gaps reset warmup; no stitching across gaps."""
    result, segment, emas, previous = [], [], None, None
    for i, k in enumerate(raw):
        valid = isinstance(k, (list, tuple)) and len(k) >= 8
        values = [finite(k[j]) for j in (0, 1, 2, 3, 4, 6)] if valid else []
        valid = bool(values) and all(v is not None for v in values)
        if valid:
            start, op, high, low, close, end = values
            valid = (time_ms(start) is not None and end == start + BAR_MS - 1
                     and op > 0 and close > 0 and 0 < low <= min(op, close)
                     and high >= max(op, close))
        if not valid:
            segment, emas, previous = [], None, None
            result.append(None)
            continue
        if previous is not None and start - previous['open_time'] != BAR_MS:
            segment, emas, previous = [], None, None
        emas = [close] * 3 if emas is None else [e + 2 / (p + 1) * (close - e) for e, p in zip(emas, (7, 25, 99))]
        volume = finite(volumes[i]) if volumes is not None and i < len(volumes) else finite(k[7])
        prior7 = [x['volume'] for x in segment[-7:]]
        ratio = None
        if volume is not None and volume >= 0 and len(prior7) == 7 and all(v is not None and v >= 0 for v in prior7) and sum(prior7) > 0:
            ratio = volume / (sum(prior7) / 7)
        s = dict(open_time=int(start), bar_time=int(end), open=op, high=high, low=low, close=close,
                 ema=list(emas), volume=volume, volume_ratio=ratio,
                 count=len(segment)+1, valid=len(segment)+1 >= WARMUP)
        segment.append(s)
        result.append(s)
        previous = s
    return result


def long_crosses(current, previous):
    if not current or not previous or not current['valid'] or not previous['valid'] or current['open_time']-previous['open_time'] != BAR_MS:
        return dict(ema7_cross_25=None, ema7_cross_99=None, ema25_cross_99=None)
    e,p=current['ema'],previous['ema']
    return {name:p[a] <= p[b] and e[a] > e[b] for name,a,b in
            [('ema7_cross_25',0,1),('ema7_cross_99',0,2),('ema25_cross_99',1,2)]}


def observe(raw, volumes=None, volume_unit='quote_notional'):
    return observation(states(raw, volumes), volume_unit)


def observation(series, volume_unit='quote_notional'):
    s = series[-1] if series else None
    out = dict(schema=SCHEMA, timeframe='4h', timestamp_unit='utc_ms', warmup_bars=WARMUP,
               source='Binance archived closed 4h OHLC', volume_unit=volume_unit,
               bar_time=s['bar_time'] if s else None, valid_bars=s['count'] if s else 0,
               quality='valid' if s and s['valid'] else 'insufficient_or_gap',
               weak_new_cross=None, above99_count=None, above99_count_exact=None,
               history60=dict(stack_seen=None, coverage=0, last_observed_stack_time=None, latest_stack_time=None),
               drawdown60_pct=None, distance_ema99_pct=None, distance_ema7_pct=None,
               candle_return_pct=None, return5_pct=None, atr14=None,
               distance_ema99_atr=None, distance_ema7_atr=None,
               candle_shape=None, research_path_b=None, technical_crosses=None, execution_thresholds_enabled=False,
               long_crosses=long_crosses(s,series[-2] if len(series)>1 else None),
               long_stack=(s['ema'][0]>s['ema'][1]>s['ema'][2]) if s and s['valid'] else None,
               recent_long_crosses=[],history_scope='last_60_archived_closed_bars_not_all_history',
               cross_version=CROSS_VERSION)
    if not s or not s['valid']:
        return out
    for i in range(max(1,len(series)-60),len(series)):
        for kind,triggered in long_crosses(series[i],series[i-1]).items():
            if triggered is True:
                out['recent_long_crosses'].append(dict(kind=kind,bar_time=series[i]['bar_time'],
                    ema=series[i]['ema'],previous_ema=series[i-1]['ema'],volume_ratio=series[i]['volume_ratio']))
    segment = series[-s['count']:]
    prev = segment[-2] if len(segment) >= 2 else None
    c, e = s['close'], s['ema']
    if prev and prev['valid']:
        from ema_squeeze_rules import medium_cross, full_cross
        out['weak_new_cross'] = prev['close'] <= prev['ema'][2] and c > e[2]
        out['research_path_b'] = bool(e[0] > e[1] > e[2] and prev['ema'][0] <= prev['ema'][1] and prev['ema'][1] > prev['ema'][2])
        out['technical_crosses'] = dict(weak_ema99_cross=out['weak_new_cross'],
            medium_ema7_25_cross=medium_cross(e[0], e[1], e[2], prev['ema'][0], prev['ema'][1], c),
            full_cross_path_a=full_cross(e[0], e[1], e[2], *prev['ema']))
    n, exact = 0, False
    for x in reversed(segment):
        if not x['valid']:
            break
        if x['close'] <= x['ema'][2]:
            exact = True
            break
        n += 1
    out.update(above99_count=n, above99_count_exact=exact)
    hist = segment[-61:-1] if len(segment) > 1 else []
    valid = [x for x in hist if x['valid']]
    stacks = [x for x in valid if x['ema'][0] > x['ema'][1] > x['ema'][2]]
    last = stacks[-1] if stacks else None
    full = len(valid) == 60
    # A positive observation is valid even with incomplete early history. A negative isn't.
    latest = last['bar_time'] if last and all(x['valid'] for x in hist if x['bar_time'] > last['bar_time']) else None
    out['history60'] = dict(stack_seen=True if stacks else False if full else None,
                            coverage=len(valid), last_observed_stack_time=last['bar_time'] if last else None,
                            latest_stack_time=latest, close_above_ema7_at_last=last['close'] > last['ema'][0] if last else None)
    if full:
        h = max(x['close'] for x in valid)
        out['drawdown60_pct'] = max(0., (h-c)/h*100)
    from struct_indicators import compute_atr
    atr = compute_atr(segment, period=14)['value']
    out['atr_method'] = 'ATR_WILDER_v1; shared struct_indicators.compute_atr'
    body, upper = abs(c-s['open']), s['high']-max(s['open'], c)
    out.update(distance_ema99_pct=(c-e[2])/e[2]*100, distance_ema7_pct=(c-e[0])/e[0]*100,
               candle_return_pct=(c/s['open']-1)*100, return5_pct=(c/segment[-6]['close']-1)*100,
               atr14=atr, candle_shape=dict(open=s['open'], high=s['high'], low=s['low'], close=c,
               body=body, upper_wick=upper, upper_body_ratio=upper/body if body > 0 else None, bullish=c > s['open']))
    if atr is not None and atr > 0:
        out.update(distance_ema99_atr=(c-e[2])/atr, distance_ema7_atr=(c-e[0])/atr)
    return out



def bind_identity(out, market, symbol):
    """Technical crosses are independent of a future candidate cycle, never entry permits."""
    from ema_squeeze_rules import RULES_VERSION
    out = dict(out)
    events = []
    if market and symbol and out.get('bar_time') is not None:
        for kind, triggered in (out.get('technical_crosses') or {}).items():
            if triggered is True:
                events.append(dict(event_id='|'.join(map(str, (market, symbol, '4h', RULES_VERSION, out['bar_time'], kind))),
                    kind=kind, bar_time=out['bar_time'], rules_version=RULES_VERSION, technical_only=True,
                    candidate_qualification='not_evaluated', automatic_fill=False))
    out['technical_events'] = events
    def event(kind,bar,**extra):
        return dict(event_id='|'.join(map(str,(market,symbol,'4h',CROSS_VERSION,bar,kind))),
                    kind=kind,bar_time=bar,observation_version=CROSS_VERSION,technical_only=True,
                    grade=None,candidate_qualification='not_evaluated',automatic_fill=False,**extra)
    out['long_cross_events']=[event(k,out['bar_time']) for k,v in (out.get('long_crosses') or {}).items() if v is True] if market and symbol else []
    out['recent_long_cross_events']=[]
    if market and symbol:
        out['recent_long_cross_events']=[event(x['kind'],x['bar_time'],ema=x['ema'],previous_ema=x['previous_ema'],volume_ratio=x['volume_ratio']) for x in out.get('recent_long_crosses',[])]
    return out


def archive_raw(root, row):
    rel = (row.get('candleArchives') or {}).get('4h')
    if not isinstance(rel, str):
        return None
    root = Path(root).resolve()
    path = (root / rel).resolve()
    if not path.is_relative_to(root / 'scan-history') or path.suffix != '.json':
        return None
    from market_candle_cache import read_json_maybe_gzip
    data = read_json_maybe_gzip(path)
    if (data.get('market'), data.get('symbol'), data.get('interval')) != (row.get('market'), row.get('symbol'), '4h'):
        return None
    raw = data.get('raw')
    if not isinstance(raw, list) or not raw or time_ms(raw[-1][6]) != time_ms(row.get('candleAsOf')):
        return None
    return raw


@lru_cache(maxsize=640)
def _archived_observation(root, rel, market, symbol, bar_time, size, modified, contract_size):
    row = dict(candleArchives={'4h': rel}, market=market, symbol=symbol, candleAsOf=bar_time)
    raw = archive_raw(root, row)
    if raw is None:
        return None
    volumes = [finite(k[5]) * contract_size if finite(k[5]) is not None else None for k in raw] if market == 'COIN-M' and contract_size else None
    if market == 'COIN-M' and not contract_size:
        volumes = [None] * len(raw)
    out = bind_identity(observe(raw, volumes, 'usd_contract_notional' if market == 'COIN-M' else 'quote_notional'), market, symbol)
    out['archive'] = rel
    return out


def enrich_rows(rows, root):
    """Derive optional observations from exact immutable archive, NEVER write snapshots."""
    count = 0
    for row in rows:
        if isinstance(row.get('emaObservation'), dict) and row['emaObservation'].get('schema') == SCHEMA:
            continue
        rel = (row.get('candleArchives') or {}).get('4h')
        if not rel:
            continue
        try:
            path = (Path(root) / rel).resolve()
            if not path.is_relative_to(Path(root).resolve() / 'scan-history'):
                continue
            st = path.stat()
            out = _archived_observation(str(root), rel, row.get('market'), row.get('symbol'), row.get('candleAsOf'), st.st_size, st.st_mtime_ns, finite(row.get('contractSize'), True))
            if out:
                # A hydrated older snapshot is evidence for its own bar only.
                row['emaObservation'] = dict(out, derived_at_read=True)
                count += 1
        except (OSError, ValueError, TypeError, IndexError, KeyError):
            continue
    return count
