"""Bounded public candle backfill; immutable evidence, never inferred trades or ATH."""
import datetime as dt
import hashlib
import json
import math
import re
import threading
import uuid
from pathlib import Path

from snapshot_store import ScanLease, atomic, encoded

UTC = dt.timezone.utc
STEPS = {'1h': 3_600_000, '4h': 14_400_000, '1d': 86_400_000}
SOURCES = {'USDⓈ-M': 'https://fapi.binance.com/fapi/v1/klines',
           'COIN-M': 'https://dapi.binance.com/dapi/v1/klines'}


def timestamp(value):
    if not isinstance(value, str):
        raise ValueError('时间须为带时区的ISO8601字符串')
    try:
        parsed = dt.datetime.fromisoformat(value.replace('Z', '+00:00'))
        if parsed.tzinfo is None:
            raise ValueError()
        return int(parsed.timestamp() * 1000)
    except (ValueError, OverflowError):
        raise ValueError('时间须为带时区的有效ISO8601字符串') from None


def date(ms):
    return dt.datetime.fromtimestamp(ms / 1000, UTC).isoformat(timespec='milliseconds')


def digest(value):
    return hashlib.sha256(encoded(value)).hexdigest()


def series_name(contract, interval):
    return hashlib.sha256((contract['market'] + '|' + contract['symbol']).encode()).hexdigest() + '-' + interval + '.json'


