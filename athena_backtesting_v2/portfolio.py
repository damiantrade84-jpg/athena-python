from __future__ import annotations

from datetime import datetime
from typing import Any

from .models import PortfolioPlan


def simulate_portfolio(trades: list[dict[str, Any]], plan: PortfolioPlan) -> dict[str, Any] | None:
    if not plan.enabled:
        return None
    ledgers: dict[str, dict[str, Any]] = {}
    for engine in ("A", "B"):
        capital = float(plan.initial_capital)
        peak = capital
        max_drawdown = 0.0
        accepted = 0
        rejected = 0
        open_positions: list[datetime] = []
        curve = [{"time": None, "equity": capital}]
        engine_trades = sorted(
            (trade for trade in trades if trade.get("engine") == engine),
            key=lambda trade: trade["entry_time"],
        )
        for trade in engine_trades:
            entry_time = datetime.fromisoformat(str(trade["entry_time"]))
            exit_time = datetime.fromisoformat(str(trade["exit_time"]))
            open_positions = [timestamp for timestamp in open_positions if timestamp > entry_time]
            if len(open_positions) >= plan.max_concurrent_positions:
                rejected += 1
                continue
            risk_amount = capital * plan.risk_per_trade
            capital += risk_amount * float(trade.get("net_r") or 0.0)
            peak = max(peak, capital)
            max_drawdown = min(max_drawdown, capital - peak)
            accepted += 1
            open_positions.append(exit_time)
            curve.append({"time": trade["exit_time"], "equity": round(capital, 2)})
        ledgers[engine] = {
            "initialCapital": plan.initial_capital,
            "finalCapital": round(capital, 2),
            "returnPct": round((capital / plan.initial_capital - 1.0) * 100.0, 4),
            "maxDrawdownAmount": round(max_drawdown, 2),
            "acceptedTrades": accepted,
            "capacityRejectedTrades": rejected,
            "equityCurve": curve,
        }
    return {
        "enabled": True,
        "riskPerTrade": plan.risk_per_trade,
        "maxConcurrentPositions": plan.max_concurrent_positions,
        "onePositionPerEngineSymbol": True,
        "separateEngineLedgers": True,
        "ledgers": ledgers,
    }

