"""One original data lane: pause/budget guard shared by its HTTP threads."""
import threading,time
_lock=threading.Lock();_gate=None
class CollectionStopped(RuntimeError):pass

def install(task_id,check,*,attempts,seconds):
    global _gate
    with _lock:
        if _gate is not None:raise CollectionStopped('collection_lane_already_owned')
        _gate=dict(task_id=task_id,check=check,remaining=attempts,deadline=time.monotonic()+seconds,used=0)

def before_send(*,reserve=True):
    with _lock:
        gate=_gate
        if gate is None:return
        if time.monotonic()>=gate['deadline']:raise CollectionStopped('collection_deadline_exhausted')
        if not gate['check']():raise CollectionStopped('collection_paused_before_send')
        if reserve:
            if gate['remaining']<=0:raise CollectionStopped('collection_http_budget_exhausted')
            gate['remaining']-=1;gate['used']+=1

def release(task_id):
    global _gate
    with _lock:
        if _gate and _gate['task_id']==task_id:
            result={k:_gate[k] for k in ('task_id','remaining','used')};_gate=None;return result
    return None