def validate(root, value, now=None):
    if not isinstance(value, dict) or set(value) != {'contracts', 'interval', 'start', 'end'}:
        raise ValueError('须包含contracts/interval/start/end')
    interval = value['interval']
    if not isinstance(interval, str) or interval not in STEPS:
        raise ValueError('仅支持1h/4h/1d已收盘K线')
    contracts = value['contracts']
    if not isinstance(contracts, list) or not 1 <= len(contracts) <= 20:
        raise ValueError('每任务选择1–20份合约；可分批补查')
    for contract in contracts:
        if (not isinstance(contract, dict) or set(contract) != {'market', 'symbol'}
                or not isinstance(contract['market'], str) or contract['market'] not in SOURCES or not isinstance(contract['symbol'], str)
                or not re.fullmatch(r'\w{2,40}', contract['symbol'])):
            raise ValueError('无效合约键，须指定市场及完整合约代码')
    keys = [c['market'] + '|' + c['symbol'] for c in contracts]
    if len(keys) != len(set(keys)):
        raise ValueError('合约不能重复')
    snapshot_bytes = (Path(root) / 'scan.json').read_bytes()
    snapshot = json.loads(snapshot_bytes)
    universe = {(r.get('market'), r.get('symbol')) for r in snapshot.get('rows', [])}
    if any((c['market'], c['symbol']) not in universe for c in contracts):
        raise ValueError('合约须在当前保存快照中，不能提交任意代码或URL')
    start, end = timestamp(value['start']), timestamp(value['end'])
    now = now or dt.datetime.now(UTC)
    if start < 0 or end <= start or end - start > 366 * STEPS['1d']:
        raise ValueError('区间须为1970年以后、结束晚于开始，且不超过366天')
    if end > int(now.timestamp() * 1000):
        raise ValueError('结束时间不能在未来')
    step = STEPS[interval]
    # Include only complete UTC bars fully inside [start,end).
    first = ((start + step - 1) // step) * step
    stop = (end // step) * step
    if stop <= first:
        raise ValueError('所选区间没有完整已收盘K线')
    return {'contracts': sorted(contracts, key=lambda c: (c['market'], c['symbol'])),
            'interval': interval, 'start': date(start), 'end': date(end),
            'effective_start': date(first), 'effective_end': date(stop),
            'expected_per_contract': (stop - first) // step,
            'snapshot': {'sha256': hashlib.sha256(snapshot_bytes).hexdigest(),
                         'scan_id': snapshot.get('scan_id'), 'revision': snapshot.get('revision'),
                         'available_at': snapshot.get('available_at'), 'legacy': not bool(snapshot.get('scan_id'))}}


def bar_valid(bar, step):
    if not isinstance(bar, list) or len(bar) < 12:
        return False
    if type(bar[0]) is not int or type(bar[6]) is not int or bar[0] % step or bar[6] != bar[0] + step - 1:
        return False
    try:
        o, h, l, c, volume = [float(bar[n]) for n in range(1, 6)]
        rest = [float(bar[n]) for n in (7, 8, 9, 10)]
        return (all(math.isfinite(x) for x in (o, h, l, c, volume, *rest))
                and min(o, h, l, c) > 0 and l <= min(o, c) <= max(o, c) <= h
                and volume >= 0 and all(x >= 0 for x in rest))
    except (ValueError, TypeError, OverflowError):
        return False


def same_bar(a, b):
    return all(float(a[i]) == float(b[i]) for i in range(11))


def coverage(bars, first, stop, step):
    expected = (stop - first) // step
    known = sorted(t for t in bars if first <= t < stop)
    gaps, cursor = [], first
    for t in known:
        if t > cursor:
            gaps.append({'start': date(cursor), 'end': date(t), 'bars': (t - cursor) // step})
        cursor = t + step
    if cursor < stop:
        gaps.append({'start': date(cursor), 'end': date(stop), 'bars': (stop - cursor) // step})
    return {'expected': expected, 'known': len(known), 'missing': expected - len(known),
            'coveragePct': round(100 * len(known) / expected, 4), 'complete': len(known) == expected,
            'gap_count': len(gaps), 'gaps': gaps[:100], 'gaps_truncated': len(gaps) > 100}


class CandleRanges:
    def __init__(self, root, request=None, clock=None):
        self.root = Path(root)
        self.path = self.root / 'candle-history'
        self.request = request
        self.clock = clock or (lambda: dt.datetime.now(UTC))
        self.lock = threading.RLock()
        self.progress = {'phase': '等待行情补查', 'completed': 0, 'total': 0}

    def prepare(self, value):
        return validate(self.root, value, self.clock())

    def history(self, limit=50):
        manifests = []
        if self.path.exists():
            for directory in self.path.iterdir():
                if re.fullmatch(r'range-[a-f0-9]{32}', directory.name):
                    try:
                        manifest = json.loads((directory / 'manifest.json').read_bytes())
                        if manifest.get('range_id') == directory.name:
                            manifests.append(manifest)
                    except (OSError, ValueError):
                        continue
        return sorted(manifests, key=lambda m: m['available_at'], reverse=True)[:limit]

    def preflight(self, value):
        # DATA-01: merge scan-window candles + range archives (read-only, no network).
        from price_index import preflight_merged
        return preflight_merged(self.root, value, clock=self.clock)

    def run(self, spec):
        # Shared lease also excludes concurrent CLI scans and their public request load.
        with ScanLease(self.root):
            return self._run(spec)

    def _run(self, spec):
        request = self.request
        if request is None:
            from scanner import request
        range_id = 'range-' + uuid.uuid4().hex
        folder = self.path / range_id
        folder.mkdir(parents=True, exist_ok=False)
        started = self.clock().isoformat()
        atomic(folder / 'request.json', encoded({'range_id': range_id, 'request': spec, 'started_at': started}))
        step = STEPS[spec['interval']]
        first, stop = timestamp(spec['effective_start']), timestamp(spec['effective_end'])
        total = len(spec['contracts']) * spec['expected_per_contract']
        self.progress = {'phase': '补查公开K线', 'completed': 0, 'total': total}
        result = []
        completed = 0
        halted = None
        for contract in spec['contracts']:
            bars, errors, pages, conflicts = {}, [], [], set()
            cursor = first
            if halted:
                errors.append({'start': date(first), 'end': date(stop), 'error': '上游请求失败后停止后续请求：' + halted})
                cursor = stop
            while cursor < stop:
                # COIN-M bounds a request to 200 days; explicit <=500-bar windows
                # avoid its most-recent-limit truncation and do not skip empty pages.
                window_end = min(stop, cursor + min(500 * step, 200 * STEPS['1d']))
                params = {'symbol': contract['symbol'], 'interval': spec['interval'], 'startTime': cursor,
                          'endTime': window_end - 1, 'limit': 500}
                self.progress = {'phase': f"补查 {contract['symbol']} {spec['interval']}", 'completed': completed, 'total': total}
                try:
                    raw = request(SOURCES[contract['market']], params)
                    retrieved = self.clock().isoformat()
                    if not isinstance(raw, list):
                        raise ValueError('行情源返回的不是K线数组')
                    page_id = len(pages)
                    filename = series_name(contract, spec['interval']) + f'.page-{page_id:04d}.json'
                    # Preserve exact response values, including rejected rows, separately.
                    payload = json.dumps({'source': SOURCES[contract['market']], 'params': params,
                                          'retrieved_at': retrieved, 'available_at': retrieved, 'raw': raw},
                                         ensure_ascii=False, allow_nan=False).encode('utf-8')
                    atomic(folder / 'pages' / filename, payload)
                    pages.append({'file': 'pages/' + filename, 'sha256': hashlib.sha256(payload).hexdigest(),
                                  'retrieved_at': retrieved, 'available_at': retrieved, 'count': len(raw)})
                    rejected = 0
                    for bar in raw:
                        if not bar_valid(bar, step) or not cursor <= bar[0] < window_end or bar[6] >= int(self.clock().timestamp() * 1000):
                            rejected += 1
                            continue
                        t = bar[0]
                        if t in conflicts:
                            continue
                        if t in bars and not same_bar(bars[t], bar):
                            del bars[t]
                            conflicts.add(t)
                            rejected += 1
                        else:
                            bars[t] = bar
                    if rejected:
                        errors.append({'start': date(cursor), 'end': date(window_end), 'error': f'{rejected}条格式/边界/重复冲突K线已排除'})
                except Exception as exc:
                    halted = str(exc)[:250]
                    errors.append({'start': date(cursor), 'end': date(window_end), 'error': halted})
                    # Rate limiting or network errors stop this contract; missing tail
                    # remains a gap. Never retry a 429 by immediately paging ahead.
                    break
                cursor = window_end
                completed += (window_end - params['startTime']) // step
            available = self.clock().isoformat()
            data = {**contract, 'interval': spec['interval'], 'source': SOURCES[contract['market']],
                    'available_at': available, 'retrieved_at': pages[-1]['retrieved_at'] if pages else None,
                    'source_time': max(b[6] for b in bars.values()) if bars else None,
                    'raw': [bars[t] for t in sorted(bars)], 'conflicted_open_times': sorted(conflicts), 'pages': pages}
            payload = encoded(data)
            atomic(folder / series_name(contract, spec['interval']), payload)
            result.append({**contract, **coverage(bars, first, stop, step), 'conflicts': len(conflicts),
                           'sha256': hashlib.sha256(payload).hexdigest(), 'available_at': available,
                           'source_time': data['source_time'], 'pages': len(pages), 'errors': errors})
        status = 'success' if all(r['complete'] and not r['errors'] for r in result) else 'partial_success' if any(r['known'] for r in result) else 'failed'
        manifest = {'range_id': range_id, 'status': status, 'request': spec, 'series': result,
                    'started_at': started, 'available_at': self.clock().isoformat(),
                    'known': sum(r['known'] for r in result), 'expected': total,
                    'scope': 'public contract interval; not token ATH, historical OI/cap/netflow or trading results'}
        # Final manifest is written once; retries always get a new archive ID.
        with (folder / 'manifest.json').open('xb') as output:
            output.write(encoded(manifest))
            output.flush()
            import os
            os.fsync(output.fileno())
        self.progress = {'phase': '补查完成' if status != 'failed' else '补查失败', 'completed': manifest['known'], 'total': total}
        return manifest
