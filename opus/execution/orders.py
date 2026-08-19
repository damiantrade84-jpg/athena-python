"""Turning a signal's geometry into an order the broker can rest.

OPUS geometry is not a market order. `SWEEP_RECLAIM` enters on a limit at the
reclaimed pool, `DISPLACEMENT_CONTINUATION` at the midpoint of a void price has
not returned to yet, `VACUUM_BREAK` on a stop beyond the coil. Only
`VALUE_FADE` is genuinely "buy it here".

Filling any of the first three at the market instead collapses the whole model:
the stop distance every gate was evaluated against changes, the reward multiple
that cleared `min_rr` changes, and the position size that expressed
`risk_pct_max` no longer expresses it. Measured on synthetic paths, the median
distance between a planned entry and the live mid was 0.72R for SWEEP_RECLAIM
and 0.55R for VACUUM_BREAK; in one case a 1.35R setup became a 1.02R fill with
16% more risk than authorised.

So an order rests at the planned price or it is not sent. The side rules are
not a policy choice, they are what a resting order MEANS:

    a buy limit rests BELOW the market   (above it, it fills instantly)
    a buy stop  rests ABOVE the market   (below it, the break already happened)

and mirrored for a sell. A plan that cannot rest is rejected with its reason
rather than downgraded to a market order, because "the price I wanted is gone"
and "here is a different trade" are not the same statement.
"""

from __future__ import annotations

import time
from dataclasses import dataclass

import numpy as np

import opus.config as config
from opus.types import Direction, Quote, Signal

# Order kinds OPUS can express, independent of any venue's own enum.
MARKET = "market"
LIMIT = "limit"
STOP = "stop"


@dataclass(frozen=True)
class OrderPlan:
    """How one signal should reach a broker, or why it cannot."""

    kind: str                       # market | limit | stop | reject
    price: float = 0.0
    expires_ts: float | None = None
    reason: str = ""

    @property
    def rejected(self) -> bool:
        return self.kind == "reject"

    @property
    def is_pending(self) -> bool:
        return self.kind in (LIMIT, STOP)

    def as_dict(self) -> dict:
        return {
            "kind": self.kind,
            "price": round(float(self.price), 8),
            "expiresTs": self.expires_ts,
            "reason": self.reason,
        }


def _reject(reason: str) -> OrderPlan:
    return OrderPlan(kind="reject", reason=reason)


def resolve_order_plan(
    signal: Signal, quote: Quote | None, *, now: float | None = None
) -> OrderPlan:
    """The order this signal's geometry actually implies, given the market."""
    now = float(now if now is not None else time.time())

    if quote is None:
        return _reject("no live quote: an order price cannot be established")
    if not (np.isfinite(quote.bid) and np.isfinite(quote.ask)):
        return _reject("live quote is not finite")
    if quote.bid <= 0 or quote.ask < quote.bid:
        return _reject("live quote is inverted or non-positive")

    entry = float(signal.levels.entry)
    if not np.isfinite(entry) or entry <= 0:
        return _reject("planned entry is not a usable price")

    kind = str(signal.levels.entry_kind or "").strip().lower()
    is_long = signal.direction is Direction.LONG

    if kind == MARKET:
        # The one archetype that means "now": price it by crossing the spread,
        # exactly as the cost model assumed.
        return OrderPlan(kind=MARKET, price=quote.entry_price(signal.direction))

    if kind not in (LIMIT, STOP):
        return _reject(f"unsupported entry kind '{signal.levels.entry_kind}'")

    # The side the order has to rest on, measured against the price this
    # direction would actually transact at.
    reference = quote.ask if is_long else quote.bid
    expiry = float(config.load()["EXECUTION"]["limit_expiry_sec"])
    expires_ts = now + expiry if expiry > 0 else None

    if kind == LIMIT:
        rests = entry < reference if is_long else entry > reference
        if not rests:
            side = "buy" if is_long else "sell"
            return _reject(
                f"a {side} limit at {entry:.8g} cannot rest against "
                f"{reference:.8g}; it would fill immediately at the market"
            )
        return OrderPlan(kind=LIMIT, price=entry, expires_ts=expires_ts)

    rests = entry > reference if is_long else entry < reference
    if not rests:
        return _reject(
            f"the break through {entry:.8g} has already happened "
            f"(market {reference:.8g}); entering now would be chasing it"
        )
    return OrderPlan(kind=STOP, price=entry, expires_ts=expires_ts)


__all__ = ["OrderPlan", "resolve_order_plan", "MARKET", "LIMIT", "STOP"]
