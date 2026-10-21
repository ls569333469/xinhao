# -*- coding: utf-8 -*-
"""B_ACCUMULATION_1H 纯规则：不发网络、不写库、不发通知。

时间：内部 UTC 毫秒。百分比阈值用小数：0.03=3%。
资格三态：pass / fail / unknown；pending 仅表示等待未闭合确认根。
"""
from __future__ import annotations

from typing import Any, Optional

from strategy_registry import REGISTRY
SPEC = next(s for s in REGISTRY["observationStrategies"] if s["id"]=="B_ACCUMULATION_1H")
STRATEGY_ID = SPEC["id"]
RULE_VERSION = SPEC["rule_version"]
TIMEFRAME = "1h"
BAR_MS = 3_600_000
BASELINE_BARS = SPEC["baseline_bars"]
EVENT_RATIO = SPEC["event_ratio"]
CONFIRM_RATIO = SPEC["confirm_ratio"]
MAX_ABS_RETURN = SPEC["max_abs_return"]  # 等于 3% 不通过


def _finite(x) -> bool:
    try:
        return x is not None and float(x) == float(x) and abs(float(x)) != float("inf")
    except (TypeError, ValueError):
        return False


def continuous_open_times(opens: list[int], step: int = BAR_MS) -> bool:
    if len(opens) < 2:
        return len(opens) == 1
    for i in range(1, len(opens)):
        if opens[i] - opens[i - 1] != step:
            return False
    return True


def dedupe_bars_by_open(bars: list[dict]) -> list[dict]:
    """同 open_time 保留最后一根；再按 open_time 升序。"""
    by = {}
    for b in bars:
        ot = b.get("open_time")
        if ot is None:
            continue
        by[int(ot)] = b
    return [by[k] for k in sorted(by)]


def volume_ratio(bars_asc: list[dict], index: int, lookback: int, *, include_prior_event_bar: bool) -> Optional[float]:
    """index 为事件/确认根在 bars_asc 中的下标。

    事件根 h：分母为前 lookback 根，不含 h。
    确认根 h+1：分母为含 h 的 lookback 根，不含 h+1。
    """
    if index < 0 or index >= len(bars_asc):
        return None
    q = bars_asc[index].get("q")
    if not _finite(q):
        return None
    if include_prior_event_bar:
        # mean(q[h+1-168 : h+1]) includes h, excludes h+1 → bars[index-168 : index]
        start = index - lookback
        end = index
    else:
        # mean(q[h-168:h]) excludes h → bars[index-lookback : index]
        start = index - lookback
        end = index
    if start < 0:
        return None
    window = bars_asc[start:end]
    if len(window) != lookback:
        return None
    opens = [int(b["open_time"]) for b in window]
    if not continuous_open_times(opens + [int(bars_asc[index]["open_time"])]):
        return None
    vals = [b.get("q") for b in window]
    if float(q) < 0 or any(not _finite(v) or float(v) < 0 for v in vals):
        return None
    mean = sum(float(v) for v in vals) / lookback
    if mean <= 0:
        return None
    return float(q) / mean


def abs_return_ok(bar: dict) -> Optional[bool]:
    o, c = bar.get("o"), bar.get("c")
    if not _finite(o) or not _finite(c) or float(o) <= 0 or float(c) <= 0:
        return None
    ret = abs(float(c) / float(o) - 1.0)
    if ret < MAX_ABS_RETURN:
        return True
    return False  # == or > 3% fail


def ema_stack_ok(ema7, ema25) -> Optional[bool]:
    if not _finite(ema7) or not _finite(ema25):
        return None
    return float(ema7) > float(ema25)


def structure_gate(*, oi_cap_pct, lsr, oi_quality="pass", lsr_quality="pass", cap_quality="pass") -> str:
    """返回 pass/fail/unknown。OI/市值>30%、LSR<0.80；等号失败。"""
    # Evaluate independent known values before combining unknown inputs.
    oi = "unknown" if oi_quality != "pass" or cap_quality != "pass" or not _finite(oi_cap_pct) else ("pass" if float(oi_cap_pct)>30 else "fail")
    ratio = "unknown" if lsr_quality != "pass" or not _finite(lsr) else ("pass" if float(lsr)<.80 else "fail")
    return combine_tri(oi, ratio)



def combine_tri(*states: str) -> str:
    """fail 优先；无 fail 有 unknown → unknown；全 pass → pass。"""
    if any(s == "fail" for s in states):
        return "fail"
    if any(s == "unknown" for s in states):
        return "unknown"
    if all(s == "pass" for s in states):
        return "pass"
    return "unknown"


