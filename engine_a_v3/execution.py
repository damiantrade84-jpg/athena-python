from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any


_AUTHORITATIVE_FIELDS = (
    "contractVersion",
    "signalId",
    "engine",
    "pair",
    "symbol",
    "type",
    "scoreGroup",
    "family",
    "subclass",
    "horizon",
    "setupId",
    "decision",
    "qualified",
    "direction",
    "decisionTime",
    "lastConfirmedCandleTs",
    "validUntil",
    "entryZone",
    "invalidation",
    "targets",
    "price",
    "entry",
    "sl",
    "tp",
    "tp1",
    "tp2",
    "rr",
    "rr1",
    "rr2",
    "predicates",
    "rejectionReasons",
    "dataFreshness",
    "validationArtifact",
    "validationStatus",
    "executionScope",
    "engineATradeEnabled",
    "trade",
    "executable",
    "signalTier",
    "signalClass",
    "timestamp",
    "style",
)


@dataclass(frozen=True)
class DemoAttestation:
    allowed: bool
    reason: str
    venue: str


def is_engine_a_v3_signal(signal: dict[str, Any] | None) -> bool:
    if not isinstance(signal, dict):
        return False
    return (
        str(signal.get("engine") or "").upper() == "ENGINE_A_V3"
        and str(signal.get("contractVersion") or "").startswith("3.")
    )


def attest_demo_execution(
    *,
    executor_mode: str | None,
    venue: str,
    mt5_trade_mode: int | None = None,
    bybit_demo: bool | None = None,
) -> DemoAttestation:
    normalized_venue = str(venue or "").strip().lower()
    if str(executor_mode or "").strip().lower() != "demo":
        return DemoAttestation(False, "EXECUTOR_MODE_NOT_DEMO", normalized_venue)
    if normalized_venue == "mt5":
        if mt5_trade_mode != 0:
            return DemoAttestation(False, "MT5_DEMO_ACCOUNT_NOT_VERIFIED", normalized_venue)
        return DemoAttestation(True, "MT5_DEMO_VERIFIED", normalized_venue)
    if normalized_venue == "bybit":
        if bybit_demo is not True:
            return DemoAttestation(False, "BYBIT_DEMO_ENDPOINT_NOT_VERIFIED", normalized_venue)
        return DemoAttestation(True, "BYBIT_DEMO_VERIFIED", normalized_venue)
    return DemoAttestation(False, "EXECUTION_VENUE_UNSUPPORTED", normalized_venue)


def _parse_timestamp(value: Any) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(str(value or "").replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def verify_refreshed_signal(
    original: dict[str, Any],
    refreshed: dict[str, Any] | None,
    *,
    now: datetime | None = None,
) -> tuple[bool, str | None]:
    if not is_engine_a_v3_signal(original) or not is_engine_a_v3_signal(refreshed):
        return False, "ENGINE_A_V3_REFRESH_CONTRACT_INVALID"
    assert refreshed is not None
    for field in ("setupId", "direction", "horizon"):
        if str(refreshed.get(field) or "") != str(original.get(field) or ""):
            return False, f"ENGINE_A_V3_REFRESH_{field.upper()}_CHANGED"
    if refreshed.get("decision") != "TRADE" or refreshed.get("qualified") is not True:
        return False, "ENGINE_A_V3_REFRESH_NOT_TRADE_QUALIFIED"
    if refreshed.get("engineATradeEnabled") is not True:
        return False, "ENGINE_A_V3_REFRESH_EXECUTION_DISABLED"
    if (
        refreshed.get("validationStatus") == "UNVALIDATED"
        and refreshed.get("executionScope") != "DEMO_ONLY"
    ):
        return False, "ENGINE_A_V3_REFRESH_UNVALIDATED_SCOPE_INVALID"
    freshness = refreshed.get("dataFreshness")
    if not isinstance(freshness, dict) or freshness.get("allowed") is not True:
        return False, "ENGINE_A_V3_REFRESH_DATA_NOT_FRESH"
    valid_until = _parse_timestamp(refreshed.get("validUntil"))
    if valid_until is None or valid_until <= (now or datetime.now(timezone.utc)):
        return False, "ENGINE_A_V3_REFRESH_EXPIRED"
    for field in ("price", "sl", "tp1"):
        try:
            value = float(refreshed[field])
        except (KeyError, TypeError, ValueError):
            return False, f"ENGINE_A_V3_REFRESH_{field.upper()}_INVALID"
        if value <= 0:
            return False, f"ENGINE_A_V3_REFRESH_{field.upper()}_INVALID"
    return True, None


def merge_refreshed_signal(
    original: dict[str, Any],
    refreshed: dict[str, Any],
) -> dict[str, Any]:
    merged = dict(original)
    for field in _AUTHORITATIVE_FIELDS:
        if field in refreshed:
            merged[field] = refreshed[field]
    merged["level_source"] = "engine_a_v3_refresh"
    return merged
