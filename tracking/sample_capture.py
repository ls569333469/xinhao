"""Automatically preserve genuine A published confirmations; never create signals."""
import hashlib
import json
import sqlite3
from pathlib import Path
from .ledger import _canonical


def capture(root, *, row, raw_bars, publication, clock_iso):
    stage = row.get('emaSqueezeStage') or {}
    confirmations = [x for x in (stage.get('confirmedEvents') or []) + [stage.get('crossConfirm') or {}]
        if x.get('status') == 'confirmed' and x.get('candidate_cycle_id') and x.get('confirm_bar_time') is not None]
    if not confirmations:
        return dict(captured=0, reason='no_original_published_confirmation')
    payload = dict(kind='original_A_published_confirmation', row=row, raw_bars=raw_bars,
        publication=publication, confirmations=confirmations, captured_at=clock_iso,
        qualification_not_recomputed=True)
    text = _canonical(payload)
    if len(text.encode()) > 4*1024*1024:
        raise ValueError('sample_capture_page_required')
    # Capture clock is provenance, not semantic uniqueness; retries preserve first receipt.
    key = hashlib.sha256(_canonical(dict(row=row, raw_bars=raw_bars, publication=publication)).encode()).hexdigest()
    path = Path(root)/'runtime/tracking-real-samples.sqlite3'; path.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(path, timeout=5)
    try:
        with db:
            db.execute('CREATE TABLE IF NOT EXISTS samples(seq INTEGER PRIMARY KEY,sample_key TEXT UNIQUE,payload_sha256 TEXT,payload_json TEXT,captured_at TEXT)')
            added = db.execute('INSERT OR IGNORE INTO samples(sample_key,payload_sha256,payload_json,captured_at) VALUES(?,?,?,?)',
                (key, hashlib.sha256(text.encode()).hexdigest(), text, clock_iso)).rowcount
    finally:
        db.close()
    return dict(captured=added, sample_key=key, independent_verdict='NOT_RUN')


def read(root, *, after=0, limit=20):
    if type(after) is not int or after<0 or type(limit) is not int or not 1<=limit<=100:
        raise ValueError('sample_page')
    path=Path(root)/'runtime/tracking-real-samples.sqlite3'
    if not path.is_file():
        return dict(ok=True,items=[],next_cursor=None)
    db=sqlite3.connect(path.resolve().as_uri()+'?mode=ro',uri=True,timeout=3)
    db.row_factory=sqlite3.Row
    try:
        rows=[dict(r) for r in db.execute('SELECT seq,sample_key,payload_sha256,captured_at FROM samples WHERE seq>? ORDER BY seq LIMIT ?',(after,limit+1))]
    finally:
        db.close()
    return dict(ok=True,items=rows[:limit],next_cursor=rows[limit-1]['seq'] if len(rows)>limit else None,
        scope='original_published_candidates_pending_independent_qualification')

