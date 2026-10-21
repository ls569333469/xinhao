# -*- coding: utf-8 -*-
"""R6: post-generation checks (RES-14), data cutoffs (REV-07), auto vs human checks (REV-09), revisions (RES-16).

Pure functions + small file store. No model calls. Automatic checks only prove defined structure/citation/
coverage rules; they never prove the content is sufficient or that sources support conclusions.
"""
from __future__ import annotations

import datetime as dt
import json
import re
import uuid
from pathlib import Path

UTC = dt.timezone.utc
CHECKS_VERSION = "post-gen-checks-v2"
FRESHNESS_POLICY_VERSION = "freshness-v1"
# Thresholds follow actual data frequency: scan default every few hours; 4h candles; news has no hard expiry.
MARKET_STALE_HOURS = 8
TECH_STALE_HOURS = 8   # 4h closed candle + one more interval
EVIDENCE_ID_RE = re.compile(r"\bev-[A-Za-z0-9][A-Za-z0-9_\-]*[A-Za-z0-9]")
HUMAN_RESULTS = ("通过", "不通过", "部分通过")


def _parse(ts):
    # R7: real scan rows carry candleAsOf as epoch milliseconds (close time, e.g. ...99999)
    if isinstance(ts, (int, float)) and not isinstance(ts, bool) and ts > 0:
        sec = ts / 1000.0 if ts > 1e11 else float(ts)
        try:
            return dt.datetime.fromtimestamp(sec, UTC)
        except (OverflowError, OSError, ValueError):
            return None
    if not ts or not isinstance(ts, str):
        return None
    try:
        v = dt.datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except ValueError:
        return None
    return v if v.tzinfo else v.replace(tzinfo=UTC)


def _iso(v):
    return v.astimezone(UTC).isoformat() if v else None


def _max_ts(values):
    ps = [p for p in (_parse(v) for v in values) if p]
    return max(ps) if ps else None


def data_cutoffs(*, now, snapshot_available_at, rows, retrieval) -> dict:
    """REV-07: separate cutoffs for market / technical / news + staleness with reasons."""
    rows = [r for r in (rows or []) if isinstance(r, dict)]
    market = _parse(snapshot_available_at) or _max_ts([r.get("asOf") or r.get("observed_at") for r in rows])
    tech = _max_ts([r.get("candleAsOf") or r.get("closed4h") for r in rows])
    items = (retrieval or {}).get("items") or []
    news_pub = _max_ts([i.get("published_at") for i in items if isinstance(i, dict)])
    news_ret = _parse((retrieval or {}).get("retrieved_at"))

    def stale(ts, hours, label):
        if ts is None:
            return {"status": "unknown", "reason": label + "截止时间未知（数据未提供），不当作最新"}
        age = (now - ts).total_seconds() / 3600
        if age > hours:
            return {"status": "stale", "reason": "%s已过 %.1f 小时，超过 %d 小时阈值" % (label, age, hours), "age_hours": round(age, 2)}
        return {"status": "fresh", "reason": None, "age_hours": round(age, 2)}

    news_status = ({"status": "unknown", "reason": "本次未取得新闻/外部证据"} if news_ret is None and not items else
                   {"status": "as_retrieved", "reason": "新闻按检索时间记录；发布日期较旧不直接判失效"})
    return {
        "policy_version": FRESHNESS_POLICY_VERSION,
        "mode": "knowledge_time",  # 本次知识时点研究：行情为截至时点，不写成当前价
        "label": "本次知识时点研究" + ("（行情截至 %s）" % _iso(market) if market else "（行情时间未知）"),
        "market": {"cutoff": _iso(market), **stale(market, MARKET_STALE_HOURS, "行情")},
        "technical": {"cutoff": _iso(tech), **stale(tech, TECH_STALE_HOURS, "技术面K线")},
        "news": {"latest_published_at": _iso(news_pub), "retrieved_at": _iso(news_ret), **news_status},
        "thresholds_hours": {"market": MARKET_STALE_HOURS, "technical": TECH_STALE_HOURS, "news": None},
        "evaluated_at": _iso(now),
    }


