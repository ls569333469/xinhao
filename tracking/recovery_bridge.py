"""Thin bridge into the existing recovery queue; no second acquisition ledger."""
from __future__ import annotations
import hashlib
import json
from .ledger import _canonical
from .recovery import Recovery, SourceFailure
from .recovery_replay import bridge_need
from .live_source import VERSION, contract_metadata

H = 3600000


def acquisition_quality(meta):
    """Shared immutable identity generation for acquisition and normal consumption."""
    identity={k:meta.get(k) for k in ('quote_asset','pair','contract_size','oi_conversion','cap','history_policy')}
    return 'live-quality-v3-' + hashlib.sha256(_canonical(identity).encode()).hexdigest()


def enqueue_verified(recovery, registry, *, contract, source, start_ms, target_ms, clock_ms):
    meta=registry.get(contract) or {}
    if source=='cap':
        # A token's USD cap is independent of the derivative's quote conversion.
        # Still require the exact verified contract and its covered time range.
        market,symbol=contract.split('|',1)
        cfg=meta.get('cap') or {}
        if meta.get('verified') is not True or not meta.get('identity_evidence') or not cfg.get('verified'):raise SourceFailure('contract_identity_unverified')
        if type(meta.get('valid_from_ms')) is not int or type(meta.get('valid_to_ms')) is not int or not meta['valid_from_ms']<=start_ms<=target_ms<=meta['valid_to_ms']:raise SourceFailure('contract_metadata_outside_history')
    else:market,symbol,meta=contract_metadata(registry,contract,start_ms,target_ms)
    inverse = market == 'COIN-M'
    if source == 'cap':
        cfg = meta.get('cap') or {}
        if not cfg.get('verified') or not cfg.get('id') or not cfg.get('identity_evidence'):
            raise SourceFailure('cap_history_provider_not_configured')
        from .live_source import cap_endpoint
        provider,endpoint=cap_endpoint(cfg)
        semantics='asof_snapshot'
        period, unit = H, 'USD'
        if start_ms != target_ms:
            raise ValueError('cap_requires_single_asof_window')
    else:
        host = 'https://dapi.binance.com' if inverse else 'https://fapi.binance.com'
        endpoint = host + (('/dapi/v1/klines' if inverse else '/fapi/v1/klines') if source in ('candles_1h','candles_4h') else '/futures/data/' + ('openInterestHist' if source == 'oi' else 'globalLongShortAccountRatio'))
        provider, semantics = 'binance', 'effective_at_ms'
        period, unit = (4*H if source=='candles_4h' else H, 'USD nominal quote') if source in ('candles_1h','candles_4h') else (H, 'USD' if source == 'oi' else 'ratio')
    # Unit/identity corrections cannot accidentally share a pre-correction download.
    quality = acquisition_quality(meta)
    # A previously fixed compatible window can satisfy an inner need. Preserve
    # both consumer identities without downloading overlapping sources twice.
    with recovery.transaction() as db:
        prior=db.execute("SELECT job_key FROM tracking_recovery WHERE contract=? AND source=? AND provider=? AND endpoint=? AND period_ms=? AND time_semantics=? AND unit_identity=? AND adapter_version=? AND quality_version=? AND start_ms<=? AND target_ms>=? ORDER BY start_ms DESC,target_ms,job_key LIMIT 1",
            (contract,source,provider,endpoint,period,semantics,unit,VERSION,quality,start_ms,target_ms)).fetchone()
    if prior:return prior[0]
    return recovery.enqueue(source=source, market=market, symbol=symbol,
        timeframe='4h' if source == 'candles_4h' else '1h', start_ms=start_ms, target_ms=target_ms,
        provider=provider, endpoint=endpoint, period_ms=period, time_semantics=semantics,
        unit_identity=unit, adapter_version=VERSION, quality_version=quality,
        queue_class='recent' if target_ms >= clock_ms-24*H else 'historical', clock_ms=clock_ms)


