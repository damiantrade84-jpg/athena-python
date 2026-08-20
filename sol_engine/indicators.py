"""Native SOL indicators built from normalized closed candles only."""

from __future__ import annotations

import math
import statistics
from typing import Iterable

from .models import Candle


def clamp(value: float, low: float = 0.0, high: float = 1.0) -> float:
    return max(low, min(high, float(value)))


def _median(values: Iterable[float]) -> float:
    rows = [float(value) for value in values if math.isfinite(float(value))]
    return float(statistics.median(rows)) if rows else 0.0


def median_absolute_deviation(values: Iterable[float]) -> float:
    rows = [float(value) for value in values if math.isfinite(float(value))]
    if not rows:
        return 0.0
    center = _median(rows)
    return _median(abs(value - center) for value in rows)


def true_ranges(candles: list[Candle]) -> list[float]:
    if not candles:
        return []
    out: list[float] = []
    previous_close = candles[0].close
    for candle in candles:
        out.append(
            max(
                candle.high - candle.low,
                abs(candle.high - previous_close),
                abs(candle.low - previous_close),
            )
        )
        previous_close = candle.close
    return out


def wilder_atr(candles: list[Candle], period: int = 14) -> float:
    period = max(2, int(period))
    ranges = true_ranges(candles)
    if len(ranges) < period:
        return 0.0
    atr = sum(ranges[:period]) / period
    for value in ranges[period:]:
        atr = ((period - 1) * atr + value) / period
    return float(atr)


def robust_orbit(
    candles: list[Candle],
    *,
    lookback: int = 32,
    slope_window: int = 8,
    atr_period: int = 14,
) -> dict:
    """Robust price location and directional persistence around equilibrium.

    The center is a median typical price and the orbit radius is median absolute
    deviation, floored by ATR.  Direction comes from a two-window robust slope;
    quality combines normalized slope and path efficiency.
    """

    minimum = max(lookback, slope_window * 2 + 2, atr_period + 2)
    if len(candles) < minimum:
        return {"available": False, "reason": "INSUFFICIENT_ORBIT_BARS"}
    sample = candles[-lookback:]
    typical = [candle.typical for candle in sample]
    center = _median(typical)
    atr = wilder_atr(sample, atr_period)
    radius = max(1.4826 * median_absolute_deviation(typical), atr * 0.10, 1e-12)
    z = (sample[-1].close - center) / radius

    left = _median(typical[-2 * slope_window : -slope_window])
    right = _median(typical[-slope_window:])
    slope_atr = (right - left) / max(atr, 1e-12)
    direction = 1 if slope_atr > 0.04 else -1 if slope_atr < -0.04 else 0

    path = sample[-(2 * slope_window + 1) :]
    gross = sum(abs(path[index].close - path[index - 1].close) for index in range(1, len(path)))
    net = abs(path[-1].close - path[0].close)
    efficiency = clamp(net / gross if gross > 0 else 0.0)
    quality = clamp(0.68 * clamp(abs(slope_atr) / 1.0) + 0.32 * efficiency)
    return {
        "available": True,
        "center": center,
        "radius": radius,
        "z": z,
        "slopeAtr": slope_atr,
        "direction": direction,
        "efficiency": efficiency,
        "quality": quality,
        "atr": atr,
    }


