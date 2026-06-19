from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

PHASE_1_CANONICAL_SYMBOLS: tuple[str, ...] = (
    "XAU/USD",
    "XAG/USD",
    "XPT/USD",
    "XPD/USD",
    "WTI Oil",
    "Brent Oil",
    "Nat Gas",
    "Gasoline",
    "Copper",
)

INDUSTRIAL_METAL_CANDIDATE_ORDER: tuple[str, ...] = (
    "Copper",
    "Aluminium",
    "Nickel",
    "Zinc",
)

PHASE_1_MT5_TERMINAL_ALIASES: dict[str, str] = {
    "XAU/USD": "XAUUSD",
    "XAG/USD": "XAGUSD",
    "XPT/USD": "XPTUSD",
    "XPD/USD": "XPDUSD",
    "WTI Oil": "SpotCrude",
    "Brent Oil": "SpotBrent",
    "Nat Gas": "NatGas",
    "Gasoline": "Gasoline",
    "Copper": "Copper",
}

PHASE_1_TIMEFRAMES: tuple[str, ...] = ("H4", "H1", "D1")

DEFAULT_REQUESTED_START = datetime(2014, 1, 1, tzinfo=timezone.utc)

RAW_ROOT = "logs/commodity_data_audit/raw/v1"
NORMALIZED_ROOT = "logs/commodity_data_audit/normalized/commodity_ohlcv_v1"
NORMALIZED_SCHEMA = "commodity_ohlcv_v1"
MANIFEST_SCHEMA = "commodity_freeze_manifest_v1"
MANIFEST_SCHEMA_V2 = "commodity_freeze_manifest_v2"
MANIFEST_SCHEMA_V3 = "commodity_freeze_manifest_v3"
QA_ALGORITHM_VERSION = "commodity_freeze_qa_v2"
QA_ALGORITHM_VERSION_V3 = "commodity_freeze_qa_v3"
QA_ALGORITHM_VERSION_V3_1 = "commodity_freeze_qa_v3_1"
MIN_RELIABLE_H4_BARS = 500

# Default corroborating peer for reliable-era H4 gap review (symbol-specific; never inherited).
DEFAULT_RELIABLE_PEER_BY_SYMBOL: dict[str, str] = {
    "XAU/USD": "XAG/USD",
    "XAG/USD": "XAU/USD",
}


def default_reliable_peer_symbol(canonical_symbol: str) -> str | None:
    return DEFAULT_RELIABLE_PEER_BY_SYMBOL.get(canonical_symbol)

# Explicitly rejected research-loader aliases for Phase-1 energy symbols.
REJECTED_RESEARCH_LOADER_ALIASES: dict[str, tuple[str, ...]] = {
    "WTI Oil": ("USOIL",),
    "Brent Oil": ("UKOIL",),
}

CHUNK_DAYS_BY_TIMEFRAME: dict[str, int] = {
    "D1": 365,
    "H4": 90,
    "H1": 30,
}

MIN_BARS_BY_TIMEFRAME: dict[str, int] = {
    "D1": 230,
    "H4": 500,
    "H1": 500,
}


def resolve_phase1_mt5_symbol(canonical_display: str) -> str:
    symbol = PHASE_1_MT5_TERMINAL_ALIASES.get(canonical_display)
    if not symbol:
        raise KeyError(f"unsupported Phase-1 canonical symbol: {canonical_display}")
    rejected = REJECTED_RESEARCH_LOADER_ALIASES.get(canonical_display, ())
    if symbol in rejected:
        raise ValueError(f"resolved alias {symbol} is a blocked research-loader alias")
    return symbol


def parse_utc_datetime(value: str) -> datetime:
    text = str(value or "").strip()
    if not text:
        raise ValueError("empty datetime")
    if len(text) == 10:
        dt = datetime.strptime(text, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        return dt
    dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def slug_symbol(canonical_display: str) -> str:
    return canonical_display.replace("/", "_").replace(" ", "_")
