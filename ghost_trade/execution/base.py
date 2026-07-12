"""Common immutable contracts for demo-only broker adapters."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping

from ..models import DemoVerificationStatus, GhostSignal, Venue


@dataclass(frozen=True, slots=True)
class DemoAttestation:
    venue: Venue
    verified: bool
    status: DemoVerificationStatus
    account_type: str
    server: str
    reason: str | None = None


@dataclass(frozen=True, slots=True)
class DemoExecutionResult:
    success: bool
    status: str
    error_code: str | None = None
    broker_order_id: str | None = None
    broker_position_id: str | None = None
    entry_price: float | None = None
    raw_response: Mapping[str, Any] = field(default_factory=dict)


def ghost_executor_payload(signal: GhostSignal) -> dict[str, Any]:
    if signal.entry is None or signal.stop is None or signal.target is None:
        raise ValueError("invalid_structural_geometry")
    return {
        "pair": signal.instrument.broker_symbol,
        "symbol": signal.instrument.broker_symbol,
        "display": signal.instrument.canonical_symbol,
        "type": signal.instrument.asset_group.value,
        "direction": signal.direction.value,
        "price": signal.entry,
        "livePrice": signal.entry,
        "sl": signal.stop,
        "tp1": signal.target,
        "tp2": signal.target,
        "rr": signal.raw_rr,
        "confluenceScore": signal.confirmed_score,
        "maxScore": 1.0,
        "entryQuality": signal.entry_quality,
        "engine": "GHOST_TRADE",
        "ghostSignalId": signal.signal_id,
        "ghostSignalVersion": signal.signal_version,
        "exit_strategy": "STRUCTURAL",
        "timestamp": signal.decision_time.isoformat(),
        "dataFreshness": signal.data_freshness,
        "candleFreshness": {
            timeframe: {"status": "FRESH", "lastBarIso": timestamp}
            for timeframe, timestamp in signal.confirmed_times.items()
        },
        "candleConsistency": {
            timeframe: {"status": "CONSISTENT", "lastBarIso": timestamp}
            for timeframe, timestamp in signal.confirmed_times.items()
        },
    }


def result_from_shared(response: Any, *, default_error: str) -> DemoExecutionResult:
    if not isinstance(response, Mapping):
        return DemoExecutionResult(False, "REJECTED", default_error)
    success = bool(response.get("success"))
    order_id = response.get("ticket") or response.get("orderId") or response.get("order_id")
    position_id = response.get("positionId") or response.get("position_id")
    entry = response.get("entryPrice", response.get("entry_price"))
    try:
        entry_price = float(entry) if entry is not None else None
    except (TypeError, ValueError):
        entry_price = None
    return DemoExecutionResult(
        success=success,
        status="SUBMITTED" if success else "REJECTED",
        error_code=None if success else str(response.get("error") or default_error),
        broker_order_id=str(order_id) if order_id is not None else None,
        broker_position_id=str(position_id) if position_id is not None else None,
        entry_price=entry_price,
        raw_response=dict(response),
    )
