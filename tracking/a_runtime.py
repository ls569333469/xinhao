"""C2-a single-message real-drive entry for A archive envelopes.

Explicit clock / first_seen / source_refs. Reuses existing AOfflinePipeline judge/handlers.
Does NOT run demo against production. Does NOT change candidate / T+1 rule logic.
Historical import discovery is never live. Qualification must not use current
candidate JSON as historical evidence (mark historical_qualification_missing).
"""
from __future__ import annotations

import re
from datetime import datetime
from typing import Any

from .a_archive_source import EVENT_STATES, SourceEnvelope
from .a_pipeline import (
    AOfflinePipeline,
    H4,
    OBS_PROFILE_VERSION,
    build_source_versions,
    make_event_id,
    make_session_id,
    _merge_missing_flags,
)
from .mappings import (
    EVENT_KIND_TO_PROFILE,
    MARKET_WHITELIST,
    discovery_for_archive,
    map_profile_id,
    separate_status_skeleton,
)


SOURCE_VERSION_TAG = "codex-abc-tracking-20261019-v3"
ARCHIVE_SOURCE_ROLE = "candles_4h"
SOURCE_REF_KEYS = frozenset(("source", "ref", "version", "effective_at_ms", "available_at", "payload_sha256"))
_SHA_RE = re.compile(r"^[a-f0-9]{64}$")


class EnvelopeRejected(ValueError):
    """Input rejected at the public boundary BEFORE anything is enqueued."""

    def __init__(self, code: str, **detail: Any):
        super().__init__(code)
        self.code = code
        self.detail = detail

    def as_dict(self) -> dict:
        return {"code": self.code, **self.detail}


def _env_dict(envelope: Any) -> dict:
    if isinstance(envelope, dict):
        return envelope
    if hasattr(envelope, "as_dict"):
        return envelope.as_dict()
    raise EnvelopeRejected("ENVELOPE_not_mapping", type=type(envelope).__name__)


def classify_envelope_identity(env: dict) -> str:
    """F1: returns "skip" (explicit no_event) or "event" (identity validated).
    Missing / conflicting identity raises EnvelopeRejected. Never defaults to a_full_cross."""
    state = env.get("event_state")
    ident = env.get("identity")
    if state not in EVENT_STATES:
        raise EnvelopeRejected("IDENTITY_event_state_missing_or_invalid", event_state=state)
    if state == "no_event":
        if ident:
            raise EnvelopeRejected("IDENTITY_conflict_no_event_with_identity")
        return "skip"
    if not isinstance(ident, dict) or not ident:
        raise EnvelopeRejected("IDENTITY_missing_for_event")
    kind = ident.get("event_kind")
    if kind not in EVENT_KIND_TO_PROFILE:
        raise EnvelopeRejected("IDENTITY_event_kind_invalid", event_kind=kind)
    if ident.get("profile_id") != map_profile_id(kind):
        raise EnvelopeRejected("IDENTITY_profile_conflict", profile_id=ident.get("profile_id"), event_kind=kind)
    market, symbol = env.get("market"), env.get("symbol")
    if market not in ("USDT-M", "COIN-M") or not isinstance(symbol, str) or not symbol:
        raise EnvelopeRejected("IDENTITY_market_symbol_invalid", market=market, symbol=symbol)
    if ident.get("market_canonical") != market or ident.get("symbol") != symbol:
        raise EnvelopeRejected("IDENTITY_market_symbol_conflict", env=(market, symbol),
                               identity=(ident.get("market_canonical"), ident.get("symbol")))
    if MARKET_WHITELIST.get(env.get("market_legacy")) != market or ident.get("market_legacy") != env.get("market_legacy"):
        raise EnvelopeRejected("IDENTITY_legacy_market_conflict")
    bar = ident.get("bar")
    if not isinstance(bar, dict):
        raise EnvelopeRejected("IDENTITY_bar_missing")
    open_ms, close_ms = bar.get("open_ms"), bar.get("exclusive_close_ms")
    if not (isinstance(open_ms, int) and isinstance(close_ms, int)) or close_ms != open_ms + H4 or open_ms % H4:
        raise EnvelopeRejected("IDENTITY_bar_invalid", bar=bar)
    if bar.get("legacy_inclusive_close_ms") != close_ms - 1 or env.get("legacy_inclusive_close_ms") != close_ms - 1:
        raise EnvelopeRejected("IDENTITY_bar_close_conflict")
    if env.get("range_end_close_ms") != close_ms:
        raise EnvelopeRejected("IDENTITY_bar_range_conflict", range_end_close_ms=env.get("range_end_close_ms"),
                               bar_close=close_ms)
    from .mappings import canonical_event_id
    if ident.get("canonical_event_id") != canonical_event_id(event_kind=kind, market=market, symbol=symbol, open_ms=open_ms):
        raise EnvelopeRejected("IDENTITY_canonical_id_conflict", got=ident.get("canonical_event_id"))
    if ident.get("discovery") != env.get("discovery"):
        raise EnvelopeRejected("IDENTITY_discovery_conflict")
    return "event"


