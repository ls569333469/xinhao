"""EMA-Squeeze v4 候选周期 / cross 等待持久化。

入池时间与 OI 基准不因重扫或重启刷新。出池不自动平仓。
"""
from __future__ import annotations

import json
from ema_observations import finite, time_ms
from snapshot_store import atomic
from ema_event_timing import process as process_event, recover_pending
import threading
import uuid
from pathlib import Path

from ema_squeeze_rules import (
    RULES_VERSION,
    candidate_expired,
    death_trend_new_lows,
    oi_contraction,
)

LOCK = threading.Lock()
DEFAULT_NAME = "ema-squeeze-candidates.json"


def _now_iso():
    import datetime as dt
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="microseconds")


class EmaSqueezeStore:
    def __init__(self, root: Path):
        self.root = Path(root)
        self.path = self.root / "runtime" / DEFAULT_NAME
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def _load(self) -> dict:
        if not self.path.exists():
            return {"schema": "ema-squeeze-candidates-v1", "rules_version": RULES_VERSION, "cycles": {}}
        raw = json.loads(self.path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict) or not isinstance(raw.get("cycles", {}), dict):
            raise ValueError("候选池格式损坏，保留原文件并停止更新")
        raw.setdefault("cycles", {})
        return raw

    def _save(self, data: dict) -> None:
        atomic(self.path, json.dumps(data, ensure_ascii=False, indent=2, allow_nan=False).encode('utf-8'))

    @staticmethod
    def _key(market, symbol):
        return f"{market}|{symbol}"

    def get_active(self, market, symbol) -> dict | None:
        with LOCK:
            data = self._load()
            cyc = data["cycles"].get(self._key(market, symbol))
            if not cyc or cyc.get("status") != "active":
                return None
            return dict(cyc)

    def persist(self) -> dict:
        """扫完落盘：即使本轮无人入池，也写出 runtime/ema-squeeze-candidates.json。"""
        with LOCK:
            data = self._load()
            data.setdefault("schema", "ema-squeeze-candidates-v1")
            data.setdefault("rules_version", RULES_VERSION)
            data.setdefault("cycles", {})
            self._save(data)
            return {
                "path": str(self.path),
                "active": sum(1 for c in data["cycles"].values() if c.get("status") == "active"),
                "cycles": len(data["cycles"]),
            }

    def list_active(self) -> list:
        with LOCK:
            data = self._load()
            return [dict(c) for c in data["cycles"].values() if c.get("status") == "active"]

    def enter_or_keep(self, *, market, symbol, oi_usd, evidence=None, now=None) -> dict:
        """满足入池条件时：已有活跃周期则保留首次 entered_at / entry_oi；否则新建。"""
        key = self._key(market, symbol)
        with LOCK:
            data = self._load()
            cur = data["cycles"].get(key)
            if cur and cur.get("status") == "active":
                # 不刷新首次入池时间与 OI 基准
                cur["last_seen_at"] = now or _now_iso()
                if evidence:
                    cur["last_evidence"] = evidence
                data["cycles"][key] = cur
                self._save(data)
                return dict(cur)
            if finite(oi_usd, True) is None:
                raise ValueError("入池需要有限且>0的 entry_oi_usd")
            cycle_id = str(uuid.uuid4())
            entered = now or _now_iso()
            rec = {
                "candidate_cycle_id": cycle_id,
                "market": market,
                "symbol": symbol,
                "status": "active",
                "entered_at": entered,
                "entry_oi_usd": float(oi_usd),
                "entry_oi_unit": "usd_notional",
                "entry_oi_source_at": (evidence or {}).get("oiAsOf"),
                "entry_oi_retrieved_at": entered,
                "rules_version": RULES_VERSION,
                "last_seen_at": entered,
                "exit_reason": None,
                "exited_at": None,
                "pending_cross": None,
                "confirmed_events": [],
                "broke_below_ema": False,
                "last_evidence": evidence or {},
            }
            if cur:
                data.setdefault("archived_cycles", {})[cur["candidate_cycle_id"]] = cur
            data["cycles"][key] = rec
            self._save(data)
            return dict(rec)

    def exit_cycle(self, *, market, symbol, reason, now=None) -> dict | None:
        key = self._key(market, symbol)
        with LOCK:
            data = self._load()
            cur = data["cycles"].get(key)
            if not cur or cur.get("status") != "active":
                return None
            cur["status"] = "exited"
            cur["exit_reason"] = reason
            cur["exited_at"] = now or _now_iso()
            cur["pending_cross"] = None
            data["cycles"][key] = cur
            self._save(data)
            return dict(cur)


    @staticmethod
    def _attach_cycle_events(summary: dict, cycle: dict | None) -> dict:
        """把本候选周期已确认事件挂到摘要，供池/确认视图使用（≠本根 ignitionGrade）。"""
        if not cycle:
            summary.setdefault("confirmedEvents", [])
            summary.setdefault("latestConfirm", None)
            summary.setdefault("brokeAboveThree", None)
            return summary
        events = []
        seen = set()
        for ev in cycle.get("confirmed_events") or []:
            if not isinstance(ev, dict):
                continue
            eid = ev.get("event_id")
            if not eid or ev.get("void") or ev.get("duplicate") or ev.get("blocked") or ev.get("candidate_cycle_id") != cycle.get("candidate_cycle_id"):
                continue
            if eid in seen:
                continue
            seen.add(eid)
            events.append(dict(ev))
        # 本轮刚确认的 crossConfirm 也并入（避免未落盘间隙）
        cc = summary.get("crossConfirm")
        if isinstance(cc, dict) and cc.get("candidate_cycle_id") == cycle.get("candidate_cycle_id") and cc.get("event_id") and not cc.get("void") and not cc.get("duplicate") and not cc.get("blocked"):
            if cc["event_id"] not in seen:
                events.append(dict(cc))
        summary["confirmedEvents"] = events
        summary["latestConfirm"] = events[-1] if events else None
        # store 字段 broke_below_ema 实际表示「已突破三线上方仍留池」
        summary["brokeAboveThree"] = bool(cycle.get("broke_below_ema"))
        summary["pendingCross"] = dict(cycle["pending_cross"]) if cycle.get("pending_cross") else None
        return summary

    def evaluate_row(self, row: dict, *, now=None) -> dict:
        """根据当轮扫描行更新候选生命周期与 pending cross。

        入池条件（Stage1）：①② + sidewaysOk（含新低/振幅）+ belowAllEma 初入；
        已入池后突破三线仍保留。返回附加到 row 的 stage 摘要。
        """
        market = row.get("market")
        symbol = row.get("symbol")
        summary = {
            "candidateCycleId": None,
            "candidateStatus": None,
            "candidateEnteredAt": None,
            "entryOiUsd": None,
            "oiVsEntry": None,
            "pendingCross": None,
            "crossConfirm": None,
            "stageNote": None,
        }
        if not market or not symbol:
            return summary

        signals = row.get("signals") or []
        core12 = len(signals) >= 2 and signals[0] is True and signals[1] is True
        sideways = row.get("sidewaysOk") is True
        below = row.get("belowAllEma") is True
        oi = row.get("oiUsd")
        closes = row.get("_closes4h")  # optional full series if attached
        death = row.get("deathTrend")
        if death is None and closes is not None:
            death = death_trend_new_lows(closes)

        active = self.get_active(market, symbol)
        now_s = now or _now_iso()

        # 淘汰检查（仅未入场候选）
        if active:
            summary["candidateCycleId"] = active["candidate_cycle_id"]
            summary["candidateEnteredAt"] = active["entered_at"]
            summary["entryOiUsd"] = active["entry_oi_usd"]
            if time_ms(row.get('candleAsOf')) is not None and time_ms(active.get('last_processed_bar_time')) is not None and time_ms(row['candleAsOf']) < time_ms(active['last_processed_bar_time']):
                summary.update(candidateStatus='active', ignoredReason='older_bar')
                return self._attach_cycle_events(summary, active)
            if candidate_expired(active["entered_at"], now_s):
                self.exit_cycle(market=market, symbol=symbol, reason="expired_30d", now=now_s)
                summary["candidateStatus"] = "exited"
                summary["stageNote"] = "expired_30d"
                return summary
            if death is True:
                self.exit_cycle(market=market, symbol=symbol, reason="new_lows", now=now_s)
                summary["candidateStatus"] = "exited"
                summary["stageNote"] = "new_lows"
                return summary
            oc = oi_contraction(active["entry_oi_usd"], oi)
            if oc is True:
                self.exit_cycle(market=market, symbol=symbol, reason="oi_contraction", now=now_s)
                summary["candidateStatus"] = "exited"
                summary["stageNote"] = "oi_contraction"
                return summary
            if oc is None:
                summary["stageNote"] = "oi_unknown_blocks_new_entry"
            if active.get("entry_oi_usd") and oi is not None:
                try:
                    summary["oiVsEntry"] = float(oi) / float(active["entry_oi_usd"])
                except (TypeError, ValueError, ZeroDivisionError):
                    summary["oiVsEntry"] = None

            # 突破三线不删除
            if row.get("belowAllEma") is False:
                with LOCK:
                    data = self._load()
                    cur = data["cycles"].get(self._key(market, symbol))
                    if cur and cur.get("status") == "active":
                        cur["broke_below_ema"] = True
                        cur["last_seen_at"] = now_s
                        data["cycles"][self._key(market, symbol)] = cur
                        self._save(data)

            active = self.get_active(market, symbol) or active
            summary["candidateStatus"] = "active"
            # pending / new cross
            summary.update(self._process_cross(row, active, now_s))
            active2 = self.get_active(market, symbol) or active
            summary["candidateCycleId"] = active2.get("candidate_cycle_id") or summary.get("candidateCycleId")
            summary["candidateEnteredAt"] = active2.get("entered_at") or summary.get("candidateEnteredAt")
            summary["entryOiUsd"] = active2.get("entry_oi_usd") if active2.get("entry_oi_usd") is not None else summary.get("entryOiUsd")
            return self._attach_cycle_events(summary, active2)

        # 新入池：①② + sideways + 三线下方；OI 必须有效
        if core12 and sideways and below:
            if finite(oi, True) is None:
                summary["stageNote"] = "pool_gate_ok_but_oi_missing"
                return summary
            rec = self.enter_or_keep(
                market=market, symbol=symbol, oi_usd=oi,
                evidence={
                    "oiAsOf": row.get("oiAsOf"),
                    "rsi4h": row.get("rsi4h"),
                    "amplitudeRatio20": row.get("amplitudeRatio20"),
                    "registryHash": row.get("registryHash"),
                },
                now=now_s,
            )
            summary["candidateCycleId"] = rec["candidate_cycle_id"]
            summary["candidateStatus"] = "active"
            summary["candidateEnteredAt"] = rec["entered_at"]
            summary["entryOiUsd"] = rec["entry_oi_usd"]
            summary["stageNote"] = "entered"
            return self._attach_cycle_events(summary, rec)

        summary["candidateStatus"] = "none"
        return summary

    def _process_cross(self, row: dict, active: dict, now_s: str) -> dict:
        key = self._key(active["market"], active["symbol"])
        with LOCK:
            data = self._load()
            cur = data["cycles"].get(key)
            if not cur or cur.get("status") != "active" or cur.get("candidate_cycle_id") != active.get("candidate_cycle_id"):
                return {"pendingCross": None, "crossConfirm": None}
            recovered = recover_pending(self.root, cur, row, now_s)
            out = process_event(cur, row, now_s)
            if recovered:
                out["eventTransitions"] = recovered.get("eventTransitions", []) + out.get("eventTransitions", [])
                out["recovery"] = {"historical": True, "automatic_fill": False,
                                   "transitions": recovered.get("eventTransitions", [])}
                if not out.get("crossConfirm"):
                    out["crossConfirm"] = recovered.get("crossConfirm")
            self._save(data)
            return out




