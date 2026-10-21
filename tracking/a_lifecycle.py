# -*- coding: utf-8 -*-
"""C2-c: full A lifecycle driver on top of C2-b transactional source/watermark layer.

Four separate dimensions (never collapsed):
  1) original_confirmation (technical_status)
  2) qualification
  3) follow_up_observation
  4) data_quality

Strong vs medium profiles via frozen EVENT_KIND_TO_PROFILE.
Correction chains still go through existing AOfflinePipeline / effective_chain.
Effective chain is shared (same builder as ledger write gate).

SOURCE_VERSION for this slice: codex-abc-tracking-20261019-v3
a_pipeline SOURCE_VERSION stays s2-r10-isolated (unchanged).
"""
from __future__ import annotations

import copy
from typing import Any

from .a_pipeline import AOfflinePipeline, H4, LEVEL_BY_PROFILE
from .a_runtime import (
    ARealDrive,
    EnvelopeRejected,
    classify_envelope_identity,
    _env_dict,
)
from .effective_chain import build_effective_transitions
from .mappings import (
    EVENT_KIND_TO_LEVEL,
    EVENT_KIND_TO_PROFILE,
    map_profile_id,
    separate_status_skeleton,
)
from .source_dispatch import SourceDispatch, CandleStep, version_set_hash

SOURCE_VERSION_TAG = "codex-abc-tracking-20261019-v3"
A_PIPELINE_SOURCE_VERSION = "s2-r10-isolated"  # unchanged unless unavoidable
CONSUMER_KEY_STRONG = "a_observer_strong"
CONSUMER_KEY_MEDIUM = "a_observer_medium"


def _consumer_for_kind(event_kind: str) -> str:
    level = EVENT_KIND_TO_LEVEL.get(event_kind)
    if level == "strong":
        return CONSUMER_KEY_STRONG
    if level == "medium":
        return CONSUMER_KEY_MEDIUM
    raise EnvelopeRejected("LIFECYCLE_unknown_event_kind", event_kind=event_kind)


def _quality_summary(missing_flags: list | None, gaps: list | None = None) -> str:
    if gaps:
        return "incomplete"
    if missing_flags:
        return "incomplete"
    return "ok"


def _four_dims(*, technical: str, qualification: str, observation: str | None,
               quality: str, historical: bool, missing_sources: list | None = None,
               first_gap_ms: int | None = None) -> dict:
    sk = separate_status_skeleton(
        technical_status=technical,
        qualification_status=qualification,
        observation_status=observation,
        data_quality_summary=quality,
        historical_import=historical,
    )
    if quality == "incomplete":
        dq = sk["data_quality"]
        if missing_sources is not None:
            dq["missing_sources"] = list(missing_sources)
        if first_gap_ms is not None:
            dq["first_gap_ms"] = first_gap_ms
    return sk


