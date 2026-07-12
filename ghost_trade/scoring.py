"""Fixed Ghost Trade score composition."""

from __future__ import annotations

import math

from .exhaustion import contextual_exhaustion
from .levels import latest_impulse_pullback, structural_geometry, structural_trigger
from .market_data import CandleBundle, confirmed_only, latest_atr
from .models import (
    ConfirmedScoreResult,
    Direction,
    EntryQualityDiagnostics,
    ExhaustionDiagnostics,
    GeometryResult,
    LiveOverlay,
    StructureDiagnostics,
    VolatilityRegime,
)
from .momentum import momentum_diagnostics
from .structure import confirmed_fractals, d1_structure
from .volatility import volatility_diagnostics


def _clip(value: float, lower: float = 0.0, upper: float = 1.0) -> float:
    return min(upper, max(lower, float(value)))


def direction_confidence(
    structure_direction: float,
    structure_strength: float,
    momentum_direction: float,
) -> float:
    value = 0.55 * structure_direction * _clip(structure_strength) + 0.45 * momentum_direction
    return _clip(value, -1.0, 1.0)


def pullback_score(depth: float) -> float:
    if not math.isfinite(depth) or depth <= 0.0 or depth >= 1.0:
        return 0.0
    if depth < 0.25:
        return _clip(depth / 0.25)
    if depth <= 0.65:
        return 1.0
    return _clip((1.0 - depth) / 0.35)


def entry_quality(
    raw_rr: float,
    target_distance: float,
    h1_atr: float,
    pullback_depth: float,
    trigger_quality: float,
) -> EntryQualityDiagnostics:
    rr = _clip((raw_rr - 1.0) / 2.0) if math.isfinite(raw_rr) else 0.0
    room = _clip((target_distance / h1_atr) / 2.0) if h1_atr > 0 and math.isfinite(target_distance) else 0.0
    pullback = pullback_score(pullback_depth)
    trigger = _clip(trigger_quality)
    score = _clip(0.40 * rr + 0.25 * room + 0.20 * pullback + 0.15 * trigger)
    return EntryQualityDiagnostics(score, rr, room, pullback, trigger)


def confirmed_score(
    confidence: float,
    quality: float,
    volatility_penalty: float,
    exhaustion_penalty: float,
) -> float:
    return _clip(abs(confidence) * _clip(quality) - max(0.0, volatility_penalty) - max(0.0, exhaustion_penalty))


def clamp_live_overlay(
    *, confirmed_score_value: float, requested_adjustment: float
) -> LiveOverlay:
    confirmed = _clip(confirmed_score_value)
    adjustment = _clip(requested_adjustment, -0.10, 0.10)
    return LiveOverlay(confirmed, adjustment, _clip(confirmed + adjustment))


def score_confirmed(
    bundle: CandleBundle,
    *,
    volume_mode: str = "exchange_volume",
    minimum_confirmed_score: float = 0.35,
    minimum_entry_quality: float = 0.60,
    minimum_raw_rr: float = 1.00,
) -> ConfirmedScoreResult:
    d1 = confirmed_only(bundle.d1, as_of=bundle.as_of)
    h4 = confirmed_only(bundle.h4, as_of=bundle.as_of)
    h1 = confirmed_only(bundle.h1, as_of=bundle.as_of)
    reasons: list[str] = []
    if len(d1) < 50:
        reasons.append("insufficient_d1_history")
    if len(h4) < 71:
        reasons.append("insufficient_h4_history")
    if len(h1) < 30:
        reasons.append("insufficient_h1_history")

    structure = d1_structure(d1) if d1 else StructureDiagnostics(0.0, 0.0, (), (), None, 0.0, 0.0)
    momentum = momentum_diagnostics(h4, volume_mode=volume_mode)
    volatility = volatility_diagnostics(h4)
    confidence = direction_confidence(
        structure.direction, structure.strength, momentum.direction
    )
    if confidence >= 0.25:
        direction = Direction.LONG
    elif confidence <= -0.25:
        direction = Direction.SHORT
    else:
        direction = Direction.NEUTRAL
        reasons.append("direction_confidence_below_threshold")

    geometry = GeometryResult(None, ("direction_not_tradeable",))
    quality = EntryQualityDiagnostics(0.0, 0.0, 0.0, 0.0, 0.0)
    exhaustion = ExhaustionDiagnostics(0.0, False, False)
    if direction is not Direction.NEUTRAL and h1 and h4:
        h1_atr = latest_atr(h1, 14) or 0.0
        h1_swing_set = confirmed_fractals(h1)
        h4_swing_set = confirmed_fractals(h4)
        h1_swings = tuple(sorted(h1_swing_set.highs + h1_swing_set.lows, key=lambda item: item.confirmation_index))
        h4_swings = tuple(sorted(h4_swing_set.highs + h4_swing_set.lows, key=lambda item: item.confirmation_index))
        geometry = structural_geometry(
            direction,
            h1[-1].close,
            h1_swings,
            h4_swings,
            h1_atr=h1_atr,
        )
        if geometry.geometry is None:
            reasons.extend(geometry.reasons)
        else:
            pullback_depth = latest_impulse_pullback(direction, h1[-1].close, h1_swings)
            trigger = structural_trigger(direction, h1, h1_swings)
            quality = entry_quality(
                geometry.geometry.raw_rr,
                geometry.geometry.target_distance,
                h1_atr,
                pullback_depth,
                trigger,
            )
            exhaustion = contextual_exhaustion(
                direction,
                h1,
                target=geometry.geometry.target,
                h1_atr=h1_atr,
                volume_available=momentum.pressure_available,
            )

    volatility_penalty = 0.15 if volatility.regime is VolatilityRegime.COMPRESSED else 0.10 if volatility.regime is VolatilityRegime.EXTREME else 0.0
    total_score = confirmed_score(
        confidence, quality.score, volatility_penalty, exhaustion.penalty
    )
    raw_rr = geometry.geometry.raw_rr if geometry.geometry is not None else 0.0
    selected = bool(
        not any(reason.startswith("insufficient_") for reason in reasons)
        and direction is not Direction.NEUTRAL
        and abs(confidence) >= 0.25
        and quality.score >= minimum_entry_quality
        and raw_rr >= minimum_raw_rr
        and total_score >= minimum_confirmed_score
        and geometry.geometry is not None
    )
    if not selected:
        if quality.score < minimum_entry_quality:
            reasons.append("entry_quality_below_threshold")
        if geometry.geometry is not None and raw_rr < minimum_raw_rr:
            reasons.append("raw_rr_below_threshold")
        if total_score < minimum_confirmed_score:
            reasons.append("confirmed_score_below_threshold")
    return ConfirmedScoreResult(
        direction,
        confidence,
        structure,
        momentum,
        volatility,
        geometry,
        quality,
        exhaustion,
        total_score,
        selected,
        tuple(dict.fromkeys(reasons)),
    )
