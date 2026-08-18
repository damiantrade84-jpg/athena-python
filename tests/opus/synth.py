"""Synthetic market builders for OPUS tests.

These construct price paths with a KNOWN embedded structure (a sweep, a
displacement void, an absorption bar) so an indicator can be asserted against
what it is supposed to detect, rather than merely asserted to run.
"""

from __future__ import annotations

import numpy as np

from opus.types import Candles, Quote

BASE_TS = 1_700_000_000  # a Tuesday, 00:00-ish UTC


def _mk(
    symbol: str,
    timeframe: str,
    o, h, l, c, v,
    step: int,
    start_ts: int = BASE_TS,
    tick_volume: bool = False,
) -> Candles:
    n = len(c)
    ts = np.array([start_ts + i * step for i in range(n)], dtype=float)
    return Candles(
        symbol=symbol,
        timeframe=timeframe,
        ts=ts,
        open=np.asarray(o, dtype=float),
        high=np.asarray(h, dtype=float),
        low=np.asarray(l, dtype=float),
        close=np.asarray(c, dtype=float),
        volume=np.asarray(v, dtype=float),
        source="synth",
        volume_is_tick_count=tick_volume,
    )


def random_walk(
    n: int = 400,
    start: float = 100.0,
    vol: float = 0.0015,
    seed: int = 11,
    timeframe: str = "M15",
    step: int = 900,
    symbol: str = "TEST",
    drift: float = 0.0,
) -> Candles:
    """A plain GBM-ish walk with well-formed OHLC and non-degenerate wicks."""
    rng = np.random.default_rng(seed)
    rets = rng.normal(drift, vol, n)
    close = start * np.exp(np.cumsum(rets))
    open_ = np.concatenate(([start], close[:-1]))
    # Independent wick extents so highs/lows are not a deterministic function
    # of open/close (which would create pathological ties).
    up = np.abs(rng.normal(0, vol * 0.8, n))
    dn = np.abs(rng.normal(0, vol * 0.8, n))
    high = np.maximum(open_, close) * (1.0 + up)
    low = np.minimum(open_, close) * (1.0 - dn)
    vol_arr = rng.lognormal(mean=7.0, sigma=0.35, size=n)
    return _mk(symbol, timeframe, open_, high, low, close, vol_arr, step)


def with_sweep(
    base: Candles,
    *,
    side: str = "low",
    depth_frac: float = 0.004,
    reclaim: bool = True,
    at: int = -3,
) -> Candles:
    """Embed a liquidity sweep of a prior extreme near the end of the series.

    side="low"  pokes below the prior swing low.
    reclaim=True closes back inside (a rejection -> bullish for a low sweep);
    reclaim=False closes beyond (a genuine break -> bearish for a low sweep).
    """
    o = base.open.copy(); h = base.high.copy()
    l = base.low.copy(); c = base.close.copy(); v = base.volume.copy()
    n = len(c)
    i = n + at if at < 0 else at

    if side == "low":
        pool = float(np.min(l[max(0, i - 40): i - 2]))
        poke = pool * (1.0 - depth_frac)
        l[i] = poke
        o[i] = pool * (1.0 + depth_frac * 0.2)
        if reclaim:
            c[i] = pool * (1.0 + depth_frac * 0.5)   # closed back above the pool
            h[i] = max(o[i], c[i]) * 1.0005
        else:
            c[i] = poke * (1.0 - depth_frac * 0.3)   # closed below, held
            h[i] = pool * (1.0 + depth_frac * 0.1)
    else:
        pool = float(np.max(h[max(0, i - 40): i - 2]))
        poke = pool * (1.0 + depth_frac)
        h[i] = poke
        o[i] = pool * (1.0 - depth_frac * 0.2)
        if reclaim:
            c[i] = pool * (1.0 - depth_frac * 0.5)
            l[i] = min(o[i], c[i]) * 0.9995
        else:
            c[i] = poke * (1.0 + depth_frac * 0.3)
            l[i] = pool * (1.0 - depth_frac * 0.1)

    v[i] = float(np.median(v)) * 3.0
    # Carry the sweep bar's close forward so later bars stay consistent.
    for j in range(i + 1, n):
        shift = c[i] - base.close[i]
        o[j] += shift; h[j] += shift; l[j] += shift; c[j] += shift
    return _mk(base.symbol, base.timeframe, o, h, l, c, v,
               int(base.ts[1] - base.ts[0]), int(base.ts[0]))


