"""Indexed B ledger. Events, revisions, cursors, outbox and refs share one transaction."""
import copy,gzip,hashlib,json,sqlite3
from contextlib import contextmanager
from pathlib import Path
from b_accumulation_store import JsonBAccumulationStore,_now_iso,stable_event_id
from b_accumulation_rules import RULE_VERSION,STRATEGY_ID
from durable_io import protection

def encoded(value):return json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':'),allow_nan=False).encode()

class SQLiteBStore(JsonBAccumulationStore):
    def __init__(self,root,path=None,initialize=False):
        self.root=Path(root);self.path=Path(path) if path else self.root/'runtime'/'b-events-v3.sqlite3'
        if initialize:
            self.path.parent.mkdir(parents=True,exist_ok=True)
            with self.connection(write=True,creating=True) as db:
                db.executescript('''
                  CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY,value TEXT);
                  CREATE TABLE IF NOT EXISTS events(event_id TEXT PRIMARY KEY,market TEXT,symbol TEXT,bar_open INTEGER,payload TEXT);
                  CREATE INDEX IF NOT EXISTS symbol_events ON events(market,symbol,bar_open);
                  CREATE TABLE IF NOT EXISTS event_revisions(event_id TEXT,revision INTEGER,replaced_at TEXT,payload TEXT,PRIMARY KEY(event_id,revision));
                  CREATE TABLE IF NOT EXISTS symbol_cursors(key TEXT PRIMARY KEY,payload TEXT);
                  CREATE TABLE IF NOT EXISTS symbol_rounds(key TEXT PRIMARY KEY,payload TEXT);
                  CREATE TABLE IF NOT EXISTS outbox(key TEXT PRIMARY KEY,payload TEXT);
                  CREATE TABLE IF NOT EXISTS evidence_blobs(hash TEXT PRIMARY KEY,storage_hash TEXT,data BLOB);
                  CREATE TABLE IF NOT EXISTS evidence_refs(owner TEXT,path TEXT,hash TEXT,PRIMARY KEY(owner,path));
                  CREATE TABLE IF NOT EXISTS replay_jobs(key TEXT PRIMARY KEY,payload TEXT);
                  CREATE TABLE IF NOT EXISTS migration_state(key TEXT PRIMARY KEY,value TEXT);
                ''')
                for key,value in dict(schema='b-accumulation-events-v3',strategy_id=STRATEGY_ID,rule_version=RULE_VERSION,generation=0,observation_started_at=None).items():
                    db.execute('INSERT OR IGNORE INTO meta VALUES(?,?)',(key,json.dumps(value)))

    @contextmanager
    def connection(self,write=False,creating=False):
        uri=self.path.resolve().as_uri()+('?mode=rwc' if creating else '?mode=rw' if write else '?mode=ro')
        db=sqlite3.connect(uri,uri=True,timeout=30);db.row_factory=sqlite3.Row
        try:
            db.execute('PRAGMA busy_timeout=30000')
            if write:db.execute('BEGIN IMMEDIATE')
            else:db.execute('BEGIN')
            yield db
            db.commit()
        except BaseException:db.rollback();raise
        finally:db.close()

    def _pack(self,db,value,owner,path=''):
        if isinstance(value,dict):
            result={}
            for key,obj in value.items():
                location=path+'/'+key
                if key in ('bg4h','bg4h_h1','baseline') and obj is not None:
                    raw=encoded(obj);digest=hashlib.sha256(raw).hexdigest();blob=gzip.compress(raw,mtime=0);sh=hashlib.sha256(blob).hexdigest()
                    db.execute('INSERT OR IGNORE INTO evidence_blobs VALUES(?,?,?)',(digest,sh,blob))
                    db.execute('INSERT OR REPLACE INTO evidence_refs VALUES(?,?,?)',(owner,location,digest))
                    result[key]={'$b_evidence':digest,'storage_hash':sh,'format':'canonical-json-gzip-v1'}
                else:result[key]=self._pack(db,obj,owner,location)
            return result
        if isinstance(value,list):return [self._pack(db,obj,owner,path+'/'+str(i)) for i,obj in enumerate(value)]
        return value

    def _expand(self,db,value,memo):
        if isinstance(value,dict):
            if '$b_evidence' in value:
                digest=value['$b_evidence']
                if digest not in memo:
                    row=db.execute('SELECT storage_hash,data FROM evidence_blobs WHERE hash=?',(digest,)).fetchone()
                    if row is None:raise ValueError('B证据块缺失: '+digest)
                    blob=row['data']
                    if hashlib.sha256(blob).hexdigest()!=row['storage_hash'] or row['storage_hash']!=value['storage_hash']:raise ValueError('B证据存储hash不符: '+digest)
                    raw=gzip.decompress(blob)
                    if hashlib.sha256(raw).hexdigest()!=digest:raise ValueError('B证据载荷hash不符: '+digest)
                    memo[digest]=json.loads(raw)
                return copy.deepcopy(memo[digest])
            return {k:self._expand(db,v,memo) for k,v in value.items()}
        if isinstance(value,list):return [self._expand(db,v,memo) for v in value]
        return value

    def _event(self,db,row,full=True,revisions=True,memo=None):
        value=json.loads(row['payload']);memo={} if memo is None else memo
        if revisions:value['revisions']=[dict(replaced_at=r['replaced_at'],snapshot=json.loads(r['payload'])) for r in db.execute('SELECT * FROM event_revisions WHERE event_id=? ORDER BY revision',(row['event_id'],))]
        return self._expand(db,value,memo) if full else value

    def _write_event(self,db,rec):
        eid=rec['event_id']
        for i,revision in enumerate(rec.get('revisions',[])):
            snapshot=revision['snapshot'];number=snapshot.get('event_revision',i+1)
            packed=self._pack(db,snapshot,eid+':revision:'+str(number))
            db.execute('INSERT OR IGNORE INTO event_revisions VALUES(?,?,?,?)',(eid,number,revision.get('replaced_at'),encoded(packed).decode()))
        packed=self._pack(db,{k:v for k,v in rec.items() if k!='revisions'},eid)
        db.execute('INSERT OR REPLACE INTO events VALUES(?,?,?,?,?)',(eid,rec['market'],rec['symbol'],rec['trigger_open_time'],encoded(packed).decode()))
        from tracking_service import enabled
        if enabled(self.root):
            from tracking.b_source import DDL,enqueue
            db.execute(DDL)
            for r in db.execute('SELECT revision,payload FROM event_revisions WHERE event_id=? ORDER BY revision',(eid,)):
                enqueue(db,eid,r['revision'],r['payload'],'historical_seed')
            enqueue(db,eid,rec['event_revision'],encoded(packed).decode(),_now_iso())

    def ensure_observation_start(self,started_at=None):
        with protection(self.root),self.connection(write=True) as db:
            value=json.loads(db.execute("SELECT value FROM meta WHERE key='observation_started_at'").fetchone()[0])
            if not value:
                value=started_at or _now_iso();db.execute("UPDATE meta SET value=? WHERE key='observation_started_at'",(json.dumps(value),));self._generation(db)
            return value

    def _generation(self,db):db.execute("UPDATE meta SET value=CAST(value AS INTEGER)+1 WHERE key='generation'")

    def commit_symbol(self,market,symbol,updates,cursor,round_summary):
        ids=[]
        with protection(self.root),self.connection(write=True) as db:
            start=json.loads(db.execute("SELECT value FROM meta WHERE key='observation_started_at'").fetchone()[0])
            for ot,payload in updates:
                eid=stable_event_id(market=market,symbol=symbol,trigger_open_time=ot)
                row=db.execute('SELECT * FROM events WHERE event_id=?',(eid,)).fetchone()
                old=self._event(db,row,revisions=False) if row else None
                if old and old.get('input_fingerprint')==payload.get('input_fingerprint'):ids.append(eid);continue
                outbox={r['key']:json.loads(r['payload']) for r in db.execute('SELECT * FROM outbox WHERE key IN (?,?)',(eid+':event',eid+':confirmation'))}
                state=dict(events={eid:old} if old else {},outbox=outbox,observation_started_at=start)
                rec=self._upsert(state,market=market,symbol=symbol,trigger_open_time=ot,payload=payload)
                self._write_event(db,rec)
                for key,value in state['outbox'].items():db.execute('INSERT OR IGNORE INTO outbox VALUES(?,?)',(key,encoded(value).decode()))
                ids.append(eid)
            key=market+'|'+symbol
            if cursor is not None:db.execute('INSERT OR REPLACE INTO symbol_cursors VALUES(?,?)',(key,encoded(cursor).decode()))
            if round_summary is not None:db.execute('INSERT OR REPLACE INTO symbol_rounds VALUES(?,?)',(key,encoded(round_summary).decode()))
            self._generation(db)
            result=[self._event(db,db.execute('SELECT * FROM events WHERE event_id=?',(eid,)).fetchone()) for eid in ids]
        from tracking_service import enabled,kick,capture_error
        if enabled(self.root):
            try:kick(self.root)
            except Exception as exc:capture_error(self.root,exc)
        return result

    def upsert_event(self,*,market,symbol,trigger_open_time,payload):
        return self.commit_symbol(market,symbol,[(trigger_open_time,payload)],None,None)[0]

    def cursor(self,market,symbol):
        with self.connection() as db:
            row=db.execute('SELECT payload FROM symbol_cursors WHERE key=?',(market+'|'+symbol,)).fetchone()
            return json.loads(row[0]) if row else None

    def list_events(self,market=None,symbol=None,full=True,revisions=True):
        with self.connection() as db:
            clauses=[];args=[]
            for key,val in [('market',market),('symbol',symbol)]:
                if val is not None:clauses.append(key+'=?');args.append(val)
            query='SELECT * FROM events'+(' WHERE '+' AND '.join(clauses) if clauses else '')+' ORDER BY bar_open DESC,symbol'
            memo={};return [self._event(db,r,full,revisions,memo) for r in db.execute(query,args).fetchall()]

    def get_event(self,event_id):
        with self.connection() as db:
            row=db.execute('SELECT * FROM events WHERE event_id=?',(event_id,)).fetchone()
            return self._event(db,row) if row else None

    def meta(self):
        with self.connection() as db:
            result={r['key']:json.loads(r['value']) for r in db.execute('SELECT * FROM meta')}
            result.update(event_count=db.execute('SELECT count(*) FROM events').fetchone()[0],rounds=[json.loads(r[0]) for r in db.execute('SELECT payload FROM symbol_rounds')],notifications=[json.loads(r[0]) for r in db.execute('SELECT payload FROM outbox')])
            return result

    def generation(self):
        with self.connection() as db:return db.execute("SELECT value FROM meta WHERE key='generation'").fetchone()[0]

    def _load(self):
        result=self.meta();result['events']={r['event_id']:r for r in self.list_events()}
        with self.connection() as db:
            for key,table in [('cursors','symbol_cursors'),('rounds','symbol_rounds'),('outbox','outbox')]:result[key]={r['key']:json.loads(r['payload']) for r in db.execute('SELECT * FROM '+table)}
        return result
