"""Phase 4: WATCH state transitions and their audit log.

WATCH holds a valid direction whose entry is currently bad. It is armed
deterministically from Engine A data and price geometry — the AI review may
annotate a WATCH but must never trigger, gate, or clear one.

Invariants preserved here:
* WATCH never bypasses ``risk_check()`` — nothing in this module executes.
* WATCH does not size anything; sizing stays in ``risk_engine`` on promotion.
* A WATCH derived from a non-validated artifact stays SHADOW and cannot promote.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

log = logging.getLogger(__name__)

ARMED = "armed"
RE_ARMED = "re_armed"
EXPIRED = "expired"
INVALIDATED = "invalidated"
PROMOTED = "promoted"
HELD = "held"

TRANSITIONS = (ARMED, RE_ARMED, EXPIRED, INVALIDATED, PROMOTED, HELD)


def _f(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return out if out == out else None


def _invalidated(direction: str, price: float | None, invalidation: float | None) -> bool:
    if price is None or invalidation is None:
        return False
    if direction == "LONG":
        return price < invalidation
    if direction == "SHORT":
        return price > invalidation
    return False


def _in_watch_zone(price: float | None, zone: Any) -> bool:
    if price is None or not isinstance(zone, dict):
        return False
    low, high = _f(zone.get("low")), _f(zone.get("high"))
    if low is None or high is None:
        return False
    if low > high:
        low, high = high, low
    return low <= price <= high


def resolve_watch_transition(
    *,
    previous: dict[str, Any] | None,
    current: dict[str, Any] | None,
    price: float | None = None,
    bar_closed: bool = False,
    expired: bool = False,
) -> str | None:
    """Classify the WATCH transition. Deterministic; no AI input.

    ``None`` means there is no WATCH in play at all.
    """
    had = bool(previous and previous.get("state") == "WATCH")
    has = bool(current and current.get("state") == "WATCH")

    if not had and not has:
        return None

    direction = str((current or previous or {}).get("direction") or "").upper()
    invalidation = _f((current or previous or {}).get("invalidation"))
    if _invalidated(direction, price, invalidation):
        return INVALIDATED
    if expired:
        return EXPIRED

    if not had and has:
        return ARMED
    if had and not has:
        # Left WATCH without invalidation or expiry: entry became acceptable.
        return PROMOTED
    # Still watching — re-arm only on a closed bar back inside the zone.
    if bar_closed and _in_watch_zone(price, (current or {}).get("watch_zone")):
        return RE_ARMED
    return HELD


def build_watch_log_record(
    *,
    transition: str,
    watch_state: dict[str, Any] | None,
    price: float | None = None,
    atr: float | None = None,
    reason: str | None = None,
    symbol: str | None = None,
) -> dict[str, Any]:
    """Structured record: price, ATR, RR at transition, and reason."""
    state = watch_state or {}
    rr = state.get("rr_at_watch") or {}
    return {
        "event": "watch_transition",
        "transition": transition,
        "symbol": symbol,
        "direction": state.get("direction"),
        "price": _f(price),
        "atr": _f(atr),
        "rrAtTransition": {
            "tp1": rr.get("rr_live_tp1"),
            "tp2": rr.get("rr_live_tp2"),
            "blended": rr.get("rr_live_blended"),
        },
        "watchZone": state.get("watch_zone"),
        "invalidation": state.get("invalidation"),
        "slAnchored": state.get("sl_anchored"),
        "minAtrFloor": state.get("min_atr_floor"),
        "shadowOnly": bool(state.get("shadow_only")),
        # WATCH is observational: nothing here authorises execution or sizing.
        "executionPermitted": False,
        "aiGated": False,
        "reason": reason or state.get("reason"),
        "loggedAt": datetime.now(timezone.utc).isoformat(),
    }


def log_watch_transition(
    *,
    transition: str,
    watch_state: dict[str, Any] | None,
    price: float | None = None,
    atr: float | None = None,
    reason: str | None = None,
    symbol: str | None = None,
) -> dict[str, Any] | None:
    """Emit the transition record. Returns it for attachment to the payload."""
    if transition not in TRANSITIONS:
        return None
    record = build_watch_log_record(
        transition=transition,
        watch_state=watch_state,
        price=price,
        atr=atr,
        reason=reason,
        symbol=symbol,
    )
    if transition != HELD:
        log.info(
            "[WATCH] %s %s price=%s atr=%s rr_tp1=%s shadow=%s reason=%s",
            transition,
            record.get("symbol"),
            record.get("price"),
            record.get("atr"),
            (record.get("rrAtTransition") or {}).get("tp1"),
            record.get("shadowOnly"),
            record.get("reason"),
        )
    return record