def _parse_iso(v: Any) -> datetime:
    try:
        return datetime.fromisoformat(v)
    except (TypeError, ValueError):
        raise EnvelopeRejected("SOURCE_REFS_available_at_invalid", available_at=v)


def validate_source_refs(env: dict, source_refs: list, *, clock_iso: str) -> None:
    """F2: caller refs must agree with the verified envelope on role/ref/version/hash/
    contract(range end)/time. Exactly one archive candle ref; nothing unverifiable."""
    if not isinstance(source_refs, list) or len(source_refs) != 1 or not isinstance(source_refs[0], dict):
        raise EnvelopeRejected("SOURCE_REFS_must_be_exactly_one_archive_ref", n=len(source_refs) if isinstance(source_refs, list) else None)
    r = source_refs[0]
    extra = sorted(set(r) - SOURCE_REF_KEYS)
    missing = sorted(SOURCE_REF_KEYS - set(r))
    if extra or missing:
        raise EnvelopeRejected("SOURCE_REFS_keys_invalid", extra=extra, missing=missing)
    if r["source"] != ARCHIVE_SOURCE_ROLE:
        raise EnvelopeRejected("SOURCE_REFS_role_mismatch", got=r["source"], want=ARCHIVE_SOURCE_ROLE)
    want_ref = env.get("evidence_ref") or env.get("source_ref")
    if not want_ref or r["ref"] != want_ref:
        raise EnvelopeRejected("SOURCE_REFS_ref_mismatch", got=r["ref"], want=want_ref)
    if r["version"] != env.get("source_version"):
        raise EnvelopeRejected("SOURCE_REFS_version_mismatch", got=r["version"], want=env.get("source_version"))
    if not isinstance(r["payload_sha256"], str) or not _SHA_RE.match(r["payload_sha256"]) \
            or r["payload_sha256"] != env.get("payload_sha256"):
        raise EnvelopeRejected("SOURCE_REFS_hash_mismatch", got=r["payload_sha256"])
    if r["effective_at_ms"] != env.get("range_end_close_ms"):
        raise EnvelopeRejected("SOURCE_REFS_effective_time_mismatch", got=r["effective_at_ms"],
                               want=env.get("range_end_close_ms"))
    want_av = env.get("source_available_at")
    if want_av is None:
        # r3 TIME: unknown source availability is NEVER filled from caller/first-seen/clock time.
        raise EnvelopeRejected("SOURCE_AVAILABLE_AT_unknown", caller_available_at=r["available_at"])
    if not isinstance(want_av, str) or r["available_at"] != want_av:
        raise EnvelopeRejected("SOURCE_REFS_available_at_mismatch", got=r["available_at"], want=want_av)


def make_event_id_for_kind(market: str, symbol: str, open_ms: int, event_kind: str) -> str:
    """Canonical A event_id; supports strong and medium."""
    from .mappings import canonical_event_id
    return canonical_event_id(event_kind=event_kind, market=market, symbol=symbol, open_ms=open_ms)


