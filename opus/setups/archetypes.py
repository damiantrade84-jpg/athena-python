"""Trade geometry for the four OPUS setups.

Every stop here is STRUCTURAL: it sits beyond the thing that would prove the
idea wrong - the far side of the sweep, the origin of the void, the outer band
of the value area. A fixed volatility multiple is not a thesis, and a stop that
does not correspond to an invalidation point is just a lottery on noise.

Structure alone is not sufficient, though. Two bounds are enforced on every
setup:

  min_stop_sigma  A stop closer than this is inside the instrument's own noise;
                  it will be taken out by spread and wick regardless of whether
                  the idea was right. It also inflates cost_r, because cost in
                  R is inverse to stop distance.

  max_stop_sigma  A stop wider than this cannot reach a sane target inside an
                  intraday horizon.

If a setup cannot satisfy both bounds AND the minimum reward multiple, it
returns None. Returning a stretched, unreachable target instead is how a
backtest gets a good RR and a live account gets a bad fill.
"""

from __future__ import annotations

import numpy as np

import opus.config as config
from opus.indicators import liquidity, value, voids
from opus.indicators.base import MarketContext
from opus.types import Archetype, Direction, Levels


def _geom() -> dict:
    return config.load()["GEOMETRY"]


def _cost_floor(ctx: MarketContext) -> float:
    """Smallest stop distance whose round-trip cost still clears the cost cap.

    Cost in R is inverse to stop distance, so for any given spread there is a
    stop below which `cost_r > max_cost_r` is arithmetically guaranteed and the
    setup can never be promoted no matter how good the read is.

    A fixed sigma floor cannot express that, because it is a volatility
    measure and this is an economics one - and the two move independently. On
    the account this was measured against, FX round-trip cost is ~2.7bps
    against an M15 sigma of ~2.6bps, so a viable stop is ~3.0 sigma; on gold,
    with sigma at 13.5bps, 0.7 sigma suffices. One constant cannot serve both.
    Deriving the floor from the live spread makes it self-adjusting per symbol,
    per session and per broker.
    """
    quote = ctx.quote
    if quote is None or quote.mid <= 0:
        return 0.0
    model = config.cost_model(ctx.asset_class)
    cap = float(config.load()["GATES"]["max_cost_r"])
    if cap <= 0:
        return 0.0
    price = float(quote.mid)
    round_trip = (
        float(quote.spread)
        + 2.0 * price * float(model["commission_bps"]) / 10_000.0
        + 2.0 * price * float(model["slippage_bps"]) / 10_000.0
    )
    return round_trip / cap


def _clamp_stop(ctx: MarketContext, entry: float, stop: float,
                direction: Direction) -> tuple[float, str] | None:
    """Enforce the stop-distance bounds, preserving the side."""
    geom = _geom()
    sigma = ctx.sigma
    if sigma <= 0:
        return None
    distance = abs(entry - stop)
    sigma_floor = float(geom["min_stop_sigma"]) * sigma
    cost_floor = _cost_floor(ctx)
    min_d = max(sigma_floor, cost_floor)
    max_d = float(geom["max_stop_sigma"]) * sigma

    note = "structural"
    if distance < min_d:
        distance = min_d
        note = "widened_to_cost_floor" if cost_floor > sigma_floor else "widened_to_min_sigma"
    elif distance > max_d:
        # Do NOT silently tighten a structural stop to fit: that would move the
        # stop inside the invalidation level and change the trade's meaning.
        return None

    stop = entry - direction.sign * distance
    return stop, note


def _opposing_pool(ctx: MarketContext, entry: float, direction: Direction) -> float | None:
    """Nearest untouched liquidity pool in the direction of the trade.

    That is where price is actually being drawn to - resting orders are the
    destination, not a round-number projection.
    """
    pools = ctx.cached("pools", lambda: liquidity.detect_pools(ctx))
    want = "high" if direction is Direction.LONG else "low"
    candidates = []
    for pool in pools:
        if pool.side != want:
            continue
        if direction is Direction.LONG and pool.price > entry:
            candidates.append((pool.price - entry, pool.price))
        elif direction is Direction.SHORT and pool.price < entry:
            candidates.append((entry - pool.price, pool.price))
    if not candidates:
        return None
    candidates.sort(key=lambda item: item[0])
    return float(candidates[0][1])