def bridge_page(ledger, registry, *, after='', limit=50, clock_ms):
    if type(limit) is not int or not 1 <= limit <= 100:
        raise ValueError('bridge_limit')
    recovery = Recovery(ledger)
    with recovery.transaction() as db:
        rows = [dict(r) for r in db.execute("SELECT * FROM tracking_recovery WHERE provider IS NULL AND job_key>? ORDER BY job_key LIMIT ?", (after, limit))]
    with recovery.transaction() as db:
        db.execute('CREATE TABLE IF NOT EXISTS recovery_bridge_cache(legacy_key TEXT PRIMARY KEY,fingerprint TEXT NOT NULL)')
        db.execute('CREATE TABLE IF NOT EXISTS recovery_bridge_results(legacy_key TEXT PRIMARY KEY,canonical_key TEXT,state TEXT NOT NULL,reason TEXT,registry_fingerprint TEXT NOT NULL,checked_ms INTEGER NOT NULL,next_retry_ms INTEGER)')
    registry_fingerprint=hashlib.sha256(_canonical(registry).encode()).hexdigest()
    items = []
    for old in rows:
        with recovery.transaction() as db:
            dependencies = [dict(r) for r in db.execute('SELECT * FROM recovery_dependencies WHERE job_key=? ORDER BY dependency_id LIMIT 101', (old['job_key'],))]
            contexts=[tuple(r) for r in db.execute('SELECT d.dependency_id,c.input_sha FROM recovery_dependencies d JOIN recovery_replay_contexts c ON c.dependency_id=d.dependency_id AND c.epoch=d.input_epoch WHERE d.job_key=? ORDER BY d.dependency_id LIMIT 101',(old['job_key'],))]
            signature=hashlib.sha256(_canonical([registry_fingerprint,old['source'],old['contract'],old['start_ms'],old['target_ms'],[{k:d[k] for k in ('dependency_id','consumer','rule_version','event_id','start_ms','target_ms','step_ms','input_epoch')} for d in dependencies],contexts]).encode()).hexdigest()
            prior=db.execute('SELECT r.* FROM recovery_bridge_results r JOIN recovery_bridge_cache c ON c.legacy_key=r.legacy_key WHERE r.legacy_key=? AND r.registry_fingerprint=? AND c.fingerprint=?',(old['job_key'],registry_fingerprint,signature)).fetchone()
        if prior and len(dependencies)<=100 and (prior['state']=='verified' or (prior['next_retry_ms'] or 0)>clock_ms):
            items.append(dict(legacy=old['job_key'],canonical=prior['canonical_key'],blocked=prior['reason'],cached=True,next_retry_ms=prior['next_retry_ms']));continue
        try:
            canonical = enqueue_verified(recovery, registry, contract=old['contract'], source=old['source'],
                start_ms=old['start_ms'], target_ms=old['target_ms'], clock_ms=clock_ms)
            if len(dependencies) > 100:
                raise ValueError('bridge_dependency_page_required')
            for dependency in dependencies:
                bridge_need(recovery, old['job_key'], canonical, consumer=dependency['consumer'],
                    rule_version=dependency['rule_version'], event_id=dependency['event_id'],
                    step_ms=dependency['step_ms'], clock_ms=clock_ms)
            # Legacy rows remain immutable facts. Link even no-event/quality-only needs.
            with recovery.transaction() as db:
                db.execute('CREATE TABLE IF NOT EXISTS recovery_origin_links(origin_key TEXT NOT NULL,job_key TEXT NOT NULL,origin_json TEXT NOT NULL,PRIMARY KEY(origin_key,job_key))')
                db.execute('INSERT OR IGNORE INTO recovery_origin_links VALUES(?,?,?)', (old['job_key'], canonical, _canonical(dict(kind='legacy_gap', source_job=old['job_key']))))
                db.execute('INSERT OR REPLACE INTO recovery_bridge_cache VALUES(?,?)',(old['job_key'],signature))
                db.execute('INSERT OR REPLACE INTO recovery_bridge_results VALUES(?,?,?,?,?,?,?)',(old['job_key'],canonical,'verified',None,registry_fingerprint,clock_ms,None))
            items.append(dict(legacy=old['job_key'], canonical=canonical, dependencies=len(dependencies)))
        except (ValueError, SourceFailure) as exc:
            with recovery.transaction() as db:
                db.execute('INSERT OR REPLACE INTO recovery_bridge_cache VALUES(?,?)',(old['job_key'],signature))
                db.execute('INSERT OR REPLACE INTO recovery_bridge_results VALUES(?,?,?,?,?,?,?)',(old['job_key'],None,'blocked',str(exc)[:300],registry_fingerprint,clock_ms,clock_ms+3600000))
            items.append(dict(legacy=old['job_key'], blocked=str(exc)))
    return dict(items=items, next_cursor=rows[-1]['job_key'] if len(rows) == limit else None)


