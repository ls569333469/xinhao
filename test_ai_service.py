"""AI-01/AI-02 offline tests with injectable HTTP opener and temp dirs."""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import ai_credentials
from ai_service import AiService, Conflict, DEFAULT_AI, validate_ai_config, normalize_base_url


class FakeResp:
    def __init__(self, status, body, headers=None):
        self.status = status
        self._body = body if isinstance(body, bytes) else body.encode()
        self.headers = headers or {}

    def read(self):
        return self._body


class AiServiceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.calls = []

        def opener(req, timeout):
            self.calls.append((req.method, req.full_url, req.data, timeout))
            # Default success unless overridden per-test via self.handler
            return self.handler(req, timeout)

        self.handler = lambda req, timeout: (200, b'{"choices":[{"message":{"content":"pong"}}]}', {})
        # Patch DPAPI with reversible stub so tests run off Windows CI too.
        self._prot = mock.patch.object(ai_credentials, '_dpapi_protect', side_effect=lambda b: b'ENC:' + b)
        self._unprot = mock.patch.object(ai_credentials, '_dpapi_unprotect', side_effect=lambda b: b[4:] if b.startswith(b'ENC:') else b)
        self._prot.start(); self._unprot.start()
        self.addCleanup(self._prot.stop)
        self.addCleanup(self._unprot.stop)
        self.addCleanup(self.tmp.cleanup)
        self.service = AiService(self.root, opener=opener)

    def test_normalize_rejects_http_and_localhost(self):
        with self.assertRaises(ValueError):
            normalize_base_url('http://api.apikey.fan/v1')
        with self.assertRaises(ValueError):
            normalize_base_url('https://127.0.0.1/v1')
        self.assertEqual(normalize_base_url('https://api.apikey.fan/v1/'), 'https://api.apikey.fan/v1')

    def test_public_config_never_contains_key(self):
        pub = self.service.public_config()
        blob = json.dumps(pub)
        self.assertNotIn('apiKey', pub)
        self.assertNotIn('api_key', blob.lower())
        self.assertFalse(pub['keyConfigured'])
        self.assertEqual(pub['capabilities']['auth']['status'], 'unverified')
        self.assertTrue(any(m['id']=='gpt-6.1-sol' for m in pub.get('knownModels', [])))

    def test_save_keep_replace_clear_key(self):
        v = self.service.public_config()['version']
        cfg = {**DEFAULT_AI, 'modelId': 'demo-model'}
        saved = self.service.save_config({'version': v, 'config': cfg, 'keyAction': 'replace', 'apiKey': 'sk-test-1'})
        self.assertTrue(saved['keyConfigured'])
        self.assertEqual(self.service.store.read_key(), 'sk-test-1')
        # empty keep
        v2 = saved['version']
        kept = self.service.save_config({'version': v2, 'config': cfg, 'keyAction': 'keep'})
        self.assertEqual(self.service.store.read_key(), 'sk-test-1')
        # replace
        v3 = kept['version']
        self.service.save_config({'version': v3, 'config': cfg, 'keyAction': 'replace', 'apiKey': 'sk-test-2'})
        self.assertEqual(self.service.store.read_key(), 'sk-test-2')
        # clear
        v4 = self.service.public_config()['version']
        cleared = self.service.save_config({'version': v4, 'config': cfg, 'keyAction': 'clear'})
        self.assertFalse(cleared['keyConfigured'])

    def test_optimistic_conflict(self):
        v = self.service.public_config()['version']
        cfg = {**DEFAULT_AI}
        self.service.save_config({'version': v, 'config': cfg, 'keyAction': 'keep'})
        with self.assertRaises(Conflict):
            self.service.save_config({'version': v, 'config': cfg, 'keyAction': 'keep'})

    def test_unimplemented_protocol_rejected(self):
        with self.assertRaises(ValueError):
            validate_ai_config({**DEFAULT_AI, 'protocol': 'messages'})
        # responses is implemented for APIKEY.FAN
        validate_ai_config({**DEFAULT_AI, 'protocol': 'responses', 'modelId': 'gpt-6.1-sol'})

    def test_connection_success_and_auth_failure(self):
        v = self.service.public_config()['version']
        self.service.save_config({'version': v, 'config': {**DEFAULT_AI, 'modelId': 'm1'}, 'keyAction': 'replace', 'apiKey': 'sk'})
        ok = self.service.test_connection()
        self.assertTrue(ok['ok'])
        self.assertEqual(ok['status'], 'connected')
        self.assertEqual(self.service.public_config()['capabilities']['auth']['status'], 'success')

        self.handler = lambda req, timeout: (401, b'{"error":"unauthorized"}', {})
        bad = self.service.test_connection()
        self.assertFalse(bad['ok'])
        self.assertEqual(bad['status'], 'auth_failed')
        self.assertEqual(self.service.public_config()['capabilities']['auth']['status'], 'failed')
        # never claim connected
        self.assertNotEqual(bad.get('status'), 'connected')

    def test_model_not_found_and_rate_limit(self):
        v = self.service.public_config()['version']
        self.service.save_config({'version': v, 'config': {**DEFAULT_AI, 'modelId': 'missing'}, 'keyAction': 'replace', 'apiKey': 'sk'})
        self.handler = lambda req, timeout: (404, b'{"error":"model not found"}', {})
        r = self.service.test_connection()
        self.assertEqual(r['status'], 'model_not_found')
        self.handler = lambda req, timeout: (429, b'rate', {'Retry-After': '3'})
        r2 = self.service.test_connection()
        self.assertEqual(r2['status'], 'rate_limited')

    def test_retrieval_unsupported_without_adapter(self):
        v = self.service.public_config()['version']
        self.service.save_config({'version': v, 'config': {**DEFAULT_AI, 'modelId': 'm1'}, 'keyAction': 'replace', 'apiKey': 'sk'})
        self.handler = lambda req, timeout: (200, b'{"choices":[{"message":{"content":"{\\"ok\\":true}"}}]}', {})
        out = self.service.test_capabilities(['retrieval'])
        self.assertEqual(out['results']['retrieval']['status'], 'unsupported')

    def test_schema_probe(self):
        v = self.service.public_config()['version']
        self.service.save_config({'version': v, 'config': {**DEFAULT_AI, 'modelId': 'm1'}, 'keyAction': 'replace', 'apiKey': 'sk'})
        self.handler = lambda req, timeout: (200, b'{"choices":[{"message":{"content":"{\\"ok\\":true}"}}]}', {})
        out = self.service.test_capabilities(['schema'])
        self.assertEqual(out['results']['schema']['status'], 'success')


