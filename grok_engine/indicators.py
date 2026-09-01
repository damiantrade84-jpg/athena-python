"""Native GROK indicators: session pools, raids, displacement, voids, CISD."""

from __future__ import annotations

import math
from typing import Any

from .config import GrokConfig
from .models import Candle
from .sessions import envelope_for_window


def clamp(value: float, low: float = 0.0, high: float = 1.0) -> float:
    return max(low, min(high, float(value)))


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


def external_liquidity(daily: list[Candle]) -> dict[str, Any]:
    if len(daily) < 2:
        return {"available": False, "reason": "INSUFFICIENT_DAILY_BARS"}
    prior = daily[-1]
    week = daily[-5:] if len(daily) >= 5 else daily
    return {
        "available": True,
        "pdh": prior.high,
        "pdl": prior.low,
        "pdc": prior.close,
        "weeklyHigh": max(candle.high for candle in week),
        "weeklyLow": min(candle.low for candle in week),
    }


def session_pools(candles: list[Candle], epoch: float, config: GrokConfig) -> dict[str, Any]:
    asia = envelope_for_window(candles, epoch, config, "asia_range")
    return {
        "asia": asia,
        "asiaHigh": asia.get("high"),
        "asiaLow": asia.get("low"),
        "asiaAvailable": bool(asia.get("available")),
    }


def raid_signature(
    candles: list[Candle],
    pools: dict[str, Any],
    external: dict[str, Any],
    *,
    atr: float,
    lookback: int,
    recent_bars: int,
    min_excursion_atr: float,
) -> dict[str, Any]:
    if len(candles) < 6 or atr <= 0:
        return {"available": False, "direction": 0, "strength": 0.0, "reason": "RAID_INPUT_UNAVAILABLE"}
    candidates: list[tuple[float, int, dict[str, Any]]] = []
    sample = candles[-max(8, lookback) :]
    pool_lows = [value for value in (pools.get("asiaLow"), external.get("pdl"), external.get("weeklyLow")) if isinstance(value, (int, float))]
    pool_highs = [value for value in (pools.get("asiaHigh"), external.get("pdh"), external.get("weeklyHigh")) if isinstance(value, (int, float))]
    for offset, candle in enumerate(sample):
        age = len(sample) - 1 - offset
        for pool in pool_lows:
            if candle.low < pool <= candle.close:
                excursion = (pool - candle.low) / atr
                if excursion < min_excursion_atr:
                    continue
                reclaim = clamp((candle.close - pool) / max(atr * 0.35, 1e-12))
                recency = clamp(1.0 - age / max(recent_bars, 1))
                strength = clamp(0.48 * clamp(excursion / 0.45) + 0.32 * reclaim + 0.20 * recency)
                candidates.append(
                    (
                        strength,
                        1,
                        {
                            "kind": "SELL_SIDE_RAID",
                            "pool": pool,
                            "extreme": candle.low,
                            "reclaimClose": candle.close,
                            "excursionAtr": excursion,
                            "eventIndex": len(candles) - len(sample) + offset,
                            "eventAgeBars": age,
                        },
                    )
                )
        for pool in pool_highs:
            if candle.high > pool >= candle.close:
                excursion = (candle.high - pool) / atr
                if excursion < min_excursion_atr:
                    continue
                reclaim = clamp((pool - candle.close) / max(atr * 0.35, 1e-12))
                recency = clamp(1.0 - age / max(recent_bars, 1))
                strength = clamp(0.48 * clamp(excursion / 0.45) + 0.32 * reclaim + 0.20 * recency)
                candidates.append(
                    (
                        strength,
                        -1,
                        {
                            "kind": "BUY_SIDE_RAID",
                            "pool": pool,
                            "extreme": candle.high,
                            "reclaimClose": candle.close,
                            "excursionAtr": excursion,
                            "eventIndex": len(candles) - len(sample) + offset,
                            "eventAgeBars": age,
                        },
                    )
                )
    if not candidates:
        return {"available": False, "direction": 0, "strength": 0.0, "reason": "NO_RAID"}
    max_age = max(1, recent_bars)
    recent = [item for item in candidates if int(item[2]["eventAgeBars"]) <= max_age]
    if not recent:
        oldest = max(candidates, key=lambda item: int(item[2]["eventAgeBars"]))
        return {
            "available": False,
            "direction": 0,
            "strength": 0.0,
            "reason": "RAID_NOT_RECENT",
            "eventAgeBars": int(oldest[2]["eventAgeBars"]),
        }
    strength, direction, evidence = max(
        recent,
        key=lambda item: (-int(item[2]["eventAgeBars"]), item[0]),
    )
    return {"available": True, "direction": direction, "strength": strength, **evidence}