def _impulse_origin(
    ctx: MarketContext, void_index: int, direction: Direction
) -> float | None:
    """The swing that launched the displacement leg containing `void_index`.

    Walks back from the void to the last opposite-side pivot within a bounded
    window. Returns None when no pivot is found, so the caller falls back to
    the void edge rather than inventing a level.
    """
    from opus.core.stats import swing_pivots

    candles = ctx.structure
    n = len(candles)
    if n < 12:
        return None
    look = int(config.indicator("VFI").get("impulse_lookback", 30))
    start = max(0, int(void_index) - look)
    stop_idx = min(n, int(void_index) + 1)
    if stop_idx - start < 6:
        return None

    highs, lows = swing_pivots(candles.high[start:stop_idx],
                              candles.low[start:stop_idx], 2)
    if direction is Direction.LONG:
        if lows.size == 0:
            return None
        return float(np.nanmin(candles.low[start:stop_idx][lows]))
    if highs.size == 0:
        return None
    return float(np.nanmax(candles.high[start:stop_idx][highs]))


def _finalise(
    ctx: MarketContext,
    *,
    direction: Direction,
    entry: float,
    stop: float,
    primary_target: float | None,
    entry_kind: str,
    stop_basis: str,
    target_basis: str,
) -> Levels | None:
    """Apply stop bounds, build the target ladder, enforce minimum RR."""
    geom = _geom()
    sigma = ctx.sigma
    if sigma <= 0 or entry <= 0:
        return None

    # The stop must be on the correct side of entry, or the setup is incoherent.
    if direction is Direction.LONG and stop >= entry:
        return None
    if direction is Direction.SHORT and stop <= entry:
        return None

    clamped = _clamp_stop(ctx, entry, stop, direction)
    if clamped is None:
        return None
    stop, stop_note = clamped
    risk = abs(entry - stop)
    if risk <= 0:
        return None

    min_rr = float(geom["min_rr"])
    max_rr = float(geom["max_rr"])

    # Prefer a structural target; fall back to the minimum acceptable RR only
    # when no structure exists in front of the trade.
    if primary_target is None:
        tp1 = entry + direction.sign * risk * min_rr
        target_basis = "min_rr_fallback"
    else:
        tp1 = float(primary_target)

    rr1 = abs(tp1 - entry) / risk
    if rr1 < min_rr:
        # Structure is too close to pay for the risk. This is a genuine
        # rejection, not a reason to move the target further out.
        return None
    if rr1 > max_rr:
        tp1 = entry + direction.sign * risk * max_rr
        rr1 = max_rr
        target_basis = f"{target_basis}_capped"

    targets = [tp1]
    bases = [target_basis]
    if int(geom["targets"]) > 1:
        tp2 = entry + direction.sign * risk * rr1 * float(geom["tp2_extension"])
        targets.append(tp2)
        bases.append("extension")

    return Levels(
        entry=float(entry),
        stop=float(stop),
        targets=[float(t) for t in targets],
        entry_kind=entry_kind,
        stop_basis=f"{stop_basis}:{stop_note}",
        target_basis=bases,
    )


# ---------------------------------------------------------------------------
# SWEEP_RECLAIM
# ---------------------------------------------------------------------------

