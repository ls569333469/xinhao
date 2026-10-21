"""Native Responses web search on the configured provider/model; no implicit fallback.

Only provider tool records and citation annotations count as search evidence.
Search summaries are not fetched original pages or manually verified conclusions.
"""
from __future__ import annotations

import hashlib
from urllib.parse import urlsplit
from research_retrieval import RetrievalAdapter, FETCH_PARTIAL, STATUS_PARTIAL, STATUS_FAILED, iso


class ModelWebSearchAdapter(RetrievalAdapter):
    name = "model_native_web_search"
    mode = "live"

    def __init__(self, *, call, clock=None):
        super().__init__(clock=clock)
        self.call = call

    def retrieve(self, *, contracts, request=None, **kwargs):
        request = request or {}
        started = iso(self.clock())
        # The caller handles paid-call logging, unknown outcomes and raw-response retention.
        # Do not catch/convert unknown outcomes into an ordinary retriable failure.
        call = self.call(contracts, request)
        payload = call.get("raw_payload") or {}
        tools, sources = self.extract_sources(payload)
        summary = call.get("text") or ""
        items = []
        for url, title in sources.items():
            items.append(self.make_item(
                evidence_id="ev-web-" + hashlib.sha256((url + "\n" + summary).encode()).hexdigest()[:24],
                source_url=url, title=title or url, excerpt=summary,
                fetch_status=FETCH_PARTIAL, quality="search_summary_unverified",
                content_level="search_summary", attempt_id=call.get("attempt_id"),
                gaps_note="模型检索摘要；未独立抓取原文，发布日期/事件日期与逐币归属待核验",
                raw_extra={"tool_call_ids": [t.get("id") for t in tools],
                           "summary_is_shared": True, "full_text_verified": False}))
        performed = bool(tools)
        gaps = [{"symbol": c.get("symbol"), "reason": "source_support_not_manually_verified"}
                for c in contracts]
        result = self._finalize(
            status=STATUS_PARTIAL if items and performed and summary.strip() else STATUS_FAILED,
            items=items if performed and summary.strip() else [], gaps=gaps, attempts=[],
            usage={"requests": 1, "model_usage": call.get("usage"), "note": "另计一次模型联网检索请求；来源摘要非原文核验"},
            attempt_id=call.get("attempt_id"), started_iso=started,
            detail="已取得联网工具记录与来源摘要；内容支持度待核验" if items and performed and summary.strip()
                   else "服务商未返回完成的 web_search_call 与来源及摘要；停止成文，不自动回退/重发",
            adapter_implemented=True)
        result.update(actual_search_performed=performed, online_verified=False,
                      tool_records=tools, raw_response_ref=call.get("raw_response_ref"))
        return result

    @staticmethod
    def extract_sources(payload):
        tools, sources = [], {}
        if not isinstance(payload, dict):
            return tools, sources
        output = payload.get("output") or []
        if not isinstance(output, list):
            return tools, sources
        def add(src):
            if not isinstance(src, dict):
                return
            url = src.get("url")
            if not isinstance(url, str):
                return
            parsed = urlsplit(url)
            if parsed.scheme in ("http", "https") and parsed.hostname and not parsed.username:
                sources[url] = src.get("title") or url
        for item in output:
            if not isinstance(item, dict):
                continue
            if item.get("type") == "web_search_call" and item.get("status") == "completed":
                tools.append(item)
                for src in (item.get("action") or {}).get("sources") or []:
                    add(src)
            for part in item.get("content") or []:
                if not isinstance(part, dict):
                    continue
                for ann in part.get("annotations") or []:
                    if isinstance(ann, dict) and ann.get("type") == "url_citation":
                        add(ann)
        return tools, sources
