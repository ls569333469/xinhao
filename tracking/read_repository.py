# -*- coding: utf-8 -*-
"""C3-a TrackingReadRepository: read-only, no mkdir/migrate/ACK/checkpoint.

Separate from TrackingLedger writer. Missing DB → not_initialized (never auto-create).
Incompatible / pre-projection schema → explicit reject (never silently fall back).
"""
from __future__ import annotations

import json
import threading
from contextlib import contextmanager
import sqlite3
from pathlib import Path
from typing import Any

REQUIRED_SCHEMA_PREFIX = "s2-isolated-tracking-ledger-v1.5"
REQUIRED_TABLES = (
    "tracking_commits",
    "tracking_summary_versions",
    "tracking_effective_members",
    "event_revisions",
    "sessions",
    "meta",
)


class ReadRepositoryError(ValueError):
    def __init__(self, code: str, **detail: Any):
        super().__init__(code)
        self.code = code
        self.detail = detail

    def as_dict(self) -> dict:
        return {"code": self.code, **self.detail}


class TrackingReadRepository:
    """Short-lived readonly connections. Never creates directories or tables."""

    FORBIDDEN_NAME_PARTS = ("signal-tracking.sqlite3", "workbench.sqlite3", "b-events-v3.sqlite3")

    def __init__(self, path: Path | str, *, _runtime_root=None):
        self.path = Path(path)
        self._local = threading.local()
        name = self.path.name.lower()
        allowed_runtime=(_runtime_root is not None and self.path.resolve()==Path(_runtime_root).resolve()/'runtime'/'signal-tracking.sqlite3')
        for bad in self.FORBIDDEN_NAME_PARTS:
            if bad in name and not allowed_runtime:
                raise ReadRepositoryError("READ_REPO_forbidden_production_name", path=str(self.path))
        if not self.path.is_file():
            raise ReadRepositoryError("not_initialized", path=str(self.path))
        self._assert_compatible_schema()

    @classmethod
    def for_runtime(cls,root):
        root=Path(root).resolve()
        return cls(root/'runtime'/'signal-tracking.sqlite3',_runtime_root=root)

    def _connect(self) -> sqlite3.Connection:
        uri = self.path.resolve().as_uri() + "?mode=ro"
        conn = sqlite3.connect(uri, uri=True, timeout=10, isolation_level=None)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA query_only=ON")
        return conn

    def _assert_compatible_schema(self) -> None:
        conn = self._connect()
        try:
            tables = {
                r["name"]
                for r in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                ).fetchall()
            }
            missing = [t for t in REQUIRED_TABLES if t not in tables]
            if missing:
                raise ReadRepositoryError(
                    "READ_SCHEMA_incompatible",
                    missing_tables=missing,
                    detail="old ledger lacks C3 projection tables",
                )
            row = conn.execute("SELECT value FROM meta WHERE key=?", ("schema",)).fetchone()
            schema = row["value"] if row else None
            if not schema or not str(schema).startswith(REQUIRED_SCHEMA_PREFIX):
                raise ReadRepositoryError(
                    "READ_SCHEMA_incompatible",
                    schema=schema,
                    required_prefix=REQUIRED_SCHEMA_PREFIX,
                )
        except sqlite3.OperationalError as exc:
            raise ReadRepositoryError("READ_SCHEMA_incompatible", error=str(exc)) from exc
        finally:
            conn.close()

    @contextmanager
    def snapshot(self):
        """One short read transaction per request, reused by nested DTO/export calls."""
        if getattr(self._local, "connection", None) is not None:
            yield
            return
        conn = self._connect()
        self._local.connection = conn
        self._local.recovery_quality_cache = {}
        try:
            conn.execute("BEGIN")
            yield
        finally:
            try:
                conn.execute("ROLLBACK")
            finally:
                self._local.connection = None
                self._local.recovery_quality_cache = None
                conn.close()

    def read(self, sql: str, args: tuple = ()) -> list[sqlite3.Row]:
        active = getattr(self._local, "connection", None)
        if active is not None:
            return active.execute(sql, args).fetchall()
        conn = self._connect()
        try:
            return conn.execute(sql, args).fetchall()
        finally:
            conn.close()

    def read_one(self, sql: str, args: tuple = ()) -> sqlite3.Row | None:
        rows = self.read(sql, args)
        return rows[0] if rows else None

    def meta(self, key: str) -> str | None:
        row = self.read_one("SELECT value FROM meta WHERE key=?", (key,))
        return row["value"] if row else None

    def tracking_commit_seq(self) -> int:
        row = self.read_one("SELECT COALESCE(MAX(commit_seq), 0) AS s FROM tracking_commits")
        if row is None:
            raise ReadRepositoryError("READ_SCHEMA_incompatible", detail="tracking_commits unreadable")
        return int(row["s"])

    def schema_version(self) -> str | None:
        return self.meta("schema")

    def recovery_commit_seq(self) -> int:
        return int(self.meta('recovery_commit_seq') or 0)

    def close(self) -> None:
        """No-op: connections are short-lived; never checkpoint."""
        return None
