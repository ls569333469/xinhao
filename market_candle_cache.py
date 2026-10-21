# -*- coding: utf-8 -*-
"""共享K线缓存：逻辑键去重、不可变块、统一读取（JSON/JSONL/gzip）。

首批：本地文件块 + SQLite 索引骨架。不发网络请求；不删受保护对象。
A 计算窗口契约不在此模块改写。
"""
from __future__ import annotations

from contextlib import closing
import gzip
import hashlib
import json
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any, Iterable, Optional

LOCK = threading.Lock()
SCHEMA_VERSION = 1


def _payload_hash(obj: Any) -> str:
    raw = json.dumps(obj, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _storage_hash(blob: bytes) -> str:
    return hashlib.sha256(blob).hexdigest()


class MarketCandleCache:
    def __init__(self, root: Path, *, readonly=False):
        self.root = Path(root)
        self.dir = self.root / "runtime" / "candle-cache"
        self.blocks = self.dir / "blocks"
        self.readonly = readonly
        self.db_path = self.dir / "index.sqlite3"
        if readonly:
            return
        self.dir.mkdir(parents=True, exist_ok=True)
        self.blocks.mkdir(parents=True, exist_ok=True)
        self.db_path = self.dir / "index.sqlite3"
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        con = sqlite3.connect(self.db_path.as_uri()+"?mode=ro", uri=True, timeout=30) if self.readonly else sqlite3.connect(str(self.db_path), timeout=30)
        con.row_factory = sqlite3.Row
        return con

    def _init_db(self) -> None:
        with LOCK:
            con = self._connect()
            try:
                con.executescript(
                    """
                    CREATE TABLE IF NOT EXISTS meta (
                      key TEXT PRIMARY KEY,
                      value TEXT NOT NULL
                    );
                    CREATE TABLE IF NOT EXISTS bars (
                      market TEXT NOT NULL,
                      symbol TEXT NOT NULL,
                      interval TEXT NOT NULL,
                      open_time INTEGER NOT NULL,
                      close_time INTEGER,
                      payload_hash TEXT NOT NULL,
                      block_id TEXT NOT NULL,
                      first_seen_at REAL NOT NULL,
                      last_seen_at REAL NOT NULL,
                      PRIMARY KEY (market, symbol, interval, open_time, payload_hash)
                    );
                    CREATE INDEX IF NOT EXISTS idx_bars_lookup
                      ON bars(market, symbol, interval, open_time);
                    CREATE TABLE IF NOT EXISTS blocks (
                      block_id TEXT PRIMARY KEY,
                      storage_hash TEXT NOT NULL,
                      payload_hash TEXT NOT NULL,
                      path TEXT NOT NULL,
                      bytes INTEGER NOT NULL,
                      created_at REAL NOT NULL
                    );
                    CREATE TABLE IF NOT EXISTS block_payloads (block_id TEXT PRIMARY KEY, data BLOB NOT NULL);
                    CREATE TABLE IF NOT EXISTS replay_pending (market TEXT,symbol TEXT,interval TEXT,open_time INTEGER,PRIMARY KEY(market,symbol,interval,open_time));
                    CREATE TABLE IF NOT EXISTS retrievals (
                      id INTEGER PRIMARY KEY AUTOINCREMENT,
                      market TEXT NOT NULL,
                      symbol TEXT NOT NULL,
                      interval TEXT NOT NULL,
                      open_time INTEGER NOT NULL,
                      payload_hash TEXT NOT NULL,
                      retrieved_at REAL NOT NULL,
                      quality TEXT,
                      error TEXT
                    );
                    """
                )
                con.execute(
                    "INSERT OR IGNORE INTO meta(key,value) VALUES('schema_version',?)",
                    (str(SCHEMA_VERSION),),
                )
                con.commit()
            finally:
                con.close()

    def put_bar(
        self,
        *,
        market: str,
        symbol: str,
        interval: str,
        open_time: int,
        bar: dict,
        close_time: int | None = None,
        quality: str | None = None,
        error: str | None = None,
        now: float | None = None,
    ) -> dict:
        """写入一根已规范化K线。同键同载荷复用块；同键不同值追加修订。"""
        now = time.time() if now is None else now
        payload = {
            "market": market,
            "symbol": symbol,
            "interval": interval,
            "open_time": int(open_time),
            "close_time": close_time,
            "bar": bar,
        }
        return self.put_many([payload], now=now, quality=quality, error=error)[0]

    def put_many(self, payloads, *, now=None, quality='ok', error=None):
        """One transaction; immutable compressed payloads inside SQLite avoid tiny-file storms."""
        now=time.time() if now is None else now
        refs=[]
        from durable_io import protection
        with protection(self.root), LOCK, closing(self._connect()) as con, con:
            for payload in payloads:
                current=con.execute('SELECT payload_hash FROM bars WHERE market=? AND symbol=? AND interval=? AND open_time=? ORDER BY last_seen_at DESC LIMIT 1',(payload['market'],payload['symbol'],payload['interval'],payload['open_time'])).fetchone()
                ph=_payload_hash(payload);bid=ph[:32]
                if current is None or current[0]!=ph:con.execute('INSERT OR IGNORE INTO replay_pending VALUES(?,?,?,?)',(payload['market'],payload['symbol'],payload['interval'],payload['open_time']))
                old=con.execute('SELECT storage_hash FROM blocks WHERE block_id=?',(bid,)).fetchone()
                if old:sh=old['storage_hash']
                else:
                    blob=gzip.compress(json.dumps(payload,ensure_ascii=False,separators=(',',':')).encode(),mtime=0)
                    sh=_storage_hash(blob)
                    con.execute('INSERT INTO block_payloads VALUES(?,?)',(bid,blob))
                    con.execute('INSERT INTO blocks VALUES(?,?,?,?,?,?)',(bid,sh,ph,'',len(blob),now))
                con.execute('INSERT INTO bars VALUES(?,?,?,?,?,?,?,?,?) ON CONFLICT(market,symbol,interval,open_time,payload_hash) DO UPDATE SET last_seen_at=excluded.last_seen_at',
                    (payload['market'],payload['symbol'],payload['interval'],payload['open_time'],payload.get('close_time'),ph,bid,now,now))
                # Each acquisition is evidence, including reuse of an unchanged payload.
                con.execute('INSERT INTO retrievals(market,symbol,interval,open_time,payload_hash,retrieved_at,quality,error) VALUES(?,?,?,?,?,?,?,?)',(payload['market'],payload['symbol'],payload['interval'],payload['open_time'],ph,now,quality,error))
                refs.append(dict(payload_hash=ph,block_id=bid,storage_hash=sh,reused=old is not None,changed=current is None or current[0]!=ph))
        return refs

    def pending_replay(self,market,symbol,interval='1h'):
        with LOCK,closing(self._connect()) as db:
            return [r[0] for r in db.execute('SELECT open_time FROM replay_pending WHERE market=? AND symbol=? AND interval=?',(market,symbol,interval))]

    def acknowledge_replay(self,market,symbol,opens,interval='1h'):
        from durable_io import protection
        with protection(self.root),LOCK,closing(self._connect()) as db,db:
            db.executemany('DELETE FROM replay_pending WHERE market=? AND symbol=? AND interval=? AND open_time=?',[(market,symbol,interval,t) for t in opens])

    def coverage(self, *, market, symbol, interval='1h', target=None):
        """Index-only coverage; never decompress candle payloads to find a cursor."""
        step={'1h':3600000,'4h':14400000}[interval]
        if not self.db_path.exists():return dict(count=0,first_open=None,last_open=None,tail=[],gaps=[],archived_contiguous_through=None)
        with LOCK,closing(self._connect()) as db:
            args=[market,symbol,interval];query='SELECT DISTINCT open_time FROM bars WHERE market=? AND symbol=? AND interval=?'
            if target is not None:query+=' AND open_time<?';args.append(target)
            times=[r[0] for r in db.execute(query+' ORDER BY open_time',args)]
        gaps=[dict(after=a,before=b) for a,b in zip(times,times[1:]) if b-a!=step]
        if times and target is not None and times[-1]+step<target:gaps.append(dict(after=times[-1],before=target,kind='missing_tail'))
        return dict(count=len(times),first_open=times[0] if times else None,last_open=times[-1] if times else None,tail=times[-2:],gaps=gaps,
                    archived_contiguous_through=(gaps[0]['after']+step if gaps else times[-1]+step if times else None))

    def get_bars(self, *, market, symbol, interval, start_open=None, end_open=None, latest_payload_only=True):
        from contextlib import closing
        with LOCK, closing(self._connect()) as con:
            has_blobs=con.execute("SELECT 1 FROM sqlite_master WHERE name='block_payloads'").fetchone() is not None
            q=('SELECT b.*,k.path,k.storage_hash,'+('p.data' if has_blobs else 'NULL AS data')+' FROM bars b JOIN blocks k ON b.block_id=k.block_id '+('LEFT JOIN block_payloads p ON b.block_id=p.block_id ' if has_blobs else '')+'WHERE b.market=? AND b.symbol=? AND b.interval=?')
            args=[market,symbol,interval]
            if start_open is not None:q+=' AND b.open_time>=?';args.append(int(start_open))
            if end_open is not None:q+=' AND b.open_time<?';args.append(int(end_open))
            q+=' ORDER BY b.open_time,b.last_seen_at DESC'
            rows=[dict(r) for r in con.execute(q,args)]
        seen=set();out=[]
        for r in rows:
            if latest_payload_only and r['open_time'] in seen:continue
            seen.add(r['open_time']);blob=r.pop('data')
            if blob is None:
                path=(self.root/r['path']).resolve();path.relative_to(self.root.resolve());blob=path.read_bytes()
            if _storage_hash(blob)!=r['storage_hash']:raise ValueError('block storage hash mismatch')
            raw=gzip.decompress(blob) if blob[:2]==b'\x1f\x8b' else blob
            payload=json.loads(raw)
            if _payload_hash(payload)!=r['payload_hash']:raise ValueError('block payload hash mismatch')
            out.append(dict(r,payload=payload))
        return out

    def read_block(self, block_id: str) -> dict:
        with LOCK:
            con = self._connect()
            try:
                row = con.execute("SELECT path, storage_hash FROM blocks WHERE block_id=?", (block_id,)).fetchone()
            finally:
                con.close()
        if not row:
            raise FileNotFoundError(block_id)
        path = self.root / row["path"]
        if row['path']:
            path.resolve().relative_to(self.root.resolve())
            blob = path.read_bytes()
        else:
            with closing(self._connect()) as con:
                stored=con.execute('SELECT data FROM block_payloads WHERE block_id=?',(block_id,)).fetchone()
                if not stored:raise FileNotFoundError(block_id)
                blob=stored[0]
        if _storage_hash(blob) != row["storage_hash"]:
            raise ValueError(f"block storage hash mismatch: {block_id}")
        if not row["path"] or path.suffix == ".gz" or path.name.endswith(".json.gz"):
            raw = gzip.decompress(blob)
        else:
            raw = blob
        payload = json.loads(raw.decode("utf-8"))
        if _payload_hash(payload)[:32] != block_id:
            raise ValueError("block payload hash mismatch")
        return payload


def read_json_maybe_gzip(path: Path) -> Any:
    """统一读取 JSON / .json.gz / JSONL（返回 list）。"""
    path = Path(path)
    from durable_io import read_bytes_shared
    data = read_bytes_shared(path)
    if path.name.endswith(".gz"):
        data = gzip.decompress(data)
    text = data.decode("utf-8")
    if path.name.endswith(".jsonl") or path.name.endswith(".jsonl.gz"):
        return [json.loads(line) for line in text.splitlines() if line.strip()]
    obj=json.loads(text)
    if isinstance(obj,dict) and obj.get("storage_format")=="shared-raw-v1":
        from shared_candles import expand
        return expand(path,obj)
    return obj