def impulse_vector(
    candles: list[Candle],
    *,
    atr: float,
    min_run: int,
    body_fraction: float,
    range_atr: float,
    single_range_atr: float,
    required_direction: int = 0,
) -> dict[str, Any]:
    if len(candles) < 5 or atr <= 0:
        return {"available": False, "direction": 0, "strength": 0.0, "reason": "IMPULSE_INPUT_UNAVAILABLE"}

    def _expansive(candle: Candle) -> bool:
        return candle.range >= atr * range_atr and candle.body >= candle.range * body_fraction

    sample = candles[-16:]
    sample_offset = len(candles) - len(sample)
    best: dict[str, Any] | None = None
    index = 0
    while index < len(sample):
        candle = sample[index]
        if not _expansive(candle) or candle.direction == 0:
            index += 1
            continue
        run = [candle]
        cursor = index + 1
        while (
            cursor < len(sample)
            and sample[cursor].direction in {0, candle.direction}
            and _expansive(sample[cursor])
        ):
            run.append(sample[cursor])
            cursor += 1
        if len(run) < min_run:
            index = max(cursor, index + 1)
            continue
        used_direction = candle.direction
        if required_direction and used_direction != required_direction:
            index = max(cursor, index + 1)
            continue
        origin = run[0].low if used_direction > 0 else run[0].high
        terminus = run[-1].high if used_direction > 0 else run[-1].low
        span = abs(terminus - origin)
        efficiency = clamp(sum(row.body for row in run) / max(span, 1e-12))
        strength = clamp(0.45 * clamp(span / (atr * 1.8)) + 0.35 * efficiency + 0.20 * clamp(len(run) / 4.0))
        age = len(sample) - cursor
        candidate = {
            "available": True,
            "direction": used_direction,
            "strength": strength,
            "origin": origin,
            "terminus": terminus,
            "spanAtr": span / atr,
            "efficiency": efficiency,
            "bars": len(run),
            "ageBars": age,
            "startIndex": sample_offset + index,
            "endIndex": sample_offset + cursor - 1,
            "_rank": (sample_offset + cursor - 1, len(run), strength),
        }
        if best is None or tuple(candidate["_rank"]) > tuple(best.get("_rank") or (-1, 0, 0.0)):
            best = candidate
        index = max(cursor, index + 1)

    singles = [
        (single_index, candle)
        for single_index, candle in enumerate(sample)
        if candle.range >= atr * single_range_atr and candle.body >= candle.range * body_fraction
        and (not required_direction or candle.direction == required_direction)
    ]
    if singles:
        single_index, single = singles[-1]
        used_direction = single.direction or (1 if single.close >= single.open else -1)
        span = single.range
        efficiency = clamp(single.body / max(span, 1e-12))
        strength = clamp(0.50 * clamp(span / (atr * 1.8)) + 0.35 * efficiency + 0.15)
        candidate = {
            "available": True,
            "direction": used_direction,
            "strength": strength,
            "origin": single.low if used_direction > 0 else single.high,
            "terminus": single.high if used_direction > 0 else single.low,
            "spanAtr": span / atr,
            "efficiency": efficiency,
            "bars": 1,
            "ageBars": len(sample) - 1 - single_index,
            "startIndex": sample_offset + single_index,
            "endIndex": sample_offset + single_index,
            "_rank": (sample_offset + single_index, 1, strength),
        }
        if best is None or tuple(candidate["_rank"]) > tuple(best.get("_rank") or (-1, 0, 0.0)):
            best = candidate

    if best is None:
        reason = "NO_ALIGNED_DISPLACEMENT" if required_direction else "NO_DISPLACEMENT"
        return {"available": False, "direction": 0, "strength": 0.0, "reason": reason}
    best.pop("_rank", None)
    return best


