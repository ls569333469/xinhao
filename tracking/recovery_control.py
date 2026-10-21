"""Durable, explicit recovery permissions. No timers or HTTP implementation.

Every request is authorized immediately before it starts. A permit consumes the
budget even if the process crashes: an unobserved attempt cannot be called free.
"""
from __future__ import annotations

import time
import uuid
from .ledger import _canonical


class RecoveryDenied(ValueError):
    pass


def integer(value, name, minimum=0, maximum=2**63-1):
    if type(value) is not int or not minimum <= value <= maximum:
        raise ValueError('recovery_invalid_' + name)
    return value


def schema(db):
    statements = [
        "CREATE TABLE IF NOT EXISTS recovery_controls(key TEXT PRIMARY KEY,value TEXT NOT NULL)",
        "CREATE TABLE IF NOT EXISTS recovery_grants(grant_id TEXT PRIMARY KEY,operation_id TEXT NOT NULL,scope_json TEXT NOT NULL,starts_ms INTEGER NOT NULL,expires_ms INTEGER NOT NULL,max_attempts INTEGER NOT NULL,attempts_used INTEGER NOT NULL DEFAULT 0,revoked INTEGER NOT NULL DEFAULT 0)",
        "CREATE TABLE IF NOT EXISTS recovery_grant_jobs(grant_id TEXT NOT NULL,job_key TEXT NOT NULL,PRIMARY KEY(grant_id,job_key))",
        "CREATE INDEX IF NOT EXISTS recovery_grant_job ON recovery_grant_jobs(job_key,grant_id)",
        "CREATE TABLE IF NOT EXISTS recovery_provider_limits(provider TEXT PRIMARY KEY,until_ms INTEGER NOT NULL,reason TEXT NOT NULL)",
        "CREATE TABLE IF NOT EXISTS recovery_attempts(attempt_id TEXT PRIMARY KEY,grant_id TEXT NOT NULL,operation_id TEXT NOT NULL,job_key TEXT NOT NULL,lease_token TEXT NOT NULL,provider TEXT NOT NULL,endpoint TEXT NOT NULL,start_ms INTEGER NOT NULL,end_ms INTEGER NOT NULL,started_ms INTEGER NOT NULL,finished_ms INTEGER,status INTEGER,error_code TEXT,elapsed_ms REAL,retry_index INTEGER NOT NULL DEFAULT 0)",
        "CREATE INDEX IF NOT EXISTS recovery_attempt_job ON recovery_attempts(job_key,started_ms,attempt_id)",
    ]
    for statement in statements:
        db.execute(statement)
    if 'transport_started' not in {r[1] for r in db.execute("PRAGMA table_info(recovery_attempts)")} :
        db.execute("ALTER TABLE recovery_attempts ADD COLUMN transport_started INTEGER")
    db.execute("INSERT OR IGNORE INTO recovery_controls VALUES('enabled','false')")


