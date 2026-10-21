"""C2-a: read-only adapter over published immutable A scan revisions.

Enumerates revision-NNNN.json under an isolated archive root, verifies content
hash / market+symbol / archive path bounds / closed-bar times one-by-one, and
emits SourceEnvelope pages. Does NOT call scanner, evaluate_row, or external fetch.
JSON watermarks / ACK are NOT used as formal correctness here (see C2-b).
"""
from __future__ import annotations

import hashlib
import json
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterator

from .mappings import (
    H4_MS,
    MAPPING_PROTOCOL_VERSION,
    MappingError,
    map_inclusive_close_to_exclusive,
    map_market,
    map_row_identity,
)

SOURCE_NAME = "a_scan_archive"
ENVELOPE_PROTOCOL_VERSION = "s2-c2a-r2-envelope-v1"
SEAL_SCHEMA = "s2-c2a-r2-sealed-evidence-v1"
SEAL_FILENAME = "sealed-evidence.json"
EVENT_STATES = ("event", "no_event")
SCAN_ID_RE = re.compile(r"^scan-[a-f0-9]{32}$")
REV_FILE_RE = re.compile(r"^revision-(\d{4})\.json$")


def _utc() -> str:
    t = time.time()
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(t)) + f".{int(t * 1000000) % 1000000:06d}+00:00"


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


@dataclass(frozen=True)
class SourceEnvelope:
    source: str
    source_ref: str                 # scan_id + revision
    source_version: str             # immutable version id (not latest.json path)
    payload_sha256: str
    market: str                     # canonical
    symbol: str
    timeframe: str
    range_start_open_ms: int
    range_end_close_ms: int         # exclusive close boundary
    source_available_at: str | None
    ingested_at: str
    discovery: str
    evidence_ref: str
    # extras for C2-a verification / mapping (not frozen schema fields)
    message_key: str
    market_legacy: str
    legacy_inclusive_close_ms: int | None = None
    row_payload_sha256: str | None = None
    identity: dict | None = None
    quality_gaps: tuple[str, ...] = ()
    # r2 (F1/F3): explicit event/no_event state and how many bars were verified one-by-one
    event_state: str = "unknown"
    verified_bar_count: int | None = None

    def as_dict(self) -> dict:
        d = {
            "source": self.source,
            "source_ref": self.source_ref,
            "source_version": self.source_version,
            "payload_sha256": self.payload_sha256,
            "market": self.market,
            "symbol": self.symbol,
            "timeframe": self.timeframe,
            "range_start_open_ms": self.range_start_open_ms,
            "range_end_close_ms": self.range_end_close_ms,
            "source_available_at": self.source_available_at,
            "ingested_at": self.ingested_at,
            "discovery": self.discovery,
            "evidence_ref": self.evidence_ref,
            "message_key": self.message_key,
            "market_legacy": self.market_legacy,
            "legacy_inclusive_close_ms": self.legacy_inclusive_close_ms,
            "row_payload_sha256": self.row_payload_sha256,
            "identity": self.identity,
            "quality_gaps": list(self.quality_gaps),
            "event_state": self.event_state,
            "verified_bar_count": self.verified_bar_count,
            "envelope_protocol_version": ENVELOPE_PROTOCOL_VERSION,
            "mapping_protocol_version": MAPPING_PROTOCOL_VERSION,
        }
        return d


@dataclass
class VerifyIssue:
    code: str
    severity: str  # reject | gap
    detail: dict = field(default_factory=dict)

    def as_dict(self) -> dict:
        return {"code": self.code, "severity": self.severity, **self.detail}


@dataclass
class RevisionPage:
    ok: bool
    scan_id: str
    revision: int
    revision_sha256: str
    envelopes: list[SourceEnvelope] = field(default_factory=list)
    issues: list[VerifyIssue] = field(default_factory=list)
    next_cursor: str | None = None
    watermark_may_advance: bool = False  # C2-a: never advance across reject/gap

    def as_dict(self) -> dict:
        return {
            "ok": self.ok,
            "scan_id": self.scan_id,
            "revision": self.revision,
            "revision_sha256": self.revision_sha256,
            "envelopes": [e.as_dict() for e in self.envelopes],
            "issues": [i.as_dict() for i in self.issues],
            "next_cursor": self.next_cursor,
            "watermark_may_advance": self.watermark_may_advance,
        }


