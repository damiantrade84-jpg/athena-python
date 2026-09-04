"""MUSE prisms — novel tide/undertow optics (no shared code with FABLE/GROK)."""

from __future__ import annotations

import math
from typing import Any, Sequence

from .models import Candle, Haven


def clamp(value: float, low: float = 0.0, high: float = 1.0) -> float:
    return max(low, min(high, float(value)))


def true_ranges(candles: Sequence[Candle]) -> list[float]:
    out: list[float] = []
    previous: float | None = None
    for candle in candles:
        if previous is None:
            out.append(max(candle.range, 1e-12))
        else:
            out.append(max(candle.range, abs(candle.high - previous), abs(candle.low - previous), 1e-12))
        previous = candle.close
    return out


def wilder_atr(candles: Sequence[Candle], period: int) -> float:
    period = max(2, int(period))
    ranges = true_ranges(candles)
    if len(ranges) < period:
        return 0.0
    atr = sum(ranges[:period]) / period
    for value in ranges[period:]:
        atr = ((period - 1) * atr + value) / period
    return float(atr)


def harmonic_mean(values: Sequence[float], floor: float) -> float:
    """Harmonic mean — the weakest prism dominates (stricter than averaging)."""
    if not values:
        return 0.0
    floored = [max(float(floor), clamp(v)) for v in values]
    if any(v <= 0 for v in floored):
        return 0.0
    return len(floored) / sum(1.0 / v for v in floored)


# ── undertow echo ──────────────────────────────────────────────────────────

def undertow_echo(vector: Sequence[Candle], atr: float, *, lookback: int, reclaim_bars: int,
                  min_depth_atr: float, max_reclaim_bars: int) -> dict[str, Any]:
    """Sweep echo scored by depth × reclaim velocity (not excursion alone)."""
    if atr <= 0 or len(vector) < max(6, lookback):
        return {"available": False, "direction": "NONE", "quality": 0.0, "reason": "INSUFFICIENT_VECTOR"}
    window = list(vector[-lookback:])
    base = len(vector) - len(window)
    # Prior extreme excludes the last `reclaim_bars` so the echo must reclaim.
    body = window[:-reclaim_bars] if len(window) > reclaim_bars else window
    if len(body) < 3:
        return {"available": False, "direction": "NONE", "quality": 0.0, "reason": "NO_BASELINE"}
    swing_high = max(c.high for c in body)
    swing_low = min(c.low for c in body)
    tail = window[-reclaim_bars:]
    best: dict[str, Any] | None = None
    for offset, candle in enumerate(tail):
        # Sell-side echo: dip under swing low, reclaim back above within window.
        depth = (swing_low - candle.low) / atr
        if depth >= min_depth_atr and candle.close > swing_low:
            velocity = depth / max(1, offset + 1)
            quality = clamp(0.30 + 0.45 * min(1.5, depth) / 1.5 + 0.25 * clamp(velocity / 1.2))
            candidate = {"direction": "LONG", "extreme": candle.low, "base": swing_low,
                         "depthAtr": round(depth, 4), "reclaimBars": offset + 1,
                         "velocity": round(velocity, 4), "quality": round(quality, 4),
                         "barsSince": len(tail) - 1 - offset, "index": base + len(window) - reclaim_bars + offset}
            if best is None or candidate["quality"] > best["quality"]:
                best = candidate
        depth_up = (candle.high - swing_high) / atr
        if depth_up >= min_depth_atr and candle.close < swing_high:
            velocity = depth_up / max(1, offset + 1)
            quality = clamp(0.30 + 0.45 * min(1.5, depth_up) / 1.5 + 0.25 * clamp(velocity / 1.2))
            candidate = {"direction": "SHORT", "extreme": candle.high, "base": swing_high,
                         "depthAtr": round(depth_up, 4), "reclaimBars": offset + 1,
                         "velocity": round(velocity, 4), "quality": round(quality, 4),
                         "barsSince": len(tail) - 1 - offset, "index": base + len(window) - reclaim_bars + offset}
            if best is None or candidate["quality"] > best["quality"]:
                best = candidate
    if best is None:
        return {"available": False, "direction": "NONE", "quality": 0.0, "reason": "NO_ECHO",
                "swingHigh": swing_high, "swingLow": swing_low}
    if best["reclaimBars"] > max_reclaim_bars:
        return {"available": False, "direction": best["direction"], "quality": 0.0,
                "reason": "RECLAIM_TOO_SLOW", **{k: v for k, v in best.items() if k != "quality"}}
    return {"available": True, **best}


# ── surge arc ──────────────────────────────────────────────────────────────

