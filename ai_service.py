"""AI provider config, protected keys, and explicit capability tests. No auto calls."""
from __future__ import annotations

import copy
import datetime as dt
import json
import sqlite3
import threading
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import urlparse

from ai_credentials import AiCredentialStore, CredentialError

UTC = dt.timezone.utc
DEFAULT_AI = {
    'provider': 'apikey',
    'protocol': 'chat',
    'baseUrl': 'https://api.apikey.fan/v1',
    'modelId': '',
    'timeoutSec': 120,
    'maxRetries': 2,
    'tasks': {'research': True, 'draft': True, 'review': True},
}
ALLOWED_PROVIDERS = {'apikey', 'custom', 'openai', 'anthropic'}
KNOWN_MODELS = {
    'apikey': [
        {'id': 'gpt-6.1-sol', 'label': '6.1 Sol', 'recommended': True},
        {'id': 'gpt-6-astra', 'label': '6 Astra'},
        {'id': 'gpt-6-sol', 'label': '6 Sol'},
        {'id': 'gpt-5.4', 'label': 'GPT-5.4'},
        {'id': 'gpt-5.2', 'label': 'GPT-5.2'},
        {'id': 'gpt-4.1', 'label': 'GPT-4.1'},
        {'id': 'gpt-4o', 'label': 'GPT-4o'},
    ],
    'openai': [
        {'id': 'gpt-4.1', 'label': 'GPT-4.1'},
        {'id': 'gpt-4o', 'label': 'GPT-4o'},
    ],
    'anthropic': [],
    'custom': [],
}
# Chat Completions + Responses (APIKEY.FAN / OpenAI Responses).
IMPLEMENTED_PROTOCOLS = {'chat', 'responses'}
CAP_STATUSES = ('unverified', 'success', 'failed', 'unsupported')


def iso(value=None):
    value = value or dt.datetime.now(UTC)
    return value.astimezone(UTC).isoformat(timespec='microseconds')


class Conflict(ValueError):
    pass


def normalize_base_url(url: str) -> str:
    if not isinstance(url, str) or not url.strip():
        raise ValueError('Base URL不能为空')
    raw = url.strip()
    parsed = urlparse(raw)
    if parsed.scheme != 'https' or not parsed.netloc:
        raise ValueError('仅允许显式配置的HTTPS供应商地址')
    host = parsed.hostname or ''
    if host in ('localhost', '127.0.0.1', '::1') or host.endswith('.local'):
        raise ValueError('不允许把本机或内网地址当作凭证发送目标')
    # Collapse accidental duplicated /v1 suffixes.
    path = parsed.path.rstrip('/')
    while path.endswith('/v1/v1'):
        path = path[:-3]
    if path.endswith('/v1'):
        path = path
    elif path in ('', '/'):
        path = '/v1'
    else:
        # Keep custom gateway paths; do not auto-append another /v1 if user already set one segment.
        pass
    rebuilt = f'https://{parsed.netloc}{path}'
    return rebuilt.rstrip('/')


def validate_ai_config(value: dict) -> dict:
    if not isinstance(value, dict):
        raise ValueError('AI配置必须是对象')
    required = set(DEFAULT_AI)
    extra = set(value) - required
    missing = required - set(value)
    if missing or extra:
        raise ValueError('AI配置字段必须恰好包含provider/protocol/baseUrl/modelId/timeoutSec/maxRetries/tasks')
    if value['provider'] not in ALLOWED_PROVIDERS:
        raise ValueError('不支持的服务商预设')
    if value['protocol'] not in IMPLEMENTED_PROTOCOLS:
        raise ValueError('当前只实现OpenAI兼容Chat Completions；其他协议标未实现并禁用')
    base = normalize_base_url(value['baseUrl'])
    if not isinstance(value['modelId'], str):
        raise ValueError('模型ID必须是字符串')
    model = value['modelId'].strip()
    if type(value['timeoutSec']) is not int or not 5 <= value['timeoutSec'] <= 300:
        raise ValueError('超时须为5–300秒整数')
    if type(value['maxRetries']) is not int or not 0 <= value['maxRetries'] <= 5:
        raise ValueError('最大重试须为0–5整数')
    tasks = value['tasks']
    if not isinstance(tasks, dict) or set(tasks) != {'research', 'draft', 'review'}:
        raise ValueError('tasks须包含research/draft/review')
    if any(type(tasks[k]) is not bool for k in tasks):
        raise ValueError('任务绑定须为布尔值')
    return {
        'provider': value['provider'],
        'protocol': value['protocol'],
        'baseUrl': base,
        'modelId': model,
        'timeoutSec': value['timeoutSec'],
        'maxRetries': value['maxRetries'],
        'tasks': copy.deepcopy(tasks),
    }


