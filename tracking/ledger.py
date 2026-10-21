"""Isolated append-only tracking ledger (SQLite) - S2-r3.

NOT production signal-tracking.sqlite3. NOT wired into task_service / server.py.
Lives under the S2 submission package (ledger_data/) by default.

S1-R2-C1 store contract (audit option 2):
  - Append path loads prior revisions for the same event_id from THIS store only
    (inside the same write transaction), each validated on write.
  - External JSON prior arrays are rejected unless passed through import_prefix,
    which runs validate_prefix_incremental on the full chain before any write.

S2-r2 (Codex S2-r1 FAIL, findings S2-R1-F1..F5):
  F1  inbox rows have an explicit life cycle: pending -> acked. consume updates the
      ORIGINAL inbox row (no second marker row, no UNIQUE clash). A key is bound to
      source + payload sha256 (enqueue) and to the commit request sha256 + stored
      result (consume). Idempotent only when the row is acked AND the request matches;
      any other reuse of the key is rejected explicitly.
  F2  store-level: an A record with qualification_status=pass is rejected unless the
      record itself carries candidate_cycle_id + matching candidate_store source + oi
      source. No caller flag can bypass it.
  F4  one guarded session writer (_apply_session_conn) used by EVERY entry
      (append_revision companion, upsert_session, consume_and_commit) inside the same
      transaction: ended is terminal (no reopen, no clearing human stop / end fields),
      identity fields immutable, watermark monotonic, every state change needs a
      contract-valid transition chain. A new observation needs a new session_id and
      leaves the old ended row untouched.
  F5  a single transactional entry (_commit) for revision + session + transitions +
      gaps + cursor + outbox + inbox ACK. Any failure rolls everything back and is
      returned as ok=false (cursor not advanced, no outbox row, inbox stays pending).
  Sessions and transitions are validated against the frozen S1 schema/rules
  (S01-S06, T01-T04) before they are written.
"""
from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import time
from pathlib import Path
from typing import Any

from .chain_validate import import_validated_prefix, judge, load_schema, validate_single, validate_prefix_incremental
from .effective_chain import merge_and_validate_effective_chain
from .paths import DEFAULT_LEDGER_PATH
from .control_contract import validate as validate_control_record, CONTROL_SCHEMA, SUPERSEDED

SCHEMA_VERSION = "s2-isolated-tracking-ledger-v1.5-c2c-c3"
SCHEMA_LEGACY_MIGRATE = frozenset({
    "s2-isolated-tracking-ledger-v1.3-r9",
    "s2-isolated-tracking-ledger-v1.4-c2b",
    "s2-isolated-tracking-ledger-v1.4-c2b-r1",
    "s2-isolated-tracking-ledger-v1.4-c2b-r2",
    "s2-isolated-tracking-ledger-v1.4-c2b-r3",
})

DDL = """
PRAGMA journal_mode=WAL;
CREATE TABLE IF NOT EXISTS meta (
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS event_revisions (
  event_id TEXT NOT NULL,
  revision INTEGER NOT NULL,
  revision_id TEXT NOT NULL UNIQUE,
  prior_revision_id TEXT,
  strategy TEXT NOT NULL,
  symbol TEXT NOT NULL,
  technical_status TEXT,
  payload_json TEXT NOT NULL,
  payload_sha256 TEXT NOT NULL,
  recorded_at_utc TEXT NOT NULL,
  PRIMARY KEY (event_id, revision)
);
CREATE INDEX IF NOT EXISTS idx_event_revisions_symbol ON event_revisions(symbol, strategy);
CREATE TABLE IF NOT EXISTS cursors (
  cursor_name TEXT PRIMARY KEY,
  cursor_value TEXT NOT NULL,
  updated_at_utc TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS gaps (
  gap_id TEXT PRIMARY KEY,
  session_id TEXT,
  scope TEXT NOT NULL,
  detail_json TEXT NOT NULL,
  status TEXT NOT NULL,
  created_at_utc TEXT NOT NULL,
  updated_at_utc TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_gaps_session ON gaps(session_id, status);
CREATE TABLE IF NOT EXISTS inbox (
  inbox_id INTEGER PRIMARY KEY AUTOINCREMENT,
  source TEXT NOT NULL,
  source_message_key TEXT NOT NULL,
  payload_json TEXT NOT NULL,
  payload_sha256 TEXT NOT NULL,
  status TEXT NOT NULL CHECK (status IN ('pending','acked')),
  created_at_utc TEXT NOT NULL,
  request_sha256 TEXT,
  result_json TEXT,
  acked_at_utc TEXT
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_inbox_source_message_key ON inbox(source_message_key);
CREATE TABLE IF NOT EXISTS outbox (
  outbox_id INTEGER PRIMARY KEY AUTOINCREMENT,
  kind TEXT NOT NULL,
  payload_json TEXT NOT NULL,
  status TEXT NOT NULL,
  source_message_key TEXT,
  created_at_utc TEXT NOT NULL,
  delivered_at_utc TEXT,
  read_at_utc TEXT
);
CREATE TABLE IF NOT EXISTS sessions (
  session_id TEXT PRIMARY KEY,
  event_id TEXT NOT NULL,
  observation_status TEXT NOT NULL,
  end_reason TEXT,
  human_stopped INTEGER NOT NULL DEFAULT 0,
  ended_at_bar_close_ms INTEGER,
  last_evaluated_bar_close_ms INTEGER,
  payload_json TEXT NOT NULL,
  opened_at_utc TEXT NOT NULL,
  updated_at_utc TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_sessions_event ON sessions(event_id);
CREATE TABLE IF NOT EXISTS session_transitions (
  transition_id TEXT PRIMARY KEY,
  session_id TEXT NOT NULL,
  event_id TEXT NOT NULL,
  from_state TEXT,
  to_state TEXT NOT NULL,
  reason TEXT NOT NULL,
  at_bar_open_ms INTEGER,
  at_bar_close_ms INTEGER,
  recorded_at TEXT NOT NULL,
  revision INTEGER,
  payload_json TEXT NOT NULL,
  seq INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_transitions_session ON session_transitions(session_id, seq);
CREATE TABLE IF NOT EXISTS replay_plans (
  plan_id TEXT PRIMARY KEY,
  event_id TEXT NOT NULL,
  session_id TEXT,
  status TEXT NOT NULL CHECK (status IN ('pending','completed','cancelled','superseded','skipped_terminal')),
  replay_from_effective_ms INTEGER,
  watermark_at_plan_ms INTEGER,
  quality_status TEXT NOT NULL,
  prior_sources_json TEXT NOT NULL,
  new_sources_json TEXT NOT NULL,
  first_available_at TEXT,
  detail_json TEXT NOT NULL,
  created_at_utc TEXT NOT NULL,
  updated_at_utc TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_replay_plans_event ON replay_plans(event_id, status);

CREATE TABLE IF NOT EXISTS source_receipts (
  message_key TEXT PRIMARY KEY,
  source TEXT NOT NULL,
  source_ref TEXT NOT NULL,
  version TEXT NOT NULL,
  payload_hash TEXT NOT NULL,
  envelope_json TEXT NOT NULL,
  ingested_at TEXT NOT NULL,
  dispatch_state TEXT NOT NULL CHECK (dispatch_state IN (
    'received','fanout_pending','dispatched','conflict','rejected')),
  receipt_acked_at TEXT,
  fanout_plan_json TEXT
);
CREATE INDEX IF NOT EXISTS idx_source_receipts_source ON source_receipts(source, dispatch_state);
CREATE TABLE IF NOT EXISTS source_jobs (
  job_key TEXT PRIMARY KEY,
  message_key TEXT NOT NULL REFERENCES source_receipts(message_key),
  consumer_key TEXT NOT NULL,
  session_id TEXT,
  state TEXT NOT NULL CHECK (state IN (
    'pending','running','effect_acked','failed','superseded','gap_paused')),
  replay_plan_id TEXT,
  next_close_ms INTEGER,
  attempt_count INTEGER NOT NULL DEFAULT 0,
  error_code TEXT,
  effect_result_json TEXT,
  effect_acked_at TEXT,
  UNIQUE(message_key, consumer_key)
);
CREATE INDEX IF NOT EXISTS idx_source_jobs_pending ON source_jobs(state, job_key);
CREATE TABLE IF NOT EXISTS source_watermarks (
  source TEXT NOT NULL,
  contract_key TEXT NOT NULL,
  timeframe TEXT NOT NULL,
  consumer_key TEXT NOT NULL,
  version_set_hash TEXT NOT NULL,
  requested_end_ms INTEGER,
  fetched_max_ms INTEGER,
  continuous_end_ms INTEGER,
  evaluated_end_ms INTEGER,
  first_gap_ms INTEGER,
  quality_json TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  PRIMARY KEY(source, contract_key, timeframe, consumer_key, version_set_hash)
);


CREATE TABLE IF NOT EXISTS tracking_commits (
  commit_seq INTEGER PRIMARY KEY AUTOINCREMENT,
  recorded_at TEXT NOT NULL,
  schema_version TEXT NOT NULL,
  source_tag TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS tracking_summary_versions (
  event_id TEXT NOT NULL,
  session_id TEXT,
  market TEXT,
  symbol TEXT,
  decision_close_ms INTEGER,
  summary_json TEXT NOT NULL,
  valid_from_seq INTEGER NOT NULL,
  valid_to_seq INTEGER,
  PRIMARY KEY (event_id, valid_from_seq)
);
CREATE INDEX IF NOT EXISTS idx_summary_keyset
  ON tracking_summary_versions(market, symbol, decision_close_ms, event_id);
CREATE INDEX IF NOT EXISTS idx_summary_latest_order ON tracking_summary_versions(COALESCE(decision_close_ms,0),event_id) WHERE valid_to_seq IS NULL;
CREATE INDEX IF NOT EXISTS idx_summary_latest_strategy ON tracking_summary_versions(json_extract(summary_json,'$.strategy'),COALESCE(decision_close_ms,0),event_id) WHERE valid_to_seq IS NULL;
CREATE INDEX IF NOT EXISTS idx_summary_latest_contract ON tracking_summary_versions(market,symbol,COALESCE(decision_close_ms,0),event_id) WHERE valid_to_seq IS NULL;
CREATE INDEX IF NOT EXISTS idx_summary_latest_contract_strategy ON tracking_summary_versions(market,symbol,json_extract(summary_json,'$.strategy'),decision_close_ms DESC,event_id DESC) WHERE valid_to_seq IS NULL;
CREATE TABLE IF NOT EXISTS tracking_effective_members (
  event_id TEXT NOT NULL,
  session_id TEXT NOT NULL,
  transition_id TEXT NOT NULL,
  page_seq INTEGER NOT NULL,
  payload_json TEXT NOT NULL,
  valid_from_seq INTEGER NOT NULL,
  valid_to_seq INTEGER,
  PRIMARY KEY (event_id, transition_id, valid_from_seq)
);
CREATE INDEX IF NOT EXISTS idx_eff_page
  ON tracking_effective_members(event_id, valid_from_seq, page_seq);

"""

TERMINAL = "ended"
SESSION_STATES = ("observing", "paused_awaiting_data", "ended")
GAP_STATES = ("open", "resolved", "closed_session_ended")
_SESSION_IDENTITY_FIELDS = ("event_id", "strategy", "tracking_profile_id", "tracking_profile_version",
                            "started_at_bar_close_ms")
_TERMINAL_FIELDS = ("observation_status", "end_reason", "ended_at_bar_close_ms", "human_stopped")


def _utc() -> str:
    t = time.time()
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(t)) + f".{int(t * 1000000) % 1000000:06d}+00:00"


