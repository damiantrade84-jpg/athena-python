"""VFI - Void Field Inefficiency.

A three-bar imbalance (bar i's low above bar i-2's high) marks a price band
that was skipped: the market repriced so fast that no two-sided trade happened
there. Most implementations treat these as binary boxes that are either "open"
or "filled" and draw them on a chart.

OPUS treats them as a decaying scalar field instead. Every void carries a mass
proportional to its size in sigma, eroded continuously by how much of it has
been traded back through and by its age. That gives three usable outputs:

    bias    net bullish minus bearish residual mass - which direction the
            market has actually been repricing, weighted by conviction
    magnet  inverse-distance-weighted pull toward unfilled voids, because
            unfilled inefficiency is where price tends to return
    shelf   the specific void price is retracing into right now, which is the
            entry zone for a continuation trade

The binary treatment loses all three: a 0.1-sigma void one bar old and a
3-sigma void that is 70% filled are not the same object.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

import opus.config as config
from opus.core.stats import squash
from opus.indicators.base import MarketContext
from opus.types import IndicatorReading


@dataclass
class Void:
    """One price inefficiency."""

    index: int
    top: float
    bottom: float
    direction: int      # +1 bullish (created by up-displacement), -1 bearish
    size_sigma: float
    fill: float = 0.0   # fraction traded back through, [0, 1]
    age: int = 0
    residual_mass: float = 0.0

    @property
    def mid(self) -> float:
        return (self.top + self.bottom) / 2.0

    @property
    def is_dead(self) -> bool:
        return self.fill >= float(config.indicator("VFI")["fill_invalidate"])

    def as_dict(self) -> dict:
        return {
            "index": int(self.index),
            "top": round(float(self.top), 8),
            "bottom": round(float(self.bottom), 8),
            "mid": round(float(self.mid), 8),
            "direction": int(self.direction),
            "sizeSigma": round(float(self.size_sigma), 4),
            "fill": round(float(self.fill), 4),
            "age": int(self.age),
            "residualMass": round(float(self.residual_mass), 4),
        }


def detect_voids(ctx: MarketContext) -> list[Void]:
    """Find three-bar imbalances and erode them by subsequent trade."""
    cfg = config.indicator("VFI")
    candles = ctx.structure
    n = len(candles)
    if n < 5 or not ctx.has_sigma:
        return []

    sigma = ctx.sigma
    lookback = int(cfg["lookback"])
    min_gap = float(cfg["min_gap_sigma"])
    max_age = int(cfg["max_age"])
    half_life = float(cfg["age_half_life"])

    start = max(2, n - lookback)
    high = candles.high
    low = candles.low
    voids: list[Void] = []

    for i in range(start, n):
        h2, l2 = float(high[i - 2]), float(low[i - 2])
        hi, li = float(high[i]), float(low[i])
        if not all(np.isfinite(x) for x in (h2, l2, hi, li)):
            continue

        if li > h2:                      # bullish void between h2 and li
            top, bottom, direction = li, h2, 1
        elif hi < l2:                    # bearish void between hi and l2
            top, bottom, direction = l2, hi, -1
        else:
            continue

        size = top - bottom
        size_sigma = size / sigma
        if size_sigma < min_gap:
            continue

        age = n - 1 - i
        if age > max_age:
            continue

        # Erosion: how deeply later bars traded back into the band.
        if i + 1 < n:
            after_low = float(np.nanmin(low[i + 1:]))
            after_high = float(np.nanmax(high[i + 1:]))
        else:
            after_low, after_high = li, hi

        if direction > 0:
            penetration = top - after_low          # traded down into it
        else:
            penetration = after_high - bottom      # traded up into it
        fill = float(np.clip(penetration / size, 0.0, 1.0)) if size > 0 else 1.0

        decay = float(np.power(0.5, age / half_life)) if half_life > 0 else 1.0
        residual = size_sigma * (1.0 - fill) * decay

        voids.append(
            Void(
                index=i, top=top, bottom=bottom, direction=direction,
                size_sigma=size_sigma, fill=fill, age=age,
                residual_mass=residual,
            )
        )

    return voids


def active_voids(ctx: MarketContext) -> list[Void]:
    """Voids that have not been substantially traded back through."""
    invalidate = float(config.indicator("VFI")["fill_invalidate"])
    return [v for v in ctx.cached("voids", lambda: detect_voids(ctx))
            if v.fill < invalidate]


def find_shelf(ctx: MarketContext, direction: int) -> Void | None:
    """The nearest live void in `direction` that price could retrace into.

    For a long, that is a bullish void at or below current price: the market
    displaced up and left an untested band beneath. Entering there buys the
    inefficiency rather than chasing the displacement.
    """
    cfg = config.indicator("VFI")
    price = ctx.last_price
    if price <= 0 or not ctx.has_sigma:
        return None
    max_dist = float(cfg["shelf_max_distance_sigma"]) * ctx.sigma

    candidates = []
    for v in active_voids(ctx):
        if v.direction != direction:
            continue
        if direction > 0:
            # Usable if price is above it or already inside it.
            if v.bottom > price:
                continue
            distance = max(0.0, price - v.top)
        else:
            if v.top < price:
                continue
            distance = max(0.0, v.bottom - price)
        if distance > max_dist:
            continue
        candidates.append((distance, -v.residual_mass, v))

    if not candidates:
        return None
    candidates.sort(key=lambda item: (item[0], item[1]))
    return candidates[0][2]


def compute(ctx: MarketContext) -> IndicatorReading:
    cfg = config.indicator("VFI")
    voids = ctx.cached("voids", lambda: detect_voids(ctx))
    if not voids:
        return IndicatorReading("VFI", 0.0, 0.0, {"reason": "no_voids"})

    live = [v for v in voids if v.fill < float(cfg["fill_invalidate"])]
    bull = sum(v.residual_mass for v in live if v.direction > 0)
    bear = sum(v.residual_mass for v in live if v.direction < 0)
    bias_raw = bull - bear
    value = squash(bias_raw, float(cfg["squash_scale"]))

    # Magnet: unfilled inefficiency close to price exerts more pull than the
    # same mass far away, so weight by inverse distance in sigma.
    price = ctx.last_price
    scale = float(cfg["magnet_distance_sigma"]) * ctx.sigma
    magnet = 0.0
    if price > 0 and ctx.has_sigma and scale > 0:
        for v in live:
            distance = abs(v.mid - price)
            pull = v.residual_mass / (1.0 + distance / scale)
            magnet += pull * (1.0 if v.mid > price else -1.0)

    confidence = float(np.tanh(len(live) / 3.0)) if live else 0.1
    nearest = min(live, key=lambda v: abs(v.mid - price), default=None) if live else None

    return IndicatorReading(
        "VFI",
        value,
        confidence,
        {
            "voids": len(voids),
            "liveVoids": len(live),
            "bullMass": round(float(bull), 4),
            "bearMass": round(float(bear), 4),
            "biasRaw": round(float(bias_raw), 4),
            "magnet": round(float(magnet), 4),
            "nearestVoid": nearest.as_dict() if nearest else None,
        },
    )


__all__ = ["Void", "detect_voids", "active_voids", "find_shelf", "compute"]
