"""Lossless rollback bridge. Export new events/revisions before reverting code.

The production service must be stopped for a rollback switch. This command only
exports a separately named file; it never deletes or replaces the active ledger.
"""
import argparse,hashlib,json,os
from pathlib import Path
from b_event_sqlite import SQLiteBStore,encoded
from durable_io import protection,atomic_json

def export_legacy(root,destination):
    root=Path(root);destination=Path(destination)
    if destination.exists():raise FileExistsError('拒绝覆盖已有回退文件: '+str(destination))
    if destination.resolve()==(root/'runtime'/'b-accumulation-events.json').resolve():raise ValueError('先导出并对账，不能直接覆盖活动账本')
    with protection(root):
        store=SQLiteBStore(root)
        with store.connection() as db:
            data={r['key']:json.loads(r['value']) for r in db.execute('SELECT * FROM meta')}
            data.pop('generation',None)
            data['schema']='b-accumulation-events-v2'
            memo={};data['events']={r['event_id']:store._event(db,r,memo=memo) for r in db.execute('SELECT * FROM events')}
            for key,table in [('cursors','symbol_cursors'),('rounds','symbol_rounds'),('outbox','outbox')]:
                data[key]={r['key']:json.loads(r['payload']) for r in db.execute('SELECT * FROM '+table)}
            # DB-only replay bookkeeping remains in the retained SQLite backup.
            generation=db.execute("SELECT value FROM meta WHERE key='generation'").fetchone()[0]
        atomic_json(destination,data)
        result=json.loads(destination.read_text(encoding='utf8'))
        if encoded(result)!=encoded(data):raise ValueError('回退导出字段不一致；禁止切换')
        return dict(path=str(destination),generation=generation,event_count=len(data['events']),
                    revisions=sum(len(e.get('revisions',[])) for e in data['events'].values()),
                    outbox=len(data['outbox']),sha256=hashlib.sha256(destination.read_bytes()).hexdigest(),active_ledger_unchanged=True)

if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root',required=True);parser.add_argument('--destination',required=True)
    args=parser.parse_args();print(json.dumps(export_legacy(args.root,args.destination),ensure_ascii=False))
