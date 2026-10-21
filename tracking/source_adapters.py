"""Source adapter protocol + fixture + non-formal JSON helpers.

C2-b formal watermarks / receipt ACK / effect ACK live in TrackingLedger
(source_receipts, source_jobs, source_watermarks) via source_dispatch.py.
PerSourceWatermarkStore and RecoveryAckJournal here are NOT the correctness
authority and must not be used as the runtime main chain.
"""
from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol


def _utc() -> str:
    t = time.time()
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(t)) + f".{int(t * 1000000) % 1000000:06d}+00:00"


def _sha(obj: Any) -> str:
    blob = json.dumps(obj, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


@dataclass
class SourceWindow:
    source: str
    ref: str
    from_ms: int
    to_ms: int
    version: str
    available_at: str
    payload: Any
    payload_sha256: str


@dataclass
class SourceAdapterResult:
    ok: bool
    windows: list[SourceWindow] = field(default_factory=list)
    errors: list[dict] = field(default_factory=list)
    next_cursor: str | None = None


class SourceAdapter(Protocol):
    name: str

    def pull(self, *, ref: str, from_ms: int, to_ms: int, cursor: str | None = None) -> SourceAdapterResult:
        ...

    def health(self) -> dict:
        ...


class FixtureSourceAdapter:
    """Deterministic adapter over in-memory / JSON fixture rows."""

    def __init__(self, name: str, rows: list[dict]):
        self.name = name
        # each row: ref, open_ms/effective_at_ms, version, available_at, payload
        self.rows = list(rows)

    def pull(self, *, ref: str, from_ms: int, to_ms: int, cursor: str | None = None) -> SourceAdapterResult:
        start_i = int(cursor or 0)
        matched = []
        for i, r in enumerate(self.rows):
            if i < start_i:
                continue
            if r.get("ref") != ref:
                continue
            eff = int(r.get("effective_at_ms") or r.get("open_ms") or 0)
            if eff < from_ms or eff > to_ms:
                continue
            payload = r.get("payload")
            matched.append(SourceWindow(
                source=self.name, ref=ref, from_ms=from_ms, to_ms=to_ms,
                version=str(r.get("version") or "v1"),
                available_at=str(r.get("available_at") or _utc()),
                payload=payload,
                payload_sha256=r.get("payload_sha256") or _sha(payload),
            ))
        return SourceAdapterResult(ok=True, windows=matched, next_cursor=str(len(self.rows)))

    def health(self) -> dict:
        return {"ok": True, "adapter": self.name, "rows": len(self.rows), "production_wired": False}


class PerSourceWatermarkStore:
    """File-backed per-source watermarks (isolated; not production DB)."""

    def __init__(self, path: Path | str):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if not self.path.exists():
            self._write({"schema": "s2-r3-per-source-watermarks-v1", "watermarks": {}})

    def _read(self) -> dict:
        return json.loads(self.path.read_text(encoding="utf-8"))

    def _write(self, data: dict) -> None:
        self.path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    def get(self, source: str, ref: str) -> int | None:
        key = f"{source}|{ref}"
        wm = self._read()["watermarks"].get(key)
        return int(wm["close_ms"]) if wm else None

    def advance(self, source: str, ref: str, close_ms: int, *, version: str | None = None) -> dict:
        data = self._read()
        key = f"{source}|{ref}"
        prev = data["watermarks"].get(key)
        if prev and int(prev["close_ms"]) > close_ms:
            return {"ok": False, "errors": [dict(code="WATERMARK_regression", have=prev["close_ms"], got=close_ms)]}
        data["watermarks"][key] = {
            "close_ms": close_ms, "version": version, "updated_at": _utc(),
        }
        self._write(data)
        return {"ok": True, "key": key, "close_ms": close_ms, "previous": prev}


class RecoveryAckJournal:
    """Idempotent recovery ACK journal (adapter restart / cross-process)."""

    def __init__(self, path: Path | str):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if not self.path.exists():
            self._write({"schema": "s2-r3-recovery-ack-v1", "acks": {}})

    def _read(self) -> dict:
        return json.loads(self.path.read_text(encoding="utf-8"))

    def _write(self, data: dict) -> None:
        self.path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    def ack(self, *, recovery_key: str, payload: dict) -> dict:
        data = self._read()
        digest = _sha(payload)
        prev = data["acks"].get(recovery_key)
        if prev is not None:
            if prev.get("payload_sha256") == digest:
                return {"ok": True, "idempotent": True, "recovery_key": recovery_key, "status": "acked"}
            return {"ok": False, "errors": [dict(code="RECOVERY_ACK_conflict", recovery_key=recovery_key)]}
        data["acks"][recovery_key] = {
            "payload_sha256": digest, "acked_at": _utc(), "payload": payload, "status": "acked",
        }
        self._write(data)
        return {"ok": True, "idempotent": False, "recovery_key": recovery_key, "status": "acked"}

    def get(self, recovery_key: str) -> dict | None:
        return self._read()["acks"].get(recovery_key)
