"""Candle normalization primitives with explicit confirmed/forming separation."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from statistics import fmean
from typing import Sequence

from .models import Candle


@dataclass(frozen=True, slots=True)
class CandleBundle:
    d1: tuple[Candle, ...]
    h4: tuple[Candle, ...]
    h1: tuple[Candle, ...]
    as_of: datetime
    m15_confirmed: tuple[Candle, ...] = ()
    m15_forming: Candle | None = None
    provider: str = ""


def confirmed_only(candles: Sequence[Candle], *, as_of: datetime) -> tuple[Candle, ...]:
    """Return only bars whose close timestamp existed at ``as_of``."""

    return tuple(candle for candle in candles if candle.close_time <= as_of)


def true_ranges(candles: Sequence[Candle]) -> list[float]:
    ranges: list[float] = []
    previous_close: float | None = None
    for candle in candles:
        if previous_close is None:
            value = candle.high - candle.low
        else:
            value = max(
                candle.high - candle.low,
                abs(candle.high - previous_close),
                abs(candle.low - previous_close),
            )
        ranges.append(max(0.0, float(value)))
        previous_close = candle.close
    return ranges


def atr_series(candles: Sequence[Candle], period: int = 14) -> list[float | None]:
    if period <= 0:
        raise ValueError("ATR period must be positive")
    ranges = true_ranges(candles)
    result: list[float | None] = [None] * len(ranges)
    for index in range(period - 1, len(ranges)):
        result[index] = fmean(ranges[index - period + 1 : index + 1])
    return result


def latest_atr(candles: Sequence[Candle], period: int = 14) -> float | None:
    series = atr_series(candles, period)
    return series[-1] if series else None
