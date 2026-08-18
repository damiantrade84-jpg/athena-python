"""Position sizing.

Size scales with the calibrated edge via fractional Kelly, and is then capped
hard. Full Kelly is not a target: it maximises long-run growth only if the
probability estimate is exactly right, and it produces drawdowns that no
discretionary operator tolerates when it is slightly wrong. Since the
probability here comes from a shrunk logistic fitted on a few hundred trades,
"slightly wrong" is the base case.

The cap binds, not Kelly. Kelly decides the shape of the allocation across
signals; `risk_pct_max` decides the worst case on any one of them.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

import opus.config as config
from opus.types import Levels


@dataclass
class SizeResult:
    units: float
    risk_pct: float
    risk_amount: float
    kelly_fraction: float
    capped_by: str
    contract_size: float = 1.0

    def as_dict(self) -> dict:
        return {
            "units": round(float(self.units), 6),
            "riskPct": round(float(self.risk_pct), 4),
            "riskAmount": round(float(self.risk_amount), 2),
            "kellyFraction": round(float(self.kelly_fraction), 5),
            "cappedBy": self.capped_by,
            "contractSize": float(self.contract_size),
        }


def size_position(
    *,
    levels: Levels,
    kelly_fraction: float,
    equity: float | None = None,
    contract_size: float = 1.0,
    min_units: float = 0.0,
    unit_step: float = 0.0,
) -> SizeResult:
    """Units to trade, given the geometry and the edge.

    `kelly_fraction` is the FULL Kelly fraction from the expectancy model; the
    configured `kelly_fraction` multiplier is applied here.
    """
    risk_cfg = config.load()["RISK"]
    equity = float(equity if equity is not None else risk_cfg["default_equity"])
    stop_distance = float(levels.risk_per_unit)

    if equity <= 0 or stop_distance <= 0 or not np.isfinite(stop_distance):
        return SizeResult(0.0, 0.0, 0.0, 0.0, "invalid_inputs", contract_size)

    full_kelly = max(float(kelly_fraction), 0.0)
    if full_kelly <= 0:
        return SizeResult(0.0, 0.0, 0.0, 0.0, "no_edge", contract_size)

    scaled = full_kelly * float(risk_cfg["kelly_fraction"])
    base = float(risk_cfg["risk_pct_base"]) / 100.0
    cap = float(risk_cfg["risk_pct_max"]) / 100.0

    # Kelly sets the shape; the base risk anchors the scale so a marginal edge
    # does not produce an absurdly small unsize-able position.
    risk_fraction = min(max(scaled, base * 0.25), cap)
    capped_by = "kelly"
    if risk_fraction >= cap:
        capped_by = "risk_pct_max"
    elif scaled < base * 0.25:
        capped_by = "risk_floor"

    risk_amount = equity * risk_fraction
    denom = stop_distance * max(float(contract_size), 1e-12)
    units = risk_amount / denom

    if unit_step > 0:
        units = float(np.floor(units / unit_step) * unit_step)
    if min_units > 0 and 0 < units < min_units:
        # Rounding down below the venue minimum means the position cannot be
        # expressed. Report zero rather than silently rounding UP and taking
        # more risk than the model authorised.
        return SizeResult(
            0.0, 0.0, 0.0, full_kelly, "below_min_units", contract_size
        )

    realised_risk = units * denom
    return SizeResult(
        units=float(max(units, 0.0)),
        risk_pct=float(realised_risk / equity * 100.0) if equity > 0 else 0.0,
        risk_amount=float(realised_risk),
        kelly_fraction=float(full_kelly),
        capped_by=capped_by,
        contract_size=float(contract_size),
    )


def correlation_group_of(symbol: str) -> str | None:
    groups = config.load()["RISK"].get("correlation_groups") or {}
    for name, members in groups.items():
        if symbol in (members or []):
            return str(name)
    return None


__all__ = ["SizeResult", "size_position", "correlation_group_of"]
