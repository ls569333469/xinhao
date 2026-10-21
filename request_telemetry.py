"""Public HTTP attempts, including retries. Never persists query credentials."""
import collections,json,threading,time,uuid
from pathlib import Path
from urllib.parse import urlsplit
_lock=threading.Lock();_counts=collections.Counter();_path=None;_task=None;_task_counts=collections.Counter()
def begin_task(task_id,kind,target=None):
    global _task,_task_counts
    with _lock:
        _task=dict(task_id=task_id,kind=kind,target_close_boundary=target);_task_counts=collections.Counter()
def end_task(task_id):
    global _task
    with _lock:
        result=dict(_task_counts) if _task and _task['task_id']==task_id else {}
        if _task and _task['task_id']==task_id:_task=None
        return result
def configure(root):
    global _path
    _path=Path(root)/'runtime'/'request-telemetry'/('http-'+uuid.uuid4().hex+'.jsonl')
def record(url,params,attempt,status,elapsed,**extra):
    parsed=urlsplit(url);kind=str(status);provider=parsed.hostname or ''
    row=dict(at=time.time(),provider=provider,endpoint=parsed.path,attempt=attempt,status=status,elapsed_ms=round(elapsed*1000,3),**extra)
    # Only explicit public data keys. No query string, headers, API keys or body.
    row['scope']={k:v for k,v in (params or {}).items() if k in ('symbol','pair','interval','period','startTime','endTime','limit')}
    with _lock:
        if _task:
            row['task']=dict(_task);_task_counts['attempts']+=1;_task_counts['status:'+kind]+=1
            if attempt>1:_task_counts['retries']+=1
        _counts['attempts']+=1;_counts['status:'+kind]+=1;_counts['provider:'+provider]+=1
        if attempt>1:_counts['retries']+=1
        if _path:
            try:
                _path.parent.mkdir(parents=True,exist_ok=True)
                with _path.open('a',encoding='utf8') as stream:stream.write(json.dumps(row,ensure_ascii=False)+'\n')
            except OSError:
                _counts['telemetry_write_errors']+=1
                if _task:_task_counts['telemetry_write_errors']+=1
def snapshot():
    with _lock:return dict(_counts)

def task_snapshot():
    with _lock:return dict(_task_counts) if _task else {}
