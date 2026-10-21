"""Daily immutable specimens, separately reviewed expectations; never signals."""
from pathlib import Path
import gzip,hashlib,json,sqlite3
from contextlib import contextmanager

def canonical(value):return json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':'),allow_nan=False)
@contextmanager
def connect(root):
    path=Path(root)/'runtime/strategy-cases.sqlite3';path.parent.mkdir(parents=True,exist_ok=True)
    db=sqlite3.connect(path,timeout=3);db.row_factory=sqlite3.Row
    try:
        db.executescript('''CREATE TABLE IF NOT EXISTS case_blobs(sha TEXT PRIMARY KEY,body BLOB NOT NULL,bytes INTEGER NOT NULL);
        CREATE TABLE IF NOT EXISTS strategy_cases(case_id TEXT PRIMARY KEY,strategy TEXT,contract TEXT,boundary_ms INTEGER,input_sha TEXT,blob_sha TEXT,provenance TEXT,state TEXT DEFAULT 'pending_review',expected TEXT,expected_basis TEXT,collected_at TEXT,UNIQUE(strategy,contract,boundary_ms,input_sha));
        CREATE INDEX IF NOT EXISTS case_page ON strategy_cases(strategy,boundary_ms,case_id);
        CREATE TABLE IF NOT EXISTS case_stats(id INTEGER PRIMARY KEY CHECK(id=1),bytes INTEGER NOT NULL,cases INTEGER NOT NULL);
        INSERT OR IGNORE INTO case_stats VALUES(1,0,0);''')
        with db:yield db
    finally:db.close()

def capture(root,*,strategy,market,symbol,boundary_ms,inputs,provenance,clock_iso):
    from collection_policy import read
    policy=read(root)
    if not policy['sampleCaptureEnabled']:return dict(captured=0,disabled=True)
    if strategy not in ('A','B','C') or type(boundary_ms) is not int:raise ValueError('case_identity')
    provenance=dict(provenance,rule_version={'A':'ema-squeeze-v4','B':'b-accum-1h-v1','C':'c-1015-v1.1'}[strategy])
    text=canonical(inputs).encode()
    if len(text)>4*1024*1024:raise ValueError('case_input_page_required')
    digest=hashlib.sha256(text).hexdigest();packed=gzip.compress(text,mtime=0)
    key=hashlib.sha256(canonical([strategy,market,symbol,boundary_ms,digest]).encode()).hexdigest()
    with connect(root) as db:
        db.execute('BEGIN IMMEDIATE')
        if db.execute('SELECT 1 FROM strategy_cases WHERE case_id=?',(key,)).fetchone():return dict(captured=0,reused=True,case_id=key)
        stats=db.execute('SELECT * FROM case_stats WHERE id=1').fetchone()
        extra=0 if db.execute('SELECT 1 FROM case_blobs WHERE sha=?',(digest,)).fetchone() else len(packed)
        if stats['bytes']+extra+1024>policy['maxSampleBytes']:return dict(captured=0,reason='case_capacity_reached')
        db.execute('INSERT OR IGNORE INTO case_blobs VALUES(?,?,?)',(digest,packed,len(packed)))
        db.execute('INSERT INTO strategy_cases(case_id,strategy,contract,boundary_ms,input_sha,blob_sha,provenance,collected_at) VALUES(?,?,?,?,?,?,?,?)',(key,strategy,market+'|'+symbol,boundary_ms,digest,digest,canonical(provenance),clock_iso))
        db.execute('UPDATE case_stats SET bytes=bytes+?,cases=cases+1 WHERE id=1',(extra+1024,))
    return dict(captured=1,case_id=key,state='pending_review')

def review(root,case_id,*,expected,basis):
    if not isinstance(expected,dict) or not isinstance(expected.get('steps'),list) or not expected['steps'] or not isinstance(basis,str) or not basis.strip():raise ValueError('case_independent_expectations_required')
    encoded=canonical(expected)
    if len(encoded.encode())>262144:raise ValueError('case_expectations_bound')
    with connect(root) as db:
        row=db.execute('SELECT state,expected,expected_basis FROM strategy_cases WHERE case_id=?',(case_id,)).fetchone()
        if not row:raise ValueError('case_missing')
        if row['state']=='reviewed' and (row['expected']!=encoded or row['expected_basis']!=basis):raise ValueError('case_expected_immutable_create_new_revision')
        db.execute("UPDATE strategy_cases SET state='reviewed',expected=?,expected_basis=? WHERE case_id=?",(encoded,basis,case_id))

def load(root,case_id):
    path=Path(root)/'runtime/strategy-cases.sqlite3'
    db=sqlite3.connect(path.resolve().as_uri()+'?mode=ro',uri=True,timeout=3);db.row_factory=sqlite3.Row
    try:
        row=db.execute('SELECT c.*,b.body FROM strategy_cases c JOIN case_blobs b ON b.sha=c.blob_sha WHERE case_id=?',(case_id,)).fetchone()
        if not row:raise ValueError('case_missing')
        data=dict(row);text=gzip.decompress(data.pop('body'))
        if hashlib.sha256(text).hexdigest()!=data['input_sha']:raise ValueError('case_hash_mismatch')
        data['inputs']=json.loads(text);data['expected']=json.loads(data['expected']) if data['expected'] else None
        return data
    finally:db.close()

