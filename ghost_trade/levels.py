"""Confirmed structural stop and target geometry."""

from __future__ import annotations

import math
from typing import Sequence

from .models import Direction, GeometryResult, Swing, SwingKind, TradeGeometry


def latest_impulse_pullback(
    direction: Direction,
    current_price: float,
    swings: Sequence[Swing],
) -> float:
    ordered = sorted(swings, key=lambda item: item.confirmation_index)
    if direction is Direction.LONG:
        for end_index in range(len(ordered) - 1, -1, -1):
            end = ordered[end_index]
            if end.kind is not SwingKind.HIGH:
                continue
            starts = [item for item in ordered[:end_index] if item.kind is SwingKind.LOW and item.price < end.price]
            if not starts:
                continue
            start = starts[-1]
            impulse = end.price - start.price
            return max(0.0, (end.price - current_price) / impulse) if impulse > 0 else 0.0
    elif direction is Direction.SHORT:
        for end_index in range(len(ordered) - 1, -1, -1):
            end = ordered[end_index]
            if end.kind is not SwingKind.LOW:
                continue
            starts = [item for item in ordered[:end_index] if item.kind is SwingKind.HIGH and item.price > end.price]
            if not starts:
                continue
            start = starts[-1]
            impulse = start.price - end.price
            return max(0.0, (current_price - end.price) / impulse) if impulse > 0 else 0.0
    return 0.0


def structural_trigger(
    direction: Direction,
    candles,
    minor_swings: Sequence[Swing],
) -> float:
    if not candles or direction not in {Direction.LONG, Direction.SHORT}:
        return 0.0
    current = candles[-1]
    ordered = sorted(minor_swings, key=lambda item: item.confirmation_index)
    highs = [item for item in ordered if item.kind is SwingKind.HIGH]
    lows = [item for item in ordered if item.kind is SwingKind.LOW]
    candle_range = current.high - current.low
    if direction is Direction.LONG:
        swept = bool(lows and current.low < lows[-1].price < current.close)
        broke = bool(highs and current.close > highs[-1].price)
        if swept or broke:
            return 1.0
        lower_wick = min(current.open, current.close) - current.low
        close_position = (current.close - current.low) / candle_range if candle_range > 0 else 0.0
        return 0.5 if lower_wick > abs(current.close - current.open) and close_position >= 0.65 else 0.0
    swept = bool(highs and current.high > highs[-1].price > current.close)
    broke = bool(lows and current.close < lows[-1].price)
    if swept or broke:
        return 1.0
    upper_wick = current.high - max(current.open, current.close)
    close_position = (current.close - current.low) / candle_range if candle_range > 0 else 1.0
    return 0.5 if upper_wick > abs(current.close - current.open) and close_position <= 0.35 else 0.0


def structural_geometry(
    direction: Direction,
    entry: float,
    h1_swings: Sequence[Swing],
    h4_swings: Sequence[Swing],
    *,
    h1_atr: float,
) -> GeometryResult:
    if direction not in {Direction.LONG, Direction.SHORT}:
        return GeometryResult(None, ("direction_not_tradeable",))
    if not all(math.isfinite(value) and value > 0 for value in (entry, h1_atr)):
        return GeometryResult(None, ("invalid_entry_or_atr",))

    if direction is Direction.LONG:
        stop_candidates = [item for item in h1_swings if item.kind is SwingKind.LOW and item.price < entry]
        target_candidates = [item for item in h4_swings if item.kind is SwingKind.HIGH and item.price > entry]
    else:
        stop_candidates = [item for item in h1_swings if item.kind is SwingKind.HIGH and item.price > entry]
        target_candidates = [item for item in h4_swings if item.kind is SwingKind.LOW and item.price < entry]

    reasons: list[str] = []
    if not stop_candidates:
        reasons.append("no_structural_stop")
    if not target_candidates:
        reasons.append("no_structural_target")
    if reasons:
        return GeometryResult(None, tuple(reasons))

    stop_swing = max(stop_candidates, key=lambda item: item.confirmation_index)
    target_swing = min(target_candidates, key=lambda item: abs(item.price - entry))
    buffer = 0.10 * h1_atr
    stop = stop_swing.price - buffer if direction is Direction.LONG else stop_swing.price + buffer
    target = float(target_swing.price)
    risk_distance = abs(entry - stop)
    target_distance = abs(target - entry)
    if not all(math.isfinite(value) and value > 0 for value in (risk_distance, target_distance)):
        return GeometryResult(None, ("invalid_structural_distance",))
    if direction is Direction.LONG and not stop < entry < target:
        return GeometryResult(None, ("invalid_level_side",))
    if direction is Direction.SHORT and not target < entry < stop:
        return GeometryResult(None, ("invalid_level_side",))
    return GeometryResult(
        TradeGeometry(
            direction=direction,
            entry=float(entry),
            unbuffered_stop=float(stop_swing.price),
            stop=float(stop),
            target=target,
            risk_distance=float(risk_distance),
            target_distance=float(target_distance),
            raw_rr=float(target_distance / risk_distance),
            stop_swing_confirmation_index=stop_swing.confirmation_index,
            target_swing_confirmation_index=target_swing.confirmation_index,
        )
    )