def hydrate_rows_from_store(rows: list, store: "EmaSqueezeStore | None" = None, root: Path | None = None) -> int:
    """把持久化池/确认事件合并进已有扫描行的 emaSqueezeStage（不改交易计算）。

    返回更新行数。用于 /api/scan 读出时补齐旧快照缺字段；本根 ignitionGrade 不覆盖。
    """
    if not rows:
        return 0
    if store is None:
        if root is None:
            root = Path(__file__).resolve().parent
        store = EmaSqueezeStore(root)
    active_list = store.list_active()
    by_key = {store._key(c.get("market"), c.get("symbol")): c for c in active_list}
    updated = 0
    for row in rows:
        key = store._key(row.get("market"), row.get("symbol"))
        cyc = by_key.get(key)
        if not cyc:
            continue
        st = dict(row.get("emaSqueezeStage") or {})
        if st.get("candidateCycleId") != cyc.get("candidate_cycle_id"):
            for field in ("crossConfirm", "pendingCross", "confirmedEvents", "latestConfirm", "eventTransitions", "recovery"):
                st.pop(field, None)
        st["hydratedFromCurrentPool"] = True
        st["candidateStatus"] = "active"
        st["candidateCycleId"] = cyc.get("candidate_cycle_id")
        st["candidateEnteredAt"] = cyc.get("entered_at")
        st["entryOiUsd"] = cyc.get("entry_oi_usd")
        oi = row.get("oiUsd")
        try:
            st["oiVsEntry"] = (float(oi) / float(cyc["entry_oi_usd"])) if oi is not None and cyc.get("entry_oi_usd") else st.get("oiVsEntry")
        except (TypeError, ValueError, ZeroDivisionError):
            pass
        if cyc.get("pending_cross") and not st.get("pendingCross"):
            st["pendingCross"] = dict(cyc["pending_cross"])
        # 保留行上已有 crossConfirm；再挂本周期全部确认事件
        EmaSqueezeStore._attach_cycle_events(st, cyc)
        row["emaSqueezeStage"] = st
        updated += 1
    return updated

