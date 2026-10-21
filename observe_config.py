# -*- coding: utf-8 -*-
"""B 观察与小时边界配置：版本化默认值；新装默认关闭观察。"""
from __future__ import annotations

import copy

TIMING_DEFAULT = {
    "closeOffsetSec": 120,
    "schema_version": 1,
}

OBSERVE_DEFAULT = {
    "schema_version": 1,
    "enabled": False,
    "strategy_id": "B_ACCUMULATION_1H",
    "rule_version": "b-accum-1h-v1",
    "baseline_bars": 168,
    "event_ratio": 8.0,
    "confirm_ratio": 2.0,
    "max_abs_return": 0.03,
    "evidence_profile": "binance-1h-oi-lsr-v1",
    "oi_lsr_period": "1h",
    "oi_lsr_max_lag_ms": 300000,
}

from b_accumulation_rules import SPEC
for _k in OBSERVE_DEFAULT:
    if _k in SPEC:OBSERVE_DEFAULT[_k]=SPEC[_k]


def migrate_timing(value) -> dict:
    out = copy.deepcopy(TIMING_DEFAULT)
    if isinstance(value, dict):
        if "closeOffsetSec" in value:
            out["closeOffsetSec"] = value["closeOffsetSec"]
        if "schema_version" in value:
            out["schema_version"] = value["schema_version"]
    return validate_timing(out)


def migrate_observe(value) -> dict:
    out = copy.deepcopy(OBSERVE_DEFAULT)
    if isinstance(value, dict):
        for k in out:
            if k in value:
                out[k] = value[k]
    return validate_observe(out)


def validate_timing(value: dict) -> dict:
    if not isinstance(value, dict):
        raise ValueError("timing 须为对象")
    for req in TIMING_DEFAULT:
        if req not in value:
            raise ValueError(f"timing 缺少 {req}")
    if type(value["closeOffsetSec"]) is not int or not (0 <= value["closeOffsetSec"] <= 600):
        raise ValueError("closeOffsetSec 须为 0–600 整数秒")
    if type(value["schema_version"]) is not int:
        raise ValueError("timing.schema_version 须为整数")
    # reject unknown keys
    if set(value) != set(TIMING_DEFAULT):
        raise ValueError("timing 含未知字段")
    return copy.deepcopy(value)


def validate_observe(value: dict) -> dict:
    if not isinstance(value, dict):
        raise ValueError("observe 须为对象")
    for req in OBSERVE_DEFAULT:
        if req not in value:
            raise ValueError(f"observe 缺少 {req}")
    if set(value) != set(OBSERVE_DEFAULT):
        raise ValueError("observe 含未知字段")
    if type(value["enabled"]) is not bool:
        raise ValueError("observe.enabled 须布尔")
    if value["strategy_id"] != "B_ACCUMULATION_1H":
        raise ValueError("observe.strategy_id 首批固定 B_ACCUMULATION_1H")
    if type(value["baseline_bars"]) is not int or value["baseline_bars"] != 168:
        raise ValueError("baseline_bars 首批固定 168")
    if type(value["oi_lsr_max_lag_ms"]) is not int or value["oi_lsr_max_lag_ms"] < 0:
        raise ValueError("oi_lsr_max_lag_ms 无效")
    out=copy.deepcopy(value)
    for key, baseline in OBSERVE_DEFAULT.items():
        if key=='enabled':continue
        if isinstance(baseline,float):
            valid=isinstance(value[key],(int,float)) and not isinstance(value[key],bool) and value[key]==baseline
            if valid:out[key]=float(value[key])
        else:valid=type(value[key]) is type(baseline) and value[key]==baseline
        if not valid:raise ValueError('冻结规则配置不可静默修改: '+key)
    return out



def target_close_boundary_ms(now_ms: int, *, offset_sec: int = 120, bar_ms: int = 3_600_000) -> int:
    """本轮目标闭合边界：已收盘的上一整点 1H 收盘时刻（close_time = open+bar-1 的下一毫秒边界用 open+bar）。

    返回的是上一根已闭合 1H 的 open_time+bar_ms（即收盘边界时刻，整点）。
    调度应在 boundary + offset_sec 之后启动。
    """
    if offset_sec < 0:
        raise ValueError("offset_sec")
    # latest closed boundary = floor(now/bar)*bar ; if still within offset after boundary, wait → use previous
    boundary = (now_ms // bar_ms) * bar_ms
    if now_ms < boundary + offset_sec * 1000:
        boundary -= bar_ms
    return boundary
