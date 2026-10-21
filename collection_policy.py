"""Versioned collection budgets. Reading settings never enables a worker."""
import copy

DEFAULT = dict(schema_version=1, recoveryEnabled=False, maxHttpAttempts=6000,
               deadlineSeconds=1800, recoveryWindowHours=48,
               recoveryAttempts=16, sampleCaptureEnabled=True,
               maxSampleBytes=134217728)

def validate(value):
    if not isinstance(value,dict) or set(value)!=set(DEFAULT):raise ValueError('collection字段不完整或含未知字段')
    for key in ('recoveryEnabled','sampleCaptureEnabled'):
        if type(value[key]) is not bool:raise ValueError('collection.'+key+'须布尔')
    for key,lo,hi in [('schema_version',1,1),('maxHttpAttempts',1,100000),
                      ('deadlineSeconds',30,7200),('recoveryWindowHours',1,720),
                      ('recoveryAttempts',1,16),('maxSampleBytes',1048576,1073741824)]:
        if type(value[key]) is not int or not lo<=value[key]<=hi:raise ValueError('collection.'+key+'超出范围')
    return copy.deepcopy(value)

def read(root):
    import sqlite3
    from pathlib import Path
    path=Path(root)/'runtime/workbench.sqlite3'
    if not path.is_file():return dict(DEFAULT)
    db=sqlite3.connect(path.resolve().as_uri()+'?mode=ro',uri=True,timeout=2)
    try:
        if 'collection' not in {r[1] for r in db.execute('PRAGMA table_info(settings)')}:return dict(DEFAULT)
        row=db.execute('SELECT collection FROM settings WHERE id=1').fetchone()
        if not row or not row[0]:return dict(DEFAULT)
        import json
        return validate(json.loads(row[0]))
    finally:db.close()
