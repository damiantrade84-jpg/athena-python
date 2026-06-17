from __future__ import annotations


def stressed_trade_return(
    *,
    direction: int,
    entry_bid: float,
    entry_ask: float,
    exit_bid: float,
    exit_ask: float,
    commission_bps: float,
    cost_multiplier: float,
) -> tuple[float, float, float]:
    entry_mid = (entry_bid + entry_ask) / 2.0
    exit_mid = (exit_bid + exit_ask) / 2.0
    gross_mid = direction * (exit_mid / entry_mid - 1.0)
    executable = (
        exit_bid / entry_ask - 1.0
        if direction > 0
        else entry_bid / exit_ask - 1.0
    )
    observed_spread_cost = gross_mid - executable
    commission = commission_bps / 1e4
    net = gross_mid - (observed_spread_cost + commission) * cost_multiplier
    return float(gross_mid), float(observed_spread_cost), float(net)
