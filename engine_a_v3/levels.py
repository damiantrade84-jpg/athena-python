from __future__ import annotations

from dataclasses import dataclass

from engine_a_v3.contract import PriceZone, Target
from engine_a_v3.session_forex import asian_session_range, parse_utc
from engine_a_v3.setups import _ema, atr_for_levels


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


def build_mean_reversion_levels(
    primary: list[dict],
    *,
    direction: str,
    swing_lookback: int = 6,
    sl_buffer_atr: float = 0.5,
    min_rr: float = 1.0,
) -> StructuralLevels | None:
    """Range-fade levels: stop just beyond the LOCAL swing being faded, target back
    at the EMA20 mean (TP2). Opposite geometry to build_structural_levels — reward
    shrinks toward the mean. Returns None if the resulting reward:risk to the mean
    is below min_rr (degenerate fade — skip rather than take poor geometry).
    """
    if len(primary) < 20 or direction not in {"LONG", "SHORT"}:
        return None
    closes = [float(candle["close"]) for candle in primary]
    current = closes[-1]
    atr = atr_for_levels(primary)
    if current <= 0 or atr <= 0:
        return None
    mean = _ema(closes, 20)[-1]
    recent = primary[-swing_lookback:]
    if direction == "SHORT":
        extreme = max(float(candle["high"]) for candle in recent)
        invalidation = max(extreme, current) + sl_buffer_atr * atr
        if not (invalidation > current > mean):
            return None
        risk = invalidation - current
        rr2 = (current - mean) / risk
        if rr2 < min_rr:
            return None
        partial = current - 0.5 * (current - mean)
        targets = (
            Target("TP1", partial, round((current - partial) / risk, 4)),
            Target("TP2", mean, round(rr2, 4)),
        )
    else:
        extreme = min(float(candle["low"]) for candle in recent)
        invalidation = min(extreme, current) - sl_buffer_atr * atr
        if not (invalidation < current < mean):
            return None
        risk = current - invalidation
        rr2 = (mean - current) / risk
        if rr2 < min_rr:
            return None
        partial = current + 0.5 * (mean - current)
        targets = (
            Target("TP1", partial, round((partial - current) / risk, 4)),
            Target("TP2", mean, round(rr2, 4)),
        )
    return StructuralLevels(
        entry_zone=PriceZone(current - 0.10 * atr, current + 0.10 * atr),
        invalidation=invalidation,
        targets=targets,
        price=current,
    )


def build_london_open_breakout_levels(
    primary: list[dict],
    *,
    direction: str,
) -> StructuralLevels | None:
    """Intraday London-open breakout: SL/TP sized from Asian session range."""
    if len(primary) < 20 or direction not in {"LONG", "SHORT"}:
        return None
    current = float(primary[-1]["close"])
    as_of = parse_utc(primary[-1].get("time") or primary[-1].get("datetime"))
    if as_of is None:
        return None
    asian = asian_session_range(primary, as_of=as_of)
    if asian is None:
        return None
    asian_range = max(asian.high - asian.low, 1e-9)
    if direction == "LONG":
        if current <= asian.high:
            return None
        invalidation = current - 0.5 * asian_range
        if invalidation >= current:
            return None
        risk = current - invalidation
        targets = (
            Target("TP1", current + 0.8 * asian_range, 0.8 * asian_range / risk),
            Target("TP2", current + 1.2 * asian_range, 1.2 * asian_range / risk),
        )
    else:
        if current >= asian.low:
            return None
        invalidation = current + 0.5 * asian_range
        if invalidation <= current:
            return None
        risk = invalidation - current
        targets = (
            Target("TP1", current - 0.8 * asian_range, 0.8 * asian_range / risk),
            Target("TP2", current - 1.2 * asian_range, 1.2 * asian_range / risk),
        )
    return StructuralLevels(
        entry_zone=PriceZone(current - 0.05 * asian_range, current + 0.05 * asian_range),
        invalidation=invalidation,
        targets=targets,
        price=current,
    )
