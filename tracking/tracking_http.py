# -*- coding: utf-8 -*-
"""Bounded /api/tracking/* read routes for the explicit host mount.

NO AI. NO scan/backfill. GET never writes. Runtime context is supplied by host.
"""
from __future__ import annotations

import hashlib
import threading
import json
import urllib.parse
from typing import Any, Callable

from .read_api import TrackingReadAPI, SOURCE_VERSION_TAG

ROUTES_DOC = [
    ("GET", "/api/tracking/summary"),
    ("GET", "/api/tracking/events"),
    ("GET", "/api/tracking/event"),
    ("GET", "/api/tracking/revisions"),
    ("GET", "/api/tracking/transitions"),
    ("GET", "/api/tracking/gaps"),
    ("POST", "/api/tracking/exports"),
    ("GET", "/api/tracking/export"),
    ("POST", "/api/tracking/read_view"),
]


def _json_bytes(obj: Any, status: int = 200) -> tuple[int, bytes, str]:
    return status, json.dumps(obj, ensure_ascii=False, indent=2).encode("utf-8"), "application/json; charset=utf-8"


def _qs(path: str) -> tuple[str, dict]:
    parsed = urllib.parse.urlparse(path)
    q = {k: v[0] if len(v) == 1 else v for k, v in urllib.parse.parse_qs(parsed.query).items()}
    return parsed.path, q


def _export_identity(*, event_id: str, read_view: str, fmt: str, body_bytes: bytes) -> str:
    """Stable, non-colliding export id from full event identity + frozen view + format + content."""
    h = hashlib.sha256()
    h.update(b"v1|")
    h.update(event_id.encode("utf-8"))
    h.update(b"|")
    h.update(read_view.encode("utf-8"))
    h.update(b"|")
    h.update(fmt.encode("utf-8"))
    h.update(b"|")
    h.update(body_bytes)
    return "exp_" + h.hexdigest()[:40]


