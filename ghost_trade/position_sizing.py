"""Deterministic fixed-risk sizing for verified demo execution."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

from .models import VolatilityRegime


@dataclass(frozen=True, slots=True)
class SizeResult:
    approved: bool
    quantity: float | None
    risk_cash: float
    loss_per_unit: float | None
    estimated_risk_cash: float | None
    reason: str | None = None


def _positive(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) and result > 0 else None


def _round_down(value: float, step: float) -> float:
    # Correct binary-float noise only when the quotient is effectively integral;
    # the post-round risk check still rejects any true budget overshoot.
    units = math.floor(value / step + 1e-9)
    rounded = units * step
    decimals = max(0, min(12, -int(math.floor(math.log10(step))) + 2)) if step < 1 else 8
    return round(rounded, decimals)


def _reject(risk_cash: float, reason: str) -> SizeResult:
    return SizeResult(False, None, risk_cash, None, None, reason)


def _risk_cash(equity: Any, fraction: Any) -> tuple[float, str | None]:
    equity_value = _positive(equity)
    fraction_value = _positive(fraction)
    if equity_value is None:
        return 0.0, "equity_unavailable"
    if fraction_value is None or fraction_value > 0.005:
        return 0.0, "risk_fraction_out_of_bounds"
    return equity_value * fraction_value, None


def size_mt5(
    *,
    equity: Any,
    risk_fraction: Any,
    entry: Any,
    stop: Any,
    symbol_info: Any,
    account_conversion_rate: float | None = None,
) -> SizeResult:
    risk_cash, error = _risk_cash(equity, risk_fraction)
    if error:
        return _reject(risk_cash, error)
    entry_value, stop_value = _positive(entry), _positive(stop)
    if entry_value is None or stop_value is None or entry_value == stop_value:
        return _reject(risk_cash, "invalid_stop_distance")
    distance = abs(entry_value - stop_value)
    tick_size = _positive(getattr(symbol_info, "trade_tick_size", None))
    tick_value = _positive(getattr(symbol_info, "trade_tick_value", None))
    contract_size = _positive(getattr(symbol_info, "trade_contract_size", None))
    conversion = _positive(account_conversion_rate)
    if tick_size is not None and tick_value is not None:
        loss_per_lot = distance / tick_size * tick_value
    elif contract_size is not None and conversion is not None:
        loss_per_lot = distance * contract_size * conversion
    else:
        return _reject(risk_cash, "tick_value_unavailable")
    step = _positive(getattr(symbol_info, "volume_step", None))
    minimum = _positive(getattr(symbol_info, "volume_min", None))
    maximum = _positive(getattr(symbol_info, "volume_max", None))
    if step is None or minimum is None or maximum is None or minimum > maximum:
        return _reject(risk_cash, "volume_filter_unavailable")
    quantity = _round_down(risk_cash / loss_per_lot, step)
    if quantity < minimum:
        return _reject(risk_cash, "quantity_below_minimum")
    quantity = min(quantity, maximum)
    estimated = quantity * loss_per_lot
    if estimated > risk_cash + max(1e-8, risk_cash * 1e-9):
        return _reject(risk_cash, "rounded_risk_exceeds_budget")
    return SizeResult(True, quantity, risk_cash, loss_per_lot, estimated)


def size_bybit(
    *,
    equity: Any,
    risk_fraction: Any,
    entry: Any,
    stop: Any,
    quantity_step: Any,
    minimum_quantity: Any,
    minimum_notional: Any,
    maximum_quantity: Any | None = None,
    volatility_regime: VolatilityRegime = VolatilityRegime.NORMAL,
) -> SizeResult:
    del volatility_regime  # Diagnostic only; volatility never increases quantity.
    risk_cash, error = _risk_cash(equity, risk_fraction)
    if error:
        return _reject(risk_cash, error)
    entry_value, stop_value = _positive(entry), _positive(stop)
    if entry_value is None or stop_value is None or entry_value == stop_value:
        return _reject(risk_cash, "invalid_stop_distance")
    step = _positive(quantity_step)
    minimum = _positive(minimum_quantity)
    notional_minimum = _positive(minimum_notional)
    maximum = _positive(maximum_quantity) if maximum_quantity is not None else None
    if step is None or minimum is None or notional_minimum is None:
        return _reject(risk_cash, "quantity_filter_unavailable")
    loss_per_unit = abs(entry_value - stop_value)
    quantity = _round_down(risk_cash / loss_per_unit, step)
    if maximum is not None:
        quantity = min(quantity, maximum)
    if quantity < minimum:
        return _reject(risk_cash, "quantity_below_minimum")
    if quantity * entry_value < notional_minimum:
        return _reject(risk_cash, "minimum_notional_not_met")
    estimated = quantity * loss_per_unit
    if estimated > risk_cash + max(1e-8, risk_cash * 1e-9):
        return _reject(risk_cash, "rounded_risk_exceeds_budget")
    return SizeResult(True, quantity, risk_cash, loss_per_unit, estimated)
