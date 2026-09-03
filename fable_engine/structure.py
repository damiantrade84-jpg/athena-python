"""Liquidity and market-structure primitives owned by FABLE.

Everything here is causal: a function only ever looks at bars up to and
including the index it is asked about, so the same code drives the live scan
and the closed-prefix chronicle replay.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import math
from typing import Sequence

from .models import Candle, Imbalance, LiquidityPool, Raid, Shift, Swing


# ── volatility ──────────────────────────────────────────────────────────────


def true_ranges(candles: Sequence[Candle]) -> list[float]:
    out: list[float] = []
    previous_close: float | None = None
    for candle in candles:
        if previous_close is None:
            out.append(candle.range)
        else:
            out.append(max(candle.range, abs(candle.high - previous_close), abs(candle.low - previous_close)))
        previous_close = candle.close
    return out


def atr_series(candles: Sequence[Candle], period: int) -> list[float | None]:
    """Wilder ATR; ``None`` until the seed window is complete."""
    ranges = true_ranges(candles)
    out: list[float | None] = [None] * len(candles)
    if len(ranges) < period or period <= 0:
        return out
    seed = sum(ranges[:period]) / period
    out[period - 1] = seed
    current = seed
    for index in range(period, len(ranges)):
        current = (current * (period - 1) + ranges[index]) / period
        out[index] = current
    return out


def atr_at(candles: Sequence[Candle], period: int, index: int | None = None) -> float | None:
    series = atr_series(candles, period)
    if not series:
        return None
    position = len(series) - 1 if index is None else index
    if position < 0 or position >= len(series):
        return None
    return series[position]


# ── swings ──────────────────────────────────────────────────────────────────


def fractal_swings(candles: Sequence[Candle], strength: int, *, end: int | None = None) -> list[Swing]:
    """Confirmed fractal pivots with ``strength`` bars on each side.

    A pivot at index ``i`` is only known once bar ``i + strength`` has closed,
    so the newest ``strength`` bars can never host a swing. ``end`` (exclusive)
    limits the search to a causal prefix.
    """
    limit = len(candles) if end is None else max(0, min(end, len(candles)))
    swings: list[Swing] = []
    for index in range(strength, limit - strength):
        candle = candles[index]
        window = candles[index - strength : index + strength + 1]
        if all(candle.high >= other.high for other in window) and sum(
            1 for other in window if other.high == candle.high
        ) == 1:
            swings.append(Swing(index, candle.time, candle.high, "high"))
        if all(candle.low <= other.low for other in window) and sum(
            1 for other in window if other.low == candle.low
        ) == 1:
            swings.append(Swing(index, candle.time, candle.low, "low"))
    return swings


def swing_sequence_bias(swings: Sequence[Swing], *, count: int = 3) -> tuple[str, float]:
    """Return (bias, strength) from the last ``count`` highs and lows.

    Higher highs + higher lows -> LONG, lower highs + lower lows -> SHORT.
    Strength is the share of consecutive comparisons that agree with the bias.
    """
    highs = [swing.price for swing in swings if swing.kind == "high"][-count:]
    lows = [swing.price for swing in swings if swing.kind == "low"][-count:]
    if len(highs) < 2 or len(lows) < 2:
        return "NONE", 0.0
    up = down = total = 0
    for series in (highs, lows):
        for previous, current in zip(series, series[1:]):
            total += 1
            if current > previous:
                up += 1
            elif current < previous:
                down += 1
    if total == 0:
        return "NONE", 0.0
    if up > down:
        return "LONG", up / total
    if down > up:
        return "SHORT", down / total
    return "NONE", 0.0


# ── liquidity pools ─────────────────────────────────────────────────────────


def _ny_day_key(epoch: float, zone) -> tuple[int, int, int]:
    local = datetime.fromtimestamp(epoch, tz=timezone.utc).astimezone(zone)
    return (local.year, local.month, local.day)


def _ny_week_key(epoch: float, zone) -> tuple[int, int]:
    local = datetime.fromtimestamp(epoch, tz=timezone.utc).astimezone(zone)
    iso = local.isocalendar()
    return (iso[0], iso[1])


def session_extremes(
    m15: Sequence[Candle],
    *,
    as_of_epoch: float,
    zone,
) -> list[LiquidityPool]:
    """Previous-day and previous-week highs/lows measured on the narrative series."""
    if not m15:
        return []
    today = _ny_day_key(as_of_epoch, zone)
    this_week = _ny_week_key(as_of_epoch, zone)
    days: dict[tuple[int, int, int], list[Candle]] = {}
    weeks: dict[tuple[int, int], list[Candle]] = {}
    for candle in m15:
        days.setdefault(_ny_day_key(candle.time, zone), []).append(candle)
        weeks.setdefault(_ny_week_key(candle.time, zone), []).append(candle)
    pools: list[LiquidityPool] = []
    previous_days = sorted(key for key in days if key < today)
    if previous_days:
        bars = days[previous_days[-1]]
        high = max(bars, key=lambda item: item.high)
        low = min(bars, key=lambda item: item.low)
        pools.append(LiquidityPool(high.high, "buyside", "PDH", 0.85, high.time))
        pools.append(LiquidityPool(low.low, "sellside", "PDL", 0.85, low.time))
    previous_weeks = sorted(key for key in weeks if key < this_week)
    if previous_weeks:
        bars = weeks[previous_weeks[-1]]
        high = max(bars, key=lambda item: item.high)
        low = min(bars, key=lambda item: item.low)
        pools.append(LiquidityPool(high.high, "buyside", "PWH", 1.0, high.time))
        pools.append(LiquidityPool(low.low, "sellside", "PWL", 1.0, low.time))
    return pools


def swing_pools(
    candles: Sequence[Candle],
    *,
    strength: int,
    lookback: int,
    source: str,
    base_strength: float,
    atr: float,
    equal_tolerance_atr: float,
    end: int | None = None,
) -> list[LiquidityPool]:
    """Swing highs/lows as pools, merging equal highs/lows into stronger pools."""
    limit = len(candles) if end is None else min(end, len(candles))
    start = max(0, limit - lookback)
    swings = [swing for swing in fractal_swings(candles, strength, end=limit) if swing.index >= start]
    tolerance = max(1e-12, atr * equal_tolerance_atr)
    pools: list[LiquidityPool] = []
    for kind, side in (("high", "buyside"), ("low", "sellside")):
        levels = [swing for swing in swings if swing.kind == kind]
        used: set[int] = set()
        for swing in levels:
            if swing.index in used:
                continue
            cluster = [other for other in levels if abs(other.price - swing.price) <= tolerance]
            for other in cluster:
                used.add(other.index)
            price = max(item.price for item in cluster) if kind == "high" else min(item.price for item in cluster)
            touches = len(cluster)
            label = f"{source}_swing" if touches == 1 else ("EQH" if kind == "high" else "EQL")
            strength_value = min(1.0, base_strength + 0.15 * (touches - 1))
            newest = max(item.time for item in cluster)
            pools.append(LiquidityPool(price, side, label, strength_value, newest, touches))
    return pools


def dedupe_pools(pools: Sequence[LiquidityPool], *, tolerance: float) -> list[LiquidityPool]:
    """Collapse pools within ``tolerance`` of each other, keeping the strongest."""
    ordered = sorted(pools, key=lambda pool: (-pool.strength, -pool.time))
    kept: list[LiquidityPool] = []
    for pool in ordered:
        if any(other.side == pool.side and abs(other.price - pool.price) <= tolerance for other in kept):
            continue
        kept.append(pool)
    return sorted(kept, key=lambda pool: pool.price)


# ── raid detection ──────────────────────────────────────────────────────────


def _participation_z(candles: Sequence[Candle], index: int, baseline: int) -> float | None:
    """Volume z-score of bar ``index`` against the preceding ``baseline`` bars."""
    if index <= 0:
        return None
    history = [candle.volume for candle in candles[max(0, index - baseline) : index] if candle.volume is not None]
    current = candles[index].volume
    if current is None or len(history) < max(8, baseline // 4):
        return None
    mean = sum(history) / len(history)
    variance = sum((value - mean) ** 2 for value in history) / len(history)
    deviation = math.sqrt(variance)
    if deviation <= 1e-12:
        return None
    return (current - mean) / deviation


def find_raids(
    m15: Sequence[Candle],
    pools: Sequence[LiquidityPool],
    *,
    atr: float,
    lookback: int,
    max_excursion_bars: int,
    min_depth_atr: float,
    max_depth_atr: float,
    participation_baseline: int,
    end: int | None = None,
) -> list[Raid]:
    """Every pool sweep inside ``lookback`` bars that closed back through the pool.

    A raid may take up to ``max_excursion_bars`` to run through the pool and
    reclaim it; the reclaim bar's close must be back on the pre-raid side.
    Sweeps deeper than ``max_depth_atr`` are treated as breakouts, not raids.
    """
    limit = len(m15) if end is None else min(end, len(m15))
    if limit < 3 or atr <= 0:
        return []
    start = max(1, limit - lookback)
    raids: list[Raid] = []
    for pool in pools:
        for reclaim_index in range(start, limit):
            bar = m15[reclaim_index]
            if pool.side == "buyside":
                if not (bar.close < pool.price):
                    continue
                window = m15[max(0, reclaim_index - max_excursion_bars + 1) : reclaim_index + 1]
                extreme = max(item.high for item in window)
                if extreme <= pool.price:
                    continue
                # the bar before the excursion window must have been below the pool
                first_index = reclaim_index - len(window) + 1
                pre_bar = m15[first_index - 1] if first_index - 1 >= 0 else None
                if pre_bar is not None and pre_bar.close > pool.price:
                    continue
                depth = (extreme - pool.price) / atr
                reclaim = (pool.price - bar.close) / atr
                direction = "SHORT"
            else:
                if not (bar.close > pool.price):
                    continue
                window = m15[max(0, reclaim_index - max_excursion_bars + 1) : reclaim_index + 1]
                extreme = min(item.low for item in window)
                if extreme >= pool.price:
                    continue
                first_index = reclaim_index - len(window) + 1
                pre_bar = m15[first_index - 1] if first_index - 1 >= 0 else None
                if pre_bar is not None and pre_bar.close < pool.price:
                    continue
                depth = (pool.price - extreme) / atr
                reclaim = (bar.close - pool.price) / atr
                direction = "LONG"
            if depth < min_depth_atr or depth > max_depth_atr:
                continue
            if pool.time >= bar.time:
                continue  # the pool must exist before it is raided
            start_index = min(
                (index for index in range(first_index, reclaim_index + 1)
                 if (m15[index].high > pool.price if pool.side == "buyside" else m15[index].low < pool.price)),
                default=reclaim_index,
            )
            raids.append(
                Raid(
                    pool=pool,
                    direction=direction,
                    start_index=start_index,
                    reclaim_index=reclaim_index,
                    extreme=extreme,
                    depth_atr=depth,
                    reclaim_atr=reclaim,
                    bars_since=limit - 1 - reclaim_index,
                    participation_z=_participation_z(m15, start_index, participation_baseline),
                )
            )
            break  # one raid per pool: the first reclaim inside the lookback
    raids.sort(key=lambda raid: (raid.reclaim_index, raid.pool.strength), reverse=True)
    return raids


# ── displacement / market structure shift ───────────────────────────────────


def fair_value_gaps(candles: Sequence[Candle], start: int, end: int, direction: str) -> list[Imbalance]:
    """Three-bar imbalances between ``start`` and ``end`` (inclusive) in the leg direction."""
    gaps: list[Imbalance] = []
    for index in range(max(start + 1, 1), min(end, len(candles) - 1)):
        before, after = candles[index - 1], candles[index + 1]
        if direction == "LONG" and after.low > before.high:
            gaps.append(Imbalance("fvg", before.high, after.low, index, candles[index].time))
        elif direction == "SHORT" and after.high < before.low:
            gaps.append(Imbalance("fvg", after.high, before.low, index, candles[index].time))
    return gaps


def order_block(candles: Sequence[Candle], start: int, break_index: int, direction: str) -> Imbalance | None:
    """Last opposing candle before the displacement that broke structure."""
    for index in range(break_index - 1, max(start - 1, -1), -1):
        candle = candles[index]
        if direction == "LONG" and candle.bearish:
            return Imbalance("order_block", candle.low, candle.high, index, candle.time)
        if direction == "SHORT" and candle.bullish:
            return Imbalance("order_block", candle.low, candle.high, index, candle.time)
    return None


def find_shift(
    m15: Sequence[Candle],
    raid: Raid,
    *,
    atr: float,
    swing_strength: int,
    min_displacement_atr: float,
    min_body_atr: float,
    max_bars_after_raid: int,
    participation_baseline: int,
    end: int | None = None,
) -> Shift | None:
    """Detect the structure shift that follows ``raid``.

    LONG narrative: after a sellside raid, price must close above the most
    recent swing high formed before the raid reclaim (the short-term high that
    defines the bearish leg). The displacement leg runs from the raid extreme
    to the highest high after the break and must travel ``min_displacement_atr``
    with at least one bar body of ``min_body_atr``. SHORT mirrors this.
    """
    limit = len(m15) if end is None else min(end, len(m15))
    if atr <= 0 or raid.reclaim_index >= limit - 1:
        return None
    direction = raid.direction
    swings = fractal_swings(m15, swing_strength, end=raid.reclaim_index + 1)
    if direction == "LONG":
        candidates = [swing for swing in swings if swing.kind == "high" and swing.index < raid.reclaim_index]
    else:
        candidates = [swing for swing in swings if swing.kind == "low" and swing.index < raid.reclaim_index]
    if not candidates:
        # Fall back to the highest high / lowest low between the last 12 bars before the raid.
        window_start = max(0, raid.start_index - 12)
        window = m15[window_start : raid.start_index + 1]
        if not window:
            return None
        if direction == "LONG":
            best = max(range(len(window)), key=lambda offset: window[offset].high)
            candidates = [Swing(window_start + best, window[best].time, window[best].high, "high")]
        else:
            best = min(range(len(window)), key=lambda offset: window[offset].low)
            candidates = [Swing(window_start + best, window[best].time, window[best].low, "low")]
    target = candidates[-1]
    break_index: int | None = None
    last_index = min(limit - 1, raid.reclaim_index + max_bars_after_raid)
    for index in range(raid.reclaim_index, last_index + 1):
        candle = m15[index]
        if direction == "LONG" and candle.close > target.price:
            break_index = index
            break
        if direction == "SHORT" and candle.close < target.price:
            break_index = index
            break
    if break_index is None:
        return None
    # Displacement extreme after the break, bounded by the causal prefix.
    if direction == "LONG":
        leg_end_index = max(range(break_index, limit), key=lambda idx: m15[idx].high)
        leg_end = m15[leg_end_index].high
        # Invalidate if price later closed back below the raid extreme.
        if any(m15[idx].close < raid.extreme for idx in range(break_index, limit)):
            return None
    else:
        leg_end_index = min(range(break_index, limit), key=lambda idx: m15[idx].low)
        leg_end = m15[leg_end_index].low
        if any(m15[idx].close > raid.extreme for idx in range(break_index, limit)):
            return None
    displacement = abs(leg_end - raid.extreme) / atr
    bodies = [m15[idx].body / atr for idx in range(raid.start_index, leg_end_index + 1)]
    max_body = max(bodies) if bodies else 0.0
    if displacement < min_displacement_atr or max_body < min_body_atr:
        return None
    gaps = fair_value_gaps(m15, raid.start_index, leg_end_index, direction)
    block = order_block(m15, raid.start_index, break_index, direction)
    imbalances: list[Imbalance] = list(gaps)
    if block is not None:
        imbalances.append(block)
    return Shift(
        direction=direction,
        broken_level=target.price,
        broken_swing_index=target.index,
        break_index=break_index,
        leg_start=raid.extreme,
        leg_end=leg_end,
        leg_end_index=leg_end_index,
        displacement_atr=displacement,
        max_body_atr=max_body,
        imbalances=tuple(imbalances),
        participation_z=_participation_z(m15, break_index, participation_baseline),
    )


# ── premium / discount and retracement ──────────────────────────────────────


def retracement(shift: Shift, price: float) -> float:
    """Fraction of the displacement leg retraced by ``price`` (0 = leg end, 1 = raid extreme)."""
    span = shift.leg_range
    if span <= 0:
        return 0.0
    if shift.direction == "LONG":
        return max(0.0, min(1.5, (shift.leg_end - price) / span))
    return max(0.0, min(1.5, (price - shift.leg_end) / span))


def dealing_range(candles: Sequence[Candle], strength: int, *, end: int | None = None) -> tuple[float, float] | None:
    """Latest confirmed swing low/high pair on the draw series (D1)."""
    swings = fractal_swings(candles, strength, end=end)
    highs = [swing for swing in swings if swing.kind == "high"]
    lows = [swing for swing in swings if swing.kind == "low"]
    if not highs or not lows:
        return None
    high = highs[-1].price
    low = lows[-1].price
    if high <= low:
        # Use the widest recent envelope when the last swings cross.
        high = max(swing.price for swing in highs[-3:])
        low = min(swing.price for swing in lows[-3:])
        if high <= low:
            return None
    return low, high


def efficiency_ratio(candles: Sequence[Candle], window: int, *, end: int | None = None) -> float | None:
    """Kaufman efficiency ratio of closes over ``window`` bars, signed by net direction."""
    limit = len(candles) if end is None else min(end, len(candles))
    if limit <= window:
        return None
    closes = [candle.close for candle in candles[limit - window - 1 : limit]]
    net = closes[-1] - closes[0]
    path = sum(abs(current - previous) for previous, current in zip(closes, closes[1:]))
    if path <= 0:
        return 0.0
    return max(-1.0, min(1.0, net / path))


def percentile_rank(values: Sequence[float], value: float) -> float | None:
    clean = [item for item in values if item is not None and math.isfinite(item)]
    if len(clean) < 10:
        return None
    below = sum(1 for item in clean if item < value)
    return below / len(clean)


def ny_zone(name: str, fallback_hours: float):
    from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

    try:
        return ZoneInfo(name)
    except ZoneInfoNotFoundError:
        return timezone(timedelta(hours=fallback_hours))