def void_map(
    candles: list[Candle],
    *,
    lookback: int,
    atr: float,
    direction: int = 0,
    minimum_index: int | None = None,
) -> dict[str, Any]:
    if len(candles) < 6 or atr <= 0:
        return {"available": False, "direction": 0, "strength": 0.0, "reason": "VOID_INPUT_UNAVAILABLE"}
    start = max(2, len(candles) - lookback)
    voids: list[dict[str, Any]] = []
    for index in range(start, len(candles)):
        left = candles[index - 2]
        right = candles[index]
        if left.high < right.low:
            low, high, direction = left.high, right.low, 1
        elif left.low > right.high:
            low, high, direction = right.high, left.low, -1
        else:
            continue
        width = high - low
        if width < atr * 0.08:
            continue
        subsequent = candles[index + 1 :]
        if direction > 0:
            fill_extreme = min((candle.low for candle in subsequent), default=high)
            filled = max(0.0, high - fill_extreme)
        else:
            fill_extreme = max((candle.high for candle in subsequent), default=low)
            filled = max(0.0, fill_extreme - low)
        fill_fraction = clamp(filled / max(width, 1e-12))
        ce = (high + low) / 2.0
        last = candles[-1].close
        inside = low <= last <= high
        voids.append(
            {
                "direction": direction,
                "low": low,
                "high": high,
                "ce": ce,
                "widthAtr": width / atr,
                "fillFraction": fill_fraction,
                "open": fill_fraction < 0.999,
                "inside": inside,
                "ageBars": len(candles) - 1 - index,
                "index": index,
            }
        )
    if not voids:
        return {"available": False, "direction": 0, "strength": 0.0, "reason": "NO_VOID"}
    eligible = [
        row
        for row in voids
        if (direction == 0 or int(row["direction"]) == direction)
        and (minimum_index is None or int(row["index"]) >= minimum_index)
    ]
    if not eligible:
        return {"available": False, "direction": direction, "strength": 0.0, "reason": "NO_CAUSAL_VOID"}
    open_voids = [row for row in eligible if row["open"]]
    chosen = min(open_voids or eligible, key=lambda row: (row["ageBars"], -row["widthAtr"]))
    location = 1.0 if chosen["inside"] else clamp(1.0 - abs(candles[-1].close - chosen["ce"]) / max(atr * 1.6, 1e-12))
    freshness = clamp(1.0 - chosen["ageBars"] / 18.0)
    openness = 1.0 - chosen["fillFraction"]
    strength = clamp(0.42 * location + 0.33 * openness + 0.25 * freshness)
    return {"available": True, "strength": strength, **chosen}


def dealing_range(candles: list[Candle], *, lookback: int, ote_inner: float, ote_outer: float) -> dict[str, Any]:
    if len(candles) < 12:
        return {"available": False, "reason": "DEALING_RANGE_UNAVAILABLE"}
    sample = candles[-lookback:]
    high = max(candle.high for candle in sample)
    low = min(candle.low for candle in sample)
    width = high - low
    if width <= 0:
        return {"available": False, "reason": "DEALING_RANGE_FLAT"}
    close = sample[-1].close
    position = (close - low) / width
    long_ote_low = high - ote_outer * width
    long_ote_high = high - ote_inner * width
    short_ote_low = low + ote_inner * width
    short_ote_high = low + ote_outer * width
    long_in_ote = long_ote_low <= close <= long_ote_high
    short_in_ote = short_ote_low <= close <= short_ote_high
    return {
        "available": True,
        "high": high,
        "low": low,
        "mid": (high + low) / 2.0,
        "position": position,
        "discount": position <= 0.50,
        "premium": position >= 0.50,
        "inOte": long_in_ote or short_in_ote,
        "longInOte": long_in_ote,
        "shortInOte": short_in_ote,
        "oteLow": long_ote_low,
        "oteHigh": long_ote_high,
        "longOteLow": long_ote_low,
        "longOteHigh": long_ote_high,
        "shortOteLow": short_ote_low,
        "shortOteHigh": short_ote_high,
    }


