from __future__ import annotations

from dataclasses import dataclass

from engine_a_v3.contract import PriceZone, Target
from engine_a_v3.session_forex import asian_session_range, parse_utc
from engine_a_v3.setups import _ema, atr_for_levels


def _resolve_tp2_rr(atr_pct: float | None) -> float:
    """Map ATR percentile to an adaptive TP2 reward multiple (config-gated)."""
    try:
        from config import CONFIG

        cfg = CONFIG.get("ENGINE_A_V3_VOLATILITY_GATING") or {}
        if not cfg.get("ENABLED", True):
            return 2.0
        tp2_min = float(cfg.get("TP2_R_MIN", 1.5))
        tp2_max = float(cfg.get("TP2_R_MAX", 2.5))
        atr_low = float(cfg.get("ATR_PCT_LOW", 0.3))
        atr_high = float(cfg.get("ATR_PCT_HIGH", 0.9))
    except Exception:
        return 2.0
    if atr_pct is None:
        return 2.0
    if atr_high <= atr_low:
        return 2.0
    frac = (float(atr_pct) - atr_low) / (atr_high - atr_low)
    frac = max(0.0, min(1.0, frac))
    return tp2_min + frac * (tp2_max - tp2_min)


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
    atr_pct: float | None = None,
    atr_period: int = 14,
) -> StructuralLevels | None:
    if len(primary) < 20 or direction not in {"LONG", "SHORT"}:
        return None
    current = float(primary[-1]["close"])
    atr = atr_for_levels(primary, atr_period)
    if current <= 0 or atr <= 0:
        return None
    floor_mult = 0.35
    try:
        from config import CONFIG

        if bool(CONFIG.get("ENGINE_A_STRUCTURAL_SL_FLOOR_ATR", True)):
            floor_mult = float(CONFIG.get("ENGINE_A_V3_STRUCTURAL_SL_FLOOR_ATR_MULT", 0.35) or 0.35)
        else:
            floor_mult = 0.0
    except Exception:
        floor_mult = 0.35
    recent = primary[-20:]
    if direction == "LONG":
        structural = min(float(candle["low"]) for candle in recent)
        invalidation = min(structural, current - 0.8 * atr)
        if floor_mult > 0:
            floor_stop = current - floor_mult * atr
            invalidation = min(invalidation, floor_stop)
        if invalidation >= current:
            return None
        risk = current - invalidation
        tp2_rr = _resolve_tp2_rr(atr_pct)
        targets = (
            Target("TP1", current + risk, 1.0),
            Target("TP2", current + tp2_rr * risk, round(tp2_rr, 4)),
        )
    else:
        structural = max(float(candle["high"]) for candle in recent)
        invalidation = max(structural, current + 0.8 * atr)
        if floor_mult > 0:
            floor_stop = current + floor_mult * atr
            invalidation = max(invalidation, floor_stop)
        if invalidation <= current:
            return None
        risk = invalidation - current
        tp2_rr = _resolve_tp2_rr(atr_pct)
        targets = (
            Target("TP1", current - risk, 1.0),
            Target("TP2", current - tp2_rr * risk, round(tp2_rr, 4)),
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
    atr_period: int = 14,
    ema_period: int = 20,
) -> StructuralLevels | None:
    """Range-fade levels: stop just beyond the LOCAL swing being faded, target back
    at the EMA mean (TP2). Opposite geometry to build_structural_levels — reward
    shrinks toward the mean. Returns None if the resulting reward:risk to the mean
    is below min_rr (degenerate fade — skip rather than take poor geometry).
    """
    if len(primary) < 20 or direction not in {"LONG", "SHORT"}:
        return None
    closes = [float(candle["close"]) for candle in primary]
    current = closes[-1]
    atr = atr_for_levels(primary, atr_period)
    if current <= 0 or atr <= 0:
        return None
    mean = _ema(closes, ema_period)[-1]
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