def _sweep_candidates(
    candles: list[Candle],
    *,
    atr: float,
    lookback: int,
    recent_bars: int,
    excursion_atr_min: float,
) -> list[dict]:
    results: list[dict] = []
    start = max(lookback, len(candles) - recent_bars)
    for index in range(start, len(candles)):
        candle = candles[index]
        prior = candles[index - lookback : index]
        if len(prior) < lookback or candle.range <= 0:
            continue
        prior_low = min(row.low for row in prior)
        prior_high = max(row.high for row in prior)
        age = len(candles) - 1 - index

        low_excursion = max(0.0, prior_low - candle.low)
        low_wick = candle.lower_wick / candle.range
        if (
            low_excursion / max(atr, 1e-12) >= excursion_atr_min
            and candle.close > prior_low
            and low_wick >= 0.32
        ):
            subsequent = candles[index + 1 :]
            remains_valid = all(row.low >= candle.low - atr * 0.05 for row in subsequent)
            remains_valid = remains_valid and candles[-1].close > prior_low
            if remains_valid:
                reclaim = clamp((candle.close - prior_low) / max(candle.range, 1e-12))
                strength = (
                    0.42 * clamp(low_excursion / max(atr * 0.45, 1e-12))
                    + 0.36 * clamp(low_wick / 0.70)
                    + 0.22 * reclaim
                ) * max(0.70, 1.0 - age * 0.08)
                results.append(
                    {
                        "kind": "SWEEP_RECLAIM",
                        "direction": 1,
                        "strength": clamp(strength),
                        "eventIndex": index,
                        "eventAgeBars": age,
                        "extreme": candle.low,
                        "boundary": prior_low,
                        "excursionAtr": low_excursion / max(atr, 1e-12),
                        "wickFraction": low_wick,
                    }
                )

        high_excursion = max(0.0, candle.high - prior_high)
        high_wick = candle.upper_wick / candle.range
        if (
            high_excursion / max(atr, 1e-12) >= excursion_atr_min
            and candle.close < prior_high
            and high_wick >= 0.32
        ):
            subsequent = candles[index + 1 :]
            remains_valid = all(row.high <= candle.high + atr * 0.05 for row in subsequent)
            remains_valid = remains_valid and candles[-1].close < prior_high
            if remains_valid:
                reclaim = clamp((prior_high - candle.close) / max(candle.range, 1e-12))
                strength = (
                    0.42 * clamp(high_excursion / max(atr * 0.45, 1e-12))
                    + 0.36 * clamp(high_wick / 0.70)
                    + 0.22 * reclaim
                ) * max(0.70, 1.0 - age * 0.08)
                results.append(
                    {
                        "kind": "SWEEP_RECLAIM",
                        "direction": -1,
                        "strength": clamp(strength),
                        "eventIndex": index,
                        "eventAgeBars": age,
                        "extreme": candle.high,
                        "boundary": prior_high,
                        "excursionAtr": high_excursion / max(atr, 1e-12),
                        "wickFraction": high_wick,
                    }
                )
    return results


def _session_raid_candidates(
    candles: list[Candle],
    *,
    atr: float,
    pools: dict,
    lookback: int,
    recent_bars: int,
    excursion_atr_min: float,
) -> list[dict]:
    results: list[dict] = []
    if not pools or atr <= 0 or len(candles) < 6:
        return results
    sample = candles[-max(8, lookback) :]
    pool_lows = [
        value
        for value in (pools.get("asiaLow"), pools.get("priorDayLow"))
        if isinstance(value, (int, float))
    ]
    pool_highs = [
        value
        for value in (pools.get("asiaHigh"), pools.get("priorDayHigh"))
        if isinstance(value, (int, float))
    ]
    for offset, candle in enumerate(sample):
        age = len(sample) - 1 - offset
        if age > max(1, recent_bars):
            continue
        index = len(candles) - len(sample) + offset
        for pool in pool_lows:
            if candle.low < pool <= candle.close:
                excursion = (pool - candle.low) / max(atr, 1e-12)
                if excursion < excursion_atr_min:
                    continue
                reclaim = clamp((candle.close - pool) / max(atr * 0.35, 1e-12))
                recency = clamp(1.0 - age / max(recent_bars, 1))
                strength = clamp(0.48 * clamp(excursion / 0.45) + 0.32 * reclaim + 0.20 * recency)
                results.append(
                    {
                        "kind": "SESSION_SWEEP",
                        "direction": 1,
                        "strength": clamp(strength),
                        "eventIndex": index,
                        "eventAgeBars": age,
                        "extreme": candle.low,
                        "boundary": pool,
                        "excursionAtr": excursion,
                        "pool": pool,
                    }
                )
        for pool in pool_highs:
            if candle.high > pool >= candle.close:
                excursion = (candle.high - pool) / max(atr, 1e-12)
                if excursion < excursion_atr_min:
                    continue
                reclaim = clamp((pool - candle.close) / max(atr * 0.35, 1e-12))
                recency = clamp(1.0 - age / max(recent_bars, 1))
                strength = clamp(0.48 * clamp(excursion / 0.45) + 0.32 * reclaim + 0.20 * recency)
                results.append(
                    {
                        "kind": "SESSION_SWEEP",
                        "direction": -1,
                        "strength": clamp(strength),
                        "eventIndex": index,
                        "eventAgeBars": age,
                        "extreme": candle.high,
                        "boundary": pool,
                        "excursionAtr": excursion,
                        "pool": pool,
                    }
                )
    return results


