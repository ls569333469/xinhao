"""Bounded project-local notification delivery; no external transport."""
import json

def page(repo,*,before_id=None,limit=20):
    if type(limit) is not int or not 1<=limit<=50:raise ValueError('notification_limit')
    if before_id is not None and (type(before_id) is not int or before_id<1):raise ValueError('notification_cursor')
    rows=repo.read("SELECT outbox_id,kind,status,created_at_utc,delivered_at_utc,read_at_utc,json_extract(payload_json,'$.event_id') AS event_id,json_extract(payload_json,'$.state') AS state,json_extract(payload_json,'$.reason') AS reason,json_extract(payload_json,'$.status') AS original_status,json_extract(payload_json,'$.delayed') AS delayed FROM outbox WHERE outbox_id<? ORDER BY outbox_id DESC LIMIT ?",(before_id or 9223372036854775807,limit+1))
    items=[dict(r) for r in rows[:limit]]
    return dict(ok=True,items=items,next_cursor=dict(before_id=items[-1]['outbox_id']) if len(rows)>limit else None,transport='project_local',receipt_is_not_user_read=True)

def acknowledge(ledger,*,ids,action):
    if not isinstance(ids,list) or not 1<=len(ids)<=50 or any(type(i) is not int or i<1 for i in ids):raise ValueError('notification_ids_1_50')
    if action not in ('delivered','read'):raise ValueError('notification_action')
    if len(set(ids))!=len(ids):raise ValueError('notification_duplicate_ids')
    # Each notice has its own durable idempotent ACK. Never relabel unread as read.
    fn=ledger.mark_outbox_delivered if action=='delivered' else ledger.mark_outbox_read
    items=[fn(i) for i in ids]
    return dict(ok=all(x.get('ok') for x in items),items=items,action=action)
