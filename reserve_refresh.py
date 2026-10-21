"""Refresh public reserve evidence once per scan; never replace precise netflow."""
import copy
import datetime as dt
import json
import time
import urllib.request
from pathlib import Path

from cex_reserves import NATIVE, URL, derive
from snapshot_store import atomic, encoded

UTC = dt.timezone.utc
RETRY_SECONDS = 3600
MAX_BYTES = 96 * 1024 * 1024


def fetch_source():
    deadline = time.monotonic() + 30
    request = urllib.request.Request(URL, headers={'User-Agent': 'EvidencePanel/1.0'})
    chunks, size = [], 0
    with urllib.request.urlopen(request, timeout=15) as response:
        while True:
            chunk = response.read(1024 * 1024)
            if not chunk:
                break
            size += len(chunk)
            if size > MAX_BYTES or time.monotonic() > deadline:
                raise ValueError('储备来源响应过大或超时')
            chunks.append(chunk)
    source = json.loads(b''.join(chunks))
    if not isinstance(source, dict) or source.get('name') != 'Binance CEX' or not isinstance(source.get('chainTvls'), dict):
        raise ValueError('储备来源身份或schema不符')
    return source


def _timestamp(value):
    try:
        parsed = dt.datetime.fromisoformat(value.replace('Z', '+00:00'))
        return parsed.timestamp() if parsed.tzinfo else None
    except (AttributeError, ValueError, TypeError):
        return None


def _read(path):
    try:
        value = json.loads(Path(path).read_bytes())
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


def _valid_bundle(value):
    return (isinstance(value, dict) and value.get('source') == 'DefiLlama Binance CEX' and value.get('endpoint') == URL
            and isinstance(value.get('records'), list) and _timestamp(value.get('retrievedAt')) is not None)


def _summarize(bundle, scan, at):
    result = copy.deepcopy(bundle)
    assets = {r['asset'] for r in scan['rows']}
    records = []
    native = {asset for asset, _ in NATIVE.values()}
    for record in result.get('records', []):
        if not isinstance(record, dict) or record.get('asset') not in assets & native:
            continue
        try:
            age = (at.timestamp() - record['asOf']) / 3600
            fresh = 0 <= age <= 36 and record.get('freshAtRetrieval') is True
        except (TypeError, KeyError):
            age, fresh = None, False
        record.update(ageHoursNow=age, freshNow=fresh, eligibleForExactNetflow=False)
        records.append(record)
    result['records'] = records
    covered = {r['asset'] for r in records if r['freshNow']}
    result.update(freshAssets=len(covered), contractReferences=sum(r['asset'] in covered for r in scan['rows']),
                  totalContracts=len(scan['rows']), exactNetflowCoverage=0)
    result['uncoveredAssets'] = sorted(assets - covered)
    return result


def unavailable():
    return {'source': 'DefiLlama Binance CEX', 'endpoint': URL, 'records': [], 'failures': [],
            'retrievedAt': None, 'freshAssets': 0, 'contractReferences': 0, 'exactNetflowCoverage': 0,
            'status': 'not_collected', 'updateError': None,
            'meaning': 'labelled reserve quantity change, not transfer netflow'}


def refresh(root, scan, *, at=None, fetch=None):
    """Caller holds ScanLease. Cache changes never publish a new scan revision."""
    root = Path(root)
    at = at or dt.datetime.now(UTC)
    stamp = at.isoformat()
    boundary = int(at.replace(hour=0, minute=0, second=0, microsecond=0).timestamp())
    path = root / 'runtime' / 'reserve-refresh.json'
    state = _read(path)
    previous = state.get('lastSuccess') or {}
    if not _valid_bundle(previous):
        previous = _read(root / 'cex-reserves.json')
    if not _valid_bundle(previous):
        previous = unavailable()
    assets = sorted({r['asset'] for r in scan['rows']})
    eligible = set(assets) & {asset for asset, _ in NATIVE.values()}
    summary = _summarize(previous, scan, at)
    current = {r['asset'] for r in summary['records'] if r['freshNow'] and r.get('asOf') == boundary}
    attempted = _timestamp(state.get('lastAttemptAt'))
    same_day = attempted is not None and int(attempted // 86400) == boundary // 86400
    throttled = same_day and 0 <= at.timestamp() - attempted < RETRY_SECONDS and state.get('assets') == assets
    complete_today = (eligible and eligible <= current and not state.get('lastError')
                      and state.get('assets') == assets)
    attempted_now = False
    error = state.get('lastError')
    cache_error = None
    if not complete_today and not throttled:
        attempted_now = True
        try:
            source = (fetch or fetch_source)()
            if not isinstance(source, dict) or source.get('name') != 'Binance CEX' or not isinstance(source.get('chainTvls'), dict):
                raise ValueError('储备来源身份或schema不符')
            candidate = derive(source, scan, at)
            if not candidate['records']:
                raise ValueError('来源未返回连续8日有效原生币余额')
            prior_dates = {r['asset']: r['asOf'] for r in summary['records']}
            if any(r['asOf'] < prior_dates.get(r['asset'], 0) for r in candidate['records']):
                raise ValueError('供应商数据日期倒退，保留上次成功证据')
            previous = candidate
            summary = _summarize(previous, scan, at)
            error = None
        except Exception as exc:
            error = type(exc).__name__ + ': ' + str(exc)[:240]
        state = {'lastAttemptAt': stamp, 'lastError': error, 'assets': assets, 'lastSuccess': previous}
        try:
            atomic(path, encoded(state))
        except OSError as exc:
            cache_error = '储备缓存保存失败: ' + str(exc)[:180]
    if error:
        status = 'update_failed' if summary['records'] else 'failed'
    elif not summary['freshAssets']:
        status = 'stale'
    elif not eligible <= {r['asset'] for r in summary['records'] if r['freshNow']}:
        status = 'partial_success'
    else:
        status = 'success'
    summary.update(status=status, checkedAt=stamp, lastAttemptAt=state.get('lastAttemptAt'),
                   lastSuccessAt=previous.get('retrievedAt'), updateError=error, cacheError=cache_error,
                   cacheUsed=not attempted_now or bool(error), attemptedThisScan=attempted_now)
    return summary


def for_row(bundle, row, at):
    """Freeze per-contract provenance into research input and its evidence hash."""
    summary = _summarize(bundle or unavailable(), {'rows': [row]}, at)
    record = next(iter(summary['records']), None)
    if record:
        # Preview identity must not change every second due to a computed age.
        record.pop('ageHoursNow', None)
    return {'record': record, 'freshNow': bool(record and record.get('freshNow')),
            'source': summary['source'], 'sourceEndpoint': summary['endpoint'],
            'retrievedAt': summary.get('retrievedAt'), 'checkedAt': summary.get('checkedAt'),
            'status': summary.get('status'), 'updateError': summary.get('updateError'),
            'scope': summary.get('scope'), 'limits': summary.get('limits'),
            'usage': 'current_auxiliary_only' if record and record.get('freshNow') else 'historical_or_missing_not_current',
            'exactNetflow': False, 'combinedSignal': False}