def build_sweep_reclaim(ctx: MarketContext, direction: Direction) -> Levels | None:
    """Liquidity was taken and rejected; enter on the reclaim of the level."""
    geom = _geom()
    pools = ctx.cached("pools", lambda: liquidity.detect_pools(ctx))
    events = ctx.cached("liquidity_events", lambda: liquidity.detect_events(ctx, pools))
    if not events:
        return None

    n = len(ctx.structure)
    # The most recent sweep whose implication matches this direction.
    match = [
        e for e in events
        if e.kind == "sweep" and e.direction == direction.sign
        and (n - 1 - e.index) <= 12
    ]
    if not match:
        return None
    ev = match[-1]

    sigma = ctx.sigma
    offset = float(geom["entry_offset_sigma"]) * sigma
    buffer = float(geom["stop_buffer_sigma"]) * sigma
    bar = ev.index

    if direction is Direction.LONG:
        # Sweep of a low: enter on the reclaim just above the pool, stop below
        # the wick that took the liquidity.
        entry = ev.pool.price + offset
        stop = float(ctx.structure.low[bar]) - buffer
    else:
        entry = ev.pool.price - offset
        stop = float(ctx.structure.high[bar]) + buffer

    target = _opposing_pool(ctx, entry, direction)
    basis = "opposing_pool"
    if target is None:
        val = value.session_value(ctx)
        if val is not None:
            candidate = float(val["vwap"])
            if (direction is Direction.LONG and candidate > entry) or (
                direction is Direction.SHORT and candidate < entry
            ):
                target, basis = candidate, "session_value"

    return _finalise(
        ctx, direction=direction, entry=entry, stop=stop, primary_target=target,
        entry_kind="limit", stop_basis="sweep_extreme", target_basis=basis or "none",
    )


# ---------------------------------------------------------------------------
# DISPLACEMENT_CONTINUATION
# ---------------------------------------------------------------------------

def build_displacement_continuation(
    ctx: MarketContext, direction: Direction
) -> Levels | None:
    """Buy the inefficiency the displacement left behind, not the displacement."""
    geom = _geom()
    shelf = voids.find_shelf(ctx, direction.sign)
    if shelf is None:
        return None

    sigma = ctx.sigma
    buffer = float(geom["stop_buffer_sigma"]) * sigma

    # Enter at the void's midpoint - the level at which the imbalance is half
    # repaired - rather than at its edge, which frequently is not reached.
    entry = float(shelf.mid)

    # The invalidation for a continuation trade is the ORIGIN OF THE IMPULSE,
    # not the far edge of the small gap it left. A void is often a fraction of
    # a sigma wide, so stopping just beyond it produces a stop inside the
    # instrument's noise that gets floored by min_stop_sigma anyway - at which
    # point the "structural" stop is structural in name only. The swing that
    # launched the leg is the level that, if traded through, actually falsifies
    # the thesis.
    origin = _impulse_origin(ctx, shelf.index, direction)
    max_distance = float(geom["max_stop_sigma"]) * sigma
    edge = float(shelf.bottom) if direction is Direction.LONG else float(shelf.top)

    base = edge
    basis = "void_edge"
    if origin is not None:
        wider = min(edge, origin) if direction is Direction.LONG else max(edge, origin)
        # Prefer the impulse origin, but only while it still yields a tradable
        # stop. A long leg can put its origin ten sigma away; taking it there
        # would be structurally pure and economically useless, and rejecting
        # the setup outright would discard an otherwise valid continuation.
        if abs(entry - wider) - buffer <= max_distance:
            base = wider
            basis = "impulse_origin"

    stop = base - buffer if direction is Direction.LONG else base + buffer

    target = _opposing_pool(ctx, entry, direction)
    return _finalise(
        ctx, direction=direction, entry=entry, stop=stop, primary_target=target,
        entry_kind="limit", stop_basis=basis,
        target_basis="opposing_pool" if target else "none",
    )


# ---------------------------------------------------------------------------
# VALUE_FADE
# ---------------------------------------------------------------------------

def build_value_fade(ctx: MarketContext, direction: Direction) -> Levels | None:
    """Price is stretched from accepted value; fade back toward it."""
    geom = _geom()
    val = value.session_value(ctx)
    if val is None:
        return None
    dispersion = float(val["dispersion"])
    vwap = float(val["vwap"])
    if dispersion <= 0:
        return None

    price = ctx.last_price
    stretch = (price - vwap) / dispersion
    outer = float(config.indicator("SAV")["band_sigma_mult"][1])

    # A fade is only a fade if price is actually extended, and only in the
    # direction that returns toward value.
    if direction is Direction.LONG and stretch > -outer:
        return None
    if direction is Direction.SHORT and stretch < outer:
        return None

    sigma = ctx.sigma
    buffer = float(geom["stop_buffer_sigma"]) * sigma
    entry = float(price)

    # Stop beyond the recent extreme of the excursion, not a fixed multiple.
    look = min(len(ctx.structure), 20)
    if direction is Direction.LONG:
        stop = float(np.nanmin(ctx.structure.low[-look:])) - buffer
    else:
        stop = float(np.nanmax(ctx.structure.high[-look:])) + buffer

    return _finalise(
        ctx, direction=direction, entry=entry, stop=stop, primary_target=vwap,
        entry_kind="market", stop_basis="excursion_extreme",
        target_basis="session_value",
    )


