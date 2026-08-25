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


def squeeze_percentile(candles: Sequence[Candle], window: int = 24) -> float | None:
    """Percentile (0..1) of the current bar-range width vs the trailing window.

    Low values = the tape is coiled relative to its recent self. Uses true
    range / ATR ratios so the measure is volatility-normalized, and compares
    the *window's* width (not just the last bar) against its own history, so a
    single quiet bar cannot fake compression.
    """
    trs = true_ranges(candles)
    if len(trs) < window + 10:
        return None
    atr = wilder_atr(candles, 14)
    if atr is None or atr <= 0:
        return None
    widths = [sum(trs[i - window:i]) / (window * atr) for i in range(window, len(trs) + 1)]
    if len(widths) < 2:
        return None
    # Midpoint tie handling: a perfectly uniform tape ranks 0.5 (no contrast),
    # not 1.0 — ties-as-below made every flat series look "expanded".
    x = widths[-1]
    hist = widths[:-1]
    below = sum(1 for v in hist if v < x)
    equal = sum(1 for v in hist if v == x)
    return (below + equal / 2.0) / len(hist)


def detect_sweep(
    candles: Sequence[Candle],
    levels: Sequence[float],
    atr: float,
    *,
    lookback: int = 8,
    max_penetration_atr: float = 0.5,
) -> dict[str, float] | None:
    """Liquidity-sweep / reclaim detection against given magnet levels.

    A sweep takes out resting stops beyond a level and then closes back through
    it — the classic failed-breakout reversal evidence. Scans the last
    ``lookback`` confirmed bars; returns {'LONG': strength, 'SHORT': strength}
    with strengths in 0..1 (deeper reclaim body = stronger), or None when no
    sweep fired.
    """
    if atr <= 0 or not levels or len(candles) < 3:
        return None
    out = {"LONG": 0.0, "SHORT": 0.0}
    n = len(candles)
    pen = max_penetration_atr * atr
    clean_levels = [v for v in (_f(lvl) for lvl in levels) if v is not None and v > 0]
    for i in range(max(1, n - lookback), n):
        c = candles[i]
        p = candles[i - 1]
        lo = _f(c.get("low"))
        hi = _f(c.get("high"))
        o = _f(c.get("open"))
        cl = _f(c.get("close"))
        pc = _f(p.get("close"))
        if lo is None or hi is None or o is None or cl is None or pc is None:
            continue
        body = abs(cl - o)
        for lv in clean_levels:
            # Sweep BELOW a support: price was holding strictly above the
            # level, pierces under it by <= pen, then closes back above —
            # resting stops taken, buyers reclaimed. Without the prior-side
            # guard every bar straddling the level would fake a sweep.
            if pc > lv and lo < lv <= cl:
                depth = min(pen, lv - lo)
                strength = clamp01((depth / pen) * 0.5 + (body / (2.0 * atr)) * 0.5)
                out["LONG"] = max(out["LONG"], strength)
            # Sweep ABOVE a resistance: price was holding strictly below,
            # pokes over the level, closes back under it.
            if pc < lv and hi > lv >= cl:
                depth = min(pen, hi - lv)
                strength = clamp01((depth / pen) * 0.5 + (body / (2.0 * atr)) * 0.5)
                out["SHORT"] = max(out["SHORT"], strength)
    if out["LONG"] <= 0.0 and out["SHORT"] <= 0.0:
        return None
    return out


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


def round_number_levels(
    price: float, count: int = 4, step_hint: float | None = None
) -> list[float]:
    """Human round-number magnet levels above and below ``price``.

    ``step_hint`` scales the grid to the instrument's own volatility (pass
    ~2*ATR so levels stay intraday-relevant; e.g. EUR/USD gets a 10-pip grid,
    not a 100-pip one). Without a hint the grid falls back to a magnitude-
    derived step. Returns sorted unique levels.
    """
    if price <= 0 or not math.isfinite(price):
        return []
    hint = step_hint if (step_hint is not None and math.isfinite(step_hint) and step_hint > 0) else None
    if hint is not None:
        # Nearest decade step to the hint (log distance), floored at 1e-6.
        mag = math.floor(math.log10(hint))
        candidates = [10.0 ** (mag - 1 + k) for k in range(3)]
        best = min(candidates, key=lambda s: abs(math.log10(s) - math.log10(hint)))
        step = max(best, 1e-6)
    else:
        mag = math.floor(math.log10(price))
        step = 10.0 ** (mag - 2)
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
