"""Robust point-in-time H4 momentum diagnostics."""

from __future__ import annotations

import math
from statistics import fmean, median
from typing import Sequence

from .market_data import atr_series
from .models import Candle, MomentumDiagnostics


_EPSILON = 1e-12


def _mad(values: Sequence[float]) -> float:
    if not values:
        return 0.0
    center = median(values)
    return median(abs(value - center) for value in values)


def _normalize(value: float, history: Sequence[float]) -> float:
    return math.tanh(value / max(_mad(history), _EPSILON))


def _pearson(left: Sequence[float], right: Sequence[float]) -> float | None:
    if len(left) != len(right) or len(left) < 3:
        return None
    mean_left, mean_right = fmean(left), fmean(right)
    numerator = sum((x - mean_left) * (y - mean_right) for x, y in zip(left, right))
    left_var = sum((x - mean_left) ** 2 for x in left)
    right_var = sum((y - mean_right) ** 2 for y in right)
    denominator = math.sqrt(left_var * right_var)
    return numerator / denominator if denominator > _EPSILON else None


def _volume_quality(mode: str) -> float:
    normalized = str(mode or "").strip().lower()
    if normalized in {"exchange", "exchange_volume"}:
        return 1.0
    if normalized in {"tick", "tick_volume", "spot_fx"}:
        return 0.5
    if normalized in {"broker", "broker_or_exchange"}:
        return 0.75
    return 0.0


def momentum_diagnostics(
    candles: Sequence[Candle], *, volume_mode: str = "exchange_volume"
) -> MomentumDiagnostics:
    if len(candles) < 11:
        return MomentumDiagnostics(0.0, 0.0, None, False, False, 0.0, None)

    roc_by_index: dict[int, float] = {}
    for index in range(10, len(candles)):
        anchor = candles[index - 10].close
        if abs(anchor) > _EPSILON:
            roc_by_index[index] = (candles[index].close - anchor) / anchor
    roc_history = list(roc_by_index.values())[-50:]
    current_roc = roc_by_index.get(len(candles) - 1)
    if current_roc is None:
        return MomentumDiagnostics(0.0, 0.0, None, False, False, 0.0, None)
    normalized_roc = _normalize(current_roc, roc_history)

    volume_quality = _volume_quality(volume_mode)
    pressure_by_index: dict[int, float] = {}
    atr_values = atr_series(candles, 14)
    usable_volume = volume_quality > 0 and all(
        candle.volume is not None and float(candle.volume) > 0 for candle in candles[-20:]
    )
    if usable_volume:
        raw_pressure: list[float | None] = [None] * len(candles)
        for index, candle in enumerate(candles):
            atr_value = atr_values[index]
            if atr_value is None or atr_value <= _EPSILON or index < 19:
                continue
            volumes = [float(item.volume or 0.0) for item in candles[index - 19 : index + 1]]
            median_volume = median(volumes)
            if median_volume <= _EPSILON:
                continue
            body_atr = (candle.close - candle.open) / atr_value
            raw_pressure[index] = body_atr * (float(candle.volume or 0.0) / median_volume)
        for index in range(4, len(candles)):
            window = raw_pressure[index - 4 : index + 1]
            if all(value is not None for value in window):
                pressure_by_index[index] = fmean(float(value) for value in window if value is not None)

    current_pressure = pressure_by_index.get(len(candles) - 1)
    pressure_available = current_pressure is not None
    normalized_pressure = None
    if pressure_available:
        pressure_history = list(pressure_by_index.values())[-50:]
        normalized_pressure = _normalize(float(current_pressure), pressure_history)
        direction = (normalized_roc + normalized_pressure) / 2.0
    else:
        direction = normalized_roc

    shared_indices = sorted(set(roc_by_index).intersection(pressure_by_index))[-50:]
    correlation = _pearson(
        [roc_by_index[index] for index in shared_indices],
        [pressure_by_index[index] for index in shared_indices],
    )
    return MomentumDiagnostics(
        direction=max(-1.0, min(1.0, direction)),
        normalized_roc=normalized_roc,
        normalized_pressure=normalized_pressure,
        roc_available=True,
        pressure_available=pressure_available,
        volume_quality=volume_quality if pressure_available else 0.0,
        correlation=correlation,
    )