def with_void(base: Candles, *, direction: int = 1, size_frac: float = 0.006,
              at: int = -6) -> Candles:
    """Embed a three-bar imbalance (displacement leg) near the end."""
    o = base.open.copy(); h = base.high.copy()
    l = base.low.copy(); c = base.close.copy(); v = base.volume.copy()
    n = len(c)
    i = n + at if at < 0 else at

    gap = c[i - 2] * size_frac
    if direction > 0:
        # bar i's low must sit above bar i-2's high
        shift = (h[i - 2] + gap) - l[i]
        for j in range(i, n):
            o[j] += shift; h[j] += shift; l[j] += shift; c[j] += shift
    else:
        shift = h[i] - (l[i - 2] - gap)
        for j in range(i, n):
            o[j] -= shift; h[j] -= shift; l[j] -= shift; c[j] -= shift
    v[i] = float(np.median(v)) * 2.5
    return _mk(base.symbol, base.timeframe, o, h, l, c, v,
               int(base.ts[1] - base.ts[0]), int(base.ts[0]))


def with_absorption(base: Candles, *, bullish: bool = True, at: int = -2) -> Candles:
    """Embed a high-effort, wide-range, low-conversion rejection bar."""
    o = base.open.copy(); h = base.high.copy()
    l = base.low.copy(); c = base.close.copy(); v = base.volume.copy()
    n = len(c)
    i = n + at if at < 0 else at
    mid = float(c[i - 1])
    width = mid * 0.010
    if bullish:
        o[i] = mid + width * 0.30
        l[i] = mid - width          # driven down hard
        h[i] = mid + width * 0.42
        c[i] = mid + width * 0.38   # returned to the top: lower prices rejected
    else:
        o[i] = mid - width * 0.30
        h[i] = mid + width
        l[i] = mid - width * 0.42
        c[i] = mid - width * 0.38
    v[i] = float(np.median(v)) * 6.0
    return _mk(base.symbol, base.timeframe, o, h, l, c, v,
               int(base.ts[1] - base.ts[0]), int(base.ts[0]))


def clean_pool_series(
    *,
    side: str = "low",
    reclaim: bool = True,
    n: int = 160,
    base: float = 100.0,
    timeframe: str = "M15",
    step: int = 900,
) -> Candles:
    """A deterministic series with ONE unambiguous pool and ONE interaction.

    Built by hand rather than sampled, so the sweep/hold classification can be
    asserted exactly. A noisy base is the wrong fixture for this: LDI
    aggregates every interaction in its lookback, so a single injected event
    competes with dozens of incidental ones and the assertion ends up testing
    the fixture's noise rather than the classifier.
    """
    o = np.full(n, base)
    h = np.full(n, base + 0.30)
    l = np.full(n, base - 0.30)
    c = np.full(n, base)
    v = np.full(n, 1000.0)

    # Gentle alternating body so Yang-Zhang sigma is well defined and non-zero.
    for i in range(n):
        wobble = 0.12 if i % 2 == 0 else -0.12
        o[i] = base - wobble
        c[i] = base + wobble

    pivot_at = n - 40
    if side == "low":
        pool = base - 1.20
        l[pivot_at] = pool                     # the single clear pivot low
        c[pivot_at] = base - 0.20
        o[pivot_at] = base - 0.10
        h[pivot_at] = base + 0.10
    else:
        pool = base + 1.20
        h[pivot_at] = pool                     # the single clear pivot high
        c[pivot_at] = base + 0.20
        o[pivot_at] = base + 0.10
        l[pivot_at] = base - 0.10

    # Final bar interacts with that pool and nothing else.
    i = n - 1
    if side == "low":
        l[i] = pool - 0.40                     # poke through
        if reclaim:
            o[i] = pool + 0.10
            c[i] = pool + 0.30                 # closed back above -> rejection
            h[i] = pool + 0.35
        else:
            o[i] = pool - 0.10
            c[i] = pool - 0.35                 # closed below -> genuine break
            h[i] = pool + 0.05
    else:
        h[i] = pool + 0.40
        if reclaim:
            o[i] = pool - 0.10
            c[i] = pool - 0.30
            l[i] = pool - 0.35
        else:
            o[i] = pool + 0.10
            c[i] = pool + 0.35
            l[i] = pool - 0.05
    v[i] = 3000.0
    return _mk("TEST", timeframe, o, h, l, c, v, step)


