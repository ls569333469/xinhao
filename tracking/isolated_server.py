# -*- coding: utf-8 -*-
"""Minimal isolated HTTP host for C3-b A tracking page + /api/tracking/*.

- Binds 127.0.0.1 only (caller picks free port).
- Serves ui/ static files from submission package.
- Injects TrackingReadAPI; never constructs production server.handler_factory.
- No AI / Research / Sim / scan / backfill side effects.
"""
from __future__ import annotations

import argparse
import json
import mimetypes
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from .read_api import TrackingReadAPI
from .tracking_http import TrackingHTTPRouter

SOURCE_VERSION_TAG = "s2-c2c-c3-isolated"


def make_handler(router: TrackingHTTPRouter, static_root: Path, ledger_path: Path | None):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, fmt, *args):
            sys.stderr.write("[isolated] " + (fmt % args) + "\n")

        def _send(self, status: int, body: bytes, ctype: str):
            self.send_response(status)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("X-Isolated-Tracking", "1")
            self.send_header("X-Production-Mount", "0")
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            if self.path.startswith("/api/"):
                status, body, ctype = router.handle("GET", self.path)
                return self._send(status, body, ctype)
            # static
            rel = self.path.split("?", 1)[0]
            if rel.startswith('/tracking/'):
                rel=rel[len('/tracking'):]
            if rel in ("/", "/index.html", "/tracking"):
                rel = "/index.html"
            target = (static_root / rel.lstrip("/")).resolve()
            if not target.is_relative_to(static_root.resolve()):
                return self._send(403, b'{"ok":false,"errors":[{"code":"PATH_ESCAPE"}]}',
                                  "application/json")
            if not target.is_file():
                return self._send(404, b'{"ok":false,"errors":[{"code":"STATIC_NOT_FOUND"}]}',
                                  "application/json")
            data = target.read_bytes()
            ctype = mimetypes.guess_type(str(target))[0] or "application/octet-stream"
            return self._send(200, data, ctype)

        def do_POST(self):
            if not self.path.startswith("/api/"):
                return self._send(405, b'{"ok":false}', "application/json")
            n = int(self.headers.get("Content-Length") or 0)
            if n < 0 or n > 1024 * 1024:
                return self._send(413, b'{"ok":false}', "application/json")
            body = self.rfile.read(n) if n else b"{}"
            status, out, ctype = router.handle("POST", self.path, body)
            return self._send(status, out, ctype)

    return Handler


class IsolatedTrackingServer:
    def __init__(self, api: TrackingReadAPI, static_root: Path, host: str = "127.0.0.1", port: int = 0):
        if host != "127.0.0.1":
            raise ValueError("isolated host must bind loopback")
        self.api = api
        self.router = TrackingHTTPRouter(api)
        self.static_root = Path(static_root)
        self.host = host
        self.port = port
        self._httpd: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None

    def start(self) -> dict:
        handler = make_handler(self.router, self.static_root, None)
        self._httpd = ThreadingHTTPServer((self.host, self.port), handler)
        self.port = self._httpd.server_address[1]
        self._thread = threading.Thread(target=self._httpd.serve_forever, daemon=True)
        self._thread.start()
        return {
            "ok": True,
            "base_url": f"http://{self.host}:{self.port}",
            "production_mount": False,
            "SOURCE_VERSION": SOURCE_VERSION_TAG,
        }

    def stop(self) -> None:
        if self._httpd is not None:
            self._httpd.shutdown()
            self._httpd.server_close()
            self._httpd = None


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Isolated A tracking host (C3-b)")
    p.add_argument("--ledger", required=True, help="path to isolated sqlite")
    p.add_argument("--ui", required=True, help="path to ui/ static root")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8765)
    args = p.parse_args(argv)
    from .read_repository import TrackingReadRepository
    api = TrackingReadAPI(repo=TrackingReadRepository(args.ledger), scan_generation="isolated-demo-gen")
    srv = IsolatedTrackingServer(api, Path(args.ui), host=args.host, port=args.port)
    info = srv.start()
    print(json.dumps(info, ensure_ascii=False))
    try:
        threading.Event().wait()
    except KeyboardInterrupt:
        srv.stop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
