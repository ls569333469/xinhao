"""Explicit resumable migration; never invoked by GET or by store construction."""
import argparse,hashlib,json,os,shutil,sqlite3,time
from pathlib import Path
from b_accumulation_store import JsonBAccumulationStore
from b_event_sqlite import SQLiteBStore,encoded
from durable_io import protection,atomic_json

def migrate(root):
    root=Path(root);source=root/'runtime'/'b-accumulation-events.json';final=root/'runtime'/'b-events-v3.sqlite3'
    staging=final.with_suffix('.migrating.sqlite3')
    with protection(root):
        digest=hashlib.sha256(source.read_bytes()).hexdigest() if source.exists() else 'empty'
        if final.exists():
            store=SQLiteBStore(root)
            with store.connection() as db:state=dict(db.execute('SELECT key,value FROM migration_state'))
            if state.get('source_sha256')!=digest:raise ValueError('已迁移库与旧账本不匹配，需核对增量；拒绝覆盖')
            return dict(already_migrated=True,source_sha256=digest,events=store.meta()['event_count'])
        backup=root/'runtime'/'migration-backups'/('b-events-'+digest+'.json')
        if source.exists():
            backup.parent.mkdir(parents=True,exist_ok=True)
            if not backup.exists():
                with source.open('rb') as read,backup.open('xb') as write:shutil.copyfileobj(read,write);write.flush();os.fsync(write.fileno())
            if hashlib.sha256(backup.read_bytes()).hexdigest()!=digest:raise ValueError('旧账本备份校验失败')
        data=JsonBAccumulationStore(root)._load();store=SQLiteBStore(root,path=staging,initialize=True)
        with store.connection(write=True) as db:
            prior=db.execute("SELECT value FROM migration_state WHERE key='source_sha256'").fetchone()
            if prior and prior[0]!=digest:raise ValueError('迁移断点属于不同源，拒绝继续')
            db.execute("INSERT OR REPLACE INTO migration_state VALUES('source_sha256',?)",(digest,))
        for eid,event in data['events'].items():
            if eid!=event['event_id']:raise ValueError('事件键与身份不符: '+eid)
            with store.connection(write=True) as db:
                if not db.execute('SELECT 1 FROM events WHERE event_id=?',(eid,)).fetchone():store._write_event(db,event)
            if encoded(store.get_event(eid))!=encoded(event):raise ValueError('迁移字段对账失败: '+eid)
        with store.connection(write=True) as db:
            for key,value in data.items():
                if key not in ('events','cursors','rounds','outbox'):db.execute('INSERT OR REPLACE INTO meta VALUES(?,?)',(key,json.dumps(value,ensure_ascii=False)))
            for key,table in [('cursors','symbol_cursors'),('rounds','symbol_rounds'),('outbox','outbox')]:
                for ident,value in data[key].items():db.execute('INSERT OR REPLACE INTO '+table+' VALUES(?,?)',(ident,encoded(value).decode()))
            db.execute("INSERT OR REPLACE INTO meta VALUES('schema','\"b-accumulation-events-v3\"')")
            db.execute("INSERT OR REPLACE INTO migration_state VALUES('status','verified')")
            store._generation(db)
        with store.connection() as db:
            integrity=db.execute('PRAGMA integrity_check').fetchone()[0]
            if integrity!='ok':raise ValueError('迁移库完整性失败: '+integrity)
            count=db.execute('SELECT count(*) FROM events').fetchone()[0]
        if count!=len(data['events']):raise ValueError('事件数量对账失败')
        if source.exists() and hashlib.sha256(source.read_bytes()).hexdigest()!=digest:raise ValueError('迁移期间源变化，禁止切换')
        staging.replace(final)
        result=dict(schema='b-events-migration-v1',source_sha256=digest,events=count,integrity=integrity,verified_at=time.time(),backup=str(backup),original_preserved=True)
        atomic_json(root/'runtime'/'b-events-migration.json',result)
        return result

if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--root',required=True);parser.add_argument('--apply',action='store_true');args=parser.parse_args()
    if not args.apply:parser.error('显式迁移需要--apply；先完成一致备份和停写检查')
    print(json.dumps(migrate(args.root),ensure_ascii=False))
