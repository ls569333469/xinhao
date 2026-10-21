# -*- coding: utf-8 -*-
"""S1 r2 isolated contract judge (隔离契约判定器).

* stdlib only (json, sys, math, re, uuid, hashlib, pathlib, copy); no network, no DB,
  no import of any production module; all paths anchored at this file.
* NEVER writes files. Results go to stdout as JSON.
* Computes A/B/C confirm / end / gate results FROM fixture inputs, compares them
  with the fixture's expected values and with any declared (compat) summary
  fields, and validates schema examples (event / session / transition).

Usage (run inside the submission directory):
  python -I -B -X utf8 tests/s1_contract_judge.py                    # default = --set all
  python -I -B -X utf8 tests/s1_contract_judge.py --set valid        # valid fixtures + valid examples only
  python -I -B -X utf8 tests/s1_contract_judge.py --case <file>      # judge ONE file as a claim
  python -I -B -X utf8 tests/s1_contract_judge.py --codex-mutations  # replay Codex S1 mutations in memory

Exit codes: 0 = every claim consistent (and, for --set all / --codex-mutations,
every negative case detected); 1 = at least one contradiction in a claimed-valid
case (or in the single --case file); 2 = a negative/mutated case was NOT detected
(judge too lax); 3 = usage / IO / profile error.
"""
from __future__ import annotations

import copy
import hashlib
import json
import math
import re
import sys
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
H = 3_600_000
H4 = 14_400_000
DAY = 86_400_000

# Production constants (source refs in profiles/original-confirmation-freeze-v1.json "constants_sources").
FROZEN = {
    "A.VOL_CONFIRM_RATIO": 2.0, "A.CANDIDATE_MAX_DAYS": 30, "A.OI_CONTRACTION_RATIO": 0.70,
    "A.NEW_LOW_WINDOW": 10, "A.NEW_LOW_MIN_COUNT": 3, "A.AMPLITUDE_BARS": 20, "A.AMPLITUDE_MAX": 1.35,
    "A.SIDEWAYS_RSI_MIN_INCL": 30, "A.SIDEWAYS_RSI_MAX_INCL": 55, "A.POOL_OI_CAP_PCT_GT": 30, "A.POOL_LSR_LT": 0.80,
    "A.BAR_MS": 14_400_000,
    "B.BASELINE_BARS": 168, "B.EVENT_RATIO": 8.0, "B.CONFIRM_RATIO": 2.0, "B.MAX_ABS_RETURN": 0.03,
    "B.OI_CAP_PCT_GT": 30, "B.LSR_LT": 0.80, "B.OI_LSR_MAX_LAG_MS": 300_000, "B.CAP_MAX_AGE_MS": 3_600_000,
    "B.BAR_MS": 3_600_000, "B.BG_BAR_MS": 14_400_000, "B.BG_MIN_ROWS": 120,
    "C.RSI_LT": 25, "C.OI_CAP_PCT_GT": 30, "C.LSR_LT": 1.5, "C.NEW_LOWS14_MAX": 2, "C.NEW_LOW_WINDOW": 14,
    "C.C1_FIRST_OFFSET": 1, "C.C1_LAST_OFFSET": 10, "C.C1_VOL_RATIO_GT": 3.0, "C.VOL_PRIOR_BARS": 7,
    "C.INVALID_NEW_LOWS_GE": 3, "C.INVALID_ADJ_LSR_GE": 1.5, "C.BAR_MS": 14_400_000, "C.OI_LSR_MAX_LAG_MS": 300_000,
}

B_STRATEGY_ID = "B_ACCUMULATION_1H"
B_RULE_VERSION = "b-accum-1h-v1"
A_RULE_VERSION = "ema-squeeze-v4"
C_RULE_VERSION = "c-1015-v1.1"
ISO_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d{1,6})?(Z|\+00:00)$")


# ----------------------------------------------------------------- helpers
def fin(x):
    if x is None or isinstance(x, bool):
        return None
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return v if math.isfinite(v) else None


def tri(*states):
    """fail first; else unknown; all pass -> pass (b_accumulation_rules.combine_tri)."""
    if any(s == "fail" for s in states):
        return "fail"
    if any(s == "unknown" for s in states):
        return "unknown"
    if states and all(s == "pass" for s in states):
        return "pass"
    return "unknown"


def iso_ms(s):
    if not isinstance(s, str) or not ISO_RE.match(s):
        return None
    import datetime as dt
    d = dt.datetime.fromisoformat(s.replace("Z", "+00:00"))
    return int(round(d.timestamp() * 1000))


def same(a, b):
    if isinstance(a, bool) or isinstance(b, bool):
        return isinstance(a, bool) and isinstance(b, bool) and a is b
    if isinstance(a, (int, float)) and isinstance(b, (int, float)):
        return math.isclose(float(a), float(b), rel_tol=1e-9, abs_tol=1e-12)
    if isinstance(a, dict) and isinstance(b, dict):
        return set(a) == set(b) and all(same(a[k], b[k]) for k in a)
    if isinstance(a, list) and isinstance(b, list):
        return len(a) == len(b) and all(same(x, y) for x, y in zip(a, b))
    return a == b


def compare_expected(expected, actual, path="expected"):
    """Every key present in expected must equal the computed value (recursive into dicts)."""
    issues = []
    if isinstance(expected, dict) and isinstance(actual, dict):
        for k, v in expected.items():
            if k not in actual:
                issues.append(dict(code="expected_key_not_computed", path=f"{path}.{k}"))
                continue
            if isinstance(v, dict) and isinstance(actual[k], dict):
                issues += compare_expected(v, actual[k], f"{path}.{k}")
            elif not same(v, actual[k]):
                issues.append(dict(code="expected_mismatch", path=f"{path}.{k}", expected=v, computed=actual[k]))
        return issues
    if not same(expected, actual):
        issues.append(dict(code="expected_mismatch", path=path, expected=expected, computed=actual))
    return issues


def issue(code, **kw):
    return dict(code=code, **kw)


# ----------------------------------------------------------------- A  (ema_squeeze_rules / ema_event_timing / ema_squeeze_store)
def a_hold(level, emas, close):
    vals = [fin(e) for e in emas]
    c = fin(close)
    if any(v is None or v <= 0 for v in vals) or c is None:
        return None
    e7, e25, e99 = vals
    if level == "strong":
        return e7 > e25 > e99
    return e7 > e25 and c > e99


def a_cross_level(inp, K):
    e7, e25, e99, p7, p25, p99, c = (fin(inp.get(k)) for k in ("ema7", "ema25", "ema99", "prev_ema7", "prev_ema25", "prev_ema99", "close"))
    full = None if None in (e7, e25, e99, p25, p99) else bool(e7 > e25 > e99 and p25 <= p99)
    med = None if None in (e7, e25, e99, p7, p25, c) else bool(p7 <= p25 and e7 > e25 and c > e99)
    weak = None if None in (e7, e25, e99, c) else bool(c > e99 and e7 < e25)
    if full is True:
        lvl = "strong"
    elif med is True:
        lvl = "medium"
    elif weak is True:
        lvl = "weak_label"
    elif None in (full, med, weak):
        lvl = "unknown"
    else:
        lvl = "none"
    profile = {"strong": "A_strong_observation", "medium": "A_medium_observation"}.get(lvl)
    return dict(full_cross=full, medium_cross=med, weak_label=weak, technical_level=lvl,
                tracking_profile=profile, creates_tracking_session=profile is not None, weak_upgraded=False)