def _compression_candidates(
    candles: list[Candle],
    *,
    atr: float,
    short_window: int,
    baseline_window: int,
    ratio_max: float,
    recent_bars: int = 8,
) -> list[dict]:
    results: list[dict] = []
    ranges = true_ranges(candles)
    start = max(baseline_window + short_window + 12, len(candles) - max(2, recent_bars))
    for index in range(start, len(candles)):
        candle = candles[index]
        if candle.range <= 0:
            continue
        compressed = ranges[index - short_window : index]
        baseline = ranges[index - short_window - baseline_window : index - short_window]
        if len(compressed) < short_window or len(baseline) < baseline_window:
            continue
        ratio = _median(compressed) / max(_median(baseline), 1e-12)
        if ratio > ratio_max:
            continue
        prior = candles[index - 12 : index]
        prior_high = max(row.high for row in prior)
        prior_low = min(row.low for row in prior)
        body_fraction = candle.body / candle.range
        age = len(candles) - 1 - index
        if candle.close > prior_high and body_fraction >= 0.52:
            breakout = (candle.close - prior_high) / max(atr, 1e-12)
            strength = (
                0.38 * clamp((ratio_max - ratio) / max(ratio_max, 1e-12) + 0.35)
                + 0.37 * clamp(body_fraction / 0.85)
                + 0.25 * clamp(breakout / 0.55)
            ) * max(0.78, 1.0 - age * 0.10)
            results.append(
                {
                    "kind": "COMPRESSION_RELEASE",
                    "direction": 1,
                    "strength": clamp(strength),
                    "eventIndex": index,
                    "eventAgeBars": age,
                    "extreme": min(row.low for row in candles[index - short_window : index + 1]),
                    "boundary": prior_high,
                    "compressionRatio": ratio,
                    "bodyFraction": body_fraction,
                    "breakoutAtr": breakout,
                }
            )
        if candle.close < prior_low and body_fraction >= 0.52:
            breakout = (prior_low - candle.close) / max(atr, 1e-12)
            strength = (
                0.38 * clamp((ratio_max - ratio) / max(ratio_max, 1e-12) + 0.35)
                + 0.37 * clamp(body_fraction / 0.85)
                + 0.25 * clamp(breakout / 0.55)
            ) * max(0.78, 1.0 - age * 0.10)
            results.append(
                {
                    "kind": "COMPRESSION_RELEASE",
                    "direction": -1,
                    "strength": clamp(strength),
                    "eventIndex": index,
                    "eventAgeBars": age,
                    "extreme": max(row.high for row in candles[index - short_window : index + 1]),
                    "boundary": prior_low,
                    "compressionRatio": ratio,
                    "bodyFraction": body_fraction,
                    "breakoutAtr": breakout,
                }
            )
    return results