class AttemptGate:
    """Request capability for a *single claimed page*, not an ambient requester.

    Adapters must issue each real HTTP invocation through call(). A supplied
    transport must perform one attempt, obey timeout_seconds, and never retry.
    Unknown transports are not installed automatically by this package.
    """
    def __init__(self, recovery, job, grant, *, clock, deadline, max_attempts,
                 monotonic=time.monotonic, timeout_seconds=10):
        self.recovery = recovery
        self.job = job
        self.grant = grant
        self.clock = clock
        self.deadline = deadline
        self.max_attempts = max_attempts
        self.monotonic = monotonic
        self.timeout_seconds = timeout_seconds
        self.used = 0
        self.last_status = None

    def call(self, transport):
        if not callable(transport):
            raise ValueError('recovery_one_attempt_transport_required')
        if self.used >= self.max_attempts or self.monotonic() >= self.deadline:
            raise RecoveryDenied('round_budget_exhausted')
        remaining = min(self.timeout_seconds, self.deadline-self.monotonic())
        if remaining <= 0:
            raise RecoveryDenied('round_deadline')
        at = integer(self.clock(), 'clock')
        attempt_id = uuid.uuid4().hex
        with self.recovery.transaction() as db:
            self.recovery.validate_claim(db, self.job, at)
            self.recovery.authorize(db, self.job, self.grant, at)
            row = db.execute('SELECT operation_id FROM recovery_grants WHERE grant_id=?', (self.grant,)).fetchone()
            changed = db.execute('UPDATE recovery_grants SET attempts_used=attempts_used+1 WHERE grant_id=? AND revoked=0 AND attempts_used<max_attempts', (self.grant,)).rowcount
            if changed != 1:
                raise RecoveryDenied('grant_budget_exhausted')
            db.execute('INSERT INTO recovery_attempts(attempt_id,grant_id,operation_id,job_key,lease_token,provider,endpoint,start_ms,end_ms,started_ms,retry_index) VALUES(?,?,?,?,?,?,?,?,?,?,?)',
                       (attempt_id,self.grant,row['operation_id'],self.job['job_key'],self.job['lease_token'],self.job['provider'],self.job['endpoint'],self.job['page_start'],self.job['page_end'],at,self.used))
        self.used += 1
        started = self.monotonic()
        response = None
        error = None; sent = False
        try:
            # The permit transaction may have waited behind a writer. Its time
            # consumes the same round deadline; never reuse the earlier timeout.
            remaining=min(self.timeout_seconds,self.deadline-self.monotonic())
            if remaining<=0:raise RecoveryDenied('round_deadline_after_permit')
            sent=True
            response = transport(timeout_seconds=remaining)
            if not isinstance(response, dict) or type(response.get('status')) is not int:
                raise ValueError('one_attempt_status_required')
            if response.get('http_attempts', 1) != 1 or response.get('retries', 0) != 0:
                raise ValueError('hidden_transport_retries_forbidden')
            self.last_status = response['status']
            return response
        except BaseException as exc:
            error = type(exc).__name__
            raise
        finally:
            ended = integer(self.clock(), 'clock')
            status = response.get('status') if isinstance(response, dict) and type(response.get('status')) is int else None
            with self.recovery.transaction() as db:
                db.execute('UPDATE recovery_attempts SET finished_ms=?,status=?,error_code=?,elapsed_ms=?,transport_started=? WHERE attempt_id=?',
                           (ended,status,error,(self.monotonic()-started)*1000,int(sent),attempt_id))
                if status in (429,418,401,402,403):
                    retry_after = response.get('retry_after_ms')
                    floor = 6*3600000 if status in (401,402,403) else 3600000 if status == 418 else 60000
                    delay = retry_after if type(retry_after) is int and retry_after >= 0 else floor
                    # Do not shorten a provider's declared Retry-After.
                    until = min(2**63-1, ended+max(floor, delay))
                    db.execute('INSERT INTO recovery_provider_limits VALUES(?,?,?) ON CONFLICT(provider) DO UPDATE SET until_ms=MAX(until_ms,excluded.until_ms),reason=excluded.reason',
                               (self.job['provider'],until,'provider_'+str(status)))
                self.recovery.bump(db, ended)


def create_grant(recovery, *, operation_id, scope, starts_ms, expires_ms, max_attempts):
    if not isinstance(operation_id,str) or not operation_id or len(operation_id)>200:
        raise ValueError('recovery_operation_required')
    if not isinstance(scope,dict) or set(scope) != {'providers','contracts','sources','start_ms','target_ms'}:
        raise ValueError('recovery_scope_required')
    for field in ('providers','contracts','sources'):
        values = scope[field]
        if not isinstance(values,list) or not 1 <= len(values) <= 1000 or any(not isinstance(v,str) or not v or len(v)>200 for v in values):
            raise ValueError('recovery_scope_'+field)
    integer(starts_ms,'grant_start');integer(expires_ms,'grant_expiry',starts_ms+1)
    integer(scope['start_ms'],'scope_start');integer(scope['target_ms'],'scope_target',scope['start_ms'])
    integer(max_attempts,'max_attempts',1,100000)
    key = 'grant-'+uuid.uuid4().hex
    with recovery.transaction() as db:
        db.execute('INSERT INTO recovery_grants(grant_id,operation_id,scope_json,starts_ms,expires_ms,max_attempts) VALUES(?,?,?,?,?,?)',
                   (key,operation_id,_canonical(scope),starts_ms,expires_ms,max_attempts))
        recovery.bump(db, starts_ms)
    return key