def a_confirm(inp, K):
    level = inp["level"]
    entered = inp.get("candidate_entered_at_ms")
    hist = bool(inp.get("historical"))
    x = inp["cross"]
    bar = int(x["bar_close_ms"])          # legacy bar_time = Binance close_time = open + 4h - 1
    thr = K["A.VOL_CONFIRM_RATIO"]
    res = dict(status=None, confirm_offset=None, reason=None, confirm_bar_close_ms=None,
               confirm_vol_ratio=None, progress_offsets=[], cross_bar_open_ms=bar - H4 + 1)
    if entered is None or bar <= int(entered):
        res.update(status="blocked", reason="cross_precedes_candidate_qualification")
        return res
    if hist:
        res.update(status="technical_only", reason="historical_qualification_missing")
        return res
    vol = fin(x.get("vol_ratio7"))
    vol = vol if vol is not None and vol >= 0 else None
    emas = [x.get("ema7"), x.get("ema25"), x.get("ema99")]
    if not x.get("quality_ok") or not x.get("oi_valid") or a_hold(level, emas, x.get("close")) is not True:
        res.update(status="blocked", reason="cross_evidence_unknown")
        return res
    nexts = sorted(inp.get("next_bars") or [], key=lambda b: int(b["bar_close_ms"]))
    if vol is not None and vol >= thr:
        res.update(status="confirmed", confirm_offset=0, confirm_bar_close_ms=bar, confirm_vol_ratio=vol)
        resolved, prev_ok = bar, True
    else:
        later = [b for b in nexts if int(b["bar_close_ms"]) > bar]
        if not later:
            res.update(status="pending", reason="waiting_next_bar_volume")
            return res
        b = later[0]
        bt = int(b["bar_close_ms"])
        v = fin(b.get("vol_ratio7"))
        v = v if v is not None and v >= 0 else None
        be = [b.get("ema7"), b.get("ema25"), b.get("ema99")]
        bc = fin(b.get("close"))
        if bt - bar != H4:
            r = "gap_missing_next_bar"
        elif not b.get("quality_ok") or any(fin(e) is None or fin(e) <= 0 for e in be) or bc is None or bc <= 0:
            r = "next_bar_structure_unknown"
        elif a_hold(level, be, bc) is not True:
            r = "structure_broken"
        elif v is None:
            r = "next_bar_volume_unknown"
        elif not b.get("oi_valid"):
            r = "next_bar_oi_unknown"
        elif v < thr:
            r = "next_bar_volume_fail"
        else:
            r = None
        if r:
            res.update(status="void", reason=r)
        else:
            res.update(status="confirmed", confirm_offset=1, confirm_bar_close_ms=bt, confirm_vol_ratio=v)
        resolved, prev_ok = bt, (v is not None and v >= thr)
    for b in nexts:
        bt = int(b["bar_close_ms"])
        if bt <= resolved:
            continue
        v = fin(b.get("vol_ratio7"))
        ok = v is not None and v >= thr
        if ok and prev_ok is not True:
            res["progress_offsets"].append((bt - bar) // H4)
        prev_ok = ok
    return res


def a_confirm_declared(fx, act, K):
    ts = fx.get("technical_signal")
    out = []
    if isinstance(ts, dict) and "status" in ts:
        out += _a_signal_checks(ts, act, K)
    ep = (fx.get("expect_profile") or {}).get("original_confirm")
    want = {"confirmed_on_T": ("confirmed", 0), "confirmed_on_T1": ("confirmed", 1), "failed": ("void", None), "not_yet": ("pending", None)}.get(ep)
    if ep is not None:
        if want is None:
            out.append(issue("unknown_expect_profile_value", value=ep))
        elif (act["status"], act["confirm_offset"]) != want:
            out.append(issue("expect_profile_contradiction", field="expect_profile.original_confirm", declared=ep, computed=[act["status"], act["confirm_offset"]]))
    return out


def _a_signal_checks(ts, act, K):
    out = []
    st = ts.get("status")
    c0, c1 = ts.get("cross_bar_open_ms"), ts.get("confirm_bar_open_ms")
    if st == "confirmed":
        if not isinstance(c0, int) or not isinstance(c1, int) or (c1 - c0) % H4:
            out.append(issue("A_confirm_bar_not_on_4h_grid", cross=c0, confirm=c1))
        else:
            off = (c1 - c0) // H4
            if off not in (0, 1):
                out.append(issue("A_confirm_outside_T_T1", declared_offset=off))
        v = fin(ts.get("confirm_vol_ratio"))
        if v is None or v < K["A.VOL_CONFIRM_RATIO"]:
            out.append(issue("A_confirm_vol_below_2", declared=ts.get("confirm_vol_ratio")))
        if ts.get("structure_hold") is not True:
            out.append(issue("A_confirm_structure_not_held"))
    if st != act["status"]:
        out.append(issue("declared_vs_computed_mismatch", field="technical_signal.status", declared=st, computed=act["status"]))
    if isinstance(c0, int) and c0 != act["cross_bar_open_ms"]:
        out.append(issue("declared_vs_computed_mismatch", field="technical_signal.cross_bar_open_ms", declared=c0, computed=act["cross_bar_open_ms"]))
    if st == "confirmed" and act["status"] == "confirmed":
        want_open = act["confirm_bar_close_ms"] - H4 + 1
        if isinstance(c1, int) and c1 != want_open:
            out.append(issue("declared_vs_computed_mismatch", field="technical_signal.confirm_bar_open_ms", declared=c1, computed=want_open))
        if not same(fin(ts.get("confirm_vol_ratio")), act["confirm_vol_ratio"]):
            out.append(issue("declared_vs_computed_mismatch", field="technical_signal.confirm_vol_ratio", declared=ts.get("confirm_vol_ratio"), computed=act["confirm_vol_ratio"]))
    return out


def death10(closes, K):
    w = K["A.NEW_LOW_WINDOW"]
    if not isinstance(closes, list) or len(closes) < w:
        return None, None
    c = [fin(x) for x in closes[-w:]]
    if None in c:
        return None, None
    n = sum(1 for i in range(1, w) if c[i] < min(c[:i]))
    return n >= K["A.NEW_LOW_MIN_COUNT"], n


def amplitude(highs, lows, K):
    n = K["A.AMPLITUDE_BARS"]
    if not isinstance(highs, list) or not isinstance(lows, list) or len(highs) < n or len(lows) < n:
        return None, None
    hs = [fin(x) for x in highs[-n:]]
    ls = [fin(x) for x in lows[-n:]]
    if any(x is None or x <= 0 for x in hs + ls):
        return None, None
    r = max(hs) / min(ls)
    return r, r < K["A.AMPLITUDE_MAX"]


def a_candidate_gate(inp, K):
    row = inp["row"]
    now = int(inp["now_ms"])
    cyc = inp.get("active_cycle")
    sig = row.get("signals") or []
    core12 = len(sig) >= 2 and sig[0] is True and sig[1] is True
    rsi = fin(row.get("rsi4h"))
    death, nl = death10(row.get("closes10"), K)
    ratio, amp_ok = amplitude(row.get("highs20"), row.get("lows20"), K)
    sideways = None if rsi is None or death is None or amp_ok is None else bool(
        K["A.SIDEWAYS_RSI_MIN_INCL"] <= rsi <= K["A.SIDEWAYS_RSI_MAX_INCL"] and death is False and amp_ok is True)
    emas = [fin(e) for e in (row.get("ema4h") or [])]
    close = fin(row.get("close"))
    below = None if close is None or len(emas) != 3 or None in emas else all(close < e for e in emas)
    oi = fin(row.get("oi_usd"))
    out = dict(core12=core12, sideways_ok=sideways, below_all_ema=below, death_trend=death,
               new_low_count10=nl, amplitude_ratio20=ratio, amplitude_ok=amp_ok,
               candidate_status=None, exit_reason=None, note=None, entered_at_ms=None, entry_oi_usd=None,
               broke_above_three=None)
    if cyc:
        out.update(entered_at_ms=cyc["entered_at_ms"], entry_oi_usd=cyc["entry_oi_usd"])   # never refreshed
        bar, last = row.get("bar_close_ms"), cyc.get("last_processed_bar_close_ms")
        if bar is not None and last is not None and int(bar) < int(last):
            out.update(candidate_status="active", note="ignored_older_bar")
            return out
        if now - int(cyc["entered_at_ms"]) >= K["A.CANDIDATE_MAX_DAYS"] * DAY:
            out.update(candidate_status="exited", exit_reason="expired_30d")
            return out
        if death is True:
            out.update(candidate_status="exited", exit_reason="new_lows")
            return out
        entry = fin(cyc["entry_oi_usd"])
        oc = None if entry is None or entry <= 0 or oi is None or oi < 0 else oi < entry * K["A.OI_CONTRACTION_RATIO"]
        if oc is True:
            out.update(candidate_status="exited", exit_reason="oi_contraction")
            return out
        out.update(candidate_status="active", note="oi_unknown_blocks_new_entry" if oc is None else None,
                   broke_above_three=bool(cyc.get("broke_above_three")) or below is False)
        return out
    if core12 and sideways is True and below is True:
        if oi is None or oi <= 0:
            out.update(candidate_status="none", note="pool_gate_ok_but_oi_missing")
            return out
        out.update(candidate_status="entered", entered_at_ms=now, entry_oi_usd=oi, broke_above_three=False)
        return out
    out.update(candidate_status="none")
    return out


def a_observation(inp, K):
    lvl = inp["level"]
    blank = dict(reason=None, end_reason=None, ended_at_close_ms=None, last_evaluated_open_ms=None, gap_from_open_ms=None)
    if lvl not in ("strong", "medium"):
        return dict(blank, session="none", reason="weak_label_not_tracked" if lvl == "weak_label" else "no_tracking_level")
    expected = int(inp["start_after_open_ms"]) + H4
    last = None
    stop = inp.get("human_stop_at_open_ms")
    stop = int(stop) if stop is not None else None
    blank = dict(blank, human_stop_at_open_ms=stop, natural_end_unverified=False)

    def stopped(gap, unverified):          # S1-R1-P2-02 spot-check: durable human stop, independent of data gaps
        t = human_stop_tail(stop, expected, dict(blank, last_evaluated_open_ms=last), note_gap=gap, natural_unverified=unverified)
        t["ended_at_close_ms"] = t["ended_at_open_ms"]   # stop boundary == close of the previous bar
        t["reason"] = None
        return t
    for b in sorted(inp.get("bars") or [], key=lambda r: int(r["open_ms"])):
        o = int(b["open_ms"])
        if stop is not None and o >= stop:
            break
        if o != expected:
            if stop is not None:
                return stopped(expected, True)
            return dict(blank, session="paused_awaiting_data", reason="missing_candles", last_evaluated_open_ms=last, gap_from_open_ms=expected)
        hold = a_hold(lvl, [b.get("ema7"), b.get("ema25"), b.get("ema99")], b.get("close"))
        if hold is None:
            if stop is not None:
                return stopped(o, True)
            return dict(blank, session="paused_awaiting_data", reason="evidence_unknown", last_evaluated_open_ms=last, gap_from_open_ms=o)
        if hold is False:
            return dict(blank, session="ended", end_reason="structure_invalid", ended_at_close_ms=o + H4, last_evaluated_open_ms=o)
        last, expected = o, o + H4
    if stop is not None:
        return stopped(None, False)
    return dict(blank, session="observing", last_evaluated_open_ms=last)


# ----------------------------------------------------------------- B  (b_observe_evaluate / b_accumulation_rules / b_evidence / b_contemporaneous)
def pick(t, samples, lag):
    """b_contemporaneous.pick_sample_at_or_before: latest s<=t; quality pass iff 0<=t-s<=lag, else fail; none -> unknown."""
    best, bd = None, None
    for r in samples or []:
        if not isinstance(r, dict) or r.get("timestamp") is None:
            continue
        try:
            s = int(r["timestamp"])
        except (TypeError, ValueError):
            continue
        d = int(t) - s
        if d < 0:
            continue
        if best is None or d < bd:
            best, bd = r, d
    if best is None:
        return None, "unknown", None
    return best, ("pass" if 0 <= bd <= lag else "fail"), bd


def b_structure(t, inp, K):
    """b_evidence.structure_at(t): OI/LSR picked at THIS boundary t; non-pass quality -> unknown (never economic fail)."""
    lag = K["B.OI_LSR_MAX_LAG_MS"]
    oi, oq_raw, od = pick(t, inp.get("oi_samples"), lag)
    ls, lq_raw, ld = pick(t, inp.get("lsr_samples"), lag)
    oq = "pass" if oq_raw == "pass" else "unknown"
    lq = "pass" if lq_raw == "pass" else "unknown"
    cap = inp.get("cap") or {}
    cv, ct = fin(cap.get("capUsd")), cap.get("capObservedAt_ms")
    cq = "pass" if (cap.get("capTrusted") is True and isinstance(ct, int) and 0 <= t - ct <= K["B.CAP_MAX_AGE_MS"]
                    and cv is not None and cv > 0) else "unknown"
    val = fin(oi.get("sumOpenInterestValue")) if oq == "pass" and oi else None
    if val is None or val < 0:
        oq, val = "unknown", None
    ratio = fin(ls.get("longShortRatio")) if lq == "pass" and ls else None
    if ratio is None or ratio < 0:
        lq, ratio = "unknown", None
    pct = val / cv * 100 if val is not None and cq == "pass" else None
    s_oi = "unknown" if oq != "pass" or cq != "pass" or pct is None else ("pass" if pct > K["B.OI_CAP_PCT_GT"] else "fail")
    s_lsr = "unknown" if lq != "pass" or ratio is None else ("pass" if ratio < K["B.LSR_LT"] else "fail")
    return dict(boundary_ms=int(t), state=tri(s_oi, s_lsr), oi_state=s_oi, lsr_state=s_lsr,
                oi_sample_ts=int(oi["timestamp"]) if oi else None, oi_raw_quality=oq_raw, oi_delta_ms=od,
                lsr_sample_ts=int(ls["timestamp"]) if ls else None, lsr_raw_quality=lq_raw, lsr_delta_ms=ld,
                cap_quality=cq, oi_cap_pct=pct, lsr=ratio)


def b_bg(rows, t, K):
    """b_evidence.background_at(rows, t): the latest CLOSED 4H, i.e. the row with open+4H == floor(t/4H)*4H."""
    want = (int(t) // H4) * H4
    for r in rows or []:
        if int(r["open_time"]) + H4 == want:
            if fin(r.get("ema7")) is None or fin(r.get("ema25")) is None or int(r.get("count", 0)) < K["B.BG_MIN_ROWS"]:
                return None
            return r
    return None


def b_ratio(bars, idx, K):
    """b_accumulation_rules.volume_ratio: q[idx] / mean(q[idx-168:idx]); window+idx must be contiguous 1h."""
    n = K["B.BASELINE_BARS"]
    if idx < n:
        return None
    w = bars[idx - n:idx]
    opens = [int(b["open_time"]) for b in w] + [int(bars[idx]["open_time"])]
    if any(b - a != H for a, b in zip(opens, opens[1:])):
        return None
    q = fin(bars[idx].get("q"))
    vals = [fin(b.get("q")) for b in w]
    if q is None or q < 0 or any(v is None or v < 0 for v in vals):
        return None
    mean = sum(vals) / n
    return None if mean <= 0 else q / mean


def b_event(inp, K):
    by = {}
    for b in inp["bars"]:
        by[int(b["open_time"])] = b
    bars = [by[k] for k in sorted(by)]
    ho = int(inp["h_open_ms"])
    target = int(inp["target_close_boundary_ms"])
    t, t1 = ho + H, ho + 2 * H            # real hourly close of h and of h+1
    idx = next((i for i, b in enumerate(bars) if int(b["open_time"]) == ho), None)
    out = dict(t_h_close_ms=t, t_h1_close_ms=t1)
    bg = b_bg(inp.get("bg4h_rows"), t, K)
    bg1 = b_bg(inp.get("bg4h_rows"), t1, K) if t1 <= target else None
    out["bg_h_open_ms"] = int(bg["open_time"]) if bg else None
    out["bg_h1_open_ms"] = int(bg1["open_time"]) if bg1 else None
    stack = None if bg is None else fin(bg["ema7"]) > fin(bg["ema25"])
    stack1 = None if bg1 is None else fin(bg1["ema7"]) > fin(bg1["ema25"])
    out["bg_h_stack"], out["bg_h1_stack"] = stack, stack1
    reasons = []
    if idx is None or idx < K["B.BASELINE_BARS"]:
        out.update(technical_event="unknown", event_ratio=None, abs_return_ok=None)
        reasons.append("warmup_short")
    else:
        ratio = b_ratio(bars, idx, K)
        o, c = fin(bars[idx].get("o")), fin(bars[idx].get("c"))
        ret_ok = None if o is None or c is None or o <= 0 or c <= 0 else abs(c / o - 1.0) < K["B.MAX_ABS_RETURN"]
        checks = ["unknown" if ratio is None else ("pass" if ratio >= K["B.EVENT_RATIO"] else "fail"),
                  "unknown" if ret_ok is None else ("pass" if ret_ok else "fail"),
                  "unknown" if stack is None else ("pass" if stack else "fail")]
        if ratio is None:
            reasons.append("baseline_unknown")
        elif ratio < K["B.EVENT_RATIO"]:
            reasons.append("ratio_fail")
        if ret_ok is False:
            reasons.append("return_fail")
        if stack is None:
            reasons.append("bg4h_unknown")
        elif not stack:
            reasons.append("bg4h_fail")
        out.update(technical_event=tri(*checks), event_ratio=ratio, abs_return_ok=ret_ok)
    h1_closed = t1 <= target
    i1 = next((i for i, b in enumerate(bars) if int(b["open_time"]) == ho + H), None)
    if not h1_closed:
        out.update(technical_confirmation="pending", confirm_ratio=None)
        reasons.append("h1_not_closed")
    elif i1 is None:
        out.update(technical_confirmation="unknown", confirm_ratio=None)
        reasons.append("h1_missing")
    elif i1 < K["B.BASELINE_BARS"]:
        out.update(technical_confirmation="unknown", confirm_ratio=None)
        reasons.append("warmup_short")
    else:
        r1 = b_ratio(bars, i1, K)
        checks = ["unknown" if r1 is None else ("pass" if r1 >= K["B.CONFIRM_RATIO"] else "fail"),
                  "unknown" if stack1 is None else ("pass" if stack1 else "fail")]
        if r1 is None:
            reasons.append("confirm_baseline_unknown")
        elif r1 < K["B.CONFIRM_RATIO"]:
            reasons.append("confirm_ratio_fail")
        if stack1 is None:
            reasons.append("bg4h_h1_unknown")
        elif stack1 is False:
            reasons.append("bg4h_h1_fail")
        out.update(technical_confirmation=tri(*checks), confirm_ratio=r1)
    sh = b_structure(t, inp, K)
    sc = b_structure(t1, inp, K) if h1_closed else dict(state="pending", boundary_ms=t1)
    out["structure_h"], out["structure_h1"] = sh, sc
    gate = tri(sh["state"], sc["state"]) if sc["state"] != "pending" else ("fail" if sh["state"] == "fail" else "pending")
    out["structure_gate"] = gate
    te, tc = out["technical_event"], out["technical_confirmation"]
    if te == "fail":
        q = "no_event"
    elif te != "pass":
        q = "pending_evidence"
    elif tc == "fail":
        q = "confirm_failed_trackable"
    elif tc == "pending":
        q = "pending_h1"
    elif tc != "pass":
        q = "pending_evidence"
    elif gate == "pass":
        q = "complete_pass"
    elif gate == "fail":
        q = "technical_observation_only"
    else:
        q = "pending_evidence"
    out["qualification_outcome"] = q
    out["tracking_allowed"] = te == "pass"
    out["strategy_tradable"] = q == "complete_pass"
    out["reasons"] = reasons
    return out


def b_event_declared(fx, act, K):
    out = []
    sq = fx.get("structure_qualification")
    if not isinstance(sq, dict):
        return out
    for key, comp, want_t in (("h", act["structure_h"], act["t_h_close_ms"]), ("h1", act["structure_h1"], act["t_h1_close_ms"])):
        d = sq.get(key)
        if not isinstance(d, dict):
            continue
        clock = d.get("bound_clock")
        if clock is not None and clock != "hourly_close":
            out.append(issue("B_structure_bound_to_non_hourly_clock", point=key, declared=clock))
        b = d.get("boundary_ms")
        if b is not None and b != want_t:
            code = "B_structure_bound_to_4h_clock" if b % H4 == 0 else "B_structure_boundary_not_h_close"
            out.append(issue(code, point=key, declared=b, required=want_t))
        if "state" in d and d["state"] != comp.get("state"):
            out.append(issue("declared_vs_computed_mismatch", field=f"structure_qualification.{key}.state", declared=d["state"], computed=comp.get("state")))
    return out


def b_observation(inp, K):
    s0 = fin(inp.get("s0_risk_line"))
    expected = int(inp["start_after_open_ms"]) + H4
    alerts, last = [], None
    stop = inp.get("human_stop_at_open_ms")
    stop = int(stop) if stop is not None else None
    blank = dict(end_reason=None, ended_at_open_ms=None, ended_bar=None, gap_from_open_ms=None, human_stop_at_open_ms=stop,
                 natural_end_unverified=False)

    def stopped(gap, unverified):          # S1-R1-P2-02 spot-check: durable human stop, independent of data gaps
        t = human_stop_tail(stop, expected, dict(blank, s0_alert_open_ms=alerts, last_evaluated_open_ms=last), note_gap=gap,
                            natural_unverified=unverified)
        return t
    for b in sorted(inp.get("closed_4h_after") or [], key=lambda r: int(r["open_ms"])):
        o = int(b["open_ms"])
        if stop is not None and o >= stop:
            break
        if o != expected:
            if stop is not None:
                return stopped(expected, True)
            return dict(blank, session="paused_awaiting_data", s0_alert_open_ms=alerts, last_evaluated_open_ms=last, gap_from_open_ms=expected)
        e7, e25, c = fin(b.get("ema7")), fin(b.get("ema25")), fin(b.get("close"))
        if e7 is None or e25 is None:
            if stop is not None:
                return stopped(o, True)
            return dict(blank, session="paused_awaiting_data", s0_alert_open_ms=alerts, last_evaluated_open_ms=last, gap_from_open_ms=o)
        if s0 is not None and c is not None and c < s0:
            alerts.append(o)
        if e7 <= e25:
            return dict(blank, session="ended", end_reason="structure_invalid", ended_at_open_ms=o,
                        ended_bar=dict(ema7=b.get("ema7"), ema25=b.get("ema25")), s0_alert_open_ms=alerts, last_evaluated_open_ms=o)
        last, expected = o, o + H4
    if stop is not None:
        return stopped(None, False)
    return dict(blank, session="observing", s0_alert_open_ms=alerts, last_evaluated_open_ms=last)


def b_observation_declared(fx, act, K):
    out = []
    of = fx.get("observation_followup")
    if not isinstance(of, dict) or "status" not in of:
        return out
    st, er, c4 = of.get("status"), of.get("end_reason"), of.get("closed_4h")
    if st == "ended" and er == "structure_invalid":
        e7 = fin((c4 or {}).get("ema7"))
        e25 = fin((c4 or {}).get("ema25"))
        if e7 is None or e25 is None or not (e7 <= e25):
            out.append(issue("B_end_without_ema7_le_ema25", declared_closed_4h=c4))
    if st == "ended" and er in ("price_rise_over_3pct", "current_lsr", "s0_break"):
        out.append(issue("B_end_on_forbidden_reason", declared=er))
    if st != act["session"]:
        out.append(issue("declared_vs_computed_mismatch", field="observation_followup.status", declared=st, computed=act["session"]))
    if st == "ended" and act["session"] == "ended" and isinstance(c4, dict):
        if not same(fin(c4.get("ema7")), fin(act["ended_bar"]["ema7"])) or not same(fin(c4.get("ema25")), fin(act["ended_bar"]["ema25"])):
            out.append(issue("declared_vs_computed_mismatch", field="observation_followup.closed_4h", declared=c4, computed=act["ended_bar"]))
    return out


def b_gate_check(inp, K):
    oi, lsr = fin(inp.get("oi_cap_pct")), fin(inp.get("lsr"))
    s_oi = "unknown" if oi is None else ("pass" if oi > K["B.OI_CAP_PCT_GT"] else "fail")
    s_lsr = "unknown" if lsr is None else ("pass" if lsr < K["B.LSR_LT"] else "fail")
    return dict(oi_cap=s_oi, lsr=s_lsr, gate=tri(s_oi, s_lsr))


# ----------------------------------------------------------------- C  (DOCS/1015 v1.1 §3, §6.7, §7; no production module)
def c_c0_check(inp, K):
    def lt(v, thr):
        v = fin(v)
        return "unknown" if v is None else ("pass" if v < thr else "fail")
    rsi = lt(inp.get("rsi14"), K["C.RSI_LT"])
    c = fin(inp.get("close"))
    emas = [fin(inp.get(k)) for k in ("ema7", "ema25", "ema99")]
    below = "unknown" if c is None or None in emas else ("pass" if all(c < e for e in emas) else "fail")
    oi = fin(inp.get("oi_cap_pct"))
    s_oi = "unknown" if oi is None else ("pass" if oi > K["C.OI_CAP_PCT_GT"] else "fail")
    s_lsr = lt(inp.get("lsr"), K["C.LSR_LT"])
    nl = inp.get("new_lows14")
    s_nl = "unknown" if not isinstance(nl, int) or isinstance(nl, bool) else ("pass" if nl <= K["C.NEW_LOWS14_MAX"] else "fail")
    return dict(rsi=rsi, below_all_ema=below, oi_cap=s_oi, lsr=s_lsr, new_lows14=s_nl, c0=tri(rsi, below, s_oi, s_lsr, s_nl))


class CSeries:
    def __init__(self, bars):
        self.by = {int(b["open_ms"]): b for b in bars}

    def bar(self, o):
        return self.by.get(int(o))

    def new_lows14(self, o, K):
        """14 consecutive real closed 4H closes ending at o; first is baseline; strict < min(previous) counts."""
        w = K["C.NEW_LOW_WINDOW"]
        cs = []
        for k in range(w - 1, -1, -1):
            b = self.bar(o - k * H4)
            if b is None or fin(b.get("c")) is None:
                return None
            cs.append(fin(b["c"]))
        return sum(1 for i in range(1, w) if cs[i] < min(cs[:i]))

    def vol_ratio7(self, o, K):
        b = self.bar(o)
        if b is None:
            return None
        q = fin(b.get("q"))
        prior = [self.bar(o - k * H4) for k in range(1, K["C.VOL_PRIOR_BARS"] + 1)]
        if q is None or q < 0 or any(p is None or fin(p.get("q")) is None or fin(p.get("q")) < 0 for p in prior):
            return None
        mean = sum(fin(p["q"]) for p in prior) / len(prior)
        return None if mean <= 0 else q / mean


def c_run(bars, t0, as_of, K):
    S = CSeries(bars)
    b0 = S.bar(t0)
    res = dict(t0_open_ms=t0, expires_at_boundary_ms=t0 + (K["C.C1_LAST_OFFSET"] + 1) * H4,
               c0=None, c0_checks=None, status=None, terminal_offset=None, terminal_reason=None,
               terminal_at_close_ms=None, confirm_vol_ratio=None, confirm_bullish=None,
               missing_reasons=[], partially_unverified=False, processed_through_offset=0,
               unresolved_offsets=[], first_unresolved_offset=None, local_terminal_blocked=[], local_facts=[],
               formal_confirmation_blocked=False,
               t11_confirm_like_ignored=False)
    if b0 is None:
        res.update(c0="unknown", status="no_cycle")
        return res
    if t0 + H4 > as_of:                      # r1 non-blocking note: T0 must be CLOSED at the input boundary
        res.update(c0="unknown", status="no_cycle", missing_reasons=["t0_not_closed"])
        return res
    chk = c_c0_check(dict(rsi14=b0.get("rsi14"), close=b0.get("c"), ema7=b0.get("ema7"), ema25=b0.get("ema25"),
                          ema99=b0.get("ema99"), oi_cap_pct=b0.get("oi_cap_pct"), lsr=b0.get("lsr"),
                          new_lows14=S.new_lows14(t0, K)), K)
    res["c0_checks"] = chk
    res["c0"] = {"pass": "formed", "fail": "not_formed", "unknown": "unknown"}[chk["c0"]]
    if chk["c0"] != "pass":
        res["status"] = "no_cycle"
        return res
    missing, status = [], "waiting"
    last_k = K["C.C1_LAST_OFFSET"]
    unresolved = []          # offsets whose real final state may still change once the missing input arrives
    local_facts = []         # local technical facts per processed root (kept even when formal advance is blocked)
    blocked = []             # local terminal hits that were NOT promoted because an earlier root is unresolved
    for k in range(K["C.C1_FIRST_OFFSET"], last_k + 1):
        o = t0 + k * H4
        close_boundary = o + H4              # real clock: T+k closes at t0_open + (k+1)*4H
        if close_boundary > as_of:
            break
        res["processed_through_offset"] = k
        b = S.bar(o)
        expire_here = False
        if b is None:                        # data gap: cannot prove the root is neither invalidating nor confirming
            missing.append("missing_candles")
            unresolved.append(k)
            local_facts.append(dict(offset=k, local="root_missing", resolved=False))
            if k == last_k:
                status = "expired"
                res.update(terminal_offset=k, terminal_reason="expiry_10_bars", terminal_at_close_ms=close_boundary)
            continue
        nl = S.new_lows14(o, K)
        lsr = fin(b.get("lsr"))
        prev = S.bar(o - H4)
        plsr = fin(prev.get("lsr")) if prev else None
        vr = S.vol_ratio7(o, K)
        o_, c_ = fin(b.get("o")), fin(b.get("c"))
        bull = None if o_ is None or c_ is None else c_ > o_
        oi = fin(b.get("oi_cap_pct"))
        if vr is None:
            missing.append("missing_volume_baseline")
        if oi is None:
            missing.append("missing_oi")
        if lsr is None:
            missing.append("missing_lsr")
        if nl is None:
            missing.append("missing_candles")
        # --- local three-valued facts. 'no' is only said when the available inputs PROVE it.
        inv_nl = "unknown" if nl is None else ("yes" if nl >= K["C.INVALID_NEW_LOWS_GE"] else "no")
        thr = K["C.INVALID_ADJ_LSR_GE"]
        if lsr is not None and plsr is not None:
            inv_adj = "yes" if (lsr >= thr and plsr >= thr) else "no"
        elif (lsr is not None and lsr < thr) or (plsr is not None and plsr < thr):
            inv_adj = "no"                   # one side proves the pair cannot both be >= 1.5
        else:
            inv_adj = "unknown"
        inv = "yes" if "yes" in (inv_nl, inv_adj) else ("unknown" if "unknown" in (inv_nl, inv_adj) else "no")
        conf = tri("unknown" if vr is None else ("pass" if vr > K["C.C1_VOL_RATIO_GT"] else "fail"),
                   "unknown" if bull is None else ("pass" if bull else "fail"),
                   "unknown" if oi is None else ("pass" if oi > K["C.OI_CAP_PCT_GT"] else "fail"),
                   "unknown" if lsr is None else ("pass" if lsr < K["C.LSR_LT"] else "fail"),
                   "unknown" if nl is None else ("pass" if nl <= K["C.NEW_LOWS14_MAX"] else "fail"))
        local = "invalidated" if inv == "yes" else ("confirmed" if (inv == "no" and conf == "pass") else None)
        root_resolved = inv == "yes" or (inv == "no" and conf in ("pass", "fail"))
        local_facts.append(dict(offset=k, local=local or ("continue" if root_resolved else "unknown"), resolved=root_resolved,
                                invalidation=inv, confirmation=conf, blocked_by=list(unresolved)))
        if unresolved:
            # 1017 S1-R1-P1-01: an earlier root can still change the final state; later hits stay LOCAL facts only.
            if local:
                blocked.append(dict(offset=k, local=local, blocked_by_offset=unresolved[0]))
            if not root_resolved:
                unresolved.append(k)
            if k == last_k:
                status = "expired"
                res.update(terminal_offset=k, terminal_reason="expiry_10_bars", terminal_at_close_ms=close_boundary)
            continue
        # no earlier unresolved root: the single-root order is valid
        if inv == "yes":
            status = "invalidated"
            res.update(terminal_offset=k, terminal_reason="new_lows_ge_3" if inv_nl == "yes" else "adjacent_lsr_ge_1_5",
                       terminal_at_close_ms=close_boundary)
            break
        if local == "confirmed":
            status = "confirmed"
            res.update(terminal_offset=k, terminal_reason="c1_confirmed", terminal_at_close_ms=close_boundary,
                       confirm_vol_ratio=vr, confirm_bullish=bull)
            break
        if not root_resolved:
            unresolved.append(k)
        # T+10: invalidation and confirmation were checked first -> expiry (fixed real clock, never extended)
        if k == last_k:
            status = "expired"
            res.update(terminal_offset=k, terminal_reason="expiry_10_bars", terminal_at_close_ms=close_boundary)
    res["unresolved_offsets"] = unresolved
    res["first_unresolved_offset"] = unresolved[0] if unresolved else None
    res["local_terminal_blocked"] = blocked
    res["local_facts"] = local_facts
    res["status"] = status
    seen = []
    for m in missing:
        if m not in seen:
            seen.append(m)
    res["missing_reasons"] = seen
    res["partially_unverified"] = (status == "expired" and bool(seen)) or (status in ("waiting", "expired") and bool(unresolved))
    res["formal_confirmation_blocked"] = bool(blocked)
    if status == "expired":
        o11 = t0 + (last_k + 1) * H4
        b11 = S.bar(o11)
        vr11 = S.vol_ratio7(o11, K) if b11 else None
        res["t11_confirm_like_ignored"] = bool(b11 and vr11 is not None and vr11 > K["C.C1_VOL_RATIO_GT"]
                                               and fin(b11.get("c")) is not None and fin(b11.get("c")) > fin(b11.get("o")))
    return res


def c_cycle(inp, K):
    bars = inp["bars"]
    t0 = int(inp["t0_open_ms"])
    r1 = c_run(bars, t0, int(inp["as_of_boundary_ms"]), K)
    out = dict(r1)
    revs = [dict(revision=1, status=r1["status"], terminal_offset=r1["terminal_offset"], terminal_reason=r1["terminal_reason"],
                 discovery="live", historical=False, notify_live=r1["status"] in ("confirmed", "invalidated", "expired"),
                 lifetime_end_boundary_ms=r1["expires_at_boundary_ms"], prior_revision=None, first_seen_at_ms=None)]
    bf = inp.get("backfill")
    if isinstance(bf, dict):
        merged = {int(b["open_ms"]): b for b in bars}
        for b in bf.get("added_bars") or []:
            merged[int(b["open_ms"])] = b
        r2 = c_run(list(merged.values()), t0, int(bf.get("as_of_boundary_ms", inp["as_of_boundary_ms"])), K)
        if (r2["status"], r2["terminal_offset"], r2["terminal_reason"]) != (r1["status"], r1["terminal_offset"], r1["terminal_reason"]):
            revs.append(dict(revision=2, status=r2["status"], terminal_offset=r2["terminal_offset"], terminal_reason=r2["terminal_reason"],
                             discovery="late_backfill", historical=True, notify_live=False,
                             lifetime_end_boundary_ms=r1["expires_at_boundary_ms"], prior_revision=1,
                             first_seen_at_ms=int(bf["received_at_ms"])))
    out["revisions"] = revs
    out["current_status"] = revs[-1]["status"]
    out["original_revision_retained"] = revs[0]["status"] == r1["status"]
    out["lifetime_extended"] = any(r["lifetime_end_boundary_ms"] != r1["expires_at_boundary_ms"] for r in revs)
    return out


def c_cycle_declared(fx, act, K):
    out = []
    ts = fx.get("technical_signal")
    if not isinstance(ts, dict) or "status" not in ts:
        return out
    st = ts.get("status")
    if st == "confirmed":
        off = ts.get("c1_bar_offset")
        if not isinstance(off, int) or not (K["C.C1_FIRST_OFFSET"] <= off <= K["C.C1_LAST_OFFSET"]):
            out.append(issue("C_confirm_offset_outside_T1_T10", declared=off))
        v = fin(ts.get("vol_ratio7"))
        if v is None or not (v > K["C.C1_VOL_RATIO_GT"]):
            out.append(issue("C_confirm_vol_not_gt_3", declared=ts.get("vol_ratio7")))
        if ts.get("bullish") is not True:
            out.append(issue("C_confirm_not_bullish", declared=ts.get("bullish")))
    if st != act["status"]:
        out.append(issue("declared_vs_computed_mismatch", field="technical_signal.status", declared=st, computed=act["status"]))
    if st == "confirmed" and act["status"] == "confirmed":
        if ts.get("c1_bar_offset") != act["terminal_offset"]:
            out.append(issue("declared_vs_computed_mismatch", field="technical_signal.c1_bar_offset", declared=ts.get("c1_bar_offset"), computed=act["terminal_offset"]))
        if not same(fin(ts.get("vol_ratio7")), act["confirm_vol_ratio"]):
            out.append(issue("declared_vs_computed_mismatch", field="technical_signal.vol_ratio7", declared=ts.get("vol_ratio7"), computed=act["confirm_vol_ratio"]))
        if ts.get("bullish") is not act["confirm_bullish"]:
            out.append(issue("declared_vs_computed_mismatch", field="technical_signal.bullish", declared=ts.get("bullish"), computed=act["confirm_bullish"]))
    return out


def c_rearm(inp, K):
    """1015 §3.3: after a terminal state, a full C0 'fail' must be observed, then 'pass'. 'unknown' never arms."""
    seen_not_met = False
    for e in sorted(inp["evaluations"], key=lambda r: int(r["open_ms"])):
        s = e["c0"]
        if s == "fail":
            seen_not_met = True
        elif s == "pass" and seen_not_met:
            return dict(rearmed=True, new_t0_open_ms=int(e["open_ms"]), new_cycle_id_differs=True)
    return dict(rearmed=False, new_t0_open_ms=None, new_cycle_id_differs=False)


def human_stop_tail(stop, expected, base, note_gap=None, natural_unverified=False):
    """S1-R1-P2-02: explicit human stop is a durable control fact; it never depends on complete market data.
    Precedence: an earlier PROVABLE natural end wins and is returned by the caller before this tail; otherwise ended/human_stop."""
    out = dict(base, session="ended", end_reason="human_stop", ended_at_open_ms=int(stop), reopened_by_replay=False,
               human_stop_at_open_ms=int(stop), natural_end_unverified=bool(natural_unverified or expected < int(stop)))
    if note_gap is not None:
        out["gap_from_open_ms"] = note_gap
    elif out["natural_end_unverified"]:
        out["gap_from_open_ms"] = expected
    return out


def c_observation(inp, K):
    origin = inp["origin"]
    stop = inp.get("human_stop_at_open_ms")
    stop = int(stop) if stop is not None else None
    blank = dict(end_reason=None, reference_low=None, ended_at_open_ms=None, risk_notes=[], last_evaluated_open_ms=None,
                 gap_from_open_ms=None, reopened_by_replay=False, human_stop_at_open_ms=stop, natural_end_unverified=False)
    if origin == "invalidated":
        # already ended by the cycle; a later human stop is retained as a control fact but does not rewrite the end
        return dict(blank, session="ended", profile=None, end_reason="cycle_invalidated", reference_kind=None,
                    ended_at_open_ms=int(inp["terminal_open_ms"]))
    ref = None
    if origin == "confirmed":
        profile, kind = "C_confirmed_observation", "cycle_low"
        t0, c1 = int(inp["t0_open_ms"]), int(inp["terminal_open_ms"])
        lows = {int(b["open_ms"]): fin(b.get("l")) for b in inp.get("cycle_bars") or []}
        need = list(range(t0, c1 + 1, H4))
        miss = [o for o in need if lows.get(o) is None]
        if miss:
            if stop is None:
                return dict(blank, session="paused_awaiting_data", profile=profile, reference_kind=kind, gap_from_open_ms=miss[0])
            # reference low unknown: cannot prove any natural end, but the human stop is final
            return human_stop_tail(stop, int(inp["terminal_open_ms"]) + H4,
                                   dict(blank, profile=profile, reference_kind=kind), note_gap=miss[0], natural_unverified=True)
        ref = min(lows[o] for o in need)
    elif origin == "expired":
        profile, kind = "C_expired_technical_observation", "t0_low"
        ref = fin(inp.get("t0_low"))
        if ref is None:
            if stop is None:
                return dict(blank, session="paused_awaiting_data", profile=profile, reference_kind=kind,
                            gap_from_open_ms=int(inp["terminal_open_ms"]) + H4)
            return human_stop_tail(stop, int(inp["terminal_open_ms"]) + H4, dict(blank, profile=profile, reference_kind=kind),
                                   natural_unverified=True)
    else:
        raise ValueError("origin must be confirmed|expired|invalidated")
    expected = int(inp["terminal_open_ms"]) + H4
    last, notes, prev_lsr = None, [], None
    for b in sorted(inp.get("after_bars") or [], key=lambda r: int(r["open_ms"])):
        o = int(b["open_ms"])
        if stop is not None and o >= stop:
            break
        pbase = dict(blank, profile=profile, reference_kind=kind, reference_low=ref, risk_notes=notes, last_evaluated_open_ms=last)
        if o != expected:
            if stop is not None:
                return human_stop_tail(stop, expected, pbase, note_gap=expected, natural_unverified=True)
            return dict(pbase, session="paused_awaiting_data", gap_from_open_ms=expected)
        c, lsr = fin(b.get("c")), fin(b.get("lsr"))
        if lsr is not None and prev_lsr is not None and lsr >= 1.5 and prev_lsr >= 1.5:
            notes.append(dict(open_ms=o, note="adjacent_lsr_ge_1_5_risk_only"))
        prev_lsr = lsr
        if c is None:
            if stop is not None:
                return human_stop_tail(stop, expected, pbase, note_gap=o, natural_unverified=True)
            return dict(pbase, session="paused_awaiting_data", gap_from_open_ms=o)
        if c < ref:
            return dict(pbase, session="ended", end_reason="structure_invalid", ended_at_open_ms=o, last_evaluated_open_ms=o)
        last, expected = o, o + H4
    base = dict(blank, profile=profile, reference_kind=kind, reference_low=ref, risk_notes=notes, last_evaluated_open_ms=last)
    if stop is not None:
        return human_stop_tail(stop, expected, base)
    return dict(base, session="observing")


KINDS = {
    "A.cross_level": (a_cross_level, None),
    "A.confirm": (a_confirm, a_confirm_declared),
    "A.candidate_gate": (a_candidate_gate, None),
    "A.observation": (a_observation, None),
    "B.event": (b_event, b_event_declared),
    "B.observation": (b_observation, b_observation_declared),
    "B.gate_check": (b_gate_check, None),
    "C.c0_check": (c_c0_check, None),
    "C.cycle": (c_cycle, c_cycle_declared),
    "C.rearm": (c_rearm, None),
    "C.observation": (c_observation, None),
}


def judge_fixture(fx, K):
    issues, computed = [], {}
    cases = fx.get("cases")
    top = cases is None
    if top:
        cases = [dict(case_id="main", kind=fx.get("kind"), inputs=fx.get("inputs"), expected=fx.get("expected"))]
    for c in cases:
        cid, kind = c.get("case_id"), c.get("kind")
        if kind not in KINDS:
            issues.append(issue("unknown_kind", case=cid, kind=kind))
            continue
        fn, decl = KINDS[kind]
        try:
            act = fn(c["inputs"], K)
        except Exception as exc:  # malformed input is a contradiction, never a pass
            issues.append(issue("input_error", case=cid, error=f"{type(exc).__name__}: {exc}"))
            continue
        computed[cid] = act
        if not isinstance(c.get("expected"), dict) or not c["expected"]:
            issues.append(issue("no_expected_values", case=cid))
        else:
            issues += compare_expected(c["expected"], act, f"{cid}.expected")
        if decl and top:
            issues += [dict(i, case=cid) for i in decl(fx, act, K)]
    return issues, computed


# ----------------------------------------------------------------- schema validation (subset of JSON Schema + cross-field rules)
def _type_ok(v, t):
    if isinstance(t, list):
        return any(_type_ok(v, x) for x in t)
    return {"string": isinstance(v, str), "integer": isinstance(v, int) and not isinstance(v, bool),
            "number": isinstance(v, (int, float)) and not isinstance(v, bool), "boolean": isinstance(v, bool),
            "object": isinstance(v, dict), "array": isinstance(v, list), "null": v is None}[t]


def struct_validate(v, sch, defs, path, errs):
    if "$ref" in sch:
        t = sch.get("type")
        if v is None and isinstance(t, list) and "null" in t:
            return
        sch = defs[sch["$ref"].split("/")[-1]]
    if "type" in sch and not _type_ok(v, sch["type"]):
        errs.append(dict(code="type", path=path, want=sch["type"]))
        return
    if "const" in sch and v != sch["const"]:
        errs.append(dict(code="const", path=path, want=sch["const"], got=v))
    if "enum" in sch and v not in sch["enum"]:
        errs.append(dict(code="enum", path=path, got=v))
    if "pattern" in sch and isinstance(v, str) and not re.search(sch["pattern"], v):
        errs.append(dict(code="pattern", path=path, got=v))
    if "minimum" in sch and isinstance(v, (int, float)) and not isinstance(v, bool) and v < sch["minimum"]:
        errs.append(dict(code="minimum", path=path, got=v))
    if isinstance(v, dict):
        for r in sch.get("required", []):
            if r not in v:
                errs.append(dict(code="required", path=f"{path}.{r}"))
        props = sch.get("properties", {})
        if sch.get("additionalProperties") is False:
            for k in v:
                if k not in props:
                    errs.append(dict(code="additional_property", path=f"{path}.{k}"))
        for k, s in props.items():
            if k in v:
                struct_validate(v[k], s, defs, f"{path}.{k}", errs)
    if isinstance(v, list):
        if "minItems" in sch and len(v) < sch["minItems"]:
            errs.append(dict(code="minItems", path=path))
        if "items" in sch:
            for i, x in enumerate(v):
                struct_validate(x, sch["items"], defs, f"{path}[{i}]", errs)


TF_MS = {"4h": H4, "1h": H}
KIND_BY_STRATEGY = {"A": {"a_full_cross", "a_medium_cross"}, "B": {"b_tech_h"}, "C": {"c_cycle"}}
A_LEGACY_KIND = {"a_full_cross": ("full_cross_path_a", "strong"), "a_medium_cross": ("medium_ema7_25_cross", "medium")}


def canonical(o):
    return json.dumps(o, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def evidence_hash(svs):
    rows = sorted(svs, key=lambda r: (str(r.get("source")), str(r.get("ref")), str(r.get("version"))))
    return hashlib.sha256(canonical(rows).encode("utf-8")).hexdigest()


def b_legacy_id(market, symbol, open_ms):
    """b_accumulation_store.stable_event_id (event_type='tech_h')."""
    return "bev-" + uuid.uuid5(uuid.NAMESPACE_URL, f"{B_STRATEGY_ID}|{B_RULE_VERSION}|{market}|{symbol}|1h|{int(open_ms)}|tech_h").hex


def new_event_id(e):
    return "|".join(str(e[k]) for k in ("strategy", "event_kind", "market", "symbol", "timeframe", "rule_version", "trigger_bar_open_ms"))


def first_confirm_rules(e, chain, errs):
    """S1-R1-P2-03: first-confirmation causality, validated together with the earlier revisions (chain = r1..r(n-1), oldest first).
    * first revision carrying a confirm block: revision_recorded_at == first_confirmed_seen_at, and EVERY input version it
      carries must be available_at <= first_confirmed_seen_at (discovery cannot precede its inputs).
    * any later revision: first_confirmed_seen_at / confirm bar are preserved from the first confirmed revision
      (a legitimate later source correction may have newer available_at and a newer revision_recorded_at)."""
    cf, rev = e.get("confirm"), e.get("revision")
    if not isinstance(cf, dict) or not isinstance(rev, int):
        return
    fc, rr = iso_ms(cf.get("first_confirmed_seen_at")), iso_ms(e.get("revision_recorded_at"))
    if rev > 1:
        if not chain:
            errs.append(dict(code="E06_prior_revisions_required_for_confirmed_revision"))
            return
        prior = chain[-1]
        if (not isinstance(prior, dict) or prior.get("event_id") != e.get("event_id") or prior.get("revision") != rev - 1
                or len(chain) != rev - 1 or e.get("prior_revision_id") != prior.get("revision_id")):
            errs.append(dict(code="E07_prior_revision_context_mismatch"))
            return
    earlier = next((r for r in chain if isinstance(r, dict) and r.get("technical_status") == "confirmed" and isinstance(r.get("confirm"), dict)), None)
    if earlier is not None:
        if iso_ms(earlier["confirm"].get("first_confirmed_seen_at")) is None or \
                cf.get("first_confirmed_seen_at") != earlier["confirm"].get("first_confirmed_seen_at"):
            errs.append(dict(code="E06_first_confirmed_seen_not_preserved", original=earlier["confirm"].get("first_confirmed_seen_at"),
                             got=cf.get("first_confirmed_seen_at")))
        return
    if fc is not None and rr is not None and rr != fc:
        errs.append(dict(code="E06_first_confirm_revision_recorded_ne_first_confirmed_seen"))
    if fc is not None:
        for x in e.get("source_versions") or []:
            ea = iso_ms(x.get("available_at"))
            if ea is not None and ea > fc:
                errs.append(dict(code="E06_first_confirmed_before_confirm_input_available", source=x.get("source"), version=x.get("version")))
        av = iso_ms(e.get("available_at"))
        if av is not None and av > fc:
            errs.append(dict(code="E06_first_confirmed_before_revision_available_at"))


def event_rules(e, errs, chain=None):
    s, k, tf = e.get("strategy"), e.get("event_kind"), e.get("timeframe")
    if k not in KIND_BY_STRATEGY.get(s, set()):
        errs.append(dict(code="E10_kind_strategy_mismatch", strategy=s, event_kind=k))
    want_tf = {"A": "4h", "B": "1h", "C": "4h"}.get(s)
    if tf != want_tf:
        errs.append(dict(code="E03_timeframe_strategy_mismatch", got=tf, want=want_tf))
    rv = {"A": A_RULE_VERSION, "B": B_RULE_VERSION, "C": C_RULE_VERSION}.get(s)
    if e.get("rule_version") != rv:
        errs.append(dict(code="E01_rule_version_mismatch", got=e.get("rule_version"), want=rv))
    tfm, o = TF_MS.get(tf), e.get("trigger_bar_open_ms")
    if tfm and isinstance(o, int):
        if o % tfm:
            errs.append(dict(code="E03_open_not_on_grid"))
        if e.get("trigger_bar_close_ms") != o + tfm:
            errs.append(dict(code="E03_close_not_open_plus_tf", got=e.get("trigger_bar_close_ms"), want=o + tfm))
        if e.get("source_close_time_ms") != o + tfm - 1:
            errs.append(dict(code="E03_source_close_time_not_close_minus_1ms"))
        if e.get("occurred_at_ms") != o + tfm:
            errs.append(dict(code="E04_occurred_at_not_trigger_close"))
        dc = e.get("decision_bar_close_ms")
        last = {"A": o + 2 * tfm, "B": o + 2 * tfm, "C": o + 11 * tfm}.get(s)
        if isinstance(dc, int):
            if dc < o + tfm or (dc - o) % tfm:
                errs.append(dict(code="E04_decision_not_a_bar_close_at_or_after_trigger"))
            if last is not None and dc > last:
                errs.append(dict(code="E04_decision_after_rule_window", decision=dc, last=last))
            cf0 = e.get("confirm")
            if isinstance(cf0, dict) and cf0.get("confirm_bar_close_ms") != dc:
                errs.append(dict(code="E04_decision_ne_confirm_close"))
    if s in ("A", "C"):
        try:
            want = new_event_id(e)
        except KeyError:
            want = None
        if e.get("event_id") != want:
            errs.append(dict(code="E01_event_id_composition", got=e.get("event_id"), want=want))
    elif s == "B" and isinstance(o, int):
        want = b_legacy_id(e.get("market"), e.get("symbol"), o)
        if e.get("event_id") != want:
            errs.append(dict(code="E01_b_legacy_event_id_not_kept", got=e.get("event_id"), want=want))
    cyc = e.get("candidate_cycle_id")
    if s == "A":
        if isinstance(cyc, str) and cyc and cyc in str(e.get("event_id")):
            errs.append(dict(code="E02_a_technical_id_contains_candidate_cycle"))
    elif cyc is not None:
        errs.append(dict(code="E02_candidate_cycle_only_for_A"))
    close = e.get("trigger_bar_close_ms")
    fs, av, rr = iso_ms(e.get("first_seen_at")), iso_ms(e.get("available_at")), iso_ms(e.get("revision_recorded_at"))
    if None in (fs, av, rr):
        errs.append(dict(code="E05_time_not_utc_iso"))
    elif isinstance(close, int):
        if av < close:
            errs.append(dict(code="E05_available_before_candle_close"))
        if fs < av and e.get("revision", 1) == 1:
            errs.append(dict(code="E05_first_seen_before_available"))
        if rr < fs:
            errs.append(dict(code="E07_revision_recorded_before_first_seen"))
        if rr < av:
            errs.append(dict(code="E05_revision_recorded_before_available"))
    cf = e.get("confirm")
    if isinstance(cf, dict) and tfm and isinstance(o, int):
        co = cf.get("confirm_bar_open_ms")
        if not isinstance(co, int) or co % tfm:
            errs.append(dict(code="E06_confirm_open_not_on_grid"))
        else:
            if cf.get("confirm_bar_close_ms") != co + tfm:
                errs.append(dict(code="E06_confirm_close_not_open_plus_tf"))
            off = (co - o) // tfm
            if cf.get("confirm_offset") != off:
                errs.append(dict(code="E06_confirm_offset_mismatch", got=cf.get("confirm_offset"), want=off))
            lo, hi = {"A": (0, 1), "B": (1, 1), "C": (1, 10)}.get(s, (0, -1))
            if not (lo <= off <= hi):
                errs.append(dict(code="E06_confirm_offset_outside_rule_window", offset=off))
            fc = iso_ms(cf.get("first_confirmed_seen_at"))
            if fc is None or fc < co + tfm:
                errs.append(dict(code="E06_first_confirmed_seen_before_confirm_close"))
            elif fs is not None and fc < fs:
                errs.append(dict(code="E06_first_confirmed_seen_before_first_seen"))
            elif rr is not None and fc > rr:
                errs.append(dict(code="E06_first_confirmed_seen_after_revision_recorded"))
            lct = cf.get("legacy_confirm_time")
            if lct is not None:
                if s != "A":
                    errs.append(dict(code="E06_legacy_confirm_time_only_for_A"))
                if cf.get("legacy_confirm_time_semantics") != "system_discovery_time":
                    errs.append(dict(code="E06_legacy_confirm_time_semantics"))
                lm = iso_ms(lct)
                if lm is None or lm < co + tfm:
                    errs.append(dict(code="E06_legacy_confirm_time_used_as_candle_close"))
    if e.get("technical_status") == "confirmed" and not isinstance(cf, dict):
        errs.append(dict(code="E06_confirmed_without_confirm_block"))
    if e.get("technical_status") != "confirmed" and isinstance(cf, dict):
        errs.append(dict(code="E06_confirm_block_without_confirmed_status"))
    rev, eid = e.get("revision"), e.get("event_id")
    if isinstance(rev, int):
        if e.get("revision_id") != f"{eid}:r{rev}":
            errs.append(dict(code="E07_revision_id_composition"))
        if rev == 1:
            if e.get("prior_revision_id") is not None or e.get("revision_reason") is not None:
                errs.append(dict(code="E07_first_revision_has_prior"))
            if fs is not None and rr is not None and rr != fs:
                errs.append(dict(code="E07_first_revision_recorded_ne_first_seen"))
        else:
            if e.get("prior_revision_id") != f"{eid}:r{rev - 1}":
                errs.append(dict(code="E07_prior_revision_link_missing"))
            if e.get("revision_reason") not in ("state_progression", "late_backfill", "source_revision", "replay_correction"):
                errs.append(dict(code="E07_revision_reason_missing"))
    svs = e.get("source_versions")
    if isinstance(svs, list) and svs:
        if e.get("evidence_hash") != evidence_hash(svs):
            errs.append(dict(code="E08_evidence_hash_mismatch", want=evidence_hash(svs)))
        avs = [iso_ms(x.get("available_at")) for x in svs]
        if None in avs:
            errs.append(dict(code="E08_source_available_not_iso"))
        elif av is not None and av != max(avs):
            errs.append(dict(code="E08_available_at_not_max_of_sources"))
        bound = e.get("decision_bar_close_ms")
        if isinstance(bound, int):
            for x in svs:
                ea = x.get("effective_at_ms")
                if isinstance(ea, int) and ea > bound:
                    errs.append(dict(code="E08_source_effective_after_decision_boundary", ref=x.get("ref")))
    first_confirm_rules(e, chain or [], errs)
    for lr in e.get("legacy_refs") or []:
        sysname = lr.get("system")
        if s == "A" and k in A_LEGACY_KIND and isinstance(o, int):
            lk, lvl = A_LEGACY_KIND[k]
            bar_time = o + H4 - 1
            if sysname == "ema_observations.bind_identity":
                want = f"{e.get('market')}|{e.get('symbol')}|4h|{A_RULE_VERSION}|{bar_time}|{lk}"
            elif sysname == "ema_event_timing.event_id":
                want = f"{e.get('market')}|{e.get('symbol')}|4h|{A_RULE_VERSION}|{lr.get('candidate_cycle_id')}|{bar_time}|{lvl}"
                if lr.get("candidate_cycle_id") != cyc:
                    errs.append(dict(code="E09_legacy_cycle_link_mismatch"))
            else:
                want = None
            if lr.get("id") != want:
                errs.append(dict(code="E09_legacy_id_mismatch", system=sysname, got=lr.get("id"), want=want))
        elif s == "B":
            if sysname != "b_accumulation_store.stable_event_id" or lr.get("id") != eid:
                errs.append(dict(code="E09_b_legacy_ref_ne_event_id"))


SESSION_PROFILES = {"A": {"A_strong_observation", "A_medium_observation"}, "B": {"B_trend_observation"},
                    "C": {"C_waiting_observation", "C_confirmed_observation", "C_expired_technical_observation"}}
C_STATE_PROFILE = {"waiting": "C_waiting_observation", "confirmed": "C_confirmed_observation", "expired": "C_expired_technical_observation"}
C_STATE_REF = {"waiting": "none", "confirmed": "cycle_low", "expired": "t0_low"}
STATE_ENUM = {"A": {"pending", "confirmed", "void", "blocked", "technical_only"},
              "B": {"pending", "confirmed", "confirm_failed", "unknown"},
              "C": {"waiting", "confirmed", "invalidated", "expired"}}


def session_rules(x, errs):
    s = x.get("strategy")
    if x.get("session_id") != f"{x.get('event_id')}|{x.get('tracking_profile_id')}|{x.get('tracking_profile_version')}":
        errs.append(dict(code="S01_session_id_composition"))
    if x.get("tracking_profile_id") not in SESSION_PROFILES.get(s, set()):
        errs.append(dict(code="S02_profile_not_allowed_for_strategy", profile=x.get("tracking_profile_id")))
    if x.get("original_cycle_state") not in STATE_ENUM.get(s, set()):
        errs.append(dict(code="S02_original_cycle_state_not_in_strategy_enum", got=x.get("original_cycle_state")))
    st = x.get("observation_status")
    ended = st == "ended"
    if ended != (x.get("ended_at_bar_close_ms") is not None) or ended != (x.get("end_reason") is not None):
        errs.append(dict(code="S04_end_fields_inconsistent"))
    if x.get("end_reason") == "human_stop" and x.get("human_stopped") is not True:
        errs.append(dict(code="S04_human_stop_flag"))
    if x.get("human_stopped") is True and not ended:
        errs.append(dict(code="S04_human_stopped_not_ended"))
    ocs, ref = x.get("original_cycle_state"), (x.get("reference") or {})
    if s == "C":
        if ocs == "invalidated":
            if not ended or x.get("end_reason") != "cycle_invalidated":
                errs.append(dict(code="S03_c_invalidated_must_end"))
        elif ocs in C_STATE_PROFILE:
            if x.get("tracking_profile_id") != C_STATE_PROFILE[ocs]:
                errs.append(dict(code="S03_c_state_profile_mismatch", state=ocs))
            if ref.get("kind") != C_STATE_REF[ocs]:
                errs.append(dict(code="S03_c_state_reference_mismatch", state=ocs, got=ref.get("kind")))
    elif x.get("end_reason") == "cycle_invalidated":
        errs.append(dict(code="S03_cycle_invalidated_only_for_C"))
    if s == "B":
        if not re.match(r"^bev-[0-9a-f]{32}$", str(x.get("event_id"))):
            errs.append(dict(code="S05_b_session_must_use_legacy_event_id"))
        if ref.get("kind") not in ("s0_risk_line", "none"):
            errs.append(dict(code="S05_b_reference_kind"))
    if s == "A" and ref.get("kind") != "none":
        errs.append(dict(code="S02_a_reference_kind"))
    sa, le = x.get("started_at_bar_close_ms"), x.get("last_evaluated_bar_close_ms")
    if isinstance(sa, int) and isinstance(le, int) and le < sa:
        errs.append(dict(code="S06_last_evaluated_before_start"))
    ea = x.get("ended_at_bar_close_ms")
    if ended and isinstance(le, int) and isinstance(ea, int) and ea > le:
        errs.append(dict(code="S06_ended_after_last_evaluated"))


ALLOWED_T = {
    (None, "observing"): {"session_started"},
    ("observing", "paused_awaiting_data"): {"missing_candles", "missing_oi", "missing_lsr", "missing_cap", "gap_blocks_continuity"},
    ("paused_awaiting_data", "observing"): {"backfilled"},
    ("observing", "ended"): {"structure_invalid", "human_stop", "cycle_invalidated", "delisted_verified"},
    ("paused_awaiting_data", "ended"): {"human_stop", "delisted_verified"},
}


def transition_rules(x, errs):
    if x.get("transition_id") != f"{x.get('session_id')}|{x.get('at_bar_close_ms')}|{x.get('to_state')}|r{x.get('revision')}":
        errs.append(dict(code="T01_transition_id_composition"))
    key = (x.get("from_state"), x.get("to_state"))
    if key not in ALLOWED_T:
        errs.append(dict(code="T02_transition_not_allowed", transition=list(key)))
    elif x.get("reason") not in ALLOWED_T[key]:
        errs.append(dict(code="T02_reason_not_allowed_for_transition", reason=x.get("reason")))
    tfm, o, c = TF_MS.get(x.get("timeframe")), x.get("at_bar_open_ms"), x.get("at_bar_close_ms")
    if tfm and isinstance(o, int) and c != o + tfm:
        errs.append(dict(code="T03_close_not_open_plus_tf"))
    rm = iso_ms(x.get("recorded_at"))
    if rm is None or (isinstance(c, int) and rm < c):
        errs.append(dict(code="T03_recorded_before_bar_close"))
    rev = x.get("revision")
    if isinstance(rev, int) and rev > 1 and not x.get("corrects_transition_id"):
        errs.append(dict(code="T04_correction_without_link"))
    if rev == 1 and x.get("corrects_transition_id") is not None:
        errs.append(dict(code="T04_first_revision_has_correction_link"))


RULES = {"event": event_rules, "session": session_rules, "transition": transition_rules}


def validate_record(rtype, rec, schema, chain=None):
    errs = []
    defs = schema.get("$defs", {})
    if rtype not in RULES or rtype not in defs:
        return [dict(code="unknown_record_type", record_type=rtype)]
    struct_validate(rec, defs[rtype], defs, rtype, errs)
    if isinstance(rec, dict):
        try:
            if rtype == "event":
                event_rules(rec, errs, chain)
            else:
                RULES[rtype](rec, errs)
        except Exception as exc:
            errs.append(dict(code="rule_error", error=f"{type(exc).__name__}: {exc}"))
    return errs


# ----------------------------------------------------------------- driver
IMPLEMENTED_RULE_IDS = {"E01", "E02", "E03", "E04", "E05", "E06", "E07", "E08", "E09", "E10",
                        "S01", "S02", "S03", "S04", "S05", "S06", "T01", "T02", "T03", "T04"}


def load(p):
    return json.loads(Path(p).read_text(encoding="utf-8"))


def load_context():
    prof = load(ROOT / "profiles" / "original-confirmation-freeze-v1.json")
    obs = load(ROOT / "profiles" / "tracking-observation-profile-v1.json")
    schema = load(ROOT / "schema" / "tracking-event-schema-v1.json")
    K = dict(prof.get("constants") or {})
    problems = []
    for k, v in FROZEN.items():
        if k not in K or not same(K[k], v):
            problems.append(dict(code="profile_constant_drift", key=k, profile=K.get(k), frozen=v))
    for k in K:
        if k not in FROZEN:
            problems.append(dict(code="profile_constant_unknown", key=k))
    declared = {r.get("id") for r in schema.get("x-cross-field-rules", [])}
    if declared != IMPLEMENTED_RULE_IDS:
        problems.append(dict(code="schema_rule_set_mismatch", declared=sorted(declared), implemented=sorted(IMPLEMENTED_RULE_IDS)))
    a = obs.get("A", {})
    if "weak_or_medium" in a or "medium" not in a or a.get("weak_scope", {}).get("creates_tracking_session") is not False \
            or a.get("weak_scope", {}).get("upgrade_to_medium_or_strong") is not False:
        problems.append(dict(code="observation_profile_weak_medium_scope"))
    if obs.get("C", {}).get("original_cycle_state_enum") != ["waiting", "confirmed", "invalidated", "expired"]:
        problems.append(dict(code="observation_profile_c_state_enum"))
    md = obs.get("common", {}).get("missing_data", {})
    if md.get("pauses_c_t10_lifetime") is not False or md.get("late_backfill_after_expiry") != "append_historical_revision_never_extend_lifetime":
        problems.append(dict(code="observation_profile_pause_lifetime"))
    return (FROZEN if not problems else K), schema, problems


def rel(path):
    p = Path(path).resolve()
    try:
        return str(p.relative_to(ROOT)).replace("\\", "/")
    except ValueError:
        return str(p)


def judge_file(path, K, schema):
    d = load(path)
    if "record_type" in d:
        errs = validate_record(d["record_type"], d.get("record"), schema, d.get("prior_records"))
        return dict(file=rel(path), type="schema_example", declared_class=d.get("example_class"),
                    contradictions=errs, expected_codes=d.get("expected_errors", []))
    issues, computed = judge_fixture(d, K)
    return dict(file=rel(path), type="fixture", fixture_id=d.get("fixture_id"), declared_class=d.get("class"),
                contradictions=issues, expected_codes=d.get("expected_detection_codes", []), computed=computed)


def classify(r):
    neg = r["declared_class"] in ("negative", "invalid")
    codes = {c["code"] for c in r["contradictions"]}
    if neg:
        want = set(r.get("expected_codes") or [])
        ok = bool(r["contradictions"]) and want <= codes
        r["verdict"] = "negative_detected" if ok else "NEGATIVE_NOT_DETECTED"
        r["missing_expected_codes"] = sorted(want - codes)
        return 0, (0 if ok else 1)
    r["verdict"] = "consistent" if not r["contradictions"] else "CONTRADICTION"
    return (1 if r["contradictions"] else 0), 0


def evaluate_set(which, K, schema):
    files = sorted((ROOT / "fixtures").glob("*.json")) + sorted((ROOT / "schema" / "examples" / "valid").glob("*.json"))
    if which == "all":
        files += sorted((ROOT / "fixtures" / "negative").glob("*.json")) + sorted((ROOT / "schema" / "examples" / "invalid").glob("*.json"))
    results, bad, und = [], 0, 0
    for f in files:
        r = judge_file(f, K, schema)
        b, u = classify(r)
        bad, und = bad + b, und + u
        r.pop("computed", None)
        results.append(r)
    return results, bad, und


def codex_mutations(K):
    """Replay the exact three S1 mutations of codex-audits/S1-20261018T011323Z/independent_s1_audit.py, in memory only."""
    def m_c1(f):
        f["technical_signal"].update(c1_bar_offset=11, vol_ratio7=3.0, bullish=False)

    def m_end(f):
        f["observation_followup"]["closed_4h"].update(ema7=2.2, ema25=2.1)

    def m_boundary(f):
        f["technical_signal"]["confirm_bar_open_ms"] = f["technical_signal"]["cross_bar_open_ms"] + 2 * 14_400_000
        f["technical_signal"]["confirm_vol_ratio"] = 1.99

    out, und = [], 0
    for name, fn, mut in (("mutated-c1", "c_c1_confirm.json", m_c1), ("mutated-end", "b_end_ema7_le_ema25.json", m_end),
                          ("mutated-boundary", "a_t1_confirm.json", m_boundary)):
        f = load(ROOT / "fixtures" / fn)
        base, _ = judge_fixture(copy.deepcopy(f), K)
        mut(f)
        issues, _ = judge_fixture(f, K)
        det = bool(issues)
        und += 0 if det else 1
        out.append(dict(mutation=name, fixture=f"fixtures/{fn}", baseline_contradictions=len(base),
                        mutated_contradiction_codes=sorted({i["code"] for i in issues}), mutated_contradictions=issues,
                        verdict="negative_detected" if det else "NEGATIVE_NOT_DETECTED"))
    return out, und


def main(argv):
    try:
        K, schema, problems = load_context()
    except Exception as exc:
        print(json.dumps(dict(error=f"context: {type(exc).__name__}: {exc}"), ensure_ascii=False))
        return 3
    head = dict(judge="tests/s1_contract_judge.py", submission_dir=ROOT.name, profile_problems=problems)
    if "--case" in argv:
        i = argv.index("--case")
        if i + 1 >= len(argv):
            print(json.dumps(dict(head, error="--case needs a path"), ensure_ascii=False))
            return 3
        p = Path(argv[i + 1])
        p = p if p.is_absolute() else Path.cwd() / p
        try:
            r = judge_file(p, K, schema)
        except Exception as exc:
            print(json.dumps(dict(head, error=f"{type(exc).__name__}: {exc}"), ensure_ascii=False))
            return 3
        r.pop("computed", None)
        ok = not r["contradictions"] and not problems
        print(json.dumps(dict(head, mode="case", consistent=ok, result=r), ensure_ascii=False, indent=1))
        return 0 if ok else (3 if problems else 1)
    if "--codex-mutations" in argv:
        res, und = codex_mutations(K)
        print(json.dumps(dict(head, mode="codex-mutations", undetected=und, results=res), ensure_ascii=False, indent=1))
        return 3 if problems else (2 if und else 0)
    which = argv[argv.index("--set") + 1] if "--set" in argv and argv.index("--set") + 1 < len(argv) else "all"
    if which not in ("valid", "all"):
        print(json.dumps(dict(head, error="--set valid|all"), ensure_ascii=False))
        return 3
    results, bad, und = evaluate_set(which, K, schema)
    summary = dict(files=len(results), consistent=sum(r["verdict"] == "consistent" for r in results),
                   contradictions_in_valid=bad, negatives_detected=sum(r["verdict"] == "negative_detected" for r in results),
                   negatives_not_detected=und)
    print(json.dumps(dict(head, mode=f"set:{which}", summary=summary, results=results), ensure_ascii=False, indent=1))
    if problems:
        return 3
    return 1 if bad else (2 if und else 0)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
