"""Versioned B events, cursor and local notification outbox in one atomic commit."""
import copy,datetime as dt,hashlib,json,uuid
from pathlib import Path
from durable_io import protection,atomic_json
from b_accumulation_rules import RULE_VERSION,STRATEGY_ID,TIMEFRAME
DEFAULT_NAME='b-accumulation-events.json'
def _now_iso():return dt.datetime.now(dt.timezone.utc).isoformat(timespec='microseconds')
def stable_event_id(*,market,symbol,trigger_open_time,event_type='tech_h'):
    return 'bev-'+uuid.uuid5(uuid.NAMESPACE_URL,f'{STRATEGY_ID}|{RULE_VERSION}|{market}|{symbol}|{TIMEFRAME}|{int(trigger_open_time)}|{event_type}').hex
class JsonBAccumulationStore:
    def __init__(self,root):self.root=Path(root);self.path=self.root/'runtime'/DEFAULT_NAME
    def _load(self):
        d=json.loads(self.path.read_text(encoding='utf-8')) if self.path.exists() else dict(schema='b-accumulation-events-v2',strategy_id=STRATEGY_ID,rule_version=RULE_VERSION,observation_started_at=None,events={})
        start=d.get('observation_started_at')
        epoch=int(dt.datetime.fromisoformat(start).timestamp()*1000) if start else None
        for r in d['events'].values():
            if 'historical' not in r:r['historical']=epoch is None or int(r['trigger_open_time'])+3_600_000<epoch
            if r.get('schema_version',0)<2:r['migration_status']='legacy_unverified'
        for k in ('cursors','rounds','outbox'):d.setdefault(k,{})
        return d
    def _save(self,d):
        d['schema']='b-accumulation-events-v2';atomic_json(self.path,d)
    def ensure_observation_start(self,started_at=None):
        with protection(self.root):
            d=self._load()
            if not d.get('observation_started_at'):d['observation_started_at']=started_at or _now_iso();self._save(d)
            return d['observation_started_at']
    def _upsert(self,d,*,market,symbol,trigger_open_time,payload):
        eid=stable_event_id(market=market,symbol=symbol,trigger_open_time=trigger_open_time);old=d['events'].get(eid);now=_now_iso()
        if old and old.get('input_fingerprint')==payload.get('input_fingerprint'):return copy.deepcopy(old)
        if old:
            rec=copy.deepcopy(old);snapshot={k:v for k,v in old.items() if k!='revisions'}
            rec.setdefault('revisions',[]).append(dict(replaced_at=now,snapshot=snapshot));rec['previous_revision']=rec.get('event_revision',1);rec['event_revision']=rec['previous_revision']+1
        else:rec=dict(event_id=eid,market=market,symbol=symbol,trigger_open_time=int(trigger_open_time),first_seen_at=now,event_revision=1,revisions=[])
        rec.update(payload);rec['last_evaluated_at']=now
        if payload.get('schema_version',0)>=2:rec.pop('migration_status',None)
        start=d.get('observation_started_at');epoch=int(dt.datetime.fromisoformat(start).timestamp()*1000) if start else 0
        rec['historical']=int(trigger_open_time)+3_600_000<epoch
        rec['rule_version']=RULE_VERSION;rec['strategy_id']=STRATEGY_ID
        if rec.get('technical_confirmation')=='pass':rec.setdefault('confirmed_first_seen_at',now)
        d['events'][eid]=rec
        if not rec['historical']:
            for kind,passed in [('event',rec.get('technical_event')=='pass'),('confirmation',rec.get('technical_confirmation')=='pass')]:
                key=eid+':'+kind
                if passed and key not in d['outbox']:d['outbox'][key]=dict(event_id=eid,kind=kind,first_seen_at=now,scope='local_page',delivered=False)
        return copy.deepcopy(rec)
    def upsert_event(self,*,market,symbol,trigger_open_time,payload):
        with protection(self.root):
            d=self._load();rec=self._upsert(d,market=market,symbol=symbol,trigger_open_time=trigger_open_time,payload=payload);self._save(d);return rec
    def commit_symbol(self,market,symbol,updates,cursor,round_summary):
        with protection(self.root):
            d=self._load();rows=[self._upsert(d,market=market,symbol=symbol,trigger_open_time=ot,payload=p) for ot,p in updates]
            d['cursors'][market+'|'+symbol]=cursor;d['rounds'][market+'|'+symbol]=round_summary;self._save(d);return rows
    def cursor(self,market,symbol):return self._load()['cursors'].get(market+'|'+symbol)
    def list_events(self,market=None,symbol=None,full=True,revisions=True):return sorted((r for r in self._load()['events'].values() if (market is None or r['market']==market) and (symbol is None or r['symbol']==symbol)),key=lambda r:(r.get('trigger_open_time',0),r.get('symbol','')),reverse=True)
    def meta(self):
        d=self._load();return {k:d.get(k) for k in ('schema','strategy_id','rule_version','observation_started_at')}|dict(event_count=len(d['events']),rounds=list(d['rounds'].values()),notifications=list(d['outbox'].values()))

class BAccumulationStore(JsonBAccumulationStore):
    def __new__(cls,root):
        # Existing JSON is never migrated as a side effect of a read request.
        if (Path(root)/'runtime'/'b-events-v3.sqlite3').exists():
            from b_event_sqlite import SQLiteBStore
            return SQLiteBStore(root)
        return super().__new__(cls)
