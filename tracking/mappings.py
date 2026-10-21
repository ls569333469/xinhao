"""C2-a explicit mappings: market / bar time / legacy ID / discovery / event-profile.

Design-only helpers for isolated archive ingest. Does not write production or
advance watermarks. System discovery time is never treated as a K-line close.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

H4_MS = 14_400_000
MAPPING_PROTOCOL_VERSION = "s2-c2a-mapping-v1"

# Frozen tracking markets (schema enum). Legacy scan rows use USDⓈ-M.
MARKET_WHITELIST = {
    "USDⓈ-M": "USDT-M",
    "USDT-M": "USDT-M",
    "COIN-M": "COIN-M",
}

LEGACY_KIND_TO_EVENT_KIND = {
    "full_cross_path_a": "a_full_cross",
    "fullCross": "a_full_cross",
    "strong": "a_full_cross",
    "medium_ema7_25_cross": "a_medium_cross",
    "mediumCrossEvent": "a_medium_cross",
    "medium": "a_medium_cross",
}

EVENT_KIND_TO_PROFILE = {
    "a_full_cross": "A_strong_observation",
    "a_medium_cross": "A_medium_observation",
}

EVENT_KIND_TO_LEVEL = {
    "a_full_cross": "strong",
    "a_medium_cross": "medium",
}

# weak_ema99_cross is out of v1 tracking scope
OUT_OF_SCOPE_KINDS = {"weak_ema99_cross", "weakNewCross", "weak"}


class MappingError(ValueError):
    def __init__(self, code: str, **detail: Any):
        super().__init__(code)
        self.code = code
        self.detail = detail

    def as_dict(self) -> dict:
        return {"code": self.code, **self.detail}


def map_market(legacy_market: str | None) -> str:
    """Whitelist map; never merge spot / USDT-M / COIN-M by base asset."""
    if legacy_market is None or legacy_market not in MARKET_WHITELIST:
        raise MappingError("MARKET_unknown_or_cross_contract", legacy_market=legacy_market)
    return MARKET_WHITELIST[legacy_market]


@dataclass(frozen=True)
class BarTimeMapping:
    """Inclusive Binance close_time (open+H4-1) → exclusive tracking close (=open+H4)."""
    legacy_inclusive_close_ms: int
    open_ms: int
    exclusive_close_ms: int
    source_close_ms: int  # == legacy inclusive close

    def as_dict(self) -> dict:
        return {
            "legacy_inclusive_close_ms": self.legacy_inclusive_close_ms,
            "open_ms": self.open_ms,
            "exclusive_close_ms": self.exclusive_close_ms,
            "source_close_ms": self.source_close_ms,
            "h4_ms": H4_MS,
        }


def map_inclusive_close_to_exclusive(legacy_close_ms: int | None) -> BarTimeMapping:
    """Only for K-line fields. Reject non-ms / misaligned closes."""
    if not isinstance(legacy_close_ms, int) or legacy_close_ms < 100_000_000_000:
        raise MappingError("TIME_invalid_legacy_close", legacy_close_ms=legacy_close_ms)
    # Binance closed 4h: end == open + H4 - 1
    open_ms = legacy_close_ms + 1 - H4_MS
    if open_ms % H4_MS != 0:
        raise MappingError("TIME_misaligned_open", legacy_close_ms=legacy_close_ms, open_ms=open_ms)
    exclusive = legacy_close_ms + 1
    if exclusive != open_ms + H4_MS:
        raise MappingError("TIME_exclusive_mismatch", legacy_close_ms=legacy_close_ms, exclusive=exclusive)
    return BarTimeMapping(
        legacy_inclusive_close_ms=legacy_close_ms,
        open_ms=open_ms,
        exclusive_close_ms=exclusive,
        source_close_ms=legacy_close_ms,
    )


def system_discovery_time_is_not_bar_time() -> dict:
    """Documented rule: confirm_time / observedAt are system times, not candle closes."""
    return {
        "rule": "system_discovery_time_ne_kline_bar_time",
        "legacy_confirm_time_semantics": "system_discovery_time",
        "kline_fields_only": [
            "confirm_bar_time",
            "cross_bar_time",
            "candleAsOf",
            "bar_time",
            "source_close_time_ms",
        ],
        "system_fields_no_plus_one": [
            "confirm_time",
            "confirmed_at",
            "observed_at",
            "first_seen_at",
            "available_at",
        ],
    }


def map_event_kind_from_row(row: dict) -> str | None:
    """Strong/medium from row flags; weak returns None (out of v1 scope)."""
    if row.get("fullCross") is True:
        return "a_full_cross"
    if row.get("mediumCrossEvent") is True:
        return "a_medium_cross"
    crosses = row.get("technical_crosses") or (row.get("emaObservation") or {}).get("technical_crosses") or {}
    if crosses.get("full_cross_path_a") is True:
        return "a_full_cross"
    if crosses.get("medium_ema7_25_cross") is True:
        return "a_medium_cross"
    if crosses.get("weak_ema99_cross") is True or row.get("weakNewCross") is True:
        return None  # out of scope, not an error by itself
    return None


def map_event_kind(legacy_kind: str) -> str:
    if legacy_kind in OUT_OF_SCOPE_KINDS:
        raise MappingError("EVENT_KIND_out_of_v1_scope", legacy_kind=legacy_kind)
    if legacy_kind not in LEGACY_KIND_TO_EVENT_KIND:
        raise MappingError("EVENT_KIND_unknown", legacy_kind=legacy_kind)
    return LEGACY_KIND_TO_EVENT_KIND[legacy_kind]


def map_profile_id(event_kind: str) -> str:
    if event_kind not in EVENT_KIND_TO_PROFILE:
        raise MappingError("PROFILE_unknown_event_kind", event_kind=event_kind)
    return EVENT_KIND_TO_PROFILE[event_kind]


def map_level(event_kind: str) -> str:
    if event_kind not in EVENT_KIND_TO_LEVEL:
        raise MappingError("LEVEL_unknown_event_kind", event_kind=event_kind)
    return EVENT_KIND_TO_LEVEL[event_kind]


def discovery_for_archive(*, historical_import: bool = True, live_scan: bool = False) -> str:
    """Historical archive import must NOT be marked live."""
    if live_scan and not historical_import:
        return "live"
    if historical_import:
        return "import"
    return "historical_replay"


def canonical_event_id(*, event_kind: str, market: str, symbol: str, open_ms: int,
                       rule_version: str = "ema-squeeze-v4") -> str:
    """Frozen A identity; market must already be canonical."""
    if market not in ("USDT-M", "COIN-M"):
        raise MappingError("EVENT_ID_market_not_canonical", market=market)
    if event_kind not in EVENT_KIND_TO_PROFILE:
        raise MappingError("EVENT_ID_bad_kind", event_kind=event_kind)
    return f"A|{event_kind}|{market}|{symbol}|4h|{rule_version}|{open_ms}"


def legacy_technical_id(*, legacy_market: str, symbol: str, inclusive_close_ms: int,
                        legacy_kind: str, rules_version: str = "ema-squeeze-v4") -> str:
    """Original ema_observations.bind_identity shape (preserved as alias, not rewritten)."""
    return "|".join(map(str, (legacy_market, symbol, "4h", rules_version, inclusive_close_ms, legacy_kind)))


def legacy_candidate_event_id(*, legacy_market: str, symbol: str, candidate_cycle_id: str,
                              inclusive_close_ms: int, level: str,
                              rules_version: str = "ema-squeeze-v4") -> str:
    """Original ema_event_timing.event_id shape (alias only)."""
    return "|".join(map(str, (legacy_market, symbol, "4h", rules_version,
                              candidate_cycle_id, inclusive_close_ms, level)))


@dataclass(frozen=True)
class IdentityMapping:
    canonical_event_id: str
    event_kind: str
    profile_id: str
    level: str
    market_canonical: str
    market_legacy: str
    symbol: str
    bar: BarTimeMapping
    legacy_aliases: tuple[str, ...]  # schema/ledger form (canonical market in id)
    legacy_original_ids: tuple[str, ...]  # production raw form (USDⓈ-M etc.)
    discovery: str
    historical_import: bool

    def as_dict(self) -> dict:
        return {
            "canonical_event_id": self.canonical_event_id,
            "event_kind": self.event_kind,
            "profile_id": self.profile_id,
            "level": self.level,
            "market_canonical": self.market_canonical,
            "market_legacy": self.market_legacy,
            "symbol": self.symbol,
            "bar": self.bar.as_dict(),
            "legacy_aliases": list(self.legacy_aliases),
            "legacy_original_ids": list(self.legacy_original_ids),
            "discovery": self.discovery,
            "historical_import": self.historical_import,
            "mapping_protocol_version": MAPPING_PROTOCOL_VERSION,
            "note_zh": "legacy_aliases 用规范 market 以满足账本 E09；legacy_original_ids 保留生产原 market 字符串可追溯",
        }


def map_row_identity(row: dict, *, historical_import: bool = True,
                     candidate_cycle_id: str | None = None) -> IdentityMapping | None:
    """Map a scan row to canonical identity. Returns None if no strong/medium event."""
    kind = map_event_kind_from_row(row)
    if kind is None:
        return None
    legacy_market = row.get("market")
    market = map_market(legacy_market)
    symbol = row.get("symbol")
    if not symbol:
        raise MappingError("SYMBOL_missing", row_market=legacy_market)
    bar = map_inclusive_close_to_exclusive(row.get("candleAsOf"))
    eid = canonical_event_id(event_kind=kind, market=market, symbol=symbol, open_ms=bar.open_ms)
    legacy_kind = "full_cross_path_a" if kind == "a_full_cross" else "medium_ema7_25_cross"
    level = map_level(kind)
    # Ledger E09 rebuilds bind_identity with event.market (canonical). Keep original separately.
    aliases = [
        legacy_technical_id(legacy_market=market, symbol=symbol,
                            inclusive_close_ms=bar.legacy_inclusive_close_ms, legacy_kind=legacy_kind),
    ]
    originals = [
        legacy_technical_id(legacy_market=legacy_market, symbol=symbol,
                            inclusive_close_ms=bar.legacy_inclusive_close_ms, legacy_kind=legacy_kind),
    ]
    if candidate_cycle_id:
        aliases.append(legacy_candidate_event_id(
            legacy_market=market, symbol=symbol, candidate_cycle_id=candidate_cycle_id,
            inclusive_close_ms=bar.legacy_inclusive_close_ms, level=level,
        ))
        originals.append(legacy_candidate_event_id(
            legacy_market=legacy_market, symbol=symbol, candidate_cycle_id=candidate_cycle_id,
            inclusive_close_ms=bar.legacy_inclusive_close_ms, level=level,
        ))
    return IdentityMapping(
        canonical_event_id=eid,
        event_kind=kind,
        profile_id=map_profile_id(kind),
        level=level,
        market_canonical=market,
        market_legacy=str(legacy_market),
        symbol=str(symbol),
        bar=bar,
        legacy_aliases=tuple(aliases),
        legacy_original_ids=tuple(originals),
        discovery=discovery_for_archive(historical_import=historical_import),
        historical_import=historical_import,
    )


# Four-way separation (confirm / qualification / observation / quality)
STATUS_DIMENSIONS = (
    "original_confirmation",  # technical_status / confirm block
    "qualification",          # qualification_status; never use current candidate JSON as historical evidence
    "follow_up_observation",  # session profile / observation_status
    "data_quality",           # missing_data_flags / quality gaps — not structure failure
)


def separate_status_skeleton(*, technical_status: str, qualification_status: str,
                             observation_status: str | None, data_quality_summary: str,
                             historical_import: bool) -> dict:
    return {
        "original_confirmation": {"technical_status": technical_status},
        "qualification": {"status": qualification_status},
        "follow_up_observation": {"status": observation_status},
        "data_quality": {"summary": data_quality_summary},
        "historical_import": historical_import,
        "note_zh": "四维分列；历史导入不标 live；资格证据不得用当前候选 JSON 冒充当时历史",
    }
