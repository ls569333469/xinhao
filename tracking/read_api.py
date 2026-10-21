# -*- coding: utf-8 -*-
"""C3-a bounded read_view / keyset pagination / common export DTO.

- Uses transaction-maintained summary/effective projections (SQL filter + keyset + LIMIT+1).
- read_view binds tracking_commit_seq + scan_generation + profile/schema + filter hash.
- Writer/read split: prefer TrackingReadRepository; optional writer ledger only for tests.
- CSV/JSON/report share one DTO; status fields written into CSV body.
- Proposed /api/tracking/* live in tracking_http.py — NO production mount.
"""
from __future__ import annotations

import csv
import hashlib
import io
import json
import threading
from dataclasses import dataclass
from typing import Any
from functools import wraps

from .effective_chain import build_effective_transitions
from .read_repository import TrackingReadRepository, ReadRepositoryError

SOURCE_VERSION_TAG = "codex-abc-tracking-20261019-v3"
READ_SCHEMA_VERSION = "tracking-read-dto-v2"


def _sha(obj: Any) -> str:
    blob = json.dumps(obj, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def read_snapshot(method):
    @wraps(method)
    def wrapped(self, *args, **kwargs):
        try:
            with self.repo.snapshot():
                return method(self, *args, **kwargs)
        except ReadRepositoryError as exc:
            return {"ok": False, "http_status": 409, "errors": [exc.as_dict()]}
    return wrapped


@dataclass
class Page:
    items: list[dict]
    next_cursor: dict | None
    limit: int
    read_view: str
    total_hint: int | None = None


class TrackingReadAPI:
    """Bounded reads. Never writes. Invalid/stale read_view → 409 semantics via error dict."""

    MAX_LIMIT = 100

    @staticmethod
    def effective_transitions(transitions: list[dict]) -> list[dict]:
        """Shared effective-chain projection (same builder as write gate)."""
        return build_effective_transitions(transitions)

    def __init__(
        self,
        repo_or_ledger: Any = None,
        *,
        repo: TrackingReadRepository | None = None,
        ledger: Any = None,
        scan_generation: str = "isolated-scan-generation",
        profile_version: str = "tracking-observation-v1.1.0",
    ):
        if repo_or_ledger is not None:
            if isinstance(repo_or_ledger, TrackingReadRepository):
                repo = repo_or_ledger
            else:
                ledger = repo_or_ledger
        if repo is None and ledger is None:
            raise ReadRepositoryError("READ_API_requires_repo_or_ledger")
        self.repo = repo or TrackingReadRepository(ledger.path)
        self.ledger = ledger
        self.scan_generation = scan_generation
        self.profile_version = profile_version
        self._view_store: dict[str, dict] = {}
        self._view_lock=threading.RLock()
        if self.repo is not None:
            # Fail closed on construct if schema incompatible.
            _ = self.repo.tracking_commit_seq()

    def _q(self, sql: str, args: tuple = ()) -> list:
        if self.repo is not None:
            return self.repo.read(sql, args)
        return self.ledger._read(sql, args)

    def _commit_seq(self) -> int:
        if self.repo is not None:
            return self.repo.tracking_commit_seq()
        try:
            row = self._q("SELECT COALESCE(MAX(commit_seq), 0) AS s FROM tracking_commits")
            return int(row[0]["s"]) if row else 0
        except Exception as exc:
            raise ReadRepositoryError("READ_SCHEMA_incompatible", error=str(exc)) from exc

    @read_snapshot
    def open_read_view(self, *, filters: dict | None = None, sort: str = "decision_close_ms,event_id") -> dict:
        """Bind opaque read_view to seq + scan generation + profile + filter hash."""
        if filters is None:
            filters = {}
        if not isinstance(filters, dict):
            return {"ok": False, "http_status": 400, "errors": [dict(code="FILTERS_must_be_object")]}
        allowed={'market','symbol','strategy','observation_status','qualification_status','technical_status','history'}
        if set(filters)-allowed or any(not isinstance(v,str) or len(v)>80 for v in filters.values()):
            return dict(ok=False,http_status=400,errors=[dict(code='FILTERS_invalid')])
        enums={'market':{'USDT-M','COIN-M'},'strategy':{'A','B','C'},'observation_status':{'active','observing','paused_awaiting_data','ended'},'qualification_status':{'pass','fail','unknown'},'history':{'history','forward'}}
        if any(v and k in enums and v not in enums[k] for k,v in filters.items()) or sort!='decision_close_ms,event_id':
            return dict(ok=False,http_status=400,errors=[dict(code='FILTERS_or_sort_invalid')])
        try:
            seq = self._commit_seq()
        except ReadRepositoryError as e:
            return {"ok": False, "http_status": 409, "errors": [e.as_dict()]}
        payload = {
            "tracking_commit_seq": seq,
            "recovery_commit_seq": self.repo.recovery_commit_seq(),
            "scan_generation": self.scan_generation,
            "profile_version": self.profile_version,
            "read_schema": READ_SCHEMA_VERSION,
            "filters": filters,
            "sort": sort,
        }
        token = "rv_" + _sha(payload)[:32]
        with self._view_lock:
            if token not in self._view_store and len(self._view_store) >= 256:
                del self._view_store[next(iter(self._view_store))]
            self._view_store[token] = payload
        return {"ok": True, "read_view": token, **payload, "SOURCE_VERSION": SOURCE_VERSION_TAG}

    def _require_view(self, read_view: str | None) -> dict:
        if not read_view or not isinstance(read_view, str):
            return {"ok": False, "http_status": 400, "errors": [dict(code="READ_VIEW_required")]}
        with self._view_lock:
            meta = self._view_store.get(read_view)
        if meta is None:
            return {"ok": False, "http_status": 410, "errors": [dict(code="READ_VIEW_unknown")]}
        # Bound generation / profile must still match the API process binding.
        if meta.get("scan_generation") != self.scan_generation or meta.get("profile_version") != self.profile_version:
            return {
                "ok": False,
                "http_status": 409,
                "errors": [dict(
                    code="READ_VIEW_generation_or_profile_changed",
                    bound_generation=meta.get("scan_generation"),
                    current_generation=self.scan_generation,
                    bound_profile=meta.get("profile_version"),
                    current_profile=self.profile_version,
                )],
            }
        if meta.get("read_schema") != READ_SCHEMA_VERSION:
            return {
                "ok": False,
                "http_status": 409,
                "errors": [dict(code="READ_VIEW_schema_changed",
                                bound=meta.get("read_schema"), current=READ_SCHEMA_VERSION)],
            }
        try:
            current = self._commit_seq()
        except ReadRepositoryError as e:
            return {"ok": False, "http_status": 409, "errors": [e.as_dict()]}
        # Stale if seq already moved before this call. Mid-request races are handled by
        # querying projections at the bound seq (new rows with higher valid_from stay out).
        recovery_seq=self.repo.recovery_commit_seq()
        if current != meta["tracking_commit_seq"] or recovery_seq != meta.get('recovery_commit_seq',0):
            return {
                "ok": False,
                "http_status": 409,
                "errors": [dict(
                    code="READ_VIEW_stale",
                    bound_seq=meta["tracking_commit_seq"],
                    current_seq=current,
                    bound_recovery_seq=meta.get('recovery_commit_seq',0),current_recovery_seq=recovery_seq,
                )],
            }
        return {"ok": True, "meta": meta, "read_view": read_view, "current_seq": current}

    def _clamp(self, limit: int | None) -> int:
        if limit is None:
            return 20
        if isinstance(limit, bool) or not isinstance(limit, (int, str)):
            raise ReadRepositoryError("LIMIT_invalid", limit=limit)
        try:
            n = int(limit)
        except (TypeError, ValueError):
            raise ReadRepositoryError("LIMIT_invalid", limit=limit)
        if n < 1 or n > self.MAX_LIMIT:
            raise ReadRepositoryError("LIMIT_out_of_range", limit=n, max=self.MAX_LIMIT)
        return n

    def _summary_row_to_dto(self, row, *, bound_seq: int) -> dict:
        summary = json.loads(row["summary_json"])
        obs = dict(summary.get("observation") or {})
        # normalize profile field
        if obs.get("profile_id") is None and summary.get("tracking_profile_id"):
            obs["profile_id"] = summary["tracking_profile_id"]
        dq = dict(summary.get("data_quality") or {})
        dq.setdefault("missing_data_flags", [])
        dq.setdefault("open_gap_count", dq.get("open_gap_count") or 0)
        from .recovery_quality import snapshot as recovery_snapshot
        recovery=recovery_snapshot(self.repo,market=summary.get('market') or row['market'],symbol=summary.get('symbol') or row['symbol'],event_id=summary.get('event_id') or row['event_id'])
        return {
            "market": summary.get("market") or row["market"],
            "symbol": summary.get("symbol") or row["symbol"],
            "strategy": summary.get("strategy") or "A",
            "event_id": summary.get("event_id") or row["event_id"],
            "session_id": summary.get("session_id") or row["session_id"],
            "strategy": summary.get("strategy"),
            "context": summary.get("context") or {},
            "decision_close_ms": summary.get("decision_close_ms") if summary.get("decision_close_ms") is not None else row["decision_close_ms"],
            "original_confirmation": summary.get("original_confirmation") or {"technical_status": None},
            "qualification": summary.get("qualification") or {"status": None},
            "observation": obs,
            "data_quality": {
                "summary": dq.get("summary") or "ok",
                "missing_data_flags": dq.get("missing_data_flags") or [],
                "open_gap_count": int(dq.get("open_gap_count") or 0),
                "recovery": recovery,
            },
            "historical_import": bool(summary.get("historical_import")),
            "revision_count": summary.get("revision_count"),
            "evidence_hash": summary.get("evidence_hash"),
            "source_version_tag": SOURCE_VERSION_TAG,
            "_bound_seq": bound_seq,
        }

    def event_summary_dto(self, event_id: str, *, bound_seq: int | None = None) -> dict | None:
        if bound_seq is None:
            try:
                bound_seq = self._commit_seq()
            except ReadRepositoryError:
                bound_seq = 0
        # Prefer projection at bound_seq
        rows = self._q(
            "SELECT event_id, session_id, market, symbol, decision_close_ms, summary_json, "
            "valid_from_seq, valid_to_seq FROM tracking_summary_versions "
            "WHERE event_id=? AND valid_from_seq<=? AND (valid_to_seq IS NULL OR valid_to_seq>?) "
            "ORDER BY valid_from_seq DESC LIMIT 1",
            (event_id, bound_seq, bound_seq),
        )
        if rows:
            return self._summary_row_to_dto(rows[0], bound_seq=bound_seq)
        return None





    @read_snapshot
    def list_events(self, *, read_view: str | None = None, limit: int | None = None,
                    after_close_ms: int | None = None, after_event_id: str | None = None,
                    cursor: str | None = None) -> dict:
        if read_view is None:
            opened = self.open_read_view(filters={})
            if not opened.get("ok"):
                return opened
            read_view = opened["read_view"]
        if cursor and after_event_id is None:
            after_event_id = cursor
            if after_close_ms is None:
                after_close_ms = -1
        vr = self._require_view(read_view)
        if not vr.get("ok"):
            return vr
        try:
            lim = self._clamp(limit)
        except ReadRepositoryError as e:
            return {"ok": False, "http_status": 400, "errors": [e.as_dict()]}
        bound = int(vr["meta"]["tracking_commit_seq"])
        filt = vr["meta"].get("filters") or {}
        where = [
            "valid_from_seq<=?",
            "valid_to_seq IS NULL",
        ]
        # _require_view pins the current seq within this read transaction.
        # Older revisions remain reconstructible through explicit history APIs.
        args: list[Any] = [bound]
        if filt.get("market"):
            where.append("market=?")
            args.append(filt["market"])
        if filt.get("symbol"):
            where.append("symbol=?")
            args.append(filt["symbol"])
        if filt.get('observation_status')=='active':
            where.append("json_extract(summary_json,'$.observation.status') IN ('observing','paused_awaiting_data')")
        if filt.get('history') in ('history','forward'):
            where.append("json_extract(summary_json,'$.historical_import')=?");args.append(1 if filt['history']=='history' else 0)
        for field, json_path in (("strategy", "$.strategy"), ("observation_status", "$.observation.status"), ("qualification_status", "$.qualification.status")):
            if filt.get(field) and not (field=="observation_status" and filt[field]=="active"):
                where.append("json_extract(summary_json, '"+json_path+"') = ?")
                args.append(filt[field])
        if filt.get("technical_status"):
            # JSON extract in SQLite
            where.append("json_extract(summary_json, '$.original_confirmation.technical_status')=?")
            args.append(filt["technical_status"])
        if after_close_ms is not None or after_event_id is not None:
            if after_close_ms is None or after_event_id is None:
                return {"ok": False, "http_status": 400,
                        "errors": [dict(code="CURSOR_incomplete",
                                        need=["after_close_ms", "after_event_id"])]}
            where.append(
                "(COALESCE(decision_close_ms,0) > ? OR "
                "(COALESCE(decision_close_ms,0)=? AND event_id > ?))"
            )
            args.extend([after_close_ms, after_close_ms, after_event_id])
        sql = (
            "SELECT event_id, session_id, market, symbol, decision_close_ms, summary_json, "
            "valid_from_seq, valid_to_seq FROM tracking_summary_versions WHERE "
            + " AND ".join(where)
            + " ORDER BY COALESCE(decision_close_ms,0) ASC, event_id ASC LIMIT ?"
        )
        args.append(lim + 1)
        rows = self._q(sql, tuple(args))
        items = [self._summary_row_to_dto(r, bound_seq=bound) for r in rows]
        # Drop internal fields from response
        for it in items:
            it.pop("_bound_seq", None)
            if it.get("revision_count") is None:
                # leave None; header path fills when needed
                pass
        window = items
        page = window[:lim]
        nxt = None
        if len(window) > lim and page:
            last = page[-1]
            nxt = {"after_close_ms": last.get("decision_close_ms"), "after_event_id": last["event_id"]}
        # Re-check generation/profile after data read (race with process-level rebinding).
        if vr["meta"].get("scan_generation") != self.scan_generation or vr["meta"].get("profile_version") != self.profile_version:
            return {
                "ok": False,
                "http_status": 409,
                "errors": [dict(code="READ_VIEW_generation_or_profile_changed")],
            }
        return {
            "ok": True,
            "read_view": read_view,
            "tracking_commit_seq": bound,
            "scan_generation": vr["meta"]["scan_generation"],
            "profile_version": vr["meta"]["profile_version"],
            "items": page,
            "next_cursor": nxt,
            "limit": lim,
            "SOURCE_VERSION": SOURCE_VERSION_TAG,
        }

    @read_snapshot
    def get_event(self, event_id: str, *, read_view: str | None = None) -> dict:
        if read_view is None:
            return {"ok":False,"http_status":400,"errors":[{"code":"READ_VIEW_required"}]}
        vr = self._require_view(read_view)
        if not vr.get("ok"):
            return vr
        bound = int(vr["meta"]["tracking_commit_seq"])
        dto = self.event_summary_dto(event_id, bound_seq=bound)
        if dto is None:
            return {"ok": False, "http_status": 404, "errors": [dict(code="EVENT_not_found", event_id=event_id)]}
        # Header-only path: prefer projection; bounded effective count from projection table
        eff_rows = self._q(
            "SELECT COUNT(*) AS c FROM tracking_effective_members "
            "WHERE event_id=? AND valid_from_seq<=? AND (valid_to_seq IS NULL OR valid_to_seq>?)",
            (event_id, bound, bound),
        )
        effective_count = int(eff_rows[0]["c"]) if eff_rows else 0
        rev_count = dto.get("revision_count")
        if rev_count is None:
            rev_count = int(self._q("SELECT COUNT(*) AS c FROM event_revisions WHERE event_id=?", (event_id,))[0]["c"])
        return {
            "ok": True,
            "read_view": read_view,
            "header": dto,
            "revision_count": rev_count,
            "transition_count": effective_count,
            "effective_count": effective_count,
            "entries": {
                "revisions": f"/api/tracking/revisions?event_id={event_id}",
                "transitions": f"/api/tracking/transitions?event_id={event_id}&view=effective",
                "gaps": f"/api/tracking/gaps?event_id={event_id}",
            },
            "SOURCE_VERSION": SOURCE_VERSION_TAG,
        }

    @read_snapshot
    def list_transitions(self, event_id: str, *, read_view: str, view: str = "effective",
                         limit: int | None = None, after_seq: int | None = None) -> dict:
        vr = self._require_view(read_view)
        if not vr.get("ok"):
            return vr
        if view not in ("raw", "effective"):
            return {"ok": False, "http_status": 400, "errors": [dict(code="VIEW_invalid", view=view)]}
        try:
            lim = self._clamp(limit)
        except ReadRepositoryError as e:
            return {"ok": False, "http_status": 400, "errors": [e.as_dict()]}
        bound = int(vr["meta"]["tracking_commit_seq"])
        if view == "effective":
            where = [
                "event_id=?",
                "valid_from_seq<=?",
                "(valid_to_seq IS NULL OR valid_to_seq>?)",
            ]
            args: list[Any] = [event_id, bound, bound]
            if after_seq is not None:
                if not isinstance(after_seq, int) or after_seq < 0:
                    return {"ok": False, "http_status": 400, "errors": [dict(code="CURSOR_invalid")]}
                where.append("page_seq>?")
                args.append(after_seq)
            sql = (
                "SELECT page_seq, payload_json FROM tracking_effective_members WHERE "
                + " AND ".join(where)
                + " ORDER BY page_seq ASC LIMIT ?"
            )
            args.append(lim + 1)
            rows = self._q(sql, tuple(args))
            annotated = []
            for r in rows:
                t = json.loads(r["payload_json"])
                annotated.append({**t, "_page_seq": int(r["page_seq"])})
            # full chain length at bound
            cnt = self._q(
                "SELECT COUNT(*) AS c FROM tracking_effective_members "
                "WHERE event_id=? AND valid_from_seq<=? AND (valid_to_seq IS NULL OR valid_to_seq>?)",
                (event_id, bound, bound),
            )
            full_len = int(cnt[0]["c"]) if cnt else 0
        else:
            where = "event_id=?"
            args = [event_id]
            if after_seq is not None:
                if not isinstance(after_seq, int) or isinstance(after_seq, bool) or after_seq < 0:
                    return {"ok": False, "http_status": 400, "errors": [dict(code="CURSOR_invalid")]}
                where += " AND rowid>?"
                args.append(after_seq)
            args.append(lim + 1)
            rows = self._q("SELECT rowid AS raw_seq, payload_json FROM session_transitions WHERE " + where + " ORDER BY rowid LIMIT ?", tuple(args))
            annotated = [{**json.loads(row["payload_json"]), "_page_seq": row["raw_seq"]} for row in rows]
            full_len = int(self._q("SELECT COUNT(*) AS c FROM session_transitions WHERE event_id=?", (event_id,))[0]["c"])
        window = annotated
        page = window[:lim]
        nxt = page[-1]["_page_seq"] if len(window) > lim and page else None
        return {
            "ok": True,
            "read_view": read_view,
            "event_id": event_id,
            "view": view,
            "items": page,
            "next_cursor": {"after_seq": nxt} if nxt is not None else None,
            "limit": lim,
            "full_chain_len": full_len,
            "SOURCE_VERSION": SOURCE_VERSION_TAG,
        }

    @read_snapshot
    def list_revisions(self, event_id: str, *, read_view: str, limit: int | None = None,
                       after_revision: int | None = None) -> dict:
        vr = self._require_view(read_view)
        if not vr.get("ok"):
            return vr
        try:
            lim = self._clamp(limit)
        except ReadRepositoryError as e:
            return {"ok": False, "http_status": 400, "errors": [e.as_dict()]}
        where = ["event_id=?"]
        args: list[Any] = [event_id]
        if after_revision is not None:
            if not isinstance(after_revision, int) or isinstance(after_revision, bool) or after_revision < 0:
                return {"ok": False, "http_status": 400, "errors": [dict(code="CURSOR_invalid")]}
            where.append("revision>?")
            args.append(int(after_revision))
        sql = (
            "SELECT payload_json, revision FROM event_revisions WHERE "
            + " AND ".join(where)
            + " ORDER BY revision ASC LIMIT ?"
        )
        args.append(lim + 1)
        rows = self._q(sql, tuple(args))
        revs = [json.loads(r["payload_json"]) for r in rows]
        window = revs
        page = window[:lim]
        nxt = page[-1].get("revision") if len(window) > lim and page else None
        return {
            "ok": True, "read_view": read_view, "event_id": event_id,
            "items": page, "next_cursor": {"after_revision": nxt} if nxt is not None else None,
            "limit": lim, "SOURCE_VERSION": SOURCE_VERSION_TAG,
        }

    @read_snapshot
    def list_gaps(self, event_id: str, *, read_view: str, limit: int | None = None,
                  after_gap_id: str | None = None) -> dict:
        vr = self._require_view(read_view)
        if not vr.get("ok"):
            return vr
        lim = self._clamp(limit)
        dto = self.event_summary_dto(event_id, bound_seq=int(vr["meta"]["tracking_commit_seq"]))
        if not dto or not dto.get("session_id"):
            return {"ok": True, "read_view": read_view, "items": [], "next_cursor": None, "SOURCE_VERSION": SOURCE_VERSION_TAG}
        rows = self._q("SELECT gap_id, scope, status, detail_json FROM gaps WHERE session_id=? AND gap_id>? ORDER BY gap_id LIMIT ?",
                       (dto["session_id"], after_gap_id or "", lim + 1))
        items = [dict(gap_id=row["gap_id"], scope=row["scope"], status=row["status"], detail=json.loads(row["detail_json"])) for row in rows[:lim]]
        return {"ok": True, "read_view": read_view, "items": items,
                "next_cursor": {"after_gap_id": items[-1]["gap_id"]} if len(rows) > lim else None,
                "limit": lim, "SOURCE_VERSION": SOURCE_VERSION_TAG}

    @read_snapshot
    def export_dto(self, event_id: str, *, read_view: str, limit: int = 100, after_revision: int = 0) -> dict:
        vr = self._require_view(read_view)
        if not vr.get("ok"):
            return vr
        bound = int(vr["meta"]["tracking_commit_seq"])
        header = self.event_summary_dto(event_id, bound_seq=bound)
        if header is None:
            return {"ok": False, "http_status": 404, "errors": [dict(code="EVENT_not_found", event_id=event_id)]}
        rows = []
        page=self.list_revisions(event_id,read_view=read_view,limit=limit,after_revision=after_revision)
        if not page.get('ok'):return page
        for rev in page['items']:
            rows.append({
                "read_view": read_view,
                "export_schema": READ_SCHEMA_VERSION,
                "profile_version": vr["meta"]["profile_version"],
                "scan_generation": vr["meta"]["scan_generation"],
                "tracking_commit_seq": bound,
                "market": header["market"],
                "symbol": header["symbol"],
                "event_id": event_id,
                "session_id": header.get("session_id"),
                "revision": rev.get("revision"),
                "technical_status": rev.get("technical_status"),
                "qualification_status": rev.get("qualification_status"),
                "observation_status": (header.get("observation") or {}).get("status"),
                "end_reason": (header.get("observation") or {}).get("end_reason"),
                "last_evaluated_bar_close_ms": (header.get("observation") or {}).get("last_evaluated_bar_close_ms"),
                "data_quality_summary": (header.get("data_quality") or {}).get("summary"),
                "recovery_commit_seq": vr['meta'].get('recovery_commit_seq',0),
                "recovery_status": header['data_quality']['recovery']['status'],
                "recovery_as_of_ms": header['data_quality']['recovery'].get('as_of_ms'),
                "recovery_quality_json": json.dumps(header['data_quality']['recovery'],ensure_ascii=False,sort_keys=True,separators=(',',':')),
                "open_gap_count": (header.get("data_quality") or {}).get("open_gap_count"),
                "historical_import": header.get("historical_import"),
                "evidence_hash": rev.get("evidence_hash"),
                "post_expiry_progress_json": json.dumps((header.get("context") or {}).get("expired_followup"),ensure_ascii=False,sort_keys=True,separators=(",",":")),
                "source_version_tag": SOURCE_VERSION_TAG,
            })
        return {
            "ok": True,
            "read_view": read_view,
            "header": header,
            "rows": rows,
            "next_cursor": page["next_cursor"],
            "limit": page["limit"],
            "SOURCE_VERSION": SOURCE_VERSION_TAG,
        }

    @read_snapshot
    def export_common(self, event_id: str, *, read_view: str | None = None, fmt: str = "json", limit: int = 100, after_revision: int = 0) -> dict:
        if read_view is None:
            opened = self.open_read_view(filters={})
            read_view = opened["read_view"]
        dto = self.export_dto(event_id, read_view=read_view,limit=limit,after_revision=after_revision)
        if not dto.get("ok"):
            return dto
        if fmt == "json":
            return {**dto, "fmt": "json"}
        if fmt == "csv":
            rows = dto["rows"]
            buf = io.StringIO()
            if rows:
                fieldnames = list(rows[0].keys())
                w = csv.DictWriter(buf, fieldnames=fieldnames)
                w.writeheader()
                w.writerows(rows)
            return {
                "ok": True, "fmt": "csv", "csv": buf.getvalue(),
                "read_view": read_view, "header": dto["header"], "next_cursor": dto["next_cursor"], "limit": dto["limit"],
                "SOURCE_VERSION": SOURCE_VERSION_TAG,
            }
        if fmt == "report":
            return {
                "ok": True, "fmt": "report",
                "title": f"ABC tracking report {event_id}",
                "read_view": read_view,
                "body": dto,
                "SOURCE_VERSION": SOURCE_VERSION_TAG,
                "ai_invoked": False,
            }
        return {"ok": False, "http_status": 400, "errors": [dict(code="FMT_invalid", fmt=fmt)]}

    def list_sessions(self, *, limit: int | None = None, cursor: str | None = None,
                      status: str | None = None) -> Page:
        try:
            lim = self._clamp(limit)
        except ReadRepositoryError:
            lim = self.MAX_LIMIT
        where='session_id>?'; args=[cursor or '']
        if status:where+=' AND observation_status=?';args.append(status)
        rows=self._q('SELECT payload_json FROM sessions WHERE '+where+' ORDER BY session_id LIMIT ?',tuple(args+[lim+1]))
        items=[json.loads(r['payload_json']) for r in rows[:lim]]
        nxt=items[-1]['session_id'] if len(rows)>lim else None
        return Page(items=items,next_cursor=nxt,limit=lim,read_view='legacy',total_hint=None)