def build_open_pending_step_from_envelope(
    envelope: SourceEnvelope | dict,
    *,
    first_seen_at: str,
    clock_iso: str,
    source_refs: list[dict],
    candidate_cycle_id: str | None = None,
    historical_import: bool = True,
    trusted_source: Any = None,
) -> dict:
    """Build one open_pending step dict — public boundary (F1/F2).

    Returns {"skipped": True, "reason": "no_event"} for an explicit no-event envelope.
    Raises EnvelopeRejected for missing/conflicting identity or untrusted/inconsistent
    refs. If trusted_source is given the envelope is also re-derived from sealed,
    published evidence. No defaults: never a_full_cross, never invented bar/profile."""
    env = _env_dict(envelope)
    if trusted_source is not None:
        ok, vi = trusted_source.verify_envelope(env)
        if not ok:
            raise EnvelopeRejected("ENVELOPE_untrusted", issues=[i.as_dict() for i in vi])
    if classify_envelope_identity(env) == "skip":
        return {"skipped": True, "reason": "no_event", "symbol": env.get("symbol"),
                "market": env.get("market"), "message_key": env.get("message_key")}
    identity = env["identity"]
    expected_discovery = discovery_for_archive(historical_import=historical_import)
    if env.get("discovery") != expected_discovery or env.get("discovery") == "live":
        raise EnvelopeRejected("DISCOVERY_mismatch_or_live", got=env.get("discovery"), want=expected_discovery)
    if candidate_cycle_id:
        raise EnvelopeRejected("CANDIDATE_cycle_unverified_in_C2a", candidate_cycle_id=candidate_cycle_id)
    if not first_seen_at or not clock_iso:
        raise EnvelopeRejected("CLOCK_or_first_seen_required")
    validate_source_refs(env, source_refs, clock_iso=clock_iso)
    event_kind = identity["event_kind"]
    market = env["market"]
    symbol = env["symbol"]
    open_ms = identity["bar"]["open_ms"]
    discovery = env["discovery"]

    # Source refs must be explicit; do not invent candidate_store from current JSON
    sources = build_source_versions(source_refs)
    legacy_refs = []
    for a in (identity.get("legacy_aliases") or []):
        # First alias is bind_identity; candidate timing alias only when cycle present
        if candidate_cycle_id and "|%s|" % candidate_cycle_id in a:
            legacy_refs.append({
                "system": "ema_event_timing.event_id",
                "id": a,
                "candidate_cycle_id": candidate_cycle_id,
            })
        else:
            legacy_refs.append({
                "system": "ema_observations.bind_identity",
                "id": a,
            })
    # Map quality gaps to schema-allowed missing_data_flags only
    gap_map = {
        "missing_candle_archive_ref": "missing_candles",
        "ARCHIVE_ref_missing": "missing_candles",
        "ARCHIVE_empty_raw": "missing_candles",
        "ARCHIVE_tail_gap": "gap_blocks_continuity",
        "ARCHIVE_window_gap": "gap_blocks_continuity",
    }
    missing = []
    for g in (env.get("quality_gaps") or []):
        m = gap_map.get(g)
        if m and m not in missing:
            missing.append(m)
    step: dict[str, Any] = {
        "kind": "open_pending",
        "sources": sources,
        "recorded_at": clock_iso,
        "legacy_refs": legacy_refs,
        "historical": bool(historical_import),
        "historical_qualification_missing": bool(historical_import and not candidate_cycle_id),
        "event_kind": event_kind,
        "discovery": discovery,
        "missing_data_flags": missing,
    }
    if candidate_cycle_id:
        step["candidate_cycle_id"] = candidate_cycle_id
    else:
        # Explicit: no candidate — qualification stays unknown/fail via existing compute_qualification
        step["candidate_cycle_id"] = None

    ctx = {
        "market": market,
        "symbol": symbol,
        "open_ms": open_ms,
        "close_ms": open_ms + H4,
        "eid": make_event_id_for_kind(market, symbol, open_ms, event_kind),
        "first_seen": first_seen_at,
        "event_kind": event_kind,
        "discovery": discovery,
        "profile_id": identity["profile_id"],
        "historical_import": historical_import,
        "source_envelope_ref": {
            "source_ref": env.get("source_ref"),
            "source_version": env.get("source_version"),
            "payload_sha256": env.get("payload_sha256"),
            "message_key": env.get("message_key"),
            "evidence_ref": env.get("evidence_ref"),
        },
    }
    return {"step": step, "ctx": ctx}