# ---------------------------------------------------------------------------
# VACUUM_BREAK
# ---------------------------------------------------------------------------

def build_vacuum_break(ctx: MarketContext, direction: Direction) -> Levels | None:
    """Compression resolves; enter on the break with a measured-move target."""
    geom = _geom()
    sigma = ctx.sigma
    candles = ctx.structure
    look = min(len(candles), 24)
    if look < 8 or sigma <= 0:
        return None

    coil_high = float(np.nanmax(candles.high[-look:]))
    coil_low = float(np.nanmin(candles.low[-look:]))
    height = coil_high - coil_low
    if height <= 0:
        return None

    # Only a genuinely compressed range is a coil - but "compressed" has to be
    # judged against how wide the range would be ANYWAY over this many bars.
    # For driftless Brownian motion the expected range over n steps is
    # ~1.596*sigma*sqrt(n), which for a 24-bar window is ~7.8 sigma. Comparing
    # the raw height to a fixed sigma count (the previous 3.0) therefore
    # demanded a range less than half of normal and the setup could almost
    # never fire. The ratio below is scale-free in both volatility and window
    # length: < 1.0 is tighter than a random walk would produce, and the
    # threshold says how much tighter.
    expected_range = 1.596 * sigma * float(np.sqrt(look))
    compression = height / expected_range if expected_range > 0 else float("inf")
    if compression > float(geom.get("coil_max_compression", 0.85)):
        return None

    offset = float(geom["entry_offset_sigma"]) * sigma
    buffer = float(geom["stop_buffer_sigma"]) * sigma

    # The stop sits INSIDE the coil, not at its far side. What invalidates a
    # breakout is price re-entering the range, not traversing the whole of it -
    # and the geometry matters arithmetically: a stop at the far side spans the
    # full height, so a one-times measured move yields RR ~1.0 BY CONSTRUCTION
    # and the setup can never clear min_rr. Stopping part-way back keeps the
    # invalidation honest and the reward multiple reachable.
    retrace = float(geom.get("coil_stop_fraction", 0.5)) * height
    extension = float(geom.get("coil_extension", 1.0))
    if direction is Direction.LONG:
        entry = coil_high + offset
        stop = coil_high - retrace - buffer
        target = entry + height * extension
    else:
        entry = coil_low - offset
        stop = coil_low + retrace + buffer
        target = entry - height * extension

    pool = _opposing_pool(ctx, entry, direction)
    basis = "measured_move"
    if pool is not None:
        # Take whichever is nearer: resting liquidity in front of a measured
        # move will stop the move before it completes.
        if direction is Direction.LONG:
            if pool < target:
                target, basis = pool, "opposing_pool"
        elif pool > target:
            target, basis = pool, "opposing_pool"

    return _finalise(
        ctx, direction=direction, entry=entry, stop=stop, primary_target=target,
        entry_kind="stop", stop_basis="coil_opposite", target_basis=basis,
    )


BUILDERS = {
    Archetype.SWEEP_RECLAIM: build_sweep_reclaim,
    Archetype.DISPLACEMENT_CONTINUATION: build_displacement_continuation,
    Archetype.VALUE_FADE: build_value_fade,
    Archetype.VACUUM_BREAK: build_vacuum_break,
}


def build(ctx: MarketContext, archetype: Archetype, direction: Direction) -> Levels | None:
    builder = BUILDERS.get(archetype)
    if builder is None:
        return None
    try:
        return builder(ctx, direction)
    except (ValueError, IndexError, ZeroDivisionError):
        # Geometry that cannot be computed is a rejection, never a guess.
        return None


__all__ = [
    "build", "BUILDERS", "build_sweep_reclaim", "build_displacement_continuation",
    "build_value_fade", "build_vacuum_break",
]