def automatic_checks(*, content, contracts, allowed_evidence_ids, drafts, evidence_rows, now, requested_search=False) -> dict:
    """RES-14 deterministic checks. Result is 'automatic' only; never a human verdict."""
    text = content if isinstance(content, str) else json.dumps(content, ensure_ascii=False)
    allowed = set(allowed_evidence_ids or [])
    cited = sorted(set(EVIDENCE_ID_RE.findall(text or "")))
    invalid = [c for c in cited if c not in allowed]
    syms = []
    for c in contracts or []:
        s = c.get("symbol") if isinstance(c, dict) else c
        if s:
            syms.append(s)
    def mentioned(s):
        base = s[:-4] if s.endswith("USDT") else s
        return s in text or (len(base) >= 2 and re.search(r"(?<![A-Za-z])" + re.escape(base) + r"(?![A-Za-z])", text or "") is not None)
    missing_syms = [s for s in syms if not mentioned(s)]
    known_fields = set()
    for r in evidence_rows or []:
        if isinstance(r, dict):
            known_fields |= {k for k, v in r.items() if v is not None}
    dep_issues = []
    for d in drafts or []:
        deps = d.get("depends_on") or []
        for f in deps if isinstance(deps, list) else [deps]:
            if not isinstance(f, str) or f not in known_fields:
                dep_issues.append({"draft_id": d.get("draft_id"), "field": f, "reason": "依赖字段不在本次冻结证据中"})
        ds = d.get("symbol")
        if ds and ds not in syms:
            dep_issues.append({"draft_id": d.get("draft_id"), "field": ds, "reason": "草案币种不在本次选中合约中"})
    bad_line = None
    for line in (text or "").splitlines()[:6]:
        cur = re.search(r"(当前价格|当前价|现价)\s*[:：]", line)
        if cur and not re.search(r"截至|快照|可得于", line):
            bad_line = cur.group(1)
            break
    items_time = {"ok": bad_line is None,
                  "detail": ("开头写成“%s”，同一行未注明截至时间；REV-07 要求按知识时点表述" % bad_line) if bad_line else None}
    items = {
        "opening_not_current_price": items_time,
        "non_empty": {"ok": bool((text or "").strip()), "detail": None if (text or "").strip() else "正文为空"},
        "citations_exist": {"ok": not invalid, "cited": cited, "invalid": invalid,
                            "detail": ("引用了不在允许列表中的证据：" + "、".join(invalid)) if invalid else None},
        "contracts_covered": {"ok": not missing_syms, "missing": missing_syms,
                              "detail": ("正文未提及选中合约：" + "、".join(missing_syms)) if missing_syms else None},
        "strategy_dependencies": {"ok": not dep_issues, "issues": dep_issues},
    }
    if requested_search:
        has_reference = bool(allowed.intersection(cited))
        items["external_evidence_cited"] = {"ok": has_reference,
            "detail": None if has_reference else "本轮要求检索，但正文未引用任何本轮已登记外部证据ID"}
    ok = all(v["ok"] for v in items.values())
    return {"kind": "automatic", "checker": "program", "version": CHECKS_VERSION, "at": _iso(now),
            "status": "passed" if ok else "failed", "items": items,
            "scope_note": "自动校验只覆盖已定义的结构/引用存在性/合约覆盖/依赖字段；引用存在不等于来源支持结论，不代表内容充分或人工已核验"}



def generation_acceptance(schema_validation, automatic):
    """Accept only deterministic checks; pending human review is not a hard failure."""
    reasons = []
    if not (schema_validation or {}).get("ok"):
        reasons.append((schema_validation or {}).get("detail") or "报告结构未通过校验")
    for name, item in ((automatic or {}).get("items") or {}).items():
        if not item.get("ok"):
            reasons.append(item.get("detail") or name)
    if (automatic or {}).get("status") != "passed" and not reasons:
        reasons.append("自动校验未通过")
    return {"version": "generation-acceptance-v1", "status": "rejected" if reasons else "accepted",
            "strategy_eligible": not reasons, "reasons": reasons,
            "note": "仅为程序结构和一致性检查；不表示内容或人工核验通过"}


def checks_block(auto: dict) -> dict:
    """REV-09: automatic / content-review todo / human are separate and never merged."""
    return {
        "automatic": auto,
        "content_review": {"status": "pending", "label": "内容核验待办",
                           "todo": ["数字与单位能否回指证据", "来源是否真正支持结论", "关键催化是否遗漏", "是否有无依据增长/因果/精确胜率"]},
        "human": {"status": "not_reviewed", "label": "未人工核验（可阅读，但不等于人工通过）", "records": []},
    }


