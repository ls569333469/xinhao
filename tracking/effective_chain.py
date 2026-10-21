"""Deterministic effective transition chain — shared by ledger writes and read_api.

S2-DEEP-F1: write path must merge existing facts + pending corrections into the same
effective chain definition used by reads, then reject (full txn rollback) if the
chain is illegal. Read/export must not separately invent hide rules that paper over
bad writes; they project this same builder.
"""
from __future__ import annotations

from typing import Any

# Match package.a_pipeline.H4 without importing a_pipeline (avoids import cycles).
H4 = 14_400_000
TERMINAL = "ended"
LEGAL_START_REASON = "session_started"
LEGAL_START_TO = "observing"


def build_effective_transitions(transitions: list[dict]) -> list[dict]:
    """Drop transitions superseded by a later correction (corrects_transition_id).

    Same definition as r8 read_api: correction links remove targets; when several
    ended corrections rewrite the same close, keep highest-revision per close;
    belt-and-suspenders drops unlinked ordinary facts after last_kept_close.
    """
    if not transitions:
        return []
    superseded = {t.get("corrects_transition_id") for t in transitions if t.get("corrects_transition_id")}
    remaining = [t for t in transitions if t.get("transition_id") not in superseded]

    end_corrs = [
        t for t in remaining
        if int(t.get("revision") or 1) > 1
        and t.get("corrects_transition_id")
        and t.get("to_state") == TERMINAL
        and isinstance(t.get("at_bar_close_ms"), int)
    ]
    if end_corrs:
        best: dict[int, dict] = {}
        for t in end_corrs:
            c = int(t["at_bar_close_ms"])
            if c not in best or int(t.get("revision") or 1) > int(best[c].get("revision") or 1):
                best[c] = t
        keep = {t["transition_id"] for t in best.values()}
        drop = {t["transition_id"] for t in end_corrs if t.get("transition_id") not in keep}
        remaining = [t for t in remaining if t.get("transition_id") not in drop]
        E = min(best.keys())
        last_kept = E - H4
        remaining = [
            t for t in remaining
            if not (
                int(t.get("revision") or 1) == 1
                and not t.get("corrects_transition_id")
                and t.get("reason") != LEGAL_START_REASON
                and isinstance(t.get("at_bar_close_ms"), int)
                and (
                    int(t["at_bar_close_ms"]) > last_kept
                    or (
                        int(t["at_bar_close_ms"]) == last_kept
                        and t.get("to_state") == "paused_awaiting_data"
                    )
                )
            )
        ]
    return remaining


def order_effective_chain(effective: list[dict]) -> list[dict]:
    """Deterministic order for chain replay = builder output order (ledger seq).

    Do NOT re-sort by at_bar_close_ms: same-close human_stop / structure_invalid after
    session_started must stay after the start; historical repair ends are appended in
    seq after pause/backfill. Read_api uses the same preserved order.
    """
    return list(effective)


def validate_effective_chain(effective: list[dict], session: dict | None = None) -> list[dict]:
    """Require a unique legal start, step from_state continuity, no post-terminal
    continuation, and (when session given) start close == started_at_bar_close_ms
    plus final state/end_reason/time match.
    """
    errs: list[dict] = []
    if not effective:
        errs.append(dict(code="EFFECTIVE_CHAIN_empty"))
        return errs

    ordered = order_effective_chain(effective)

    starts = [t for t in ordered if t.get("from_state") is None]
    if len(starts) != 1:
        errs.append(dict(
            code="EFFECTIVE_CHAIN_start_count",
            count=len(starts),
            want=1,
            start_ids=[t.get("transition_id") for t in starts],
        ))
    else:
        s0 = starts[0]
        if s0.get("reason") != LEGAL_START_REASON or s0.get("to_state") != LEGAL_START_TO:
            errs.append(dict(
                code="EFFECTIVE_CHAIN_illegal_start",
                transition_id=s0.get("transition_id"),
                reason=s0.get("reason"),
                to_state=s0.get("to_state"),
                want_reason=LEGAL_START_REASON,
                want_to_state=LEGAL_START_TO,
            ))
        if ordered[0].get("transition_id") != s0.get("transition_id"):
            errs.append(dict(
                code="EFFECTIVE_CHAIN_start_not_first",
                first_id=ordered[0].get("transition_id"),
                start_id=s0.get("transition_id"),
            ))
        # S2-r10 / remaining DEEP-F1 variant: unique effective start close must equal
        # the fixed session start anchor (session.started_at_bar_close_ms). Corrections
        # may not rewrite start time while identity keeps the original started_at.
        if session is not None:
            want_start = session.get("started_at_bar_close_ms")
            got_start = s0.get("at_bar_close_ms")
            if want_start is not None and got_start != want_start:
                errs.append(dict(
                    code="EFFECTIVE_CHAIN_start_close_mismatch",
                    transition_id=s0.get("transition_id"),
                    session_started_at=want_start,
                    effective_start_close=got_start,
                ))

    state: Any = None
    seen_terminal = False
    for i, t in enumerate(ordered):
        if seen_terminal:
            errs.append(dict(
                code="EFFECTIVE_CHAIN_after_terminal",
                index=i,
                transition_id=t.get("transition_id"),
                from_state=t.get("from_state"),
                to_state=t.get("to_state"),
            ))
        if t.get("from_state") != state:
            errs.append(dict(
                code="EFFECTIVE_CHAIN_from_state_mismatch",
                index=i,
                transition_id=t.get("transition_id"),
                expected_from_state=state,
                actual_from_state=t.get("from_state"),
            ))
        state = t.get("to_state")
        if state == TERMINAL:
            seen_terminal = True

    if session is not None:
        want_status = session.get("observation_status")
        if state != want_status:
            errs.append(dict(
                code="EFFECTIVE_CHAIN_session_state_mismatch",
                chain_final=state,
                session_status=want_status,
            ))
        if want_status == TERMINAL:
            end_rows = [t for t in ordered if t.get("to_state") == TERMINAL]
            if not end_rows:
                errs.append(dict(code="EFFECTIVE_CHAIN_session_ended_without_end_transition"))
            else:
                last_end = end_rows[-1]
                if session.get("end_reason") != last_end.get("reason"):
                    errs.append(dict(
                        code="EFFECTIVE_CHAIN_end_reason_mismatch",
                        session_end_reason=session.get("end_reason"),
                        transition_reason=last_end.get("reason"),
                        transition_id=last_end.get("transition_id"),
                    ))
                if session.get("ended_at_bar_close_ms") != last_end.get("at_bar_close_ms"):
                    errs.append(dict(
                        code="EFFECTIVE_CHAIN_ended_at_mismatch",
                        session_ended_at=session.get("ended_at_bar_close_ms"),
                        transition_close=last_end.get("at_bar_close_ms"),
                        transition_id=last_end.get("transition_id"),
                    ))

    return errs


def merge_and_validate_effective_chain(
    existing: list[dict],
    pending: list[dict],
    session: dict | None,
) -> tuple[list[dict], list[dict]]:
    """Merge existing facts + pending corrections, build effective chain, validate.

    Returns (effective_ordered, errors). Caller must reject the whole batch if errors.
    """
    combined = list(existing) + list(pending)
    effective = build_effective_transitions(combined)
    errs = validate_effective_chain(effective, session)
    return order_effective_chain(effective), errs
