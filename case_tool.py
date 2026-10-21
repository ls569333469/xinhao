"""Explicit local sample review/replay. Never starts scans or fetches input."""
import argparse,json,sqlite3
from pathlib import Path

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data-root',type=Path,required=True)
    sub=parser.add_subparsers(dest='command',required=True)
    ls=sub.add_parser('list');ls.add_argument('--after',default='');ls.add_argument('--limit',type=int,default=50)
    review=sub.add_parser('review');review.add_argument('case_id');review.add_argument('--expected',type=Path,required=True);review.add_argument('--basis',required=True)
    replay=sub.add_parser('replay');replay.add_argument('case_id')
    sub.add_parser('seed-reference',help='Explicitly save synthetic rule fixtures; no market requests')
    a=parser.parse_args()
    from tracking import case_library as lib
    if a.command=='list':
        if not 1<=a.limit<=100:parser.error('limit must be 1..100')
        path=(a.data_root/'runtime/strategy-cases.sqlite3').resolve()
        if not path.is_file():out=dict(items=[],next_cursor=None)
        else:
            db=sqlite3.connect(path.as_uri()+'?mode=ro',uri=True,timeout=3);db.row_factory=sqlite3.Row
            try:
                rows=[dict(r) for r in db.execute('SELECT case_id,strategy,contract,boundary_ms,input_sha,state,collected_at FROM strategy_cases WHERE case_id>? ORDER BY case_id LIMIT ?',(a.after,a.limit))]
                out=dict(items=rows,next_cursor=rows[-1]['case_id'] if len(rows)==a.limit else None)
            finally:db.close()
    elif a.command=='seed-reference':
        from tracking.reference_cases import seed
        out=dict(items=seed(a.data_root),synthetic=True)
    elif a.command=='review':
        lib.review(a.data_root,a.case_id,expected=json.loads(a.expected.read_text(encoding='utf-8-sig')),basis=a.basis);out=dict(reviewed=a.case_id)
    else:out=lib.replay(a.data_root,a.case_id)
    print(json.dumps(out,ensure_ascii=False,indent=2))
if __name__=='__main__':main()