class TrackingHTTPRouter:
    """Pure router: (method, path, body) -> (status, body_bytes, content_type)."""

    def __init__(self, api: TrackingReadAPI, *, mount_context="isolated"):
        if mount_context not in ("isolated", "runtime"):
            raise ValueError("mount_context")
        self.mount_context = mount_context
        self.api = api
        self._exports: dict[str, dict] = {}
        self._export_lock = threading.RLock()
        self._export_bytes = 0

    def handle(self, method: str, path: str, body: bytes | None = None) -> tuple[int, bytes, str]:
        method = method.upper()
        route, q = _qs(path)
        try:
            if method=='GET' and route in ('/api/tracking/event','/api/tracking/events','/api/tracking/summary','/api/tracking/transitions','/api/tracking/revisions','/api/tracking/gaps') and not q.get('read_view'):
                return _json_bytes(dict(ok=False,errors=[dict(code='READ_VIEW_required')]),400)
            if method == "POST" and route == "/api/tracking/read_view":
                payload = json.loads(body.decode("utf-8") or "{}") if body else {}
                out = self.api.open_read_view(filters=payload.get("filters") or {})
                return _json_bytes(out, 200 if out.get("ok") else 400)

            if method == "GET" and route == "/api/tracking/summary":
                rv = q.get("read_view")
                out = self.api.list_events(read_view=rv, limit=int(q["limit"]) if "limit" in q else 20)
                if out.get("ok"):
                    out = {**out, "items": [
                        {k: it[k] for k in (
                            "market", "symbol", "event_id", "session_id",
                            "original_confirmation", "qualification", "observation",
                            "data_quality", "historical_import",
                        ) if k in it}
                        for it in out.get("items") or []
                    ]}
                return _json_bytes(out, out.get("http_status") or (200 if out.get("ok") else 400))

            if method == "GET" and route == "/api/tracking/events":
                kwargs = {"read_view": q.get("read_view")}
                if "limit" in q:
                    kwargs["limit"] = int(q["limit"])
                if "after_close_ms" in q:
                    kwargs["after_close_ms"] = int(q["after_close_ms"])
                if "after_event_id" in q:
                    kwargs["after_event_id"] = q["after_event_id"]
                out = self.api.list_events(**kwargs)
                return _json_bytes(out, out.get("http_status") or (200 if out.get("ok") else 400))

            if method == "GET" and route == "/api/tracking/event":
                eid = q.get("event_id")
                if not eid:
                    return _json_bytes({"ok": False, "errors": [dict(code="event_id_required")]}, 400)
                out = self.api.get_event(eid, read_view=q.get("read_view"))
                return _json_bytes(out, out.get("http_status") or (200 if out.get("ok") else 400))

            if method == "GET" and route == "/api/tracking/revisions":
                eid = q.get("event_id")
                kwargs = {"read_view": q.get("read_view")}
                if "limit" in q:
                    kwargs["limit"] = int(q["limit"])
                if "after_revision" in q:
                    kwargs["after_revision"] = int(q["after_revision"])
                out = self.api.list_revisions(eid, **kwargs)
                return _json_bytes(out, out.get("http_status") or (200 if out.get("ok") else 400))

            if method == "GET" and route == "/api/tracking/transitions":
                eid = q.get("event_id")
                kwargs = {"read_view": q.get("read_view"), "view": q.get("view") or "effective"}
                if "limit" in q:
                    kwargs["limit"] = int(q["limit"])
                if "after_seq" in q:
                    kwargs["after_seq"] = int(q["after_seq"])
                out = self.api.list_transitions(eid, **kwargs)
                return _json_bytes(out, out.get("http_status") or (200 if out.get("ok") else 400))

            if method == "GET" and route == "/api/tracking/gaps":
                out = self.api.list_gaps(q.get("event_id"), read_view=q.get("read_view"),
                    limit=int(q.get("limit", 20)), after_gap_id=q.get("after_gap_id"))
                return _json_bytes(out, out.get("http_status") or (200 if out.get("ok") else 400))

            if method == "POST" and route == "/api/tracking/exports":
                payload = json.loads(body.decode("utf-8") or "{}") if body else {}
                eid = payload.get("event_id")
                rv = payload.get("read_view")
                fmt = payload.get("fmt") or "json"
                out = self.api.export_common(eid, read_view=rv, fmt=fmt,limit=payload.get("limit",100),after_revision=payload.get("after_revision",0))
                if out.get("ok"):
                    if fmt == "csv":
                        body_bytes = (out.get("csv") or "").encode("utf-8")
                        content_type = "text/csv; charset=utf-8"
                    elif fmt == "report":
                        from .report_download import render
                        body_bytes = render(out)
                        content_type = "text/html; charset=utf-8"
                    else:
                        # freeze static report / json from same DTO bytes
                        frozen = {k: out[k] for k in out if k != "export_id"}
                        body_bytes = json.dumps(frozen, ensure_ascii=False, indent=2).encode("utf-8")
                        content_type = "application/json; charset=utf-8"
                    export_id = _export_identity(
                        event_id=str(eid or ""),
                        read_view=str(out.get("read_view") or rv or ""),
                        fmt=str(fmt),
                        body_bytes=body_bytes,
                    )
                    stored = {
                        "export_id": export_id,
                        "fmt": fmt,
                        "event_id": eid,
                        "read_view": out.get("read_view") or rv,
                        "content_type": content_type,
                        "body_bytes": body_bytes,
                        "out": out,
                    }
                    with self._export_lock:
                        prior=self._exports.get(export_id)
                        if prior is not None:
                            if prior['body_bytes']!=body_bytes or prior['content_type']!=content_type:
                                return _json_bytes(dict(ok=False,errors=[dict(code='EXPORT_id_immutable_conflict')]),409)
                        else:
                            while self._exports and (len(self._exports)>=32 or self._export_bytes+len(body_bytes)>16*1024*1024):
                                old_id=next(iter(self._exports))
                                self._export_bytes-=len(self._exports.pop(old_id)['body_bytes'])
                            if len(body_bytes)>16*1024*1024:return _json_bytes(dict(ok=False,errors=[dict(code='EXPORT_page_required')]),413)
                            self._exports[export_id]=stored;self._export_bytes+=len(body_bytes)
                    out = {**out, "export_id": export_id}
                return _json_bytes(out, out.get("http_status") or (200 if out.get("ok") else 400))

            if method == "GET" and route == "/api/tracking/export":
                export_id = q.get("export_id")
                with self._export_lock:
                    stored = self._exports.get(export_id)
                if stored:
                    return 200, stored["body_bytes"], stored["content_type"]
                # allow on-the-fly by event_id+read_view+fmt (does not mutate saved store)
                eid = q.get("event_id")
                rv = q.get("read_view")
                fmt = q.get("fmt") or "json"
                if not eid or not rv:
                    return _json_bytes({"ok": False, "errors": [dict(code="export_id_or_event_required")]}, 400)
                out = self.api.export_common(eid, read_view=rv, fmt=fmt)
                ctype = "text/csv; charset=utf-8" if fmt == "csv" else "application/json; charset=utf-8"
                if fmt == "csv" and out.get("ok"):
                    return out.get("http_status") or 200, out["csv"].encode("utf-8"), ctype
                if fmt == "report" and out.get("ok"):
                    from .report_download import render
                    return 200, render(out), "text/html; charset=utf-8"
                return _json_bytes(out, out.get("http_status") or (200 if out.get("ok") else 400))

            if method == "GET" and route == "/api/tracking/health":
                return _json_bytes({
                    "ok": True,
                    "isolated": self.mount_context == "isolated",
                    "mount_context": self.mount_context,
                    "SOURCE_VERSION": SOURCE_VERSION_TAG,
                    "routes": ROUTES_DOC,
                })

            return _json_bytes({"ok": False, "errors": [dict(code="NOT_FOUND", path=route)]}, 404)
        except (ValueError, TypeError) as exc:
            return _json_bytes({"ok": False, "errors": [{"code": "REQUEST_invalid", "detail": str(exc)}]}, 400)
        except Exception as exc:
            return _json_bytes({"ok": False, "errors": [dict(code="ROUTER_CRASH",
                                                             error=f"{type(exc).__name__}: {exc}")]}, 500)
