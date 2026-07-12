"""H4 volatility-regime diagnostics."""

from __future__ import annotations

from statistics import fmean, pstdev
from typing import Sequence

from .market_data import atr_series
from .models import Candle, VolatilityDiagnostics, VolatilityRegime


def classify_volatility(
    *,
    atr_percentile: float,
    bb_width_percentile: float,
    prior_bb_width_percentile: float | None = None,
    atr_rising: bool = False,
    bb_width_rising: bool = False,
) -> VolatilityRegime:
    if atr_percentile > 90:
        return VolatilityRegime.EXTREME
    if (
        prior_bb_width_percentile is not None
        and prior_bb_width_percentile < 30
        and atr_rising
        and bb_width_rising
    ):
        return VolatilityRegime.SQUEEZE_RELEASE
    if atr_percentile < 30 and bb_width_percentile < 30:
        return VolatilityRegime.COMPRESSED
    if atr_percentile > 70 and bb_width_percentile > 70:
        return VolatilityRegime.EXPANDING
    return VolatilityRegime.NORMAL


def _percentile(current: float, history: Sequence[float]) -> float:
    if not history:
        return 50.0
    below = sum(value < current for value in history)
    equal = sum(value == current for value in history)
    return 100.0 * (below + 0.5 * equal) / len(history)


def _bb_widths(candles: Sequence[Candle], period: int = 20) -> list[float | None]:
    result: list[float | None] = [None] * len(candles)
    for index in range(period - 1, len(candles)):
        closes = [item.close for item in candles[index - period + 1 : index + 1]]
        middle = fmean(closes)
        if abs(middle) <= 1e-12:
            continue
        result[index] = 4.0 * pstdev(closes) / abs(middle)
    return result


def volatility_diagnostics(candles: Sequence[Candle]) -> VolatilityDiagnostics:
    atr_values = atr_series(candles, 14)
    widths = _bb_widths(candles, 20)
    if len(candles) < 71 or atr_values[-1] is None or widths[-1] is None:
        return VolatilityDiagnostics(
            VolatilityRegime.NORMAL, 50.0, 50.0, None, False, False, False
        )
    current_atr = float(atr_values[-1])
    current_width = float(widths[-1])
    atr_history = [float(value) for value in atr_values[-51:-1] if value is not None]
    width_history = [float(value) for value in widths[-51:-1] if value is not None]
    atr_percentile = _percentile(current_atr, atr_history)
    width_percentile = _percentile(current_width, width_history)
    prior_width = float(widths[-2]) if widths[-2] is not None else current_width
    prior_reference = [float(value) for value in widths[-52:-2] if value is not None]
    prior_width_percentile = _percentile(prior_width, prior_reference)
    previous_atr = float(atr_values[-2]) if atr_values[-2] is not None else current_atr
    atr_rising = current_atr > previous_atr
    width_rising = current_width > prior_width
    regime = classify_volatility(
        atr_percentile=atr_percentile,
        bb_width_percentile=width_percentile,
        prior_bb_width_percentile=prior_width_percentile,
        atr_rising=atr_rising,
        bb_width_rising=width_rising,
    )
    return VolatilityDiagnostics(
        regime,
        atr_percentile,
        width_percentile,
        prior_width_percentile,
        atr_rising,
        width_rising,
        True,
    )