def trending(n: int = 400, slope: float = 0.0016, seed: int = 5, **kw) -> Candles:
    """A clean directional path: high fractal efficiency."""
    return random_walk(n=n, vol=0.0006, drift=slope, seed=seed, **kw)


def choppy(n: int = 400, seed: int = 5, period: float = 7.0, **kw) -> Candles:
    """An oscillating path: low fractal efficiency, high rotation.

    The period must be short relative to the efficiency window, or a window
    that spans half a cycle looks perfectly monotone and scores as a trend -
    which is exactly what a 38-bar period did against a 20-bar window.
    """
    rng = np.random.default_rng(seed)
    t = np.arange(n)
    close = 100.0 + 1.2 * np.sin(2.0 * np.pi * t / period) + rng.normal(0, 0.05, n)
    open_ = np.concatenate(([close[0]], close[:-1]))
    high = np.maximum(open_, close) + np.abs(rng.normal(0, 0.08, n))
    low = np.minimum(open_, close) - np.abs(rng.normal(0, 0.08, n))
    v = rng.lognormal(7.0, 0.3, n)
    return _mk(kw.get("symbol", "TEST"), kw.get("timeframe", "M15"),
               open_, high, low, close, v, kw.get("step", 900))


def coherent_ladder(
    n_trigger: int = 1500,
    seed: int = 11,
    symbol: str = "TEST",
    vol: float = 0.0009,
    drift: float = 0.0,
) -> tuple[Candles, Candles, Candles]:
    """Build (context H1, structure M15, trigger M5) from ONE price path.

    Production always hands the three rungs the same instrument. Generating
    them as independent random walks - as a naive fixture does - makes the
    quote disagree with session VWAP by double-digit sigma and silently turns
    every value-based assertion into a test of the fixture rather than of the
    engine.
    """
    trigger = random_walk(
        n=n_trigger, vol=vol, seed=seed, timeframe="M5", step=300,
        symbol=symbol, drift=drift,
    )
    structure = resample(trigger, 3, "M15")
    context = resample(structure, 4, "H1")
    return context, structure, trigger


def quote_for(candles: Candles, spread_pct: float = 0.01) -> Quote:
    mid = float(candles.close[-1])
    half = mid * spread_pct / 200.0
    return Quote(
        symbol=candles.symbol,
        bid=mid - half,
        ask=mid + half,
        ts=float(candles.ts[-1]) + 60.0,
        source="synth",
    )


def resample(candles: Candles, factor: int, timeframe: str) -> Candles:
    """Aggregate `factor` bars into one, for building a coarser rung."""
    n = len(candles) // factor
    o = np.empty(n); h = np.empty(n); l = np.empty(n)
    c = np.empty(n); v = np.empty(n); ts = np.empty(n)
    for i in range(n):
        s = slice(i * factor, (i + 1) * factor)
        o[i] = candles.open[s][0]
        h[i] = candles.high[s].max()
        l[i] = candles.low[s].min()
        c[i] = candles.close[s][-1]
        v[i] = candles.volume[s].sum()
        ts[i] = candles.ts[s][0]
    return Candles(
        symbol=candles.symbol, timeframe=timeframe, ts=ts,
        open=o, high=h, low=l, close=c, volume=v, source="synth",
    )
