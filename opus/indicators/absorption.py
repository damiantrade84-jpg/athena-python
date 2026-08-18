"""ARC - Absorption / Rejection Curve.

Effort versus result. A bar that traded enormous volume across a wide range and
finished back where it started did not fail to move - it was *absorbed*.
Someone with size took the other side of everything the aggressors could throw.
That is a materially different event from a quiet bar of the same net change,
and no volume oscillator distinguishes them, because both show "volume".

ARC scores three things jointly per bar:

    effort E   how abnormal the participation was (robust z, heavy tails)
    result R   |close-open| / (high-low): how much of the travel was converted
    location L close position within the range, which names the winning side

A high-effort, low-conversion, wide-range bar closing on its high means price
was driven down, met size, and was returned to the top: lower prices were
rejected. That is bullish, and it is what `E * (1-R) * L` isolates.
"""

from __future__ import annotations

import numpy as np

import opus.config as config
from opus.core.stats import robust_z, squash
from opus.indicators.base import MarketContext
from opus.types import IndicatorReading


def compute(ctx: MarketContext) -> IndicatorReading:
    cfg = config.indicator("ARC")
    candles = ctx.structure
    n = len(candles)
    if n < 12 or not ctx.has_sigma:
        return IndicatorReading("ARC", 0.0, 0.0, {"reason": "insufficient_bars"})

    lookback = int(cfg["lookback"])
    effort_window = int(cfg["effort_window"])
    min_range = float(cfg["min_range_sigma"]) * ctx.sigma
    half_life = float(cfg["event_half_life"])

    start = max(0, n - lookback)
    o = candles.open[start:]
    h = candles.high[start:]
    lo = candles.low[start:]
    c = candles.close[start:]
    v = candles.volume[start:]
    m = c.shape[0]

    rng = h - lo
    valid = (
        np.isfinite(o) & np.isfinite(h) & np.isfinite(lo) & np.isfinite(c)
        & (rng > 0) & (rng >= min_range)
    )
    if int(valid.sum()) < 5:
        return IndicatorReading(
            "ARC", 0.0, 0.1, {"reason": "no_qualifying_bars", "checked": int(m)}
        )

    # Volume may legitimately be a tick count (MT5 FX). Tick count is still a
    # valid participation proxy, but it is not size, so the reading is marked
    # and its confidence trimmed rather than being silently treated as volume.
    vol_ok = np.isfinite(v) & (v > 0)
    has_volume = bool(vol_ok.sum() >= max(10, m * 0.5))

    result = np.abs(c - o) / np.where(rng > 0, rng, np.nan)
    location = (c - lo) / np.where(rng > 0, rng, np.nan)
    centred = 2.0 * location - 1.0            # [-1, +1], +1 = close on the high
    expansion = np.minimum(rng / ctx.sigma, 4.0)

    contributions = np.zeros(m)
    efforts = np.zeros(m)
    for i in range(m):
        if not valid[i]:
            continue
        if has_volume:
            window = v[max(0, i - effort_window): i + 1]
            e = robust_z(window, effort_window)
        else:
            # No usable volume: fall back to range expansion as the effort
            # proxy. Weaker, and confidence below reflects that.
            window = rng[max(0, i - effort_window): i + 1]
            e = robust_z(window, effort_window)
        # Only above-normal participation carries absorption information.
        e = max(e, 0.0)
        efforts[i] = e
        if e <= 0:
            continue
        inefficiency = 1.0 - float(result[i])
        contributions[i] = e * inefficiency * float(expansion[i]) * float(centred[i])

    active = int(np.count_nonzero(contributions))
    if active == 0:
        return IndicatorReading(
            "ARC", 0.0, 0.15,
            {"reason": "no_effort_bars", "hasVolume": has_volume},
        )

    ages = np.arange(m - 1, -1, -1, dtype=float)
    weights = np.power(0.5, ages / half_life) if half_life > 0 else np.ones(m)
    raw = float(np.sum(contributions * weights) / max(np.sum(weights), 1e-9))
    value = squash(raw, float(cfg["squash_scale"]))

    recent_active = int(np.count_nonzero(contributions[-int(half_life * 3):]))
    confidence = float(np.tanh(recent_active / 3.0))
    if not has_volume:
        confidence *= 0.65
    elif candles.volume_is_tick_count:
        confidence *= 0.85

    peak_idx = int(np.argmax(np.abs(contributions)))
    return IndicatorReading(
        "ARC",
        value,
        confidence,
        {
            "rawScore": round(raw, 4),
            "activeBars": active,
            "recentActiveBars": recent_active,
            "hasVolume": has_volume,
            "volumeIsTickCount": bool(candles.volume_is_tick_count),
            "peakBarsAgo": m - 1 - peak_idx,
            "peakEffortZ": round(float(efforts[peak_idx]), 3),
            "peakConversion": round(float(result[peak_idx]), 3)
            if np.isfinite(result[peak_idx]) else None,
            "peakCloseLocation": round(float(location[peak_idx]), 3)
            if np.isfinite(location[peak_idx]) else None,
        },
    )


__all__ = ["compute"]