class ReportRevisions:
    """RES-16: revisions after generation. Never rewrites report.json content or prompt_input.json."""

    def __init__(self, reports_dir: Path, clock=None):
        self.dir = Path(reports_dir)
        self.clock = clock or (lambda: dt.datetime.now(UTC))

    def _rdir(self, report_id):
        if not re.fullmatch(r"report-[a-f0-9]{32}", report_id or ""):
            raise KeyError(report_id)
        d = self.dir / report_id
        if not (d / "report.json").is_file():
            raise KeyError(report_id)
        return d

    def _load(self, d, name):
        p = d / name
        return json.loads(p.read_text(encoding="utf-8")) if p.is_file() else []

    def _write(self, d, name, data):
        tmp = d / (name + ".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
        tmp.replace(d / name)

    def add_revision(self, report_id, payload) -> dict:
        d = self._rdir(report_id)
        if not isinstance(payload, dict):
            raise ValueError("请求体必须是对象")
        editor = (payload.get("editor") or "").strip() if isinstance(payload.get("editor"), str) else ""
        reason = (payload.get("reason") or "").strip() if isinstance(payload.get("reason"), str) else ""
        content = payload.get("content")
        if not editor or not reason:
            raise ValueError("修订必须写明修改人 editor 和修改原因 reason")
        if not isinstance(content, str) or not content.strip():
            raise ValueError("修订正文 content 不能为空")
        added = payload.get("added_evidence") or []
        if not isinstance(added, list) or any(not isinstance(e, dict) for e in added):
            raise ValueError("added_evidence 须为对象数组")
        revs = self._load(d, "revisions.json")
        n = len(revs) + 1
        now = self.clock()
        ev = []
        for i, e in enumerate(added):
            ev.append({**{k: e.get(k) for k in ("title", "source_url", "published_at", "excerpt")},
                       "evidence_id": "ev-post-%s-r%d-%d" % (report_id[-8:], n, i + 1),
                       "added_at": _iso(now), "phase": "post_generation",
                       "note": "生成后补充证据；原模型未看过"})
        rev = {"revision": n, "revision_id": "rev-" + uuid.uuid4().hex[:12], "base": "original" if n == 1 else n - 1,
               "created_at": _iso(now), "editor": editor, "reason": reason, "content": content,
               "added_evidence": ev, "model_saw_added_evidence": False}
        revs.append(rev)
        self._write(d, "revisions.json", revs)
        return {k: v for k, v in rev.items() if k != "content"}

    def add_human_review(self, report_id, payload) -> dict:
        d = self._rdir(report_id)
        if not isinstance(payload, dict):
            raise ValueError("请求体必须是对象")
        reviewer = payload.get("reviewer")
        result = payload.get("result")
        if not isinstance(reviewer, str) or not reviewer.strip():
            raise ValueError("人工核验必须写明核验人 reviewer")
        if result not in HUMAN_RESULTS:
            raise ValueError("result 只能是：" + "/".join(HUMAN_RESULTS))
        revs = self._load(d, "revisions.json")
        target = payload.get("revision", len(revs))
        if not isinstance(target, int) or target < 0 or target > len(revs):
            raise ValueError("revision 不存在（0=原始版本）")
        recs = self._load(d, "human_reviews.json")
        rec = {"review_id": "hr-" + uuid.uuid4().hex[:12], "at": _iso(self.clock()), "reviewer": reviewer.strip(),
               "result": result, "revision": target, "note": (payload.get("note") or "")[:2000] if isinstance(payload.get("note"), str) else "",
               "kind": "human"}
        recs.append(rec)
        self._write(d, "human_reviews.json", recs)
        return rec

    def overlay(self, report: dict) -> dict:
        """Attach revisions/human records for reading. Original content stays as report['content']."""
        try:
            d = self._rdir(report.get("report_id"))
        except KeyError:
            return report
        revs = self._load(d, "revisions.json")
        hrs = self._load(d, "human_reviews.json")
        out = dict(report)
        out["revisions"] = [{k: v for k, v in r.items() if k != "content"} for r in revs]
        out["current_version"] = revs[-1]["revision"] if revs else 0
        if revs:
            out["final_content"] = revs[-1]["content"]
        checks = dict(out.get("checks") or {})
        if checks:
            human = dict(checks.get("human") or {})
            human["records"] = hrs
            if hrs:
                last = hrs[-1]
                human["status"] = "reviewed"
                human["label"] = "人工核验：%s（%s，版本 %s）" % (last["result"], last["reviewer"], last["revision"])
            checks["human"] = human
            out["checks"] = checks
        return out
