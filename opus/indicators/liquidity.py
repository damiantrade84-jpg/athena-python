"""LDI - Liquidity Displacement Index.

The single question this indicator answers: when price traded through a level
where stops were resting, did it STAY there or was it rejected?

Those two outcomes look identical to any oscillator and to most breakout logic,
yet they are opposite trades. A poke above equal highs that closes back below
took buy-side liquidity and is bearish. The same poke that closes and holds
above is a genuine repricing and is bullish. LDI separates them by measuring
*retention*: what fraction of the excursion beyond the level the bar kept by
its close.

Pools are weighted by how much liquidity plausibly rests at them - equal highs
score far higher than a lone pivot, because a cluster of touches at one price
is where stop orders accumulate.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

import opus.config as config
from opus.core.stats import squash, swing_pivots
from opus.indicators.base import MarketContext
from opus.types import IndicatorReading


@dataclass
class LiquidityPool:
    """A price level where resting orders are likely to sit."""

    price: float
    side: str            # "high" = buy-side liquidity above, "low" = sell-side below
    index: int           # structure-TF bar index where the pool was formed
    touches: int = 1
    mass: float = 1.0
    origin: str = "pivot"
    swept_at: int | None = None

    @property
    def is_swept(self) -> bool:
        return self.swept_at is not None

    def as_dict(self) -> dict:
        return {
            "price": round(float(self.price), 8),
            "side": self.side,
            "index": int(self.index),
            "touches": int(self.touches),
            "mass": round(float(self.mass), 4),
            "origin": self.origin,
            "sweptAt": self.swept_at,
        }


@dataclass
class LiquidityEvent:
    """One interaction between price and a pool."""

    index: int
    pool: LiquidityPool
    kind: str            # "sweep" (rejected) | "hold" (displaced through)
    direction: int       # +1 bullish implication, -1 bearish implication
    depth_sigma: float
    retention: float
    strength: float
    detail: dict = field(default_factory=dict)


def detect_pools(ctx: MarketContext) -> list[LiquidityPool]:
    """Find liquidity pools on the structure timeframe.

    Clusters pivots that sit within `equality_tol_sigma` of each other into a
    single pool whose mass grows with the number of touches. This is the
    quantitative form of "equal highs": three touches of one price is a far
    more attractive stop-hunt target than one isolated swing.
    """
    cfg = config.indicator("LDI")
    candles = ctx.structure
    n = len(candles)
    if n < 3 * int(cfg["pivot_k"]) + 5 or not ctx.has_sigma:
        return []

    lookback = int(cfg["lookback"])
    start = max(0, n - lookback)
    high = candles.high[start:]
    low = candles.low[start:]
    sigma = ctx.sigma
    tol = float(cfg["equality_tol_sigma"]) * sigma
    half_life = float(cfg["touch_decay_half_life"])
    max_age = int(cfg["pool_max_age"])

    piv_h, piv_l = swing_pivots(high, low, int(cfg["pivot_k"]))
    last = high.shape[0] - 1

    def _cluster(indices: np.ndarray, prices: np.ndarray, side: str) -> list[LiquidityPool]:
        pools: list[LiquidityPool] = []
        # Newest first so a cluster is anchored on its most recent touch.
        order = sorted(indices.tolist(), reverse=True)
        used: set[int] = set()
        for idx in order:
            if idx in used:
                continue
            price = float(prices[idx])
            members = [idx]
            for other in order:
                if other in used or other == idx:
                    continue
                if abs(float(prices[other]) - price) <= tol:
                    members.append(other)
                    used.add(other)
            used.add(idx)
            anchor = max(members)
            age = last - anchor
            if age > max_age:
                continue
            touches = len(members)
            # Mass: touches beyond the first add sub-linearly (a fourth touch
            # adds less than the second), then decay by recency.
            recency = float(np.power(0.5, age / half_life)) if half_life > 0 else 1.0
            mass = (1.0 + np.log1p(touches - 1) * 1.6) * recency
            pools.append(
                LiquidityPool(
                    price=float(np.mean([prices[m] for m in members])),
                    side=side,
                    index=int(anchor + start),
                    touches=touches,
                    mass=float(mass),
                    origin="equal" if touches > 1 else "pivot",
                )
            )
        return pools

    pools = _cluster(piv_h, high, "high") + _cluster(piv_l, low, "low")

    # Session and prior-day extremes are pools in their own right: they are
    # the levels the widest audience is watching, so they collect the most
    # resting orders even when no fractal pivot formed there.
    pools.extend(_session_extreme_pools(ctx))
    pools.sort(key=lambda p: p.mass, reverse=True)
    return pools


def _session_extreme_pools(ctx: MarketContext) -> list[LiquidityPool]:
    """Prior-session high/low as explicit pools."""
    from opus.indicators.base import session_for_ts

    candles = ctx.structure
    n = len(candles)
    if n < 20:
        return []
    ts = candles.ts
    current = session_for_ts(float(ts[-1]))

    # Walk back to the previous distinct session block.
    i = n - 1
    while i > 0 and session_for_ts(float(ts[i - 1])) == current:
        i -= 1
    cur_start = i
    if cur_start <= 1:
        return []
    prev_session = session_for_ts(float(ts[cur_start - 1]))
    j = cur_start - 1
    while j > 0 and session_for_ts(float(ts[j - 1])) == prev_session:
        j -= 1
    if cur_start - j < 2:
        return []

    seg_h = candles.high[j:cur_start]
    seg_l = candles.low[j:cur_start]
    if not (np.isfinite(seg_h).any() and np.isfinite(seg_l).any()):
        return []

    hi_idx = int(np.nanargmax(seg_h)) + j
    lo_idx = int(np.nanargmin(seg_l)) + j
    return [
        LiquidityPool(
            price=float(candles.high[hi_idx]), side="high", index=hi_idx,
            touches=1, mass=1.45, origin=f"session_high:{prev_session}",
        ),
        LiquidityPool(
            price=float(candles.low[lo_idx]), side="low", index=lo_idx,
            touches=1, mass=1.45, origin=f"session_low:{prev_session}",
        ),
    ]


def detect_events(ctx: MarketContext, pools: list[LiquidityPool]) -> list[LiquidityEvent]:
    """Classify every pool interaction as a sweep or a hold."""
    cfg = config.indicator("LDI")
    candles = ctx.structure
    n = len(candles)
    if n == 0 or not pools or not ctx.has_sigma:
        return []

    sigma = ctx.sigma
    lookback = int(cfg["lookback"])
    scan_start = max(0, n - lookback)
    sweep_max = float(cfg["sweep_retention_max"])
    hold_min = float(cfg["hold_retention_min"])
    min_pen = float(cfg["min_penetration_sigma"])

    events: list[LiquidityEvent] = []
    for pool in pools:
        # Only bars strictly after the pool formed can interact with it.
        first = max(scan_start, pool.index + 1)
        for i in range(first, n):
            h = float(candles.high[i])
            l = float(candles.low[i])
            c = float(candles.close[i])
            if not (np.isfinite(h) and np.isfinite(l) and np.isfinite(c)):
                continue

            if pool.side == "high":
                excursion = h - pool.price
                if excursion <= 0:
                    continue
                depth = excursion / sigma
                if depth < min_pen:
                    continue
                retention = (c - pool.price) / excursion
            else:
                excursion = pool.price - l
                if excursion <= 0:
                    continue
                depth = excursion / sigma
                if depth < min_pen:
                    continue
                retention = (pool.price - c) / excursion

            if retention < sweep_max:
                kind = "sweep"
                # Rejected: the implication is AGAINST the penetration side.
                direction = -1 if pool.side == "high" else 1
                # A deeper poke that still failed is a stronger rejection, but
                # this MUST be clamped. Retention is unbounded below - a bar
                # that dips one tick under a pool and closes far above it
                # yields retention of -2.5 or worse, which without a clamp
                # produces ~9x the strength of a normal event, saturates the
                # tanh on its own, and lets one wick dictate the indicator.
                extremity = (sweep_max - retention) / max(sweep_max, 1e-9)
                extremity = min(max(extremity, 0.0), 1.0)
                strength = float(np.tanh(depth) * (0.45 + 0.55 * extremity))
            elif retention > hold_min:
                kind = "hold"
                direction = 1 if pool.side == "high" else -1
                extremity = (retention - hold_min) / max(1.0 - hold_min, 1e-9)
                extremity = min(max(extremity, 0.0), 1.0)
                strength = float(np.tanh(depth) * (0.45 + 0.55 * extremity))
            else:
                # Ambiguous middle: price closed inside the excursion. Carries
                # no directional claim; recording it would manufacture signal.
                continue

            if pool.swept_at is None:
                pool.swept_at = i
            events.append(
                LiquidityEvent(
                    index=i,
                    pool=pool,
                    kind=kind,
                    direction=direction,
                    depth_sigma=float(depth),
                    retention=float(retention),
                    strength=float(strength * pool.mass),
                    detail={"poolPrice": pool.price, "poolOrigin": pool.origin},
                )
            )
            # One classified interaction per pool per direction is enough; the
            # level's information is spent once it has been taken.
            break

    events.sort(key=lambda e: e.index)
    return events


def compute(ctx: MarketContext) -> IndicatorReading:
    """Signed liquidity displacement in [-1, 1]; positive supports long."""
    cfg = config.indicator("LDI")
    pools = ctx.cached("pools", lambda: detect_pools(ctx))
    if not pools:
        return IndicatorReading("LDI", 0.0, 0.0, {"reason": "no_pools"})

    events = ctx.cached("liquidity_events", lambda: detect_events(ctx, pools))
    if not events:
        return IndicatorReading(
            "LDI", 0.0, 0.15, {"reason": "no_events", "pools": len(pools)}
        )

    n = len(ctx.structure)
    half_life = float(cfg["event_half_life"])
    contributions = []
    for ev in events:
        age = n - 1 - ev.index
        w = float(np.power(0.5, age / half_life)) if half_life > 0 else 1.0
        contributions.append(w * ev.direction * ev.strength)

    raw = float(np.sum(contributions))
    value = squash(raw, float(cfg["squash_scale"]))

    # Confidence rises with the number of *recent* events, not the total: a
    # burst of stale events from 150 bars ago is not current evidence.
    recent = [e for e in events if (n - 1 - e.index) <= half_life * 3]
    confidence = float(np.tanh(len(recent) / 2.5)) if recent else 0.15

    latest = events[-1]
    return IndicatorReading(
        "LDI",
        value,
        confidence,
        {
            "pools": len(pools),
            "events": len(events),
            "recentEvents": len(recent),
            "rawScore": round(raw, 4),
            "lastKind": latest.kind,
            "lastDirection": latest.direction,
            "lastDepthSigma": round(latest.depth_sigma, 3),
            "lastRetention": round(latest.retention, 3),
            "lastPoolOrigin": latest.pool.origin,
            "lastBarsAgo": n - 1 - latest.index,
        },
    )


__all__ = [
    "LiquidityPool", "LiquidityEvent", "detect_pools", "detect_events", "compute",
]
