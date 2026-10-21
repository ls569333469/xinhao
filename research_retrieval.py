# -*- coding: utf-8 -*-
"""R2b: retrieve-then-generate retrieval adapter (REV-03).

Route: retrieve first → register/freeze evidence (id/hash/time/coverage/gaps)
→ then build model input that may only cite allowed evidence_ids.

Modes:
  - fixture (explicit test injection only): offline fixtures prove freeze/registration/status
  - live: optional HTTP source fetch when RESEARCH_RETRIEVAL_MODE=live and a key/base is set
  - none: adapter present but retrieval not requested → status=disabled

Production AiResearch defaults to the existing model's native web-search adapter.
This legacy HTTP adapter module never implicitly enables test fixtures.

UI "enable search" (requested_search) is NOT the same as "already retrieved".
Status always reflects the actual retrieval outcome.

Does NOT invent full-page verification from URL-only snippets.
Does NOT call paid LLM.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import urllib.error
import urllib.request
import uuid
from pathlib import Path
from typing import Any, Callable
from env_config import env_get

UTC = dt.timezone.utc

STRATEGY = "retrieve_then_generate"

# Terminal retrieval statuses (RES-07/08)
STATUS_SUCCESS = "success"
STATUS_PARTIAL = "partial_success"
STATUS_FAILED = "failed"
STATUS_UNSUPPORTED = "unsupported"
STATUS_DISABLED = "disabled"  # search not requested
STATUS_NOT_CONFIGURED = "not_configured"  # live requested but no key/base

FETCH_OK = "ok"
FETCH_TIMEOUT = "timeout"
FETCH_404 = "http_404"
FETCH_PARTIAL = "partial"
FETCH_UNIMPLEMENTED = "unimplemented_source"
FETCH_ERROR = "error"
FETCH_FIXTURE = "fixture"


def iso(value=None) -> str:
    value = value or dt.datetime.now(UTC)
    return value.astimezone(UTC).isoformat(timespec="microseconds")


def _sha256_text(text: str) -> str:
    return hashlib.sha256((text or "").encode("utf-8")).hexdigest()


def _sha256_obj(obj: Any) -> str:
    blob = json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
    return _sha256_text(blob)


class RetrievalAdapter:
    """Base adapter: subclasses implement fetch_for_contracts."""

    name = "base"
    mode = "none"
    implemented = True

    def __init__(self, *, clock: Callable | None = None, fixtures_dir: Path | None = None):
        self.clock = clock or (lambda: dt.datetime.now(UTC))
        self.fixtures_dir = Path(fixtures_dir) if fixtures_dir else None

    def retrieve(
        self,
        *,
        contracts: list[dict],
        request: dict | None = None,
        submission_id: str | None = None,
        task_id: str | None = None,
        scenario: str | None = None,
    ) -> dict:
        """Run retrieval and return a structured result (never raises for source misses)."""
        request = request or {}
        attempt_id = "retrieval-attempt-" + uuid.uuid4().hex
        started = self.clock()
        started_iso = iso(started)
        items: list[dict] = []
        attempts: list[dict] = []
        gaps: list[dict] = []
        usage: dict[str, Any] = {"requests": 0, "bytes": 0, "note": "retrieval_http_only_no_llm"}

        attempt_meta = {
            "at": started_iso,
            "kind": "retrieval_attempt",
            "attempt_id": attempt_id,
            "submission_id": submission_id or request.get("submission_id"),
            "task_id": task_id or request.get("task_id"),
            "adapter": self.name,
            "mode": self.mode,
            "status": "started",
            "strategy": STRATEGY,
        }
        attempts.append(attempt_meta)

        try:
            raw_items, raw_gaps, raw_usage, fetch_notes = self.fetch_for_contracts(
                contracts=contracts,
                request=request,
                attempt_id=attempt_id,
                scenario=scenario,
            )
        except TimeoutError as exc:
            fail = {
                "at": iso(self.clock()),
                "kind": "retrieval_attempt",
                "attempt_id": attempt_id,
                "submission_id": submission_id or request.get("submission_id"),
                "task_id": task_id or request.get("task_id"),
                "adapter": self.name,
                "mode": self.mode,
                "status": "failed",
                "fetch_status": FETCH_TIMEOUT,
                "detail": f"检索超时：{exc}",
            }
            attempts.append(fail)
            return self._finalize(
                status=STATUS_FAILED,
                items=[],
                gaps=[{"reason": "timeout", "detail": str(exc)[:300]}],
                attempts=attempts,
                usage=usage,
                attempt_id=attempt_id,
                started_iso=started_iso,
                detail=fail["detail"],
                adapter_implemented=True,
            )
        except Exception as exc:  # noqa: BLE001 — surface as failed retrieval, not crash run()
            fail = {
                "at": iso(self.clock()),
                "kind": "retrieval_attempt",
                "attempt_id": attempt_id,
                "submission_id": submission_id or request.get("submission_id"),
                "task_id": task_id or request.get("task_id"),
                "adapter": self.name,
                "mode": self.mode,
                "status": "failed",
                "fetch_status": FETCH_ERROR,
                "detail": f"检索异常：{type(exc).__name__}: {exc}"[:400],
            }
            attempts.append(fail)
            return self._finalize(
                status=STATUS_FAILED,
                items=[],
                gaps=[{"reason": "error", "detail": str(exc)[:300]}],
                attempts=attempts,
                usage=usage,
                attempt_id=attempt_id,
                started_iso=started_iso,
                detail=fail["detail"],
                adapter_implemented=True,
            )

        items = list(raw_items or [])
        gaps = list(raw_gaps or [])
        if isinstance(raw_usage, dict):
            usage.update(raw_usage)

        ok_count = sum(1 for it in items if it.get("fetch_status") == FETCH_OK or it.get("fetch_status") == FETCH_FIXTURE)
        partial_count = sum(1 for it in items if it.get("fetch_status") == FETCH_PARTIAL)
        fail_like = sum(
            1
            for it in items
            if it.get("fetch_status") in (FETCH_TIMEOUT, FETCH_404, FETCH_UNIMPLEMENTED, FETCH_ERROR)
        )

        if not items and gaps:
            status = STATUS_FAILED
            detail = "检索未取得任何证据；已记录缺口，不得标已联网核验"
        elif fail_like and (ok_count or partial_count):
            status = STATUS_PARTIAL
            detail = "部分来源取得证据，其余失败/未实现；报告须标注缺口"
        elif fail_like and not ok_count and not partial_count:
            status = STATUS_FAILED
            detail = "全部来源失败或未实现；不得标 online-verified"
        elif partial_count and not fail_like:
            status = STATUS_PARTIAL
            detail = "仅取得摘要/部分字段，非全文核验"
        elif ok_count:
            status = STATUS_SUCCESS
            detail = "检索取得可登记证据（fixture 或 live 正文/摘录已登记）"
        else:
            status = STATUS_FAILED
            detail = "无可用证据项"

        if fetch_notes:
            detail = f"{detail}；{fetch_notes}"

        done = {
            "at": iso(self.clock()),
            "kind": "retrieval_attempt",
            "attempt_id": attempt_id,
            "submission_id": submission_id or request.get("submission_id"),
            "task_id": task_id or request.get("task_id"),
            "adapter": self.name,
            "mode": self.mode,
            "status": "completed",
            "result_status": status,
            "item_count": len(items),
            "usage": usage,
        }
        attempts.append(done)
        return self._finalize(
            status=status,
            items=items,
            gaps=gaps,
            attempts=attempts,
            usage=usage,
            attempt_id=attempt_id,
            started_iso=started_iso,
            detail=detail,
            adapter_implemented=True,
        )

    def fetch_for_contracts(
        self,
        *,
        contracts: list[dict],
        request: dict,
        attempt_id: str,
        scenario: str | None = None,
    ) -> tuple[list[dict], list[dict], dict, str]:
        raise NotImplementedError

    def _finalize(
        self,
        *,
        status: str,
        items: list[dict],
        gaps: list[dict],
        attempts: list[dict],
        usage: dict,
        attempt_id: str,
        started_iso: str,
        detail: str,
        adapter_implemented: bool,
    ) -> dict:
        allowed = [it["evidence_id"] for it in items if it.get("evidence_id")]
        coverage = {
            "item_count": len(items),
            "ok_or_fixture": sum(
                1 for it in items if it.get("fetch_status") in (FETCH_OK, FETCH_FIXTURE)
            ),
            "partial": sum(1 for it in items if it.get("fetch_status") == FETCH_PARTIAL),
            "failed": sum(
                1
                for it in items
                if it.get("fetch_status") in (FETCH_TIMEOUT, FETCH_404, FETCH_UNIMPLEMENTED, FETCH_ERROR)
            ),
            "gap_count": len(gaps),
        }
        # evidence bundle hash covers registered items + gaps (freeze input)
        bundle = {
            "strategy": STRATEGY,
            "status": status,
            "items": items,
            "gaps": gaps,
            "retrieved_at": started_iso,
        }
        evidence_bundle_hash = _sha256_obj(bundle)
        # Never claim online-verified on failure / disabled / not configured
        online_verified = status == STATUS_SUCCESS and self.mode == "live" and coverage["ok_or_fixture"] > 0
        return {
            "schema": "research-retrieval-result-v1",
            "strategy": STRATEGY,
            "status": status,
            "adapter": self.name,
            "adapter_mode": self.mode,
            "adapter_implemented": bool(adapter_implemented),
            "attempt_id": attempt_id,
            "retrieved_at": started_iso,
            "finished_at": iso(self.clock()),
            "items": items,
            "allowed_evidence_ids": allowed,
            "coverage": coverage,
            "gaps": gaps,
            "attempts": attempts,
            "usage": usage,
            "evidence_bundle_hash": evidence_bundle_hash,
            "online_verified": bool(online_verified),
            "detail": detail,
            "citation_policy": (
                "模型仅可引用 allowed_evidence_ids 与本轮扫描行 evidence；"
                "禁止伪造 evidence_id；摘要不等于原文全文核验"
            ),
        }

    def make_item(
        self,
        *,
        evidence_id: str | None = None,
        source_url: str | None,
        title: str,
        excerpt: str,
        fetch_status: str,
        quality: str = "unverified",
        published_at: str | None = None,
        event_at: str | None = None,
        symbol: str | None = None,
        market: str | None = None,
        asset: str | None = None,
        publisher: str | None = None,
        gaps_note: str | None = None,
        attempt_id: str | None = None,
        content_level: str = "excerpt",  # excerpt | structured | full_text
        raw_extra: dict | None = None,
    ) -> dict:
        retrieved_at = iso(self.clock())
        excerpt = excerpt or ""
        content_hash = _sha256_text(excerpt)
        eid = evidence_id or ("ev-" + content_hash[:16])
        item = {
            "evidence_id": eid,
            "source_url": source_url,
            "title": title,
            "excerpt": excerpt[:4000],
            "content_hash": content_hash,
            "content_level": content_level,  # never upgrade URL-only to full_text
            "published_at": published_at,
            "event_at": event_at,
            "retrieved_at": retrieved_at,
            "available_at": retrieved_at,
            "timezone": "UTC" if published_at or event_at else "unknown",
            "quality": quality,
            "fetch_status": fetch_status,
            "publisher": publisher,
            "symbol": symbol,
            "market": market,
            "asset": asset,
            "attempt_id": attempt_id,
            "gaps_note": gaps_note,
        }
        if raw_extra:
            item["extra"] = raw_extra
        return item


class FixtureRetrievalAdapter(RetrievalAdapter):
    """Offline fixtures: prove retrieve→freeze→status without live/paid calls."""

    name = "fixture_retrieval"
    mode = "fixture"

    def fetch_for_contracts(
        self,
        *,
        contracts: list[dict],
        request: dict,
        attempt_id: str,
        scenario: str | None = None,
    ) -> tuple[list[dict], list[dict], dict, str]:
        scenario = (scenario or request.get("retrieval_scenario") or env_get("RESEARCH_RETRIEVAL_SCENARIO") or "ok").strip()
        fixtures = self._load_fixture_pack(scenario)
        items: list[dict] = []
        gaps: list[dict] = []
        usage = {"requests": 0, "bytes": 0, "fixture_scenario": scenario, "note": "offline_fixture_no_network"}

        # Map per-contract fixture entries; support failure scenarios globally
        if scenario == "timeout":
            raise TimeoutError("fixture simulated retrieval timeout")

        for c in contracts:
            sym = (c.get("symbol") or c.get("pair") or "UNKNOWN").upper()
            asset = c.get("asset") or sym.replace("USDT", "")
            market = c.get("market") or "USDⓈ-M"
            entries = fixtures.get("by_symbol", {}).get(sym) or fixtures.get("default") or []
            if scenario == "unimplemented":
                items.append(
                    self.make_item(
                        source_url=f"fixture://unimplemented/{sym}",
                        title=f"{sym} 来源未实现",
                        excerpt="",
                        fetch_status=FETCH_UNIMPLEMENTED,
                        quality="unverified",
                        symbol=sym,
                        market=market,
                        asset=asset,
                        attempt_id=attempt_id,
                        content_level="none",
                        gaps_note="该来源适配器未实现；不得标已核验",
                    )
                )
                gaps.append({"symbol": sym, "reason": "unimplemented_source", "detail": "fixture: source not implemented"})
                usage["requests"] += 1
                continue
            if scenario == "http_404":
                items.append(
                    self.make_item(
                        source_url=f"https://example.invalid/404/{sym}",
                        title=f"{sym} 404",
                        excerpt="",
                        fetch_status=FETCH_404,
                        quality="unverified",
                        symbol=sym,
                        market=market,
                        asset=asset,
                        attempt_id=attempt_id,
                        content_level="none",
                        gaps_note="HTTP 404；不得当作无事件",
                    )
                )
                gaps.append({"symbol": sym, "reason": "http_404", "detail": "fixture: 404"})
                usage["requests"] += 1
                continue
            if not entries:
                gaps.append({"symbol": sym, "reason": "no_fixture", "detail": "无对应夹具条目"})
                continue
            for entry in entries:
                fetch_status = entry.get("fetch_status") or FETCH_FIXTURE
                excerpt = entry.get("excerpt") or ""
                content_level = entry.get("content_level") or "excerpt"
                if fetch_status == FETCH_PARTIAL:
                    content_level = "excerpt"
                if fetch_status in (FETCH_404, FETCH_TIMEOUT, FETCH_UNIMPLEMENTED, FETCH_ERROR):
                    content_level = "none"
                    excerpt = excerpt or ""
                item = self.make_item(
                    evidence_id=entry.get("evidence_id"),
                    source_url=entry.get("source_url") or f"fixture://{scenario}/{sym}",
                    title=entry.get("title") or f"{sym} fixture",
                    excerpt=excerpt,
                    fetch_status=fetch_status,
                    quality=entry.get("quality") or "fixture",
                    published_at=entry.get("published_at"),
                    event_at=entry.get("event_at"),
                    symbol=sym,
                    market=market,
                    asset=asset,
                    publisher=entry.get("publisher") or "fixture",
                    gaps_note=entry.get("gaps_note"),
                    attempt_id=attempt_id,
                    content_level=content_level,
                    raw_extra={"fixture_scenario": scenario},
                )
                items.append(item)
                usage["requests"] += 1
                usage["bytes"] += len(excerpt.encode("utf-8"))
                if entry.get("gap"):
                    gaps.append({"symbol": sym, "reason": entry.get("gap"), "detail": entry.get("gaps_note") or entry.get("gap")})

        # Global gaps from pack
        for g in fixtures.get("gaps") or []:
            gaps.append(dict(g))

        note = f"fixture_scenario={scenario}"
        return items, gaps, usage, note

    def _load_fixture_pack(self, scenario: str) -> dict:
        if self.fixtures_dir:
            path = self.fixtures_dir / f"{scenario}.json"
            if path.exists():
                return json.loads(path.read_text(encoding="utf-8"))
        # Built-in minimal packs (tests may also drop files under fixtures_dir)
        if scenario == "partial":
            return {
                "default": [
                    {
                        "evidence_id": "ev-fixture-partial-announce",
                        "title": "公告摘要（动态表格未加载）",
                        "excerpt": "外层页面可访问；关键解锁表格未渲染，仅有标题摘要。",
                        "source_url": "fixture://partial/announce",
                        "fetch_status": FETCH_PARTIAL,
                        "content_level": "excerpt",
                        "quality": "unverified",
                        "publisher": "fixture-partial",
                        "gaps_note": "动态组件未加载；不得标全文核验或无解锁",
                        "gap": "dynamic_component_not_loaded",
                    }
                ],
                "gaps": [{"reason": "dynamic_component_not_loaded", "detail": "关键表格未加载"}],
            }
        if scenario == "empty_news":
            return {
                "default": [],
                "gaps": [
                    {
                        "reason": "no_news_result",
                        "detail": "检索无新闻命中；不得捏造「没有新闻/没有事件」为已核验结论",
                    }
                ],
            }
        # default "ok"
        return {
            "default": [
                {
                    "evidence_id": "ev-fixture-ok-catalyst",
                    "title": "官方动态夹具（非 live）",
                    "excerpt": "这是离线夹具摘录，用于证明 evidence 登记与冻结；非付费联网全文。",
                    "source_url": "fixture://ok/official",
                    "fetch_status": FETCH_FIXTURE,
                    "content_level": "excerpt",
                    "quality": "fixture",
                    "publisher": "fixture-official",
                    "published_at": "2026-10-01T00:00:00+00:00",
                }
            ],
            "by_symbol": {},
            "gaps": [],
        }


class LiveHttpRetrievalAdapter(RetrievalAdapter):
    """Optional live HTTP fetch. Requires explicit mode=live + base/key.

    Only records what was actually returned. URL/status alone never becomes
    full_text verification. No paid LLM.
    """

    name = "live_http_retrieval"
    mode = "live"

    def __init__(
        self,
        *,
        clock: Callable | None = None,
        base_url: str | None = None,
        api_key: str | None = None,
        timeout_sec: float = 15.0,
        opener: Callable | None = None,
    ):
        super().__init__(clock=clock)
        self.base_url = (base_url or "").rstrip("/")
        self.api_key = api_key or ""
        self.timeout_sec = float(timeout_sec)
        self.opener = opener  # (Request) -> (status, body_bytes, headers_dict)

    def fetch_for_contracts(
        self,
        *,
        contracts: list[dict],
        request: dict,
        attempt_id: str,
        scenario: str | None = None,
    ) -> tuple[list[dict], list[dict], dict, str]:
        if not self.base_url:
            return (
                [],
                [{"reason": "not_configured", "detail": "缺少 RESEARCH_RETRIEVAL_BASE_URL"}],
                {"requests": 0, "bytes": 0},
                "live base_url 未配置",
            )
        items: list[dict] = []
        gaps: list[dict] = []
        usage = {"requests": 0, "bytes": 0, "note": "live_http_no_llm"}
        for c in contracts:
            sym = (c.get("symbol") or c.get("pair") or "UNKNOWN").upper()
            asset = c.get("asset") or sym.replace("USDT", "")
            market = c.get("market") or "USDⓈ-M"
            # Conservative: hit a search/query endpoint if provided; else GET base/symbol
            url = f"{self.base_url}/search?q={urllib.parse.quote(asset + ' crypto announcement')}"
            try:
                status, body, _headers = self._http_get(url)
            except TimeoutError:
                items.append(
                    self.make_item(
                        source_url=url,
                        title=f"{sym} live timeout",
                        excerpt="",
                        fetch_status=FETCH_TIMEOUT,
                        symbol=sym,
                        market=market,
                        asset=asset,
                        attempt_id=attempt_id,
                        content_level="none",
                        gaps_note="live 超时",
                    )
                )
                gaps.append({"symbol": sym, "reason": "timeout", "detail": url})
                usage["requests"] += 1
                continue
            usage["requests"] += 1
            usage["bytes"] += len(body or b"")
            if status == 404:
                items.append(
                    self.make_item(
                        source_url=url,
                        title=f"{sym} 404",
                        excerpt="",
                        fetch_status=FETCH_404,
                        symbol=sym,
                        market=market,
                        asset=asset,
                        attempt_id=attempt_id,
                        content_level="none",
                        gaps_note="HTTP 404",
                    )
                )
                gaps.append({"symbol": sym, "reason": "http_404", "detail": url})
                continue
            if status != 200:
                items.append(
                    self.make_item(
                        source_url=url,
                        title=f"{sym} HTTP {status}",
                        excerpt=(body or b"")[:500].decode("utf-8", errors="replace"),
                        fetch_status=FETCH_ERROR,
                        symbol=sym,
                        market=market,
                        asset=asset,
                        attempt_id=attempt_id,
                        content_level="excerpt",
                        gaps_note=f"HTTP {status}",
                    )
                )
                gaps.append({"symbol": sym, "reason": "http_error", "detail": f"status={status}"})
                continue
            text = (body or b"").decode("utf-8", errors="replace")
            # Mark as excerpt only — do not claim full-page verification
            items.append(
                self.make_item(
                    source_url=url,
                    title=f"{sym} live 摘录",
                    excerpt=text[:2000],
                    fetch_status=FETCH_OK if text.strip() else FETCH_PARTIAL,
                    quality="third_party",
                    symbol=sym,
                    market=market,
                    asset=asset,
                    attempt_id=attempt_id,
                    content_level="excerpt",
                    gaps_note=None if text.strip() else "空响应",
                    publisher="live_http",
                )
            )
            if not text.strip():
                gaps.append({"symbol": sym, "reason": "empty_body", "detail": url})
        return items, gaps, usage, "live_http"

    def _http_get(self, url: str) -> tuple[int, bytes, dict]:
        if self.opener:
            return self.opener(url)
        req = urllib.request.Request(url, method="GET")
        if self.api_key:
            req.add_header("Authorization", "Bearer " + self.api_key)
        req.add_header("User-Agent", "yidong-research-retrieval/r2b")
        try:
            with urllib.request.urlopen(req, timeout=self.timeout_sec) as resp:
                return int(resp.status), resp.read(), dict(resp.headers.items())
        except urllib.error.HTTPError as exc:
            body = exc.read() if hasattr(exc, "read") else b""
            return int(exc.code), body or b"", {}
        except TimeoutError:
            raise
        except Exception as exc:
            # Map URLError timeout-ish
            if "timed out" in str(exc).lower():
                raise TimeoutError(str(exc)) from exc
            raise


# late import for quote used in live adapter
import urllib.parse  # noqa: E402


def resolve_adapter(
    *,
    root: Path | None = None,
    clock: Callable | None = None,
    explicit: RetrievalAdapter | None = None,
) -> RetrievalAdapter:
    """Production defaults to unconfigured live; fixtures require explicit test injection."""
    if explicit is not None:
        return explicit
    mode = (env_get("RESEARCH_RETRIEVAL_MODE") or "live").strip().lower()
    fixtures_dir = None
    if root is not None:
        fixtures_dir = Path(root) / "design" / "retrieval-fixtures"
        if not fixtures_dir.is_dir():
            fixtures_dir = Path(root) / "runtime" / "retrieval-fixtures"
    if mode == "live":
        base = env_get("RESEARCH_RETRIEVAL_BASE_URL") or ""
        key = env_get("RESEARCH_RETRIEVAL_API_KEY") or ""
        if not base:
            # Fall back to fixture but annotate via a thin wrapper result at call site;
            # still return live adapter so retrieve reports not_configured gaps.
            return LiveHttpRetrievalAdapter(clock=clock, base_url="", api_key=key)
        return LiveHttpRetrievalAdapter(clock=clock, base_url=base, api_key=key)
    return LiveHttpRetrievalAdapter(clock=clock, base_url="", api_key="")


def disabled_result(*, reason: str = "未勾选检索（requested_search=false）") -> dict:
    """Search not requested — must not look like retrieved/verified."""
    now = iso()
    return {
        "schema": "research-retrieval-result-v1",
        "strategy": STRATEGY,
        "status": STATUS_DISABLED,
        "adapter": None,
        "adapter_mode": "none",
        "adapter_implemented": True,  # path exists; just not run
        "attempt_id": None,
        "retrieved_at": None,
        "finished_at": now,
        "items": [],
        "allowed_evidence_ids": [],
        "coverage": {"item_count": 0, "ok_or_fixture": 0, "partial": 0, "failed": 0, "gap_count": 0},
        "gaps": [],
        "attempts": [],
        "usage": None,
        "evidence_bundle_hash": None,
        "online_verified": False,
        "detail": reason,
        "citation_policy": "未启用检索；仅可引用本轮扫描行证据",
    }


def not_configured_live_result(*, detail: str) -> dict:
    now = iso()
    return {
        "schema": "research-retrieval-result-v1",
        "strategy": STRATEGY,
        "status": STATUS_NOT_CONFIGURED,
        "adapter": "live_http_retrieval",
        "adapter_mode": "live",
        "adapter_implemented": True,
        "attempt_id": None,
        "retrieved_at": now,
        "finished_at": now,
        "items": [],
        "allowed_evidence_ids": [],
        "coverage": {"item_count": 0, "ok_or_fixture": 0, "partial": 0, "failed": 0, "gap_count": 1},
        "gaps": [{"reason": "not_configured", "detail": detail}],
        "attempts": [],
        "usage": {"requests": 0, "bytes": 0},
        "evidence_bundle_hash": _sha256_text(detail),
        "online_verified": False,
        "detail": detail,
        "citation_policy": "live 未配置；不得标已联网核验",
    }


def configured_capability(*, explicit=None):
    """Read-only configuration status. Never probes a model or search endpoint."""
    adapter = explicit or resolve_adapter()
    mode = getattr(adapter, "mode", "none")
    configured = bool(getattr(adapter, "base_url", "")) if mode == "live" else False
    return {"mode": mode, "configured": configured, "online_verified": False,
            "status": "configured_unverified" if configured else "not_configured",
            "detail": "检索渠道已配置，实际结果逐次核验" if configured else "未配置真实检索渠道；不会使用测试数据代替新闻"}