def liquidity_event(
    candles: list[Candle],
    *,
    atr_period: int = 14,
    lookback: int = 20,
    recent_bars: int = 4,
    excursion_atr_min: float = 0.04,
    compression_short_window: int = 6,
    compression_baseline_window: int = 30,
    compression_ratio_max: float = 0.72,
    session_pools: dict | None = None,
    session_lookback_bars: int = 16,
) -> dict:
    required = max(lookback + recent_bars, compression_short_window + compression_baseline_window + 14)
    if len(candles) < required:
        return {"available": False, "reason": "INSUFFICIENT_LIQUIDITY_BARS"}
    atr = wilder_atr(candles, atr_period)
    if atr <= 0:
        return {"available": False, "reason": "ATR_UNAVAILABLE"}
    candidates = _sweep_candidates(
        candles,
        atr=atr,
        lookback=lookback,
        recent_bars=recent_bars,
        excursion_atr_min=excursion_atr_min,
    )
    candidates.extend(
        _compression_candidates(
            candles,
            atr=atr,
            short_window=compression_short_window,
            baseline_window=compression_baseline_window,
            ratio_max=compression_ratio_max,
            recent_bars=recent_bars,
        )
    )
    candidates.extend(
        _session_raid_candidates(
            candles,
            atr=atr,
            pools=session_pools or {},
            lookback=session_lookback_bars,
            recent_bars=recent_bars,
            excursion_atr_min=excursion_atr_min,
        )
    )
    if not candidates:
        return {"available": True, "kind": "NONE", "direction": 0, "strength": 0.0, "atr": atr}
    winner = max(candidates, key=lambda item: (float(item["strength"]), -int(item["eventAgeBars"])))
    return {"available": True, "atr": atr, **winner}


def displacement_pulse(
    candles: list[Candle],
    *,
    window: int = 3,
    break_lookback: int = 6,
) -> dict:
    required = window + break_lookback + 2
    if len(candles) < required:
        return {"available": False, "reason": "INSUFFICIENT_TRIGGER_BARS"}
    recent = candles[-window:]
    ranges = true_ranges(candles)
    denominator = sum(ranges[-window:])
    net = recent[-1].close - recent[0].open
    efficiency = abs(net) / denominator if denominator > 0 else 0.0
    direction = 1 if net > 0 else -1 if net < 0 else 0
    last = candles[-1]
    body_fraction = last.body / last.range if last.range > 0 else 0.0
    prior = candles[-(break_lookback + 1) : -1]
    micro_break = False
    if direction > 0:
        micro_break = last.close > max(row.high for row in prior)
    elif direction < 0:
        micro_break = last.close < min(row.low for row in prior)
    strength = clamp(
        0.52 * clamp(efficiency / 0.72)
        + 0.30 * clamp(body_fraction / 0.82)
        + 0.18 * float(micro_break)
    )
    return {
        "available": True,
        "direction": direction,
        "strength": strength,
        "efficiency": efficiency,
        "bodyFraction": body_fraction,
        "microBreak": micro_break,
        "netMove": net,
    }


def participation_impulse(
    candles: list[Candle],
    *,
    recent_window: int = 3,
    baseline_window: int = 30,
    scale_floor: float = 0.05,
) -> dict:
    if len(candles) < recent_window + baseline_window:
        return {"available": False, "reason": "INSUFFICIENT_PARTICIPATION_BARS", "strength": 0.0}
    volumes = [candle.volume for candle in candles]
    if any(value is None for value in volumes[-(recent_window + baseline_window) :]):
        return {"available": False, "reason": "PARTICIPATION_UNAVAILABLE", "strength": 0.0}
    rows = [float(value or 0.0) for value in volumes]
    if max(rows[-(recent_window + baseline_window) :], default=0.0) <= 0:
        return {"available": False, "reason": "PARTICIPATION_UNAVAILABLE", "strength": 0.0}
    logs = [math.log1p(value) for value in rows]
    baseline = logs[-(recent_window + baseline_window) : -recent_window]
    recent = logs[-recent_window:]
    center = _median(baseline)
    # A flat baseline has zero MAD. The explicit floor keeps the diagnostic
    # finite and prevents a tiny volume change from presenting as an absurd z.
    scale = max(1.4826 * median_absolute_deviation(baseline), float(scale_floor), 1e-9)
    z = (_median(recent) - center) / scale
    strength = clamp((z + 0.45) / 2.45)
    sources = sorted({str(candle.volume_source) for candle in candles[-recent_window:] if candle.volume_source})
    return {"available": True, "z": z, "strength": strength, "sources": sources}