def surge_arc(vector: Sequence[Candle], atr: float, *, start_index: int, direction: str,
              min_run: int, body_share: float, leg_atr: float, single_leg_atr: float) -> dict[str, Any]:
    """Displacement arc after the echo: efficiency × body dominance × persistence."""
    if atr <= 0 or start_index < 0 or start_index >= len(vector):
        return {"available": False, "quality": 0.0, "reason": "BAD_ECHO_ANCHOR"}
    leg = list(vector[start_index:])
    if len(leg) < min_run:
        return {"available": False, "quality": 0.0, "reason": "LEG_TOO_SHORT"}
    sign = 1.0 if direction == "LONG" else -1.0
    net = sign * (leg[-1].close - (leg[0].open))
    path = sum(max(c.range, 1e-12) for c in leg)
    efficiency = clamp(net / path) if path > 0 else 0.0
    bodies = sorted((c.body for c in leg), reverse=True)
    run = 0
    for candle in leg:
        aligned = (candle.close > candle.open) if direction == "LONG" else (candle.close < candle.open)
        dominant = candle.body >= max(1e-12, candle.range * body_share)
        if aligned and dominant:
            run += 1
        else:
            break
    leg_range_atr = abs(leg[-1].close - leg[0].open) / atr if atr > 0 else 0.0
    single_best = max((c.range / atr for c in leg), default=0.0) if atr > 0 else 0.0
    if run < min_run and single_best < single_leg_atr:
        return {"available": False, "quality": 0.0, "reason": "NO_ARC",
                "efficiency": round(efficiency, 4), "run": run, "legAtr": round(leg_range_atr, 4)}
    if leg_range_atr < leg_atr and single_best < single_leg_atr:
        return {"available": False, "quality": 0.0, "reason": "ARC_TOO_SMALL",
                "efficiency": round(efficiency, 4), "run": run, "legAtr": round(leg_range_atr, 4)}
    quality = clamp(0.35 * efficiency + 0.35 * clamp(leg_range_atr / 1.6) + 0.30 * clamp(run / 4.0))
    leg_start = leg[0].open
    leg_end = leg[-1].close
    return {"available": True, "direction": direction, "quality": round(quality, 4),
            "efficiency": round(efficiency, 4), "run": run,
            "legStart": leg_start, "legEnd": leg_end,
            "legAtr": round(leg_range_atr, 4), "singleBestAtr": round(single_best, 4)}


# ── haven lattice ──────────────────────────────────────────────────────────

def haven_lattice(vector: Sequence[Candle], *, lookback: int, max_age_bars: int, fresh_boost: float) -> dict[str, Any]:
    """Unfilled imbalance cells with age decay; stacked fresh havens compound."""
    if len(vector) < 6:
        return {"available": False, "quality": 0.0, "reason": "INSUFFICIENT_VECTOR", "havens": []}
    window = list(vector[-lookback:])
    base = len(vector) - len(window)
    havens: list[Haven] = []
    for i in range(1, len(window) - 1):
        prev, cur, nxt = window[i - 1], window[i], window[i + 1]
        if cur.low > prev.high:  # bullish void
            havens.append(Haven(kind="void", low=prev.high, high=cur.low,
                                index=base + i, time=cur.time, age_bars=len(window) - 1 - i))
        elif cur.high < prev.low:  # bearish void
            havens.append(Haven(kind="void", low=cur.high, high=prev.low,
                                index=base + i, time=cur.time, age_bars=len(window) - 1 - i))
    # Keep only unfilled cells: price must not have crossed back through since.
    fresh: list[Haven] = []
    last_close = window[-1].close
    for haven in havens:
        if haven.age_bars > max_age_bars:
            continue
        filled = False
        for later in window[haven.index - base + 1:]:
            if haven.low <= later.close <= haven.high or (later.low <= haven.mid <= later.high):
                filled = True
                break
        if not filled:
            fresh.append(haven)
    if not fresh:
        return {"available": False, "quality": 0.0, "reason": "NO_FRESH_HAVEN",
                "havens": [], "lastClose": last_close}
    # Quality: nearest haven proximity + freshness + stacking.
    nearest = min(fresh, key=lambda h: abs(h.mid - last_close))
    price_scale = max(1e-9, abs(last_close) * 0.001)
    proximity = clamp(1.0 - abs(nearest.mid - last_close) / (price_scale * 8.0))
    freshness = clamp(1.0 - nearest.age_bars / max(1, max_age_bars))
    stacking = clamp(len(fresh) / 4.0)
    quality = clamp(0.45 * proximity + 0.35 * freshness + 0.20 * stacking + fresh_boost * freshness * 0.2)
    return {"available": True, "quality": round(quality, 4),
            "nearest": {"kind": nearest.kind, "low": nearest.low, "high": nearest.high, "mid": nearest.mid,
                        "ageBars": nearest.age_bars, "index": nearest.index},
            "count": len(fresh),
            "havens": [{"kind": h.kind, "low": h.low, "high": h.high, "mid": h.mid,
                        "ageBars": h.age_bars, "index": h.index} for h in fresh[-6:]]}