class AiService:
    def __init__(self, root, clock=None, opener=None):
        self.root = Path(root)
        self.clock = clock or (lambda: dt.datetime.now(UTC))
        self.opener = opener  # injectable for tests: callable(Request, timeout) -> http response-like
        self.lock = threading.RLock()
        self.store = AiCredentialStore(self.root)
        self.path = self.root / 'runtime' / 'workbench.sqlite3'
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._db() as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS ai_settings (
                    id INTEGER PRIMARY KEY CHECK(id=1),
                    version INTEGER NOT NULL,
                    config TEXT NOT NULL,
                    config_id TEXT NOT NULL,
                    capabilities TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
            ''')
            now = iso(self.clock())
            caps = json.dumps({
                'auth': {'status': 'unverified', 'tested_at': None, 'detail': None},
                'model': {'status': 'unverified', 'tested_at': None, 'detail': None},
                'schema': {'status': 'unverified', 'tested_at': None, 'detail': None},
                'retrieval': {'status': 'unverified', 'tested_at': None, 'detail': None},
            }, ensure_ascii=False)
            db.execute(
                'INSERT OR IGNORE INTO ai_settings VALUES (1,1,?,?,?,?)',
                (json.dumps(DEFAULT_AI, ensure_ascii=False), self.store.ensure_config_id(None), caps, now),
            )

    def _db(self):
        from contextlib import contextmanager

        @contextmanager
        def connection():
            db = sqlite3.connect(self.path, timeout=10)
            db.row_factory = sqlite3.Row
            try:
                with db:
                    yield db
            finally:
                db.close()
        return connection()

    def public_config(self) -> dict:
        with self.lock, self._db() as db:
            row = db.execute('SELECT * FROM ai_settings WHERE id=1').fetchone()
        config = json.loads(row['config'])
        caps = json.loads(row['capabilities'])
        provider = config.get('provider', 'apikey')
        return {
            'version': row['version'],
            'configId': row['config_id'],
            'keyConfigured': self.store.has_key(),
            'updated_at': row['updated_at'],
            'capabilities': caps,
            'implementedProtocols': sorted(IMPLEMENTED_PROTOCOLS),
            'disabledProtocols': [p for p in ('chat', 'responses', 'messages') if p not in IMPLEMENTED_PROTOCOLS],
            'knownModels': list(KNOWN_MODELS.get(provider, [])),
            **config,
        }

    def save_config(self, payload: dict) -> dict:
        if not isinstance(payload, dict) or 'version' not in payload:
            raise ValueError('保存须带当前version')
        if type(payload['version']) is not int:
            raise ValueError('version必须是整数')
        key_action = payload.get('keyAction', 'keep')
        if key_action not in ('keep', 'replace', 'clear'):
            raise ValueError('keyAction只支持keep/replace/clear')
        # Accept either nested config or flat fields.
        if 'config' in payload:
            raw_config = payload['config']
        else:
            raw_config = {k: payload[k] for k in DEFAULT_AI if k in payload}
            if set(raw_config) != set(DEFAULT_AI):
                raise ValueError('保存须提供完整AI配置字段')
        config = validate_ai_config(raw_config)
        api_key = payload.get('apiKey')
        with self.lock, self._db() as db:
            row = db.execute('SELECT * FROM ai_settings WHERE id=1').fetchone()
            if payload['version'] != row['version']:
                raise Conflict('AI配置已被其他页面更新，请重新读取后保存')
            previous_meta = {
                'configId': row['config_id'],
                'version': row['version'],
            }
            # Key mutation first; on failure keep previous DB row untouched.
            try:
                if key_action == 'replace':
                    self.store.write_key(api_key if isinstance(api_key, str) else '')
                elif key_action == 'clear':
                    self.store.clear_key()
                elif key_action == 'keep':
                    if api_key not in (None, ''):
                        raise ValueError('保留原Key时不要提交apiKey；要更换请用replace')
                    # empty input keeps existing key — nothing to do
            except CredentialError:
                raise
            except ValueError:
                raise
            # Saving config does not prove connectivity; reset capability claims
            # only when endpoint/model/provider changes.
            old = json.loads(row['config'])
            caps = json.loads(row['capabilities'])
            identity_changed = any(old.get(k) != config.get(k) for k in ('provider', 'protocol', 'baseUrl', 'modelId'))
            if identity_changed or key_action in ('replace', 'clear'):
                for name in caps:
                    caps[name] = {'status': 'unverified', 'tested_at': None, 'detail': '配置已变更，需重新显式测试'}
            now = iso(self.clock())
            db.execute(
                'UPDATE ai_settings SET version=version+1,config=?,capabilities=?,updated_at=? WHERE id=1',
                (json.dumps(config, ensure_ascii=False), json.dumps(caps, ensure_ascii=False), now),
            )
            self.store.save_meta({
                'configId': previous_meta['configId'],
                'provider': config['provider'],
                'updated_at': now,
            })
        return self.public_config()

    def _request_json(self, method: str, url: str, headers: dict, body: dict | None, timeout: int):
        data = None if body is None else json.dumps(body, ensure_ascii=False).encode('utf-8')
        req = urllib.request.Request(url, data=data, headers=headers, method=method)
        if self.opener:
            return self.opener(req, timeout)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                raw = resp.read()
                status = getattr(resp, 'status', 200)
                return status, raw, dict(resp.headers.items())
        except urllib.error.HTTPError as exc:
            raw = exc.read() if hasattr(exc, 'read') else b''
            return exc.code, raw, dict(exc.headers.items()) if exc.headers else {}
        except urllib.error.URLError as exc:
            raise TimeoutError(str(exc.reason) if getattr(exc, 'reason', None) else str(exc)) from exc

    def _auth_headers(self, key: str) -> dict:
        return {
            'Authorization': f'Bearer {key}',
            'Content-Type': 'application/json',
            'Accept': 'application/json',
            'User-Agent': 'anomaly-workbench/2.8-ai',
        }

    def _classify_http(self, status: int, body_text: str) -> tuple[str, str]:
        lower = (body_text or '').lower()
        if status in (401, 403):
            return 'auth_failed', f'HTTP {status} 鉴权失败'
        if status == 429:
            return 'rate_limited', 'HTTP 429 触发限流'
        if status == 404 and ('model' in lower or 'not found' in lower):
            return 'model_not_found', '模型不存在或不可用'
        if status >= 500:
            return 'upstream_error', f'HTTP {status} 上游错误'
        if status >= 400:
            return 'request_failed', f'HTTP {status}'
        return 'ok', 'ok'


    def list_models(self) -> dict:
        """Fetch provider /models when Key is saved; always include known presets."""
        pub = self.public_config()
        presets = list(KNOWN_MODELS.get(pub.get('provider', 'apikey'), []))
        remote = []
        error = None
        if pub['keyConfigured']:
            try:
                key = self.store.read_key()
                url = pub['baseUrl'].rstrip('/') + '/models'
                status, raw, _ = self._request_json('GET', url, self._auth_headers(key), None, min(pub['timeoutSec'], 30))
                text_body = raw.decode('utf-8', errors='replace')
                kind, detail = self._classify_http(status, text_body)
                if kind == 'ok':
                    payload = json.loads(text_body)
                    items = payload.get('data') if isinstance(payload, dict) else payload
                    if isinstance(items, list):
                        for item in items:
                            if isinstance(item, dict) and item.get('id'):
                                remote.append({'id': str(item['id']), 'label': str(item.get('id')), 'source': 'provider'})
                            elif isinstance(item, str):
                                remote.append({'id': item, 'label': item, 'source': 'provider'})
                else:
                    error = detail
            except Exception as exc:
                error = str(exc)[:300]
        # Merge: presets first, then remote not already listed
        seen = {m['id'] for m in presets}
        merged = [{'id': m['id'], 'label': m.get('label') or m['id'], 'source': 'preset', 'recommended': bool(m.get('recommended'))} for m in presets]
        for m in remote:
            if m['id'] not in seen:
                merged.append({'id': m['id'], 'label': m['label'], 'source': 'provider'})
                seen.add(m['id'])
        return {
            'models': merged,
            'provider': pub.get('provider'),
            'keyConfigured': pub['keyConfigured'],
            'error': error,
            'fetched_at': iso(self.clock()),
        }

    def test_connection(self) -> dict:
        """Explicit small Chat Completions request. Never marks success on transport-only checks."""
        pub = self.public_config()
        if not pub['keyConfigured']:
            return {
                'ok': False,
                'status': 'not_configured',
                'detail': '尚未配置API Key；请先保存密钥后再测试',
                'tested_at': iso(self.clock()),
                'capabilities': pub['capabilities'],
            }
        if not pub['modelId']:
            return {
                'ok': False,
                'status': 'model_missing',
                'detail': '请填写准确的模型ID后再测试',
                'tested_at': iso(self.clock()),
                'capabilities': pub['capabilities'],
            }
        try:
            key = self.store.read_key()
        except CredentialError as exc:
            return {'ok': False, 'status': 'key_unreadable', 'detail': str(exc), 'tested_at': iso(self.clock()),
                    'capabilities': pub['capabilities']}
        protocol = pub.get('protocol') or 'chat'
        if protocol == 'responses':
            url = pub['baseUrl'].rstrip('/') + '/responses'
            body = {
                'model': pub['modelId'],
                'input': 'ping',
                'max_output_tokens': 16,
            }
        else:
            url = pub['baseUrl'].rstrip('/') + '/chat/completions'
            body = {
                'model': pub['modelId'],
                'messages': [{'role': 'user', 'content': 'ping'}],
                'max_tokens': 8,
                'temperature': 0,
            }
        tested_at = iso(self.clock())
        try:
            status, raw, headers = self._request_json('POST', url, self._auth_headers(key), body, pub['timeoutSec'])
            text = raw.decode('utf-8', errors='replace')[:800]
            kind, detail = self._classify_http(status, text)
            ok = kind == 'ok'
            auth_status = 'success' if ok else ('failed' if kind == 'auth_failed' else 'unverified')
            model_status = 'success' if ok else ('failed' if kind in ('model_not_found', 'request_failed', 'upstream_error', 'rate_limited', 'auth_failed') else 'unverified')
            with self.lock, self._db() as db:
                row = db.execute('SELECT capabilities FROM ai_settings WHERE id=1').fetchone()
                caps = json.loads(row['capabilities'])
                caps['auth'] = {'status': auth_status if ok or kind == 'auth_failed' else caps['auth']['status'],
                                'tested_at': tested_at, 'detail': detail if not ok else '鉴权通过'}
                if kind == 'auth_failed':
                    caps['auth'] = {'status': 'failed', 'tested_at': tested_at, 'detail': detail}
                    caps['model'] = {'status': 'unverified', 'tested_at': tested_at, 'detail': '鉴权失败，未验证模型'}
                elif ok:
                    caps['auth'] = {'status': 'success', 'tested_at': tested_at, 'detail': '鉴权通过'}
                    caps['model'] = {'status': 'success', 'tested_at': tested_at, 'detail': '小请求成功'}
                elif kind == 'model_not_found':
                    caps['auth'] = {'status': 'success', 'tested_at': tested_at, 'detail': '鉴权看似通过，但模型不可用'}
                    caps['model'] = {'status': 'failed', 'tested_at': tested_at, 'detail': detail}
                elif kind == 'rate_limited':
                    caps['model'] = {'status': 'failed', 'tested_at': tested_at, 'detail': detail}
                else:
                    caps['model'] = {'status': 'failed', 'tested_at': tested_at, 'detail': detail}
                db.execute('UPDATE ai_settings SET capabilities=? WHERE id=1', (json.dumps(caps, ensure_ascii=False),))
            return {
                'ok': ok,
                'status': 'connected' if ok else kind,
                'http_status': status,
                'detail': '模型小请求成功' if ok else detail,
                'tested_at': tested_at,
                'retry_after': headers.get('Retry-After') if headers else None,
                'capabilities': self.public_config()['capabilities'],
            }
        except TimeoutError as exc:
            tested_at = iso(self.clock())
            detail = f'超时或网络不可达：{exc}'
            with self.lock, self._db() as db:
                row = db.execute('SELECT capabilities FROM ai_settings WHERE id=1').fetchone()
                caps = json.loads(row['capabilities'])
                caps['model'] = {'status': 'failed', 'tested_at': tested_at, 'detail': detail}
                db.execute('UPDATE ai_settings SET capabilities=? WHERE id=1', (json.dumps(caps, ensure_ascii=False),))
            return {'ok': False, 'status': 'timeout', 'detail': detail, 'tested_at': tested_at,
                    'capabilities': self.public_config()['capabilities']}

    def test_capabilities(self, checks: list[str] | None = None) -> dict:
        """Independent model/schema/retrieval checks. Retrieval unsupported unless adapter exists."""
        wanted = checks or ['model', 'schema', 'retrieval']
        if not isinstance(wanted, list) or not wanted or any(x not in ('model', 'schema', 'retrieval') for x in wanted):
            raise ValueError('checks只能包含model/schema/retrieval')
        results = {}
        pub = self.public_config()
        tested_at = iso(self.clock())
        if 'model' in wanted:
            conn = self.test_connection()
            results['model'] = {
                'status': 'success' if conn.get('ok') else 'failed',
                'tested_at': tested_at,
                'detail': conn.get('detail'),
            }
        if 'schema' in wanted:
            # Chat Completions JSON schema support varies; probe response_format json_object.
            results['schema'] = self._test_schema()
        if 'retrieval' in wanted:
            results['retrieval'] = {
                'status': 'unsupported',
                'tested_at': tested_at,
                'detail': '当前未配置独立检索适配器；模型自称联网不算检索成功',
            }
        with self.lock, self._db() as db:
            row = db.execute('SELECT capabilities FROM ai_settings WHERE id=1').fetchone()
            caps = json.loads(row['capabilities'])
            for name, value in results.items():
                caps[name] = value
            # Keep auth from connection test if model was checked.
            if 'model' in results:
                caps['auth'] = self.public_config()['capabilities']['auth']
            db.execute('UPDATE ai_settings SET capabilities=? WHERE id=1', (json.dumps(caps, ensure_ascii=False),))
        return {'ok': all(r['status'] == 'success' for r in results.values() if r['status'] != 'unsupported'),
                'results': results, 'capabilities': self.public_config()['capabilities'], 'tested_at': tested_at}

    def _test_schema(self) -> dict:
        pub = self.public_config()
        tested_at = iso(self.clock())
        if not pub['keyConfigured'] or not pub['modelId']:
            return {'status': 'failed', 'tested_at': tested_at, 'detail': '缺少Key或模型ID，无法验证结构化输出'}
        try:
            key = self.store.read_key()
        except CredentialError as exc:
            return {'status': 'failed', 'tested_at': tested_at, 'detail': str(exc)}
        if pub.get('protocol') == 'responses':
            return {'status': 'unsupported', 'tested_at': tested_at,
                    'detail': 'Responses协议暂用本地JSON校验；供应商原生schema未在此探针验收'}
        url = pub['baseUrl'].rstrip('/') + '/chat/completions'
        body = {
            'model': pub['modelId'],
            'messages': [
                {'role': 'system', 'content': 'Reply with JSON only.'},
                {'role': 'user', 'content': 'Return {"ok":true}'},
            ],
            'max_tokens': 32,
            'temperature': 0,
            'response_format': {'type': 'json_object'},
        }
        try:
            status, raw, _ = self._request_json('POST', url, self._auth_headers(key), body, pub['timeoutSec'])
            text = raw.decode('utf-8', errors='replace')
            kind, detail = self._classify_http(status, text)
            if kind != 'ok':
                if 'response_format' in text.lower() or 'json' in text.lower() and status >= 400:
                    return {'status': 'unsupported', 'tested_at': tested_at,
                            'detail': '供应商可能不支持JSON schema/response_format；可回退文本生成+本地校验'}
                return {'status': 'failed', 'tested_at': tested_at, 'detail': detail}
            payload = json.loads(text)
            content = payload.get('choices', [{}])[0].get('message', {}).get('content', '')
            parsed = json.loads(content) if isinstance(content, str) else content
            if isinstance(parsed, dict):
                return {'status': 'success', 'tested_at': tested_at, 'detail': '结构化JSON输出可用'}
            return {'status': 'failed', 'tested_at': tested_at, 'detail': '返回非JSON对象'}
        except TimeoutError as exc:
            return {'status': 'failed', 'tested_at': tested_at, 'detail': f'超时：{exc}'}
        except (json.JSONDecodeError, KeyError, IndexError, TypeError) as exc:
            return {'status': 'failed', 'tested_at': tested_at, 'detail': f'结构化解析失败：{exc}'}
