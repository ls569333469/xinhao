# -*- coding: utf-8 -*-
"""C2-b source dispatch: receipt ACK vs effect ACK, per-source watermarks, recovery.

Formal state lives in TrackingLedger SQLite (source_receipts / source_jobs /
source_watermarks). This module orchestrates accept -> fanout jobs -> per-candle
effect commit with watermark advance in the SAME transaction as the effect ACK.

Recovery from A's immutable archives uses unique message_key + re-read +
reconciliation. We explicitly do NOT claim file/SQLite cross-store atomicity.

Bounded reads: page/continuation only; full verify_envelope re-read cost is
registered as a C3 carry-forward (see evidence/conditions-carry-forward.json).
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from .ledger import TrackingLedger, LedgerReject, _canonical, _sha256_text, timeframe_to_ms

SOURCE_VERSION_TAG = "codex-abc-tracking-20261019-v3"
H4_MS = 4 * 60 * 60 * 1000


def message_key_for(envelope: dict) -> str:
    return TrackingLedger.build_message_key(
        source=envelope["source"],
        source_ref=envelope["source_ref"],
        version=envelope["version"],
        payload_hash=str(envelope["payload_hash"]).lower(),
    )


def version_set_hash(versions: Iterable[str]) -> str:
    blob = _canonical(sorted({str(v) for v in versions}))
    return _sha256_text(blob)


@dataclass(frozen=True)
class CandleStep:
    """One closed candle the consumer intends to evaluate."""
    close_ms: int
    present: bool  # False => explicit gap at this close


class SourceDispatch:
    """Orchestrates receipt/job/watermark flow on an isolated TrackingLedger."""

    def __init__(self, ledger: TrackingLedger):
        self.ledger = ledger

    def accept_page(
        self,
        envelopes: list[dict],
        *,
        consumer_jobs: list[dict] | None = None,
        crash_after: str | None = None,
    ) -> dict:
        """Accept verified envelopes; create jobs. Receipt ACK per envelope.

        consumer_jobs items: {consumer_key, session_id?, next_close_ms?, job_key?}
        applied to EACH envelope (fanout). Partial fanout crash leaves receipt
        in fanout_pending so restart can finish without double effects.
        """
        results = []
        for env in envelopes:
            jobs = None
            if consumer_jobs:
                jobs = [
                    {
                        "consumer_key": j["consumer_key"],
                        "session_id": j.get("session_id"),
                        "next_close_ms": j.get("next_close_ms"),
                        "job_key": j.get("job_key"),
                        "replay_plan_id": j.get("replay_plan_id"),
                    }
                    for j in consumer_jobs
                ]
            r = self.ledger.accept_source_receipt(
                env, jobs=jobs, mark_dispatched=bool(jobs), crash_after=crash_after,
            )
            results.append(r)
            if r.get("crashed"):
                return {"ok": False, "crashed": True, "results": results}
            if not r.get("ok") and not r.get("idempotent"):
                # conflict still recorded; continue collecting
                pass
        all_ok = all(x.get("ok") for x in results)
        return {"ok": all_ok, "results": results, "SOURCE_VERSION": SOURCE_VERSION_TAG}

    def build_candle_effect_spec(
        self,
        job_key: str,
        *,
        step: CandleStep,
        watermark_identity: dict,
        fetched_max_ms: int | None = None,
        requested_end_ms: int | None = None,
        quality: dict | None = None,
        effect: dict | None = None,
    ) -> dict:
        """Assemble job_effect companion for a same-TX ledger commit (no write yet)."""
        if not isinstance(step.close_ms, int) or isinstance(step.close_ms, bool) or step.close_ms < 0:
            return {"ok": False, "errors": [dict(code="CANDLE_illegal_close_ms")]}
        rows = self.ledger._read('SELECT * FROM source_jobs WHERE job_key=?', (job_key,))
        job = dict(rows[0]) if rows else None
        if job is None:
            return {"ok": False, "errors": [dict(code="JOB_not_found", job_key=job_key)]}
        if job.get("state") == "effect_acked" and step.present:
            return {
                "ok": True,
                "job_effect": {
                    "job_key": job_key,
                    "effect": effect or {"kind": "evaluated", "close_ms": step.close_ms},
                    "watermark": None,
                    "gap_pause": False,
                },
            }
        prev = self.ledger.get_source_watermark(**watermark_identity)
        fetched = fetched_max_ms
        if fetched is None:
            fetched = step.close_ms if step.present else (prev or {}).get("fetched_max_ms")
            if fetched is None and not step.present:
                fetched = step.close_ms
        requested = requested_end_ms if requested_end_ms is not None else (prev or {}).get("requested_end_ms")
        q = dict(quality or (prev or {}).get("quality") or {})
        first_gap = (prev or {}).get("first_gap_ms")
        expected = None
        if first_gap is not None:
            expected = first_gap
        else:
            prefix_next = None
            if prev is not None and prev.get("continuous_end_ms") is not None:
                bar = timeframe_to_ms(str(watermark_identity.get("timeframe") or job.get("timeframe") or ""))
                if bar is not None:
                    prefix_next = int(prev["continuous_end_ms"]) + bar
            job_next = job.get("next_close_ms")
            if job_next is not None and prefix_next is not None:
                expected = job_next if job_next <= prefix_next else prefix_next
            elif prefix_next is not None:
                expected = prefix_next
            elif job_next is not None:
                expected = job_next
        if expected is not None and step.close_ms != expected:
            return {
                "ok": False,
                "errors": [dict(
                    code="CANDLE_skip_unprocessed_root",
                    expected_next_ms=expected,
                    got_close_ms=step.close_ms,
                    job_key=job_key,
                )],
            }
        if not step.present:
            q = dict(q, gap=True, first_gap_ms=step.close_ms)
            patch = {
                **watermark_identity,
                "requested_end_ms": requested,
                "fetched_max_ms": fetched,
                "continuous_end_ms": (prev or {}).get("continuous_end_ms"),
                "evaluated_end_ms": (prev or {}).get("evaluated_end_ms"),
                "first_gap_ms": step.close_ms,
                "quality": q,
            }
            return {
                "ok": True,
                "job_effect": {
                    "job_key": job_key,
                    "effect": effect or {"kind": "gap", "close_ms": step.close_ms},
                    "watermark": patch,
                    "gap_pause": True,
                },
            }
        continuous = step.close_ms
        evaluated = step.close_ms
        out_gap = first_gap
        if first_gap is not None:
            if step.close_ms != first_gap:
                return {
                    "ok": False,
                    "errors": [dict(
                        code="CANDLE_skip_first_gap",
                        first_gap_ms=first_gap,
                        got_close_ms=step.close_ms,
                    )],
                }
            out_gap = None
            q = dict(q, gap=False)
            q.pop("first_gap_ms", None)
        patch = {
            **watermark_identity,
            "requested_end_ms": requested,
            "fetched_max_ms": max(fetched, step.close_ms) if fetched is not None else step.close_ms,
            "continuous_end_ms": continuous,
            "evaluated_end_ms": evaluated,
            "first_gap_ms": out_gap,
            "quality": q,
        }
        return {
            "ok": True,
            "job_effect": {
                "job_key": job_key,
                "effect": effect or {"kind": "evaluated", "close_ms": step.close_ms},
                "watermark": patch,
                "gap_pause": False,
            },
        }

    def process_candle_job(
        self,
        job_key: str,
        *,
        step: CandleStep,
        watermark_identity: dict,
        fetched_max_ms: int | None = None,
        requested_end_ms: int | None = None,
        quality: dict | None = None,
        effect: dict | None = None,
        crash_after: str | None = None,
    ) -> dict:
        """Per-candle recovery step (F1 contiguous consumption).

        - present candle: only the expected next root (first_gap > continuous_end+bar > job.next_close);
          continuous/evaluated = that close_ms (same tx as effect ACK)
        - missing candle: gap_pause at exactly the expected next root; do NOT advance
          continuous/evaluated past the gap (even if fetched_max is higher)
        """
        if not isinstance(step.close_ms, int) or isinstance(step.close_ms, bool) or step.close_ms < 0:
            return {"ok": False, "errors": [dict(code="CANDLE_illegal_close_ms")]}
        rows = self.ledger._read('SELECT * FROM source_jobs WHERE job_key=?', (job_key,))
        job = dict(rows[0]) if rows else None
        if job is None:
            return {"ok": False, "errors": [dict(code="JOB_not_found", job_key=job_key)]}
        # Idempotent replay: already effect_acked → ledger path (no skip-ahead recheck)
        if job.get("state") == "effect_acked" and step.present:
            return self.ledger.commit_job_effect(
                job_key, effect=effect or {"kind": "evaluated", "close_ms": step.close_ms},
                watermark=None, gap_pause=False, crash_after=crash_after,
            )
        # Read watermark by caller identity for patch assembly; commit_job_effect re-binds.
        prev = self.ledger.get_source_watermark(**watermark_identity)
        fetched = fetched_max_ms
        if fetched is None:
            fetched = step.close_ms if step.present else (prev or {}).get("fetched_max_ms")
            if fetched is None and not step.present:
                fetched = step.close_ms
        requested = requested_end_ms if requested_end_ms is not None else (prev or {}).get("requested_end_ms")
        q = dict(quality or (prev or {}).get("quality") or {})

        first_gap = (prev or {}).get("first_gap_ms")
        # F1: first_gap wins; else derive persisted continuous prefix independently of
        # optional job.next_close_ms (omitting cursor must NOT disable prefix check).
        expected = None
        if first_gap is not None:
            expected = first_gap
        else:
            prefix_next = None
            if prev is not None and prev.get("continuous_end_ms") is not None:
                bar = timeframe_to_ms(str(watermark_identity.get("timeframe") or job.get("timeframe") or ""))
                if bar is not None:
                    prefix_next = int(prev["continuous_end_ms"]) + bar
            job_next = job.get("next_close_ms")
            if job_next is not None and prefix_next is not None:
                expected = job_next if job_next <= prefix_next else prefix_next
            elif prefix_next is not None:
                expected = prefix_next
            elif job_next is not None:
                expected = job_next
        if expected is not None and step.close_ms != expected:
            return {
                "ok": False,
                "errors": [dict(
                    code="CANDLE_skip_unprocessed_root",
                    expected_next_ms=expected,
                    got_close_ms=step.close_ms,
                    job_key=job_key,
                )],
            }

        if not step.present:
            q = dict(q, gap=True, first_gap_ms=step.close_ms)
            continuous = (prev or {}).get("continuous_end_ms")
            evaluated = (prev or {}).get("evaluated_end_ms")
            patch = {
                **watermark_identity,
                "requested_end_ms": requested,
                "fetched_max_ms": fetched,
                "continuous_end_ms": continuous,
                "evaluated_end_ms": evaluated,
                "first_gap_ms": step.close_ms,
                "quality": q,
            }
            return self.ledger.commit_job_effect(
                job_key, effect=effect or {"kind": "gap", "close_ms": step.close_ms},
                watermark=patch, gap_pause=True, crash_after=crash_after,
            )

        # present: exact expected root only; clear gap only when filling that exact root
        continuous = step.close_ms
        evaluated = step.close_ms
        out_gap = first_gap
        if first_gap is not None:
            if step.close_ms != first_gap:
                return {
                    "ok": False,
                    "errors": [dict(
                        code="CANDLE_skip_first_gap",
                        first_gap_ms=first_gap,
                        got_close_ms=step.close_ms,
                    )],
                }
            out_gap = None
            q = dict(q, gap=False)
            q.pop("first_gap_ms", None)
        patch = {
            **watermark_identity,
            "requested_end_ms": requested,
            "fetched_max_ms": max(fetched, step.close_ms) if fetched is not None else step.close_ms,
            "continuous_end_ms": continuous,
            "evaluated_end_ms": evaluated,
            "first_gap_ms": out_gap,
            "quality": q,
        }
        return self.ledger.commit_job_effect(
            job_key, effect=effect or {"kind": "evaluated", "close_ms": step.close_ms},
            watermark=patch, gap_pause=False, crash_after=crash_after,
        )

    def reconcile_archive_keys(
        self,
        *,
        source: str,
        archive_keys: list[dict],
    ) -> dict:
        """Reconciliation against immutable archive listing (re-read, not atomic).

        archive_keys items: {source_ref, version, payload_hash}
        Returns missing (in archive but no receipt), matched, conflicts.
        Does NOT claim file+SQLite atomicity — caller re-accepts missing keys.
        """
        missing, matched, conflicts = [], [], []
        for item in archive_keys:
            try:
                mk = TrackingLedger.build_message_key(
                    source=source,
                    source_ref=item["source_ref"],
                    version=item["version"],
                    payload_hash=str(item["payload_hash"]).lower(),
                )
            except LedgerReject as rej:
                conflicts.append({"item": item, "errors": rej.as_errors()})
                continue
            row = self.ledger.get_source_receipt(mk)
            if row is None:
                missing.append({"message_key": mk, **item})
            elif row["payload_hash"] != str(item["payload_hash"]).lower():
                conflicts.append({
                    "message_key": mk, "have": row["payload_hash"],
                    "got": str(item["payload_hash"]).lower(),
                })
            else:
                matched.append({"message_key": mk, "dispatch_state": row["dispatch_state"],
                                "receipt_acked_at": row["receipt_acked_at"]})
        return {
            "ok": True,
            "source": source,
            "missing_count": len(missing),
            "matched_count": len(matched),
            "conflict_count": len(conflicts),
            "missing": missing,
            "matched": matched,
            "conflicts": conflicts,
            "atomicity_claim": False,
            "recovery_method": "immutable_reread_plus_unique_message_key",
            "SOURCE_VERSION": SOURCE_VERSION_TAG,
        }
