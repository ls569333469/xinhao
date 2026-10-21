"""EMA三线金叉＋Squeeze v4：确定性规则组件（扫描/SIM/EVAL/BACKTEST共用）。

与旧固定百分比触价模式分离。本模块不发起行情/交易请求。
规则来源：DOCS/design/audit-gpt-ema-v4/给GROK-EMA-Squeeze增量实施规则-v4.md
"""
from __future__ import annotations

import datetime as dt
from typing import Optional

RULES_VERSION = "ema-squeeze-v4"
SCHEMA_EXEC = "ema99-close-trail-v1"
CANDIDATE_MAX_DAYS = 30
OI_CONTRACTION_RATIO = 0.70
VOL_CONFIRM_RATIO = 2.0
NEW_LOW_WINDOW = 10
NEW_LOW_MIN_COUNT = 3
AMPLITUDE_BARS = 20
AMPLITUDE_MAX = 1.35
UTC = dt.timezone.utc


def _finite(x) -> bool:
    try:
        return x is not None and float(x) == float(x) and abs(float(x)) != float("inf")
    except (TypeError, ValueError):
        return False


def full_cross(ema7, ema25, ema99, prev_ema7, prev_ema25, prev_ema99) -> Optional[bool]:
    """完全金叉：当前 EMA7>EMA25>EMA99 且上一根 EMA25<=EMA99。

    不再使用「上一根不是完整排列」代替。上一根相等允许；当前相等不算。
    """
    vals = (ema7, ema25, ema99, prev_ema25, prev_ema99)
    if not all(_finite(v) for v in vals):
        return None
    stack = float(ema7) > float(ema25) > float(ema99)
    crossed = float(prev_ema25) <= float(prev_ema99)
    return bool(stack and crossed)


def medium_cross(ema7, ema25, ema99, prev_ema7, prev_ema25, close) -> Optional[bool]:
    """中信号：EMA7 上一根<=EMA25、当前>EMA25，且收盘>EMA99。"""
    vals = (ema7, ema25, ema99, prev_ema7, prev_ema25, close)
    if not all(_finite(v) for v in vals):
        return None
    return bool(
        float(prev_ema7) <= float(prev_ema25)
        and float(ema7) > float(ema25)
        and float(close) > float(ema99)
    )




def death_trend_new_lows(closes, window: int = NEW_LOW_WINDOW, min_count: int = NEW_LOW_MIN_COUNT) -> Optional[bool]:
    """过去 window 根闭合收盘中，至少 min_count 次创窗口内此前最低收盘。

    第一根只作基准不计新低。严格小于才计数。未满 window / 无效 → None。
    """
    if closes is None or len(closes) < window:
        return None
    try:
        c = [float(x) for x in closes[-window:]]
    except (TypeError, ValueError):
        return None
    if any(x != x or abs(x) == float("inf") for x in c):
        return None
    count = 0
    for i in range(1, window):
        if c[i] < min(c[0:i]):
            count += 1
    return count >= min_count


def new_low_count(closes, window: int = NEW_LOW_WINDOW) -> Optional[int]:
    if closes is None or len(closes) < window:
        return None
    try:
        c = [float(x) for x in closes[-window:]]
    except (TypeError, ValueError):
        return None
    count = 0
    for i in range(1, window):
        if c[i] < min(c[0:i]):
            count += 1
    return count


def amplitude_ratio(highs, lows, bars: int = AMPLITUDE_BARS) -> Optional[float]:
    """max(high)/min(low) over last bars；缺失 → None。"""
    if highs is None or lows is None or len(highs) < bars or len(lows) < bars:
        return None
    try:
        hs = [float(x) for x in highs[-bars:]]
        ls = [float(x) for x in lows[-bars:]]
    except (TypeError, ValueError):
        return None
    if any(x <= 0 or x != x for x in hs + ls):
        return None
    lo = min(ls)
    if lo <= 0:
        return None
    return max(hs) / lo


def amplitude_ok(highs, lows, bars: int = AMPLITUDE_BARS, max_ratio: float = AMPLITUDE_MAX) -> Optional[bool]:
    r = amplitude_ratio(highs, lows, bars)
    if r is None:
        return None
    return r < max_ratio


