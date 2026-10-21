"""Offline A sample path using frozen S1-r2 rules (cross + T/T+1 vol confirm -> observation -> end).

S2-r7 (Codex S2-r6 FAIL, P1 S2-R6-F1 / P1 S2-R6-F2):
  After multiple pause/fill cycles, an authorized from-earliest historical revision
  that ends before the open gap must supersede the ENTIRE obsolete observation
  segment (every ordinary pause/backfill with at_bar_close_ms > ended_close), not
  only the latest pause. Each obsolete transition gets a revision>1 correction
  link; effective-time projection keeps one ended rewrite and drops superseded
  ordinary facts so the effective final state matches the durable session (ended).
  Ledger rejects self-referential / cyclic / non-predecessor corrects_transition_id
  in the same transaction (session unchanged on reject). SOURCE_VERSION is
  s2-r10-isolated. Observing-after-fill historical rewrite and equal-time pause supersede (S2-R7-F1/F2); prior multi-pause/self-ref holds remain.

S2-r6 (Codex S2-r5 FAIL, P1 S2-R5B-F1 / P2 S2-R5B-F2):
  When an authorized from-earliest historical revision ends a session that was
  paused_awaiting_data before an open gap, do NOT invent an ordinary revision=1
  backfilled transition at an earlier close that precedes the pause. Emit a proper
  correction transition: revision>1 and corrects_transition_id pointing at the pause
  transition being superseded. Effective-time replay excludes superseded transitions
  so the final state matches the durable session (ended). Prior r4/r5 holds for
  future-suffix-while-pending are unchanged.

S2-r5 (Codex S2-r4 FAIL, P1 S2-R4-F1):
  While a pending_history_replay plan is unresolved, history is replayed BEFORE the future.
  Any observe_tick / pause_missing that does not start at the plan's earliest affected root
  is held (TICK_history_replay_pending_hold; <=watermark suffix ends keep
  TICK_replay_skips_earliest_affected_root): nothing is written, the message is not ACKed,
  so an ordinary future tick can no longer create an authoritative natural end that skips
  the affected history. A from-earliest replay that is continuous through a future ending
  root, or that pauses at a gap at/after the watermark, completes the plan in the same
  transaction. A durable ended (non human-stop) session that still carries a pending plan
  (only reachable from pre-r5 ledgers) is never ACKed as durable_ended_kept; the tick fails
  TICK_pending_history_replay_blocked_by_terminal_session and the plan stays recoverable.
  human_stop closes an open plan as skipped_terminal (never revived). A new source revision
  that supersedes a pending plan keeps min(earliest affected root). The no-plan watermark
  guard (TICK_end_contradicts_evaluated_history) is unchanged.

S2-r4 (Codex S2-r3 FAIL, P1 S2-R3-F1/F2):
  Authorized historical replay must start at the plan's earliest affected root and cover a
  continuous bar range; a suffix that skips the earliest root is rejected and must NOT
  complete the pending plan. Healthy revision replay that re-covers from that earliest root
  through watermark_at_plan_ms completes the plan (quality replay_applied). Observation
  message identity binds to the pending plan_id so pre-revision ACKs do not suppress
  post-revision replays. Ordinary watermark guard stays for ticks without a covering plan.

S2-r3 (Codex S2-r2 CONDITIONAL, P1 S2-R2-C1):
  Historical source_revision that can affect already-evaluated history creates a durable
  recoverable replay plan (pending_history_replay). A later observe_tick under that plan
  may end at/under the watermark when the frozen rule finds structure_invalid in the
  authorized range. The TICK_end_contradicts_evaluated_history guard remains for
  ordinary ticks. human_stop stays terminal; watermark is never rewound.

S2-r2 (Codex S2-r1 FAIL):
  R1-F2  compute_qualification has NO override. A scenario step carrying
         force_qualification_status / qualification_override is rejected before any
         write. Overrides for negative fixtures live only in tests/ (a subclass hook),
         and the store independently refuses an A "pass" without evidence.
  R1-F3  every step re-reads authoritative state from the ledger (latest revision,
         confirm block, level via session profile, session status + watermark).
         Transition ids follow the frozen contract
         {session_id}|{at_bar_close_ms}|{to_state}|r{revision} - no per-run counters.
  R1-F5  every step is one inbox message: enqueue (key bound to source+payload) ->
         ONE consume_and_commit carrying revision/session/transitions/gaps/cursor/outbox
         and the ACK of that same inbox row. Healthy ticks advance the durable
         watermark+cursor; a complete repair persists paused->observing (backfilled)
         and a historical end found during repair is persisted as backfilled->ended.
         Any write failure is returned as ok=false (nothing applied, message stays pending).
  Duplicate delivery of an already-acked message is skipped (no re-apply).

Uses s1_contract_judge.a_cross_level / a_confirm / a_observation. Writes only through
TrackingLedger (isolated). Never touches production.
"""
from __future__ import annotations

import hashlib
import json
import sys
import time
from typing import Any

from .ledger import TrackingLedger, _canonical
from .paths import S1_CONTRACT_ROOT

_JUDGE_DIR = str(S1_CONTRACT_ROOT / "tests")
if _JUDGE_DIR not in sys.path:
    sys.path.insert(0, _JUDGE_DIR)

import s1_contract_judge as judge  # noqa: E402

H4 = 14_400_000
A_RULE = "ema-squeeze-v4"
OBS_PROFILE_VERSION = "tracking-observation-v1.1.0"
SOURCE_VERSION = "s2-r10-isolated"
FORBIDDEN_STEP_KEYS = ("force_qualification_status", "qualification_override")
STEP_KINDS = ("open_pending", "confirm", "observe_tick", "pause_missing", "human_stop", "source_revision")
# frozen ALLOWED_T reasons for observing -> paused_awaiting_data
PAUSE_REASON_MAP = {"missing_candles": "missing_candles", "missing_oi": "missing_oi", "missing_lsr": "missing_lsr",
                    "missing_cap": "missing_cap", "gap_blocks_continuity": "gap_blocks_continuity",
                    "evidence_unknown": "gap_blocks_continuity"}
LEVEL_BY_PROFILE = {"A_strong_observation": "strong", "A_medium_observation": "medium"}


