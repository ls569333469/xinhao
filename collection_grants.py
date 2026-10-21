"""Finite daily grants under the saved plan; display settings cannot renew them."""
import hashlib,json
DAY=86400000


def release_completed(recovery,*,clock_ms):
    """Release only unused reservations for fully archived jobs, never spent attempts."""
    with recovery.transaction() as db:
        if not db.execute("SELECT 1 FROM sqlite_master WHERE name='collection_grant_windows'").fetchone():return 0
        db.execute('CREATE TABLE IF NOT EXISTS collection_grant_releases(grant_id TEXT PRIMARY KEY,original_max INTEGER,released_to INTEGER,at_ms INTEGER)')
        rows=db.execute("""SELECT g.grant_id,g.max_attempts,g.attempts_used FROM collection_grant_windows w
          JOIN recovery_grants g USING(grant_id) JOIN tracking_recovery j ON j.job_key=w.job_key
          WHERE w.window_id=? AND j.state IN ('archived_awaiting_effect','evaluated','complete')
          AND g.max_attempts>MAX(g.attempts_used,1) AND g.revoked=0
          AND NOT EXISTS(SELECT 1 FROM recovery_grant_jobs b JOIN tracking_recovery k USING(job_key)
            WHERE b.grant_id=g.grant_id AND k.state NOT IN ('archived_awaiting_effect','evaluated','complete'))
          AND NOT EXISTS(SELECT 1 FROM recovery_attempts a WHERE a.grant_id=g.grant_id AND a.finished_ms IS NULL)
          LIMIT 100""",(clock_ms//DAY,)).fetchall()
        for row in rows:
            minimum=max(row['attempts_used'],1)
            db.execute('INSERT OR IGNORE INTO collection_grant_releases VALUES(?,?,?,?)',(row['grant_id'],row['max_attempts'],minimum,clock_ms))
            db.execute('UPDATE recovery_grants SET max_attempts=? WHERE grant_id=?',(minimum,row['grant_id']))
        if rows:
            db.execute("UPDATE tracking_recovery SET next_retry_ms=0 WHERE state='pending_authorization' AND error_code='saved_plan_daily_budget_reserved'")
            recovery.bump(db,clock_ms)
        return len(rows)


def saved_plan_grant(recovery,job,cfg,*,clock_ms):
    # Exactly the authorized fixed job and saved acquisition/budget settings.
    # Scope edits do not reset the cumulative plan's per-day cap below.
    scope=dict(providers=[job['provider']],contracts=[job['contract']],sources=[job['source']],start_ms=job['start_ms'],target_ms=job['target_ms'])
    fingerprint=hashlib.sha256(json.dumps({k:cfg[k] for k in ('recoveryWindowHours','recoveryAttempts','maxHttpAttempts')},sort_keys=True).encode()).hexdigest()
    window=clock_ms//DAY;starts=window*DAY;ends=starts+DAY
    key=hashlib.sha256(json.dumps([fingerprint,job['job_key'],window]).encode()).hexdigest()
    prefix='collection-saved-plan:'+str(window)+':'
    # Outer IMMEDIATE transaction makes budget allocation + grant creation + link
    # atomic; create_grant would open a second writer, so write its exact schema
    # here using the same existing Recovery ledger and validation below.
    with recovery.transaction() as db:
        db.execute('CREATE TABLE IF NOT EXISTS collection_grant_windows(scope_key TEXT PRIMARY KEY,grant_id TEXT NOT NULL,window_id INTEGER NOT NULL,plan_fingerprint TEXT NOT NULL,job_key TEXT NOT NULL)')
        row=db.execute('SELECT g.* FROM collection_grant_windows w JOIN recovery_grants g USING(grant_id) WHERE w.scope_key=?',(key,)).fetchone()
        if row:return row['grant_id'],None if not row['revoked'] and row['attempts_used']<row['max_attempts'] else 'saved_plan_window_revoked_or_exhausted'
        # Honor a still-live pre-window grant linked by the original collector.
        # Migration must not mint a second budget for its exact frozen scope.
        legacy_table=db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='collection_grants'").fetchone()
        if legacy_table:
            legacy=db.execute('SELECT g.* FROM collection_grants c JOIN recovery_grants g USING(grant_id) WHERE g.scope_json=? AND (g.revoked=1 OR (g.starts_ms<=? AND g.expires_ms>?)) ORDER BY g.revoked DESC,g.starts_ms DESC LIMIT 1',(json.dumps(scope,sort_keys=True,separators=(',',':')),clock_ms,clock_ms)).fetchone()
            if legacy:
                if legacy['revoked']:return None,'saved_plan_revoked'
                if legacy['attempts_used']>=legacy['max_attempts']:return legacy['grant_id'],'saved_plan_legacy_window_exhausted'
                return legacy['grant_id'],None
        # Explicit revocation of ANY prior saved-plan grant for this job blocks
        # automatic renewal, even across days or unrelated setting changes.
        revoked=db.execute("SELECT 1 FROM recovery_grant_jobs j JOIN recovery_grants g USING(grant_id) WHERE j.job_key=? AND g.revoked=1 AND (g.operation_id LIKE 'collection-plan-v%' OR g.operation_id LIKE 'collection-saved-plan:%') LIMIT 1",(job['job_key'],)).fetchone()
        if revoked or db.execute('SELECT 1 FROM collection_grant_windows w JOIN recovery_grants g USING(grant_id) WHERE w.job_key=? AND g.revoked=1 LIMIT 1',(job['job_key'],)).fetchone():return None,'saved_plan_revoked'
        # Reserve bounded capacity up-front across all jobs and policy revisions.
        reserved=db.execute('SELECT COALESCE(SUM(g.max_attempts),0) FROM collection_grant_windows w JOIN recovery_grants g USING(grant_id) WHERE w.window_id=?',(window,)).fetchone()[0]
        # Old-version grants that have sent today also consume today's budget.
        old_used=db.execute("SELECT COUNT(*) FROM recovery_attempts a JOIN recovery_grants g USING(grant_id) WHERE a.started_ms>=? AND a.started_ms<? AND g.operation_id LIKE 'collection-plan-v%'",(starts,ends)).fetchone()[0]
        capacity=min(cfg['recoveryAttempts']*4,cfg['maxHttpAttempts']-reserved-old_used)
        if capacity<=0:return None,'saved_plan_daily_budget_reserved'
        import uuid
        grant='grant-'+uuid.uuid4().hex
        db.execute('INSERT INTO recovery_grants(grant_id,operation_id,scope_json,starts_ms,expires_ms,max_attempts) VALUES(?,?,?,?,?,?)',(grant,prefix+key[:24],json.dumps(scope,sort_keys=True,separators=(',',':')),starts,ends,capacity))
        db.execute('INSERT INTO collection_grant_windows VALUES(?,?,?,?,?)',(key,grant,window,fingerprint,job['job_key']))
        recovery.bump(db,clock_ms)
        return grant,None