def oi_contraction(entry_oi_usd, current_oi_usd, ratio: float = OI_CONTRACTION_RATIO) -> Optional[bool]:
    """当前美元OI 严格低于入池基准 * ratio → 萎缩出池。恰好等于不淘汰。缺失 → None。"""
    if not _finite(entry_oi_usd) or not _finite(current_oi_usd):
        return None
    entry = float(entry_oi_usd)
    cur = float(current_oi_usd)
    if entry <= 0 or cur < 0:
        return None
    return cur < entry * ratio


def parse_iso(ts) -> Optional[dt.datetime]:
    if ts is None:
        return None
    if isinstance(ts, dt.datetime):
        return ts if ts.tzinfo else ts.replace(tzinfo=UTC)
    try:
        s = str(ts).replace("Z", "+00:00")
        d = dt.datetime.fromisoformat(s)
        return d if d.tzinfo else d.replace(tzinfo=UTC)
    except Exception:
        return None


def candidate_expired(entered_at, now=None, days: int = CANDIDATE_MAX_DAYS) -> Optional[bool]:
    """从 entered_at 起算 days 天到期；不能从金叉时间起算。"""
    start = parse_iso(entered_at)
    if start is None:
        return None
    end = parse_iso(now) if now is not None else dt.datetime.now(UTC)
    if end is None:
        return None
    return (end - start) >= dt.timedelta(days=days)


def structure_still_holds_strong(ema7, ema25, ema99) -> Optional[bool]:
    if not all(_finite(v) for v in (ema7, ema25, ema99)):
        return None
    return float(ema7) > float(ema25) > float(ema99)


def structure_still_holds_medium(ema7, ema25, ema99, close) -> Optional[bool]:
    if not all(_finite(v) for v in (ema7, ema25, ema99, close)):
        return None
    return float(ema7) > float(ema25) and float(close) > float(ema99)


def initial_stop_state(*, cross_ema99, actual_entry_price, candidate_cycle_id=None,
                       cross_time=None, confirm_time=None, entry_time=None) -> dict:
    if not _finite(cross_ema99):
        raise ValueError("cross_ema99 必须为有效数字")
    if not _finite(actual_entry_price):
        raise ValueError("actual_entry_price 必须为有效数字")
    return {
        "schema": SCHEMA_EXEC,
        "rules_version": RULES_VERSION,
        "candidate_cycle_id": candidate_cycle_id,
        "cross_time": cross_time,
        "confirm_time": confirm_time,
        "entry_time": entry_time,
        "cross_ema99": float(cross_ema99),
        "stop": float(cross_ema99),
        "actual_entry_price": float(actual_entry_price),
        "trailing_activated": False,
        "pending_exit": False,
        "pending_exit_reason": None,
        "last_processed_bar_time": None,
    }


def process_close_for_trail(state: dict, *, close, ema99, bar_time=None) -> dict:
    """按 v4 §5.2 顺序处理一根闭合K线。

    1. 先用开盘前已生效的 S 检查 close < S → 发出退出待执行
    2. 若未触发，检查是否首次收盘高于实际买入价
    3. 若追踪已开，S_next = max(S, EMA99)，从下一根生效

    盘中触价不退出；相等不退出。返回新状态副本。
    """
    if not isinstance(state, dict) or state.get("schema") != SCHEMA_EXEC:
        raise ValueError("需要 ema99-close-trail-v1 状态")
    if state.get("pending_exit"):
        out = dict(state)
        out["last_processed_bar_time"] = bar_time
        return out
    if not _finite(close):
        raise ValueError("close 无效")
    s = float(state["stop"])
    entry = float(state["actual_entry_price"])
    out = dict(state)
    out["last_processed_bar_time"] = bar_time

    if float(close) < s:
        out["pending_exit"] = True
        out["pending_exit_reason"] = "close_below_stop"
        return out

    if not out.get("trailing_activated") and float(close) > entry:
        out["trailing_activated"] = True

    if out.get("trailing_activated") and _finite(ema99):
        out["stop"] = max(float(out["stop"]), float(ema99))

    return out


def resolve_ema99_exit_fill(*, pending_exit: bool, next_open) -> Optional[dict]:
    """若已有收盘退出信号，在下一可执行开盘参考成交。开盘低于 stop 不绕过收盘条件。"""
    if not pending_exit:
        return None
    if not _finite(next_open):
        return None
    return {
        "exit_price": float(next_open),
        "exit_reason": "ema99_close_trail",
        "fill_rule": "next_open_after_close_exit_signal",
    }
