"""Bounded recovery summary; old ledger rows remain immutable evidence.

This projection never marks acquisition/effects resolved. GET reads its stored
value only; the existing single recovery worker refreshes it transactionally.
"""
import json

TERMINAL=('evaluated','resolved','complete','cancelled')


def refresh(db, *, clock_ms, window_hours):
    tables={r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    if 'recovery_origin_links' not in tables:
        db.execute('CREATE TABLE recovery_origin_links(origin_key TEXT NOT NULL,job_key TEXT NOT NULL,origin_json TEXT NOT NULL,PRIMARY KEY(origin_key,job_key))')
    db.execute('CREATE TABLE IF NOT EXISTS recovery_bridge_results(legacy_key TEXT PRIMARY KEY,canonical_key TEXT,state TEXT NOT NULL,reason TEXT,registry_fingerprint TEXT NOT NULL,checked_ms INTEGER NOT NULL,next_retry_ms INTEGER)')
    # A source link alone isn't enough. Every legacy consumer needs its exact
    # destination edge; incomplete bridges continue to appear as unresolved.
    mapped="""j.provider IS NULL AND EXISTS (
        SELECT 1 FROM recovery_origin_links l JOIN tracking_recovery n ON n.job_key=l.job_key
        WHERE l.origin_key=j.job_key AND n.provider IS NOT NULL
        AND EXISTS(SELECT 1 FROM recovery_bridge_results b WHERE b.legacy_key=j.job_key AND b.canonical_key=n.job_key AND b.state='verified')
        AND n.contract=j.contract AND n.source=j.source
        AND n.start_ms<=j.start_ms AND n.target_ms>=j.target_ms
        AND NOT EXISTS (
          SELECT 1 FROM recovery_dependencies d WHERE d.job_key=j.job_key
          AND NOT EXISTS (SELECT 1 FROM recovery_dependencies e
            WHERE e.job_key=n.job_key AND e.consumer=d.consumer AND e.rule_version=d.rule_version
            AND e.event_id IS d.event_id AND e.step_ms=d.step_ms
            AND e.start_ms<=d.start_ms AND e.target_ms>=d.target_ms)))"""
    # This table is orders of magnitude smaller than samples/chunks; don't scan
    # either payload table. Preserve a strict read-work bound on the projection.
    db.execute('CREATE INDEX IF NOT EXISTS collection_origin_target ON recovery_origin_links(job_key,origin_key)')
    db.execute('CREATE TEMP TABLE collection_effective_needs AS SELECT j.*, CASE WHEN '+mapped+' THEN 1 ELSE 0 END AS bridged FROM tracking_recovery j LIMIT 50001')
    try:
        n=db.execute('SELECT COUNT(*) FROM collection_effective_needs').fetchone()[0]
        if n>50000:return dict(status='page_required',as_of_ms=clock_ms,reason='recovery_needs_exceed_projection_limit')
        counts=[dict(r) for r in db.execute('SELECT source,state,COUNT(*) AS count FROM collection_effective_needs WHERE bridged=0 GROUP BY source,state ORDER BY source,state')]
        excluded=db.execute('SELECT COUNT(*) FROM collection_effective_needs WHERE bridged=1').fetchone()[0]
        cutoff=clock_ms-window_hours*3600000
        outside=[dict(r) for r in db.execute("SELECT source,COUNT(*) AS count,MIN(target_ms) AS oldest_target_ms FROM collection_effective_needs WHERE bridged=0 AND provider IS NOT NULL AND state IN ('pending','backoff','pending_authorization') AND target_ms<? GROUP BY source",(cutoff,))]
        consumers=[dict(r) for r in db.execute("SELECT d.state,COUNT(*) AS count FROM recovery_dependencies d JOIN collection_effective_needs j USING(job_key) WHERE j.bridged=0 GROUP BY d.state")]
        affected=db.execute("SELECT COUNT(DISTINCT contract) FROM collection_effective_needs WHERE bridged=0 AND state NOT IN ('evaluated','resolved','complete','cancelled')").fetchone()[0]
        reasons=[dict(r) for r in db.execute("SELECT source,error_code,COUNT(*) AS count FROM collection_effective_needs WHERE bridged=0 AND error_code IS NOT NULL GROUP BY source,error_code ORDER BY COUNT(*) DESC LIMIT 24")]
        mapping_blockers=[dict(r) for r in db.execute("SELECT j.source,b.reason,COUNT(*) AS count,MIN(b.next_retry_ms) AS next_retry_ms FROM collection_effective_needs j JOIN recovery_bridge_results b ON b.legacy_key=j.job_key WHERE j.bridged=0 AND b.state='blocked' GROUP BY j.source,b.reason ORDER BY COUNT(*) DESC LIMIT 24")]
        value=dict(schema='collection-effective-needs-v1',status='ready',as_of_ms=clock_ms,as_of_seq=(db.execute("SELECT value FROM meta WHERE key='recovery_commit_seq'").fetchone() or (None,))[0],source_counts=counts,
            legacy_bridged=excluded,registered_rows=n,affected_contracts=affected,consumer_counts=consumers,
            outside_automatic_window=outside,automatic_window_hours=window_hours,
            outside_window_action='retained_waiting_historical_repair_not_automatically_expanded',
            reasons=reasons,mapping_blockers=mapping_blockers,count_unit='source_demands_not_contracts_or_candle_roots',
            effect_ack_required=True)
        db.execute("INSERT INTO meta VALUES('collection_effective_projection',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",(json.dumps(value,ensure_ascii=False),))
        return value
    finally:db.execute('DROP TABLE collection_effective_needs')
