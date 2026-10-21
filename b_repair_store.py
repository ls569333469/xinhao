"""Durable, bounded B evidence repair queue. No network or strategy decisions."""
import hashlib,json,sqlite3
from contextlib import contextmanager
from pathlib import Path

H=3_600_000

def missing_evidence(event):
    result=[]
    if event.get('technical_event')=='unknown':result.append('technical_h')
    if event.get('technical_confirmation')=='unknown':result.append('technical_h1')
    for field,label in [('structure_h','h'),('structure_confirmation','h1')]:
        value=event.get(field) or {}
        if value.get('state')=='pending':continue
        for source in ('oi','lsr','cap'):
            if value.get(source+'_quality')!='pass':result.append(label+':'+source)
    return result

class RepairStore:
    def __init__(self,root):self.path=Path(root)/'runtime/b-evidence-repair.sqlite3'

    def ready(self):
        if not self.path.exists():return False
        with self.db() as db:return db.execute("SELECT count(*) FROM sqlite_master WHERE type='table' AND name IN ('jobs','caps','provider_caps','controls')").fetchone()[0]==4

    @contextmanager
    def db(self,write=False):
        if write:self.path.parent.mkdir(parents=True,exist_ok=True)
        db=sqlite3.connect(self.path.resolve().as_uri()+('?mode=rwc' if write else '?mode=ro'),uri=True,timeout=10)
        db.row_factory=sqlite3.Row
        try:
            if write:db.execute('BEGIN IMMEDIATE')
            yield db
            if write:db.commit()
        except BaseException:db.rollback();raise
        finally:db.close()

    def initialize(self):
        with self.db(True) as db:
            schema='''
              CREATE TABLE IF NOT EXISTS jobs(event_id TEXT PRIMARY KEY,market TEXT,symbol TEXT,trigger_open INTEGER,input_hash TEXT,missing TEXT,state TEXT,attempts INTEGER,next_try INTEGER,lease_until INTEGER,last_attempt INTEGER,last_error TEXT,receipt TEXT,updated_at INTEGER);
              CREATE INDEX IF NOT EXISTS repair_due ON jobs(state,next_try);
              CREATE TABLE IF NOT EXISTS caps(market TEXT,symbol TEXT,observed_at INTEGER,digest TEXT,payload TEXT,PRIMARY KEY(market,symbol,observed_at,digest));
              CREATE TABLE IF NOT EXISTS provider_caps(provider TEXT,asset_id TEXT,observed_at INTEGER,digest TEXT,payload TEXT,PRIMARY KEY(provider,asset_id,observed_at,digest));
              CREATE TABLE IF NOT EXISTS imported(path TEXT PRIMARY KEY,stamp TEXT);
              CREATE TABLE IF NOT EXISTS provider_windows(key TEXT PRIMARY KEY,next_try INTEGER,error TEXT);
              CREATE TABLE IF NOT EXISTS controls(key TEXT PRIMARY KEY,value TEXT);
            '''
            for statement in schema.split(';'):
                if statement.strip():db.execute(statement)
            db.execute("INSERT OR IGNORE INTO controls VALUES('enabled','true')")

    def enabled(self):
        if not self.path.exists():return True
        try:
            with self.db() as db:
                row=db.execute("SELECT value FROM controls WHERE key='enabled'").fetchone()
                return json.loads(row[0]) if row else True
        except sqlite3.OperationalError as exc:
            if 'no such table' in str(exc):return True # new schema not committed yet
            raise

    def set_enabled(self,enabled):
        if type(enabled) is not bool:raise ValueError('enabled must be boolean')
        self.initialize()
        with self.db(True) as db:db.execute("UPDATE controls SET value=? WHERE key='enabled'",(json.dumps(enabled),))

    def discover(self,events,now):
        self.initialize()
        with self.db(True) as db:
            for event in events:
                # Explicit failures/corrected records are not advertised as unresolved.
                needs=missing_evidence(event)
                eligible=event.get('technical_event')!='fail' and event.get('structure_gate')!='fail' and event.get('technical_confirmation')!='fail' and bool(needs)
                old=db.execute('SELECT * FROM jobs WHERE event_id=?',(event['event_id'],)).fetchone()
                if not eligible:
                    if old and old['state']!='resolved':db.execute("UPDATE jobs SET state='resolved',missing='[]',lease_until=NULL,updated_at=? WHERE event_id=?",(now,event['event_id']))
                    continue
                fp=event.get('input_fingerprint') or str(event.get('event_revision'))
                if not old:
                    db.execute('INSERT INTO jobs VALUES(?,?,?,?,?,?,?,0,?,NULL,NULL,NULL,NULL,?)',(event['event_id'],event['market'],event['symbol'],event['trigger_open_time'],fp,json.dumps(needs),'queued',now,now))
                elif old['input_hash']!=fp and old['state']!='running':
                    db.execute("UPDATE jobs SET input_hash=?,missing=?,state='queued',next_try=?,updated_at=? WHERE event_id=?",(fp,json.dumps(needs),now,now,event['event_id']))

    def due(self,now):
        if not self.ready() or not self.enabled():return False
        with self.db() as db:return db.execute("SELECT 1 FROM jobs WHERE (state IN ('queued','retry','blocked') AND next_try<=?) OR (state='running' AND lease_until<=?) LIMIT 1",(now,now)).fetchone() is not None

    def claim(self,now):
        with self.db(True) as db:
            row=db.execute("SELECT * FROM jobs WHERE (state IN ('queued','retry','blocked') AND next_try<=?) OR (state='running' AND lease_until<=?) ORDER BY next_try,attempts,event_id LIMIT 1",(now,now)).fetchone()
            if not row:return None
            db.execute("UPDATE jobs SET state='running',attempts=attempts+1,last_attempt=?,lease_until=? WHERE event_id=?",(now,now+5*60_000,row['event_id']))
            return dict(row)|dict(attempts=row['attempts']+1)

    def finish(self,job,now,*,missing,error=None,blocked=False,receipt=None):
        delay=min(6*H,5*60_000*2**min(job['attempts']-1,6))
        state='resolved' if not missing else 'blocked' if blocked else 'retry'
        with self.db(True) as db:
            db.execute('UPDATE jobs SET state=?,missing=?,next_try=?,lease_until=NULL,last_error=?,receipt=?,updated_at=? WHERE event_id=?',(state,json.dumps(missing),now+(6*H if blocked else delay),error,json.dumps(receipt,ensure_ascii=False),now,job['event_id']))

    def record_caps(self,rows,now):
        self.initialize()
        from b_evidence import ms
        with self.db(True) as db:
            for item in rows:
                at=ms(item.get('capObservedAt'))
                if not item.get('market') or not item.get('symbol') or item.get('capTrusted') is not True or at is None or at>now:continue
                value={k:v for k,v in item.items() if k.startswith('cap') and not isinstance(v,(dict,list))}
                encoded=json.dumps(value,ensure_ascii=False,sort_keys=True,allow_nan=False);digest=hashlib.sha256(encoded.encode()).hexdigest()
                value.setdefault('capFirstRetrievedAt',now)
                db.execute('INSERT OR IGNORE INTO caps VALUES(?,?,?,?,?)',(item['market'],item['symbol'],at,digest,json.dumps(value,ensure_ascii=False)))
                if value.get('capSource') and value.get('capId'):db.execute('INSERT OR IGNORE INTO provider_caps VALUES(?,?,?,?,?)',(value['capSource'],value['capId'],at,digest,json.dumps(value,ensure_ascii=False)))

    def caps(self,market,symbol,item=None):
        if not self.ready():return []
        with self.db() as db:
            values=[json.loads(r[0]) for r in db.execute('SELECT payload FROM caps WHERE market=? AND symbol=? ORDER BY observed_at',(market,symbol))]
            if item and item.get('capTrusted') is True and item.get('capSource') and item.get('capId'):
                values.extend(json.loads(r[0]) for r in db.execute('SELECT payload FROM provider_caps WHERE provider=? AND asset_id=? ORDER BY observed_at',(item['capSource'],item['capId'])))
            return values

    def summary(self,market=None,symbol=None):
        if not self.ready():return dict(schema='b-evidence-repair-v1',enabled=True,counts={},jobs=[],initializing=self.path.exists())
        where=' WHERE market=? AND symbol=?' if market and symbol else ''
        args=(market,symbol) if where else ()
        with self.db() as db:
            counts={r[0]:r[1] for r in db.execute('SELECT state,count(*) FROM jobs'+where+' GROUP BY state',args)}
            rows=[dict(r) for r in db.execute('SELECT event_id,state,missing,attempts,next_try,last_attempt,last_error,updated_at FROM jobs'+where+' ORDER BY updated_at DESC LIMIT 1000',args)]
        for row in rows:row['missing']=json.loads(row['missing'])
        return dict(schema='b-evidence-repair-v1',enabled=self.enabled(),counts=counts,jobs=rows)