def cisd_state(
    candles: list[Candle],
    raid: dict[str, Any],
    impulse: dict[str, Any],
    *,
    lookback: int,
) -> dict[str, Any]:
    if len(candles) < 8:
        return {"available": False, "confirmed": False, "strength": 0.0, "reason": "CISD_INPUT_UNAVAILABLE"}
    direction = int(raid.get("direction") or impulse.get("direction") or 0)
    if direction == 0:
        return {"available": False, "confirmed": False, "strength": 0.0, "reason": "CISD_DIRECTION_UNRESOLVED"}
    raid_index = raid.get("eventIndex")
    impulse_start = impulse.get("startIndex")
    impulse_end = impulse.get("endIndex")
    if not all(isinstance(value, int) for value in (raid_index, impulse_start, impulse_end)):
        return {"available": False, "confirmed": False, "strength": 0.0, "reason": "CISD_SEQUENCE_UNAVAILABLE"}
    if int(impulse_start) < int(raid_index) or int(impulse_end) < int(impulse_start):
        return {"available": False, "confirmed": False, "strength": 0.0, "reason": "CISD_SEQUENCE_INVALID"}
    origin_start = max(0, int(impulse_start) - lookback)
    opposite = [
        (index, candles[index])
        for index in range(origin_start, int(impulse_start))
        if candles[index].direction == -direction
    ]
    if not opposite:
        return {"available": False, "confirmed": False, "strength": 0.0, "reason": "CISD_ORIGIN_UNAVAILABLE"}
    origin_index, origin_candle = opposite[-1]
    origin = origin_candle.open
    confirmation_start = int(impulse_end)
    confirmation_index = None
    for index in range(confirmation_start, len(candles)):
        close = candles[index].close
        if (direction > 0 and close > float(origin)) or (direction < 0 and close < float(origin)):
            confirmation_index = index
            break
    last = candles[-1]
    confirmed = confirmation_index is not None
    forming = ((last.high > float(origin)) if direction > 0 else (last.low < float(origin))) and not confirmed
    strength = 1.0 if confirmed else 0.42 if forming else 0.0
    return {
        "available": True,
        "confirmed": confirmed,
        "forming": forming,
        "strength": strength,
        "origin": float(origin),
        "originIndex": origin_index,
        "eventIndex": confirmation_index,
        "direction": direction,
    }


def dealing_quality(dealing: dict[str, Any], direction: int) -> float:
    if not dealing.get("available") or direction == 0:
        return 0.0
    raw_position = dealing.get("position")
    position = float(raw_position) if isinstance(raw_position, (int, float)) else 0.5
    if direction > 0:
        if dealing.get("longInOte"):
            return 1.0
        return clamp(1.0 - position / 0.72)
    if dealing.get("shortInOte"):
        return 1.0
    return clamp(position / 0.72)


def ema_last(values: list[float], period: int) -> float:
    """SMA-seeded exponential moving average of the series tail."""
    period = max(2, int(period))
    if len(values) < period:
        return 0.0
    ema = sum(values[:period]) / period
    k = 2.0 / (period + 1.0)
    for value in values[period:]:
        ema = value * k + ema * (1.0 - k)
    return float(ema)


def intraday_trend_bias(
    candles: list[Candle],
    *,
    atr: float,
    fast_period: int,
    slow_period: int,
    min_separation_atr: float,
    require_price_side: bool,
) -> dict[str, Any]:
    """H1 EMA-stack intraday bias used by the counter-trend hard gate.

    Direction is +1/-1 only when the fast/slow separation is decisive
    (>= min_separation_atr * ATR) and, optionally, the last close sits on the
    trend side of the fast EMA. Anything else is neutral (0) so chop keeps
    both directions eligible. Fail-closed on insufficient input.
    """
    fast_period = int(fast_period)
    slow_period = int(slow_period)
    needed = max(slow_period, fast_period, 2)
    if len(candles) < needed or atr <= 0:
        return {
            "available": False,
            "direction": 0,
            "strength": 0.0,
            "reason": "BIAS_INPUT_UNAVAILABLE",
        }
    closes = [float(candle.close) for candle in candles]
    fast = ema_last(closes, fast_period)
    slow = ema_last(closes, slow_period)
    if fast <= 0 or slow <= 0:
        return {
            "available": False,
            "direction": 0,
            "strength": 0.0,
            "reason": "BIAS_INPUT_UNAVAILABLE",
        }
    separation_atr = (fast - slow) / atr
    last_close = closes[-1]
    threshold = float(min_separation_atr)
    if separation_atr >= threshold and (not require_price_side or last_close > fast):
        direction = 1
    elif separation_atr <= -threshold and (not require_price_side or last_close < fast):
        direction = -1
    else:
        direction = 0
    strength = clamp(abs(separation_atr) / max(threshold * 3.0, 1e-12)) if direction else 0.0
    return {
        "available": True,
        "direction": direction,
        "strength": strength,
        "fast": fast,
        "slow": slow,
        "separationAtr": separation_atr,
        "lastClose": last_close,
        "reason": None,
    }
