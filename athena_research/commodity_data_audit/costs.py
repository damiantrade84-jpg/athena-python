from __future__ import annotations

from dataclasses import dataclass

STRESS_MULTIPLIERS: tuple[float, ...] = (1.0, 1.5, 2.0)

# Documented in config.yaml NAKED_ENGINE.MAX_SPREAD_POINTS_OVERRIDES (research fallback).
_DOCUMENTED_SPREAD_OVERRIDES: dict[str, float] = {
    "XAU/USD": 60.0,
    "XAG/USD": 80.0,
    "Nat Gas": 40.0,
    "WTI Oil": 8.0,
    "Brent Oil": 10.0,
    "Copper": 20.0,
}
_DEFAULT_COMMODITY_SPREAD = 30.0


@dataclass(frozen=True)
class ModelledCostAssumptions:
    spread_points: float
    slippage_points: float
    commission_bps: float
    swap_bps_per_day: float
    stress_multipliers: tuple[float, ...]
    notes: str


def _config_spread_override(display: str) -> float | None:
    try:
        from config import CONFIG

        naked = CONFIG.get("NAKED_ENGINE", {}) or {}
        overrides = naked.get("MAX_SPREAD_POINTS_OVERRIDES", {}) or {}
        if display in overrides:
            return float(overrides[display])
        commodity_default = (naked.get("MAX_SPREAD_POINTS", {}) or {}).get("commodity")
        if commodity_default is not None:
            return float(commodity_default)
    except Exception:
        return None
    return None


def modelled_costs_for_instrument(display: str) -> ModelledCostAssumptions:
    spread = _config_spread_override(display)
    if spread is None:
        spread = _DOCUMENTED_SPREAD_OVERRIDES.get(display, _DEFAULT_COMMODITY_SPREAD)
    return ModelledCostAssumptions(
        spread_points=spread,
        slippage_points=max(1.0, spread * 0.25),
        commission_bps=0.0,
        swap_bps_per_day=0.5,
        stress_multipliers=STRESS_MULTIPLIERS,
        notes=(
            "Historical bid/ask spread series not stored in MT5/EODHD candle fetches; "
            "costs are conservative model assumptions, not broker-realistic backtests."
        ),
    )


def stress_spread_points(base_spread: float, multiplier: float) -> float:
    return round(base_spread * multiplier, 4)
