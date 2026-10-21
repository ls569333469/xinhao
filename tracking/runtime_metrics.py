"""Bounded process metrics with an explicit writer and query-only reader."""
import json
import os
import sqlite3
import time
from pathlib import Path


def memory_bytes():
    if os.name == 'nt':
        import ctypes
        from ctypes import wintypes
        class Counters(ctypes.Structure):
            _fields_ = [('cb', wintypes.DWORD), ('faults', wintypes.DWORD)] + [(n, ctypes.c_size_t) for n in (
                'peak_working_set', 'working_set', 'peak_paged', 'paged', 'peak_nonpaged', 'nonpaged', 'pagefile', 'peak_pagefile')]
        data = Counters(); data.cb = ctypes.sizeof(data)
        kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        kernel.GetCurrentProcess.restype = wintypes.HANDLE
        psapi = ctypes.WinDLL('psapi', use_last_error=True)
        psapi.GetProcessMemoryInfo.argtypes = [wintypes.HANDLE, ctypes.c_void_p, wintypes.DWORD]
        if psapi.GetProcessMemoryInfo(kernel.GetCurrentProcess(), ctypes.byref(data), data.cb):
            return data.working_set
        return None
    try:
        return int(Path('/proc/self/statm').read_text().split()[1]) * os.sysconf('SC_PAGE_SIZE')
    except (OSError, ValueError, IndexError):
        return None


def record(root, kind, duration_ms, detail):
    path = Path(root)/'runtime/tracking-metrics.sqlite3'; path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(detail, ensure_ascii=False, allow_nan=False)
    if len(payload.encode()) > 65536:
        raise ValueError('metrics_detail_bound')
    db = sqlite3.connect(path, timeout=3)
    try:
        with db:
            db.execute('CREATE TABLE IF NOT EXISTS metrics(seq INTEGER PRIMARY KEY,at_ms INTEGER,kind TEXT,duration_ms REAL,rss_bytes INTEGER,detail_json TEXT)')
            db.execute('CREATE INDEX IF NOT EXISTS metrics_kind ON metrics(kind,seq DESC)')
            db.execute('INSERT INTO metrics(at_ms,kind,duration_ms,rss_bytes,detail_json) VALUES(?,?,?,?,?)',
                (int(time.time()*1000), kind, duration_ms, memory_bytes(), payload))
            db.execute('DELETE FROM metrics WHERE seq<(SELECT MAX(seq)-4095 FROM metrics)')
    finally:
        db.close()


def read(root, limit=100):
    if type(limit) is not int or not 1 <= limit <= 500:
        raise ValueError('metrics_limit')
    path = Path(root)/'runtime/tracking-metrics.sqlite3'
    if not path.is_file():
        return dict(ok=True, items=[], samples=0, p50_ms=None, p95_ms=None, measured=False)
    db = sqlite3.connect(path.resolve().as_uri()+'?mode=ro', uri=True, timeout=3)
    db.row_factory = sqlite3.Row
    try:
        rows = [dict(r) for r in db.execute('SELECT * FROM metrics ORDER BY seq DESC LIMIT ?', (limit,))]
    finally:
        db.close()
    values = sorted(r['duration_ms'] for r in rows if r['kind']=='runtime_tick')
    def percentile(q):
        return values[max(0, __import__('math').ceil(len(values)*q)-1)] if values else None
    return dict(ok=True, items=rows, samples=len(values), p50_ms=percentile(.5), p95_ms=percentile(.95),
        scope='recent_local_runtime_ticks_not_browser_latency', measured=bool(values))

