# -*- coding: utf-8 -*-
"""B 同期门禁：0 ≤ t − s ≤ max_lag_ms；s>t 或不符 → 不通过。"""
from __future__ import annotations

from typing import Any, Optional


def contemporaneous_ok(t_ms: int, s_ms: int | None, *, max_lag_ms: int = 300_000) -> str:
    """返回 pass / fail / unknown。"""
    if s_ms is None:
        return "unknown"
    try:
        t = int(t_ms)
        s = int(s_ms)
    except (TypeError, ValueError):
        return "unknown"
    delta = t - s
    if delta < 0:
        return "fail"  # s after t
    if delta > int(max_lag_ms):
        return "fail"
    return "pass"


def pick_sample_at_or_before(t_ms: int, samples: list[dict], *, time_key: str = "timestamp", max_lag_ms: int = 300_000) -> tuple[Optional[dict], str, Optional[int]]:
    """选不晚于 t、最接近 t 且满足门禁的样本。返回 (sample, quality, delta_ms)。"""
    if not samples:
        return None, "unknown", None
    best = None
    best_delta = None
    for row in samples:
        if not isinstance(row, dict):
            continue
        s = row.get(time_key)
        if s is None:
            continue
        try:
            s_i = int(s)
        except (TypeError, ValueError):
            continue
        delta = int(t_ms) - s_i
        if delta < 0:
            continue
        if best is None or delta < best_delta:
            best, best_delta = row, delta
    if best is None:
        return None, "unknown", None
    q = contemporaneous_ok(t_ms, int(best[time_key]), max_lag_ms=max_lag_ms)
    return best, q, best_delta
