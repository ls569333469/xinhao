"""Read-only reuse of a source-bound, precomputed presentation upgrade.

Old scans and strategy ledgers are never rewritten. Later scans compute their
own v1.2 observations normally and never inherit this older scan's results.
"""
import json
from functools import lru_cache
from pathlib import Path

PATH='runtime/ema-long-observations-bootstrap.json'

@lru_cache(maxsize=2)
def _read(path,size,stamp):
    if size>16*1024*1024:raise ValueError('observation_bootstrap_size')
    return json.loads(Path(path).read_text(encoding='utf8'))

def enrich(rows,root,*,scan_id,scan_sha256):
    path=Path(root)/PATH
    if not path.is_file():return 0
    try:
        stat=path.stat();data=_read(str(path),stat.st_size,stat.st_mtime_ns)
        if data.get('schema')!='ema-observation-bootstrap-v1' or data.get('scan_id')!=scan_id or data.get('scan_sha256')!=scan_sha256:return 0
        evidence={(x['market'],x['symbol']):x for x in data['rows']}
        n=0
        for row in rows:
            item=evidence.get((row.get('market'),row.get('symbol')))
            if not item or item['archive']!=(row.get('candleArchives') or {}).get('4h') or item['candleAsOf']!=row.get('candleAsOf'):continue
            if item['observation'].get('schema')!='ema-observation-v1.2':continue
            row['emaObservation']=dict(item['observation'],derived_at_read=True,bootstrap_source_bound=True);n+=1
        return n
    except (OSError,ValueError,KeyError,TypeError):return 0
