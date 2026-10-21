"""Versioned scan storage. Public evidence only; no network or trading."""
from durable_io import protected
import datetime as dt
import hashlib
import json
import math
import os
import uuid
from collections import Counter
from pathlib import Path


def utcnow():
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec='microseconds')


def clean(value):
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if isinstance(value, dict):
        return {k: clean(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [clean(v) for v in value]
    return value


def encoded(value):
    return json.dumps(clean(value), ensure_ascii=False, indent=2, allow_nan=False).encode('utf-8')


def atomic(path, content):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + '.' + uuid.uuid4().hex + '.tmp')
    try:
        with temporary.open('xb') as output:
            output.write(content)
            output.flush()
            os.fsync(output.fileno())
        from durable_io import replace_atomic
        replace_atomic(temporary,path)
    finally:
        temporary.unlink(missing_ok=True)


class ScanLease:
    """Cross-process lock; never silently steal an orphaned lock."""
    def __init__(self, root):
        self.path = Path(root) / 'scan.lock'

    def __enter__(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        try:
            self.file = self.path.open('x', encoding='utf-8')
        except FileExistsError as exc:
            raise RuntimeError('扫描锁已存在；检查在途扫描/孤立锁，不重复启动。') from exc
        self.file.write(json.dumps({'pid': os.getpid(), 'startedAt': utcnow()}))
        self.file.flush()
        return self

    def __exit__(self, *args):
        self.file.close()
        self.path.unlink(missing_ok=True)


def assess(rows, expected, minimum=0.9):
    """Row and critical-field coverage; missing market caps/netflow stay unknown."""
    keys = [(r['market'], r['symbol']) for r in rows]
    if len(keys) != len(set(keys)):
        raise ValueError('同一次扫描出现重复合约')
    if expected <= 0 or len(rows) > expected:
        raise ValueError('目录为空或返回数量异常，保留旧快照')
    coverage = {'rows': len(rows) / expected}
    from scan_data_policy import usd_exempt
    exempted = sum(usd_exempt(r) for r in rows)
    for field in ('oiUsd', 'accountRatio', 'closed4h'):
        considered = [r for r in rows if field != 'oiUsd' or not usd_exempt(r)]
        denominator = expected - exempted if field == 'oiUsd' else expected
        coverage[field] = sum(isinstance(r.get(field), (int, float)) and not isinstance(r.get(field), bool)
                               and math.isfinite(r[field]) for r in considered) / denominator if denominator else 1.
    accepted = all(v >= minimum for v in coverage.values())
    return {'accepted': accepted, 'minimum': minimum, 'ratios': coverage, 'usd_conversion_excluded': exempted,
            'status': ('success' if all(v == 1 for v in coverage.values()) else 'partial_success') if accepted else 'failed'}


def aggregate_oi(rows, universe):
    expected = Counter((u[0]['baseAsset'] if isinstance(u, tuple) else u['asset']) for u in universe)
    # Caller supplies normalized asset metadata, not contract multipliers.
    groups = {}
    for r in rows:
        groups.setdefault(r['asset'], []).append(r)
    for asset, group in groups.items():
        values = [r['oiUsd'] for r in group if isinstance(r.get('oiUsd'), (int, float)) and math.isfinite(r['oiUsd'])]
        total = sum(values) if values else None
        complete = len(values) == expected[asset]
        for r in group:
            r['assetOiCoverage'] = {'expected': expected[asset], 'received': len(group), 'known': len(values), 'complete': complete}
            r['binanceAssetOiPartialUsd'] = total
            r['binanceAssetOiUsd'] = total if complete else None
            r['binanceAssetOiCapPct'] = total / r['capUsd'] * 100 if complete and r.get('capUsd', 0) and r['capUsd'] > 0 else None


class ScanArchive:
    def __init__(self, root):
        self.root = Path(root)
        self.scan_id = 'scan-' + uuid.uuid4().hex
        self.path = self.root / 'scan-history' / self.scan_id
        self.path.mkdir(parents=True)
        self.started = utcnow()
        self.observations = {}
        self.revision = 0
        self.status('running')

    @classmethod
    def open(cls, root, scan_id):
        """Reopen published archive after restart without creating a new scan."""
        import re
        if not re.fullmatch(r'scan-[a-f0-9]{32}',scan_id): raise ValueError('无效scan_id')
        obj=cls.__new__(cls)
        obj.root=Path(root)
        obj.scan_id=scan_id
        obj.path=obj.root/'scan-history'/scan_id
        obj.path.resolve().relative_to((obj.root/'scan-history').resolve())
        state=json.loads((obj.path/'status.json').read_bytes())
        obj.revision=state.get('revision',0)
        if not obj.revision: raise ValueError('此扫描尚无已发布revision')
        obj.started=state['startedAt']
        obj.observations={}
        for line in (obj.path/'observations.jsonl').read_text(encoding='utf-8').splitlines():
            row=json.loads(line);obj.observations[row['market']+'|'+row['symbol']]=row
        return obj

    def status(self, state, **extra):
        status_path = self.path / 'status.json'
        previous = json.loads(status_path.read_bytes()) if status_path.exists() else {}
        atomic(status_path, encoded({**previous, 'scan_id': self.scan_id, 'status': state, 'startedAt': self.started, 'updatedAt': utcnow(), **extra}))

    def record(self, row):
        if self.revision: raise RuntimeError('已发布扫描不能改写原始观测')
        row = clean(row)
        key = row['market'] + '|' + row['symbol']
        row.update(scan_id=self.scan_id, observed_at=row.get('observed_at', utcnow()), observation_key=key)
        self.observations[key] = row
        atomic(self.path / 'observations.jsonl', b''.join(encoded(r).replace(b'\n', b'') + b'\n' for r in self.observations.values()))

    def candles(self, row, interval, raw):
        if self.revision: raise RuntimeError('已发布扫描不能改写原始K线')
        if not raw:
            return None
        filename = hashlib.sha256((row['market'] + '|' + row['symbol']).encode()).hexdigest() + '-' + interval + '.json'
        path = self.path / 'candles' / filename
        available = utcnow()
        from shared_candles import write as write_shared
        write_shared(self.root, path, {'market': row['market'], 'symbol': row['symbol'], 'interval': interval,
                            'source': 'Binance public REST', 'retrieved_at': available, 'available_at': available,
                            'source_time': raw[-1][6], 'count': len(raw), 'scope': 'returned closed window; not verified lifetime ATH',
                            'raw': raw})
        return str(path.relative_to(self.root)).replace('\\', '/')

    @protected
    def publish(self, result, minimum=0.9):
        result = clean(result)
        from asset_identity import cap_coverage
        result['capCoverage'] = cap_coverage(result['rows'], result.get('expectedCapContracts'))
        quality = assess(result['rows'], result['total'], minimum)
        if not quality['accepted']:
            self.status('failed', quality=quality)
            raise ValueError('扫描覆盖未达门槛，已保存失败记录，保留上次成功快照')
        caps = result['capCoverage']
        quality['cap_required'] = caps['required_contracts']
        quality['cap_trusted'] = caps['required_trusted_value']
        quality['cap_complete'] = caps['all_required_trusted']
        if caps['required_contracts'] and not caps['all_required_trusted']:
            quality['status'] = 'partial_success'
            quality['message'] = f"市值未齐全：可信{caps['required_trusted_value']}/{caps['required_contracts']}；缺口见capCoverage"
        available = utcnow()
        self.revision += 1
        result.update(scan_id=self.scan_id, revision=self.revision, schemaVersion=2,
                      available_at=available, quality=quality, archive=str(self.path.relative_to(self.root)).replace('\\', '/'))
        for r in result['rows']:
            r.update(scan_id=self.scan_id, revision=self.revision)
            r.setdefault('fieldAvailableAt', {})
            for field in r:
                if field != 'fieldAvailableAt':
                    r['fieldAvailableAt'].setdefault(field, available)
        payload = encoded(result)
        # Preserve any pre-versioning sample exactly, without pretending it was
        # historically available before this import.
        latest = self.root / 'scan.json'
        if latest.exists():
            previous = latest.read_bytes()
            legacy = self.root / 'scan-history' / 'legacy' / (hashlib.sha256(previous).hexdigest() + '.json')
            if not json.loads(previous).get('scan_id') and not legacy.exists():
                atomic(legacy, previous)
                old_observations = self.root / 'observations.jsonl'
                if old_observations.exists():
                    raw_observations = old_observations.read_bytes()
                    atomic(legacy.parent / ('observations-' + hashlib.sha256(raw_observations).hexdigest() + '.jsonl'),raw_observations)
                atomic(legacy.with_suffix('.metadata.json'),encoded({'imported_at':utcnow(),'provenance':'legacy bytes preserved; historical field availability not reconstructed'}))

        from tracking_service import prepare_publication
        prepare_publication(self.root,result,payload)
        immutable = self.path / f'revision-{self.revision:04d}.json'
        with immutable.open('xb') as output:
            output.write(payload)
            output.flush()
            os.fsync(output.fileno())
        atomic(latest, payload)
        # Compatibility file is latest-run only; immutable observations remain per scan.
        atomic(self.root / 'observations.jsonl', (self.path / 'observations.jsonl').read_bytes() if self.observations else b'')
        self.status(quality['status'], quality=quality, revision=self.revision)
        from tracking_service import publication_hook
        publication_hook(self.root,result)
        return result

    @protected
    def revise(self, changes):
        with ScanLease(self.root):
            latest = json.loads((self.root / 'scan.json').read_bytes())
            if latest.get('scan_id') != self.scan_id or latest.get('revision') != self.revision:
                raise ValueError('不允许旧扫描补核覆盖更新的最新扫描')
            return self._revise(changes)

    def _revise(self, changes, verification=None):
        """Only known contract patches; caller holds ScanLease. Old revisions never change."""
        previous = json.loads((self.path / f'revision-{self.revision:04d}.json').read_bytes())
        from strategy_registry import VERSION_HASH
        if previous.get('registryHash') not in (None,VERSION_HASH):
            raise ValueError('规则版本已变更，不能在数据补核中混合新旧策略')
        by_key = {(r['market'], r['symbol']): r for r in previous['rows']}
        available = utcnow()
        for patch in changes:
            key = (patch['market'], patch['symbol'])
            if key not in by_key:
                raise ValueError('补核不允许创建未扫描合约')
            if any(k in patch for k in ('scan_id', 'revision', 'fieldAvailableAt', 'asset', 'observed_at', 'candleArchives')):
                raise ValueError('补核不能覆盖版本/可得时间元数据')
            for field, value in clean(patch).items():
                if field not in ('market', 'symbol'):
                    by_key[key][field] = value
                    by_key[key]['fieldAvailableAt'][field] = available
            r = by_key[key]
            from scanner import classify, number
            oi, cap = number(r.get('oiUsd')), number(r.get('capUsd'))
            r['oiCapPct'] = oi / cap * 100 if oi is not None and cap is not None and cap > 0 else None
            classify(r)
            for field in ('oiCapPct', 'signals', 'knownCount', 'hitCount', 'strategyCandidates', 'riskFlags', 'state'):
                r['fieldAvailableAt'][field] = available
        counts = previous.get('expectedAssetContracts', {})
        if not counts:
            # Missing catalog provenance never upgrades a partial sum to complete.
            counts = {asset: max((r.get('assetOiCoverage', {}).get('expected', 0) for r in group), default=0)
                      or len(group) + 1 for asset, group in
                      ((a, [r for r in previous['rows'] if r['asset'] == a]) for a in {r['asset'] for r in previous['rows']})}
        aggregate_oi(previous['rows'], [{'asset': a} for a, count in counts.items() for _ in range(count)])
        for r in previous['rows']:
            for field in ('assetOiCoverage', 'binanceAssetOiUsd', 'binanceAssetOiPartialUsd', 'binanceAssetOiCapPct'):
                r['fieldAvailableAt'][field] = available
        previous['candidateCount'] = sum(all(v is True for v in r.get('signals', [])[:3]) and len(r.get('signals', [])) >= 3 for r in previous['rows'])
        previous['capSource'] = '；'.join(sorted({r['capSource'] for r in previous['rows'] if r.get('capSource')}))
        previous['enrichedAt'] = available
        if verification is not None:
            previous['verification'] = verification
        return self.publish(previous)
