"""Local .env AI credentials, with compatibility for existing DPAPI vaults."""
from __future__ import annotations

import json
import os
import uuid
from pathlib import Path
from env_config import read_env, write_env_value


class CredentialError(RuntimeError):
    pass


def _dpapi_protect(data: bytes) -> bytes:
    if os.name != 'nt':
        raise CredentialError('AI凭证仅支持在本机Windows用DPAPI保护保存')
    import ctypes
    import ctypes.wintypes as wt

    class DATA_BLOB(ctypes.Structure):
        _fields_ = [('cbData', wt.DWORD), ('pbData', ctypes.POINTER(ctypes.c_char))]

    crypt32 = ctypes.windll.crypt32
    kernel32 = ctypes.windll.kernel32
    blob_in = DATA_BLOB(len(data), ctypes.create_string_buffer(data, len(data)))
    blob_out = DATA_BLOB()
    if not crypt32.CryptProtectData(ctypes.byref(blob_in), 'anomaly-workbench-ai', None, None, None, 0, ctypes.byref(blob_out)):
        raise CredentialError('DPAPI保护失败，密钥未写入')
    try:
        protected = ctypes.string_at(blob_out.pbData, blob_out.cbData)
    finally:
        kernel32.LocalFree(blob_out.pbData)
    return protected


def _dpapi_unprotect(data: bytes) -> bytes:
    if os.name != 'nt':
        raise CredentialError('AI凭证仅支持在本机Windows用DPAPI读取')
    import ctypes
    import ctypes.wintypes as wt

    class DATA_BLOB(ctypes.Structure):
        _fields_ = [('cbData', wt.DWORD), ('pbData', ctypes.POINTER(ctypes.c_char))]

    crypt32 = ctypes.windll.crypt32
    kernel32 = ctypes.windll.kernel32
    blob_in = DATA_BLOB(len(data), ctypes.create_string_buffer(data, len(data)))
    blob_out = DATA_BLOB()
    if not crypt32.CryptUnprotectData(ctypes.byref(blob_in), None, None, None, None, 0, ctypes.byref(blob_out)):
        raise CredentialError('DPAPI解密失败，请重新保存API Key')
    try:
        return ctypes.string_at(blob_out.pbData, blob_out.cbData)
    finally:
        kernel32.LocalFree(blob_out.pbData)


class AiCredentialStore:
    """Isolated credential files under runtime/credentials/; not served statically."""

    def __init__(self, root: Path):
        self.root = Path(root)
        self.dir = self.root / 'runtime' / 'credentials'
        self.meta_path = self.dir / 'ai-config.json'
        self.key_path = self.dir / 'ai-key.dpapi'
        self.dir.mkdir(parents=True, exist_ok=True)
        # Harden directory ACL best-effort; ignore if unsupported.
        try:
            os.chmod(self.dir, 0o700)
        except OSError:
            pass

    def load_meta(self) -> dict | None:
        if not self.meta_path.exists():
            return None
        return json.loads(self.meta_path.read_text(encoding='utf-8'))

    def has_key(self) -> bool:
        if (self.root / '.env').exists():
            return bool(read_env(self.root).get('AI_API_KEY', '').strip())
        return self.key_path.exists() and self.key_path.stat().st_size > 0

    def read_key(self) -> str:
        if not self.has_key():
            raise CredentialError('尚未配置API Key')
        if (self.root / '.env').exists():
            return read_env(self.root)['AI_API_KEY'].strip()
        return _dpapi_unprotect(self.key_path.read_bytes()).decode('utf-8')

    def write_key(self, key: str) -> None:
        if not isinstance(key, str) or not key.strip():
            raise ValueError('替换API Key时必须提供非空密钥')
        if (self.root / '.env').exists():
            write_env_value('AI_API_KEY', key.strip(), root=self.root)
            return
        protected = _dpapi_protect(key.strip().encode('utf-8'))
        tmp = self.key_path.with_suffix('.tmp')
        tmp.write_bytes(protected)
        tmp.replace(self.key_path)

    def clear_key(self) -> None:
        if (self.root / '.env').exists():
            write_env_value('AI_API_KEY', '', root=self.root)
        if self.key_path.exists():
            self.key_path.unlink()

    def save_meta(self, meta: dict) -> None:
        tmp = self.meta_path.with_suffix('.tmp')
        tmp.write_text(json.dumps(meta, ensure_ascii=False, indent=2, allow_nan=False), encoding='utf-8')
        tmp.replace(self.meta_path)

    def ensure_config_id(self, meta: dict | None) -> str:
        if meta and meta.get('configId'):
            return meta['configId']
        return 'ai-' + uuid.uuid4().hex[:12]
