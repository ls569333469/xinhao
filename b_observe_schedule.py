# -*- coding: utf-8 -*-
"""B 观察小时调度：按闭合边界 + offset 对齐；默认关闭不入队。"""
from __future__ import annotations

import datetime as dt

from observe_config import target_close_boundary_ms

UTC = dt.timezone.utc
BAR_MS = 3_600_000


def next_observe_due_iso(now: dt.datetime, *, offset_sec: int = 120) -> str:
    """下一轮计划启动时刻 = 当前目标闭合边界 + offset；若已过则推到下一小时边界+offset。"""
    if now.tzinfo is None:
        now = now.replace(tzinfo=UTC)
    now_ms = int(now.timestamp() * 1000)
    boundary = target_close_boundary_ms(now_ms, offset_sec=offset_sec)
    start_ms = boundary + offset_sec * 1000
    if start_ms <= now_ms:
        # 本边界已触发过，排下一根
        boundary += BAR_MS
        start_ms = boundary + offset_sec * 1000
    return dt.datetime.fromtimestamp(start_ms / 1000, tz=UTC).isoformat(timespec="microseconds")


def should_fire(now: dt.datetime, next_due_iso: str | None, *, enabled: bool) -> bool:
    if not enabled:
        return False
    if not next_due_iso:
        return False
    due = dt.datetime.fromisoformat(next_due_iso)
    if due.tzinfo is None:
        due = due.replace(tzinfo=UTC)
    if now.tzinfo is None:
        now = now.replace(tzinfo=UTC)
    return due <= now


def in_flight_reuse_key(target_close_boundary: int) -> str:
    return f"b_observe_1h:{int(target_close_boundary)}"
