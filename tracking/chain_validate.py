"""S1-R2-C1: revision-chain / historical-context validation for S2 store & import.

Codex S1-r2 condition S1-R2-C1: validate_record only checked the current record and
trusted prior_records as context. Closing path chosen for S2 (audit option 2 + option 1 helper):

  prior_records are defined as "prefix already stored in an append-only trusted
  store that validated each revision on append". External JSON arrays are NEVER
  trusted as chain context until import_validated_prefix validates every record
  and the linkage, then appends through the store gate.

validate_prefix_incremental implements the option-1 algorithm used by the store
on every append and by the import gate.
"""
from __future__ import annotations

import copy
import sys
from typing import Any

from .paths import S1_CONTRACT_ROOT

_JUDGE_DIR = str(S1_CONTRACT_ROOT / "tests")
if _JUDGE_DIR not in sys.path:
    sys.path.insert(0, _JUDGE_DIR)

import s1_contract_judge as judge  # noqa: E402  (frozen S1-r2 copy)


def load_schema():
    return judge.load(S1_CONTRACT_ROOT / "schema" / "tracking-event-schema-v1.json")


def validate_single(record: dict, schema=None, prior_trusted: list | None = None) -> list[dict]:
    """Validate one event record. prior_trusted must already be store-trusted (or empty)."""
    schema = schema or load_schema()
    errors = judge.validate_record("event", record, schema, prior_trusted)
    from .identity_contract import identity_errors
    errors = identity_errors(errors,record)
    # Preserve production USDⓈ-M UUID identity while canonicalizing display market.
    if record.get("strategy") == "B" and record.get("market") == "USDT-M":
        legacy_id = judge.b_legacy_id("USDⓈ-M", record.get("symbol"), record.get("trigger_bar_open_ms"))
        if record.get("event_id") == legacy_id and {"system": "b_accumulation_store.stable_event_id", "id": legacy_id} in (record.get("legacy_refs") or []):
            errors = [e for e in errors if e.get("code") != "E01_b_legacy_event_id_not_kept"]
    return errors


def _link_errors(prev: dict | None, cur: dict, expect_rev: int) -> list[dict]:
    errs: list[dict] = []
    if cur.get("revision") != expect_rev:
        errs.append(dict(code="CHAIN_revision_not_sequential", got=cur.get("revision"), want=expect_rev))
    want_id = f"{cur.get('event_id')}:r{expect_rev}"
    if cur.get("revision_id") != want_id:
        errs.append(dict(code="CHAIN_revision_id_mismatch", got=cur.get("revision_id"), want=want_id))
    if expect_rev == 1:
        if cur.get("prior_revision_id") is not None:
            errs.append(dict(code="CHAIN_r1_must_have_null_prior", got=cur.get("prior_revision_id")))
    else:
        if prev is None:
            errs.append(dict(code="CHAIN_missing_previous"))
        else:
            if prev.get("event_id") != cur.get("event_id"):
                errs.append(dict(code="CHAIN_event_id_drift", prior=prev.get("event_id"), current=cur.get("event_id")))
            if prev.get("revision") != expect_rev - 1:
                errs.append(dict(code="CHAIN_prior_revision_gap", prior=prev.get("revision"), current=cur.get("revision")))
            if cur.get("prior_revision_id") != prev.get("revision_id"):
                errs.append(dict(code="CHAIN_prior_revision_id_unlink", got=cur.get("prior_revision_id"), want=prev.get("revision_id")))
            if prev.get("first_seen_at") != cur.get("first_seen_at"):
                errs.append(dict(code="CHAIN_first_seen_drift", prior=prev.get("first_seen_at"), current=cur.get("first_seen_at")))
    return errs


def validate_prefix_incremental(records: list[dict], schema=None) -> list[dict]:
    """Incrementally validate r1..rN.

    Each record must pass structure+semantics; event_id stable; revision consecutive;
    revision_id/prior_revision_id linked; first_seen preserved. First-confirm causality
    uses only already-validated trusted priors (never raw unverified JSON).
    """
    schema = schema or load_schema()
    if not records:
        return [dict(code="CHAIN_empty_prefix")]
    all_errs: list[dict] = []
    trusted: list[dict] = []
    eid0 = None
    for i, rec in enumerate(records):
        expect_rev = i + 1
        if not isinstance(rec, dict):
            all_errs.append(dict(code="CHAIN_record_not_object", index=i))
            return all_errs
        if eid0 is None:
            eid0 = rec.get("event_id")
        elif rec.get("event_id") != eid0:
            all_errs.append(dict(code="CHAIN_event_id_inconsistent", index=i, got=rec.get("event_id"), want=eid0))
            return all_errs
        link = _link_errors(trusted[-1] if trusted else None, rec, expect_rev)
        if link:
            for e in link:
                e["index"] = i
            all_errs.extend(link)
            return all_errs
        verrs = validate_single(rec, schema, trusted if trusted else None)
        if verrs:
            for e in verrs:
                ee = dict(e) if isinstance(e, dict) else {"code": str(e)}
                ee["index"] = i
                ee["at_revision"] = rec.get("revision")
                all_errs.append(ee)
            return all_errs
        trusted.append(rec)
    return all_errs


def import_validated_prefix(records: list[dict], schema=None) -> tuple[list[dict], list[dict]]:
    """Gate for external historical import. On any error, nothing may be written."""
    errs = validate_prefix_incremental(records, schema)
    if errs:
        return [], errs
    return list(records), []


def mutate_prior_first_confirm_after_discovery(records: list[dict]) -> list[dict]:
    """Illegal: prior confirmed revision has first_confirmed_seen before its confirm input."""
    out = copy.deepcopy(records)
    for r in out[:-1]:
        cf = r.get("confirm")
        if isinstance(cf, dict) and cf.get("first_confirmed_seen_at"):
            r["available_at"] = "2026-10-03T08:01:36.000Z"
            r["revision_recorded_at"] = "2026-10-03T08:01:37.000Z"
            for sv in r.get("source_versions") or []:
                if sv.get("source") == "candles_4h" and sv.get("version") == "v2":
                    sv["available_at"] = "2026-10-03T08:01:36.000Z"
            return out
    raise RuntimeError("no confirmed prior to mutate")


def mutate_prior_event_identity(records: list[dict]) -> list[dict]:
    out = copy.deepcopy(records)
    r1 = out[0]
    r1["event_id"] = str(r1["event_id"]).replace("ABCUSDT", "FOREIGNUSDT")
    r1["revision_id"] = r1["event_id"] + ":r1"
    return out


def mutate_prior_revision_order(records: list[dict]) -> list[dict]:
    out = copy.deepcopy(records)
    out[0]["revision"] = 99
    return out