def evaluate_h_event(
    bars_asc: list[dict],
    h_index: int,
    *,
    bg4h: dict | None,
) -> dict:
    """评估事件根 h（技术层）。bars 须已去重升序。"""
    out = {
        "strategy_id": STRATEGY_ID,
        "rule_version": RULE_VERSION,
        "timeframe": TIMEFRAME,
        "technical_event": "unknown",
        "ratio": None,
        "abs_return_ok": None,
        "structure_h_bg": None,
        "reasons": [],
    }
    bars = dedupe_bars_by_open(bars_asc)
    # re-find index after dedupe by open_time
    if h_index < 0 or h_index >= len(bars_asc):
        out["reasons"].append("bad_index")
        return out
    target_ot = int(bars_asc[h_index]["open_time"])
    bars = dedupe_bars_by_open(bars_asc)
    try:
        idx = next(i for i, b in enumerate(bars) if int(b["open_time"]) == target_ot)
    except StopIteration:
        out["reasons"].append("h_missing_after_dedupe")
        return out

    if idx < BASELINE_BARS:
        out["reasons"].append("warmup_short")
        return out

    ratio = volume_ratio(bars, idx, BASELINE_BARS, include_prior_event_bar=False)
    out["ratio"] = ratio
    ret_ok = abs_return_ok(bars[idx])
    out["abs_return_ok"] = ret_ok
    stack = None
    if bg4h:
        stack = ema_stack_ok(bg4h.get("ema7"), bg4h.get("ema25"))
        out["structure_h_bg"] = stack

    checks = []
    if ratio is None:
        checks.append("unknown")
        out["reasons"].append("baseline_unknown")
    elif ratio >= EVENT_RATIO:
        checks.append("pass")
    else:
        checks.append("fail")
        out["reasons"].append("ratio_fail")

    if ret_ok is None:
        checks.append("unknown")
        out["reasons"].append("return_unknown")
    elif ret_ok:
        checks.append("pass")
    else:
        checks.append("fail")
        out["reasons"].append("return_fail")

    if stack is None:
        checks.append("unknown")
        out["reasons"].append("bg4h_unknown")
    elif stack:
        checks.append("pass")
    else:
        checks.append("fail")
        out["reasons"].append("bg4h_fail")

    out["technical_event"] = combine_tri(*checks)
    out["open_time"] = target_ot
    out["expected_confirmation_open_time"] = target_ot + BAR_MS
    return out


def evaluate_h1_confirm(
    bars_asc: list[dict],
    h_open_time: int,
    *,
    bg4h_h1: dict | None,
    h1_closed: bool,
) -> dict:
    out = {
        "technical_confirmation": "unknown",
        "ratio": None,
        "pending": False,
        "reasons": [],
    }
    bars = dedupe_bars_by_open(bars_asc)
    expect = int(h_open_time) + BAR_MS
    try:
        idx = next(i for i, b in enumerate(bars) if int(b["open_time"]) == expect)
    except StopIteration:
        if not h1_closed:
            out["technical_confirmation"] = "pending"
            out["pending"] = True
            out["reasons"].append("h1_not_closed")
            return out
        out["reasons"].append("h1_missing")
        return out

    if not h1_closed:
        out["technical_confirmation"] = "pending"
        out["pending"] = True
        out["reasons"].append("h1_not_closed")
        return out

    if idx < BASELINE_BARS:
        out["reasons"].append("warmup_short")
        return out

    ratio = volume_ratio(bars, idx, BASELINE_BARS, include_prior_event_bar=True)
    out["ratio"] = ratio
    stack = ema_stack_ok(bg4h_h1.get("ema7"), bg4h_h1.get("ema25")) if bg4h_h1 else None

    checks = []
    if ratio is None:
        checks.append("unknown")
        out["reasons"].append("confirm_baseline_unknown")
    elif ratio >= CONFIRM_RATIO:
        checks.append("pass")
    else:
        checks.append("fail")
        out["reasons"].append("confirm_ratio_fail")

    if stack is None:
        checks.append("unknown")
        out["reasons"].append("bg4h_h1_unknown")
    elif stack:
        checks.append("pass")
    else:
        checks.append("fail")
        out["reasons"].append("bg4h_h1_fail")

    out["technical_confirmation"] = combine_tri(*checks)
    out["open_time"] = expect
    return out


def risk_theory(*, p_theory, s0) -> str:
    if not _finite(p_theory) or not _finite(s0):
        return "unknown"
    if float(p_theory) > float(s0):
        return "pass"
    return "fail"