def _sha_sources(sources: list[dict]) -> str:
    blob = json.dumps(sources, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def _evidence_hash(sources: list[dict]) -> str:
    try:
        return judge.evidence_hash(sources or [])
    except Exception:
        return _sha_sources(sources or [])


def make_event_id(market: str, symbol: str, open_ms: int) -> str:
    return f"A|a_full_cross|{market}|{symbol}|4h|{A_RULE}|{open_ms}"


def make_session_id(event_id: str, profile_id: str) -> str:
    return f"{event_id}|{profile_id}|{OBS_PROFILE_VERSION}"


def make_transition_id(session_id: str, at_bar_close_ms: int, to_state: str, revision: int = 1) -> str:
    """Frozen S1 contract identity (idempotency key); stable across restarts."""
    return f"{session_id}|{at_bar_close_ms}|{to_state}|r{revision}"


def build_source_versions(entries: list) -> list[dict]:
    out = []
    for e in entries:
        if isinstance(e, dict):
            out.append(dict(e))
            continue
        source, ref, version, eff, avail, payload = e
        out.append({
            "source": source,
            "ref": ref,
            "version": version,
            "effective_at_ms": eff,
            "available_at": avail,
            "payload_sha256": hashlib.sha256(payload.encode("utf-8")).hexdigest() if len(payload) != 64 else payload,
        })
    return out




def source_version_key(s: dict) -> tuple:
    return (s.get("source"), s.get("ref"), s.get("version"))


def earliest_history_affecting_effective_ms(prior_sources: list[dict], new_sources: list[dict]) -> int | None:
    """Return earliest effective_at_ms among new/changed source versions; None if none changed."""
    prior = {source_version_key(s): s for s in (prior_sources or []) if isinstance(s, dict)}
    affected: list[int] = []
    for s in (new_sources or []):
        if not isinstance(s, dict):
            continue
        key = source_version_key(s)
        old = prior.get(key)
        changed = old is None or old.get("payload_sha256") != s.get("payload_sha256")
        if not changed:
            continue
        eff = s.get("effective_at_ms")
        if isinstance(eff, int):
            affected.append(eff)
    return min(affected) if affected else None


def observation_first_open_ms(inp: dict) -> int:
    return int(inp["start_after_open_ms"]) + H4


def observation_bars_continuous_from(inp: dict, first_open: int) -> bool:
    """True iff bars are non-empty and contiguous 4H opens starting at first_open."""
    bars = inp.get("bars") or []
    if not bars:
        return False
    for i, b in enumerate(bars):
        if int(b["open_ms"]) != first_open + i * H4:
            return False
    return True


def replay_starts_at_earliest(inp: dict, plan: dict) -> bool:
    earliest = plan.get("replay_from_effective_ms")
    if not isinstance(earliest, int):
        return False
    return observation_first_open_ms(inp) == int(earliest)


def replay_covers_plan_watermark(inp: dict, plan: dict) -> bool:
    """Healthy path: continuous bars from earliest affected root through plan watermark."""
    if not replay_starts_at_earliest(inp, plan):
        return False
    earliest = int(plan["replay_from_effective_ms"])
    if not observation_bars_continuous_from(inp, earliest):
        return False
    wm_plan = plan.get("watermark_at_plan_ms")
    if not isinstance(wm_plan, int):
        return False
    last_close = int(inp["bars"][-1]["open_ms"]) + H4
    return last_close >= wm_plan


def replay_authorized_for_historical_end(inp: dict, plan: dict, ended_close: int) -> bool:
    """Invalidation path: start at earliest, continuous through the ending bar, end in range."""
    if not plan or plan.get("status") != "pending":
        return False
    if not isinstance(plan.get("replay_from_effective_ms"), int):
        return False
    if not replay_starts_at_earliest(inp, plan):
        return False
    earliest = int(plan["replay_from_effective_ms"])
    if not observation_bars_continuous_from(inp, earliest):
        return False
    if int(ended_close) < earliest:
        return False
    end_open = int(ended_close) - H4
    opens = {int(b["open_ms"]) for b in (inp.get("bars") or [])}
    return end_open in opens


def compute_qualification(
    *,
    candidate_cycle_id: str | None,
    sources: list[dict],
    step: dict | None = None,
    confirm_result: dict | None = None,
) -> tuple[str, list[str]]:
    """Compute qualification_status from candidate + contemporaneous evidence ONLY.

    Returns (status, flags). status in {pass, fail, unknown}. There is no override
    parameter: callers cannot ask for a status (S2-R1-F2).
    """
    step = step or {}
    flags: list[str] = []

    cyc = candidate_cycle_id
    if cyc is None or (isinstance(cyc, str) and not cyc.strip()):
        flags.append("missing_candidate_cycle")
        return "fail", flags

    src_by = {s.get("source"): s for s in (sources or []) if isinstance(s, dict)}
    if "candidate_store" not in src_by:
        flags.append("missing_candidate_evidence")
    else:
        cref = str(src_by["candidate_store"].get("ref") or "")
        if cyc not in cref:
            flags.append("candidate_ref_mismatch")

    if "oi" not in src_by:
        flags.append("missing_oi")

    if step.get("historical") or step.get("historical_qualification_missing"):
        flags.append("historical_qualification_missing")
        return "unknown", flags

    if confirm_result:
        reason = confirm_result.get("reason")
        status = confirm_result.get("status")
        if status == "blocked" and reason == "cross_precedes_candidate_qualification":
            flags.append(reason)
            return "fail", flags
        if status == "technical_only" and reason == "historical_qualification_missing":
            flags.append(reason)
            return "unknown", flags
        if status == "blocked" and reason == "cross_evidence_unknown":
            flags.append(reason)
            return "unknown", flags
        if reason in ("next_bar_oi_unknown", "next_bar_structure_unknown", "next_bar_volume_unknown"):
            flags.append(reason)
            return "unknown", flags

    if flags:
        if "candidate_ref_mismatch" in flags:
            return "fail", flags
        return "unknown", flags

    return "pass", flags


ALLOWED_MISSING_FLAGS = {
    "missing_candles",
    "missing_oi",
    "missing_lsr",
    "missing_cap",
    "missing_volume_baseline",
    "gap_blocks_continuity",
}


def _merge_missing_flags(base_flags, q_flags):
    """Only schema-allowed missing_data_flags values are persisted on the event."""
    out = list(base_flags or [])
    for f in q_flags or []:
        mapped = f
        if f in ("missing_candidate_evidence", "missing_candidate_cycle", "candidate_ref_mismatch"):
            continue
        if f in ("historical_qualification_missing", "cross_precedes_candidate_qualification",
                 "cross_evidence_unknown", "next_bar_structure_unknown", "next_bar_volume_unknown"):
            continue
        if f == "next_bar_oi_unknown":
            mapped = "missing_oi"
        if mapped in ALLOWED_MISSING_FLAGS and mapped not in out:
            out.append(mapped)
    return out


def _utc_fallback() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime()) + ".000000+00:00"


def _fail(code: str, **detail) -> dict:
    return {"ok": False, "errors": [dict(code=code, **detail)]}


