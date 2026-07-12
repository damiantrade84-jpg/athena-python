"""Contextual, bounded H1 exhaustion diagnostics."""

from __future__ import annotations

from statistics import median
from typing import Sequence

from .models import Candle, Direction, ExhaustionDiagnostics


def _relative_volumes(candles: Sequence[Candle]) -> list[float] | None:
    if len(candles) < 20 or any(item.volume is None or item.volume <= 0 for item in candles):
        return None
    values: list[float] = []
    for index in range(len(candles) - 3, len(candles)):
        start = max(0, index - 19)
        benchmark = median(float(item.volume or 0.0) for item in candles[start : index + 1])
        if benchmark <= 0:
            return None
        values.append(float(candles[index].volume or 0.0) / benchmark)
    return values


def contextual_exhaustion(
    direction: Direction,
    candles: Sequence[Candle],
    *,
    target: float,
    h1_atr: float,
    volume_available: bool,
) -> ExhaustionDiagnostics:
    if not candles or h1_atr <= 0 or direction not in {Direction.LONG, Direction.SHORT}:
        return ExhaustionDiagnostics(0.0, False, False)
    current = candles[-1]
    candle_range = current.high - current.low
    adverse_wick = False
    if candle_range > 0:
        close_position = (current.close - current.low) / candle_range
        if direction is Direction.LONG:
            wick = current.high - max(current.open, current.close)
            near_target = abs(target - current.high) <= 1.25 * h1_atr
            adverse_wick = wick > h1_atr and close_position <= 0.35 and near_target
        else:
            wick = min(current.open, current.close) - current.low
            near_target = abs(current.low - target) <= 1.25 * h1_atr
            adverse_wick = wick > h1_atr and close_position >= 0.65 and near_target

    declining = False
    if volume_available and len(candles) >= 5:
        if direction is Direction.LONG:
            new_extreme = current.high > max(item.high for item in candles[-5:-1])
        else:
            new_extreme = current.low < min(item.low for item in candles[-5:-1])
        relatives = _relative_volumes(candles)
        declining = bool(
            new_extreme
            and relatives is not None
            and relatives[0] > relatives[1] > relatives[2]
        )
    penalty = min(0.30, (0.15 if adverse_wick else 0.0) + (0.15 if declining else 0.0))
    return ExhaustionDiagnostics(penalty, adverse_wick, declining)
