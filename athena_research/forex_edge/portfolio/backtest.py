from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from athena_research.forex_edge.portfolio.construction import (
    map_currency_weights_to_pairs,
)
from athena_research.forex_edge.portfolio.costs import rebalance_cost


@dataclass(frozen=True)
class PortfolioBacktest:
    daily_returns: pd.DataFrame
    positions: pd.DataFrame
    turnover: float
    total_cost: float


def run_monthly_portfolio(
    pair_returns: pd.DataFrame,
    decisions: dict[pd.Timestamp, pd.Series],
    *,
    roundtrip_cost_bps: dict[str, float],
    cost_multiplier: float,
) -> PortfolioBacktest:
    work = pair_returns.copy()
    work["timestamp"] = pd.to_datetime(work["timestamp"], utc=True)
    pivot = (
        work.pivot(index="timestamp", columns="symbol", values="return")
        .sort_index()
        .fillna(0.0)
    )
    current = pd.Series(dtype=float)
    pending = [
        (pd.Timestamp(decision).tz_convert("UTC"), map_currency_weights_to_pairs(weights))
        for decision, weights in decisions.items()
    ]
    pending.sort(key=lambda item: item[0])
    pending_index = 0
    rows: list[dict[str, object]] = []
    positions: list[dict[str, object]] = []
    total_cost = 0.0
    total_turnover = 0.0

    for timestamp, returns in pivot.iterrows():
        cost = 0.0
        while pending_index < len(pending) and timestamp > pending[pending_index][0]:
            target = pending[pending_index][1]
            missing = set(target.index) - set(pivot.columns)
            if missing:
                raise ValueError(f"MISSING_PAIR:{sorted(missing)}")
            cost += rebalance_cost(
                current,
                target,
                roundtrip_cost_bps,
                multiplier=cost_multiplier,
            )
            aligned = pd.concat([current, target], axis=1).fillna(0.0)
            aligned.columns = ["previous", "current"]
            total_turnover += float((aligned["current"] - aligned["previous"]).abs().sum())
            current = target
            pending_index += 1
        gross_return = float((returns.reindex(current.index).fillna(0.0) * current).sum())
        net_return = gross_return - cost
        total_cost += cost
        rows.append(
            {
                "timestamp": timestamp,
                "gross_return": gross_return,
                "cost": cost,
                "net_return": net_return,
                "gross_exposure": float(current.abs().sum()) if len(current) else 0.0,
                "net_pair_exposure": float(current.sum()) if len(current) else 0.0,
            }
        )
        pos_row = {"timestamp": timestamp}
        pos_row.update({symbol: float(value) for symbol, value in current.items()})
        positions.append(pos_row)

    if len(current):
        liquidation = rebalance_cost(
            current,
            pd.Series(dtype=float),
            roundtrip_cost_bps,
            multiplier=cost_multiplier,
        )
        total_cost += liquidation
        total_turnover += float(current.abs().sum())
        if rows:
            rows[-1]["cost"] = float(rows[-1]["cost"]) + liquidation
            rows[-1]["net_return"] = float(rows[-1]["gross_return"]) - float(rows[-1]["cost"])

    return PortfolioBacktest(
        daily_returns=pd.DataFrame(rows),
        positions=pd.DataFrame(positions).fillna(0.0),
        turnover=total_turnover,
        total_cost=total_cost,
    )
