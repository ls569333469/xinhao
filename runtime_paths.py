"""Code resources stay versioned; explicit data roots must already be prepared."""
import os
from pathlib import Path
CODE_ROOT=Path(__file__).resolve().parent

def data_root():
    value=os.environ.get('ABC_DATA_ROOT')
    if not value:return CODE_ROOT
    root=Path(value).expanduser().resolve()
    if not root.is_dir() or not (root/'runtime'/'data-root.json').is_file():
        raise ValueError('ABC_DATA_ROOT must contain prepared runtime/data-root.json; refusing an empty/new data directory')
    import json
    marker=json.loads((root/'runtime'/'data-root.json').read_text(encoding='utf-8-sig'))
    if marker.get('schema')!='abc-data-root-v1':raise ValueError('data_root_marker_schema')
    return root

class HostLease:
    """OS-held exclusive lock fences alternate ports/code versions; crash releases."""
    def __init__(self,root):self.path=Path(root)/'runtime'/'host.lock';self.stream=None
    def __enter__(self):
        self.path.parent.mkdir(parents=True,exist_ok=True)
        self.stream=self.path.open('a+b')
        try:
            if os.name=='nt':
                import msvcrt
                if self.path.stat().st_size==0:self.stream.write(b'0');self.stream.flush()
                self.stream.seek(0);msvcrt.locking(self.stream.fileno(),msvcrt.LK_NBLCK,1)
            else:
                import fcntl
                fcntl.flock(self.stream.fileno(),fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BaseException:
            self.stream.close();self.stream=None;raise RuntimeError('data_root_already_owned')
        return self
    def __exit__(self,*exc):
        if self.stream:self.stream.close();self.stream=None
