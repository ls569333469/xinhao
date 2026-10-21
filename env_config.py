"""Local .env credentials, without dependencies, interpolation or secret logging."""
from __future__ import annotations

import json
import os
import re
import threading
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parent
_LOCK = threading.RLock()
_NAME = re.compile(r'[A-Za-z_][A-Za-z0-9_]*\Z')


def read_env(root=None):
    path = Path(root or ROOT) / '.env'
    if not path.exists():
        return {}
    result = {}
    for number, line in enumerate(path.read_text(encoding='utf-8-sig').splitlines(), 1):
        line = line.strip()
        if not line or line.startswith('#'):
            continue
        if line.startswith('export '):
            line = line[7:].lstrip()
        name, sep, value = line.partition('=')
        name, value = name.strip(), value.strip()
        if not sep or not _NAME.fullmatch(name) or name in result:
            raise ValueError(f'.env 第 {number} 行变量名无效或重复')
        if value.startswith('"'):
            try:
                value, end = json.JSONDecoder().raw_decode(value)
                if not isinstance(value, str) or value is None:
                    raise ValueError()
                trailing = line.partition('=')[2].strip()[end:].strip()
                if trailing and not trailing.startswith('#'):
                    raise ValueError()
            except (ValueError, TypeError):
                raise ValueError(f'.env 第 {number} 行双引号格式无效') from None
        elif value.startswith("'"):
            end = value.find("'", 1)
            if end < 0 or (value[end+1:].strip() and not value[end+1:].strip().startswith('#')):
                raise ValueError(f'.env 第 {number} 行单引号格式无效')
            value = value[1:end]
        else:
            value = re.split(r'\s+#', value, maxsplit=1)[0].rstrip()
        result[name] = value
    return result


def env_get(name, default=None, *, root=None):
    """A present .env entry, including an empty entry, overrides inherited env."""
    values = read_env(root)
    return values[name] if name in values else os.environ.get(name, default)


def write_env_value(name, value, *, root=None):
    if not _NAME.fullmatch(name) or not isinstance(value, str) or '\n' in value or '\r' in value or '\0' in value:
        raise ValueError('凭证变量名或值无效')
    path = Path(root or ROOT) / '.env'
    with _LOCK:
        read_env(path.parent)  # refuse malformed files rather than discarding content
        lines = path.read_text(encoding='utf-8-sig').splitlines() if path.exists() else []
        entry = name + '=' + json.dumps(value, ensure_ascii=False)
        for i, line in enumerate(lines):
            if re.match(r'^\s*(?:export\s+)?' + re.escape(name) + r'\s*=', line):
                lines[i] = entry
                break
        else:
            lines.append(entry)
        tmp = path.with_name('.env.write-' + uuid.uuid4().hex + '.tmp')
        try:
            with tmp.open('x', encoding='utf-8', newline='\n') as handle:
                handle.write('\n'.join(lines) + '\n')
                handle.flush()
                os.fsync(handle.fileno())
            os.chmod(tmp, 0o600)
            tmp.replace(path)
        finally:
            tmp.unlink(missing_ok=True)