class AOfflinePipeline:
    """Drive one A event through confirm -> observe -> end, persisting to the isolated ledger."""

    def __init__(self, ledger: TrackingLedger, constants: dict | None = None):
        self.ledger = ledger
        if constants is None:
            prof = judge.load(S1_CONTRACT_ROOT / "profiles" / "original-confirmation-freeze-v1.json")
            constants = dict(prof.get("constants") or {})
        self.K = constants

    # ------------------------------------------------------------------ rules
    def detect_and_confirm(self, inp: dict) -> dict:
        cross = judge.a_cross_level(inp["cross_inputs"], self.K)
        if not cross.get("creates_tracking_session"):
            return {"phase": "no_session", "cross": cross}
        level = cross["technical_level"]
        conf_inp = dict(inp["confirm_inputs"])
        conf_inp["level"] = level
        conf = judge.a_confirm(conf_inp, self.K)
        return {"phase": "confirm", "cross": cross, "confirm": conf, "level": level}

    def observe(self, inp: dict, *, event_id: str | None = None) -> dict:
        """Run observation rule, but a durable ended session wins over fresh rule output."""
        if event_id:
            sess = self.ledger.get_session(event_id)
            if sess and sess.get("observation_status") == "ended":
                ec = sess.get("ended_at_bar_close_ms")
                lc = sess.get("last_evaluated_bar_close_ms")
                return {
                    "session": "ended",
                    "end_reason": sess.get("end_reason"),
                    "ended_at_close_ms": ec,
                    "ended_at_open_ms": (ec - H4) if isinstance(ec, int) else None,
                    "human_stop_at_open_ms": None,
                    "natural_end_unverified": False,
                    "reason": None,
                    "last_evaluated_open_ms": (lc - H4) if isinstance(lc, int) else None,
                    "gap_from_open_ms": None,
                    "reopened_by_replay": False,
                    "from_durable_session": True,
                }
        return judge.a_observation(inp, self.K)

    def _qualify(self, **kw) -> tuple[str, list[str]]:
        """Business qualification = compute_qualification (no override)."""
        return compute_qualification(**kw)

    def persist_revision(self, record: dict, *, notify: bool = False, source_message_key: str | None = None) -> dict:
        """Plain revision append (no session companions) through the guarded ledger entry."""
        return self.ledger.append_revision(
            record,
            outbox_kind="a_state_change" if notify else None,
            outbox_payload={"event_id": record.get("event_id"), "revision": record.get("revision"),
                            "technical_status": record.get("technical_status")} if notify else None,
            source_message_key=source_message_key,
        )

    # ------------------------------------------------------------------ builders
    def _profile_for_level(self, level: str) -> str:
        return {"strong": "A_strong_observation", "medium": "A_medium_observation"}.get(level, "A_strong_observation")

    def _build_session(self, *, event_id: str, level: str, technical_status: str, observation_status: str,
                       started_at_bar_close_ms: int, last_evaluated_bar_close_ms: int | None = None,
                       ended_at_bar_close_ms: int | None = None, end_reason: str | None = None,
                       human_stopped: bool = False, gap_refs: list[str] | None = None) -> dict:
        profile = self._profile_for_level(level)
        return {
            "schema_version": "tracking-event-schema-v1.1.0",
            "session_id": make_session_id(event_id, profile),
            "event_id": event_id,
            "strategy": "A",
            "tracking_profile_id": profile,
            "tracking_profile_version": OBS_PROFILE_VERSION,
            "original_cycle_state": technical_status if technical_status in (
                "pending", "confirmed", "void", "blocked", "technical_only") else "confirmed",
            "observation_status": observation_status,
            "started_at_bar_close_ms": started_at_bar_close_ms,
            "last_evaluated_bar_close_ms": last_evaluated_bar_close_ms or started_at_bar_close_ms,
            "ended_at_bar_close_ms": ended_at_bar_close_ms,
            "end_reason": end_reason,
            "human_stopped": bool(human_stopped),
            "reference": {"kind": "none", "value": None, "from_open_ms": None, "to_open_ms": None},
            "gap_refs": list(gap_refs or []),
        }

    def _build_transition(self, *, session: dict, from_state: str | None, to_state: str, reason: str,
                          at_bar_close_ms: int, recorded_at: str, evidence_hash: str,
                          revision: int = 1, corrects_transition_id: str | None = None) -> dict:
        """Build a session transition. Ordinary writes use revision=1 and no corrects link.
        Corrections (schema x-revision-contract.transitions) must pass revision>1 and the
        transition_id being superseded; revision 1 must not carry corrects_transition_id.
        """
        if revision < 1:
            raise ValueError("transition revision must be >= 1")
        if revision == 1 and corrects_transition_id is not None:
            raise ValueError("revision 1 must not set corrects_transition_id")
        if revision > 1 and not corrects_transition_id:
            raise ValueError("correction transitions (revision>1) require corrects_transition_id")
        return {
            "schema_version": "tracking-event-schema-v1.1.0",
            "transition_id": make_transition_id(session["session_id"], at_bar_close_ms, to_state, revision),
            "session_id": session["session_id"],
            "timeframe": "4h",
            "from_state": from_state,
            "to_state": to_state,
            "at_bar_open_ms": at_bar_close_ms - H4,
            "at_bar_close_ms": at_bar_close_ms,
            "reason": reason,
            "recorded_at": recorded_at,
            "evidence_hash": evidence_hash if isinstance(evidence_hash, str) and len(evidence_hash) == 64
            else hashlib.sha256(str(evidence_hash).encode()).hexdigest(),
            "source_version": SOURCE_VERSION,
            "revision": int(revision),
            "corrects_transition_id": corrects_transition_id,
        }

    def _pause_transition_to_correct(self, eid: str) -> dict | None:
        """Latest ordinary pause transition for this event (not itself a correction)."""
        pause = None
        for t in self.ledger.list_transitions(eid):
            if (t.get("to_state") == "paused_awaiting_data"
                    and not t.get("corrects_transition_id")
                    and int(t.get("revision") or 1) == 1):
                pause = t
        return pause

    def _obsolete_observation_transitions(self, eid: str, ended_close: int) -> list[dict]:
        """Ordinary observation facts economically after an historical end rewrite.

        S2-R6-F1: after multiple pause/fill cycles, correcting only the latest pause
        leaves earlier pause/backfilled rows in the effective timeline. Every ordinary
        (revision=1, not itself a correction) transition economically after the last
        kept healthy bar must be superseded via corrects_transition_id. Raw history
        is retained.

        Boundary (S2-R7-F1 / S2-R7-F2):
        - last_kept_close = ended_close - H4 (close of the bar before the ending bar).
        - Any ordinary fact with close > last_kept_close is obsolete (includes pause
          or backfill at ended_close, and all later pause/fill cycles).
        - A pause exactly at last_kept_close is also obsolete: under the rewrite the
          next bar exists (and ends the session), so the missing-data pause did not
          happen. session_started / initial observing at last_kept_close is kept.
        """
        last_kept_close = int(ended_close) - H4
        out: list[dict] = []
        for t in self.ledger.list_transitions(eid):
            if int(t.get("revision") or 1) != 1:
                continue
            if t.get("corrects_transition_id"):
                continue
            if t.get("reason") == "session_started":
                continue
            close = t.get("at_bar_close_ms")
            if not isinstance(close, int):
                continue
            if close > last_kept_close:
                out.append(t)
            elif close == last_kept_close and t.get("to_state") == "paused_awaiting_data":
                out.append(t)
        out.sort(key=lambda t: (int(t["at_bar_close_ms"]), int(t.get("revision") or 1), t.get("transition_id") or ""))
        return out

    def _next_correction_revision(self, eid: str, corrected: dict) -> int:
        existing = [int(t.get("revision") or 1) for t in self.ledger.list_transitions(eid)]
        base = int((corrected or {}).get("revision") or 1)
        return max(existing + [base]) + 1

    # ------------------------------------------------------------------ authoritative state
    def _restore(self, eid: str, scenario: dict) -> dict:
        revs = self.ledger.get_revisions(eid)
        last = revs[-1] if revs else None
        sess = self.ledger.get_session(eid)
        level = LEVEL_BY_PROFILE.get((sess or {}).get("tracking_profile_id")) or scenario.get("level", "strong")
        return {
            "last_record": last,
            "rev": last["revision"] if last else 0,
            "session": sess,
            "level": level,
            "confirm": (last or {}).get("confirm"),
            "technical_status": (last or {}).get("technical_status"),
        }

    @staticmethod
    def default_message_key(kind: str, eid: str, step: dict) -> str:
        return f"a_pipeline.{kind}|{eid}|{hashlib.sha256(_canonical(step).encode('utf-8')).hexdigest()[:32]}"

    def message_key_for_step(self, kind: str, eid: str, step: dict) -> str:
        """Bind observe identity to pending replay plan so pre-revision ACKs do not suppress replays."""
        if kind in ("observe_tick", "pause_missing"):
            plan = self.ledger.get_pending_replay_plan(eid)
            if plan and plan.get("status") == "pending":
                bound = dict(step)
                bound["_replay_plan_id"] = plan.get("plan_id")
                bound["_replay_from_effective_ms"] = plan.get("replay_from_effective_ms")
                bound["_watermark_at_plan_ms"] = plan.get("watermark_at_plan_ms")
                return self.default_message_key(kind, eid, bound)
        return self.default_message_key(kind, eid, step)

    # ------------------------------------------------------------------ driver
    def run_demo_scenario(self, scenario: dict) -> dict:
        """End-to-end offline scenario; each step is one durable inbox message.

        scenario keys: market, symbol, trigger_open_ms, steps
        step kinds: open_pending, confirm, observe_tick, pause_missing, human_stop, source_revision
        """
        market = scenario["market"]
        symbol = scenario["symbol"]
        open_ms = int(scenario["trigger_open_ms"])
        eid = make_event_id(market, symbol, open_ms)
        timeline: list[dict] = []
        ctx = {"market": market, "symbol": symbol, "open_ms": open_ms, "close_ms": open_ms + H4, "eid": eid,
               "first_seen": scenario.get("first_seen_at", "2026-10-03T04:01:30.000Z"), "scenario": scenario}

        # validate every step BEFORE any write (no partial scenario on a forbidden step)
        for i, step in enumerate(scenario["steps"]):
            bad = [k for k in FORBIDDEN_STEP_KEYS if k in step]
            if bad:
                timeline.append({"step": step.get("kind"), "index": i, "error": "QUALIFICATION_override_forbidden",
                                 "keys": bad})
                return self._result(False, eid, timeline, [dict(code="QUALIFICATION_override_forbidden", keys=bad,
                                                                step_index=i)])
            if step.get("kind") not in STEP_KINDS:
                timeline.append({"step": step.get("kind"), "index": i, "error": "unknown_step"})
                return self._result(False, eid, timeline, [dict(code="unknown_step", step_index=i)])

        for step in scenario["steps"]:
            kind = step["kind"]
            state = self._restore(eid, scenario)
            key = step.get("source_message_key") or self.message_key_for_step(kind, eid, step)
            source = f"a_pipeline.{kind}"
            payload = {"event_id": eid, "kind": kind, "step": step}
            enq = self.ledger.enqueue_inbox(source, payload, source_message_key=key)
            if not enq.get("ok"):
                timeline.append({"step": kind, "inbox": enq})
                return self._result(False, eid, timeline, enq.get("errors"))
            if enq.get("status") == "acked":
                stored = self.ledger.get_inbox(key)
                timeline.append({"step": kind, "duplicate_delivery": True, "inbox": enq,
                                 "stored_result": (stored or {}).get("result")})
                continue
            msg = {"key": key, "inbox_id": enq["inbox_id"], "source": source, "payload": payload}
            handler = getattr(self, f"_step_{kind}") if kind != "pause_missing" else self._step_observe_tick
            res = handler(step, state, msg, ctx, timeline)
            if not res.get("ok"):
                if timeline and "persist" not in timeline[-1]:
                    timeline[-1]["persist"] = res
                return self._result(False, eid, timeline, res.get("errors"))
        return self._result(True, eid, timeline, None)

    def _result(self, ok: bool, eid: str, timeline: list, errors) -> dict:
        out = {
            "ok": ok,
            "event_id": eid,
            "revisions": self.ledger.get_revisions(eid),
            "session": self.ledger.get_session(eid),
            "transitions": self.ledger.list_transitions(eid),
            "cursor": self.ledger.get_cursor(f"a_observe|{eid}"),
            "timeline": timeline,
            "ledger_stats": self.ledger.stats(),
        }
        if errors:
            out["errors"] = errors
        return out

    def _commit(self, msg: dict, **effects) -> dict:
        # Prefer explicit job_effect kwarg; else take from the step payload (lifecycle binding).
        if "job_effect" not in effects:
            step = (msg.get("payload") or {}).get("step") or {}
            if isinstance(step, dict) and step.get("job_effect") is not None:
                effects["job_effect"] = step["job_effect"]
        return self.ledger.consume_and_commit(
            source_message_key=msg["key"], inbox_ack_id=msg["inbox_id"], message_source=msg["source"],
            message_payload=msg["payload"], **effects)

    # ------------------------------------------------------------------ steps
    def _step_open_pending(self, step, state, msg, ctx, timeline) -> dict:
        eid, open_ms, close_ms = ctx["eid"], ctx["open_ms"], ctx["close_ms"]
        sources = build_source_versions(step["sources"])
        avail = max(s["available_at"] for s in sources)
        cyc = step.get("candidate_cycle_id")
        q_status, q_flags = self._qualify(candidate_cycle_id=cyc, sources=sources, step=step)
        rec = {
            "schema_version": "tracking-event-schema-v1.1.0",
            "event_id": eid,
            "strategy": "A",
            "event_kind": "a_full_cross",
            "market": ctx["market"],
            "symbol": ctx["symbol"],
            "timeframe": "4h",
            "rule_version": A_RULE,
            "trigger_bar_open_ms": open_ms,
            "trigger_bar_close_ms": close_ms,
            "source_close_time_ms": close_ms - 1,
            "occurred_at_ms": close_ms,
            "decision_bar_close_ms": close_ms,
            "first_seen_at": ctx["first_seen"],
            "available_at": avail,
            "revision_recorded_at": step.get("recorded_at", ctx["first_seen"]),
            "discovery": "live",
            "technical_status": "pending",
            "qualification_status": q_status,
            "confirm": None,
            "candidate_cycle_id": cyc if cyc else None,
            "revision": 1,
            "revision_id": f"{eid}:r1",
            "prior_revision_id": None,
            "revision_reason": None,
            "source_versions": sources,
            "evidence_hash": _evidence_hash(sources),
            "legacy_refs": step.get("legacy_refs") or [],
            "missing_data_flags": _merge_missing_flags(step.get("missing_data_flags") or [], q_flags),
        }
        res = self._commit(msg, record=rec)
        timeline.append({"step": "open_pending", "persist": res, "technical_status": "pending",
                          "qualification_status": q_status})
        return res

    def _step_confirm(self, step, state, msg, ctx, timeline) -> dict:
        eid, open_ms = ctx["eid"], ctx["open_ms"]
        last = state["last_record"]
        rule_input = step["rule_input"]
        cross_close = ((rule_input.get("confirm_inputs") or {}).get("cross") or {}).get("bar_close_ms")
        if cross_close != ctx["close_ms"] - 1:
            return _fail("CONFIRM_trigger_input_mismatch", expected=ctx["close_ms"] - 1, actual=cross_close)
        det = self.detect_and_confirm(rule_input)
        conf = det.get("confirm") or {}
        level = det.get("level") or state["level"]
        timeline.append({"step": "rule_confirm", "result": conf, "cross": det.get("cross")})
        status = conf.get("status")
        if status not in ("confirmed", "void", "blocked", "pending", "technical_only"):
            res = self._commit(msg)  # nothing to apply; ACK the message
            timeline.append({"step": "confirm", "noop": "no_tracking_session", "persist": res})
            return res
        if last is None:
            return _fail("CONFIRM_without_pending_revision", event_id=eid)
        rev = state["rev"] + 1
        sources = build_source_versions(step["sources"])
        avail = max(s["available_at"] for s in sources)
        q_status, q_flags = self._qualify(candidate_cycle_id=last.get("candidate_cycle_id"), sources=sources,
                                          step=step, confirm_result=conf)
        missing = _merge_missing_flags(step.get("missing_data_flags") or [], q_flags)
        rec = dict(last)
        rec.update({
            "revision": rev,
            "revision_id": f"{eid}:r{rev}",
            "prior_revision_id": last["revision_id"],
            "revision_reason": "state_progression",
            "qualification_status": q_status,
            "available_at": avail,
            "source_versions": sources,
            "missing_data_flags": missing,
            "evidence_hash": _evidence_hash(sources),
        })
        if status != "confirmed":
            rec.update({
                "technical_status": status,
                "decision_bar_close_ms": conf.get("confirm_bar_close_ms") or last["decision_bar_close_ms"],
                "revision_recorded_at": step.get("recorded_at", avail),
                "confirm": None,
            })
            res = self._commit(msg, record=rec)
            timeline.append({"step": "confirm", "persist": res, "technical_status": status,
                             "qualification_status": q_status})
            return res
        offset = conf["confirm_offset"]
        c_open = open_ms + offset * H4
        c_close = c_open + H4
        first_confirmed_seen = step.get("first_confirmed_seen_at", step.get("recorded_at"))
        rec.update({
            "technical_status": "confirmed",
            "decision_bar_close_ms": c_close,
            "revision_recorded_at": first_confirmed_seen,
            "confirm": {
                "confirm_bar_open_ms": c_open,
                "confirm_bar_close_ms": c_close,
                "confirm_offset": offset,
                "first_confirmed_seen_at": first_confirmed_seen,
                "legacy_confirm_time": first_confirmed_seen,
                "legacy_confirm_time_semantics": "system_discovery_time",
            },
        })
        session = self._build_session(event_id=eid, level=level, technical_status="confirmed",
                                      observation_status="observing", started_at_bar_close_ms=c_close,
                                      last_evaluated_bar_close_ms=c_close)
        tr = self._build_transition(session=session, from_state=None, to_state="observing", reason="session_started",
                                    at_bar_close_ms=c_close, recorded_at=first_confirmed_seen,
                                    evidence_hash=rec["evidence_hash"])
        res = self._commit(msg, record=rec, session=session, transitions=[tr],
                           cursor=(f"a_observe|{eid}", str(c_close)), outbox_kind="a_state_change",
                           outbox_payload={"event_id": eid, "revision": rev, "technical_status": "confirmed"})
        timeline.append({"step": "confirm", "persist": res, "technical_status": "confirmed",
                         "qualification_status": q_status, "offset": offset, "session": "observing"})
        return res

    def _step_source_revision(self, step, state, msg, ctx, timeline) -> dict:
        """Source revision: append event revision; schedule per-root replay when history can change.

        Keeps confirm/qualification blocks on the event row (versioned; first confirmable time
        unchanged). Does NOT clear human_stop or reopen an ended session. When new/changed
        sources have effective_at_ms at/under the session watermark, writes a durable
        recoverable replay plan with quality_status=pending_history_replay so the notice ACK
        is not treated as judgment-complete.
        """
        eid, last, sess = ctx["eid"], state["last_record"], state["session"]
        if last is None:
            return _fail("SOURCE_REVISION_without_prior", event_id=eid)
        rev = state["rev"] + 1
        sources = build_source_versions(step["sources"])
        avail = max(s["available_at"] for s in sources)
        prior_sources = list(last.get("source_versions") or [])
        rec = dict(last)
        rec.update({
            "revision": rev,
            "revision_id": f"{eid}:r{rev}",
            "prior_revision_id": last["revision_id"],
            "revision_reason": "source_revision",
            "available_at": avail,
            "revision_recorded_at": step.get("recorded_at", avail),
            "source_versions": sources,
            "confirm": dict(last["confirm"]) if last.get("confirm") else None,
            "missing_data_flags": [],
            "evidence_hash": _evidence_hash(sources),
        })
        earliest = earliest_history_affecting_effective_ms(prior_sources, sources)
        replay_plan = None
        supersede_eid = None
        outbox = None
        plan_note = {"earliest_affected_effective_ms": earliest}
        if sess is not None:
            wm = sess.get("last_evaluated_bar_close_ms")
            terminal = sess.get("observation_status") == "ended"
            human = bool(sess.get("human_stopped"))
            plan_note.update({
                "session_id": sess.get("session_id"),
                "watermark_at_plan_ms": wm,
                "observation_status": sess.get("observation_status"),
                "human_stopped": human,
            })
            if terminal or human:
                # Keep terminal/human_stop; still record a skipped plan for auditability.
                plan_id = f"{eid}|source_rev_r{rev}|skipped_terminal"
                replay_plan = {
                    "plan_id": plan_id,
                    "event_id": eid,
                    "session_id": sess.get("session_id"),
                    "status": "skipped_terminal",
                    "replay_from_effective_ms": earliest,
                    "watermark_at_plan_ms": wm,
                    "quality_status": "terminal_no_replay",
                    "prior_sources": prior_sources,
                    "new_sources": sources,
                    "first_available_at": avail,
                    "detail": dict(plan_note, reason="session_terminal_or_human_stopped"),
                }
                supersede_eid = eid
            elif earliest is not None and isinstance(wm, int) and earliest <= wm:
                # S2-r5: a newer revision supersedes an unfinished plan but must not drop its
                # earlier affected root (earliest invalidation is kept across revisions).
                open_plan = self.ledger.get_pending_replay_plan(eid)
                if (open_plan and open_plan.get("status") == "pending"
                        and isinstance(open_plan.get("replay_from_effective_ms"), int)
                        and int(open_plan["replay_from_effective_ms"]) < earliest):
                    plan_note["carried_earliest_from_plan_id"] = open_plan.get("plan_id")
                    plan_note["own_earliest_affected_effective_ms"] = earliest
                    earliest = int(open_plan["replay_from_effective_ms"])
                    plan_note["earliest_affected_effective_ms"] = earliest
                plan_id = f"{eid}|source_rev_r{rev}|replay"
                replay_plan = {
                    "plan_id": plan_id,
                    "event_id": eid,
                    "session_id": sess.get("session_id"),
                    "status": "pending",
                    "replay_from_effective_ms": earliest,
                    "watermark_at_plan_ms": wm,
                    "quality_status": "pending_history_replay",
                    "prior_sources": prior_sources,
                    "new_sources": sources,
                    "first_available_at": avail,
                    "detail": dict(plan_note, reason="source_revision_affects_evaluated_history"),
                }
                supersede_eid = eid
                outbox = ("a_source_revision_replay_pending", {
                    "event_id": eid,
                    "plan_id": plan_id,
                    "revision": rev,
                    "replay_from_effective_ms": earliest,
                    "watermark_at_plan_ms": wm,
                    "quality_status": "pending_history_replay",
                })
            else:
                plan_note["reason"] = "no_history_overlap_or_no_change"
        res = self._commit(
            msg, record=rec, replay_plan=replay_plan, supersede_replay_event_id=supersede_eid,
            outbox_kind=outbox[0] if outbox else None, outbox_payload=outbox[1] if outbox else None)
        timeline.append({
            "step": "source_revision", "persist": res,
            "replay_plan": (replay_plan or {}).get("status"),
            "quality_status": (replay_plan or {}).get("quality_status"),
            "earliest_affected_effective_ms": earliest,
        })
        return res

    def _step_human_stop(self, step, state, msg, ctx, timeline) -> dict:
        eid, sess, last = ctx["eid"], state["session"], state["last_record"]
        stop = int(step["human_stop_at_open_ms"])
        if sess and sess.get("observation_status") == "ended":
            obs = {"session": "ended", "end_reason": sess.get("end_reason"),
                   "ended_at_close_ms": sess.get("ended_at_bar_close_ms"), "from_durable_session": True,
                   "reopened_by_replay": False}
            timeline.append({"step": "human_stop", "observation": obs})
            res = self._commit(msg)
            timeline.append({"step": "durable_ended_kept", "session": "ended", "persist": res})
            return res
        if sess is None:
            # frozen contract has no (None -> ended): nothing to stop; ACK as a recorded no-op
            res = self._commit(msg)
            timeline.append({"step": "human_stop", "noop": "no_observation_session", "persist": res})
            return res
        obs_inp = dict(step["observation_input"])
        obs_inp["human_stop_at_open_ms"] = stop
        obs = judge.a_observation(obs_inp, self.K)
        timeline.append({"step": "human_stop", "observation": obs})
        if obs.get("session") != "ended":
            return _fail("HUMAN_STOP_rule_did_not_end", observation=obs)
        wm = sess["last_evaluated_bar_close_ms"]
        ended_close = obs.get("ended_at_close_ms") or stop
        end_reason = obs.get("end_reason") or "human_stop"
        prev = sess["observation_status"]
        recorded = step.get("recorded_at") or _utc_fallback()
        eh = (last or {}).get("evidence_hash") or ("0" * 64)
        transitions = []
        if end_reason != "human_stop" and prev == "paused_awaiting_data":
            # a provable natural end inside the paused window: record backfill first (contract)
            gaps_open = self.ledger.list_gaps(sess["session_id"], status="open")
            g0 = min((g["detail"].get("gap_from_open_ms") for g in gaps_open), default=None)
            if g0 is None or ended_close - H4 < g0:
                return _fail("TICK_end_before_gap_contradicts_history", ended_close=ended_close, gap_from=g0)
            transitions.append(self._build_transition(session=sess, from_state=prev, to_state="observing",
                                                      reason="backfilled", at_bar_close_ms=g0 + H4,
                                                      recorded_at=recorded, evidence_hash=eh))
            prev = "observing"
        session = dict(sess)
        session.update({"observation_status": "ended", "ended_at_bar_close_ms": ended_close, "end_reason": end_reason,
                        "human_stopped": True, "last_evaluated_bar_close_ms": max(wm, ended_close)})
        transitions.append(self._build_transition(session=session, from_state=prev, to_state="ended", reason=end_reason,
                                                  at_bar_close_ms=ended_close, recorded_at=recorded, evidence_hash=eh))
        gaps = [dict(g, status="closed_session_ended") for g in self.ledger.list_gaps(sess["session_id"], status="open")]
        # S2-r5: a human stop is terminal and is never revived by history replay; close any
        # unfinished history plan as skipped_terminal in the same transaction so it is not
        # left dangling (same policy as a source_revision that arrives after a stop).
        # (supersede the pending row + insert a skipped_terminal record, as _step_source_revision
        # does for a terminal session; the ledger is not changed for this.)
        closed_plan = None
        supersede_eid = None
        open_plan = self.ledger.get_pending_replay_plan(eid)
        if open_plan and open_plan.get("status") == "pending":
            closed_plan = {k: v for k, v in open_plan.items() if k not in ("created_at_utc", "updated_at_utc")}
            closed_plan.update(plan_id=f"{open_plan['plan_id']}|human_stop|skipped_terminal",
                               status="skipped_terminal", quality_status="terminal_no_replay",
                               detail=dict(open_plan.get("detail") or {}, reason="human_stop_while_history_pending",
                                           superseded_plan_id=open_plan["plan_id"],
                                           closed_at_bar_close_ms=ended_close))
            supersede_eid = eid
        res = self._commit(msg, session=session, transitions=transitions, gaps=gaps,
                           cursor=(f"a_observe|{eid}", str(session["last_evaluated_bar_close_ms"])),
                           outbox_kind="a_session_ended",
                           outbox_payload={"event_id": eid, "end_reason": end_reason, "human_stopped": True},
                           replay_plan=closed_plan, supersede_replay_event_id=supersede_eid)
        timeline.append({"step": "persist_session_end", "persist": res, "end_reason": end_reason})
        return res

    def _step_observe_tick(self, step, state, msg, ctx, timeline) -> dict:
        """observe_tick / pause_missing: persist whatever the frozen rule says, per candle window."""
        eid, sess, last = ctx["eid"], state["session"], state["last_record"]
        kind = step["kind"]
        inp = step["observation_input"]
        obs = self.observe(inp, event_id=eid)
        entry = {"step": kind, "observation": obs}
        timeline.append(entry)
        pending_plan = self.ledger.get_pending_replay_plan(eid)
        plan_open = bool(pending_plan and pending_plan.get("status") == "pending")
        if obs.get("from_durable_session"):
            if plan_open and sess is not None and not sess.get("human_stopped"):
                # S2-R4-F1: an unresolved history plan on a natural-ended session (pre-r5 ledger
                # only) must not be swallowed as durable_ended_kept; keep message + plan pending.
                return _fail("TICK_pending_history_replay_blocked_by_terminal_session",
                             plan_id=pending_plan.get("plan_id"),
                             replay_from_effective_ms=pending_plan.get("replay_from_effective_ms"),
                             watermark_at_plan_ms=pending_plan.get("watermark_at_plan_ms"),
                             ended_at_bar_close_ms=sess.get("ended_at_bar_close_ms"),
                             end_reason=sess.get("end_reason"))
            res = self._commit(msg)
            entry.update(noop="durable_ended_kept", persist=res)
            return res
        if sess is None or obs.get("session") not in ("observing", "paused_awaiting_data", "ended"):
            res = self._commit(msg)
            entry.update(noop="no_observation_session" if sess is None else "not_tracked", persist=res)
            return res
        sid = sess["session_id"]
        wm = int(sess["last_evaluated_bar_close_ms"])
        prev = sess["observation_status"]
        first_open = observation_first_open_ms(inp)
        if first_open > wm:
            return _fail("TICK_window_skips_unevaluated_bars", first_bar_open_ms=first_open, watermark_close_ms=wm)
        if plan_open and not sess.get("human_stopped") and not replay_starts_at_earliest(inp, pending_plan):
            # S2-R4-F1: history first. Hold every tick that does not replay from the earliest
            # affected root while the plan is unresolved (no write, no ACK, plan stays pending).
            ec0 = obs.get("ended_at_close_ms")
            hold_detail = dict(watermark_close_ms=wm, first_bar_open_ms=first_open,
                               replay_from_effective_ms=pending_plan.get("replay_from_effective_ms"),
                               watermark_at_plan_ms=pending_plan.get("watermark_at_plan_ms"),
                               plan_id=pending_plan.get("plan_id"))
            if obs.get("session") == "ended" and isinstance(ec0, int) and int(ec0) <= wm:
                return _fail("TICK_replay_skips_earliest_affected_root", ended_close=int(ec0), **hold_detail)
            return _fail("TICK_history_replay_pending_hold", observed_session=obs.get("session"),
                         observed_ended_close_ms=ec0, **hold_detail)
        recorded = step.get("recorded_at") or _utc_fallback()
        eh = (last or {}).get("evidence_hash") or ("0" * 64)
        open_gaps = self.ledger.list_gaps(sid, status="open")
        last_eval_open = obs.get("last_evaluated_open_ms")
        cursor_name = f"a_observe|{eid}"
        st = obs["session"]
        session = dict(sess)
        session["gap_refs"] = list(sess.get("gap_refs") or [])
        transitions: list[dict] = []
        gaps: list[dict] = []
        outbox = None

        def resolve_covered(upto_open):
            for g in open_gaps:
                gf = g["detail"].get("gap_from_open_ms")
                if isinstance(gf, int) and upto_open is not None and gf <= upto_open:
                    gaps.append(dict(g, status="resolved"))

        def backfill_transition():
            g0 = min(g["detail"].get("gap_from_open_ms") for g in open_gaps)
            transitions.append(self._build_transition(session=session, from_state="paused_awaiting_data",
                                                      to_state="observing", reason="backfilled",
                                                      at_bar_close_ms=g0 + H4, recorded_at=recorded,
                                                      evidence_hash=eh))

        def mark_plan_complete(plan, *, healthy: bool = False):
            entry["authorized_source_revision_replay"] = True
            entry["replay_plan_id"] = plan["plan_id"]
            if healthy:
                entry["healthy_replay_complete"] = True

        if st == "ended":
            ended_close = int(obs.get("ended_at_close_ms"))
            end_open = ended_close - H4
            plan = pending_plan
            authorized_replay = False
            if ended_close <= wm:
                # Ordinary ticks still rejected. Authorized only under a durable pending
                # plan that starts at the earliest affected root and covers continuously
                # through the ending bar (S2-R3-F1: no suffix skip + false complete).
                if (plan and plan.get("status") == "pending" and not sess.get("human_stopped")
                        and replay_authorized_for_historical_end(inp, plan, ended_close)):
                    authorized_replay = True
                elif (plan and plan.get("status") == "pending" and not sess.get("human_stopped")
                      and not replay_starts_at_earliest(inp, plan)):
                    return _fail(
                        "TICK_replay_skips_earliest_affected_root",
                        ended_close=ended_close,
                        watermark_close_ms=wm,
                        first_bar_open_ms=first_open,
                        replay_from_effective_ms=plan.get("replay_from_effective_ms"),
                        plan_id=plan.get("plan_id"),
                    )
                elif (plan and plan.get("status") == "pending" and not sess.get("human_stopped")):
                    return _fail(
                        "TICK_replay_range_incomplete",
                        ended_close=ended_close,
                        watermark_close_ms=wm,
                        first_bar_open_ms=first_open,
                        replay_from_effective_ms=plan.get("replay_from_effective_ms"),
                        plan_id=plan.get("plan_id"),
                    )
                else:
                    return _fail("TICK_end_contradicts_evaluated_history", ended_close=ended_close,
                                 watermark_close_ms=wm)
            elif plan_open and not sess.get("human_stopped"):
                # S2-R4-F1: an end after the watermark under a pending plan is only accepted from
                # a replay that starts at the earliest root and is continuous through the ending
                # bar; that replay has re-judged all affected history, so it completes the plan.
                if replay_authorized_for_historical_end(inp, plan, ended_close):
                    authorized_replay = True
                else:
                    return _fail(
                        "TICK_replay_range_incomplete",
                        ended_close=ended_close,
                        watermark_close_ms=wm,
                        first_bar_open_ms=first_open,
                        replay_from_effective_ms=plan.get("replay_from_effective_ms"),
                        plan_id=plan.get("plan_id"),
                    )
            # S2-R7-F1: historical correction must not depend on currently being paused.
            # After gap-fill resume (prev=observing), authorized from-earliest rewrite that
            # ends early must still supersede every obsolete pause/backfill so durable
            # session ended matches the effective timeline.
            end_via_pause_correction = False
            g0 = min((g["detail"].get("gap_from_open_ms") for g in open_gaps), default=None)
            end_before_open_gap = isinstance(g0, int) and end_open < g0
            if prev == "paused_awaiting_data" and not (authorized_replay and end_before_open_gap):
                # Ordinary paused path: unauthorized end-before-gap fails; otherwise
                # resume via ordinary backfill then fall through to ordinary ended.
                if g0 is None or end_open < g0:
                    return _fail("TICK_end_before_gap_contradicts_history", ended_close=ended_close, gap_from=g0)
                backfill_transition()
                resolve_covered(end_open)
            elif authorized_replay:
                # S2-R5B-F1 / S2-R6-F1 / S2-R7-F1 / S2-R7-F2: authorized from-earliest
                # rewrite. Do NOT invent ordinary revision=1 backfilled before a pause.
                # Supersede EVERY ordinary obsolete observation fact (close > ended_close,
                # plus pause at close == ended_close). Works whether prev is paused or
                # observing after a real fill. from_state=observing keeps effective replay
                # coherent with the durable ended session.
                obsolete = self._obsolete_observation_transitions(eid, ended_close)
                if not obsolete and prev == "paused_awaiting_data":
                    pause_t = self._pause_transition_to_correct(eid)
                    if pause_t is None:
                        return _fail("TICK_pause_correction_missing_pause_transition",
                                     ended_close=ended_close, gap_from=g0)
                    obsolete = [pause_t]
                if obsolete:
                    corr_rev = self._next_correction_revision(eid, obsolete[0])
                    pre_state = "observing"
                    for obs_t in obsolete:
                        transitions.append(self._build_transition(
                            session=session,
                            from_state=pre_state,
                            to_state="ended",
                            reason=obs.get("end_reason"),
                            at_bar_close_ms=ended_close,
                            recorded_at=recorded,
                            evidence_hash=eh,
                            revision=corr_rev,
                            corrects_transition_id=obs_t["transition_id"],
                        ))
                        corr_rev += 1
                    end_via_pause_correction = True
            # Never rewind watermark; on authorized historical invalidation keep max(wm, ended).
            new_wm = max(wm, ended_close)
            session.update({"observation_status": "ended", "end_reason": obs.get("end_reason"),
                            "ended_at_bar_close_ms": ended_close, "human_stopped": False,
                            "last_evaluated_bar_close_ms": new_wm})
            # Ordinary path: after backfill_transition, prev chain ends at observing.
            # Correction path already wrote observing->ended (correcting obsolete facts).
            if not end_via_pause_correction:
                transitions.append(self._build_transition(session=session, from_state="observing", to_state="ended",
                                                          reason=obs.get("end_reason"), at_bar_close_ms=ended_close,
                                                          recorded_at=recorded, evidence_hash=eh))
            done = {g["gap_id"] for g in gaps}
            gaps += [dict(g, status="closed_session_ended") for g in open_gaps if g["gap_id"] not in done]
            outbox = ("a_session_ended", {"event_id": eid, "end_reason": obs.get("end_reason"),
                                          "human_stopped": False,
                                          "authorized_source_revision_replay": authorized_replay,
                                          "replay_plan_id": (plan or {}).get("plan_id") if authorized_replay else None})
            if authorized_replay and plan:
                mark_plan_complete(plan, healthy=False)
        elif st == "observing":
            new_wm = (int(last_eval_open) + H4) if last_eval_open is not None else wm
            # S2-R3-F2: healthy revision replay that re-covers earliest..watermark_at_plan
            # must complete the pending plan (even when no new closed bar beyond wm).
            if (pending_plan and pending_plan.get("status") == "pending"
                    and not sess.get("human_stopped")
                    and replay_covers_plan_watermark(inp, pending_plan)):
                mark_plan_complete(pending_plan, healthy=True)
                if new_wm <= wm:
                    # Same window re-confirmed healthy under the new source/plan identity.
                    res = self._commit(
                        msg,
                        cursor=(cursor_name, str(wm)),
                        complete_replay_plan_id=pending_plan["plan_id"],
                        outbox_kind="a_source_revision_replay_healthy",
                        outbox_payload={
                            "event_id": eid,
                            "plan_id": pending_plan["plan_id"],
                            "replay_from_effective_ms": pending_plan.get("replay_from_effective_ms"),
                            "watermark_at_plan_ms": pending_plan.get("watermark_at_plan_ms"),
                            "quality_status": "replay_applied",
                        },
                    )
                    entry.update(noop="healthy_replay_range_confirmed", persist=res,
                                 durable_session=sess["observation_status"],
                                 last_evaluated_bar_close_ms=wm)
                    return res
                session["last_evaluated_bar_close_ms"] = new_wm
                if prev == "paused_awaiting_data":
                    if not open_gaps:
                        return _fail("SESSION_paused_without_open_gap", session_id=sid)
                    backfill_transition()
                    resolve_covered(last_eval_open)
                    if len(gaps) != len(open_gaps):
                        return _fail("TICK_repair_incomplete", open_gaps=[g["gap_id"] for g in open_gaps])
                    session["observation_status"] = "observing"
                    outbox = ("a_session_resumed", {"event_id": eid, "reason": "backfilled",
                                                    "last_evaluated_bar_close_ms": new_wm})
            elif new_wm <= wm:
                res = self._commit(msg)
                entry.update(noop="no_new_closed_bar", persist=res)
                return res
            elif plan_open and not sess.get("human_stopped"):
                # S2-R4-F1: never advance the forward watermark past an unfinished history plan.
                return _fail("TICK_replay_range_incomplete", watermark_close_ms=wm, new_watermark_close_ms=new_wm,
                             first_bar_open_ms=first_open,
                             replay_from_effective_ms=pending_plan.get("replay_from_effective_ms"),
                             watermark_at_plan_ms=pending_plan.get("watermark_at_plan_ms"),
                             plan_id=pending_plan.get("plan_id"))
            else:
                session["last_evaluated_bar_close_ms"] = new_wm
                if prev == "paused_awaiting_data":
                    if not open_gaps:
                        return _fail("SESSION_paused_without_open_gap", session_id=sid)
                    backfill_transition()
                    resolve_covered(last_eval_open)
                    if len(gaps) != len(open_gaps):
                        return _fail("TICK_repair_incomplete", open_gaps=[g["gap_id"] for g in open_gaps])
                    session["observation_status"] = "observing"
                    outbox = ("a_session_resumed", {"event_id": eid, "reason": "backfilled",
                                                    "last_evaluated_bar_close_ms": new_wm})
        else:  # paused_awaiting_data
            gap_from = obs.get("gap_from_open_ms")
            if not isinstance(gap_from, int) or gap_from < wm:
                # the input lacks bars that were already evaluated: never pause/rewind on incomplete history
                return _fail("TICK_input_incomplete_for_evaluated_history", gap_from_open_ms=gap_from,
                             watermark_close_ms=wm)
            reason = PAUSE_REASON_MAP.get(obs.get("reason") or "", "gap_blocks_continuity")
            new_wm = max(wm, (int(last_eval_open) + H4) if last_eval_open is not None else wm)
            gid = f"{sid}|gap|{gap_from}"
            already_open = any(g["gap_id"] == gid for g in open_gaps)
            if plan_open and not sess.get("human_stopped"):
                # S2-R4-F1: replay from the earliest root re-judged every evaluated bar without an
                # end before reaching a gap at/after the watermark -> history plan is complete.
                mark_plan_complete(pending_plan, healthy=True)
            if already_open and new_wm == wm and prev == "paused_awaiting_data":
                if entry.get("authorized_source_revision_replay"):
                    res = self._commit(
                        msg, cursor=(cursor_name, str(wm)), complete_replay_plan_id=pending_plan["plan_id"],
                        outbox_kind="a_source_revision_replay_healthy",
                        outbox_payload={"event_id": eid, "plan_id": pending_plan["plan_id"],
                                        "replay_from_effective_ms": pending_plan.get("replay_from_effective_ms"),
                                        "watermark_at_plan_ms": pending_plan.get("watermark_at_plan_ms"),
                                        "quality_status": "replay_applied", "gap_still_open": gid})
                    entry.update(noop="gap_still_open_replay_complete", persist=res,
                                 durable_session=prev, last_evaluated_bar_close_ms=wm)
                    return res
                res = self._commit(msg)
                entry.update(noop="gap_still_open", persist=res)
                return res
            resolve_covered((gap_from - H4) if isinstance(gap_from, int) else None)
            if not already_open:
                gaps.append({"gap_id": gid, "session_id": sid, "scope": "a_observe_4h", "status": "open",
                             "detail": {"gap_from_open_ms": gap_from, "reason": obs.get("reason"),
                                        "transition_reason": reason, "watermark_close_ms": new_wm}})
            if gid not in session["gap_refs"]:
                session["gap_refs"].append(gid)
            session["last_evaluated_bar_close_ms"] = new_wm
            if prev == "observing":
                session["observation_status"] = "paused_awaiting_data"
                transitions.append(self._build_transition(session=session, from_state="observing",
                                                          to_state="paused_awaiting_data", reason=reason,
                                                          at_bar_close_ms=new_wm, recorded_at=recorded,
                                                          evidence_hash=eh))
                outbox = ("a_session_paused", {"event_id": eid, "reason": reason, "gap_from_open_ms": gap_from})
        complete_plan_id = entry.get("replay_plan_id") if entry.get("authorized_source_revision_replay") else None
        res = self._commit(msg, session=session, transitions=transitions, gaps=gaps,
                           cursor=(cursor_name, str(session["last_evaluated_bar_close_ms"])),
                           outbox_kind=outbox[0] if outbox else None, outbox_payload=outbox[1] if outbox else None,
                           complete_replay_plan_id=complete_plan_id)
        entry.update(persist=res, durable_session=session["observation_status"],
                     last_evaluated_bar_close_ms=session["last_evaluated_bar_close_ms"])
        return res
