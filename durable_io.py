"""Atomic JSON and an inter-process protection lock shared by evidence writers/cleanup."""
import contextlib, json, os, threading, time, uuid
from pathlib import Path
LOCK = threading.RLock()
LOCAL=threading.local()
@contextlib.contextmanager
def protection(root):
    key=str(Path(root).resolve())
    held=getattr(LOCAL,'held',set())
    if key in held:
        yield
        return
    path=Path(root)/'runtime'/'evidence.lock'
    path.parent.mkdir(parents=True,exist_ok=True)
    with LOCK, path.open('a+b') as f:
        f.seek(0,2)
        if f.tell()==0: f.write(b'0');f.flush()
        f.seek(0)
        if os.name=='nt':
            import msvcrt
            deadline=time.monotonic()+60
            while True:
                try:msvcrt.locking(f.fileno(),msvcrt.LK_NBLCK,1);break
                except OSError:
                    if time.monotonic()>=deadline:raise TimeoutError('证据锁等待超时')
                    time.sleep(.05)
        else:
            import fcntl
            fcntl.flock(f,fcntl.LOCK_EX)
        LOCAL.held=held|{key}
        try:yield
        finally:
            LOCAL.held=held
            f.seek(0)
            if os.name=='nt':msvcrt.locking(f.fileno(),msvcrt.LK_UNLCK,1)
            else:fcntl.flock(f,fcntl.LOCK_UN)
def atomic_json(path,value):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    temp=path.with_name(path.name+'.'+uuid.uuid4().hex+'.tmp')
    try:
        with temp.open('x',encoding='utf-8',newline='\n') as f:
            json.dump(value,f,ensure_ascii=False,sort_keys=True,allow_nan=False);f.flush();os.fsync(f.fileno())
        replace_atomic(temp,path)
    finally:temp.unlink(missing_ok=True)

def replace_atomic(source,destination,budget=2.0):
    """Retry sharing violations, never blanket-retry ACL access denied."""
    deadline=time.monotonic()+budget;attempt=0
    while True:
        try:return Path(source).replace(destination)
        except OSError as exc:
            conflict=getattr(exc,'winerror',None) in (32,33)
            if not conflict or time.monotonic()>=deadline:raise
            attempt+=1;time.sleep(min(.025*2**min(attempt,4),max(0,deadline-time.monotonic())))

def read_bytes_shared(path):
    """Windows readers allow an atomic publisher to replace the old inode."""
    if os.name!='nt':return Path(path).read_bytes()
    import ctypes,msvcrt
    from ctypes import wintypes
    create=ctypes.WinDLL('kernel32',use_last_error=True).CreateFileW
    create.argtypes=[wintypes.LPCWSTR,wintypes.DWORD,wintypes.DWORD,wintypes.LPVOID,wintypes.DWORD,wintypes.DWORD,wintypes.HANDLE];create.restype=wintypes.HANDLE
    handle=create(str(Path(path)),0x80000000,7,None,3,0x80,None)
    if handle==wintypes.HANDLE(-1).value:raise ctypes.WinError(ctypes.get_last_error())
    try:fd=msvcrt.open_osfhandle(handle,os.O_RDONLY|os.O_BINARY)
    except BaseException:
        ctypes.windll.kernel32.CloseHandle(wintypes.HANDLE(handle));raise
    with os.fdopen(fd,'rb') as stream:return stream.read()


def protected(method):
    """Publishers participate in the same evidence/cleanup critical section."""
    import functools
    @functools.wraps(method)
    def wrapped(self,*args,**kwargs):
        with protection(self if isinstance(self,(str,Path)) else self.root):return method(self,*args,**kwargs)
    return wrapped
