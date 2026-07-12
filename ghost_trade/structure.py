"""Point-in-time confirmed swing and D1 structure calculations."""

from __future__ import annotations

from statistics import fmean
from typing import Sequence

from .market_data import latest_atr
from .models import Candle, StructureDiagnostics, Swing, SwingKind, SwingSet


def confirmed_fractals(
    candles: Sequence[Candle], *, available_through: int | None = None
) -> SwingSet:
    last_available = len(candles) - 1 if available_through is None else min(
        int(available_through), len(candles) - 1
    )
    highs: list[Swing] = []
    lows: list[Swing] = []
    for center in range(2, len(candles) - 2):
        confirmation = center + 2
        if confirmation > last_available:
            break
        window = candles[center - 2 : center + 3]
        candidate = candles[center]
        other_highs = [bar.high for offset, bar in enumerate(window) if offset != 2]
        other_lows = [bar.low for offset, bar in enumerate(window) if offset != 2]
        if all(candidate.high > value for value in other_highs):
            highs.append(
                Swing(
                    kind=SwingKind.HIGH,
                    center_index=center,
                    confirmation_index=confirmation,
                    price=float(candidate.high),
                    center_time=candidate.open_time,
                    confirmation_time=candles[confirmation].close_time,
                )
            )
        if all(candidate.low < value for value in other_lows):
            lows.append(
                Swing(
                    kind=SwingKind.LOW,
                    center_index=center,
                    confirmation_index=confirmation,
                    price=float(candidate.low),
                    center_time=candidate.open_time,
                    confirmation_time=candles[confirmation].close_time,
                )
            )
    return SwingSet(tuple(highs), tuple(lows))


def structure_from_swings(
    highs: Sequence[Swing],
    lows: Sequence[Swing],
    *,
    atr_value: float,
    current_index: int,
) -> StructureDiagnostics:
    recent_highs = tuple(sorted(highs, key=lambda item: item.confirmation_index)[-3:])
    recent_lows = tuple(sorted(lows, key=lambda item: item.confirmation_index)[-3:])
    if len(recent_highs) < 3 or len(recent_lows) < 3 or atr_value <= 0:
        return StructureDiagnostics(
            direction=0.0,
            strength=0.0,
            last_swing_highs=tuple(item.price for item in recent_highs),
            last_swing_lows=tuple(item.price for item in recent_lows),
            bars_since_structure_event=None,
            ordered_fraction=0.0,
            displacement_atr=0.0,
        )

    high_prices = tuple(float(item.price) for item in recent_highs)
    low_prices = tuple(float(item.price) for item in recent_lows)
    bullish_comparisons = sum(a < b for a, b in zip(high_prices, high_prices[1:])) + sum(
        a < b for a, b in zip(low_prices, low_prices[1:])
    )
    bearish_comparisons = sum(a > b for a, b in zip(high_prices, high_prices[1:])) + sum(
        a > b for a, b in zip(low_prices, low_prices[1:])
    )
    if bullish_comparisons == 4:
        direction = 1.0
        ordered = 1.0
    elif bearish_comparisons == 4:
        direction = -1.0
        ordered = 1.0
    else:
        direction = 0.0
        ordered = max(bullish_comparisons, bearish_comparisons) / 4.0

    displacements = [abs(b - a) for a, b in zip(high_prices, high_prices[1:])]
    displacements.extend(abs(b - a) for a, b in zip(low_prices, low_prices[1:]))
    displacement_atr = fmean(displacements) / atr_value
    displacement_score = min(1.0, displacement_atr / 2.0)
    latest_confirmation = max(recent_highs[-1].confirmation_index, recent_lows[-1].confirmation_index)
    bars_since = max(0, int(current_index) - latest_confirmation)
    freshness = max(0.0, 1.0 - bars_since / 20.0)
    strength = min(1.0, max(0.0, 0.50 * ordered + 0.30 * displacement_score + 0.20 * freshness))
    return StructureDiagnostics(
        direction=direction,
        strength=strength,
        last_swing_highs=high_prices,
        last_swing_lows=low_prices,
        bars_since_structure_event=bars_since,
        ordered_fraction=ordered,
        displacement_atr=displacement_atr,
    )


def d1_structure(candles: Sequence[Candle]) -> StructureDiagnostics:
    swings = confirmed_fractals(candles)
    atr_value = latest_atr(candles, 14) or 0.0
    return structure_from_swings(
        swings.highs,
        swings.lows,
        atr_value=atr_value,
        current_index=max(0, len(candles) - 1),
    )