def capture_publication(root,*,row,inputs,publication,clock_iso):
    stage=row.get('emaSqueezeStage') or {}
    important=bool(stage.get('candidateStatus')=='active' or stage.get('confirmedEvents') or stage.get('crossConfirm'))
    # Fixed thinning for ordinary negatives; all retained specimens need review.
    sample=int(hashlib.sha256((row['market']+'|'+row['symbol']).encode()).hexdigest()[:8],16)%256==0
    from .c_rebound_rules import prepare_bars
    from .tracking_engine import milliseconds
    target=row['candleAsOf']+1
    bars=prepare_bars(inputs['raw_bars'],target_close_ms=target)
    c_interest=bool(bars and bars[-1].get('rsi14') is not None and bars[-1]['rsi14']<25)
    if not important and not c_interest and not sample:return dict(captured=0,reason='not_selected')
    payload=dict(row=row,input_available_at_ms=milliseconds(publication['available_at']),**inputs)
    return {s:capture(root,strategy=s,market=row['market'],symbol=row['symbol'],boundary_ms=target,inputs=payload,provenance=dict(kind='real_archived_publication',publication=publication,expected_not_inferred=True),clock_iso=clock_iso) for s,selected in [('A',important or sample),('C',c_interest or sample)] if selected}

def _diagnostic(root,error):
    # One bounded diagnostic. Sample capture must never turn a valid scan into
    # a failed collection or prevent the real strategy transaction.
    try:
        from durable_io import atomic_json
        atomic_json(Path(root)/'runtime/case-capture-status.json',dict(ok=False,error=type(error).__name__+':'+str(error)[:300]))
    except Exception:pass

def capture_safe(root,**kw):
    try:return capture(root,**kw)
    except Exception as exc:_diagnostic(root,exc);return dict(captured=0,error=str(exc)[:300])

def capture_publication_safe(root,**kw):
    try:return capture_publication(root,**kw)
    except Exception as exc:_diagnostic(root,exc);return dict(captured=0,error=str(exc)[:300])

def asof_inputs(inputs,boundary,as_of):
    """Closed roots AND availability fence enforced before any engine adapter."""
    if type(as_of) is not int or as_of<boundary:raise ValueError('case_asof_before_boundary')
    data=json.loads(canonical(inputs))
    acquired=data.get('input_available_at_ms')
    if acquired is not None and acquired>as_of:raise ValueError('case_not_yet_available')
    for field,step,key in [('raw_bars',14400000,'open_ms'),('raw4h',14400000,None),('bars_asc',3600000,'open_time')]:
        if field in data:
            data[field]=[b for b in data[field] if int(b[0] if key is None else b[key])+step<=boundary and (not isinstance(b,dict) or b.get('available_at_ms',acquired or 0)<=as_of)]
    for field in ('oi_samples','lsr_samples','caps'):
        if field in data:data[field]=[s for s in data[field] if s['effective_at_ms']<=boundary and s['available_at_ms']<=as_of]
    ev=data.get('evidence') or {}
    for field in ('oi_samples','lsr_samples'):
        if field in ev:ev[field]=[s for s in ev[field] if int(s['timestamp'])<=boundary and int(s.get('_first_retrieved_at',acquired or as_of+1))<=as_of]
    if 'cap_samples' in ev:
        from b_evidence import ms
        ev['cap_samples']=[c for c in ev['cap_samples'] if (ms(c.get('capObservedAt')) or as_of+1)<=boundary and (ms(c.get('capFirstRetrievedAt')) or as_of+1)<=as_of]
    return data

def replay(root,case_id,*,engines=None):
    """Caller binds ORIGINAL producer adapters; no default network/production DB."""
    case=load(root,case_id)
    if case['state']!='reviewed':raise ValueError('case_pending_review')
    import tempfile
    from .case_replay import OriginalEngines
    steps=case['expected']['steps'];actual=[];last=-1
    with tempfile.TemporaryDirectory(prefix='abc-case-') as folder:
        original=OriginalEngines(Path(folder),case)
        for step in steps:
            boundary=step.get('boundary_ms')
            if type(boundary) is not int or boundary<=last:raise ValueError('case_step_order')
            last=boundary;as_of=step.get('as_of_ms',boundary)
            inp=asof_inputs(case['inputs'],boundary,as_of)
            value=engines[case['strategy']](inp,boundary) if engines else original.evaluate(inp,boundary,as_of)
            actual.append(value)
            if 'assertions' in step:
                if not isinstance(step['assertions'],dict) or not step['assertions']:raise ValueError('case_assertions_empty')
                for path,wanted in step['assertions'].items():
                    got=value
                    try:
                        for part in path.split('.'):got=got[int(part)] if isinstance(got,list) else got[part]
                    except (KeyError,IndexError,TypeError,ValueError):got={'missing_path':path}
                    if got!=wanted:return dict(ok=False,case_id=case_id,first_divergence_ms=boundary,path=path,expected=wanted,actual=got)
            elif value!=step['result']:return dict(ok=False,case_id=case_id,first_divergence_ms=boundary,expected=step['result'],actual=value)
    return dict(ok=True,case_id=case_id,steps=len(actual),input_sha=case['input_sha'])
