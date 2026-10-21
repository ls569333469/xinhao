"""Task/schedule/API contract tests. Temporary databases and fake market sources."""
import concurrent.futures
import datetime as dt
import json
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

from server import handler_factory
from task_service import Conflict, DEFAULT_SCAN, TaskService


class TaskTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.now = dt.datetime(2026, 10, 13, tzinfo=dt.timezone.utc)
        self.calls = 0
        self.service = TaskService(self.root, self.run_scan, clock=lambda: self.now)

    def tearDown(self):
        self.service.close()
        if self.service.worker:
            self.service.worker.join(3)
        self.tmp.cleanup()

    def run_scan(self):
        self.calls += 1
        return {'quality': {'status': 'partial_success'}, 'scan_id': 'scan-test', 'revision': 1,
                'received': 9, 'total': 10, 'capCoverage':{'required_contracts':9,'required_trusted_value':8}}

    def save(self, **updates):
        config = self.service.settings()
        return self.service.save_settings({'version': config['version'], 'scan': {**config['scan'], **updates}})

    def finish(self):
        self.service.tick()
        self.service.worker.join(3)
        self.assertFalse(self.service.worker.is_alive())

    def test_default_disabled_does_not_scan(self):
        self.assertEqual(self.service.settings()['scan'], DEFAULT_SCAN)
        self.service.tick()
        self.now += dt.timedelta(days=10)
        self.service.tick()
        self.assertEqual(self.calls, 0)
        self.assertEqual(self.service.tasks(), [])

    def test_settings_persist_and_optimistic_conflict(self):
        before = self.service.settings()
        saved = self.save(enabled=True, intervalHours=8)
        self.assertEqual(saved['version'], before['version'] + 1)
        self.assertEqual(dt.datetime.fromisoformat(saved['next_due']), self.now + dt.timedelta(minutes=2))
        with self.assertRaises(Conflict):
            self.service.save_settings({'version': before['version'], 'scan': DEFAULT_SCAN})
        reopened = TaskService(self.root, self.run_scan, clock=lambda: self.now)
        self.assertEqual(reopened.settings()['scan'], saved['scan'])
        reopened.close()

    def test_invalid_settings_rejected_without_mutation(self):
        before = self.service.settings()
        for change in ({'enabled': 1}, {'intervalHours': True}, {'intervalHours': 0},
                       {'intervalHours': 3}, {'timezone': 'bad'}, {'recoverMissed': 'yes'}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                self.save(**change)
        with self.assertRaises(ValueError):
            self.service.save_settings({'version': 1, 'scan': DEFAULT_SCAN, 'apiKey': 'not-accepted'})
        self.assertEqual(self.service.settings()['version'], before['version'])

    def test_concurrent_manual_requests_reuse_one_task(self):
        with concurrent.futures.ThreadPoolExecutor(max_workers=12) as pool:
            submissions = list(pool.map(lambda _: self.service.submit(), range(30)))
        self.assertEqual(len({s['task']['id'] for s in submissions}), 1)
        self.assertEqual(sum(not s['reused'] for s in submissions), 1)
        self.finish()
        self.assertEqual(self.calls, 1)
        task = self.service.tasks()[0]
        self.assertEqual(task['status'], 'partial_success')
        self.assertEqual(task['result']['scan_id'], 'scan-test')
        self.assertEqual(task['result']['capCoverage']['required_trusted_value'],8)
        self.assertIn('部分完成',task['progress']['phase'])

    def test_manual_and_schedule_overlap_reuse(self):
        self.save(enabled=True)
        task = self.service.submit()['task']
        self.now += dt.timedelta(hours=20)
        self.finish()
        self.assertEqual(self.calls, 1)
        self.assertEqual(len(self.service.tasks()), 1)
        self.assertEqual(self.service.tasks()[0]['id'], task['id'])
        self.assertEqual(dt.datetime.fromisoformat(self.service.settings()['next_due']), self.now + dt.timedelta(minutes=2))

    def test_running_task_reuse_and_cancel_rejected(self):
        gate = threading.Event()
        self.service.runner = lambda: (gate.wait(3), self.run_scan())[1]
        task = self.service.submit()['task']
        try:
            self.service.tick()
            self.assertTrue(self.service.submit()['reused'])
            with self.assertRaises(Conflict):
                self.service.cancel(task['id'])
            self.save(enabled=False)
            self.assertEqual(self.service.task(task['id'])['status'], 'running')
        finally:
            gate.set()
            self.service.worker.join(3)

    def test_cancel_queue_and_resubmit(self):
        task = self.service.submit()['task']
        self.assertEqual(self.service.cancel(task['id'])['status'], 'cancelled')
        self.assertNotEqual(self.service.submit()['task']['id'], task['id'])
        with self.assertRaises(KeyError):
            self.service.cancel('missing')

    def test_runner_failure_preserves_latest_file(self):
        path = self.root / 'scan.json'
        path.write_bytes(b'{"legacy":"preserved"}')
        self.service.runner = lambda: (_ for _ in ()).throw(RuntimeError('source unavailable'))
        self.service.submit()
        self.finish()
        self.assertEqual(self.service.tasks()[0]['status'], 'failed')
        self.assertIn('source unavailable', self.service.tasks()[0]['error'])
        self.assertEqual(path.read_bytes(), b'{"legacy":"preserved"}')

    def test_unqualified_runner_result_is_not_success(self):
        self.service.runner = lambda: {'received': 0}
        self.service.submit()
        self.finish()
        self.assertEqual(self.service.tasks()[0]['status'], 'failed')

    def test_restart_marks_interruption_then_recovers_once(self):
        self.save(enabled=True)
        task = self.service.submit()['task']
        with self.service._db() as db:
            db.execute("UPDATE tasks SET status='running' WHERE id=?", (task['id'],))
        self.now += dt.timedelta(days=7)
        self.service.close()
        self.service = TaskService(self.root, self.run_scan, clock=lambda: self.now)
        self.assertEqual(self.service.task(task['id'])['status'], 'failed')
        self.finish()
        self.service.tick()
        self.assertEqual(self.calls, 1)
        self.assertEqual(len(self.service.tasks()), 2)
        self.assertEqual(sum(t['reason'] == 'recovery' for t in self.service.tasks()), 1)

    def test_restart_recovery_off_skips_backlog(self):
        self.save(enabled=True, recoverMissed=False)
        self.now += dt.timedelta(days=7)
        self.service.close()
        self.service = TaskService(self.root, self.run_scan, clock=lambda: self.now)
        self.service.tick()
        self.assertEqual(self.service.tasks(), [])
        self.now += dt.timedelta(hours=4)
        self.finish()
        self.assertEqual(self.calls, 1)

    def test_disabling_keeps_history_and_clears_due(self):
        self.save(enabled=True)
        self.service.submit()
        self.save(enabled=False)
        self.finish()
        self.now += dt.timedelta(days=100)
        self.service.tick()
        self.assertEqual(self.calls, 1)
        self.assertIsNone(self.service.settings()['next_due'])


class HttpTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        (self.root / 'design').mkdir()
        (self.root / 'design' / 'prototype.html').write_text('<script id="snapshot-data" type="application/json">{"old":true}</script>', encoding='utf-8')
        (self.root / 'scan.json').write_text('{"rows":[{"symbol":"TESTUSDT"}]}', encoding='utf-8')
        (self.root / 'design' / 'private.txt').write_text('not-public', encoding='utf-8')
        self.calls = 0
        self.service = TaskService(self.root, lambda: (_ for _ in ()).throw(AssertionError('no scan on GET')))
        self.server = ThreadingHTTPServer(('127.0.0.1', 0), None)
        self.host = f'127.0.0.1:{self.server.server_port}'
        self.url = 'http://' + self.host
        self.server.RequestHandlerClass = handler_factory(self.root, self.service, True, [self.host])
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(3)
        self.service.close()
        self.tmp.cleanup()

    def request(self, path, method='GET', body=None, headers=None):
        hdr = {'Content-Type': 'application/json'}
        if method not in ('GET', 'HEAD'):
            hdr['Origin'] = self.url
        hdr.update(headers or {})
        data = body.encode() if isinstance(body, str) else json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(self.url + path, data=data, method=method, headers=hdr)
        try:
            with urllib.request.urlopen(req, timeout=3) as response:
                return response.status, response.read()
        except urllib.error.HTTPError as exc:
            with exc:
                return exc.code, exc.read()

    def test_read_only_routes_never_start_scan_and_latest_embed(self):
        for path in ('/api/scan', '/api/tasks', '/api/settings', '/api/health', '/prototype.html'):
            self.assertEqual(self.request(path)[0], 200)
        self.assertIn(b'TESTUSDT', self.request('/prototype.html')[1])
        self.assertNotIn(b'"old":true', self.request('/prototype.html')[1])
        self.assertEqual(self.service.tasks(), [])

    def test_reserves_embed_and_download_follow_published_snapshot_only(self):
        template = '<script id="snapshot-data" type="application/json">{}</script><script id="reserves-data" type="application/json">{"stale_static":true}</script>'
        (self.root/'design/prototype.html').write_text(template, encoding='utf-8')
        bundle = {'source':'DefiLlama Binance CEX','status':'success','records':[{'asset':'BTC','asOf':123}], 'retrievedAt':'2026-10-15T00:00:00Z'}
        (self.root/'scan.json').write_text(json.dumps({'rows':[], 'reserveEvidence':bundle}), encoding='utf-8')
        html = self.request('/prototype.html')[1].decode()
        self.assertNotIn('stale_static', html)
        import re
        embedded = re.search(r'<script id="reserves-data"[^>]*>(.*?)</script>', html).group(1)
        self.assertEqual(json.loads(embedded), bundle)
        self.assertEqual(json.loads(self.request('/reserves-data.json')[1]), bundle)
        (self.root/'scan.json').write_text('{"rows":[],"scan_id":"new-without-reserves"}', encoding='utf-8')
        self.assertEqual(json.loads(self.request('/reserves-data.json')[1])['status'], 'not_collected')
        self.assertNotIn('stale_static', self.request('/prototype.html')[1].decode())
        self.assertEqual(self.service.tasks(), [])

    def test_private_files_traversal_and_head_are_not_served(self):
        for path in ('/runtime/workbench.sqlite3', '/server.py', '/private.txt', '/%2e%2e/server.py', '/api/scans', '/design/private.txt'):
            self.assertEqual(self.request(path)[0], 404, path)
            self.assertEqual(self.request(path, 'HEAD')[0], 404, path)

    def test_wrong_host_cross_origin_and_non_json_rejected(self):
        self.assertEqual(self.request('/api/settings', headers={'Host': 'evil.test'})[0], 403)
        self.assertEqual(self.request('/api/scans', 'POST', {}, {'Origin': 'https://evil.test'})[0], 403)
        self.assertEqual(self.request('/api/scans', 'POST', {}, {'Origin': ''})[0], 403)
        self.assertEqual(self.request('/api/scans', 'POST', {}, {'Sec-Fetch-Site': 'cross-site'})[0], 403)
        self.assertEqual(self.request('/api/scans', 'POST', {}, {'Content-Type': 'text/plain'})[0], 415)
        self.assertEqual(self.service.tasks(), [])

    def test_submit_query_cancel_and_missing_task(self):
        code, raw = self.request('/api/scans', 'POST', {})
        self.assertEqual(code, 202)
        task = json.loads(raw)['task']
        self.assertEqual(json.loads(self.request('/api/scans', 'POST', {})[1])['reused'], True)
        self.assertEqual(self.request('/api/tasks/' + task['id'])[0], 200)
        self.assertEqual(self.request('/api/tasks/' + task['id'] + '/cancel', 'POST', {})[0], 200)
        self.assertEqual(self.request('/api/tasks/task-' + '0' * 32)[0], 404)

    def test_bad_json_shape_and_unsupported_api(self):
        for body in ('{"x":NaN}', '{bad', 'null', '[]', '{"symbol":"BTCUSDT"}'):
            self.assertEqual(self.request('/api/scans', 'POST', body)[0], 400)
        self.assertEqual(self.request('/api/scans', 'POST', 'x' * 17000)[0], 413)
        self.assertIn(self.request('/api/reports', 'POST', {})[0], (400, 409))  # route exists; empty/unbound body rejected (400 validation or 409 binding gate)

    def test_settings_write_read_and_conflict(self):
        payload = {'version': 1, 'scan': {**DEFAULT_SCAN, 'intervalHours': 8}}
        self.assertEqual(self.request('/api/settings', 'PUT', payload)[0], 200)
        self.assertEqual(json.loads(self.request('/api/settings')[1])['scan']['intervalHours'], 8)
        self.assertEqual(self.request('/api/settings', 'PUT', payload)[0], 409)



class ScanAndResearchTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.now = dt.datetime(2026, 10, 13, tzinfo=dt.timezone.utc)
        self.scan_calls = 0
        self.research_calls = 0
        (self.root / "design").mkdir()
        (self.root / "design" / "prompt-templates.json").write_text(json.dumps({
            "version": "research-prompts-v1.0",
            "common": "只用证据。",
            "schema": {"type": "object"},
            "profiles": {
                "quick": {"label": "快", "instruction": "短", "goal": "g", "search": {}},
                "standard": {"label": "标准", "instruction": "中", "goal": "g", "search": {}},
                "deep": {"label": "深", "instruction": "长", "goal": "g", "search": {}},
            }
        }, ensure_ascii=False), encoding="utf-8")
        # Old snapshot that must NOT be used if scan fails
        (self.root / "scan.json").write_text(json.dumps({
            "scan_id": "scan-old",
            "revision": 1,
            "available_at": "2026-10-12T00:00:00+00:00",
            "rows": [{"symbol": "BTCUSDT", "market": "USDⓈ-M", "price": 1}],
        }, ensure_ascii=False), encoding="utf-8")
        self.service = TaskService(self.root, self.run_scan, clock=lambda: self.now)
        from ai_research import AiResearch

        class FakeAi:
            def public_config(self):
                return {"keyConfigured": True, "modelId": "gpt-test", "protocol": "chat",
                        "baseUrl": "https://example.test/v1", "configId": "cfg", "timeoutSec": 30}
            def store(self):
                return None

        ai = FakeAi()
        ai.store = type("S", (), {"read_key": staticmethod(lambda: "sk")})()
        research = AiResearch(self.root, ai)
        orig_run = research.run

        def counted_run(req):
            self.research_calls += 1
            # Avoid live model: short-circuit via cache-like success using bind fields
            # Patch: write a fake report without model call
            from ai_research import iso
            import uuid
            report_id = "report-" + uuid.uuid4().hex
            report_dir = research.dir / report_id
            report_dir.mkdir(parents=True, exist_ok=True)
            report = {
                "report_id": report_id,
                "created_at": iso(research.clock()),
                "request": {k: v for k, v in req.items() if k != "_evidence"},
                "modelId": req.get("modelId"),
                "content": "ok",
                "source": "ai",
                "title": "BTCUSDT投研报告",
                "contracts": req.get("contracts") or [],
                "contract_count": req.get("selected_count"),
                "depth": req.get("depth"),
                "cache_key": req.get("request_hash"),
            }
            (report_dir / "report.json").write_text(json.dumps(report, ensure_ascii=False), encoding="utf-8")
            research.progress = {"phase": "完成", "completed": 1, "total": 1}
            return {
                "status": "success",
                "report_id": report_id,
                "available_at": report["created_at"],
                "contract_count": req["selected_count"],
                "modelId": req.get("modelId"),
                "cache_key": req.get("request_hash"),
                "cache_hit": False,
            }

        research.run = counted_run
        self.service.set_research(research)
        self.fail_scan = False

    def tearDown(self):
        self.service.close()
        if self.service.worker:
            self.service.worker.join(3)
        self.tmp.cleanup()

    def run_scan(self):
        self.scan_calls += 1
        if self.fail_scan:
            return {"quality": {"status": "failed", "message": "覆盖不足"}, "scan_id": "scan-bad"}
        # Full expected==received so DATA-01 coverage hard-gate allows research (R1 offline)
        rows = [
            {"symbol": f"SYM{i}USDT", "market": "USDⓈ-M", "price": 99 + i, "markPrice": 99 + i}
            for i in range(10)
        ]
        rows[0]["symbol"] = "BTCUSDT"
        snap = {
            "scan_id": "scan-new",
            "revision": 7,
            "available_at": "2026-10-13T01:00:00+00:00",
            "rows": rows,
            "quality": {"status": "success"},
            "received": 10,
            "total": 10,
        }
        (self.root / "scan.json").write_text(json.dumps(snap, ensure_ascii=False), encoding="utf-8")
        return snap

    def test_scan_and_research_success_binds_new_snapshot(self):
        body = {"mode": "scan_and_research", "contracts": [{"symbol": "BTCUSDT"}], "depth": "standard"}
        submitted = self.service.submit_research(body)
        self.assertEqual(submitted["task"]["kind"], "scan_and_research")
        self.service.tick()
        self.service.worker.join(5)
        task = self.service.task(submitted["task"]["id"])
        self.assertEqual(task["status"], "success")
        self.assertEqual(self.scan_calls, 1)
        self.assertEqual(self.research_calls, 1)
        self.assertEqual(task["result"]["scan_id"], "scan-new")
        self.assertEqual(task["result"]["revision"], 7)
        self.assertEqual(task["progress"]["phase"], "完成")

    def test_scan_and_research_scan_failure_skips_model(self):
        self.fail_scan = True
        body = {"mode": "scan_and_research", "contracts": [{"symbol": "BTCUSDT"}], "depth": "standard"}
        submitted = self.service.submit_research(body)
        self.service.tick()
        self.service.worker.join(5)
        task = self.service.task(submitted["task"]["id"])
        self.assertEqual(task["status"], "failed")
        self.assertEqual(self.scan_calls, 1)
        self.assertEqual(self.research_calls, 0)
        self.assertIn("未继续投研", task["progress"]["phase"])
        # old snapshot file still on disk, but research was not called
        snap = json.loads((self.root / "scan.json").read_text(encoding="utf-8"))
        self.assertEqual(snap["scan_id"], "scan-old")




class ResearchSubmissionIdR1Tests(unittest.TestCase):
    """R1 offline: submission_id idempotency, no content-hash merge, DB upgrade, freeze persist."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.now = dt.datetime(2026, 10, 14, 12, 0, tzinfo=dt.timezone.utc)
        self.model_calls = 0
        (self.root / "design").mkdir()
        (self.root / "design" / "prompt-templates.json").write_text(json.dumps({
            "version": "research-prompts-v1.0",
            "common": "只用证据。",
            "schema": {"type": "object"},
            "profiles": {
                "quick": {"label": "快", "instruction": "短", "goal": "g", "search": {}},
                "standard": {"label": "标准", "instruction": "中", "goal": "g", "search": {}},
                "deep": {"label": "深", "instruction": "长", "goal": "g", "search": {}},
            }
        }, ensure_ascii=False), encoding="utf-8")
        (self.root / "scan.json").write_text(json.dumps({
            "scan_id": "scan-A",
            "revision": 1,
            "available_at": "2026-10-14T00:00:00+00:00",
            "rows": [{"symbol": "BTCUSDT", "market": "USDⓈ-M", "price": 1, "fundingPct": 0.01}],
        }, ensure_ascii=False), encoding="utf-8")
        self.service = TaskService(self.root, lambda: {"quality": {"status": "success"}}, clock=lambda: self.now)
        from ai_research import AiResearch, iso
        import uuid as _uuid

        class FakeAi:
            def public_config(self):
                return {"keyConfigured": True, "modelId": "gpt-test", "protocol": "chat",
                        "baseUrl": "https://example.test/v1", "configId": "cfg", "version": 1, "timeoutSec": 30}
        ai = FakeAi()
        ai.store = type("S", (), {"read_key": staticmethod(lambda: "sk")})()
        research = AiResearch(self.root, ai)

        def counted_run(req):
            self.model_calls += 1
            report_id = "report-" + _uuid.uuid4().hex
            report_dir = research.dir / report_id
            report_dir.mkdir(parents=True, exist_ok=True)
            report = {
                "report_id": report_id,
                "created_at": iso(research.clock()),
                "request": {k: v for k, v in req.items() if k != "_evidence"},
                "modelId": req.get("modelId"),
                "content": "new-body-" + report_id[-6:],
                "source": "ai",
                "title": "BTCUSDT投研报告",
                "contracts": req.get("contracts") or [],
                "contract_count": req.get("selected_count"),
                "depth": req.get("depth"),
                "cache_key": req.get("request_hash"),
                "submission_id": req.get("submission_id"),
            }
            (report_dir / "report.json").write_text(json.dumps(report, ensure_ascii=False), encoding="utf-8")
            research.progress = {"phase": "完成", "completed": 1, "total": 1}
            return {
                "status": "success",
                "report_id": report_id,
                "available_at": report["created_at"],
                "contract_count": req["selected_count"],
                "modelId": req.get("modelId"),
                "cache_key": req.get("request_hash"),
                "cache_hit": False,
                "submission_id": req.get("submission_id"),
            }

        research.run = counted_run
        self.research = research
        self.service.set_research(research)

    def tearDown(self):
        self.service.close()
        if self.service.worker:
            self.service.worker.join(3)
        self.tmp.cleanup()

    def _body(self, **extra):
        b = {"mode": "snapshot", "contracts": [{"symbol": "BTCUSDT"}], "depth": "standard"}
        b.update(extra)
        return b

    def test_res01_two_independent_confirms_same_content(self):
        a = self.service.submit_research(self._body())
        self.service.tick()
        self.service.worker.join(5)
        b = self.service.submit_research(self._body())
        self.service.tick()
        self.service.worker.join(5)
        self.assertNotEqual(a["task"]["id"], b["task"]["id"])
        self.assertNotEqual(a["submission_id"], b["submission_id"])
        self.assertEqual(self.model_calls, 2)
        ta = self.service.task(a["task"]["id"])
        tb = self.service.task(b["task"]["id"])
        self.assertEqual(ta["status"], "success")
        self.assertEqual(tb["status"], "success")
        self.assertNotEqual(ta["result"]["report_id"], tb["result"]["report_id"])

    def test_res02_active_plus_independent_queues(self):
        # Hold first task in queued by not ticking yet
        first = self.service.submit_research(self._body(submission_id="sub-active-1"))
        second = self.service.submit_research(self._body(submission_id="sub-active-2"))
        self.assertFalse(first["reused"])
        self.assertFalse(second["reused"])
        self.assertNotEqual(first["task"]["id"], second["task"]["id"])
        # Same content hash must NOT merge
        self.assertEqual(first["task"]["status"], "queued")
        self.assertEqual(second["task"]["status"], "queued")

    def test_res03_idempotent_retry_and_intent_conflict(self):
        sid = "sub-retry-1"
        first = self.service.submit_research(self._body(submission_id=sid))
        retry = self.service.submit_research(self._body(submission_id=sid))
        self.assertTrue(retry["reused"])
        self.assertEqual(first["task"]["id"], retry["task"]["id"])
        from task_service import Conflict
        with self.assertRaises(Conflict):
            self.service.submit_research(self._body(submission_id=sid, user_focus="changed"))

    def test_res05_stale_preview_intent_rejected(self):
        from task_service import Conflict
        prev = self.research.preview(self._body())
        with self.assertRaises(Conflict):
            self.service.submit_research(self._body(
                submission_id=prev["submission_id"],
                preview_intent_hash=prev["intent_hash"],
                user_focus="new-focus-invalidates",
            ))

    def test_freeze_persists_across_memory_clear(self):
        submitted = self.service.submit_research(self._body(submission_id="sub-persist-1"))
        tid = submitted["task"]["id"]
        freeze = self.root / "runtime" / "task-freeze" / tid
        self.assertTrue((freeze / "intent.json").exists())
        self.assertTrue((freeze / "evidence.json").exists())
        # Simulate process restart: drop side-channel
        self.service._research_evidence = {}
        self.service.tick()
        self.service.worker.join(5)
        task = self.service.task(tid)
        self.assertEqual(task["status"], "success")
        self.assertEqual(self.model_calls, 1)

    def test_db_upgrade_drops_request_hash_unique_for_research(self):
        import sqlite3
        # Create a legacy-shaped DB in a copy path then open via TaskService
        legacy_root = Path(self.tmp.name) / "legacy"
        legacy_root.mkdir()
        (legacy_root / "runtime").mkdir()
        dbp = legacy_root / "runtime" / "workbench.sqlite3"
        con = sqlite3.connect(str(dbp))
        con.executescript("""
            CREATE TABLE settings (
                id INTEGER PRIMARY KEY CHECK(id=1), version INTEGER NOT NULL,
                scan TEXT NOT NULL, next_due TEXT, updated_at TEXT NOT NULL);
            CREATE TABLE tasks (
                id TEXT PRIMARY KEY, kind TEXT NOT NULL, status TEXT NOT NULL,
                reason TEXT NOT NULL, created_at TEXT NOT NULL, started_at TEXT,
                finished_at TEXT, progress TEXT NOT NULL, result TEXT, error TEXT,
                settings_version INTEGER NOT NULL,
                request TEXT NOT NULL DEFAULT '{}', request_hash TEXT);
            CREATE UNIQUE INDEX one_active_research ON tasks(request_hash)
                WHERE kind='ai_research' AND status IN ('queued','running');
            CREATE UNIQUE INDEX one_active_scan_research ON tasks(request_hash)
                WHERE kind='scan_and_research' AND status IN ('queued','running');
            INSERT INTO settings VALUES (1,1,'{"enabled":false,"intervalHours":4,"timezone":"Asia/Shanghai","recoverMissed":true}',NULL,'t');
            INSERT INTO tasks (id,kind,status,reason,created_at,progress,settings_version,request,request_hash)
            VALUES ('old-1','ai_research','success','manual','t','{}',1,'{}','samehash');
        """)
        con.commit()
        con.close()
        # Copy design/scan fixtures
        import shutil
        shutil.copytree(self.root / "design", legacy_root / "design")
        shutil.copy(self.root / "scan.json", legacy_root / "scan.json")
        svc = TaskService(legacy_root, lambda: {}, clock=lambda: self.now)
        svc.set_research(self.research)
        # Re-bind research root? AiResearch points to self.root; for prepare need templates on legacy
        from ai_research import AiResearch
        class FakeAi:
            def public_config(self):
                return {"keyConfigured": True, "modelId": "gpt-test", "protocol": "chat",
                        "baseUrl": "https://example.test/v1", "configId": "cfg", "version": 1, "timeoutSec": 30}
        ai = FakeAi()
        ai.store = type("S", (), {"read_key": staticmethod(lambda: "sk")})()
        research = AiResearch(legacy_root, ai)
        research.run = lambda req: {
            "status": "success", "report_id": "report-x", "available_at": "t",
            "contract_count": 1, "modelId": "gpt-test", "cache_key": req.get("request_hash"), "cache_hit": False,
        }
        svc.set_research(research)
        # Indexes dropped; two independent same-content confirms both enqueue
        a = svc.submit_research({"contracts": [{"symbol": "BTCUSDT"}], "depth": "standard", "submission_id": "sub-up-1"})
        b = svc.submit_research({"contracts": [{"symbol": "BTCUSDT"}], "depth": "standard", "submission_id": "sub-up-2"})
        self.assertNotEqual(a["task"]["id"], b["task"]["id"])
        # History preserved
        old = svc.task("old-1")
        self.assertEqual(old["status"], "success")
        # Verify indexes
        with svc._db() as db:
            names = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='index' AND tbl_name='tasks'")}
        self.assertNotIn("one_active_research", names)
        self.assertNotIn("one_active_scan_research", names)
        self.assertIn("one_submission_research", names)
        svc.close()



if __name__ == '__main__':
    unittest.main()
