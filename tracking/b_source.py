"""Additive source changefeed, separate source ACK after tracking COMMIT.

Never imports a JSON event history wholesale. Historical seeding is keyset-paged,
retains each existing revision, and never rewrites B event/cursor/outbox data.
"""
import hashlib,json
from .b_tracking import BTracker
from .tracking_engine import iso

DDL="""CREATE TABLE IF NOT EXISTS tracking_changes(seq INTEGER PRIMARY KEY AUTOINCREMENT,event_id TEXT NOT NULL,revision INTEGER NOT NULL,payload TEXT NOT NULL,payload_hash TEXT NOT NULL,created_at TEXT NOT NULL,acked_at TEXT,UNIQUE(event_id,revision))"""

def enqueue(db,eid,revision,payload,now):
    sha=hashlib.sha256(payload.encode()).hexdigest()
    old=db.execute('SELECT payload_hash FROM tracking_changes WHERE event_id=? AND revision=?',(eid,revision)).fetchone()
    if old and old[0]!=sha:raise ValueError('B_tracking_source_revision_conflict')
    db.execute('INSERT OR IGNORE INTO tracking_changes(event_id,revision,payload,payload_hash,created_at) VALUES(?,?,?,?,?)',(eid,revision,payload,sha,now))

def seed_page(store, *, after_event_id='',after_revision=0,limit=100):
    if type(limit) is not int or not 1<=limit<=100:raise ValueError('B_seed_limit')
    with store.connection(write=True) as db:
        db.execute(DDL)
        rows=db.execute("SELECT event_id,revision,payload FROM (SELECT event_id,revision,payload FROM event_revisions UNION ALL SELECT event_id,CAST(json_extract(payload,'$.event_revision') AS INTEGER),payload FROM events) WHERE event_id>? OR (event_id=? AND revision>?) ORDER BY event_id,revision LIMIT ?",(after_event_id,after_event_id,after_revision,limit+1)).fetchall()
        for row in rows[:limit]:enqueue(db,row['event_id'],row['revision'],row['payload'],'historical_seed')
        return dict(ok=True,count=min(len(rows),limit),continuation=dict(after_event_id=rows[limit-1]['event_id'],after_revision=rows[limit-1]['revision']) if len(rows)>limit else None)

def consume_page(store,ledger,*,clock_iso,limit=50,after_tracking_commit=None):
    if type(limit) is not int or not 1<=limit<=100:raise ValueError('B_consumer_limit')
    with store.connection() as db:
        exists=db.execute("SELECT 1 FROM sqlite_master WHERE name='tracking_changes'").fetchone()
        if not exists:return dict(ok=True,count=0,not_initialized=True)
        rows=db.execute('SELECT * FROM tracking_changes WHERE acked_at IS NULL ORDER BY seq LIMIT ?',(limit,)).fetchall()
        source=[]
        for row in rows:
            if hashlib.sha256(row['payload'].encode()).hexdigest()!=row['payload_hash']:raise ValueError('B_tracking_change_hash_mismatch')
            source.append((dict(row),store._expand(db,json.loads(row['payload']),{})))
    results=[]
    for change,payload in source:
        historical=change['created_at']=='historical_seed' or bool(payload.get('historical'))
        result=BTracker(ledger).consume_revision(payload,clock_iso=clock_iso,historical_import=historical)
        results.append(result)
        if not result.get('ok'):break
        if after_tracking_commit:after_tracking_commit(change,result)
        # Crash here leaves source pending. Retrying verifies the same inbox hash.
        with store.connection(write=True) as db:
            db.execute('UPDATE tracking_changes SET acked_at=? WHERE seq=? AND payload_hash=? AND acked_at IS NULL',(clock_iso,change['seq'],change['payload_hash']))
    return dict(ok=all(r.get('ok') for r in results),count=len(results),items=results)


def seed_incremental(store,*,limit=100):
    """Durable paged bootstrap; a crash may repeat a page but cannot skip it."""
    with store.connection(write=True) as db:
        db.execute('CREATE TABLE IF NOT EXISTS tracking_seed_state(key TEXT PRIMARY KEY,value TEXT NOT NULL)')
        row=db.execute("SELECT value FROM tracking_seed_state WHERE key='existing-revisions-v1'").fetchone()
        old=row[0] if row else None
    if old=='done':return dict(ok=True,count=0,complete=True)
    cursor=json.loads(old) if old else {}
    result=seed_page(store,**cursor,limit=limit)
    new=json.dumps(result['continuation'],sort_keys=True) if result['continuation'] else 'done'
    with store.connection(write=True) as db:
        row=db.execute("SELECT value FROM tracking_seed_state WHERE key='existing-revisions-v1'").fetchone()
        if (row[0] if row else None)!=old:raise ValueError('B_seed_cursor_concurrent_change')
        db.execute("INSERT OR REPLACE INTO tracking_seed_state VALUES('existing-revisions-v1',?)",(new,))
    return dict(result,complete=new=='done')
