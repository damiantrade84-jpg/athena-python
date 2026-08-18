"""Cost-aware expectancy: the gate that decides whether a setup is a trade.

A conviction score is not a reason to trade. The only reason to trade is a
positive expected value after the full round-trip cost of getting in and out.

Intraday, that distinction is decisive rather than academic. A 55%-probability
setup at 1.5R is a good trade with a 0.05R cost and a losing trade with a 0.35R
cost, and the second case is entirely normal on a tight stop: cost expressed in
R is INVERSE to stop distance, so the tighter the stop, the larger the fraction
of risk the spread consumes. Systems that gate on score alone systematically
select the setups where cost is worst, because tight stops flatter every
score-per-unit-risk metric.

    cost_r = (spread + 2*commission + 2*slippage) / stop_distance
    E[R]   = p * RR - (1 - p) - cost_r

Both sides of the round trip are charged. Charging one side understates the
true cost by half and is the most common way this calculation is got wrong.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

import opus.config as config
from opus.types import Levels, Quote


@dataclass
class CostBreakdown:
    """Round-trip cost, in price units and expressed in R."""

    spread: float
    commission: float
    slippage: float
    total_price: float
    cost_r: float
    stop_distance: float
    spread_source: str

    def as_dict(self) -> dict:
        return {
            "spread": round(float(self.spread), 8),
            "commission": round(float(self.commission), 8),
            "slippage": round(float(self.slippage), 8),
            "totalPrice": round(float(self.total_price), 8),
            "costR": round(float(self.cost_r), 5),
            "stopDistance": round(float(self.stop_distance), 8),
            "spreadSource": self.spread_source,
        }


@dataclass
class Expectancy:
    probability: float
    rr: float
    cost_r: float
    expectancy_r: float
    kelly_fraction: float
    breakeven_probability: float

    def as_dict(self) -> dict:
        return {
            "probability": round(float(self.probability), 4),
            "rr": round(float(self.rr), 4),
            "costR": round(float(self.cost_r), 5),
            "expectancyR": round(float(self.expectancy_r), 5),
            "kellyFraction": round(float(self.kelly_fraction), 5),
            "breakevenProbability": round(float(self.breakeven_probability), 4),
        }


def compute_cost(
    *,
    levels: Levels,
    quote: Quote | None,
    asset_class: str,
    reference_price: float | None = None,
) -> CostBreakdown:
    """Round-trip cost for this specific trade geometry.

    Missing a live spread does not mean zero spread. When no quote is
    available the configured slippage allowance is charged as a stand-in and
    the source is labelled, so a signal can never look cheap merely because
    the feed was down.
    """
    model = config.cost_model(asset_class)
    stop_distance = float(levels.risk_per_unit)
    price = float(
        reference_price
        if reference_price is not None
        else (quote.mid if quote is not None else levels.entry)
    )
    if price <= 0:
        price = abs(float(levels.entry)) or 1.0

    if quote is not None and bool(model.get("use_live_spread", True)):
        spread = float(quote.spread)
        spread_source = quote.source or "quote"
    else:
        # No quote: assume a spread equal to the slippage allowance rather
        # than assuming zero.
        spread = price * float(model["slippage_bps"]) / 10_000.0
        spread_source = "assumed_no_quote"

    commission = 2.0 * price * float(model["commission_bps"]) / 10_000.0
    slippage = 2.0 * price * float(model["slippage_bps"]) / 10_000.0
    total = spread + commission + slippage

    if stop_distance <= 0 or not np.isfinite(stop_distance):
        cost_r = float("inf")
    else:
        cost_r = total / stop_distance

    return CostBreakdown(
        spread=spread,
        commission=commission,
        slippage=slippage,
        total_price=total,
        cost_r=float(cost_r),
        stop_distance=stop_distance,
        spread_source=spread_source,
    )


def kelly(probability: float, rr: float, cost_r: float = 0.0) -> float:
    """Kelly fraction for a binary payoff, net of cost.

    f* = p - (1 - p) / b, with b the net reward multiple. Negative means the
    edge is against you and the correct size is zero, not a small short.
    """
    b = float(rr) - float(cost_r)
    if b <= 0:
        return 0.0
    p = float(np.clip(probability, 0.0, 1.0))
    f = p - (1.0 - p) / b
    return float(max(f, 0.0))


def evaluate(
    *,
    probability: float,
    levels: Levels,
    quote: Quote | None,
    asset_class: str,
    target_index: int = 0,
) -> tuple[Expectancy, CostBreakdown]:
    """Net expectancy in R for this trade, after the full round trip."""
    cost = compute_cost(levels=levels, quote=quote, asset_class=asset_class)
    rr = float(levels.reward_multiple(target_index))
    p = float(np.clip(probability, 0.0, 1.0))

    if not np.isfinite(cost.cost_r):
        expectancy_r = float("-inf")
    else:
        expectancy_r = p * rr - (1.0 - p) - cost.cost_r

    # Probability at which this geometry breaks even, given its cost. Reported
    # so the UI can show how much of the edge is structural versus predictive.
    denom = rr + 1.0
    breakeven = ((1.0 + cost.cost_r) / denom) if denom > 0 else 1.0

    return (
        Expectancy(
            probability=p,
            rr=rr,
            cost_r=float(cost.cost_r),
            expectancy_r=float(expectancy_r),
            kelly_fraction=kelly(p, rr, cost.cost_r),
            breakeven_probability=float(np.clip(breakeven, 0.0, 1.0)),
        ),
        cost,
    )


def spread_pct_for(quote: Quote | None) -> float:
    return float(quote.spread_pct) if quote is not None else float("inf")


__all__ = [
    "CostBreakdown", "Expectancy", "compute_cost", "kelly", "evaluate",
    "spread_pct_for",
]
