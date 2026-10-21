"""Read-only merge of scan-window candles and candle-range archives.

Does not fetch Binance, rewrite archives, or claim six historical indicators.
Distinguishes 当前可得 (what disk can serve now) from 历史当时可得 (each source's available_at).
"""
from __future__ import annotations
from market_candle_cache import read_json_maybe_gzip

import datetime as dt
import gzip
import hashlib
import json
import re
from pathlib import Path

from candle_ranges import (
    STEPS,
    bar_valid,
    coverage,
    date,
    same_bar,
    series_name,
    timestamp,
)

UTC = dt.timezone.utc
SCAN_ID_RE = re.compile(r'^scan-[a-f0-9]{32}$')
RANGE_ID_RE = re.compile(r'^range-[a-f0-9]{32}$')
REV_RE = re.compile(r'^revision-(\d{4})\.json$')
INTERVALS = tuple(STEPS)


def _sha256_bytes(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _safe_json_bytes(path: Path):
    raw = path.read_bytes()
    return raw, read_json_maybe_gzip(path)


def continuity(open_times, step):
    """Per-bar continuity inside the known span [min, max+step)."""
    known = sorted(open_times)
    if not known:
        return {
            'continuous': True,
            'span_bars': 0,
            'gap_slots': 0,
            'trailing_continuous': 0,
            'longest_continuous': 0,
            'warmup_ready_hint': 0,
        }
    first, last = known[0], known[-1]
    span_bars = (last - first) // step + 1
    known_set = set(known)
    gap_slots = span_bars - len(known)
    trailing = 0
    t = last
    while t >= first and t in known_set:
        trailing += 1
        t -= step
    longest = cur = 0
    t = first
    while t <= last:
        if t in known_set:
            cur += 1
            longest = max(longest, cur)
        else:
            cur = 0
        t += step
    return {
        'continuous': gap_slots == 0,
        'span_bars': span_bars,
        'gap_slots': gap_slots,
        'trailing_continuous': trailing,
        'longest_continuous': longest,
        # Hint only: how many closed bars sit in a trailing streak (indicator warmup).
        'warmup_ready_hint': trailing,
    }


def list_snapshot_revisions(root, limit=100):
    """Browse published scan revisions already on disk. No invented history."""
    root = Path(root)
    history = root / 'scan-history'
    rows = []
    if not history.exists():
        return {'snapshots': [], 'scope': '尚无scan-history目录；不伪造历史快照'}

    # Versioned scans
    for directory in sorted(history.iterdir()):
        if not directory.is_dir() or not SCAN_ID_RE.fullmatch(directory.name):
            continue
        status = {}
        status_path = directory / 'status.json'
        if status_path.exists():
            try:
                status = json.loads(status_path.read_bytes())
            except (OSError, ValueError):
                status = {'status': 'unreadable'}
        for path in sorted(directory.glob('revision-*.json')):
            m = REV_RE.fullmatch(path.name)
            if not m:
                continue
            try:
                raw, data = _safe_json_bytes(path)
            except (OSError, ValueError):
                rows.append({
                    'scan_id': directory.name,
                    'revision': int(m.group(1)),
                    'path': str(path.relative_to(root)).replace('\\', '/'),
                    'error': 'revision文件不可读',
                    'provenance': 'scan-history',
                })
                continue
            quality = data.get('quality') or status.get('quality')
            total = data.get('total')
            received = data.get('received')
            try:
                total_i = int(total) if total is not None else None
                received_i = int(received) if received is not None else None
            except (TypeError, ValueError):
                total_i, received_i = None, None
            if received_i is None:
                received_i = len(data.get('rows') or [])
            row_ratio = None
            if total_i and total_i > 0:
                row_ratio = round(received_i / total_i, 4)
            content_sha = _sha256_bytes(raw)
            # coverage fingerprint: content + expected/received (not a universe rebuild)
            fp_payload = f'{content_sha}|total={total_i}|received={received_i}|q={(quality or {}).get("status")}'
            import hashlib as _hl
            cov_fp = _hl.sha256(fp_payload.encode('utf-8')).hexdigest()
            rows.append({
                'scan_id': data.get('scan_id') or directory.name,
                'revision': data.get('revision') or int(m.group(1)),
                'available_at': data.get('available_at'),
                'started_at': data.get('startedAt') or status.get('startedAt'),
                'finished_at': data.get('finishedAt'),
                'status': (quality or {}).get('status') or status.get('status'),
                'expected': total_i,
                'received': received_i,
                'total': total_i,  # alias kept for older UI
                'expected_vs_received': {
                    'expected': total_i,
                    'received': received_i,
                    'missing': (total_i - received_i) if (total_i is not None and received_i is not None) else None,
                    'row_ratio': row_ratio,
                },
                'coverage_fingerprint': cov_fp,
                'candidate_count': data.get('candidateCount'),
                'quality': quality,
                'schema_version': data.get('schemaVersion'),
                'content_sha256': content_sha,
                'candle_files': len(list((directory / 'candles').glob('*.json'))) if (directory / 'candles').exists() else 0,
                'provenance': 'scan-history',
                'availability': '历史当时可得' if data.get('available_at') else '可得时间未知',
            })

    # Legacy import (bytes preserved; not reconstructed as historical availability)
    legacy = history / 'legacy'
    if legacy.exists():
        for path in sorted(legacy.glob('*.json')):
            if path.name.endswith('.metadata.json'):
                continue
            try:
                raw, data = _safe_json_bytes(path)
            except (OSError, ValueError):
                continue
            meta = {}
            meta_path = path.with_suffix('.metadata.json')
            if meta_path.exists():
                try:
                    meta = json.loads(meta_path.read_bytes())
                except (OSError, ValueError):
                    meta = {}
            received_i = data.get('received') or len(data.get('rows') or [])
            try:
                total_i = int(data['total']) if data.get('total') is not None else None
                received_i = int(received_i)
            except (TypeError, ValueError):
                total_i = data.get('total')
            content_sha = _sha256_bytes(raw)
            import hashlib as _hl
            fp_payload = f'{content_sha}|total={total_i}|received={received_i}|legacy'
            cov_fp = _hl.sha256(fp_payload.encode('utf-8')).hexdigest()
            rows.append({
                'scan_id': data.get('scan_id') or None,
                'revision': data.get('revision'),
                'available_at': data.get('available_at') or meta.get('imported_at'),
                'started_at': data.get('startedAt'),
                'finished_at': data.get('finishedAt'),
                'status': 'legacy_import',
                'expected': total_i,
                'received': received_i,
                'total': total_i,
                'expected_vs_received': {
                    'expected': total_i,
                    'received': received_i,
                    'missing': (int(total_i) - int(received_i)) if (total_i is not None and received_i is not None) else None,
                    'row_ratio': (round(int(received_i) / int(total_i), 4) if total_i else None),
                },
                'coverage_fingerprint': cov_fp,
                'candidate_count': data.get('candidateCount'),
                'quality': data.get('quality'),
                'schema_version': data.get('schemaVersion'),
                'content_sha256': content_sha,
                'candle_files': 0,
                'provenance': 'legacy',
                'availability': 'legacy导入；历史字段可得时间未重建',
                'note': meta.get('provenance') or 'legacy bytes preserved; historical field availability not reconstructed',
            })

    for p in history.glob('scan-*/revision-*.json.cleaned.json'):
        try:
            d=read_json_maybe_gzip(p)
            rows.append(dict(scan_id=d['scan_id'],revision=int(d['revision_file'].split('-')[1].split('.')[0]),status='cleaned',availability='已按保留策略清理',note=d['reason'],content_sha256=d['source_hash'],available_at=None,received=None,total=None,candle_files=None,provenance='retention-catalog'))
        except (OSError,ValueError,KeyError):continue

    rows.sort(key=lambda r: (r.get('available_at') or '', r.get('scan_id') or '', r.get('revision') or 0), reverse=True)
    return {
        'snapshots': rows[:limit],
        'total_found': len(rows),
        'scope': '仅列出磁盘上已有的scan revision/legacy导入；缺失revision不补造；价格窗口另见价格索引；不重建全历史宇宙',
        'labels': {
            'current': '当前可得：现在能从磁盘读到的合并证据',
            'historical': '历史当时可得：各归档写入时的available_at；不等于当时已有全部字段',
            'expected': '应有合约数（扫描目录 total）',
            'received': '实收合约行数',
            'coverage_fingerprint': 'content_sha256+expected/received 指纹；便于浏览对照，非宇宙重建',
        },
    }


def _iter_scan_candle_files(root):
    history = Path(root) / 'scan-history'
    if not history.exists():
        return
    for directory in history.iterdir():
        if not directory.is_dir() or not SCAN_ID_RE.fullmatch(directory.name):
            continue
        candles = directory / 'candles'
        if not candles.exists():
            continue
        for path in list(candles.glob('*-*.json'))+list(candles.glob('*-*.json.gz')):
            # skip page dumps if any
            if '.page-' in path.name:
                continue
            yield directory.name, path


def _iter_range_manifests(root):
    path = Path(root) / 'candle-history'
    if not path.exists():
        return
    for directory in path.iterdir():
        if not directory.is_dir() or not RANGE_ID_RE.fullmatch(directory.name):
            continue
        manifest_path = directory / 'manifest.json'
        if not manifest_path.exists():
            continue
        try:
            manifest = json.loads(manifest_path.read_bytes())
        except (OSError, ValueError):
            continue
        if manifest.get('range_id') != directory.name:
            continue
        yield directory, manifest


def summarize_price_index(root, interval=None, limit=300):
    """Lightweight per-series catalog from scan candles + range manifests (no full merge)."""
    root = Path(root)
    if interval is not None and interval not in STEPS:
        raise ValueError('仅支持4h或1d')
    series = {}

    def slot(market, symbol, iv):
        key = f'{market}|{symbol}|{iv}'
        row = series.setdefault(key, {
            'market': market,
            'symbol': symbol,
            'interval': iv,
            'scan_sources': [],
            'range_sources': [],
            'approx_bars_scan': 0,
            'approx_bars_range_known': 0,
            'current_available': False,
        })
        return row

    for scan_id, path in _iter_scan_candle_files(root):
        # filename: <sha256>-<interval>.json
        name = path.name.removesuffix('.gz')
        if not name.endswith('.json'):
            continue
        stem = name[:-5]
        if '-' not in stem:
            continue
        sha_part, iv = stem.rsplit('-', 1)
        if iv not in STEPS or (interval and iv != interval):
            continue
        try:
            raw, data = _safe_json_bytes(path)
        except (OSError, ValueError):
            continue
        market, symbol = data.get('market'), data.get('symbol')
        if not isinstance(market, str) or not isinstance(symbol, str):
            continue
        row = slot(market, symbol, iv)
        count = data.get('count')
        if not isinstance(count, int):
            count = len(data.get('raw') or [])
        entry = {
            'source_kind': 'scan_window',
            'scan_id': scan_id,
            'file': str(path.relative_to(root)).replace('\\', '/'),
            'available_at': data.get('available_at'),
            'retrieved_at': data.get('retrieved_at'),
            'count': count,
            'content_sha256': _sha256_bytes(raw),
            'availability': '历史当时可得' if data.get('available_at') else '可得时间未知',
        }
        row['scan_sources'].append(entry)
        row['approx_bars_scan'] = max(row['approx_bars_scan'], count)
        row['current_available'] = True

    for directory, manifest in _iter_range_manifests(root):
        req = manifest.get('request') or {}
        iv = req.get('interval')
        if iv not in STEPS or (interval and iv != interval):
            continue
        for item in manifest.get('series') or []:
            market, symbol = item.get('market'), item.get('symbol')
            if not isinstance(market, str) or not isinstance(symbol, str):
                continue
            row = slot(market, symbol, iv)
            known = item.get('known') if isinstance(item.get('known'), int) else 0
            entry = {
                'source_kind': 'candle_range',
                'range_id': manifest.get('range_id'),
                'status': manifest.get('status'),
                'available_at': item.get('available_at') or manifest.get('available_at'),
                'known': known,
                'expected': item.get('expected'),
                'coveragePct': item.get('coveragePct'),
                'sha256': item.get('sha256'),
                'request_start': req.get('effective_start'),
                'request_end': req.get('effective_end'),
                'availability': '历史当时可得' if (item.get('available_at') or manifest.get('available_at')) else '可得时间未知',
            }
            row['range_sources'].append(entry)
            row['approx_bars_range_known'] = max(row['approx_bars_range_known'], known)
            row['current_available'] = True

    from market_candle_cache import MarketCandleCache
    from contextlib import closing
    cache=MarketCandleCache(root,readonly=True)
    if cache.db_path.exists() and interval in (None,'1h'):
        with closing(cache._connect()) as con:
            for item in con.execute("SELECT market,symbol,interval,COUNT(DISTINCT open_time) n FROM bars WHERE interval='1h' GROUP BY market,symbol,interval"):
                row=slot(item['market'],item['symbol'],item['interval']);row['current_available']=True;row['approx_bars_scan']=max(row['approx_bars_scan'],item['n']);row['label_current']='当前可得'
    rows = list(series.values())
    rows.sort(key=lambda r: (r['symbol'], r['market'], r['interval']))
    truncated = len(rows) > limit
    rows = rows[:limit]
    for row in rows:
        row['source_count'] = len(row['scan_sources']) + len(row['range_sources'])
        row['label_current'] = '当前可得' if row['current_available'] else '当前不可得'
        # Honest: catalog counts are per-source maxima, not a verified merge.
        row['merge_note'] = '目录级概览；逐根合并/缺口/冲突见 /api/price-index/series'

    scan_dirs = sum(1 for p in (root / 'scan-history').iterdir() if p.is_dir() and SCAN_ID_RE.fullmatch(p.name)) if (root / 'scan-history').exists() else 0
    range_dirs = sum(1 for p in (root / 'candle-history').iterdir() if p.is_dir() and RANGE_ID_RE.fullmatch(p.name)) if (root / 'candle-history').exists() else 0

    return {
        'series': rows,
        'total_series': len(series),
        'returned': len(rows),
        'truncated': truncated,
        'archives': {'scan_windows': scan_dirs, 'candle_ranges': range_dirs},
        'checked_at': dt.datetime.now(UTC).isoformat(timespec='milliseconds'),
        'scope': (
            '只读价格索引目录：合并扫描窗口K线与区间补查归档的来源清单。'
            '不证明历史OI/市值/链上流/账户比/费率齐全，也不等于可信ATH或六历史指标完成。'
        ),
        'labels': {
            'current_available': '当前可得：磁盘上现在能打开的价格证据',
            'historical_available_at': '历史当时可得：各来源写入时的available_at；后补价格不能回填成当时已得',
            'missing_history': '目录中没有的区间一律标不可得，不标记为已覆盖',
        },
    }


def merge_series(root, market, symbol, interval, start=None, end=None, include_bars=False):
    """Full read-only merge for one contract/interval with hash checks and conflict exclusion."""
    root = Path(root)
    if interval not in STEPS:
        raise ValueError('仅支持4h或1d')
    if not isinstance(market, str) or not isinstance(symbol, str):
        raise ValueError('须指定market与symbol')
    step = STEPS[interval]
    first = timestamp(start) if start else None
    stop = timestamp(end) if end else None
    if first is not None and stop is not None and stop <= first:
        raise ValueError('结束须晚于开始')

    merged = {}
    conflicted = set()
    evidence = []
    errors = []

    # 1) Scan-window candles
    for scan_id, path in _iter_scan_candle_files(root):
        if not path.name.removesuffix('.gz').endswith(f'-{interval}.json'):
            continue
        try:
            raw_bytes, data = _safe_json_bytes(path)
            if data.get('market') != market or data.get('symbol') != symbol or data.get('interval') != interval:
                continue
            content_hash = _sha256_bytes(raw_bytes)
            raw = data.get('raw') or []
            if not isinstance(raw, list):
                raise ValueError('扫描窗口K线格式异常')
            accepted = 0
            for bar in raw:
                if not bar_valid(bar, step):
                    continue
                t = bar[0]
                if first is not None and t < first:
                    continue
                if stop is not None and t >= stop:
                    continue
                if t in conflicted:
                    continue
                if t in merged and not same_bar(merged[t], bar):
                    conflicted.add(t)
                    del merged[t]
                else:
                    merged[t] = bar
                    accepted += 1
            evidence.append({
                'source_kind': 'scan_window',
                'scan_id': scan_id,
                'file': str(path.relative_to(root)).replace('\\', '/'),
                'available_at': data.get('available_at'),
                'content_sha256': content_hash,
                'bars_considered': len(raw),
                'bars_accepted': accepted,
                'availability': '历史当时可得' if data.get('available_at') else '可得时间未知',
            })
        except (OSError, ValueError, KeyError, TypeError) as exc:
            errors.append({'source_kind': 'scan_window', 'scan_id': scan_id, 'file': str(path), 'error': str(exc)[:180]})

    # 2) Range archives (manifest sha256 enforced)
    for directory, manifest in _iter_range_manifests(root):
        req = manifest.get('request') or {}
        if req.get('interval') != interval:
            continue
        # Optional overlap filter when caller gave a window
        if first is not None and stop is not None:
            try:
                if timestamp(req.get('effective_end')) <= first or timestamp(req.get('effective_start')) >= stop:
                    continue
            except (ValueError, TypeError):
                pass
        for item in manifest.get('series') or []:
            if (item.get('market'), item.get('symbol')) != (market, symbol):
                continue
            filename = series_name({'market': market, 'symbol': symbol}, interval)
            try:
                raw_bytes = (directory / filename).read_bytes()
                expected_hash = item.get('sha256')
                actual_hash = _sha256_bytes(raw_bytes)
                if expected_hash and actual_hash != expected_hash:
                    raise ValueError('归档校验失败')
                data = json.loads(raw_bytes)
                for bar in data.get('raw') or []:
                    if not bar_valid(bar, step):
                        raise ValueError('归档K线格式异常')
                for t in data.get('conflicted_open_times') or []:
                    if not isinstance(t, int):
                        continue
                    if first is not None and t < first:
                        continue
                    if stop is not None and t >= stop:
                        continue
                    conflicted.add(t)
                    merged.pop(t, None)
                accepted = 0
                for bar in data.get('raw') or []:
                    t = bar[0]
                    if first is not None and t < first:
                        continue
                    if stop is not None and t >= stop:
                        continue
                    if t in conflicted:
                        continue
                    if t in merged and not same_bar(merged[t], bar):
                        conflicted.add(t)
                        del merged[t]
                    else:
                        merged[t] = bar
                        accepted += 1
                evidence.append({
                    'source_kind': 'candle_range',
                    'range_id': manifest.get('range_id'),
                    'available_at': item.get('available_at') or manifest.get('available_at'),
                    'sha256': actual_hash,
                    'bars_accepted': accepted,
                    'availability': '历史当时可得' if (item.get('available_at') or manifest.get('available_at')) else '可得时间未知',
                })
            except (OSError, ValueError, KeyError, TypeError) as exc:
                errors.append({
                    'source_kind': 'candle_range',
                    'range_id': manifest.get('range_id'),
                    'error': str(exc)[:180],
                })

    # 3) Shared 1H data, preserving original quote-volume unit and lineage.
    from market_candle_cache import MarketCandleCache
    cache=MarketCandleCache(root,readonly=True)
    if interval=='1h' and cache.db_path.exists():
        try:
            for r in cache.get_bars(market=market,symbol=symbol,interval=interval,start_open=first,end_open=stop):
                p=r['payload'];b=p['bar'];t=p['open_time']
                bar=[t,b['o'],b['h'],b['l'],b['c'],b.get('base_v',0),t+step-1,b['q']]
                if t in conflicted:continue
                if t in merged and not same_bar(merged[t],bar):
                    conflicted.add(t);merged.pop(t,None)
                else:merged[t]=bar
            evidence.append(dict(source_kind='shared_1h',available_at=None,availability='当前可得；逐根首次可得见共享索引'))
        except (OSError,ValueError,KeyError,TypeError) as exc:errors.append(dict(source_kind='shared_1h',error=str(exc)))

    known_times = sorted(merged)
    # Coverage window: explicit query, else known span (catalog continuity only)
    if first is not None and stop is not None:
        cov_first, cov_stop = first, stop
        # Align like candle_ranges.validate
        cov_first = ((first + step - 1) // step) * step
        cov_stop = (stop // step) * step
        if cov_stop <= cov_first:
            cov = {
                'expected': 0, 'known': 0, 'missing': 0, 'coveragePct': 0.0,
                'complete': False, 'gap_count': 0, 'gaps': [], 'gaps_truncated': False,
            }
        else:
            cov = coverage(merged, cov_first, cov_stop, step)
        window = {'start': date(cov_first), 'end': date(cov_stop), 'mode': 'query'}
    elif known_times:
        cov_first, cov_stop = known_times[0], known_times[-1] + step
        cov = coverage(merged, cov_first, cov_stop, step)
        window = {'start': date(cov_first), 'end': date(cov_stop), 'mode': 'known_span'}
    else:
        cov = {
            'expected': 0, 'known': 0, 'missing': 0, 'coveragePct': 0.0,
            'complete': False, 'gap_count': 0, 'gaps': [], 'gaps_truncated': False,
        }
        window = {'start': None, 'end': None, 'mode': 'empty'}

    cont = continuity(known_times, step)
    earliest_source = min((e.get('available_at') for e in evidence if e.get('available_at')), default=None)
    latest_source = max((e.get('available_at') for e in evidence if e.get('available_at')), default=None)

    result = {
        'market': market,
        'symbol': symbol,
        'interval': interval,
        'window': window,
        **cov,
        'conflicts': len(conflicted),
        'conflicted_open_times': [date(t) for t in sorted(conflicted)[:50]],
        'continuity': cont,
        'evidence': evidence,
        'errors': errors,
        'current_available': bool(known_times),
        'label_current': '当前可得' if known_times else '当前不可得',
        'historical_availability': {
            'earliest_source_available_at': earliest_source,
            'latest_source_available_at': latest_source,
            'note': '各来源available_at是写入磁盘的时间；后补区间价格属于当前可得，不能当作历史当时已得的入场证据',
        },
        'checked_at': dt.datetime.now(UTC).isoformat(timespec='milliseconds'),
        'scope': (
            '只读合并：扫描窗口 + 区间补查；哈希校验（补查manifest）/格式校验；'
            '同开盘时间矛盾排除；不填零；不宣称六历史指标或ATH完整'
        ),
    }
    if include_bars:
        result['bars'] = [merged[t] for t in known_times]
        result['bars_note'] = (
            'OHLCV raw：[openTime,o,h,l,c,...]；冲突时刻已排除；'
            '供 EVAL-01 撮合核价，不填零、不臆造断档'
        )
    return result


def preflight_merged(root, value, clock=None):
    """Same request shape as CandleRanges.preflight, but includes scan-window candles."""
    from candle_ranges import validate
    clock = clock or (lambda: dt.datetime.now(UTC))
    spec = validate(root, value, clock())
    first = timestamp(spec['effective_start'])
    stop = timestamp(spec['effective_end'])
    output = []
    for contract in spec['contracts']:
        detail = merge_series(root, contract['market'], contract['symbol'], spec['interval'],
                              start=spec['effective_start'], end=spec['effective_end'])
        output.append({
            **contract,
            'expected': detail['expected'],
            'known': detail['known'],
            'missing': detail['missing'],
            'coveragePct': detail['coveragePct'],
            'complete': detail['complete'],
            'gap_count': detail['gap_count'],
            'gaps': detail['gaps'],
            'gaps_truncated': detail['gaps_truncated'],
            'conflicts': detail['conflicts'],
            'continuity': detail['continuity'],
            'evidence': detail['evidence'],
            'errors': detail['errors'],
            'label_current': detail['label_current'],
            'historical_availability': detail['historical_availability'],
        })
    return {
        'request': spec,
        'series': output,
        'complete': all(r['complete'] for r in output),
        'checked_at': clock().isoformat(),
        'scope': (
            '当前可得覆盖：已合并扫描窗口K线与区间补查归档；'
            '校验哈希/冲突排除/缺口；旧样本缺失处仍标不可得，不能回填成历史当时可得；'
            '局部价格覆盖不等于六历史指标、箱体或可信ATH'
        ),
    }
