from __future__ import annotations

import pandas as pd


MAJORS = frozenset(
    {"EURUSD", "GBPUSD", "USDJPY", "AUDUSD", "NZDUSD", "USDCAD", "USDCHF"}
)


def roundtrip_cost_bps(symbol: str) -> float:
    return 1.8 if symbol in MAJORS else 4.1


def rebalance_cost(
    previous: pd.Series,
    current: pd.Series,
    costs_bps: dict[str, float],
    *,
    multiplier: float,
) -> float:
    aligned = pd.concat([previous, current], axis=1).fillna(0.0)
    aligned.columns = ["previous", "current"]
    delta = (aligned["current"] - aligned["previous"]).abs()
    per_side = pd.Series(
        {symbol: costs_bps[symbol] / 2e4 for symbol in delta.index}
    )
    return float((delta * per_side * multiplier).sum())
