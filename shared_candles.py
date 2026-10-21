"""Content-addressed immutable chunks; legacy documents remain byte-identical."""
import gzip,hashlib,json,uuid,os
from pathlib import Path
from durable_io import protection,atomic_json

def encoded(obj):return json.dumps(obj,ensure_ascii=False,indent=2,allow_nan=False).encode('utf-8')
def write(root,path,document):
    root=Path(root);rows=document['raw'];groups={}
    # UTC-aligned groups prevent each rolling window shifting every chunk.
    step={'1h':3600000,'4h':14400000,'1d':86400000}[document['interval']]
    for row in rows:groups.setdefault(int(row[0])//(step*64),[]).append(row)
    with protection(root):
        refs=[]
        for key,chunk in sorted(groups.items()):
            raw=encoded(chunk);ph=hashlib.sha256(raw).hexdigest();blob=gzip.compress(raw,mtime=0)
            rel='runtime/shared-candles/'+ph+'.json.gz';target=root/rel
            if not target.exists():
                target.parent.mkdir(parents=True,exist_ok=True);tmp=target.with_name(target.name+'.'+uuid.uuid4().hex+'.tmp')
                with tmp.open('xb') as output:output.write(blob);output.flush();os.fsync(output.fileno())
                tmp.replace(target)
            refs.append(dict(path=rel,payload_hash=ph,storage_hash=hashlib.sha256(target.read_bytes()).hexdigest(),count=len(chunk)))
        manifest={k:v for k,v in document.items() if k!='raw'}
        manifest['document_keys']=list(document)
        manifest.update(storage_format='shared-raw-v1',chunks=refs,document_hash=hashlib.sha256(encoded(document)).hexdigest())
        atomic_json(path,manifest)
    return manifest

def expand(path,manifest):
    path=Path(path).resolve();root=next((p for p in path.parents if (p/'runtime'/'shared-candles').is_dir()),None)
    if root is None:raise FileNotFoundError('shared candle root unavailable')
    rows=[]
    for ref in manifest['chunks']:
        target=(root/ref['path']).resolve();target.relative_to(root/'runtime'/'shared-candles')
        blob=target.read_bytes()
        if hashlib.sha256(blob).hexdigest()!=ref['storage_hash']:raise ValueError('shared storage hash mismatch')
        raw=gzip.decompress(blob)
        if hashlib.sha256(raw).hexdigest()!=ref['payload_hash']:raise ValueError('shared payload hash mismatch')
        rows.extend(json.loads(raw))
    obj={k:(rows if k=='raw' else manifest[k]) for k in manifest['document_keys']}
    # Document key order is irrelevant to the value contract. Verify canonical hash.
    if hashlib.sha256(encoded(obj)).hexdigest()!=manifest['document_hash']:raise ValueError('shared document hash mismatch')
    return obj