def _sha256_text(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


def _canonical(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False, separators=(",", ":"), sort_keys=True)

def version_set_hash(versions) -> str:
    """Stable hash of a version set (sorted unique strings)."""
    blob = _canonical(sorted({str(v) for v in versions}))
    return _sha256_text(blob)


def timeframe_to_ms(timeframe: str) -> int | None:
    """Parse common bar labels (1m/5m/15m/1h/4h/1d) to milliseconds."""
    if not isinstance(timeframe, str):
        return None
    m = re.fullmatch(r"(\d+)([mhd])", timeframe.strip().lower())
    if not m:
        return None
    n, unit = int(m.group(1)), m.group(2)
    if n <= 0:
        return None
    mult = {"m": 60_000, "h": 3_600_000, "d": 86_400_000}[unit]
    return n * mult




class LedgerReject(Exception):
    """Explicit, expected refusal inside a write transaction (always rolled back)."""

    def __init__(self, code: str, **detail):
        super().__init__(code)
        self.code = code
        self.detail = detail

    def as_errors(self) -> list[dict]:
        if "errors" in self.detail and isinstance(self.detail["errors"], list):
            return [dict(code=self.code, **{k: v for k, v in self.detail.items() if k != "errors"})] + list(self.detail["errors"])
        return [dict(code=self.code, **self.detail)]


def check_a_qualification_evidence(record: dict) -> list[str]:
    """Store-level rule (S2-R1-F2): A pass needs the evidence on the record itself."""
    if record.get("strategy") != "A" or record.get("qualification_status") != "pass":
        return []
    missing: list[str] = []
    cyc = record.get("candidate_cycle_id")
    srcs = [s for s in (record.get("source_versions") or []) if isinstance(s, dict)]
    if not isinstance(cyc, str) or not cyc.strip():
        missing.append("candidate_cycle_id")
    cands = [s for s in srcs if s.get("source") == "candidate_store"]
    if not cands:
        missing.append("candidate_store_source")
    elif isinstance(cyc, str) and cyc.strip() and not any(cyc in str(s.get("ref") or "") for s in cands):
        missing.append("candidate_ref_mismatch")
    if not any(s.get("source") == "oi" for s in srcs):
        missing.append("oi_source")
    if "missing_oi" in (record.get("missing_data_flags") or []):
        missing.append("missing_oi_flag")
    return missing


class TrackingLedger:
    """Append-only isolated ledger. Refuse production path names."""

    FORBIDDEN_NAME_PARTS = ("signal-tracking.sqlite3", "workbench.sqlite3", "b-events-v3.sqlite3")

    def __init__(self, path: Path | str | None = None, *, _runtime_root=None):
        self.path = Path(path) if path else DEFAULT_LEDGER_PATH
        name = self.path.name.lower()
        allowed_runtime=(_runtime_root is not None and self.path.resolve()==Path(_runtime_root).resolve()/'runtime'/'signal-tracking.sqlite3')
        for bad in self.FORBIDDEN_NAME_PARTS:
            if bad in name and not allowed_runtime:
                raise ValueError(f"refusing production-like ledger name: {self.path}")
        resolved = self.path.resolve()
        from .paths import SUBMISSION_ROOT
        import tempfile
        ok_roots = [SUBMISSION_ROOT.resolve(),Path(tempfile.gettempdir()).resolve(),
                    Path(r"C:\Users\ls569\AppData\Local\Temp").resolve(),
                    Path(r"C:\Users\ls569\grok-scratch-s2r3").resolve()]
        if not allowed_runtime and not any(resolved.is_relative_to(r) for r in ok_roots):
            raise ValueError(f"ledger path outside gated roots: {resolved}")
        self.runtime_mode=allowed_runtime
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.schema = load_schema()
        self._init_db()

    @classmethod
    def for_runtime(cls,root):
        from tracking_service import enabled
        if not enabled(root):raise ValueError('tracking_disabled')
        root=Path(root).resolve()
        return cls(root/'runtime'/'signal-tracking.sqlite3',_runtime_root=root)

    # ------------------------------------------------------------------ plumbing
    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.path), timeout=30, isolation_level=None)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    def _read(self, sql: str, args: tuple = ()) -> list[sqlite3.Row]:
        conn = self._connect()
        try:
            return conn.execute(sql, args).fetchall()
        finally:
            conn.close()

    def close(self) -> None:
        try:
            conn = self._connect()
            try:
                conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            finally:
                conn.close()
        except Exception:
            pass

    def _read_existing_schema(self, conn: sqlite3.Connection) -> str | None:
        row = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='meta'"
        ).fetchone()
        if row is None:
            return None
        srow = conn.execute("SELECT value FROM meta WHERE key='schema'").fetchone()
        return srow["value"] if srow else None

    def _stamp_meta(self, conn: sqlite3.Connection) -> None:
        for k, v in (
            ("schema", SCHEMA_VERSION),
            ("production_wired", "true" if self.runtime_mode else "false"),
            ("s1_r2_c1_policy", "append_only_store_validated_prefix"),
            ("s2_r2_fixes", "R1-F1_inbox_lifecycle,R1-F2_store_qualification,R1-F3_stable_transition_ids,"
                            "R1-F4_terminal_guard_all_writers,R1-F5_single_transaction_entry"),
            ("s2_c2b", "source_receipts_jobs_watermarks_receipt_vs_effect_ack"),
            ("s2_c2c_c3", "lifecycle_driver_read_view_projection"),
            ("v2_read_index_schema", "latest-summary-index-v1"),
        ):
            conn.execute("INSERT OR REPLACE INTO meta(key,value) VALUES(?,?)", (k, v))

    def _apply_ddl(self, conn: sqlite3.Connection) -> None:
        # Avoid executescript inside an open transaction (it auto-commits).
        for stmt in DDL.split(";"):
            s = stmt.strip()
            if not s:
                continue
            conn.execute(s)

    def _ensure_v2_read_indexes(self, conn):
        if conn.execute("SELECT 1 FROM meta WHERE key='v2_read_index_schema' AND value='latest-summary-index-v1'").fetchone():return
        conn.execute('BEGIN IMMEDIATE')
        try:
            for stmt in DDL.split(';'):
                if stmt.strip().startswith('CREATE INDEX IF NOT EXISTS idx_summary_latest_'):conn.execute(stmt)
            conn.execute("INSERT OR REPLACE INTO meta VALUES('v2_read_index_schema','latest-summary-index-v1')")
            conn.execute('COMMIT')
        except BaseException:
            if conn.in_transaction:conn.execute('ROLLBACK')
            raise

    def _ensure_c2b_r1_columns(self, conn: sqlite3.Connection) -> None:
        # Table may not exist yet on fresh migrate from pre-c2b.
        exists = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='source_receipts'"
        ).fetchone()
        if not exists:
            return
        cols = {r["name"] for r in conn.execute("PRAGMA table_info(source_receipts)")}
        if "fanout_plan_json" not in cols:
            conn.execute("ALTER TABLE source_receipts ADD COLUMN fanout_plan_json TEXT")

    def _verify_c2b_layout(self, conn: sqlite3.Connection) -> None:
        row = conn.execute("SELECT value FROM meta WHERE key='schema'").fetchone()
        cols = {r["name"] for r in conn.execute("PRAGMA table_info(inbox)")}
        c2b_tables = {r["name"] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
        need = {"source_receipts", "source_jobs", "source_watermarks", "tracking_commits", "tracking_summary_versions", "tracking_effective_members"}
        receipt_cols = {r["name"] for r in conn.execute("PRAGMA table_info(source_receipts)")}
        if (row is None or row["value"] != SCHEMA_VERSION or "request_sha256" not in cols
                or not need.issubset(c2b_tables) or "fanout_plan_json" not in receipt_cols):
            raise ValueError(
                f"ledger schema mismatch ({row['value'] if row else None}); "
                "use a fresh isolated c2c-c3 ledger"
            )

    def _looks_like_known_ledger_layout(self, conn: sqlite3.Connection) -> bool:
        """True when DB already has ledger tables (not a blank file)."""
        tables = {
            r["name"] if isinstance(r, sqlite3.Row) else r[0]
            for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
        markers = {"meta", "inbox", "event_revisions", "sessions", "source_receipts"}
        return bool(tables & markers)

    def _migrate_legacy_receipt_keys(self, conn: sqlite3.Connection) -> None:
        """Rewrite pre-r1 source|ref|version keys to length-prefixed canonical keys (F4).

        Uses insert-canonical / repoint-jobs / delete-legacy so FK stays valid inside
        the migration transaction (PRAGMA foreign_keys cannot flip mid-transaction).
        """
        exists = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='source_receipts'"
        ).fetchone()
        if not exists:
            return
        rows = conn.execute("SELECT * FROM source_receipts").fetchall()
        if not rows:
            return
        for row in rows:
            canonical = self.build_message_key(
                source=row["source"], source_ref=row["source_ref"], version=row["version"],
            )
            old_key = row["message_key"]
            if old_key == canonical:
                continue
            clash = conn.execute(
                "SELECT 1 FROM source_receipts WHERE message_key=?", (canonical,),
            ).fetchone()
            if clash is not None:
                raise ValueError(
                    f"legacy receipt key remap collision ({old_key} -> {canonical})"
                )
            conn.execute(
                "INSERT INTO source_receipts("
                "message_key,source,source_ref,version,payload_hash,envelope_json,"
                "ingested_at,dispatch_state,receipt_acked_at,fanout_plan_json"
                ") VALUES(?,?,?,?,?,?,?,?,?,?)",
                (
                    canonical,
                    row["source"],
                    row["source_ref"],
                    row["version"],
                    row["payload_hash"],
                    row["envelope_json"],
                    row["ingested_at"],
                    row["dispatch_state"],
                    row["receipt_acked_at"],
                    row["fanout_plan_json"] if "fanout_plan_json" in row.keys() else None,
                ),
            )
            jobs = conn.execute(
                "SELECT job_key, message_key, consumer_key FROM source_jobs WHERE message_key=?",
                (old_key,),
            ).fetchall()
            for j in jobs:
                new_jk = j["job_key"]
                suffix = f"|{j['consumer_key']}"
                if j["job_key"] == f"{old_key}{suffix}":
                    new_jk = f"{canonical}{suffix}"
                elif j["job_key"].startswith(old_key + "|"):
                    new_jk = canonical + j["job_key"][len(old_key):]
                if new_jk != j["job_key"]:
                    taken = conn.execute(
                        "SELECT 1 FROM source_jobs WHERE job_key=?", (new_jk,),
                    ).fetchone()
                    if taken is not None:
                        raise ValueError(
                            f"legacy job_key remap collision ({j['job_key']} -> {new_jk})"
                        )
                    conn.execute(
                        "UPDATE source_jobs SET job_key=?, message_key=? WHERE job_key=?",
                        (new_jk, canonical, j["job_key"]),
                    )
                else:
                    conn.execute(
                        "UPDATE source_jobs SET message_key=? WHERE job_key=?",
                        (canonical, j["job_key"]),
                    )
            conn.execute(
                "DELETE FROM source_receipts WHERE message_key=?", (old_key,),
            )

    def _init_db(self) -> None:
        """F5: validate before COMMIT; missing schema on known layout is not fresh."""
        conn = self._connect()
        try:
            existing = self._read_existing_schema(conn)
            if existing is None:
                if self._looks_like_known_ledger_layout(conn):
                    raise ValueError(
                        "ledger missing schema stamp on existing layout; "
                        "refusing to open as a fresh empty DB"
                    )
                self._apply_ddl(conn)
                self._ensure_c2b_r1_columns(conn)
                self._stamp_meta(conn)
                self._verify_c2b_layout(conn)
                return
            if existing == SCHEMA_VERSION:
                # Core schema remains compatible. One additive, transactional index
                # migration runs only on the writer; GET never migrates.
                self._verify_c2b_layout(conn)
                self._ensure_v2_read_indexes(conn)
                return
            if existing in SCHEMA_LEGACY_MIGRATE:
                conn.execute("BEGIN IMMEDIATE")
                try:
                    # Pre-check: known-bad inbox layout must fail before any stamp.
                    cols = {r["name"] for r in conn.execute("PRAGMA table_info(inbox)")}
                    if "inbox" in {
                        r["name"] if isinstance(r, sqlite3.Row) else r[0]
                        for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
                    } and "request_sha256" not in cols:
                        raise ValueError(
                            f"ledger schema mismatch ({existing}); "
                            "use a fresh isolated c2c-c3 ledger"
                        )
                    self._apply_ddl(conn)
                    self._ensure_c2b_r1_columns(conn)
                    self._migrate_legacy_receipt_keys(conn)
                    self._stamp_meta(conn)
                    # Verify inside the same transaction before COMMIT (F5).
                    self._verify_c2b_layout(conn)
                    conn.execute("COMMIT")
                except Exception:
                    try:
                        conn.execute("ROLLBACK")
                    except Exception:
                        pass
                    raise
                return
            # Unknown / future schema: refuse with zero writes.
            raise ValueError(
                f"ledger schema unsupported ({existing}); refusing restamp to {SCHEMA_VERSION}"
            )
        finally:
            conn.close()

    # ------------------------------------------------------------------ reads
    @staticmethod
    def _revisions_conn(conn: sqlite3.Connection, event_id: str) -> list[dict]:
        rows = conn.execute(
            "SELECT payload_json FROM event_revisions WHERE event_id=? ORDER BY revision ASC", (event_id,)
        ).fetchall()
        return [json.loads(r["payload_json"]) for r in rows]

    def get_revisions(self, event_id: str) -> list[dict]:
        conn = self._connect()
        try:
            return self._revisions_conn(conn, event_id)
        finally:
            conn.close()

    def get_session(self, event_id: str) -> dict | None:
        """Latest session (by insertion) for the event; older ended sessions stay untouched."""
        rows = self._read("SELECT payload_json FROM sessions WHERE event_id=? ORDER BY rowid DESC LIMIT 1", (event_id,))
        return json.loads(rows[0]["payload_json"]) if rows else None

    def list_sessions(self, event_id: str) -> list[dict]:
        rows = self._read("SELECT payload_json FROM sessions WHERE event_id=? ORDER BY rowid ASC", (event_id,))
        return [json.loads(r["payload_json"]) for r in rows]

    def get_session_by_id(self, session_id: str) -> dict | None:
        rows = self._read("SELECT payload_json FROM sessions WHERE session_id=?", (session_id,))
        return json.loads(rows[0]["payload_json"]) if rows else None

    def list_transitions(self, event_id: str) -> list[dict]:
        rows = self._read(
            "SELECT payload_json FROM session_transitions WHERE event_id=? ORDER BY seq ASC", (event_id,))
        return [json.loads(r["payload_json"]) for r in rows]

    def list_gaps(self, session_id: str, *, status: str | None = None) -> list[dict]:
        if status:
            rows = self._read("SELECT * FROM gaps WHERE session_id=? AND status=? ORDER BY created_at_utc, gap_id",
                              (session_id, status))
        else:
            rows = self._read("SELECT * FROM gaps WHERE session_id=? ORDER BY created_at_utc, gap_id", (session_id,))
        return [dict(gap_id=r["gap_id"], session_id=r["session_id"], scope=r["scope"],
                     detail=json.loads(r["detail_json"]), status=r["status"]) for r in rows]

    def get_cursor(self, name: str) -> str | None:
        rows = self._read("SELECT cursor_value FROM cursors WHERE cursor_name=?", (name,))
        return rows[0]["cursor_value"] if rows else None

    def get_inbox(self, source_message_key: str) -> dict | None:
        rows = self._read("SELECT * FROM inbox WHERE source_message_key=?", (source_message_key,))
        if not rows:
            return None
        r = rows[0]
        return dict(inbox_id=r["inbox_id"], source=r["source"], source_message_key=r["source_message_key"],
                    payload=json.loads(r["payload_json"]), payload_sha256=r["payload_sha256"], status=r["status"],
                    request_sha256=r["request_sha256"],
                    result=json.loads(r["result_json"]) if r["result_json"] else None, acked_at_utc=r["acked_at_utc"])

    def list_inbox(self) -> list[dict]:
        rows = self._read("SELECT source_message_key FROM inbox ORDER BY inbox_id")
        return [self.get_inbox(r["source_message_key"]) for r in rows]

    # ------------------------------------------------------------------ guarded writers (inside a transaction)
    def _write_revision_conn(self, conn: sqlite3.Connection, record: dict, now: str) -> tuple[str, str]:
        """Returns (state, digest) where state in {'inserted','exists'}; raises LedgerReject."""
        if not isinstance(record, dict):
            raise LedgerReject("STORE_record_not_object")
        eid, rev = record.get("event_id"), record.get("revision")
        if not isinstance(eid, str) or not isinstance(rev, int) or isinstance(rev, bool) or rev < 1:
            raise LedgerReject("STORE_event_id_revision_required")
        payload = _canonical(record)
        digest = _sha256_text(payload)
        missing = check_a_qualification_evidence(record)
        if missing:
            raise LedgerReject("STORE_qualification_pass_without_evidence", event_id=eid, revision=rev, missing=missing)
        priors = self._revisions_conn(conn, eid)
        if rev <= len(priors):
            if _canonical(priors[rev - 1]) == payload:
                return "exists", digest
            raise LedgerReject("STORE_r1_conflict" if rev == 1 else "STORE_revision_conflict",
                               event_id=eid, revision=rev, detail="same revision key, different payload")
        if rev != len(priors) + 1:
            raise LedgerReject("STORE_prior_count_mismatch", have=len(priors), need=rev - 1)
        errs = validate_prefix_incremental(priors + [record], self.schema) if rev > 1 else validate_single(record, self.schema, None)
        if errs:
            raise LedgerReject("STORE_validation_failed", errors=errs)
        conn.execute(
            "INSERT INTO event_revisions(event_id,revision,revision_id,prior_revision_id,strategy,symbol,"
            "technical_status,payload_json,payload_sha256,recorded_at_utc) VALUES(?,?,?,?,?,?,?,?,?,?)",
            (eid, rev, record.get("revision_id"), record.get("prior_revision_id"), record.get("strategy"),
             record.get("symbol"), record.get("technical_status"), payload, digest, now),
        )
        return "inserted", digest

    def _apply_session_conn(self, conn: sqlite3.Connection, session: dict | None,
                            transitions: list[dict], now: str) -> str:
        """The ONLY session writer. Returns 'inserted' | 'updated' | 'unchanged'."""
        if session is None:
            if transitions:
                raise LedgerReject("TRANSITION_without_session")
            return "none"
        if not isinstance(session, dict):
            raise LedgerReject("SESSION_not_object")
        serrs = validate_control_record("session", session, self.schema)
        if serrs:
            raise LedgerReject("SESSION_contract_invalid", errors=serrs)
        sid, eid, new_state = session["session_id"], session["event_id"], session["observation_status"]
        for t in transitions:
            terrs = validate_control_record("transition", t, self.schema)
            if terrs:
                raise LedgerReject("TRANSITION_contract_invalid", transition_id=t.get("transition_id"), errors=terrs)
            if t.get("session_id") != sid:
                raise LedgerReject("TRANSITION_session_mismatch", transition_id=t.get("transition_id"))
        prev_row = conn.execute("SELECT * FROM sessions WHERE session_id=?", (sid,)).fetchone()
        payload = _canonical(session)
        if prev_row is None:
            active = conn.execute(
                "SELECT session_id FROM sessions WHERE event_id=? AND observation_status<>?", (eid, TERMINAL)
            ).fetchall()
            if active:
                raise LedgerReject("SESSION_active_exists", event_id=eid, active=[r["session_id"] for r in active])
            prev_state = None
        else:
            prev = json.loads(prev_row["payload_json"])
            prev_state = prev_row["observation_status"]
            for k in _SESSION_IDENTITY_FIELDS:
                if prev.get(k) != session.get(k):
                    raise LedgerReject("SESSION_identity_conflict", session_id=sid, field=k)
            if prev_state == TERMINAL:
                if new_state != TERMINAL:
                    raise LedgerReject("SESSION_already_ended", session_id=sid, event_id=eid,
                                       end_reason=prev.get("end_reason"), human_stopped=prev.get("human_stopped"))
                if all(prev.get(k) == session.get(k) for k in _TERMINAL_FIELDS) and not transitions:
                    return "unchanged"
                raise LedgerReject("SESSION_already_ended", session_id=sid, event_id=eid,
                                   detail="terminal state is immutable")
            if bool(prev.get("human_stopped")) and not session.get("human_stopped"):
                raise LedgerReject("SESSION_human_stop_cleared", session_id=sid)
            plw, nlw = prev.get("last_evaluated_bar_close_ms"), session.get("last_evaluated_bar_close_ms")
            if isinstance(plw, int) and isinstance(nlw, int) and nlw < plw:
                raise LedgerReject("SESSION_watermark_regression", session_id=sid, have=plw, got=nlw)
            for g in prev.get("gap_refs") or []:
                if g not in (session.get("gap_refs") or []):
                    raise LedgerReject("SESSION_gap_refs_dropped", session_id=sid, gap_id=g)
        # transition chain must lead exactly from the stored state to the new state.
        # S2-r6/r7: a correction (revision>1 + corrects_transition_id) may rewrite history, so its
        # from_state is the pre-correction effective state and need not equal the stored session
        # state. S2-R6-F2: the corrected target must be a real same-session predecessor
        # (already persisted, or an earlier transition in this batch). Self-reference, cycles,
        # and unknown targets are rejected in this same transaction (nothing applied).
        if transitions:
            existing_rows = conn.execute(
                "SELECT transition_id, payload_json FROM session_transitions WHERE session_id=? ORDER BY seq ASC", (sid,)
            ).fetchall()
            existing_tids = {r["transition_id"] for r in existing_rows}
            corrects_map: dict[str, str] = {}
            for r in existing_rows:
                try:
                    t_payload = json.loads(r["payload_json"])
                except Exception:
                    t_payload = {}
                cid0 = t_payload.get("corrects_transition_id") if isinstance(t_payload, dict) else None
                if cid0:
                    corrects_map[r["transition_id"]] = cid0
            # Only predecessors are valid correction targets (not the whole batch up-front,
            # which previously allowed a transition to correct itself via batch_tids).
            prior_known = set(existing_tids)
            cur = prev_state
            for t in transitions:
                rev = int(t.get("revision") or 1)
                is_corr = rev > 1
                tid = t.get("transition_id")
                if is_corr:
                    cid = t.get("corrects_transition_id")
                    if not cid:
                        raise LedgerReject("TRANSITION_correction_without_link",
                                           transition_id=tid)
                    if cid == tid:
                        raise LedgerReject("TRANSITION_correction_self_reference",
                                           transition_id=tid, corrects_transition_id=cid)
                    if cid not in prior_known:
                        raise LedgerReject("TRANSITION_correction_target_missing",
                                           transition_id=tid,
                                           corrects_transition_id=cid)
                    # Cycle detection along corrects links (existing + earlier-in-batch).
                    walk = cid
                    seen = {tid}
                    while walk is not None:
                        if walk in seen:
                            raise LedgerReject("TRANSITION_correction_cycle",
                                               transition_id=tid, corrects_transition_id=cid,
                                               cycle_hit=walk)
                        seen.add(walk)
                        walk = corrects_map.get(walk)
                    # Historical rewrite: do not require from_state == stored cur.
                else:
                    if t.get("from_state") != cur:
                        raise LedgerReject("TRANSITION_from_state_mismatch", transition_id=tid,
                                           stored_state=cur, from_state=t.get("from_state"))
                cur = t.get("to_state")
                if tid:
                    prior_known.add(tid)
                    if is_corr and t.get("corrects_transition_id"):
                        corrects_map[tid] = t.get("corrects_transition_id")
            if cur != new_state:
                raise LedgerReject("TRANSITION_chain_does_not_reach_session_state", end=cur, session_state=new_state)
            # S2-DEEP-F1: merge existing facts + pending corrections into the deterministic
            # effective chain (same builder as read_api) and reject the whole batch if
            # the chain is illegal. Illegal chain must not update session then hide via reads.
            existing_payloads = []
            for r in existing_rows:
                try:
                    existing_payloads.append(json.loads(r["payload_json"]))
                except Exception:
                    raise LedgerReject("TRANSITION_payload_corrupt", transition_id=r["transition_id"])
            _eff, chain_errs = merge_and_validate_effective_chain(existing_payloads, transitions, session)
            if chain_errs:
                raise LedgerReject("EFFECTIVE_CHAIN_INVALID", session_id=sid, errors=chain_errs)
        elif prev_state is None:
            raise LedgerReject("SESSION_create_requires_start_transition", session_id=sid, got=new_state,
                               detail="frozen contract: a session is created only by (None->observing, "
                                      "session_started); (None->ended) is not an allowed transition")
        elif prev_state != new_state:
            raise LedgerReject("SESSION_transition_required", session_id=sid, from_state=prev_state, to_state=new_state)
        args = (new_state, session.get("end_reason"), 1 if session.get("human_stopped") else 0,
                session.get("ended_at_bar_close_ms"), session.get("last_evaluated_bar_close_ms"), payload, now)
        if prev_row is None:
            conn.execute(
                "INSERT INTO sessions(observation_status,end_reason,human_stopped,ended_at_bar_close_ms,"
                "last_evaluated_bar_close_ms,payload_json,updated_at_utc,session_id,event_id,opened_at_utc) "
                "VALUES(?,?,?,?,?,?,?,?,?,?)", args + (sid, eid, now))
            state = "inserted"
        else:
            cur = conn.execute(
                "UPDATE sessions SET observation_status=?, end_reason=?, human_stopped=?, ended_at_bar_close_ms=?, "
                "last_evaluated_bar_close_ms=?, payload_json=?, updated_at_utc=? "
                "WHERE session_id=? AND observation_status=? AND observation_status<>?",
                args + (sid, prev_state, TERMINAL))
            if cur.rowcount != 1:
                raise LedgerReject("SESSION_concurrent_change", session_id=sid)
            state = "updated" if prev_row["payload_json"] != payload else "unchanged"
        for t in transitions:
            self._insert_transition_conn(conn, t, eid)
        return state

    def _insert_transition_conn(self, conn: sqlite3.Connection, transition: dict, event_id: str) -> None:
        tid = transition["transition_id"]
        payload = _canonical(transition)
        existing = conn.execute("SELECT payload_json FROM session_transitions WHERE transition_id=?", (tid,)).fetchone()
        if existing is not None:
            raise LedgerReject("TRANSITION_conflict" if existing["payload_json"] != payload else "TRANSITION_duplicate",
                               transition_id=tid)
        seq = conn.execute("SELECT COALESCE(MAX(seq),0)+1 AS n FROM session_transitions WHERE session_id=?",
                           (transition["session_id"],)).fetchone()["n"]
        conn.execute(
            "INSERT INTO session_transitions(transition_id,session_id,event_id,from_state,to_state,reason,"
            "at_bar_open_ms,at_bar_close_ms,recorded_at,revision,payload_json,seq) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
            (tid, transition["session_id"], event_id, transition.get("from_state"), transition["to_state"],
             transition["reason"], transition.get("at_bar_open_ms"), transition.get("at_bar_close_ms"),
             transition["recorded_at"], transition.get("revision"), payload, seq),
        )

    def _write_gaps_conn(self, conn: sqlite3.Connection, gaps: list[dict], now: str) -> None:
        for g in gaps:
            from .recovery import enqueue_gap_conn
            enqueue_gap_conn(conn,g)
            gid, status = g.get("gap_id"), g.get("status")
            if not gid or status not in GAP_STATES:
                raise LedgerReject("GAP_invalid", gap=g)
            row = conn.execute("SELECT status FROM gaps WHERE gap_id=?", (gid,)).fetchone()
            if row is None:
                conn.execute(
                    "INSERT INTO gaps(gap_id,session_id,scope,detail_json,status,created_at_utc,updated_at_utc) "
                    "VALUES(?,?,?,?,?,?,?)",
                    (gid, g.get("session_id"), g.get("scope") or "a_observe",
                     _canonical(g.get("detail") or {}), status, now, now))
            elif row["status"] != status:
                if row["status"] != "open":
                    raise LedgerReject("GAP_reopen_forbidden", gap_id=gid, have=row["status"], got=status)
                conn.execute("UPDATE gaps SET status=?, updated_at_utc=? WHERE gap_id=? AND status='open'",
                             (status, now, gid))

    def _write_cursor_conn(self, conn: sqlite3.Connection, cursor: tuple[str, str], now: str) -> None:
        cname, cvalue = cursor
        row = conn.execute("SELECT cursor_value FROM cursors WHERE cursor_name=?", (cname,)).fetchone()
        if row is not None:
            try:
                if int(cvalue) < int(row["cursor_value"]):
                    raise LedgerReject("CURSOR_regression", cursor=cname, have=row["cursor_value"], got=cvalue)
            except ValueError:
                pass
            conn.execute("UPDATE cursors SET cursor_value=?, updated_at_utc=? WHERE cursor_name=?", (cvalue, now, cname))
        else:
            conn.execute("INSERT INTO cursors(cursor_name,cursor_value,updated_at_utc) VALUES(?,?,?)", (cname, cvalue, now))

    def _write_outbox_conn(self, conn: sqlite3.Connection, kind: str, payload: dict, key: str | None, now: str) -> int:
        if key is not None:
            existing=conn.execute("SELECT outbox_id,payload_json FROM outbox WHERE kind=? AND source_message_key=? LIMIT 1",(kind,key)).fetchone()
            if existing:
                if _canonical(json.loads(existing['payload_json']))!=_canonical(payload):
                    raise LedgerReject('OUTBOX_key_content_conflict',key=key)
                return int(existing['outbox_id'])
        cur = conn.execute(
            "INSERT INTO outbox(kind,payload_json,status,source_message_key,created_at_utc) VALUES(?,?,?,?,?)",
            (kind, json.dumps(payload, ensure_ascii=False), "pending", key, now))
        return int(cur.lastrowid)

    def _claim_message_conn(self, conn: sqlite3.Connection, message: dict, request_sha: str, now: str) -> dict:
        key, ack_id = message.get("key"), message.get("inbox_ack_id")
        if key is None and ack_id is None:
            raise LedgerReject("CONSUME_key_required")
        if key is None:
            row = conn.execute("SELECT * FROM inbox WHERE inbox_id=?", (ack_id,)).fetchone()
            if row is None:
                raise LedgerReject("INBOX_not_found", inbox_id=ack_id)
        else:
            row = conn.execute("SELECT * FROM inbox WHERE source_message_key=?", (key,)).fetchone()
        if row is None:
            if ack_id is not None:
                raise LedgerReject("CONSUME_ack_key_mismatch", source_message_key=key, inbox_ack_id=ack_id,
                                   detail="inbox_ack_id does not belong to this source_message_key")
            body = message.get("payload") if message.get("payload") is not None else {"direct_consume_request_sha256": request_sha}
            pj = _canonical(body)
            conn.execute(
                "INSERT INTO inbox(source,source_message_key,payload_json,payload_sha256,status,created_at_utc) "
                "VALUES(?,?,?,?,?,?)", (message.get("source") or "direct_consume", key, pj, _sha256_text(pj), "pending", now))
            row = conn.execute("SELECT * FROM inbox WHERE source_message_key=?", (key,)).fetchone()
        else:
            if ack_id is not None and int(ack_id) != int(row["inbox_id"]):
                raise LedgerReject("CONSUME_ack_key_mismatch", source_message_key=key, inbox_ack_id=ack_id,
                                   key_inbox_id=row["inbox_id"])
            if message.get("source") is not None and message["source"] != row["source"]:
                raise LedgerReject("CONSUME_message_source_conflict", source_message_key=row["source_message_key"])
            if message.get("payload") is not None and _sha256_text(_canonical(message["payload"])) != row["payload_sha256"]:
                raise LedgerReject("CONSUME_message_payload_conflict", source_message_key=row["source_message_key"])
        out = dict(row)
        if row["status"] == "acked":
            if row["request_sha256"] == request_sha:
                out["replay"] = True
                return out
            raise LedgerReject("CONSUME_payload_conflict", source_message_key=row["source_message_key"],
                               detail="key already consumed with a different commit request")
        return out

    # ------------------------------------------------------------------ THE transactional entry
    def _commit(self, *, record: dict | None = None, session: dict | None = None,
                transitions: list[dict] | None = None, gaps: list[dict] | None = None,
                cursor: tuple[str, str] | None = None, outbox_kind: str | None = None,
                outbox_payload: dict | None = None, message: dict | None = None,
                replay_plan: dict | None = None, complete_replay_plan_id: str | None = None,
                supersede_replay_event_id: str | None = None,
                job_effect: dict | None = None, cursor_updates: dict | None = None,
                expected_cursors: dict | None = None, extra_outbox: list | None = None,
                retired_sessions: list | None = None, recovery_effect: dict | None = None) -> dict:
        transitions = [t for t in (transitions or []) if t is not None]
        request = {"record": record, "session": session, "transitions": transitions, "gaps": gaps or [],
                   "cursor": list(cursor) if cursor else None, "outbox_kind": outbox_kind,
                   "outbox_payload": outbox_payload, "replay_plan": replay_plan,
                   "complete_replay_plan_id": complete_replay_plan_id,
                   "supersede_replay_event_id": supersede_replay_event_id,
                   "job_effect": job_effect, "cursor_updates": cursor_updates,
                   "expected_cursors": expected_cursors, "extra_outbox": extra_outbox, "retired_sessions": retired_sessions}
        if recovery_effect is not None:
            request['recovery_effect'] = recovery_effect
        request_sha = _sha256_text(_canonical(request))
        now = _utc()
        conn = self._connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            msg_row = None
            if message is not None:
                msg_row = self._claim_message_conn(conn, message, request_sha, now)
                if msg_row.get("replay"):
                    conn.execute("COMMIT")
                    stored = json.loads(msg_row["result_json"] or "{}")
                    eid = (record or session or {}).get("event_id") or stored.get("event_id")
                    return dict(stored, ok=True, idempotent=True, already_exists=True,
                                session=self.get_session(eid) if eid else None)
            for cursor_name, expected_value in (expected_cursors or {}).items():
                old_cursor = conn.execute("SELECT cursor_value FROM cursors WHERE cursor_name=?", (cursor_name,)).fetchone()
                if (old_cursor[0] if old_cursor else None) != expected_value:
                    raise LedgerReject("CURSOR_concurrent_change", cursor=cursor_name)
            result: dict = {"ok": True, "idempotent": False, "request_sha256": request_sha}
            if record is not None:
                state, digest = self._write_revision_conn(conn, record, now)
                result.update(event_id=record["event_id"], revision=record["revision"], payload_sha256=digest,
                              revision_state=state)
                if state == "exists" and message is None:
                    # plain append retry of an identical stored revision. State-changing companions are
                    # never silently dropped or re-applied here: they need a keyed consume (idempotent
                    # by message) so the retry can be proven to be the same commit.
                    if session is not None or transitions or gaps or cursor is not None:
                        raise LedgerReject("STORE_companions_require_message_key", event_id=record["event_id"],
                                           revision=record["revision"],
                                           detail="revision already stored; use consume_and_commit with its key")
                    conn.execute("COMMIT")
                    return dict(result, idempotent=True, already_exists=True,
                                companions_skipped=["outbox"] if outbox_kind else [])
            for retired in retired_sessions or []:
                old_session, old_transitions = retired["session"], retired["transitions"]
                signal_revision=record and record.get('strategy') in ('B','C') and record.get('revision_reason')=='source_revision' and old_session.get('event_id')==record.get('event_id')
                observation_revision=False
                if message and message.get('source')=='observation_source_revision':
                    payload=message.get('payload') or {}
                    prior=conn.execute('SELECT payload_json,status FROM inbox WHERE source_message_key=?',(payload.get('prior_input_key'),)).fetchone()
                    if prior and prior['status']=='acked':
                        before=json.loads(prior['payload_json'])
                        observation_revision=(before.get('event_id')==payload.get('event_id')==old_session.get('event_id') and before.get('session_id')==old_session.get('session_id') and payload.get('session_id')==session.get('session_id'))
                        old_bars={b['open_ms']:b for b in before.get('bars',[])}
                        from .control_contract import observation_bar_source
                        changed=any(b['open_ms'] in old_bars and _canonical(observation_bar_source(b))!=_canonical(observation_bar_source(old_bars[b['open_ms']])) for b in payload.get('bars',[]))
                        observation_revision=observation_revision and changed
                if not (signal_revision or observation_revision) or old_session.get('end_reason')!=SUPERSEDED or old_session.get('schema_version')!=CONTROL_SCHEMA:
                    raise LedgerReject('CONTROL_retirement_requires_verified_source_revision')
                if not session or old_session["session_id"] == session["session_id"] or old_session.get("human_stopped"):
                    raise LedgerReject("CONTROL_illegal_profile_retirement")
                self._apply_session_conn(conn, old_session, old_transitions, now)
            sstate = self._apply_session_conn(conn, session, transitions, now)
            if session is not None:
                result.update(event_id=result.get("event_id") or session["event_id"], session_id=session["session_id"],
                              observation_status=session["observation_status"], session_state=sstate,
                              transition_ids=[t["transition_id"] for t in transitions])
            if gaps:
                self._write_gaps_conn(conn, gaps, now)
                from .recovery_replay import capture_gap_contexts_conn
                from datetime import datetime
                capture_gap_contexts_conn(conn,gaps,message,clock_ms=int(datetime.fromisoformat(now).timestamp()*1000))
                result["gap_ids"] = [g["gap_id"] for g in gaps]
            if cursor is not None:
                self._write_cursor_conn(conn, cursor, now)
                result["cursor"] = [cursor[0], cursor[1]]
            for cursor_name, cursor_value in (cursor_updates or {}).items():
                self._write_cursor_conn(conn, (cursor_name, cursor_value), now)
            for notice in extra_outbox or []:
                self._write_outbox_conn(conn, notice["kind"], notice["payload"], notice["key"], now)
            if supersede_replay_event_id:
                n = self._supersede_pending_plans_conn(conn, supersede_replay_event_id, now)
                result["replay_plans_superseded"] = n
            if replay_plan is not None:
                pid = self._upsert_replay_plan_conn(conn, replay_plan, now)
                result["replay_plan_id"] = pid
                result["replay_plan_status"] = replay_plan.get("status")
                result["quality_status"] = replay_plan.get("quality_status")
            if complete_replay_plan_id:
                self._complete_replay_plan_conn(conn, complete_replay_plan_id, now)
                result["replay_plan_completed"] = complete_replay_plan_id
            if outbox_kind:
                body = outbox_payload or {"event_id": result.get("event_id"), "revision": result.get("revision")}
                result["outbox_id"] = self._write_outbox_conn(conn, outbox_kind, body,
                                                              (msg_row or {}).get("source_message_key"), now)
            # Optional source job effect ACK + watermark in SAME transaction
            if job_effect is not None:
                self._validate_business_effect_conn(conn, job_effect, record=record, session=session)
                je = self._apply_job_effect_conn(conn, job_effect, now=now)
                result["job_effect_ack"] = je
            # C3-a projection bump (same transaction; fail → full rollback)
            if recovery_effect is not None:
                from .recovery_effects import apply_effect_conn
                from datetime import datetime
                result['recovery_effect_ack'] = apply_effect_conn(conn,recovery_effect,record=record,session=session,message=message,
                    cursor_updates=cursor_updates,now_ms=int(datetime.fromisoformat(now).timestamp()*1000))
            if record is not None or session is not None or transitions or gaps:
                if record is None and session is None and gaps:
                    gap_sid = next((g.get("session_id") for g in gaps if g.get("session_id")), None)
                    gap_row = conn.execute("SELECT payload_json FROM sessions WHERE session_id=?", (gap_sid,)).fetchone() if gap_sid else None
                    session = json.loads(gap_row[0]) if gap_row else None
                self._bump_projection_conn(
                    conn, record=record, session=session,
                    transitions=transitions, now=now,
                )
            if msg_row is not None:
                result["inbox_id"] = int(msg_row["inbox_id"])
                result["source_message_key"] = msg_row["source_message_key"]
                self._ack_inbox_conn(conn, int(msg_row["inbox_id"]), request_sha, result, now)
            conn.execute("COMMIT")
            return result
        except LedgerReject as rej:
            self._rollback(conn)
            return {"ok": False, "errors": rej.as_errors()}
        except Exception as exc:  # any sqlite / IO failure -> nothing applied, reported upward
            self._rollback(conn)
            return {"ok": False, "errors": [dict(code="STORE_write_failed", error=f"{type(exc).__name__}: {exc}")]}
        finally:
            conn.close()

    def _ack_inbox_conn(self, conn: sqlite3.Connection, inbox_id: int, request_sha: str, result: dict, now: str) -> None:
        cur = conn.execute(
            "UPDATE inbox SET status='acked', acked_at_utc=?, request_sha256=?, result_json=? "
            "WHERE inbox_id=? AND status='pending'", (now, request_sha, _canonical(result), inbox_id))
        if cur.rowcount != 1:
            raise LedgerReject("INBOX_ack_failed", inbox_id=inbox_id)

    @staticmethod
    def _rollback(conn: sqlite3.Connection) -> None:
        try:
            conn.execute("ROLLBACK")
        except Exception:
            pass

    # ------------------------------------------------------------------ public write entries (all -> _commit)
    def append_revision(self, record: dict, *, outbox_kind: str | None = None, outbox_payload: dict | None = None,
                        cursor: tuple[str, str] | None = None, session: dict | None = None,
                        transition: dict | None = None, transitions: list[dict] | None = None,
                        gaps: list[dict] | None = None, inbox_ack_id: int | None = None,
                        source_message_key: str | None = None) -> dict:
        """Validate then append one revision (+ optional companions) in ONE transaction.

        Companions go through the same guarded writers as every other entry. With a
        source_message_key / inbox_ack_id this is a consume (see consume_and_commit).
        """
        if not isinstance(record, dict):
            raise TypeError("record must be dict")
        trs = list(transitions or []) + ([transition] if transition is not None else [])
        message = None
        if source_message_key is not None or inbox_ack_id is not None:
            message = {"key": source_message_key, "inbox_ack_id": inbox_ack_id}
        return self._commit(record=record, session=session, transitions=trs, gaps=gaps, cursor=cursor,
                            outbox_kind=outbox_kind, outbox_payload=outbox_payload, message=message)

    def upsert_session(self, session: dict, *, transition: dict | None = None,
                       transitions: list[dict] | None = None) -> dict:
        trs = list(transitions or []) + ([transition] if transition is not None else [])
        return self._commit(session=session, transitions=trs)

    def consume_and_commit(self, *, source_message_key: str | None, record: dict | None = None,
                           session: dict | None = None, transition: dict | None = None,
                           transitions: list[dict] | None = None, gaps: list[dict] | None = None,
                           cursor: tuple[str, str] | None = None, outbox_kind: str | None = None,
                           outbox_payload: dict | None = None, inbox_ack_id: int | None = None,
                           message_source: str | None = None, message_payload: dict | None = None,
                           replay_plan: dict | None = None, complete_replay_plan_id: str | None = None,
                           supersede_replay_event_id: str | None = None,
                           job_effect: dict | None = None, cursor_updates: dict | None = None,
                           expected_cursors: dict | None = None, extra_outbox: list | None = None, retired_sessions: list | None = None, recovery_effect: dict | None = None) -> dict:
        """Consume one inbox message: apply its effects and ACK the ORIGINAL inbox row atomically.

        - pending row (or no row: direct consume creates it) -> apply + ack in one transaction
        - acked row + identical commit request -> idempotent replay of the stored result
        - acked row + different request, or payload/source/ack-id mismatch -> explicit reject
        - optional job_effect binds source job ACK + watermark in the SAME transaction
        """
        trs = list(transitions or []) + ([transition] if transition is not None else [])
        message = {"key": source_message_key, "inbox_ack_id": inbox_ack_id, "source": message_source,
                   "payload": message_payload}
        return self._commit(record=record, session=session, transitions=trs, gaps=gaps, cursor=cursor,
                            outbox_kind=outbox_kind, outbox_payload=outbox_payload, message=message,
                            replay_plan=replay_plan, complete_replay_plan_id=complete_replay_plan_id,
                            supersede_replay_event_id=supersede_replay_event_id,
                            job_effect=job_effect, cursor_updates=cursor_updates,
                            expected_cursors=expected_cursors, extra_outbox=extra_outbox,
                            retired_sessions=retired_sessions, recovery_effect=recovery_effect)

    def import_prefix(self, records: list[dict]) -> dict:
        trusted, errs = import_validated_prefix(records, self.schema)
        if errs:
            return {"ok": False, "errors": errs, "written": 0}
        written = 0
        for rec in trusted:
            res = self.append_revision(rec)
            if not res.get("ok"):
                return {"ok": False, "errors": res.get("errors"), "written": written}
            if not res.get("already_exists"):
                written += 1
        return {"ok": True, "written": written, "event_id": trusted[0]["event_id"] if trusted else None}

    def set_cursor(self, name: str, value: str) -> dict:
        return self._commit(cursor=(name, value))

    def enqueue_inbox(self, source: str, payload: dict, *, source_message_key: str | None = None) -> dict:
        """Enqueue a pending message. A key is bound to source + payload sha256.

        Same key + same source + same payload -> idempotent (returns current status).
        Same key with different source or payload -> explicit INBOX_key_conflict.
        """
        pj = _canonical(payload)
        psha = _sha256_text(pj)
        if source_message_key is None:
            source_message_key = f"{source}|{psha}"
        now = _utc()
        conn = self._connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("SELECT * FROM inbox WHERE source_message_key=?", (source_message_key,)).fetchone()
            if row is not None:
                conn.execute("COMMIT")
                if row["source"] != source or row["payload_sha256"] != psha:
                    return {"ok": False, "errors": [dict(code="INBOX_key_conflict", source_message_key=source_message_key,
                                                         detail="same key, different source/payload")],
                            "inbox_id": int(row["inbox_id"]), "status": row["status"]}
                return {"ok": True, "inbox_id": int(row["inbox_id"]), "idempotent": True, "already_exists": True,
                        "status": row["status"], "source_message_key": source_message_key}
            cur = conn.execute(
                "INSERT INTO inbox(source,source_message_key,payload_json,payload_sha256,status,created_at_utc) "
                "VALUES(?,?,?,?,?,?)", (source, source_message_key, pj, psha, "pending", now))
            inbox_id = int(cur.lastrowid)
            conn.execute("COMMIT")
            return {"ok": True, "inbox_id": inbox_id, "idempotent": False, "status": "pending",
                    "source_message_key": source_message_key}
        except Exception as exc:
            self._rollback(conn)
            return {"ok": False, "errors": [dict(code="INBOX_enqueue_failed", error=f"{type(exc).__name__}: {exc}")]}
        finally:
            conn.close()

    def ack_inbox(self, inbox_id: int) -> dict:
        """Pending messages are ACKed only by consume_and_commit together with their effects."""
        rows = self._read("SELECT status FROM inbox WHERE inbox_id=?", (inbox_id,))
        if not rows:
            return {"ok": False, "errors": [dict(code="INBOX_not_found", inbox_id=inbox_id)]}
        if rows[0]["status"] == "acked":
            return {"ok": True, "inbox_id": inbox_id, "status": "acked", "idempotent": True}
        return {"ok": False, "errors": [dict(code="INBOX_ack_requires_consume", inbox_id=inbox_id)],
                "status": rows[0]["status"]}

    def mark_outbox_delivered(self, outbox_id: int) -> dict:
        now = _utc()
        conn = self._connect()
        try:
            row = conn.execute("SELECT status, delivered_at_utc, read_at_utc FROM outbox WHERE outbox_id=?", (outbox_id,)).fetchone()
            if row is None:
                return {"ok": False, "errors": [dict(code="OUTBOX_not_found", outbox_id=outbox_id)]}
            if row["delivered_at_utc"]:
                return {"ok": True, "outbox_id": outbox_id, "status": row["status"], "idempotent": True,
                        "delivered": True, "read": bool(row["read_at_utc"])}
            conn.execute("UPDATE outbox SET status=?, delivered_at_utc=? WHERE outbox_id=? AND delivered_at_utc IS NULL", ("delivered", now, outbox_id))
            row = conn.execute("SELECT status, delivered_at_utc, read_at_utc FROM outbox WHERE outbox_id=?", (outbox_id,)).fetchone()
        finally:
            conn.close()
        return {"ok": True, "outbox_id": outbox_id, "status": row["status"], "delivered": bool(row["delivered_at_utc"]), "read": bool(row["read_at_utc"])}

    def mark_outbox_read(self, outbox_id: int) -> dict:
        """Mark read only after delivered. Distinguishes delivered vs read."""
        now = _utc()
        conn = self._connect()
        try:
            row = conn.execute("SELECT status, delivered_at_utc, read_at_utc FROM outbox WHERE outbox_id=?", (outbox_id,)).fetchone()
            if row is None:
                return {"ok": False, "errors": [dict(code="OUTBOX_not_found", outbox_id=outbox_id)]}
            if not row["delivered_at_utc"]:
                return {"ok": False, "errors": [dict(code="OUTBOX_not_delivered", outbox_id=outbox_id)]}
            if row["read_at_utc"]:
                return {"ok": True, "outbox_id": outbox_id, "status": "read", "idempotent": True, "delivered": True, "read": True}
            conn.execute("UPDATE outbox SET status=?, read_at_utc=? WHERE outbox_id=? AND read_at_utc IS NULL", ("read", now, outbox_id))
        finally:
            conn.close()
        return {"ok": True, "outbox_id": outbox_id, "status": "read", "delivered": True, "read": True}

    def list_outbox(self, *, status: str | None = None) -> list[dict]:
        sql = ("SELECT outbox_id, kind, payload_json, status, source_message_key, created_at_utc, delivered_at_utc, "
               "read_at_utc FROM outbox")
        rows = self._read(sql + " WHERE status=? ORDER BY outbox_id", (status,)) if status else self._read(sql + " ORDER BY outbox_id")
        return [{"outbox_id": r["outbox_id"], "kind": r["kind"], "payload": json.loads(r["payload_json"]),
                 "status": r["status"], "source_message_key": r["source_message_key"],
                 "created_at_utc": r["created_at_utc"], "delivered_at_utc": r["delivered_at_utc"],
                 "read_at_utc": r["read_at_utc"]} for r in rows]


    def _upsert_replay_plan_conn(self, conn: sqlite3.Connection, plan: dict, now: str) -> str:
        pid = plan["plan_id"]
        detail = plan.get("detail") if isinstance(plan.get("detail"), dict) else (plan.get("detail") or {})
        args = (
            plan.get("event_id"), plan.get("session_id"), plan["status"],
            plan.get("replay_from_effective_ms"), plan.get("watermark_at_plan_ms"),
            plan.get("quality_status") or "pending_history_replay",
            _canonical(plan.get("prior_sources") or []),
            _canonical(plan.get("new_sources") or []),
            plan.get("first_available_at"),
            _canonical(detail), now, now, pid,
        )
        existing = conn.execute("SELECT plan_id FROM replay_plans WHERE plan_id=?", (pid,)).fetchone()
        if existing is None:
            conn.execute(
                "INSERT INTO replay_plans(event_id,session_id,status,replay_from_effective_ms,"
                "watermark_at_plan_ms,quality_status,prior_sources_json,new_sources_json,"
                "first_available_at,detail_json,created_at_utc,updated_at_utc,plan_id) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)", args)
        else:
            conn.execute(
                "UPDATE replay_plans SET event_id=?, session_id=?, status=?, replay_from_effective_ms=?,"
                "watermark_at_plan_ms=?, quality_status=?, prior_sources_json=?, new_sources_json=?,"
                "first_available_at=?, detail_json=?, updated_at_utc=? WHERE plan_id=?",
                args[:-1] + (pid,))
        return pid

    def _supersede_pending_plans_conn(self, conn: sqlite3.Connection, event_id: str, now: str) -> int:
        cur = conn.execute(
            "UPDATE replay_plans SET status='superseded', updated_at_utc=? "
            "WHERE event_id=? AND status='pending'", (now, event_id))
        return int(cur.rowcount)

    def _complete_replay_plan_conn(self, conn: sqlite3.Connection, plan_id: str, now: str) -> None:
        cur = conn.execute(
            "UPDATE replay_plans SET status='completed', quality_status='replay_applied', updated_at_utc=? "
            "WHERE plan_id=? AND status='pending'", (now, plan_id))
        if cur.rowcount != 1:
            raise LedgerReject("REPLAY_PLAN_complete_failed", plan_id=plan_id)

    def get_pending_replay_plan(self, event_id: str) -> dict | None:
        rows = self._read(
            "SELECT * FROM replay_plans WHERE event_id=? AND status='pending' "
            "ORDER BY created_at_utc DESC LIMIT 1", (event_id,))
        if not rows:
            return None
        return self._row_to_replay_plan(rows[0])

    def list_replay_plans(self, event_id: str | None = None) -> list[dict]:
        if event_id:
            rows = self._read("SELECT * FROM replay_plans WHERE event_id=? ORDER BY created_at_utc", (event_id,))
        else:
            rows = self._read("SELECT * FROM replay_plans ORDER BY created_at_utc")
        return [self._row_to_replay_plan(r) for r in rows]

    @staticmethod
    def _row_to_replay_plan(r: sqlite3.Row) -> dict:
        return {
            "plan_id": r["plan_id"],
            "event_id": r["event_id"],
            "session_id": r["session_id"],
            "status": r["status"],
            "replay_from_effective_ms": r["replay_from_effective_ms"],
            "watermark_at_plan_ms": r["watermark_at_plan_ms"],
            "quality_status": r["quality_status"],
            "prior_sources": json.loads(r["prior_sources_json"]),
            "new_sources": json.loads(r["new_sources_json"]),
            "first_available_at": r["first_available_at"],
            "detail": json.loads(r["detail_json"]),
            "created_at_utc": r["created_at_utc"],
            "updated_at_utc": r["updated_at_utc"],
        }

    def stats(self) -> dict:
        conn = self._connect()
        try:
            c = lambda sql: conn.execute(sql).fetchone()[0]  # noqa: E731
            out = {
                "path": str(self.path),
                "revisions": c("SELECT COUNT(*) FROM event_revisions"),
                "events": c("SELECT COUNT(DISTINCT event_id) FROM event_revisions"),
                "inbox": c("SELECT COUNT(*) FROM inbox"),
                "inbox_pending": c("SELECT COUNT(*) FROM inbox WHERE status='pending'"),
                "outbox": c("SELECT COUNT(*) FROM outbox"),
                "sessions": c("SELECT COUNT(*) FROM sessions"),
                "transitions": c("SELECT COUNT(*) FROM session_transitions"),
                "gaps": c("SELECT COUNT(*) FROM gaps"),
                "replay_plans": c("SELECT COUNT(*) FROM replay_plans"),
                "replay_plans_pending": c("SELECT COUNT(*) FROM replay_plans WHERE status='pending'"),
                "tables": [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")],
                "production_wired": False,
            }
        finally:
            conn.close()
        return out


    # ------------------------------------------------------------------ C2-b: source receipts / jobs / watermarks
    # Formal correctness authority for per-source watermarks and ACK lives HERE
    # (same SQLite as the ledger). JSON PerSourceWatermarkStore / RecoveryAckJournal
    # in source_adapters.py are non-formal helpers only.
    #
    # Two ACK layers (plan 4.4):
    #   receipt ACK  = envelope + recoverable dispatch state persisted
    #                  (dispatch_state in received/dispatched; receipt_acked_at set)
    #   effect ACK   = job effects + watermark advance in ONE transaction
    #                  (job.state=effect_acked; effect_acked_at set)
    # Receipt ACK never implies every session evaluated. We do NOT claim
    # file+SQLite cross-store atomicity; recovery is immutable re-read + unique key.

    DISPATCH_RECEIVED = "received"
    DISPATCH_FANOUT = "fanout_pending"
    DISPATCH_DONE = "dispatched"
    DISPATCH_CONFLICT = "conflict"
    DISPATCH_REJECTED = "rejected"
    JOB_PENDING = "pending"
    JOB_RUNNING = "running"
    JOB_EFFECT_ACKED = "effect_acked"
    JOB_FAILED = "failed"
    JOB_SUPERSEDED = "superseded"
    JOB_GAP_PAUSED = "gap_paused"

    @staticmethod
    def _key_part(value: str) -> str:
        """Length-prefixed part so embedded separators cannot collide (F4)."""
        return f"{len(value)}:{value}"

    @staticmethod
    def build_message_key(*, source: str, source_ref: str, version: str, payload_hash: str | None = None) -> str:
        """Canonical unambiguous receipt key (F4).

        payload_hash is validated when provided but is NOT part of the key, so same key +
        different hash is detected as RECEIPT_key_hash_conflict.
        """
        if not all(isinstance(x, str) and x.strip() for x in (source, source_ref, version)):
            raise LedgerReject("RECEIPT_illegal_key_parts")
        if payload_hash is not None:
            ph = str(payload_hash).lower()
            if len(ph) != 64 or any(c not in "0123456789abcdef" for c in ph):
                raise LedgerReject("RECEIPT_illegal_payload_hash", payload_hash=payload_hash)
        return (
            f"{TrackingLedger._key_part(source)}|"
            f"{TrackingLedger._key_part(source_ref)}|"
            f"{TrackingLedger._key_part(version)}"
        )

    @staticmethod
    def _bound_identity_from_receipt_job(receipt_row, job_row, envelope: dict) -> dict:
        """Derive watermark identity solely from persisted receipt+job (F2)."""
        market = envelope.get("market")
        symbol = envelope.get("symbol")
        if isinstance(market, str) and market.strip() and isinstance(symbol, str) and symbol.strip():
            contract_key = f"{market}|{symbol}"
        elif isinstance(envelope.get("contract_key"), str) and envelope["contract_key"].strip():
            contract_key = envelope["contract_key"]
        else:
            raise LedgerReject("WATERMARK_receipt_missing_contract")
        timeframe = envelope.get("timeframe")
        if not isinstance(timeframe, str) or not timeframe.strip():
            raise LedgerReject("WATERMARK_receipt_missing_timeframe")
        return {
            "source": receipt_row["source"],
            "contract_key": contract_key,
            "timeframe": timeframe,
            "consumer_key": job_row["consumer_key"],
            "version_set_hash": version_set_hash([receipt_row["version"]]),
        }

    @staticmethod
    def _normalize_fanout_plan(jobs: list) -> list[dict]:
        plan = []
        seen = set()
        for j in jobs:
            if not isinstance(j, dict):
                raise LedgerReject("JOB_illegal_spec")
            consumer = j.get("consumer_key")
            if not isinstance(consumer, str) or not consumer.strip():
                raise LedgerReject("JOB_illegal_consumer_key")
            if consumer in seen:
                raise LedgerReject("JOB_duplicate_consumer_in_plan", consumer_key=consumer)
            seen.add(consumer)
            item = {"consumer_key": consumer}
            if j.get("session_id") is not None:
                item["session_id"] = j.get("session_id")
            if j.get("replay_plan_id") is not None:
                item["replay_plan_id"] = j.get("replay_plan_id")
            if j.get("next_close_ms") is not None:
                nc = j.get("next_close_ms")
                if not isinstance(nc, int) or isinstance(nc, bool) or nc < 0:
                    raise LedgerReject("JOB_illegal_next_close_ms", next_close_ms=nc)
                item["next_close_ms"] = nc
            if j.get("job_key") is not None:
                item["job_key"] = j.get("job_key")
            plan.append(item)
        return plan

    def _maybe_crash(self, token: str | None, at: str) -> None:
        """Test-only crash injection: raise before COMMIT at a named boundary."""
        if token is not None and token == at:
            raise RuntimeError(f"C2B_CRASH_INJECT:{at}")


    def _bump_projection_conn(self, conn: sqlite3.Connection, *, record: dict | None,
                              session: dict | None, transitions: list[dict], now: str) -> int:
        """C3-a: compact summary + effective members in SAME write transaction."""
        cur = conn.execute(
            "INSERT INTO tracking_commits(recorded_at, schema_version, source_tag) VALUES(?,?,?)",
            (now, SCHEMA_VERSION, "codex-abc-tracking-20261019-v3"),
        )
        seq = int(cur.lastrowid)
        event_id = None
        if record is not None:
            event_id = record.get("event_id")
        elif session is not None:
            event_id = session.get("event_id")
        if event_id:
            # close previous open summary interval
            conn.execute(
                "UPDATE tracking_summary_versions SET valid_to_seq=? "
                "WHERE event_id=? AND valid_to_seq IS NULL",
                (seq, event_id),
            )
            sess = session
            if sess is None:
                row = conn.execute(
                    "SELECT payload_json FROM sessions WHERE event_id=? ORDER BY rowid DESC LIMIT 1",
                    (event_id,),
                ).fetchone()
                sess = json.loads(row["payload_json"]) if row else None
            head = record
            if head is None:
                row = conn.execute("SELECT payload_json FROM event_revisions WHERE event_id=? ORDER BY revision DESC LIMIT 1", (event_id,)).fetchone()
                head = json.loads(row["payload_json"]) if row else {}
            context_row = conn.execute("SELECT cursor_value FROM cursors WHERE cursor_name=?", ("tracking.context|" + event_id,)).fetchone()
            context = json.loads(context_row[0]) if context_row else {}
            cycle_sid = (event_id + "|C_waiting_observation|tracking-observation-v1.1.0") if (head or {}).get("strategy") == "C" and not sess else None
            open_gap_count = 0
            sid = (sess or {}).get("session_id") or cycle_sid
            if sid:
                open_gap_count = int(conn.execute(
                    "SELECT COUNT(*) AS c FROM gaps WHERE session_id=? AND status=?",
                    (sid, "open"),
                ).fetchone()["c"])
            summary = {
                "event_id": event_id,
                "strategy": (head or {}).get("strategy") or "A",
                "revision_count": int(conn.execute("SELECT COUNT(*) FROM event_revisions WHERE event_id=?", (event_id,)).fetchone()[0]),
                "evidence_hash": (head or {}).get("evidence_hash"),
                "session_id": sid,
                "context": context,
                "market": (head or {}).get("market") or (sess or {}).get("market"),
                "symbol": (head or {}).get("symbol") or (sess or {}).get("symbol"),
                "decision_close_ms": (head or {}).get("decision_bar_close_ms")
                    or (head or {}).get("trigger_bar_close_ms"),
                "original_confirmation": {"technical_status": (head or {}).get("technical_status"), "confirm": (head or {}).get("confirm"), "trigger_bar_close_ms": (head or {}).get("trigger_bar_close_ms"), "first_seen_at": (head or {}).get("first_seen_at"), "available_at": (head or {}).get("available_at")},
                "qualification": {"status": (head or {}).get("qualification_status")},
                "observation": context.get("observation") if context.get("observation") and (not sess or (head or {}).get("technical_status")=="waiting") else {
                    "status": (sess or {}).get("observation_status"),
                    "last_evaluated_bar_close_ms": (sess or {}).get("last_evaluated_bar_close_ms"),
                    "end_reason": (sess or {}).get("end_reason"),
                    "profile_id": (sess or {}).get("tracking_profile_id") or (sess or {}).get("profile_id"),
                },
                "data_quality": {
                    "summary": (
                        "incomplete"
                        if ((head or {}).get("missing_data_flags") or open_gap_count > 0)
                        else "ok"
                    ),
                    "missing_data_flags": list((head or {}).get("missing_data_flags") or []),
                    "open_gap_count": open_gap_count,
                },
                "historical_import": bool(
                    (head or {}).get("discovery") in ("import", "historical_import", "historical_replay", "late_backfill")
                    or (head or {}).get("historical")
                ),
            }
            conn.execute(
                "INSERT INTO tracking_summary_versions("
                "event_id, session_id, market, symbol, decision_close_ms, summary_json, valid_from_seq, valid_to_seq)"
                " VALUES(?,?,?,?,?,?,?,NULL)",
                (
                    event_id,
                    summary.get("session_id"),
                    summary.get("market"),
                    summary.get("symbol"),
                    summary.get("decision_close_ms"),
                    _canonical(summary),
                    seq,
                ),
            )
            # rebuild effective members for this event from full chain (shared builder)
            all_tr = [
                json.loads(r["payload_json"])
                for r in conn.execute(
                    "SELECT payload_json FROM session_transitions WHERE event_id=? ORDER BY seq ASC",
                    (event_id,),
                ).fetchall()
            ]
            # include pending transitions about to be inserted? caller passes transitions already inserted
            from .effective_chain import build_effective_transitions
            eff = build_effective_transitions(all_tr)
            conn.execute(
                "UPDATE tracking_effective_members SET valid_to_seq=? "
                "WHERE event_id=? AND valid_to_seq IS NULL",
                (seq, event_id),
            )
            for i, t in enumerate(eff, 1):
                conn.execute(
                    "INSERT INTO tracking_effective_members("
                    "event_id, session_id, transition_id, page_seq, payload_json, valid_from_seq, valid_to_seq)"
                    " VALUES(?,?,?,?,?,?,NULL)",
                    (
                        event_id,
                        t.get("session_id") or (sess or {}).get("session_id") or "",
                        t.get("transition_id") or f"anon-{i}",
                        i,
                        _canonical(t),
                        seq,
                    ),
                )
        return seq

    def accept_source_receipt(
        self,
        envelope: dict,
        *,
        jobs: list[dict] | None = None,
        mark_dispatched: bool = False,
        crash_after: str | None = None,
    ) -> dict:
        """Persist one source receipt (+ optional fanout jobs) and set receipt ACK.

        Idempotent when canonical message_key + full identity + payload_hash match.
        Same key + different hash -> conflict isolation (no overwrite).
        Replay after durable receive / partial fanout idempotently fills missing jobs (F3).
        External message_key must match the canonical derived key exactly (F4).
        """
        if not isinstance(envelope, dict):
            return {"ok": False, "errors": [dict(code="RECEIPT_envelope_not_dict")]}
        required = ("source", "source_ref", "version", "payload_hash")
        missing = [k for k in required if not isinstance(envelope.get(k), str) or not str(envelope.get(k)).strip()]
        if missing:
            return {"ok": False, "errors": [dict(code="RECEIPT_illegal_input", missing=missing)]}
        source = envelope["source"]
        source_ref = envelope["source_ref"]
        version = envelope["version"]
        payload_hash = str(envelope["payload_hash"]).lower()
        try:
            message_key = self.build_message_key(
                source=source, source_ref=source_ref, version=version, payload_hash=payload_hash,
            )
        except LedgerReject as rej:
            return {"ok": False, "errors": rej.as_errors()}
        external = envelope.get("message_key")
        if external is not None:
            if not isinstance(external, str) or external != message_key:
                return {
                    "ok": False,
                    "errors": [dict(
                        code="RECEIPT_message_key_mismatch",
                        expected=message_key,
                        got=external,
                    )],
                }
        try:
            plan_in = self._normalize_fanout_plan(list(jobs or []))
        except LedgerReject as rej:
            return {"ok": False, "errors": rej.as_errors()}
        now = _utc()
        env_json = _canonical(envelope)
        conn = self._connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            self._maybe_crash(crash_after, "after_begin")
            row = conn.execute(
                "SELECT * FROM source_receipts WHERE message_key=?", (message_key,)
            ).fetchone()
            if row is not None:
                # F4: same key retry must also match source/ref/version (not only hash)
                if (row["source"] != source or row["source_ref"] != source_ref
                        or row["version"] != version):
                    raise LedgerReject(
                        "RECEIPT_key_identity_conflict",
                        message_key=message_key,
                        have={"source": row["source"], "source_ref": row["source_ref"],
                              "version": row["version"]},
                        got={"source": source, "source_ref": source_ref, "version": version},
                    )
                if row["payload_hash"] != payload_hash:
                    conn.execute(
                        "UPDATE source_receipts SET dispatch_state=? WHERE message_key=? AND dispatch_state!=?",
                        (self.DISPATCH_CONFLICT, message_key, self.DISPATCH_CONFLICT),
                    )
                    self._maybe_crash(crash_after, "before_conflict_commit")
                    conn.execute("COMMIT")
                    return {
                        "ok": False,
                        "errors": [dict(
                            code="RECEIPT_key_hash_conflict",
                            message_key=message_key,
                            have=row["payload_hash"],
                            got=payload_hash,
                        )],
                        "dispatch_state": self.DISPATCH_CONFLICT,
                        "receipt_acked": bool(row["receipt_acked_at"]),
                    }
                # F2: conflict/rejected is sticky — original-hash redelivery must NOT clear it
                # and must not reopen consumption / watermark progress via this path.
                if row["dispatch_state"] in (self.DISPATCH_CONFLICT, self.DISPATCH_REJECTED):
                    self._maybe_crash(crash_after, "before_sticky_conflict_commit")
                    conn.execute("COMMIT")
                    return {
                        "ok": False,
                        "errors": [dict(
                            code="RECEIPT_conflict_sticky",
                            message_key=message_key,
                            dispatch_state=row["dispatch_state"],
                        )],
                        "dispatch_state": row["dispatch_state"],
                        "receipt_acked": bool(row["receipt_acked_at"]),
                        "idempotent": True,
                    }
                # F3: durable receive / partial fanout replay — merge plan, fill missing jobs
                existing_plan = []
                if row["fanout_plan_json"]:
                    try:
                        existing_plan = json.loads(row["fanout_plan_json"])
                    except Exception:
                        existing_plan = []
                if not isinstance(existing_plan, list):
                    existing_plan = []
                by_consumer = {p["consumer_key"]: dict(p) for p in existing_plan
                               if isinstance(p, dict) and p.get("consumer_key")}
                for p in plan_in:
                    prev = by_consumer.get(p["consumer_key"])
                    if prev is None:
                        by_consumer[p["consumer_key"]] = dict(p)
                    else:
                        # validate existing plan/job contract on overlap
                        for fld in ("session_id", "replay_plan_id", "next_close_ms", "job_key"):
                            if fld in p and fld in prev and p[fld] != prev[fld]:
                                raise LedgerReject(
                                    "JOB_plan_contract_conflict",
                                    consumer_key=p["consumer_key"], field=fld,
                                    have=prev[fld], got=p[fld],
                                )
                        merged = dict(prev)
                        merged.update(p)
                        by_consumer[p["consumer_key"]] = merged
                merged_plan = list(by_consumer.values())
                existing_jobs = {
                    r["consumer_key"]: r
                    for r in conn.execute(
                        "SELECT * FROM source_jobs WHERE message_key=?", (message_key,),
                    ).fetchall()
                }
                created = []
                for p in merged_plan:
                    consumer = p["consumer_key"]
                    if consumer in existing_jobs:
                        ej = existing_jobs[consumer]
                        if p.get("next_close_ms") is not None and ej["next_close_ms"] is not None:
                            if p["next_close_ms"] != ej["next_close_ms"]:
                                raise LedgerReject(
                                    "JOB_existing_contract_conflict",
                                    consumer_key=consumer, field="next_close_ms",
                                    have=ej["next_close_ms"], got=p["next_close_ms"],
                                )
                        continue
                    job_key = p.get("job_key") or f"{message_key}|{consumer}"
                    next_close = p.get("next_close_ms")
                    conn.execute(
                        "INSERT INTO source_jobs(job_key,message_key,consumer_key,session_id,state,"
                        "replay_plan_id,next_close_ms,attempt_count,error_code,effect_result_json,effect_acked_at)"
                        " VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                        (job_key, message_key, consumer, p.get("session_id"), self.JOB_PENDING,
                         p.get("replay_plan_id"), next_close, 0, None, None, None),
                    )
                    created.append({"job_key": job_key, "consumer_key": consumer, "state": self.JOB_PENDING})
                conn.execute(
                    "UPDATE source_receipts SET fanout_plan_json=? WHERE message_key=?",
                    (_canonical(merged_plan), message_key),
                )
                # refresh job list
                all_jobs = conn.execute(
                    "SELECT job_key, consumer_key, state FROM source_jobs WHERE message_key=? ORDER BY job_key",
                    (message_key,),
                ).fetchall()
                have_consumers = {r["consumer_key"] for r in all_jobs}
                need_consumers = {p["consumer_key"] for p in merged_plan}
                dispatch_state = row["dispatch_state"]
                if (mark_dispatched and need_consumers and need_consumers.issubset(have_consumers)
                        and dispatch_state not in (self.DISPATCH_CONFLICT, self.DISPATCH_REJECTED)):
                    conn.execute(
                        "UPDATE source_receipts SET dispatch_state=? WHERE message_key=?",
                        (self.DISPATCH_DONE, message_key),
                    )
                    dispatch_state = self.DISPATCH_DONE
                elif (merged_plan and dispatch_state == self.DISPATCH_RECEIVED
                      and dispatch_state not in (self.DISPATCH_CONFLICT, self.DISPATCH_REJECTED)):
                    conn.execute(
                        "UPDATE source_receipts SET dispatch_state=? WHERE message_key=?",
                        (self.DISPATCH_FANOUT, message_key),
                    )
                    dispatch_state = self.DISPATCH_FANOUT
                self._maybe_crash(crash_after, "before_idempotent_commit")
                conn.execute("COMMIT")
                return {
                    "ok": True,
                    "idempotent": len(created) == 0,
                    "message_key": message_key,
                    "dispatch_state": dispatch_state,
                    "receipt_acked": bool(row["receipt_acked_at"]),
                    "receipt_acked_at": row["receipt_acked_at"],
                    "jobs": [dict(job_key=r["job_key"], consumer_key=r["consumer_key"], state=r["state"])
                             for r in all_jobs],
                    "jobs_filled": created,
                }

            # Fresh receipt insert
            if plan_in and not mark_dispatched:
                dispatch_state = self.DISPATCH_FANOUT
            elif mark_dispatched and plan_in:
                dispatch_state = self.DISPATCH_DONE
            elif mark_dispatched and not plan_in:
                dispatch_state = self.DISPATCH_DONE
            else:
                dispatch_state = self.DISPATCH_RECEIVED

            conn.execute(
                "INSERT INTO source_receipts(message_key,source,source_ref,version,payload_hash,"
                "envelope_json,ingested_at,dispatch_state,receipt_acked_at,fanout_plan_json) "
                "VALUES(?,?,?,?,?,?,?,?,?,?)",
                (message_key, source, source_ref, version, payload_hash, env_json, now,
                 dispatch_state, now, _canonical(plan_in)),
            )
            self._maybe_crash(crash_after, "after_receipt_insert")
            created = []
            for j in plan_in:
                consumer = j["consumer_key"]
                job_key = j.get("job_key") or f"{message_key}|{consumer}"
                next_close = j.get("next_close_ms")
                conn.execute(
                    "INSERT INTO source_jobs(job_key,message_key,consumer_key,session_id,state,"
                    "replay_plan_id,next_close_ms,attempt_count,error_code,effect_result_json,effect_acked_at)"
                    " VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                    (job_key, message_key, consumer, j.get("session_id"), self.JOB_PENDING,
                     j.get("replay_plan_id"), next_close, 0, None, None, None),
                )
                created.append({"job_key": job_key, "consumer_key": consumer, "state": self.JOB_PENDING})
            self._maybe_crash(crash_after, "after_jobs_insert")
            if mark_dispatched and plan_in:
                # Only dispatched when all planned jobs landed in this same tx
                conn.execute(
                    "UPDATE source_receipts SET dispatch_state=? WHERE message_key=?",
                    (self.DISPATCH_DONE, message_key),
                )
                dispatch_state = self.DISPATCH_DONE
            self._maybe_crash(crash_after, "before_commit")
            conn.execute("COMMIT")
            return {
                "ok": True,
                "idempotent": False,
                "message_key": message_key,
                "dispatch_state": dispatch_state,
                "receipt_acked": True,
                "receipt_acked_at": now,
                "jobs": created,
            }
        except LedgerReject as rej:
            self._rollback(conn)
            return {"ok": False, "errors": rej.as_errors()}
        except Exception as exc:
            self._rollback(conn)
            if str(exc).startswith("C2B_CRASH_INJECT:"):
                return {"ok": False, "errors": [dict(code="C2B_CRASH_INJECT", at=str(exc).split(":", 1)[1])],
                        "crashed": True}
            return {"ok": False, "errors": [dict(code="RECEIPT_write_failed",
                                                error=f"{type(exc).__name__}: {exc}")]}
        finally:
            conn.close()

    def complete_receipt_fanout(self, message_key: str, *, crash_after: str | None = None) -> dict:
        """Mark fanout finished only when every persisted plan target has a job (F3)."""
        now = _utc()
        conn = self._connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT * FROM source_receipts WHERE message_key=?", (message_key,)
            ).fetchone()
            if row is None:
                raise LedgerReject("RECEIPT_not_found", message_key=message_key)
            if row["dispatch_state"] == self.DISPATCH_DONE:
                conn.execute("COMMIT")
                return {"ok": True, "idempotent": True, "dispatch_state": self.DISPATCH_DONE}
            if row["dispatch_state"] not in (self.DISPATCH_FANOUT, self.DISPATCH_RECEIVED):
                raise LedgerReject("RECEIPT_bad_dispatch_state", state=row["dispatch_state"])
            plan = []
            if row["fanout_plan_json"]:
                try:
                    plan = json.loads(row["fanout_plan_json"]) or []
                except Exception:
                    plan = []
            need = {p["consumer_key"] for p in plan if isinstance(p, dict) and p.get("consumer_key")}
            have = {
                r["consumer_key"]
                for r in conn.execute(
                    "SELECT consumer_key FROM source_jobs WHERE message_key=?", (message_key,),
                ).fetchall()
            }
            if need and not need.issubset(have):
                raise LedgerReject(
                    "RECEIPT_fanout_incomplete",
                    message_key=message_key,
                    missing=sorted(need - have),
                )
            conn.execute(
                "UPDATE source_receipts SET dispatch_state=? WHERE message_key=?",
                (self.DISPATCH_DONE, message_key),
            )
            self._maybe_crash(crash_after, "before_commit")
            conn.execute("COMMIT")
            return {"ok": True, "idempotent": False, "dispatch_state": self.DISPATCH_DONE,
                    "receipt_acked_at": row["receipt_acked_at"]}
        except LedgerReject as rej:
            self._rollback(conn)
            return {"ok": False, "errors": rej.as_errors()}
        except Exception as exc:
            self._rollback(conn)
            if str(exc).startswith("C2B_CRASH_INJECT:"):
                return {"ok": False, "errors": [dict(code="C2B_CRASH_INJECT", at=str(exc).split(":", 1)[1])],
                        "crashed": True}
            return {"ok": False, "errors": [dict(code="RECEIPT_fanout_failed",
                                                error=f"{type(exc).__name__}: {exc}")]}
        finally:
            conn.close()

    def get_source_receipt(self, message_key: str) -> dict | None:
        rows = self._read("SELECT * FROM source_receipts WHERE message_key=?", (message_key,))
        if not rows:
            return None
        r = rows[0]
        plan = None
        raw_plan = r["fanout_plan_json"] if "fanout_plan_json" in r.keys() else None
        if raw_plan:
            try:
                plan = json.loads(raw_plan)
            except Exception:
                plan = None
        return {
            "message_key": r["message_key"], "source": r["source"], "source_ref": r["source_ref"],
            "version": r["version"], "payload_hash": r["payload_hash"],
            "envelope": json.loads(r["envelope_json"]), "ingested_at": r["ingested_at"],
            "dispatch_state": r["dispatch_state"], "receipt_acked_at": r["receipt_acked_at"],
            "fanout_plan": plan,
        }

    def list_source_jobs(self, *, state: str | None = None, message_key: str | None = None) -> list[dict]:
        sql = "SELECT * FROM source_jobs"
        args: list = []
        where = []
        if state is not None:
            where.append("state=?"); args.append(state)
        if message_key is not None:
            where.append("message_key=?"); args.append(message_key)
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " ORDER BY job_key"
        rows = self._read(sql, tuple(args))
        out = []
        for r in rows:
            out.append({
                "job_key": r["job_key"], "message_key": r["message_key"],
                "consumer_key": r["consumer_key"], "session_id": r["session_id"],
                "state": r["state"], "replay_plan_id": r["replay_plan_id"],
                "next_close_ms": r["next_close_ms"], "attempt_count": r["attempt_count"],
                "error_code": r["error_code"],
                "effect_result": json.loads(r["effect_result_json"]) if r["effect_result_json"] else None,
                "effect_acked_at": r["effect_acked_at"],
            })
        return out

    def get_source_watermark(
        self, *, source: str, contract_key: str, timeframe: str,
        consumer_key: str, version_set_hash: str,
    ) -> dict | None:
        rows = self._read(
            "SELECT * FROM source_watermarks WHERE source=? AND contract_key=? AND timeframe=? "
            "AND consumer_key=? AND version_set_hash=?",
            (source, contract_key, timeframe, consumer_key, version_set_hash),
        )
        if not rows:
            return None
        r = rows[0]
        return {
            "source": r["source"], "contract_key": r["contract_key"], "timeframe": r["timeframe"],
            "consumer_key": r["consumer_key"], "version_set_hash": r["version_set_hash"],
            "requested_end_ms": r["requested_end_ms"], "fetched_max_ms": r["fetched_max_ms"],
            "continuous_end_ms": r["continuous_end_ms"], "evaluated_end_ms": r["evaluated_end_ms"],
            "first_gap_ms": r["first_gap_ms"], "quality": json.loads(r["quality_json"]),
            "updated_at": r["updated_at"],
        }

    def _validate_watermark_patch(self, prev: dict | None, patch: dict, *, gap_pause: bool) -> dict:
        """Merge patch onto previous watermark; enforce never-ahead-of-effect ordering."""
        if not isinstance(patch, dict):
            raise LedgerReject("WATERMARK_illegal_patch")
        keys = ("source", "contract_key", "timeframe", "consumer_key", "version_set_hash")
        for k in keys:
            if not isinstance(patch.get(k), str) or not patch[k].strip():
                raise LedgerReject("WATERMARK_illegal_identity", field=k)
        if prev is not None:
            for k in keys:
                if prev[k] != patch[k]:
                    raise LedgerReject("WATERMARK_identity_mismatch", field=k)
        def _ms(name, allow_none=True):
            v = patch.get(name, None if prev is None else prev.get(name))
            if v is None:
                if allow_none:
                    return None
                raise LedgerReject("WATERMARK_missing_ms", field=name)
            if not isinstance(v, int) or isinstance(v, bool) or v < 0:
                raise LedgerReject("WATERMARK_illegal_ms", field=name, got=v)
            return v
        requested = _ms("requested_end_ms")
        fetched = _ms("fetched_max_ms")
        continuous = _ms("continuous_end_ms")
        evaluated = _ms("evaluated_end_ms")
        first_gap = _ms("first_gap_ms")
        # Ordering: evaluated <= continuous <= fetched (when all set)
        # Watermark must never advance past what the effect actually committed.
        if continuous is not None and fetched is not None and continuous > fetched:
            raise LedgerReject("WATERMARK_continuous_ahead_of_fetched",
                               continuous=continuous, fetched=fetched)
        if evaluated is not None and continuous is not None and evaluated > continuous:
            raise LedgerReject("WATERMARK_evaluated_ahead_of_continuous",
                               evaluated=evaluated, continuous=continuous)
        if evaluated is not None and fetched is not None and evaluated > fetched:
            raise LedgerReject("WATERMARK_evaluated_ahead_of_fetched",
                               evaluated=evaluated, fetched=fetched)
        if gap_pause:
            if first_gap is None:
                raise LedgerReject("WATERMARK_gap_requires_first_gap_ms")
            # continuous / evaluated must not jump past the gap
            if continuous is not None and continuous > first_gap:
                raise LedgerReject("WATERMARK_continuous_past_gap",
                                   continuous=continuous, first_gap=first_gap)
            if evaluated is not None and evaluated > first_gap:
                raise LedgerReject("WATERMARK_evaluated_past_gap",
                                   evaluated=evaluated, first_gap=first_gap)
        # Monotonicity vs previous (no regression of evaluated/continuous/fetched)
        if prev is not None:
            for name, newv in (("fetched_max_ms", fetched), ("continuous_end_ms", continuous),
                               ("evaluated_end_ms", evaluated)):
                oldv = prev.get(name)
                if oldv is not None and newv is not None and newv < oldv:
                    raise LedgerReject("WATERMARK_regression", field=name, have=oldv, got=newv)
        quality = patch.get("quality")
        if quality is None:
            quality = (prev or {}).get("quality") or {}
        if not isinstance(quality, dict):
            raise LedgerReject("WATERMARK_illegal_quality")
        return {
            "source": patch["source"], "contract_key": patch["contract_key"],
            "timeframe": patch["timeframe"], "consumer_key": patch["consumer_key"],
            "version_set_hash": patch["version_set_hash"],
            "requested_end_ms": requested, "fetched_max_ms": fetched,
            "continuous_end_ms": continuous, "evaluated_end_ms": evaluated,
            "first_gap_ms": first_gap, "quality": quality,
        }

    def _validate_business_effect_conn(self, conn, spec, *, record, session):
        """Bind a source ACK to the business fact produced in this transaction."""
        effect = spec.get("effect") or {}
        eid = effect.get("event_id")
        if not eid:
            raise LedgerReject("EFFECT_business_event_required")
        fact = record or session
        if not fact or fact.get("event_id") != eid:
            raise LedgerReject("EFFECT_requires_matching_business_write", event_id=eid)
        job = conn.execute("SELECT * FROM source_jobs WHERE job_key=?", (spec.get("job_key"),)).fetchone()
        if job is None:
            raise LedgerReject("JOB_not_found")
        receipt = conn.execute("SELECT * FROM source_receipts WHERE message_key=?", (job["message_key"],)).fetchone()
        env = json.loads(receipt["envelope_json"]) if receipt else {}
        row = conn.execute("SELECT payload_json FROM event_revisions WHERE event_id=? ORDER BY revision DESC LIMIT 1", (eid,)).fetchone()
        head = record or (json.loads(row["payload_json"]) if row else {})
        if (env.get("market"), env.get("symbol"), env.get("timeframe")) != (head.get("market"), head.get("symbol"), head.get("timeframe")):
            raise LedgerReject("EFFECT_business_contract_mismatch", event_id=eid)
        sid = (session or {}).get("session_id")
        if job["session_id"] and job["session_id"] != sid:
            raise LedgerReject("EFFECT_business_session_mismatch", event_id=eid)
        profile = (session or {}).get("tracking_profile_id")
        expected_consumer = {"A_strong_observation": "a_observer_strong", "A_medium_observation": "a_observer_medium"}.get(profile)
        if expected_consumer and job["consumer_key"] != expected_consumer:
            raise LedgerReject("EFFECT_business_consumer_mismatch")
        close = effect.get("close_ms")
        if not isinstance(close, int) or isinstance(close, bool):
            raise LedgerReject("EFFECT_illegal_close_ms")
        if spec.get("gap_pause"):
            actual = (session or {}).get("last_evaluated_bar_close_ms")
            if (session or {}).get("observation_status") != "paused_awaiting_data" or actual is None or close != actual + 14_400_000:
                raise LedgerReject("EFFECT_gap_close_mismatch", actual=actual, requested=close)
        else:
            actual = ((session or {}).get("last_evaluated_bar_close_ms") if session else head.get("decision_bar_close_ms"))
            if actual != close:
                raise LedgerReject("EFFECT_business_close_mismatch", actual=actual, requested=close)

    def _apply_job_effect_conn(self, conn: sqlite3.Connection, job_effect: dict, *, now: str) -> dict:
        """Apply source job effect ACK (+ optional watermark) on an open transaction."""
        if not isinstance(job_effect, dict):
            raise LedgerReject("JOB_illegal_effect_companion")
        job_key = job_effect.get("job_key")
        effect = job_effect.get("effect")
        watermark = job_effect.get("watermark")
        gap_pause = bool(job_effect.get("gap_pause"))
        error_code = job_effect.get("error_code")
        if not isinstance(job_key, str) or not job_key.strip():
            raise LedgerReject("JOB_illegal_key")
        if effect is not None and not isinstance(effect, dict):
            raise LedgerReject("JOB_illegal_effect")
        row = conn.execute("SELECT * FROM source_jobs WHERE job_key=?", (job_key,)).fetchone()
        if row is None:
            raise LedgerReject("JOB_not_found", job_key=job_key)
        receipt = conn.execute(
            "SELECT * FROM source_receipts WHERE message_key=?", (row["message_key"],),
        ).fetchone()
        if receipt is None:
            raise LedgerReject("JOB_receipt_missing", message_key=row["message_key"])
        if receipt["dispatch_state"] in (self.DISPATCH_CONFLICT, self.DISPATCH_REJECTED):
            raise LedgerReject(
                "JOB_receipt_not_consumable",
                message_key=row["message_key"],
                dispatch_state=receipt["dispatch_state"],
            )
        envelope = json.loads(receipt["envelope_json"])
        bound = self._bound_identity_from_receipt_job(receipt, row, envelope)

        if row["state"] == self.JOB_EFFECT_ACKED and not gap_pause:
            prev_eff = json.loads(row["effect_result_json"] or "null")
            if effect is not None and prev_eff is not None and _canonical(prev_eff) != _canonical(effect):
                raise LedgerReject("JOB_effect_conflict", job_key=job_key)
            wm = None
            prev_row = conn.execute(
                "SELECT * FROM source_watermarks WHERE source=? AND contract_key=? AND timeframe=? "
                "AND consumer_key=? AND version_set_hash=?",
                (bound["source"], bound["contract_key"], bound["timeframe"],
                 bound["consumer_key"], bound["version_set_hash"]),
            ).fetchone()
            if prev_row is not None:
                wm = {
                    "source": prev_row["source"], "contract_key": prev_row["contract_key"],
                    "timeframe": prev_row["timeframe"], "consumer_key": prev_row["consumer_key"],
                    "version_set_hash": prev_row["version_set_hash"],
                    "requested_end_ms": prev_row["requested_end_ms"],
                    "fetched_max_ms": prev_row["fetched_max_ms"],
                    "continuous_end_ms": prev_row["continuous_end_ms"],
                    "evaluated_end_ms": prev_row["evaluated_end_ms"],
                    "first_gap_ms": prev_row["first_gap_ms"],
                    "quality": json.loads(prev_row["quality_json"]),
                }
            return {
                "ok": True, "idempotent": True, "job_key": job_key,
                "state": row["state"], "effect_acked_at": row["effect_acked_at"],
                "watermark": wm, "receipt_acked_separate": True,
            }
        if row["state"] not in (self.JOB_PENDING, self.JOB_RUNNING, self.JOB_GAP_PAUSED):
            raise LedgerReject("JOB_bad_state", state=row["state"], job_key=job_key)

        prev_row = conn.execute(
            "SELECT * FROM source_watermarks WHERE source=? AND contract_key=? AND timeframe=? "
            "AND consumer_key=? AND version_set_hash=?",
            (bound["source"], bound["contract_key"], bound["timeframe"],
             bound["consumer_key"], bound["version_set_hash"]),
        ).fetchone()
        prev = None
        if prev_row is not None:
            prev = {
                "source": prev_row["source"], "contract_key": prev_row["contract_key"],
                "timeframe": prev_row["timeframe"], "consumer_key": prev_row["consumer_key"],
                "version_set_hash": prev_row["version_set_hash"],
                "requested_end_ms": prev_row["requested_end_ms"],
                "fetched_max_ms": prev_row["fetched_max_ms"],
                "continuous_end_ms": prev_row["continuous_end_ms"],
                "evaluated_end_ms": prev_row["evaluated_end_ms"],
                "first_gap_ms": prev_row["first_gap_ms"],
                "quality": json.loads(prev_row["quality_json"]),
            }

        effect_close = None
        if isinstance(effect, dict) and effect.get("close_ms") is not None:
            effect_close = effect.get("close_ms")
            if not isinstance(effect_close, int) or isinstance(effect_close, bool) or effect_close < 0:
                raise LedgerReject("EFFECT_illegal_close_ms", close_ms=effect_close)

        expected_next = None
        if prev is not None and prev.get("first_gap_ms") is not None:
            expected_next = prev["first_gap_ms"]
        else:
            prefix_next = None
            if prev is not None and prev.get("continuous_end_ms") is not None:
                bar = timeframe_to_ms(bound.get("timeframe") or "")
                if bar is not None:
                    prefix_next = int(prev["continuous_end_ms"]) + bar
            job_next = row["next_close_ms"]
            if job_next is not None and prefix_next is not None:
                expected_next = job_next if job_next <= prefix_next else prefix_next
            elif prefix_next is not None:
                expected_next = prefix_next
            elif job_next is not None:
                expected_next = job_next

        if effect_close is not None and expected_next is not None and effect_close != expected_next:
            raise LedgerReject(
                "EFFECT_skip_unprocessed_root",
                expected_next_ms=expected_next,
                got_close_ms=effect_close,
                job_key=job_key,
            )

        new_state = self.JOB_GAP_PAUSED if gap_pause else (
            self.JOB_FAILED if error_code else self.JOB_EFFECT_ACKED
        )
        wm_merged = None
        if watermark is not None:
            for k in ("source", "contract_key", "timeframe", "consumer_key", "version_set_hash"):
                if watermark.get(k) != bound[k]:
                    raise LedgerReject(
                        "WATERMARK_identity_not_bound",
                        field=k, expected=bound[k], got=watermark.get(k),
                    )
            patch = dict(watermark)
            patch.update(bound)
            if not gap_pause and effect_close is None:
                raise LedgerReject("WATERMARK_requires_effect_close", job_key=job_key)
            if not gap_pause and effect_close is not None:
                for fld in ("continuous_end_ms", "evaluated_end_ms"):
                    claimed = patch.get(fld)
                    if claimed is not None and claimed > effect_close:
                        raise LedgerReject(
                            "WATERMARK_ahead_of_effect",
                            field=fld, effect_close_ms=effect_close, got=claimed,
                        )
                patch["continuous_end_ms"] = effect_close
                patch["evaluated_end_ms"] = effect_close
                if prev is not None and prev.get("first_gap_ms") is not None:
                    if effect_close == prev["first_gap_ms"]:
                        patch["first_gap_ms"] = None
                        q = dict(patch.get("quality") or prev.get("quality") or {})
                        q["gap"] = False
                        q.pop("first_gap_ms", None)
                        patch["quality"] = q
                    else:
                        raise LedgerReject(
                            "EFFECT_not_filling_first_gap",
                            first_gap_ms=prev["first_gap_ms"], got=effect_close,
                        )
            wm_merged = self._validate_watermark_patch(prev, patch, gap_pause=gap_pause)
            conn.execute(
                "INSERT INTO source_watermarks("
                "source,contract_key,timeframe,consumer_key,version_set_hash,"
                "requested_end_ms,fetched_max_ms,continuous_end_ms,evaluated_end_ms,"
                "first_gap_ms,quality_json,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?) "
                "ON CONFLICT(source,contract_key,timeframe,consumer_key,version_set_hash) DO UPDATE SET "
                "requested_end_ms=excluded.requested_end_ms,"
                "fetched_max_ms=excluded.fetched_max_ms,"
                "continuous_end_ms=excluded.continuous_end_ms,"
                "evaluated_end_ms=excluded.evaluated_end_ms,"
                "first_gap_ms=excluded.first_gap_ms,"
                "quality_json=excluded.quality_json,"
                "updated_at=excluded.updated_at",
                (wm_merged["source"], wm_merged["contract_key"], wm_merged["timeframe"],
                 wm_merged["consumer_key"], wm_merged["version_set_hash"],
                 wm_merged["requested_end_ms"], wm_merged["fetched_max_ms"],
                 wm_merged["continuous_end_ms"], wm_merged["evaluated_end_ms"],
                 wm_merged["first_gap_ms"], _canonical(wm_merged["quality"]), now),
            )

        eff_json = _canonical(effect) if effect is not None else row["effect_result_json"]
        if new_state == self.JOB_EFFECT_ACKED:
            acked_at = now
        else:
            acked_at = None
        conn.execute(
            "UPDATE source_jobs SET state=?, attempt_count=attempt_count+1, error_code=?, "
            "effect_result_json=?, effect_acked_at=? WHERE job_key=?",
            (new_state, error_code, eff_json, acked_at, job_key),
        )
        return {
            "ok": True, "idempotent": False, "job_key": job_key, "state": new_state,
            "effect_acked": new_state == self.JOB_EFFECT_ACKED,
            "effect_acked_at": acked_at, "watermark": wm_merged,
            "receipt_acked_separate": True,
        }

    def commit_job_effect(
        self,
        job_key: str,
        *,
        effect: dict | None = None,
        watermark: dict | None = None,
        gap_pause: bool = False,
        error_code: str | None = None,
        crash_after: str | None = None,
    ) -> dict:
        """Effect ACK + optional watermark advance in ONE transaction.

        F1: continuous/evaluated derived from verified effect; refuse skip-ahead of
            persisted next_close / first_gap.
        F2: watermark identity must match JOIN(job, receipt) bound contract; conflict /
            rejected receipts cannot authorize effects. Reject => full table unchanged.
        """
        if not isinstance(job_key, str) or not job_key.strip():
            return {"ok": False, "errors": [dict(code="JOB_illegal_key")]}
        if effect is not None and not isinstance(effect, dict):
            return {"ok": False, "errors": [dict(code="JOB_illegal_effect")]}
        now = _utc()
        conn = self._connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            self._maybe_crash(crash_after, "after_begin")
            row = conn.execute("SELECT * FROM source_jobs WHERE job_key=?", (job_key,)).fetchone()
            if row is None:
                raise LedgerReject("JOB_not_found", job_key=job_key)
            receipt = conn.execute(
                "SELECT * FROM source_receipts WHERE message_key=?", (row["message_key"],),
            ).fetchone()
            if receipt is None:
                raise LedgerReject("JOB_receipt_missing", message_key=row["message_key"])
            if receipt["dispatch_state"] in (self.DISPATCH_CONFLICT, self.DISPATCH_REJECTED):
                raise LedgerReject(
                    "JOB_receipt_not_consumable",
                    message_key=row["message_key"],
                    dispatch_state=receipt["dispatch_state"],
                )
            envelope = json.loads(receipt["envelope_json"])
            bound = self._bound_identity_from_receipt_job(receipt, row, envelope)

            if row["state"] == self.JOB_EFFECT_ACKED and not gap_pause:
                prev_eff = json.loads(row["effect_result_json"] or "null")
                if effect is not None and prev_eff is not None and _canonical(prev_eff) != _canonical(effect):
                    raise LedgerReject("JOB_effect_conflict", job_key=job_key)
                self._maybe_crash(crash_after, "before_idempotent_commit")
                conn.execute("COMMIT")
                wm = self.get_source_watermark(**bound)
                return {
                    "ok": True, "idempotent": True, "job_key": job_key,
                    "state": row["state"], "effect_acked_at": row["effect_acked_at"],
                    "watermark": wm,
                }
            if row["state"] not in (self.JOB_PENDING, self.JOB_RUNNING, self.JOB_GAP_PAUSED):
                raise LedgerReject("JOB_bad_state", state=row["state"], job_key=job_key)

            # Load prior watermark by BOUND identity only (never by caller-spoofed keys)
            prev_row = conn.execute(
                "SELECT * FROM source_watermarks WHERE source=? AND contract_key=? AND timeframe=? "
                "AND consumer_key=? AND version_set_hash=?",
                (bound["source"], bound["contract_key"], bound["timeframe"],
                 bound["consumer_key"], bound["version_set_hash"]),
            ).fetchone()
            prev = None
            if prev_row is not None:
                prev = {
                    "source": prev_row["source"], "contract_key": prev_row["contract_key"],
                    "timeframe": prev_row["timeframe"], "consumer_key": prev_row["consumer_key"],
                    "version_set_hash": prev_row["version_set_hash"],
                    "requested_end_ms": prev_row["requested_end_ms"],
                    "fetched_max_ms": prev_row["fetched_max_ms"],
                    "continuous_end_ms": prev_row["continuous_end_ms"],
                    "evaluated_end_ms": prev_row["evaluated_end_ms"],
                    "first_gap_ms": prev_row["first_gap_ms"],
                    "quality": json.loads(prev_row["quality_json"]),
                }

            effect_close = None
            if isinstance(effect, dict) and effect.get("close_ms") is not None:
                effect_close = effect.get("close_ms")
                if not isinstance(effect_close, int) or isinstance(effect_close, bool) or effect_close < 0:
                    raise LedgerReject("EFFECT_illegal_close_ms", close_ms=effect_close)

            # F1 expected next root: first_gap wins; else derive persisted continuous
            # prefix independently of optional job.next_close_ms (omitting cursor must NOT
            # disable prefix check). Job cursor is an extra contract that may clamp down.
            expected_next = None
            if prev is not None and prev.get("first_gap_ms") is not None:
                expected_next = prev["first_gap_ms"]
            else:
                prefix_next = None
                if prev is not None and prev.get("continuous_end_ms") is not None:
                    bar = timeframe_to_ms(bound.get("timeframe") or "")
                    if bar is not None:
                        prefix_next = int(prev["continuous_end_ms"]) + bar
                job_next = row["next_close_ms"]
                if job_next is not None and prefix_next is not None:
                    expected_next = job_next if job_next <= prefix_next else prefix_next
                elif prefix_next is not None:
                    expected_next = prefix_next
                elif job_next is not None:
                    expected_next = job_next

            if effect_close is not None and expected_next is not None and effect_close != expected_next:
                raise LedgerReject(
                    "EFFECT_skip_unprocessed_root",
                    expected_next_ms=expected_next,
                    got_close_ms=effect_close,
                    job_key=job_key,
                )

            new_state = self.JOB_GAP_PAUSED if gap_pause else (
                self.JOB_FAILED if error_code else self.JOB_EFFECT_ACKED
            )
            wm_merged = None
            if watermark is not None:
                # F2: caller identity must exactly match bound receipt+job contract
                for k in ("source", "contract_key", "timeframe", "consumer_key", "version_set_hash"):
                    if watermark.get(k) != bound[k]:
                        raise LedgerReject(
                            "WATERMARK_identity_not_bound",
                            field=k, expected=bound[k], got=watermark.get(k),
                        )
                patch = dict(watermark)
                patch.update(bound)
                # F1: without a verified effect.close, refuse continuous/evaluated moves
                if not gap_pause and effect_close is None:
                    raise LedgerReject(
                        "WATERMARK_requires_effect_close",
                        job_key=job_key,
                    )
                # F1: continuous/evaluated must not claim ahead of verified effect
                if not gap_pause and effect_close is not None:
                    for fld in ("continuous_end_ms", "evaluated_end_ms"):
                        claimed = patch.get(fld)
                        if claimed is not None and claimed > effect_close:
                            raise LedgerReject(
                                "WATERMARK_ahead_of_effect",
                                field=fld, effect_close_ms=effect_close, got=claimed,
                            )
                    # Derive continuous/evaluated from verified effect (not caller leap)
                    patch["continuous_end_ms"] = effect_close
                    patch["evaluated_end_ms"] = effect_close
                    if prev is not None and prev.get("first_gap_ms") is not None:
                        if effect_close == prev["first_gap_ms"]:
                            patch["first_gap_ms"] = None
                            q = dict(patch.get("quality") or prev.get("quality") or {})
                            q["gap"] = False
                            q.pop("first_gap_ms", None)
                            patch["quality"] = q
                        else:
                            raise LedgerReject(
                                "EFFECT_not_filling_first_gap",
                                first_gap_ms=prev["first_gap_ms"], got=effect_close,
                            )
                wm_merged = self._validate_watermark_patch(prev, patch, gap_pause=gap_pause)
                conn.execute(
                    "INSERT INTO source_watermarks("
                    "source,contract_key,timeframe,consumer_key,version_set_hash,"
                    "requested_end_ms,fetched_max_ms,continuous_end_ms,evaluated_end_ms,"
                    "first_gap_ms,quality_json,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?) "
                    "ON CONFLICT(source,contract_key,timeframe,consumer_key,version_set_hash) DO UPDATE SET "
                    "requested_end_ms=excluded.requested_end_ms,"
                    "fetched_max_ms=excluded.fetched_max_ms,"
                    "continuous_end_ms=excluded.continuous_end_ms,"
                    "evaluated_end_ms=excluded.evaluated_end_ms,"
                    "first_gap_ms=excluded.first_gap_ms,"
                    "quality_json=excluded.quality_json,"
                    "updated_at=excluded.updated_at",
                    (wm_merged["source"], wm_merged["contract_key"], wm_merged["timeframe"],
                     wm_merged["consumer_key"], wm_merged["version_set_hash"],
                     wm_merged["requested_end_ms"], wm_merged["fetched_max_ms"],
                     wm_merged["continuous_end_ms"], wm_merged["evaluated_end_ms"],
                     wm_merged["first_gap_ms"], _canonical(wm_merged["quality"]), now),
                )
                self._maybe_crash(crash_after, "after_watermark_upsert")

            eff_json = _canonical(effect) if effect is not None else row["effect_result_json"]
            if new_state == self.JOB_EFFECT_ACKED:
                acked_at = now
            elif new_state == self.JOB_GAP_PAUSED:
                acked_at = None
            else:
                acked_at = None
            conn.execute(
                "UPDATE source_jobs SET state=?, attempt_count=attempt_count+1, error_code=?, "
                "effect_result_json=?, effect_acked_at=? WHERE job_key=?",
                (new_state, error_code, eff_json, acked_at, job_key),
            )
            self._maybe_crash(crash_after, "after_job_update")
            self._maybe_crash(crash_after, "before_commit")
            conn.execute("COMMIT")
            return {
                "ok": True, "idempotent": False, "job_key": job_key, "state": new_state,
                "effect_acked": new_state == self.JOB_EFFECT_ACKED,
                "effect_acked_at": acked_at, "watermark": wm_merged,
                "receipt_acked_separate": True,
            }
        except LedgerReject as rej:
            self._rollback(conn)
            return {"ok": False, "errors": rej.as_errors()}
        except Exception as exc:
            self._rollback(conn)
            if str(exc).startswith("C2B_CRASH_INJECT:"):
                return {"ok": False, "errors": [dict(code="C2B_CRASH_INJECT", at=str(exc).split(":", 1)[1])],
                        "crashed": True}
            return {"ok": False, "errors": [dict(code="JOB_effect_write_failed",
                                                error=f"{type(exc).__name__}: {exc}")]}
        finally:
            conn.close()

    def mark_job_running(self, job_key: str) -> dict:
        now = _utc()
        conn = self._connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("SELECT * FROM source_jobs WHERE job_key=?", (job_key,)).fetchone()
            if row is None:
                raise LedgerReject("JOB_not_found", job_key=job_key)
            if row["state"] == self.JOB_RUNNING:
                conn.execute("COMMIT")
                return {"ok": True, "idempotent": True, "state": self.JOB_RUNNING}
            if row["state"] not in (self.JOB_PENDING, self.JOB_GAP_PAUSED):
                raise LedgerReject("JOB_bad_state", state=row["state"])
            conn.execute(
                "UPDATE source_jobs SET state=?, attempt_count=attempt_count+1 WHERE job_key=?",
                (self.JOB_RUNNING, job_key),
            )
            conn.execute("COMMIT")
            return {"ok": True, "idempotent": False, "state": self.JOB_RUNNING}
        except LedgerReject as rej:
            self._rollback(conn)
            return {"ok": False, "errors": rej.as_errors()}
        except Exception as exc:
            self._rollback(conn)
            return {"ok": False, "errors": [dict(code="JOB_running_failed",
                                                error=f"{type(exc).__name__}: {exc}")]}
        finally:
            conn.close()