@dataclass
class ReceiptState:
    """In-memory idempotency for C2-a demos only — NOT formal C2-b SQLite receipts."""
    by_key: dict[str, str] = field(default_factory=dict)  # message_key -> payload_sha256

    def accept(self, message_key: str, payload_sha256: str) -> dict:
        prev = self.by_key.get(message_key)
        if prev is None:
            self.by_key[message_key] = payload_sha256
            return {"ok": True, "idempotent": False, "status": "accepted"}
        if prev == payload_sha256:
            return {"ok": True, "idempotent": True, "status": "accepted"}
        return {
            "ok": False,
            "status": "conflict",
            "errors": [dict(code="SOURCE_KEY_hash_conflict", message_key=message_key,
                            have=prev, got=payload_sha256)],
        }


def stable_message_key(*, source: str, source_ref: str, source_version: str,
                       payload_sha256: str, protocol: str = ENVELOPE_PROTOCOL_VERSION) -> str:
    """Stable business key — never wall-clock. Same-key same-hash idempotent."""
    raw = f"{source}|{source_ref}|{source_version}|{payload_sha256}|{protocol}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


class AArchiveSource:
    """Read-only published revision adapter rooted at an isolated archive mirror."""

    def __init__(self, archive_root: Path | str, *, historical_import: bool = True,
                 expected_revision_hashes: dict[str, str] | None = None,
                 sealed_manifest: "dict | Path | str | None" = None,
                 sealed_manifest_sha256: str | None = None):
        self.root = Path(archive_root).resolve()
        self.history = (self.root / "scan-history").resolve()
        self.historical_import = historical_import
        # optional pin: "scan_id/revision-0001.json" -> sha256
        self.expected_revision_hashes = expected_revision_hashes or {}
        self.receipts = ReceiptState()
        # F2/F4: trusted sealed evidence (local audit baseline fixed at first capture;
        # there is no supplier signature, so this does NOT prove the data was never
        # altered before capture). Supplied out-of-band or found at <root>/sealed-evidence.json.
        self.seal: dict | None = None
        self.seal_load_issues: list[VerifyIssue] = []
        self._load_seal(sealed_manifest, sealed_manifest_sha256)

    def _load_seal(self, manifest, pin_sha256):
        data = None
        if isinstance(manifest, dict):
            data = json.loads(json.dumps(manifest))
        else:
            path = Path(manifest) if manifest is not None else (self.root / SEAL_FILENAME)
            if manifest is None and not path.is_file():
                return  # missing -> every read fails closed with SEAL_manifest_missing
            try:
                raw = path.read_bytes()
            except OSError as e:
                self.seal_load_issues.append(VerifyIssue("SEAL_manifest_unreadable", "reject", {"error": str(e)}))
                return
            if pin_sha256 and sha256_bytes(raw) != pin_sha256:
                self.seal_load_issues.append(VerifyIssue("SEAL_manifest_hash_mismatch", "reject", {
                    "expected": pin_sha256, "got": sha256_bytes(raw)}))
                return
            try:
                data = json.loads(raw)
            except json.JSONDecodeError as e:
                self.seal_load_issues.append(VerifyIssue("SEAL_manifest_invalid", "reject", {"error": str(e)}))
                return
        if not isinstance(data, dict) or data.get("schema") != SEAL_SCHEMA or not isinstance(data.get("scans"), dict):
            self.seal_load_issues.append(VerifyIssue("SEAL_manifest_invalid", "reject", {"schema": (data or {}).get("schema") if isinstance(data, dict) else None}))
            return
        self.seal = data

    def _seal_entry(self, scan_id: str, revision: int) -> dict | None:
        if self.seal is None:
            return None
        pub = ((self.seal.get("scans") or {}).get(scan_id) or {}).get("publications") or {}
        e = pub.get(str(int(revision)))
        return e if isinstance(e, dict) else None

    def _read_status(self, scan_dir: Path) -> tuple[dict | None, bytes | None]:
        sp = scan_dir / "status.json"
        if not sp.is_file():
            return None, None
        try:
            raw = sp.read_bytes()
            st = json.loads(raw)
        except (OSError, json.JSONDecodeError):
            return None, None
        return (st if isinstance(st, dict) else None), raw

    @staticmethod
    def _validate_entry_candles(scan_id: str, revision: int, entry: dict) -> list[VerifyIssue]:
        """r3 SEAL: a used publication must carry a complete candles mapping. Every candle ref
        needs full sha256, bytes and market/symbol/4h contract binding. Missing -> reject
        (fail closed); an unverifiable candle can never be read as 'ok'."""
        ctx = {"scan_id": scan_id, "revision": int(revision)}
        candles = entry.get("candles")
        if not isinstance(candles, dict) or not candles:
            return [VerifyIssue("SEAL_manifest_invalid", "reject", {**ctx, "reason": "candles_missing_or_not_mapping"})]
        bad = []
        for rel, sc in candles.items():
            ok = (isinstance(rel, str) and rel and isinstance(sc, dict)
                  and isinstance(sc.get("sha256"), str) and re.fullmatch(r"[a-f0-9]{64}", sc["sha256"])
                  and isinstance(sc.get("bytes"), int) and not isinstance(sc.get("bytes"), bool) and sc["bytes"] > 0
                  and isinstance(sc.get("market"), str) and sc["market"]
                  and isinstance(sc.get("symbol"), str) and sc["symbol"]
                  and sc.get("interval") == "4h")
            if not ok:
                bad.append(rel if isinstance(rel, str) else repr(rel))
        if bad:
            return [VerifyIssue("SEAL_manifest_invalid", "reject", {**ctx, "reason": "candle_ref_incomplete", "refs": bad[:20]})]
        return []

    def _publish_gate(self, scan_id: str, scan_dir: Path, revision: int) -> list[VerifyIssue]:
        """F4: a revision is readable only with a trusted publish proof.
        Needs (a) status.json present/accepted with status.revision >= revision,
        (b) a sealed publication entry for exactly this revision,
        (c) when status.revision equals the sealed commit revision, the status bytes hash must match.
        revision file content (own 'accepted' flag) is never enough."""
        out: list[VerifyIssue] = list(self.seal_load_issues)
        if self.seal is None and not self.seal_load_issues:
            out.append(VerifyIssue("SEAL_manifest_missing", "reject", {"scan_id": scan_id}))
        st, st_raw = self._read_status(scan_dir)
        if st is None:
            out.append(VerifyIssue("PUBLISH_status_missing", "reject", {"scan_id": scan_id, "revision": revision}))
            return out
        if st.get("scan_id") not in (None, scan_id):
            out.append(VerifyIssue("PUBLISH_status_scan_mismatch", "reject", {"file": st.get("scan_id")}))
        if not (st.get("quality") or {}).get("accepted") is True:
            out.append(VerifyIssue("PUBLISH_status_not_accepted", "reject", {}))
        try:
            st_rev = int(st.get("revision") or 0)
        except (TypeError, ValueError):
            st_rev = 0
        if st_rev < int(revision):
            out.append(VerifyIssue("PUBLISH_revision_not_published", "reject", {
                "status_revision": st_rev, "requested": int(revision)}))
        entry = self._seal_entry(scan_id, revision)
        if self.seal is not None:
            if entry is None:
                out.append(VerifyIssue("SEAL_revision_not_sealed", "reject", {
                    "scan_id": scan_id, "revision": int(revision)}))
            else:
                out.extend(self._validate_entry_candles(scan_id, revision, entry))
                commit_rev = entry.get("publish_status_revision")
                if not isinstance(commit_rev, int) or commit_rev > st_rev or commit_rev < int(revision):
                    out.append(VerifyIssue("PUBLISH_commit_proof_invalid", "reject", {
                        "sealed_commit_revision": commit_rev, "status_revision": st_rev}))
                elif st_rev == commit_rev and sha256_bytes(st_raw) != entry.get("status_sha256"):
                    out.append(VerifyIssue("PUBLISH_status_proof_mismatch", "reject", {
                        "expected": entry.get("status_sha256"), "got": sha256_bytes(st_raw)}))
        return out

    def health(self) -> dict:
        return {
            "ok": self.history.is_dir(),
            "adapter": SOURCE_NAME,
            "archive_root": str(self.root),
            "production_wired": False,
            "formal_watermark": False,
            "historical_import": self.historical_import,
        }

    def list_published_revisions(self) -> list[dict]:
        """Only revisions that pass the same trusted publish gate as read_published_page."""
        out = []
        if not self.history.is_dir():
            return out
        for scan_dir in sorted(self.history.iterdir()):
            if not scan_dir.is_dir() or not SCAN_ID_RE.match(scan_dir.name):
                continue
            for rev_file in sorted(scan_dir.glob("revision-*.json")):
                m = REV_FILE_RE.match(rev_file.name)
                if not m:
                    continue
                n = int(m.group(1))
                if n < 1:
                    continue
                gate = self._publish_gate(scan_dir.name, scan_dir, n)
                if any(i.severity == "reject" for i in gate):
                    continue
                entry = self._seal_entry(scan_dir.name, n) or {}
                digest = sha256_file(rev_file)
                if digest != entry.get("revision_sha256"):
                    continue
                st, _ = self._read_status(scan_dir)
                out.append({
                    "scan_id": scan_dir.name,
                    "revision": n,
                    "path": str(rev_file.relative_to(self.root)).replace("\\", "/"),
                    "bytes": rev_file.stat().st_size,
                    "sha256": digest,
                    "status_revision": int((st or {}).get("revision") or 0),
                    "quality_status": ((st or {}).get("quality") or {}).get("status"),
                    "sealed": True,
                })
        return out

    def read_published_page(self, *, scan_id: str, revision: int,
                            cursor: int = 0, limit: int = 50,
                            ingested_at: str | None = None,
                            expected_sha256: str | None = None) -> RevisionPage:
        issues: list[VerifyIssue] = []
        if not SCAN_ID_RE.match(scan_id):
            return RevisionPage(False, scan_id, revision, "", issues=[
                VerifyIssue("SCAN_ID_invalid", "reject", {"scan_id": scan_id})])
        scan_dir = (self.history / scan_id).resolve()
        try:
            scan_dir.relative_to(self.history)
        except ValueError:
            return RevisionPage(False, scan_id, revision, "", issues=[
                VerifyIssue("PATH_escape", "reject", {"scan_id": scan_id})])
        gate_issues: list[VerifyIssue] = self._publish_gate(scan_id, scan_dir, revision)
        rev_path = scan_dir / f"revision-{revision:04d}.json"
        if not rev_path.is_file():
            return RevisionPage(False, scan_id, revision, "", issues=[
                VerifyIssue("REVISION_missing", "reject", {"path": str(rev_path)})])

        raw = rev_path.read_bytes()
        digest = sha256_bytes(raw)
        _entry = self._seal_entry(scan_id, revision)
        if _entry is not None and _entry.get("revision_sha256") != digest:
            gate_issues.append(VerifyIssue("SEAL_revision_hash_mismatch", "reject", {
                "expected": _entry.get("revision_sha256"), "got": digest}))
        pin_key = f"{scan_id}/revision-{revision:04d}.json"
        expect = expected_sha256 or self.expected_revision_hashes.get(pin_key)
        if expect and expect != digest:
            issues.append(VerifyIssue("CHECKSUM_mismatch", "reject", {
                "path": pin_key, "expected": expect, "got": digest}))
            return RevisionPage(False, scan_id, revision, digest, issues=issues,
                                watermark_may_advance=False)

        try:
            payload = json.loads(raw)
        except json.JSONDecodeError as e:
            issues.append(VerifyIssue("PARSE_failed", "reject", {"error": str(e)}))
            return RevisionPage(False, scan_id, revision, digest, issues=issues,
                                watermark_may_advance=False)

        if payload.get("scan_id") != scan_id:
            issues.append(VerifyIssue("SCAN_ID_mismatch", "reject", {
                "file": payload.get("scan_id"), "expected": scan_id}))
            return RevisionPage(False, scan_id, revision, digest, issues=issues,
                                watermark_may_advance=False)
        if int(payload.get("revision") or 0) != int(revision):
            issues.append(VerifyIssue("REVISION_mismatch", "reject", {
                "file": payload.get("revision"), "expected": revision}))
            return RevisionPage(False, scan_id, revision, digest, issues=issues,
                                watermark_may_advance=False)

        quality = payload.get("quality") or {}
        if quality.get("accepted") is not True:
            issues.append(VerifyIssue("QUALITY_not_accepted", "reject", {
                "quality": quality.get("status")}))
            return RevisionPage(False, scan_id, revision, digest, issues=issues,
                                watermark_may_advance=False)

        rows = payload.get("rows") or []
        if not isinstance(rows, list):
            issues.append(VerifyIssue("ROWS_invalid", "reject", {}))
            return RevisionPage(False, scan_id, revision, digest, issues=issues,
                                watermark_may_advance=False)

        ingested = ingested_at or _utc()
        # available_at unknown stays unknown — do not forge with _utc()
        source_available_at = payload.get("available_at")
        if source_available_at is not None and not isinstance(source_available_at, str):
            source_available_at = None

        start = max(0, int(cursor))
        end = start + max(1, int(limit))
        slice_rows = rows[start:end]
        envelopes: list[SourceEnvelope] = []
        page_reject = False

        issues.extend(gate_issues)
        page_level_reject = any(i.severity == "reject" for i in gate_issues)
        sealed_candles = (_entry or {}).get("candles") if isinstance(_entry, dict) else None
        for idx, row in enumerate(slice_rows):
            abs_i = start + idx
            if not isinstance(row, dict):
                issues.append(VerifyIssue("ROW_invalid", "reject", {"row_index": abs_i}))
                page_reject = True
                continue
            env, row_issues = self._verify_row(
                scan_id=scan_id, revision=revision, revision_sha256=digest,
                row=row, row_index=abs_i, ingested_at=ingested,
                source_available_at=source_available_at, sealed_candles=sealed_candles,
            )
            for iss in row_issues:
                issues.append(iss)
                if iss.severity == "reject":
                    page_reject = True
            if env is not None:
                envelopes.append(env)

        if page_level_reject:
            envelopes = []  # unpublished / unsealed revision never yields consumable envelopes
        next_cursor = str(end) if end < len(rows) else None
        ok = (not page_reject) and not any(i.severity == "reject" for i in issues)
        # Explicit: quality gaps / rejects must NOT advance continuous watermarks
        return RevisionPage(
            ok=ok, scan_id=scan_id, revision=revision, revision_sha256=digest,
            envelopes=envelopes, issues=issues, next_cursor=next_cursor,
            watermark_may_advance=False if (not ok or any(i.severity == "gap" for i in issues)) else ok,
        )

    def _verify_row(self, *, scan_id: str, revision: int, revision_sha256: str,
                    row: dict, row_index: int, ingested_at: str,
                    source_available_at: str | None,
                    sealed_candles: dict | None = None) -> tuple[SourceEnvelope | None, list[VerifyIssue]]:
        issues: list[VerifyIssue] = []
        gaps: list[str] = []
        legacy_market = row.get("market")
        symbol = row.get("symbol")
        try:
            market = map_market(legacy_market)
        except MappingError as e:
            issues.append(VerifyIssue(e.code, "reject", {**e.detail, "row_index": row_index}))
            return None, issues

        # Time: only convert K fields
        legacy_close = row.get("candleAsOf")
        bar = None
        if legacy_close is not None:
            try:
                bar = map_inclusive_close_to_exclusive(int(legacy_close))
            except (MappingError, TypeError, ValueError) as e:
                detail = e.detail if isinstance(e, MappingError) else {"legacy_close_ms": legacy_close}
                code = e.code if isinstance(e, MappingError) else "TIME_invalid_legacy_close"
                issues.append(VerifyIssue(code, "reject", {**detail, "row_index": row_index, "symbol": symbol}))
                return None, issues

        # Archive path + candle identity
        rel = (row.get("candleArchives") or {}).get("4h")
        evidence_ref = None
        range_start = bar.open_ms if bar else 0
        range_end = bar.exclusive_close_ms if bar else 0
        candle_sha = None
        verified_bars = None
        if not isinstance(rel, str):
            gaps.append("missing_candle_archive_ref")
            issues.append(VerifyIssue("ARCHIVE_ref_missing", "gap", {
                "row_index": row_index, "symbol": symbol}))
        else:
            evidence_ref = rel.replace("\\", "/")
            ok_path, path_issue, candle_meta = self._verify_candle_file(
                rel=evidence_ref, legacy_market=legacy_market, symbol=symbol,
                legacy_close=legacy_close, sealed_candles=sealed_candles,
            )
            if not ok_path:
                path_issue.detail.setdefault("row_index", row_index)
                issues.append(path_issue)
                if path_issue.severity == "reject":
                    return None, issues
                gaps.append(path_issue.code)
            candle_sha = candle_meta.get("sha256")
            verified_bars = candle_meta.get("verified_bar_count")
            # F3: consumable range = the verified contiguous window only (tail window on gap)
            if candle_meta.get("range_start_open_ms") is not None:
                range_start = candle_meta["range_start_open_ms"]
            if candle_meta.get("range_end_close_ms") is not None:
                range_end = candle_meta["range_end_close_ms"]

        identity = None
        event_state = "no_event"
        try:
            ident = map_row_identity(row, historical_import=self.historical_import)
            if ident is not None:
                identity = ident.as_dict()
                event_state = "event"
                # Cross-check market canonicalization
                if ident.market_canonical != market:
                    issues.append(VerifyIssue("MARKET_identity_mismatch", "reject", {
                        "mapped": market, "identity": ident.market_canonical}))
                    return None, issues
        except MappingError as e:
            issues.append(VerifyIssue(e.code, "reject", {**e.detail, "row_index": row_index}))
            return None, issues

        discovery = "import" if self.historical_import else "historical_replay"
        if discovery == "live":
            issues.append(VerifyIssue("DISCOVERY_live_forbidden_on_archive", "reject", {}))
            return None, issues

        row_blob = json.dumps(row, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode("utf-8")
        row_sha = sha256_bytes(row_blob)
        source_ref = f"{scan_id}/r{revision:04d}"
        # version binds revision AND referenced candle bytes (F2): same version never means changed candles
        source_version = f"{scan_id}:revision:{revision}:{revision_sha256[:16]}"
        if candle_sha:
            source_version += f":candles:{candle_sha[:16]}"
        # payload hash binds revision content + row + candle
        payload_material = {
            "revision_sha256": revision_sha256,
            "row_sha256": row_sha,
            "candle_sha256": candle_sha,
            "window_start_open_ms": int(range_start),
            "window_end_close_ms": int(range_end),
            "verified_bar_count": verified_bars,
            "event_state": event_state,
            "market": market,
            "symbol": symbol,
            "protocol": ENVELOPE_PROTOCOL_VERSION,
        }
        payload_sha = sha256_bytes(json.dumps(payload_material, sort_keys=True, separators=(",", ":")).encode())
        msg_key = stable_message_key(
            source=SOURCE_NAME, source_ref=source_ref, source_version=source_version,
            payload_sha256=payload_sha,
        )

        # available_at: unknown stays unknown
        env = SourceEnvelope(
            source=SOURCE_NAME,
            source_ref=source_ref,
            source_version=source_version,
            payload_sha256=payload_sha,
            market=market,
            symbol=str(symbol),
            timeframe="4h",
            range_start_open_ms=int(range_start),
            range_end_close_ms=int(range_end),
            source_available_at=source_available_at,  # may be None
            ingested_at=ingested_at,
            discovery=discovery,
            evidence_ref=evidence_ref or "",
            message_key=msg_key,
            market_legacy=str(legacy_market),
            legacy_inclusive_close_ms=int(legacy_close) if isinstance(legacy_close, int) else None,
            row_payload_sha256=row_sha,
            identity=identity,
            quality_gaps=tuple(gaps),
            event_state=event_state,
            verified_bar_count=verified_bars,
        )
        return env, issues

    def _verify_candle_file(self, *, rel: str, legacy_market: str, symbol: str,
                            legacy_close: Any,
                            sealed_candles: dict | None = None) -> tuple[bool, VerifyIssue | None, dict]:
        meta: dict = {}
        # path confinement
        try:
            path = (self.root / rel).resolve()
            path.relative_to(self.history)
        except (ValueError, OSError):
            return False, VerifyIssue("PATH_out_of_archive", "reject", {"rel": rel}), meta
        if path.suffix != ".json" or not path.is_file():
            return False, VerifyIssue("ARCHIVE_file_missing", "reject", {"rel": rel}), meta
        try:
            raw = path.read_bytes()
            data = json.loads(raw)
        except (OSError, json.JSONDecodeError) as e:
            return False, VerifyIssue("ARCHIVE_read_failed", "reject", {"rel": rel, "error": str(e)}), meta

        meta["sha256"] = sha256_bytes(raw)
        if (data.get("market"), data.get("symbol"), data.get("interval")) != (legacy_market, symbol, "4h"):
            return False, VerifyIssue("CONTRACT_mismatch", "reject", {
                "rel": rel,
                "file": (data.get("market"), data.get("symbol"), data.get("interval")),
                "row": (legacy_market, symbol, "4h"),
            }), meta

        # F2: candle bytes must equal the sealed evidence of this publication
        if not isinstance(sealed_candles, dict):
            return False, VerifyIssue("SEAL_candle_not_sealed", "reject", {"rel": rel, "reason": "no_sealed_candles"}), meta
        if True:
            sc = sealed_candles.get(rel)
            if not isinstance(sc, dict):
                return False, VerifyIssue("SEAL_candle_not_sealed", "reject", {"rel": rel}), meta
            if sc.get("sha256") != meta["sha256"] or sc.get("bytes") != len(raw):
                return False, VerifyIssue("SEAL_candle_hash_mismatch", "reject", {
                    "rel": rel, "expected": sc.get("sha256"), "got": meta["sha256"]}), meta
            if (sc.get("market"), sc.get("symbol"), sc.get("interval")) != (legacy_market, symbol, "4h"):
                return False, VerifyIssue("SEAL_candle_binding_mismatch", "reject", {
                    "rel": rel, "sealed": (sc.get("market"), sc.get("symbol"), sc.get("interval")),
                    "row": (legacy_market, symbol, "4h")}), meta

        raw_bars = data.get("raw")
        if not isinstance(raw_bars, list) or not raw_bars:
            return False, VerifyIssue("ARCHIVE_empty_raw", "gap", {"rel": rel}), meta

        # last bar (as-of) first, same codes as before
        last = raw_bars[-1]
        try:
            open_ms = int(last[0])
            end_ms = int(last[6])
        except (TypeError, ValueError, IndexError):
            return False, VerifyIssue("ARCHIVE_bar_time_invalid", "reject", {"rel": rel}), meta
        if end_ms != open_ms + H4_MS - 1:
            return False, VerifyIssue("ARCHIVE_close_not_inclusive_H4", "reject", {
                "rel": rel, "open_ms": open_ms, "end_ms": end_ms}), meta
        if legacy_close is not None and int(legacy_close) != end_ms:
            return False, VerifyIssue("ARCHIVE_asof_mismatch", "reject", {
                "rel": rel, "candleAsOf": legacy_close, "last_end": end_ms}), meta

        # F3: verify EVERY bar of the declared window one-by-one:
        # time bounds, alignment, strict order/no duplicates, finite OHLC, continuity.
        if "count" in data and data.get("count") != len(raw_bars):
            return False, VerifyIssue("ARCHIVE_count_mismatch", "reject", {
                "rel": rel, "declared": data.get("count"), "actual": len(raw_bars)}), meta
        if "source_time" in data and data.get("source_time") != end_ms:
            return False, VerifyIssue("ARCHIVE_source_time_mismatch", "reject", {
                "rel": rel, "source_time": data.get("source_time"), "last_end": end_ms}), meta
        prev_open = None
        gap_ranges: list[dict] = []
        missing_total = 0
        window_start_idx = 0
        for i, bar in enumerate(raw_bars):
            try:
                o, e = int(bar[0]), int(bar[6])
                for k in (1, 2, 3, 4):
                    v = float(bar[k])
                    if v != v or v in (float("inf"), float("-inf")) or v <= 0:
                        raise ValueError("bad_ohlc")
            except (TypeError, ValueError, IndexError):
                return False, VerifyIssue("ARCHIVE_bar_time_invalid", "reject", {
                    "rel": rel, "index": i}), meta
            if e != o + H4_MS - 1:
                return False, VerifyIssue("ARCHIVE_close_not_inclusive_H4", "reject", {
                    "rel": rel, "open_ms": o, "end_ms": e, "index": i}), meta
            if o % H4_MS != 0:
                return False, VerifyIssue("ARCHIVE_open_misaligned", "reject", {
                    "rel": rel, "open_ms": o, "index": i}), meta
            if prev_open is not None:
                if o <= prev_open:
                    return False, VerifyIssue("ARCHIVE_bar_order_invalid", "reject", {
                        "rel": rel, "prev_open": prev_open, "open_ms": o, "index": i}), meta
                if o - prev_open != H4_MS:
                    miss = (o - prev_open) // H4_MS - 1
                    missing_total += miss
                    window_start_idx = i
                    if len(gap_ranges) < 20:
                        gap_ranges.append({"after_open_ms": prev_open, "next_open_ms": o, "missing_bars": miss})
            prev_open = o

        meta["verified_bar_count"] = len(raw_bars) - window_start_idx
        meta["range_end_close_ms"] = end_ms + 1
        if missing_total:
            # explicit gap; consumable range is limited to the verified contiguous TAIL window
            meta["range_start_open_ms"] = int(raw_bars[window_start_idx][0])
            return False, VerifyIssue("ARCHIVE_window_gap", "gap", {
                "rel": rel, "missing_bars_total": missing_total,
                "gap_ranges": gap_ranges,
                "declared_first_open_ms": int(raw_bars[0][0]),
                "verified_window_start_open_ms": meta["range_start_open_ms"],
                "verified_bar_count": meta["verified_bar_count"]}), meta
        meta["range_start_open_ms"] = int(raw_bars[0][0])
        return True, None, meta

    # ---- F1/F2: trusted re-verification of an envelope before it may be consumed ----
    def verify_envelope(self, envelope: "SourceEnvelope | dict") -> tuple[bool, list[VerifyIssue]]:
        """Re-derive the envelope from sealed + published evidence and require equality
        of every field. A forged / stale / altered envelope is rejected."""
        d = envelope.as_dict() if hasattr(envelope, "as_dict") else envelope
        if not isinstance(d, dict):
            return False, [VerifyIssue("ENVELOPE_not_mapping", "reject", {})]
        m = re.match(r"^(scan-[a-f0-9]{32})/r(\d{4})$", str(d.get("source_ref") or ""))
        if not m:
            return False, [VerifyIssue("ENVELOPE_source_ref_invalid", "reject", {"source_ref": d.get("source_ref")})]
        scan_id, rev = m.group(1), int(m.group(2))
        ing = d.get("ingested_at")
        if not isinstance(ing, str) or not ing:
            return False, [VerifyIssue("ENVELOPE_ingested_at_missing", "reject", {})]
        page = self.read_published_page(scan_id=scan_id, revision=rev, cursor=0, limit=10 ** 9,
                                        ingested_at=ing)
        blocking = [i for i in page.issues if i.severity == "reject" and "row_index" not in i.detail]
        if blocking:
            return False, [VerifyIssue("ENVELOPE_source_not_verified", "reject", {
                "codes": [i.code for i in blocking]})]
        hit = [e for e in page.envelopes if e.message_key == d.get("message_key")]
        if not hit:
            row_rejects = [i.code for i in page.issues if i.severity == "reject"]
            return False, [VerifyIssue("ENVELOPE_not_from_trusted_source", "reject", {
                "message_key": d.get("message_key"), "row_reject_codes": row_rejects})]
        want = hit[0].as_dict()
        keys = sorted(set(want) | set(d))
        diff = [k for k in keys if want.get(k) != d.get(k)]
        if diff:
            return False, [VerifyIssue("ENVELOPE_field_mismatch", "reject", {"fields": diff})]
        return True, []

    def load_confirm_rule_input(self, *, event_id=None, sources=None, head=None):
        """Recompute only from a verified, immutable archive of this contract.

        Archive candles do not prove historical candidate/OI qualification. Missing
        qualification stays unavailable; a neighboring test fixture is never read.
        """
        if not head or head.get("event_id") != event_id or not isinstance(sources, list):
            return None
        trigger = head.get("trigger_bar_open_ms")
        if not isinstance(trigger, int):
            return None
        prior = head.get("source_versions") or []
        for source in sources:
            if source.get("source") != "candles_4h":
                continue
            # New versions must first enter the source-revision path; no silent rebinding.
            if source not in prior:
                continue
            version = source.get("version") or ""
            match = re.match(r"^(scan-[a-f0-9]{32}):revision:(\d+):", version)
            if not match:
                continue
            scan_id, revision = match.group(1), int(match.group(2))
            cursor = 0
            found = None
            while True:
                page = self.read_published_page(scan_id=scan_id, revision=revision,
                    limit=100, cursor=cursor, ingested_at=head.get("revision_recorded_at"))
                if not page.ok:
                    break
                for env in page.envelopes:
                    if (env.market, env.symbol, env.evidence_ref, env.source_version, env.payload_sha256) == (
                        head.get("market"), head.get("symbol"), source.get("ref"), version, source.get("payload_sha256")):
                        found = env
                        break
                if found or not page.next_cursor:
                    break
                cursor = page.next_cursor
            if found is None or found.quality_gaps:
                continue
            entry = self._seal_entry(scan_id, revision)
            sealed = (entry or {}).get("candles", {}).get(found.evidence_ref)
            path = (self.root / found.evidence_ref).resolve()
            if not path.is_relative_to(self.history) or not sealed:
                continue
            raw = path.read_bytes()
            if sha256_bytes(raw) != sealed.get("sha256") or len(raw) != sealed.get("bytes"):
                continue
            data = json.loads(raw)
            bars = data.get("raw") or []
            index = next((i for i, bar in enumerate(bars) if int(bar[0]) == trigger), None)
            if index is None or index < 120:
                continue
            def ema(values, period):
                result = values[0]
                alpha = 2 / (period + 1)
                for value in values[1:]:
                    result += alpha * (value - result)
                return result
            def at(i):
                closes = [float(bar[4]) for bar in bars[:i + 1]]
                # Ratio is unchanged by a fixed inverse-contract multiplier.
                volumes = [float(bar[5] if found.market == "COIN-M" else bar[7]) for bar in bars[i - 7:i + 1]]
                denominator = sum(volumes[:-1]) / 7
                emas = [ema(closes, period) for period in (7, 25, 99)]
                return dict(bar_close_ms=int(bars[i][6]), ema7=emas[0], ema25=emas[1],
                    ema99=emas[2], close=closes[-1], vol_ratio7=volumes[-1] / denominator if denominator > 0 else None,
                    quality_ok=True, oi_valid=False)
            cross = at(index)
            previous = [ema([float(bar[4]) for bar in bars[:index]], period) for period in (7, 25, 99)]
            return {
                "cross_inputs": dict(ema7=cross["ema7"], ema25=cross["ema25"], ema99=cross["ema99"],
                    prev_ema7=previous[0], prev_ema25=previous[1], prev_ema99=previous[2], close=cross["close"]),
                "confirm_inputs": {"candidate_entered_at_ms": None, "historical": True,
                    "cross": cross, "next_bars": [at(index + 1)] if index + 1 < len(bars) else []},
                "provenance": {"event_id": event_id, "market": found.market, "symbol": found.symbol,
                    "trigger_open_ms": trigger, "source_version": version,
                    "candle_sha256": sealed["sha256"], "qualification": "historical_candidate_and_oi_unavailable"},
            }
        return None

    def accept_envelope(self, envelope: SourceEnvelope) -> dict:
        """Idempotent same-key same-hash; conflict on same-key different-hash."""
        return self.receipts.accept(envelope.message_key, envelope.payload_sha256)
