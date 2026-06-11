"""Versioned round-trip cost model for ASE labels (§1.3)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

ModelFamily = Literal["forex", "crypto", "commodity", "equity", "index_etf"]
ForexSubclass = Literal["major", "cross_em"]
CryptoSubclass = Literal["major", "alt"]
EquitySubclass = Literal["us", "jse"]


@dataclass(frozen=True)
class CostModel:
    version: str
    spread_bps: float
    commission_bps: float
    slippage_frac_of_range: float
    swap_bps_per_day: float

    def round_trip_cost_bps(self) -> float:
        return self.spread_bps + self.commission_bps


COST_MODEL_V0 = CostModel(
    version="cm-2026.06.0",
    spread_bps=0.0,
    commission_bps=0.0,
    slippage_frac_of_range=0.0,
    swap_bps_per_day=0.0,
)

# Deliberately conservative defaults — replace with broker-measured values in Phase 0 T0.4.
_V0_TABLE: dict[tuple[str, str], CostModel] = {
    ("forex", "major"): CostModel("cm-2026.06.0", 1.2, 0.6, 0.05, 0.0),
    ("forex", "cross_em"): CostModel("cm-2026.06.0", 3.5, 0.6, 0.08, 0.0),
    ("crypto", "major"): CostModel("cm-2026.06.0", 2.0, 7.0, 0.05, 0.0),
    ("crypto", "alt"): CostModel("cm-2026.06.0", 6.0, 7.0, 0.10, 0.0),
    ("commodity", "cfd"): CostModel("cm-2026.06.0", 3.0, 0.0, 0.08, 0.0),
    ("equity", "us"): CostModel("cm-2026.06.0", 2.5, 1.0, 0.05, 0.0),
    ("equity", "jse"): CostModel("cm-2026.06.0", 8.0, 5.0, 0.10, 0.0),
    ("index_etf", "default"): CostModel("cm-2026.06.0", 1.5, 0.5, 0.05, 0.0),
}


def cost_model_for_instrument(
    family: ModelFamily,
    *,
    subclass: str = "default",
    swap_bps_per_day: float | None = None,
) -> CostModel:
    """Resolve v0 cost model; swap may be overridden from carry/funding feed at label time."""
    key = (family, subclass)
    if key not in _V0_TABLE:
        key = (family, "default")
    if key not in _V0_TABLE:
        raise KeyError(f"no cost model for family={family!r} subclass={subclass!r}")
    base = _V0_TABLE[key]
    if swap_bps_per_day is None:
        return base
    return CostModel(
        version=base.version,
        spread_bps=base.spread_bps,
        commission_bps=base.commission_bps,
        slippage_frac_of_range=base.slippage_frac_of_range,
        swap_bps_per_day=swap_bps_per_day,
    )


def cost_r_units(
    cost_model: CostModel,
    *,
    k_sl: float,
    sigma_h_bps: float,
    bar_range_frac: float = 0.0,
    hold_days: float = 0.0,
    carry_sign: int = 0,
) -> float:
    """Round-trip cost expressed in R-units at label time."""
    if sigma_h_bps <= 0 or k_sl <= 0:
        raise ValueError("sigma_h_bps and k_sl must be positive")
    bps = cost_model.round_trip_cost_bps()
    bps += 2.0 * cost_model.slippage_frac_of_range * bar_range_frac * sigma_h_bps
    if hold_days and carry_sign:
        bps += abs(cost_model.swap_bps_per_day) * hold_days
    return bps / (k_sl * sigma_h_bps)