if __name__ == '__main__':
    unittest.main()


from server import handler_factory
from http.server import ThreadingHTTPServer
from task_service import TaskService
import threading


class AiHttpTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.addCleanup(self.tmp.cleanup)
        self._prot = mock.patch.object(ai_credentials, "_dpapi_protect", side_effect=lambda b: b"ENC:" + b)
        self._unprot = mock.patch.object(ai_credentials, "_dpapi_unprotect", side_effect=lambda b: b[4:] if b.startswith(b"ENC:") else b)
        self._prot.start(); self._unprot.start()
        self.addCleanup(self._prot.stop); self.addCleanup(self._unprot.stop)
        self.service = TaskService(self.root, runner=lambda: {"quality": {"status": "success"}})
        self.ai = AiService(self.root, opener=lambda req, timeout: (200, b'{"choices":[{"message":{"content":"pong"}}]}', {}))
        self.host = "127.0.0.1:18767"
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), handler_factory(self.root, self.service, prototype=True, allowed_hosts=[self.host], ai=self.ai))
        self.host = f"127.0.0.1:{self.server.server_address[1]}"
        # rebuild handler with actual host
        self.server.RequestHandlerClass = handler_factory(self.root, self.service, prototype=True, allowed_hosts=[self.host], ai=self.ai)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        try:
            self.server.shutdown()
        except Exception:
            pass
        try:
            self.server.server_close()
        except Exception:
            pass
        if getattr(self, "thread", None):
            self.thread.join(timeout=2)

    def request(self, path, method="GET", body=None, extra=None):
        import urllib.request
        data = None if body is None else (body if isinstance(body, (bytes, str)) else json.dumps(body))
        if isinstance(data, str):
            data = data.encode()
        headers = {"Host": self.host, "Content-Type": "application/json", "Origin": "http://" + self.host, "Sec-Fetch-Site": "same-origin"}
        if extra:
            headers.update(extra)
        req = urllib.request.Request("http://" + self.host + path, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=3) as resp:
                return resp.status, resp.read().decode()
        except urllib.error.HTTPError as e:
            return e.code, e.read().decode()

    def test_ai_config_get_put_and_no_key_leak(self):
        code, raw = self.request("/api/ai/config")
        self.assertEqual(code, 200)
        data = json.loads(raw)
        self.assertFalse(data["keyConfigured"])
        self.assertNotIn("apiKey", data)
        payload = {"version": data["version"], "keyAction": "replace", "apiKey": "sk-http", "config": {**DEFAULT_AI, "modelId": "m1"}}
        code, raw = self.request("/api/ai/config", "PUT", payload)
        self.assertEqual(code, 200)
        saved = json.loads(raw)
        self.assertTrue(saved["keyConfigured"])
        self.assertNotIn("sk-http", raw)
        code, raw = self.request("/api/ai/test/connection", "POST", {})
        self.assertEqual(code, 200)
        self.assertTrue(json.loads(raw)["ok"])