class ALifecycleDriver:
    """Drive real A lifecycle through C2-b receipt/job/watermark + existing pipeline."""

    def __init__(self, pipeline: AOfflinePipeline, trusted_source: Any = None):
        if trusted_source is None:
            raise EnvelopeRejected("TRUSTED_SOURCE_required")
        self.pipeline = pipeline
        self.ledger = pipeline.ledger
        self.dispatch = SourceDispatch(pipeline.ledger)
        self.drive = ARealDrive(pipeline, trusted_source=trusted_source)
        self.trusted_source = trusted_source

    def open_from_envelope(
        self,
        *,
        envelope: dict,
        first_seen_at: str,
        clock_iso: str,
        source_refs: list[dict],
        historical_import: bool = True,
        candidate_cycle_id: str | None = None,
    ) -> dict:
        """Accept sealed envelope into C2-b layer, then open_pending via ARealDrive.

        Fail-closed: missing clock/refs/trusted verify → reject before enqueue.
        Four dimensions returned separately; strong/medium from identity.event_kind.
        """
        if not first_seen_at or not clock_iso:
            return {"ok": False, "errors": [dict(code="CLOCK_or_first_seen_required")]}
        if not isinstance(source_refs, list) or not source_refs:
            return {"ok": False, "errors": [dict(code="SOURCE_REFS_required")]}
        try:
            env = _env_dict(envelope)
            ok, issues = self.trusted_source.verify_envelope(env)
            if not ok:
                raise EnvelopeRejected("ENVELOPE_untrusted", issues=[i.as_dict() for i in issues])
            kind_path = classify_envelope_identity(env)
            if kind_path == "skip":
                return {"ok": True, "skipped": True, "reason": "no_event",
                        "SOURCE_VERSION": SOURCE_VERSION_TAG}
            event_kind = env["identity"]["event_kind"]
            consumer = _consumer_for_kind(event_kind)
            close_ms = env["identity"]["bar"]["exclusive_close_ms"]
            receipt_env = {
                "source": env.get("source") or "a_archive",
                "source_ref": env.get("source_ref") or env.get("evidence_ref"),
                "version": env["source_version"],
                "payload_hash": env["payload_sha256"],
                "market": env["market"],
                "symbol": env["symbol"],
                "timeframe": env.get("timeframe") or "4h",
            }
            for req in ("source_ref", "version", "payload_hash"):
                if not receipt_env.get(req):
                    raise EnvelopeRejected("RECEIPT_field_required", field=req)
            accept = self.dispatch.accept_page(
                [receipt_env],
                consumer_jobs=[{
                    "consumer_key": consumer,
                    "next_close_ms": close_ms,
                }],
            )
            if not accept.get("ok"):
                return {"ok": False, "phase": "receipt", "accept": accept,
                        "SOURCE_VERSION": SOURCE_VERSION_TAG}
            opened = self.drive.drive_open_pending(
                envelope=env,
                first_seen_at=first_seen_at,
                clock_iso=clock_iso,
                source_refs=source_refs,
                candidate_cycle_id=candidate_cycle_id,
                historical_import=historical_import,
            )
            if not opened.get("ok"):
                return {"ok": False, "phase": "open_pending", "opened": opened,
                        "accept": accept, "SOURCE_VERSION": SOURCE_VERSION_TAG}
            dims = opened.get("dimensions") or _four_dims(
                technical="pending",
                qualification="unknown",
                observation=None,
                quality=_quality_summary((opened.get("revision") or {}).get("missing_data_flags")),
                historical=historical_import,
            )
            return {
                "ok": True,
                "phase": "open_pending",
                "event_id": opened.get("event_id"),
                "event_kind": event_kind,
                "profile_id": map_profile_id(event_kind),
                "level": EVENT_KIND_TO_LEVEL[event_kind],
                "consumer_key": consumer,
                "accept": accept,
                "opened": opened,
                "dimensions": dims,
                "SOURCE_VERSION": SOURCE_VERSION_TAG,
                "a_pipeline_SOURCE_VERSION": A_PIPELINE_SOURCE_VERSION,
            }
        except EnvelopeRejected as e:
            return {"ok": False, "rejected_before_enqueue": True, "errors": [e.as_dict()],
                    "SOURCE_VERSION": SOURCE_VERSION_TAG}

    def _recover_rule_input(self, *, event_id: str, sources: list[dict], head: dict) -> dict | None:
        """Restore original A confirm rule_input from trusted source only (no caller bypass)."""
        loader = getattr(self.trusted_source, "load_confirm_rule_input", None)
        if not callable(loader):
            return None
        try:
            got = loader(event_id=event_id, sources=sources, head=head)
        except TypeError:
            got = loader(event_id, sources, head)
        if got is None:
            return None
        if not isinstance(got, dict):
            return None
        if "cross_inputs" not in got or "confirm_inputs" not in got:
            return None
        return copy.deepcopy(got)

    def _assemble_job_effect(
        self,
        *,
        job_key: str,
        watermark_identity: dict,
        close_ms: int,
        present: bool,
        effect: dict,
    ) -> dict:
        """Build job_effect companion for consume_and_commit (same TX as business effects)."""
        return self.dispatch.build_candle_effect_spec(
            job_key,
            step=CandleStep(close_ms=close_ms, present=present),
            watermark_identity=watermark_identity,
            effect=effect,
        )

    def confirm_event(
        self,
        *,
        event_id: str,
        clock_iso: str,
        sources: list[dict],
        job_key: str | None = None,
        watermark_identity: dict | None = None,
        close_ms: int | None = None,
        **_ignored,
    ) -> dict:
        """Advance original_confirmation via existing pipeline confirm (+ optional effect ACK).

        rule_input is recovered from trusted_source. Caller technical_status is ignored
        (cannot bypass the original confirmor). Business persist + source effect ACK +
        watermark share one ledger transaction when job_key is provided.
        """
        if not clock_iso:
            return {"ok": False, "errors": [dict(code="CLOCK_required")],
                    "SOURCE_VERSION": SOURCE_VERSION_TAG}
        if not isinstance(sources, list) or not sources:
            return {"ok": False, "errors": [dict(code="SOURCES_required")],
                    "SOURCE_VERSION": SOURCE_VERSION_TAG}
        revs = self.ledger.get_revisions(event_id)
        if not revs:
            return {"ok": False, "errors": [dict(code="EVENT_not_found", event_id=event_id)],
                    "SOURCE_VERSION": SOURCE_VERSION_TAG}
        head = revs[-1]
        q_status = head.get("qualification_status") or "unknown"
        rule_input = self._recover_rule_input(event_id=event_id, sources=sources, head=head)
        if rule_input is None:
            return {"ok": False, "errors": [dict(code="RULE_INPUT_unavailable",
                                                 event_id=event_id,
                                                 detail="trusted_source did not restore confirm rule_input")],
                    "SOURCE_VERSION": SOURCE_VERSION_TAG}
        step = {
            "kind": "confirm",
            "rule_input": rule_input,
            "sources": sources,
            "recorded_at": clock_iso,
        }
        state = self.pipeline._restore(event_id, {
            "market": head["market"], "symbol": head["symbol"],
            "trigger_open_ms": head["trigger_bar_open_ms"],
        })
        job_effect = None
        if job_key and watermark_identity and isinstance(close_ms, int):
            spec = self._assemble_job_effect(
                job_key=job_key,
                watermark_identity=watermark_identity,
                close_ms=close_ms,
                present=True,
                effect={"kind": "confirm", "event_id": event_id, "close_ms": close_ms},
            )
            if not spec.get("ok"):
                return {"ok": False, "phase": "effect_ack_assemble", "errors": spec.get("errors"),
                        "SOURCE_VERSION": SOURCE_VERSION_TAG}
            job_effect = spec["job_effect"]
            step["job_effect"] = job_effect
        inbox_payload = {
            "event_id": event_id, "kind": "confirm", "step": step,
            "source_version_tag": SOURCE_VERSION_TAG,
        }
        msg_key = self.pipeline.message_key_for_step("confirm", event_id, step)
        enq = self.ledger.enqueue_inbox("a_lifecycle.confirm", inbox_payload, source_message_key=msg_key)
        if not enq.get("ok"):
            return {"ok": False, "errors": enq.get("errors"), "inbox": enq,
                    "SOURCE_VERSION": SOURCE_VERSION_TAG}
        if enq.get("status") == "acked":
            return {"ok": True, "idempotent": True, "event_id": event_id,
                    "dimensions": self.snapshot_dimensions(event_id),
                    "SOURCE_VERSION": SOURCE_VERSION_TAG}
        msg = {"key": msg_key, "inbox_id": enq["inbox_id"], "source": "a_lifecycle.confirm",
               "payload": inbox_payload}
        timeline: list = []
        ctx = {
            "eid": event_id,
            "market": head["market"],
            "symbol": head["symbol"],
            "open_ms": head["trigger_bar_open_ms"],
            "close_ms": head["trigger_bar_close_ms"],
            "first_seen": head.get("first_seen_at") or clock_iso,
        }
        res = self.pipeline._step_confirm(step, state, msg, ctx, timeline)
        if not res.get("ok"):
            return {"ok": False, "persist": res, "errors": res.get("errors"),
                    "SOURCE_VERSION": SOURCE_VERSION_TAG}
        dims = self.snapshot_dimensions(event_id)
        if (q_status == "unknown" and dims["qualification"]["status"] == "pass"):
            return {"ok": False, "errors": [dict(code="QUALIFICATION_collapsed_into_confirm")],
                    "dimensions": dims, "SOURCE_VERSION": SOURCE_VERSION_TAG}
        return {
            "ok": True,
            "event_id": event_id,
            "persist": res,
            "effect_ack": res.get("job_effect_ack"),
            "dimensions": dims,
            "timeline": timeline,
            "SOURCE_VERSION": SOURCE_VERSION_TAG,
            "a_pipeline_SOURCE_VERSION": A_PIPELINE_SOURCE_VERSION,
        }

    def _synthesize_gap_observation(self, *, sess: dict, close_ms: int) -> dict:
        """Build pause_missing observation_input for a missing candle at close_ms."""
        wm = int(sess["last_evaluated_bar_close_ms"])
        profile = sess.get("tracking_profile_id") or "A_strong_observation"
        level = LEVEL_BY_PROFILE.get(profile, "strong")
        start = int(sess.get("started_at_bar_close_ms") or wm) - H4
        return {
            "level": level,
            "start_after_open_ms": start,
            "bars": [
                {"open_ms": wm - H4, "ema7": 3.0, "ema25": 2.0, "ema99": 1.0, "close": 3.1},
                {"open_ms": close_ms, "ema7": 3.1, "ema25": 2.0, "ema99": 1.0, "close": 3.2},
            ],
        }

    def observe_tick(
        self,
        *,
        event_id: str,
        clock_iso: str,
        observation_input: dict,
        job_key: str | None = None,
        watermark_identity: dict | None = None,
        close_ms: int | None = None,
        present: bool = True,
    ) -> dict:
        """One observe tick; gap uses present=False → durable pause + watermark gap_pause same TX."""
        if not clock_iso:
            return {"ok": False, "errors": [dict(code="CLOCK_required")],
                    "SOURCE_VERSION": SOURCE_VERSION_TAG}
        if present and not isinstance(observation_input, dict):
            return {"ok": False, "errors": [dict(code="OBSERVATION_INPUT_required")],
                    "SOURCE_VERSION": SOURCE_VERSION_TAG}
        revs = self.ledger.get_revisions(event_id)
        if not revs:
            return {"ok": False, "errors": [dict(code="EVENT_not_found", event_id=event_id)],
                    "SOURCE_VERSION": SOURCE_VERSION_TAG}
        head = revs[-1]
        sess = self.ledger.get_session(event_id)
        kind = "observe_tick"
        obs_in = observation_input if isinstance(observation_input, dict) else {}
        if not present:
            if not (job_key and watermark_identity and isinstance(close_ms, int)):
                return {"ok": False, "errors": [dict(code="GAP_requires_job_and_close")],
                        "SOURCE_VERSION": SOURCE_VERSION_TAG}
            if sess is None:
                return {"ok": False, "errors": [dict(code="GAP_requires_session")],
                        "SOURCE_VERSION": SOURCE_VERSION_TAG}
            if sess.get("observation_status") == "ended":
                return {"ok": False, "errors": [dict(code="GAP_session_already_ended")],
                        "dimensions": self.snapshot_dimensions(event_id), "SOURCE_VERSION": SOURCE_VERSION_TAG}
            kind = "pause_missing"
            if not obs_in:
                obs_in = self._synthesize_gap_observation(sess=sess, close_ms=close_ms)
        step = {
            "kind": kind,
            "observation_input": obs_in,
            "recorded_at": clock_iso,
        }
        job_effect = None
        if job_key and watermark_identity and isinstance(close_ms, int):
            spec = self._assemble_job_effect(
                job_key=job_key,
                watermark_identity=watermark_identity,
                close_ms=close_ms,
                present=present,
                effect={"kind": "gap" if not present else "observe",
                        "event_id": event_id, "close_ms": close_ms},
            )
            if not spec.get("ok"):
                return {"ok": False, "phase": "effect_ack_assemble", "errors": spec.get("errors"),
                        "SOURCE_VERSION": SOURCE_VERSION_TAG}
            job_effect = spec["job_effect"]
            step["job_effect"] = job_effect
        state = self.pipeline._restore(event_id, {
            "market": head["market"], "symbol": head["symbol"],
            "trigger_open_ms": head["trigger_bar_open_ms"],
        })
        inbox_payload = {
            "event_id": event_id, "kind": kind, "step": step,
            "source_version_tag": SOURCE_VERSION_TAG,
        }
        msg_key = self.pipeline.message_key_for_step(kind, event_id, step)
        enq = self.ledger.enqueue_inbox(f"a_lifecycle.{kind}", inbox_payload, source_message_key=msg_key)
        if not enq.get("ok"):
            return {"ok": False, "errors": enq.get("errors"), "inbox": enq,
                    "SOURCE_VERSION": SOURCE_VERSION_TAG}
        if enq.get("status") == "acked":
            return {"ok": True, "idempotent": True, "event_id": event_id,
                    "dimensions": self.snapshot_dimensions(event_id),
                    "SOURCE_VERSION": SOURCE_VERSION_TAG}
        msg = {"key": msg_key, "inbox_id": enq["inbox_id"], "source": f"a_lifecycle.{kind}",
               "payload": inbox_payload}
        timeline: list = []
        ctx = {
            "eid": event_id,
            "market": head["market"],
            "symbol": head["symbol"],
            "open_ms": head["trigger_bar_open_ms"],
            "close_ms": head["trigger_bar_close_ms"],
            "first_seen": head.get("first_seen_at") or clock_iso,
        }
        res = self.pipeline._step_observe_tick(step, state, msg, ctx, timeline)
        if not res.get("ok"):
            # For gap path: still surface judge/persist failure without crashing.
            return {"ok": False, "phase": "gap_pause" if not present else "observe",
                    "persist": res, "errors": res.get("errors"),
                    "observe": (timeline[-1].get("observation") if timeline else None),
                    "SOURCE_VERSION": SOURCE_VERSION_TAG}
        dims = self.snapshot_dimensions(event_id)
        if not present:
            dims["data_quality"] = {
                "summary": "incomplete",
                "missing_sources": ["candles_4h"],
                "first_gap_ms": close_ms,
            }
            return {
                "ok": True,
                "phase": "gap_pause",
                "effect_ack": res.get("job_effect_ack"),
                "dimensions": dims,
                "persist": res,
                "SOURCE_VERSION": SOURCE_VERSION_TAG,
            }
        return {
            "ok": True,
            "event_id": event_id,
            "observe": (timeline[-1].get("observation") if timeline else None),
            "effect_ack": res.get("job_effect_ack"),
            "dimensions": dims,
            "effective_transitions": self.effective_chain_for(event_id),
            "persist": res,
            "SOURCE_VERSION": SOURCE_VERSION_TAG,
            "a_pipeline_SOURCE_VERSION": A_PIPELINE_SOURCE_VERSION,
        }


    def snapshot_dimensions(self, event_id: str) -> dict:
        revs = self.ledger.get_revisions(event_id)
        sess = self.ledger.get_session(event_id)
        gaps = []
        if sess:
            gaps = self.ledger.list_gaps(sess["session_id"], status="open")
        head = revs[-1] if revs else {}
        return _four_dims(
            technical=head.get("technical_status") or "pending",
            qualification=head.get("qualification_status") or "unknown",
            observation=(sess or {}).get("observation_status"),
            quality=_quality_summary(head.get("missing_data_flags"), gaps),
            historical=bool(head.get("discovery") in ("import", "historical_import")
                            or head.get("historical")),
        )

    def effective_chain_for(self, event_id: str) -> list[dict]:
        """Shared effective-chain projection (same builder as write gate)."""
        return build_effective_transitions(self.ledger.list_transitions(event_id))

    def strong_vs_medium_matrix(self) -> dict:
        return {
            "profiles": dict(EVENT_KIND_TO_PROFILE),
            "levels": dict(EVENT_KIND_TO_LEVEL),
            "consumers": {
                "a_full_cross": CONSUMER_KEY_STRONG,
                "a_medium_cross": CONSUMER_KEY_MEDIUM,
            },
            "note": "strong/medium stay separate sessions/profiles; weak out of v1 scope",
        }