class ARealDrive:
    """Thin real-input driver: one durable inbox message → existing handler."""

    def __init__(self, pipeline: AOfflinePipeline, trusted_source: Any = None):
        self.pipeline = pipeline
        self.ledger = pipeline.ledger
        # F2: the trusted sealed source the envelope must be re-derived from. Without it
        # every drive call fails closed (nothing is enqueued).
        self.trusted_source = trusted_source

    def drive_open_pending(
        self,
        *,
        envelope: SourceEnvelope | dict,
        first_seen_at: str,
        clock_iso: str,
        source_refs: list[dict],
        candidate_cycle_id: str | None = None,
        historical_import: bool = True,
        source_message_key: str | None = None,
    ) -> dict:
        """Single-message entry. Clock and first_seen are required (no demo defaults)."""
        if not first_seen_at or not clock_iso:
            return {"ok": False, "errors": [dict(code="CLOCK_or_first_seen_required")]}
        if not source_refs:
            return {"ok": False, "errors": [dict(code="SOURCE_REFS_required")]}
        if self.trusted_source is None:
            return {"ok": False, "errors": [dict(code="TRUSTED_SOURCE_required")]}

        # everything below validates BEFORE enqueue; any rejection leaves events/inbox untouched
        try:
            env_d = _env_dict(envelope)
            if source_message_key is not None and source_message_key != env_d.get("message_key"):
                raise EnvelopeRejected("MESSAGE_KEY_conflict", got=source_message_key,
                                       want=env_d.get("message_key"))
            built = build_open_pending_step_from_envelope(
                env_d,
                first_seen_at=first_seen_at,
                clock_iso=clock_iso,
                source_refs=source_refs,
                candidate_cycle_id=candidate_cycle_id,
                historical_import=historical_import,
                trusted_source=self.trusted_source,
            )
        except EnvelopeRejected as e:
            return {"ok": False, "rejected_before_enqueue": True, "errors": [e.as_dict()]}
        if built.get("skipped"):
            return {"ok": True, "skipped": True, "reason": built["reason"], "enqueued": False,
                    "symbol": built.get("symbol"), "market": built.get("market")}
        step, ctx = built["step"], built["ctx"]
        eid = ctx["eid"]
        # Override pipeline hard-coded a_full_cross / live via a patched step path
        key = env_d.get("message_key")
        if not key:
            key = self.pipeline.default_message_key("open_pending", eid, step)

        source = "a_runtime.open_pending"
        payload = {
            "event_id": eid,
            "kind": "open_pending",
            "step": step,
            "source_version_tag": SOURCE_VERSION_TAG,
            "ctx_discovery": ctx["discovery"],
            "ctx_event_kind": ctx["event_kind"],
        }
        enq = self.ledger.enqueue_inbox(source, payload, source_message_key=key)
        if not enq.get("ok"):
            return {"ok": False, "errors": enq.get("errors"), "inbox": enq, "event_id": eid}
        if enq.get("status") == "acked":
            stored = self.ledger.get_inbox(key)
            return {
                "ok": True,
                "idempotent": True,
                "event_id": eid,
                "inbox": enq,
                "stored_result": (stored or {}).get("result"),
                "dimensions": separate_status_skeleton(
                    technical_status="pending",
                    qualification_status="unknown",
                    observation_status=None,
                    data_quality_summary="unchanged_idempotent",
                    historical_import=historical_import,
                ),
            }

        msg = {"key": key, "inbox_id": enq["inbox_id"], "source": source, "payload": payload}
        # Use existing handler but inject discovery/event_kind into ctx for record build
        timeline: list = []
        state = self.pipeline._restore(eid, {"market": ctx["market"], "symbol": ctx["symbol"],
                                            "trigger_open_ms": ctx["open_ms"]})
        res = self._open_pending_real(step, state, msg, ctx, timeline)
        out = {
            "ok": bool(res.get("ok")),
            "event_id": eid,
            "persist": res,
            "timeline": timeline,
            "ctx": {k: ctx[k] for k in ("market", "symbol", "open_ms", "close_ms", "event_kind",
                                       "discovery", "profile_id", "historical_import",
                                       "source_envelope_ref", "first_seen")},
            "SOURCE_VERSION": SOURCE_VERSION_TAG,
        }
        if not res.get("ok"):
            out["errors"] = res.get("errors")
            return out
        # Read back revision for dimension snapshot
        revs = self.ledger.get_revisions(eid)
        last = revs[-1] if revs else {}
        out["dimensions"] = separate_status_skeleton(
            technical_status=last.get("technical_status") or "pending",
            qualification_status=last.get("qualification_status") or "unknown",
            observation_status=None,
            data_quality_summary="ok" if not step.get("missing_data_flags") else "incomplete",
            historical_import=historical_import,
        )
        out["revision"] = last
        return out

    def _open_pending_real(self, step, state, msg, ctx, timeline) -> dict:
        """Like AOfflinePipeline._step_open_pending but respects event_kind + discovery from ctx."""
        eid, open_ms, close_ms = ctx["eid"], ctx["open_ms"], ctx["close_ms"]
        sources = build_source_versions(step["sources"])
        # available_at: max of provided source available_at; unknown sources stay out
        avail_list = [s["available_at"] for s in sources if s.get("available_at")]
        avail = max(avail_list) if avail_list else None
        cyc = step.get("candidate_cycle_id")
        # Historical archive without contemporaneous candidate evidence: qualification
        # stays unknown. Do NOT invent current candidate JSON. Do not change T+1 rules.
        if ctx.get("historical_import") and not cyc:
            q_status, q_flags = "unknown", ["historical_qualification_missing", "missing_candidate_evidence"]
        else:
            q_status, q_flags = self.pipeline._qualify(candidate_cycle_id=cyc, sources=sources, step=step)
        event_kind = ctx.get("event_kind") or "a_full_cross"
        discovery = ctx.get("discovery") or "import"
        rec = {
            "schema_version": "tracking-event-schema-v1.1.0",
            "event_id": eid,
            "strategy": "A",
            "event_kind": event_kind,
            "market": ctx["market"],
            "symbol": ctx["symbol"],
            "timeframe": "4h",
            "rule_version": "ema-squeeze-v4",
            "trigger_bar_open_ms": open_ms,
            "trigger_bar_close_ms": close_ms,
            "source_close_time_ms": close_ms - 1,
            "occurred_at_ms": close_ms,
            "decision_bar_close_ms": close_ms,
            "first_seen_at": ctx["first_seen"],
            "available_at": avail if avail is not None else ctx["first_seen"],
            "revision_recorded_at": step.get("recorded_at", ctx["first_seen"]),
            "discovery": discovery,
            "technical_status": "pending",
            "qualification_status": q_status,
            "confirm": None,
            "candidate_cycle_id": cyc if cyc else None,
            "revision": 1,
            "revision_id": f"{eid}:r1",
            "prior_revision_id": None,
            "revision_reason": None,
            "source_versions": sources,
            "evidence_hash": __import__("hashlib").sha256(
                __import__("json").dumps(sources, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode()
            ).hexdigest(),
            "legacy_refs": step.get("legacy_refs") or [],
            "missing_data_flags": _merge_missing_flags(step.get("missing_data_flags") or [], q_flags),
        }
        # Prefer pipeline evidence hash helper when available
        try:
            from .a_pipeline import _evidence_hash
            rec["evidence_hash"] = _evidence_hash(sources)
        except Exception:
            pass
        res = self.pipeline._commit(msg, record=rec)
        timeline.append({
            "step": "open_pending_real",
            "persist": res,
            "technical_status": "pending",
            "qualification_status": q_status,
            "discovery": discovery,
            "event_kind": event_kind,
            "profile_id": ctx.get("profile_id"),
            "historical_import": ctx.get("historical_import"),
        })
        return res