# ── compass rose ───────────────────────────────────────────────────────────

def compass_rose(current: Sequence[Candle], atr: float, *, channel: int, slope_span: int,
                 min_slope_atr: float) -> dict[str, Any]:
    """H4 trend compass: Donchian position × channel slope × expansion."""
    if atr <= 0 or len(current) < channel + 2:
        return {"available": False, "direction": "NONE", "quality": 0.0, "reason": "INSUFFICIENT_CURRENT"}
    window = list(current[-channel:])
    highest = max(c.high for c in window)
    lowest = min(c.low for c in window)
    span = max(1e-12, highest - lowest)
    last = window[-1].close
    position = clamp((last - lowest) / span)  # 0 bottom → 1 top
    recent = list(current[-slope_span:])
    slope = (recent[-1].close - recent[0].close) / max(1, len(recent) - 1) / atr if atr > 0 else 0.0
    expansion = clamp(sum(c.range for c in recent[-4:]) / max(1e-12, sum(c.range for c in recent[:4])))
    if abs(slope) < min_slope_atr:
        direction = "NONE"
        quality = clamp(0.30 + 0.20 * clamp(expansion / 2.0))
    elif slope > 0:
        direction = "LONG"
        quality = clamp(0.30 * position + 0.40 * clamp(abs(slope) / 0.6) + 0.30 * clamp(expansion / 2.0))
    else:
        direction = "SHORT"
        quality = clamp(0.30 * (1.0 - position) + 0.40 * clamp(abs(slope) / 0.6) + 0.30 * clamp(expansion / 2.0))
    return {"available": True, "direction": direction, "quality": round(quality, 4),
            "position": round(position, 4), "slopeAtr": round(slope, 4),
            "expansion": round(expansion, 4), "rangeHigh": highest, "rangeLow": lowest}


# ── halo field ─────────────────────────────────────────────────────────────

def halo_field(voices: dict[str, Any], direction: str) -> dict[str, Any]:
    """Median of available standardized voices; event veto fails closed."""
    event = voices.get("eventRisk") if isinstance(voices.get("eventRisk"), dict) else None
    if isinstance(event, dict) and event.get("allowed") is False:
        return {"quality": 0.0, "modifier": 0.88, "available": 0, "total": 6,
                "reason": str(event.get("reason") or "EVENT_RISK_VETO"), "veto": True}
    scored: list[float] = []
    detail: dict[str, Any] = {}
    for key in ("carryZ", "cotZ", "volSkewZ", "fundingZ", "sentimentZ", "trendZ"):
        raw = voices.get(key)
        if raw is None:
            continue
        try:
            z = float(raw)
        except (TypeError, ValueError):
            continue
        if not math.isfinite(z):
            continue
        aligned = z if direction == "LONG" else -z
        # Saturating map: z of ±2 → ~0.88 quality contribution.
        contribution = clamp(0.5 + 0.22 * max(-2.5, min(2.5, aligned)))
        scored.append(contribution)
        detail[key] = round(contribution, 4)
    if not scored:
        return {"quality": 0.5, "modifier": 1.0, "available": 0, "total": 6,
                "reason": "NO_HALO_VOICES", "veto": False, "detail": detail}
    ordered = sorted(scored)
    median = ordered[len(ordered) // 2]
    dissent = clamp(sum(1.0 for s in scored if s < 0.35) / max(1, len(scored)))
    return {"quality": round(median, 4), "modifier": round(0.88 + 0.18 * median, 4),
            "available": len(scored), "total": 6, "dissent": round(dissent, 4),
            "reason": None, "veto": False, "detail": detail}


def spark_confirm(spark: Sequence[Candle], atr: float, *, direction: str, recent_bars: int,
                  min_body_atr: float) -> dict[str, Any]:
    """M5 micro-reclaim recency: a fresh with-trend body confirms the release."""
    if atr <= 0 or not spark or direction == "NONE":
        return {"confirmed": False, "quality": 0.0, "reason": "NO_DIRECTION"}
    tail = list(spark[-recent_bars:])
    for offset in range(len(tail) - 1, -1, -1):
        candle = tail[offset]
        with_trend = candle.bullish if direction == "LONG" else candle.bearish
        if with_trend and candle.body / atr >= min_body_atr:
            age = len(tail) - 1 - offset
            quality = clamp(1.0 - age / max(1, recent_bars))
            return {"confirmed": True, "quality": round(quality, 4), "ageBars": age,
                    "bodyAtr": round(candle.body / atr, 4), "reason": None}
    return {"confirmed": False, "quality": 0.0, "reason": "SPARK_STALE", "ageBars": recent_bars}
