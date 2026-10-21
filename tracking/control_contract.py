"""Additive lifecycle-control contract, separate from frozen market rules.

v1.2 introduces only source_revision_superseded: a corrected C cycle can retire
an obsolete ABC observation profile and open the corrected profile atomically. This
is neither a market invalidation nor a human stop. v1.1 remains unchanged.
"""
import copy
import re
from .chain_validate import judge

CONTROL_SCHEMA='tracking-control-schema-v1.2.0'
SUPERSEDED='source_revision_superseded'


def validate(kind,record,schema):
    if record.get('schema_version')!=CONTROL_SCHEMA:
        return judge.validate_record(kind,record,schema)
    extended=copy.deepcopy(schema)
    extended['$defs'][kind]['properties']['schema_version']['const']=CONTROL_SCHEMA
    if kind=='session':extended['$defs']['session']['properties']['end_reason']['enum'].append(SUPERSEDED)
    errors=judge.validate_record(kind,record,extended)
    if kind=='session' and record.get('strategy') in ('A','B','C'):
        base=f"{record.get('event_id')}|{record.get('tracking_profile_id')}|{record.get('tracking_profile_version')}"
        if re.fullmatch(re.escape(base)+r'\|source-revision\|[a-f0-9]{64}',record.get('session_id','')):
            errors=[e for e in errors if e['code']!='S01_session_id_composition']
    if kind=='transition' and record.get('reason')==SUPERSEDED and record.get('from_state') in ('observing','paused_awaiting_data') and record.get('to_state')=='ended':
        errors=[e for e in errors if e['code']!='T02_reason_not_allowed_for_transition']
    return errors


def observation_bar_source(bar):
    """Raw source changes, excluding rolling-window indicator seed drift."""
    keys=('open_ms','o','h','l','c','q','q_unit')
    if not all(k in bar for k in ('o','h','l','q')):
        keys+=('ema7','ema25','ema99')
    return {k:bar[k] for k in keys if k in bar}