def register_b_round(ledger, result):
    """Retain fixed source_resume gap ranges; task status/results are untouched.

    No grants, no requests, no 'resolved' states, even for a technical fail.
    A later explicit runtime bridges only verified contracts and authorized scope.
    """
    recovery = Recovery(ledger)
    origin = 'b-round:' + str(result['round_id'])
    clock = result['published_at']
    for detail in result.get('details', []):
        market = 'USDT-M' if detail['market'] == 'USDⓈ-M' else detail['market']
        contract = market + '|' + detail['symbol']
        for source, cursor in (detail.get('source_cursors') or {}).items():
            if source not in ('oi', 'lsr'):
                continue
            for gap in cursor.get('gaps') or []:
                start, end = gap['start'], gap['end']
                if type(start) is not int or type(end) is not int or end <= start:
                    continue
                job = recovery.enqueue(source=source, market=market, symbol=detail['symbol'], timeframe='1h',
                    start_ms=start, target_ms=end-H, clock_ms=clock)
                with recovery.transaction() as db:
                    db.execute('CREATE TABLE IF NOT EXISTS recovery_origin_links(origin_key TEXT NOT NULL,job_key TEXT NOT NULL,origin_json TEXT NOT NULL,PRIMARY KEY(origin_key,job_key))')
                    db.execute('INSERT OR IGNORE INTO recovery_origin_links VALUES(?,?,?)', (origin, job, _canonical(dict(
                        contract=contract, source=source, original_gap=gap, task_target=result['target_close_boundary'],
                        raw_coverage_status=result.get('coverage_status'), count_unit='source_ranges_not_samples'))))
        cc=detail.get('candle_coverage') or {}
        target=result['target_close_boundary']
        warmup_start=max(H,target-169*H)
        first=cc.get('first_open')
        if first is None or first+H>warmup_start:
            end=min(target,first if first is not None else target)
            if warmup_start<=end:
                recovery.enqueue(source='candles_1h',market=market,symbol=detail['symbol'],timeframe='1h',start_ms=warmup_start,target_ms=end,clock_ms=clock)
        for gap in cc.get('gaps',[]):
            start,end=gap['after']+2*H,min(gap['before'],result['target_close_boundary'])
            if start<=end:
                job=recovery.enqueue(source='candles_1h',market=market,symbol=detail['symbol'],timeframe='1h',start_ms=start,target_ms=end,clock_ms=clock)
                with recovery.transaction() as db:
                    db.execute('CREATE TABLE IF NOT EXISTS recovery_origin_links(origin_key TEXT NOT NULL,job_key TEXT NOT NULL,origin_json TEXT NOT NULL,PRIMARY KEY(origin_key,job_key))')
                    db.execute('INSERT OR IGNORE INTO recovery_origin_links VALUES(?,?,?)',(origin,job,_canonical(dict(contract=contract,source='candles_1h',original_gap=gap))))
        coverage=detail.get('coverage') or {}
        # Missing qualification is retained even without an event_id. Each
        # consumer root keeps its own fixed target; cap never becomes dense hourly.
        for ot in coverage.get('business_required_evidence_opens',coverage.get('evidence_unresolved_opens',[])):
            for boundary in (ot+H,ot+2*H):
                if boundary>result['target_close_boundary']:continue
                for source in ('oi','lsr','cap'):
                    cursor=(detail.get('source_cursors') or {}).get(source) or {}
                    if source!='cap' and any(g['start']<=boundary<g['end'] for g in cursor.get('gaps',[])):
                        continue
                    recovery.enqueue(source=source,market=market,symbol=detail['symbol'],timeframe='1h',
                        start_ms=boundary,target_ms=boundary,clock_ms=clock)
        for ot in coverage.get('unresolved_opens',[]):
            for boundary in sorted({((ot+H)//(4*H))*(4*H),((ot+2*H)//(4*H))*(4*H)}):
                if boundary<=0:continue
                # Frozen A/B background uses the exact 1499-root initialization.
                recovery.enqueue(source='candles_4h',market=market,symbol=detail['symbol'],timeframe='4h',
                    start_ms=max(4*H,boundary-1498*4*H),target_ms=boundary,clock_ms=clock)

