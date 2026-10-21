"""Small read-only collection/recovery progress. No worker/bootstrap side effects."""
import json,sqlite3
from pathlib import Path
from collection_policy import read

def status(root):
    result=dict(policy=read(root),initialized=False,source_counts=[],b_replay_counts=[])
    path=Path(root)/'runtime/signal-tracking.sqlite3'
    if not path.is_file():return result
    db=sqlite3.connect(path.resolve().as_uri()+'?mode=ro',uri=True,timeout=2);db.row_factory=sqlite3.Row
    try:
        db.execute('BEGIN')
        names={r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name IN ('meta','recovery_quality_counts','recovery_b_replays')")}
        result['initialized']=True
        if 'meta' in names:
            values=dict(db.execute("SELECT key,value FROM meta WHERE key IN ('collection_last_recovery','recovery_commit_seq','recovery_as_of_ms','collection_projection','collection_effective_projection','collection_local_archive_count')"))
            result['recovery_commit_seq']=values.get('recovery_commit_seq')
            result['as_of_ms']=int(values.get('recovery_as_of_ms') or 0) or None
            if values.get('collection_last_recovery'):
                last=json.loads(values['collection_last_recovery']);result['last']=dict(at_ms=last['at_ms'],pages=len(last.get('pages',[])),b_ok=last.get('b_replay',{}).get('ok'),effects_ok=last.get('effects',{}).get('ok'),effects=last.get('effects',{}).get('items',[]),effects_pending=sum(bool(x.get('pending')) for x in last.get('effects',{}).get('items',[])),network_scope='saved_policy_fixed_jobs',local_evidence=last.get('local_evidence'),identity_sync=last.get('identity_sync'),historical_cycles=last.get('historical_cycles'))
            result['local_archived_pages']=int(values.get('collection_local_archive_count') or 0)
            result['effective']=json.loads(values['collection_effective_projection']) if values.get('collection_effective_projection') else None
            result['projection']=json.loads(values['collection_projection']) if values.get('collection_projection') else None
        if 'recovery_quality_counts' in names:
            result['source_counts']=[dict(r) for r in db.execute('SELECT source,state,SUM(n) AS count FROM recovery_quality_counts WHERE n>0 GROUP BY source,state ORDER BY source,state LIMIT 64')]
        if 'recovery_b_replays' in names:
            result['b_replay_counts']=[dict(r) for r in db.execute('SELECT state,COUNT(*) AS count FROM recovery_b_replays GROUP BY state ORDER BY state LIMIT 16')]
        return result
    finally:db.close()
