from __future__ import annotations

from dataclasses import dataclass

from engine_a_v3.contract import PriceZone, Target
from engine_a_v3.setups import atr_for_levels


@dataclass(frozen=True)
class StructuralLevels:
    entry_zone: PriceZone
    invalidation: float
    targets: tuple[Target, ...]
    price: float


def build_structural_levels(
    primary: list[dict],
    *,
    direction: str,
) -> StructuralLevels | None:
    if len(primary) < 20 or direction not in {"LONG", "SHORT"}:
        return None
    current = float(primary[-1]["close"])
    atr = atr_for_levels(primary)
    if current <= 0 or atr <= 0:
        return None
    recent = primary[-20:]
    if direction == "LONG":
        structural = min(float(candle["low"]) for candle in recent)
        invalidation = min(structural, current - 0.8 * atr)
        if invalidation >= current:
            return None
        risk = current - invalidation
        targets = (
            Target("TP1", current + risk, 1.0),
            Target("TP2", current + 2.0 * risk, 2.0),
        )
    else:
        structural = max(float(candle["high"]) for candle in recent)
        invalidation = max(structural, current + 0.8 * atr)
        if invalidation <= current:
            return None
        risk = invalidation - current
        targets = (
            Target("TP1", current - risk, 1.0),
            Target("TP2", current - 2.0 * risk, 2.0),
        )
    return StructuralLevels(
        entry_zone=PriceZone(current - 0.10 * atr, current + 0.10 * atr),
        invalidation=invalidation,
        targets=targets,
        price=current,
    )
