"""Pure Engine B Fair Value Gap and Breakaway Gap lifecycle helpers.

The helpers in this module are deliberately independent from the runtime monolith so
live scans and historical replay can consume the same deterministic candle rules.
"""

from __future__ import annotations

from typing import Any


def _number(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def enrich_fvg_lifecycle(
    fvgs: list[dict[str, Any]],
    candles: list[dict[str, Any]],
    *,
    atr: float = 0.0,
    timeframe: str | None = None,
    config: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Attach fill, displacement, and BAG state to detected three-candle FVGs.

    BAG is a state transition, not a new gap geometry. A newly formed FVG can be a
    candidate; confirmation requires structure-breaking displacement, follow-through,
    and a gap that remains materially open. A 50% retrace invalidates BAG status but
    remains the ordinary FVG consequent-encroachment mitigation rule.
    """

    cfg = config if isinstance(config, dict) else {}
    atr_value = max(0.0, _number(atr))
    min_body_atr = max(0.0, _number(cfg.get("BAG_MIN_DISPLACEMENT_BODY_ATR"), 0.8))
    min_body_ratio = max(0.0, min(1.0, _number(cfg.get("BAG_MIN_BODY_RANGE_RATIO"), 0.65)))
    min_gap_atr = max(0.0, _number(cfg.get("FVG_MIN_SIZE_ATR"), 0.05))
    breakout_lookback = max(3, int(_number(cfg.get("BAG_BREAKOUT_LOOKBACK_BARS"), 8)))
    followthrough_bars = max(1, int(_number(cfg.get("BAG_MIN_FOLLOWTHROUGH_BARS"), 2)))
    continuation_atr = max(0.0, _number(cfg.get("BAG_MIN_CONTINUATION_ATR"), 0.5))
    max_fill = max(0.0, min(1.0, _number(cfg.get("BAG_MAX_FILL_FRACTION"), 0.25)))

    enriched: list[dict[str, Any]] = []
    for source in fvgs:
        item = dict(source)
        direction = str(item.get("type") or "").lower()
        top = _number(item.get("top"))
        bottom = _number(item.get("bottom"))
        size = max(0.0, top - bottom)
        middle_index = int(_number(item.get("bar_index"), -1))
        third_index = middle_index + 1
        post = candles[third_index + 1 :] if 0 <= third_index < len(candles) else []

        fill_fraction = 0.0
        if size > 0 and post:
            if direction == "bullish":
                deepest = min(_number(c.get("low"), top) for c in post)
                fill_fraction = (top - deepest) / size
            elif direction == "bearish":
                deepest = max(_number(c.get("high"), bottom) for c in post)
                fill_fraction = (deepest - bottom) / size
        fill_fraction = max(0.0, min(1.0, fill_fraction))
        ce = bottom + size * 0.5
        if fill_fraction <= 0:
            fill_state = "unfilled"
        elif fill_fraction < 0.5:
            fill_state = "partial"
        elif fill_fraction < 1.0:
            fill_state = "ce_touched"
        else:
            fill_state = "full"

        displacement_body_atr = 0.0
        displacement_body_ratio = 0.0
        structure_break = False
        if 0 <= middle_index < len(candles):
            middle = candles[middle_index]
            open_ = _number(middle.get("open"))
            close = _number(middle.get("close"))
            high = _number(middle.get("high"))
            low = _number(middle.get("low"))
            body = abs(close - open_)
            range_ = max(high - low, 0.0)
            displacement_body_atr = body / atr_value if atr_value > 0 else 0.0
            displacement_body_ratio = body / range_ if range_ > 0 else 0.0
            prior = candles[max(0, middle_index - breakout_lookback) : middle_index]
            if len(prior) >= 3:
                if direction == "bullish":
                    structure_break = close > max(_number(c.get("high")) for c in prior)
                elif direction == "bearish":
                    structure_break = close < min(_number(c.get("low")) for c in prior)

        gap_size_atr = size / atr_value if atr_value > 0 else 0.0
        displacement_ok = (
            atr_value > 0
            and displacement_body_atr >= min_body_atr
            and displacement_body_ratio >= min_body_ratio
            and gap_size_atr >= min_gap_atr
        )
        latest_close = _number(candles[-1].get("close")) if candles else 0.0
        continuation = (
            (latest_close - top) / atr_value
            if direction == "bullish" and atr_value > 0
            else (bottom - latest_close) / atr_value
            if direction == "bearish" and atr_value > 0
            else 0.0
        )
        followthrough = len(post) >= followthrough_bars and continuation >= continuation_atr

        if not displacement_ok:
            bag_state = "not_bag"
            bag_reason = "insufficient_displacement"
        elif not structure_break:
            bag_state = "not_bag"
            bag_reason = "no_structure_break"
        elif fill_fraction >= 0.5:
            bag_state = "invalidated"
            bag_reason = "consequent_encroachment_reached"
        elif followthrough and fill_fraction <= max_fill:
            bag_state = "confirmed"
            bag_reason = "structure_break_displacement_followthrough"
        else:
            bag_state = "candidate"
            bag_reason = "awaiting_followthrough"

        item.update(
            {
                "timeframe": str(timeframe or item.get("timeframe") or "").upper() or None,
                "ce": round(ce, 8),
                "fill_fraction": round(fill_fraction, 4),
                "fill_pct": round(fill_fraction * 100.0, 2),
                "fill_state": fill_state,
                "mitigated": fill_fraction >= 0.5,
                "fully_mitigated": fill_fraction >= 1.0,
                "gap_size_atr": round(gap_size_atr, 4),
                "displacement_body_atr": round(displacement_body_atr, 4),
                "displacement_body_ratio": round(displacement_body_ratio, 4),
                "structure_break": structure_break,
                "continuation_atr": round(continuation, 4),
                "bag_state": bag_state,
                "bag_reason": bag_reason,
            }
        )
        enriched.append(item)
    return enriched


def directional_fvg_context(
    fvgs: list[dict[str, Any]],
    *,
    direction: str,
    active_zone: dict[str, Any] | None,
    trigger_candles: list[dict[str, Any]] | None,
    atr: float,
    timeframe: str | None,
) -> dict[str, Any]:
    """Select direction-aligned FVG/BAG evidence and evaluate trigger reaction."""

    wanted = "bullish" if str(direction).upper() == "LONG" else "bearish"
    aligned = [f for f in fvgs if str(f.get("type") or "").lower() == wanted]
    zone_lower = _number((active_zone or {}).get("lower"), float("nan"))
    zone_upper = _number((active_zone or {}).get("upper"), float("nan"))
    overlaps: list[dict[str, Any]] = []
    if active_zone and zone_lower == zone_lower and zone_upper == zone_upper:
        overlaps = [
            f
            for f in aligned
            if not (zone_upper < _number(f.get("bottom")) or zone_lower > _number(f.get("top")))
        ]

    price = _number((trigger_candles or [{}])[-1].get("close")) if trigger_candles else 0.0
    nearest = min(
        aligned,
        key=lambda f: 0.0
        if _number(f.get("bottom")) <= price <= _number(f.get("top"))
        else min(abs(price - _number(f.get("bottom"))), abs(price - _number(f.get("top")))),
        default=None,
    )
    reaction = False
    if nearest and trigger_candles:
        last = trigger_candles[-1]
        open_ = _number(last.get("open"))
        high = _number(last.get("high"))
        low = _number(last.get("low"))
        close = _number(last.get("close"))
        bottom = _number(nearest.get("bottom"))
        top = _number(nearest.get("top"))
        ce = _number(nearest.get("ce"), bottom + (top - bottom) * 0.5)
        if wanted == "bullish":
            reaction = low <= top and close >= ce and close > open_
        else:
            reaction = high >= bottom and close <= ce and close < open_

    bags = [f for f in aligned if f.get("bag_state") in {"candidate", "confirmed"}]
    confirmed_bags = [f for f in bags if f.get("bag_state") == "confirmed"]
    selected_bag = min(
        confirmed_bags or bags,
        key=lambda f: abs(price - _number(f.get("ce"))),
        default=None,
    )
    return {
        "timeframe": str(timeframe or "").upper() or None,
        "direction": str(direction or "").upper(),
        "aligned_active_count": len(aligned),
        "opposing_active_count": max(0, len(fvgs) - len(aligned)),
        "overlap": bool(overlaps),
        "overlap_count": len(overlaps),
        "reaction_confirmed": reaction,
        "nearest": nearest,
        "bag_state": selected_bag.get("bag_state") if selected_bag else "none",
        "bag": selected_bag,
        "confirmed_bag_count": len(confirmed_bags),
    }
