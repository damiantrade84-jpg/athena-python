"""Pure indicator primitives for OX Alpha.

Every function is side-effect free and operates on plain lists of candle dicts
(``time/open/high/low/close/volume``) or float series, so the whole scoring
stack is unit-testable without feeds, config, or brokers.
"""
from __future__ import annotations

import math
from typing import Any, Iterable, Sequence

Candle = dict[str, Any]


def _f(value: Any) -> float | None:
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(out):
        return None
    return out


def closes(candles: Sequence[Candle]) -> list[float]:
    out = []
    for c in candles:
        v = _f(c.get("close"))
        if v is not None:
            out.append(v)
    return out


def true_ranges(candles: Sequence[Candle]) -> list[float]:
    """True range per bar; first bar falls back to high-low."""
    out: list[float] = []
    prev_close: float | None = None
    for c in candles:
        h = _f(c.get("high"))
        l = _f(c.get("low"))
        cl = _f(c.get("close"))
        if h is None or l is None or cl is None:
            prev_close = None
            continue
        if prev_close is None:
            out.append(max(0.0, h - l))
        else:
            out.append(max(h - l, abs(h - prev_close), abs(l - prev_close)))
        prev_close = cl
    return out


def wilder_atr(candles: Sequence[Candle], period: int = 14) -> float | None:
    trs = true_ranges(candles)
    if len(trs) < period or period <= 0:
        return None
    atr = sum(trs[:period]) / period
    for tr in trs[period:]:
        atr = (atr * (period - 1) + tr) / period
    return atr


def roc(series: Sequence[float], period: int) -> float | None:
    """Rate of change over ``period`` steps, as a signed fraction."""
    if period <= 0 or len(series) <= period:
        return None
    start = series[-period - 1]
    end = series[-1]
    if start == 0 or not math.isfinite(start) or not math.isfinite(end):
        return None
    return (end - start) / abs(start)


def stdev(series: Sequence[float]) -> float | None:
    n = len(series)
    if n < 2:
        return None
    mean = sum(series) / n
    var = sum((x - mean) ** 2 for x in series) / (n - 1)
    return math.sqrt(var)


def zscore(series: Sequence[float], lookback: int) -> float | None:
    """Z-score of the last value against the trailing ``lookback`` window."""
    if lookback < 2 or len(series) < lookback:
        return None
    window = list(series[-lookback:])
    sd = stdev(window)
    if sd is None or sd <= 0:
        return None
    mean = sum(window) / len(window)
    return (window[-1] - mean) / sd


def percentile_rank(series: Sequence[float], value: float) -> float | None:
    """Percent of historical values at or below ``value`` (0..1)."""
    vals = [v for v in series if math.isfinite(v)]
    if not vals:
        return None
    below = sum(1 for v in vals if v <= value)
    return below / len(vals)


def path_efficiency(candles: Sequence[Candle], lookback: int) -> float | None:
    """Net displacement / total traveled range over the last ``lookback`` bars.

    ~+1 clean directional drive, ~0 choppy two-way traffic. Uses close-to-close
    legs so it measures how much of the gross movement actually went somewhere.
    """
    cl = closes(candles)
    if lookback < 2 or len(cl) < lookback + 1:
        return None
    legs = [cl[i] - cl[i - 1] for i in range(len(cl) - lookback, len(cl))]
    traveled = sum(abs(x) for x in legs)
    net = cl[-1] - cl[-lookback - 1]
    if traveled <= 0:
        return None
    return max(-1.0, min(1.0, net / traveled))


def swing_pivots(
    candles: Sequence[Candle], left: int = 2, right: int = 2
) -> tuple[list[tuple[int, float]], list[tuple[int, float]]]:
    """Confirmed fractal pivots. Returns (highs, lows) as (index, price)."""
    highs: list[tuple[int, float]] = []
    lows: list[tuple[int, float]] = []
    n = len(candles)
    for i in range(left, n - right):
        h = _f(candles[i].get("high"))
        l = _f(candles[i].get("low"))
        if h is None or l is None:
            continue
        window_h = all(
            (_f(candles[j].get("high")) or -math.inf) < h
            for j in range(i - left, i + right + 1)
            if j != i
        )
        window_l = all(
            (_f(candles[j].get("low")) or math.inf) > l
            for j in range(i - left, i + right + 1)
            if j != i
        )
        if window_h:
            highs.append((i, h))
        if window_l:
            lows.append((i, l))
    return highs, lows


def round_number_levels(price: float, count: int = 4) -> list[float]:
    """Human round-number magnet levels above and below ``price``.

    Step adapts to magnitude (0-step for FX sub-pips handled by caller scale).
    Returns alternating above/below levels nearest first.
    """
    if price <= 0 or not math.isfinite(price):
        return []
    mag = math.floor(math.log10(price))
    step = 10.0 ** (mag - 2)
    # FX majors live around 1e0..1e2; a 1% step is too wide for intraday, so
    # refine: choose step so that price/step lands between 50 and 500.
    while price / step > 500:
        step *= 10.0
    while price / step < 50 and step > 1e-6:
        step /= 10.0
    base = math.floor(price / step) * step
    levels: list[float] = []
    up = base + step
    down = base if base < price else base - step
    for _ in range(count):
        levels.append(up)
        levels.append(down)
        up += step
        down -= step
    return sorted(set(round(lvl, 10) for lvl in levels))


def hour_of_day_range_profile(
    candles: Sequence[Candle], bucket_seconds: int
) -> dict[int, float]:
    """Average in-bucket travel (high-low)/close per UTC hour.

    Built from the loaded history itself — no external session calendar — so it
    adapts to the venue's own activity fingerprint.
    """
    profile: dict[int, list[float]] = {}
    for c in candles:
        ts = _f(c.get("time"))
        h = _f(c.get("high"))
        l = _f(c.get("low"))
        cl = _f(c.get("close"))
        if ts is None or h is None or l is None or cl is None or cl <= 0:
            continue
        hour = int((ts // 3600) % 24)
        profile.setdefault(hour, []).append((h - l) / cl)
    return {
        hour: sum(vals) / len(vals) for hour, vals in profile.items() if vals
    }


def volumes(candles: Sequence[Candle]) -> list[float]:
    out = []
    for c in candles:
        v = _f(c.get("volume"))
        if v is None:
            v = _f(c.get("vol"))
        if v is not None and v > 0:
            out.append(v)
    return out


def clamp01(x: float) -> float:
    return max(0.0, min(1.0, x))


def tanh_scale(x: float, scale: float) -> float:
    """Smooth 0..1 squash of a signed input around 0."""
    if scale <= 0 or not math.isfinite(x):
        return 0.0
    return math.tanh(x / scale)
