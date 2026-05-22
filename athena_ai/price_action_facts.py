"""Deterministic price-action facts derived from OHLCV and known levels.

The job of this module is to give the AI reviewer a small, auditable set of
chart facts *with their raw measurements and a confidence label*. The AI is
free to discount a fact when its confidence is low — that is the whole point.

Hard rules:
  * Every returned fact carries a raw value and a confidence in {high, medium, low}.
  * Missing input → emit {state: "unknown", confidence: "low"}; never omit, never guess.
  * Never fabricate a level. If a level was not provided, do not invent one.
  * Pure functions only. No engine reimplementation; if a fact lives in
    engine_a_ctx already (e.g. POC/VAH/VAL, EMA cluster), read it from there.
"""

from __future__ import annotations

from typing import Any

Confidence = str  # "high" | "medium" | "low"


def _to_float(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _candle_field(candle: dict | None, *keys: str) -> float | None:
    if not isinstance(candle, dict):
        return None
    for key in keys:
        if key in candle:
            val = _to_float(candle.get(key))
            if val is not None:
                return val
    return None


def _unknown(reason: str = "input_missing") -> dict[str, Any]:
    return {"state": "unknown", "confidence": "low", "reason": reason}


def _last_n(candles: list[dict] | None, n: int) -> list[dict]:
    if not isinstance(candles, list):
        return []
    return list(candles[-n:])


# ----- atomic fact derivations -------------------------------------------------


def derive_wick_event(
    candles: list[dict] | None,
    atr: float | None,
) -> dict[str, Any]:
    """Classify the last candle's wick anatomy relative to ATR."""
    if not candles:
        return _unknown("no_candles")
    last = candles[-1]
    high = _candle_field(last, "high", "h")
    low = _candle_field(last, "low", "l")
    open_ = _candle_field(last, "open", "o")
    close = _candle_field(last, "close", "c")
    if high is None or low is None or open_ is None or close is None:
        return _unknown("incomplete_ohlc")

    body_top = max(open_, close)
    body_bottom = min(open_, close)
    upper_wick = max(0.0, high - body_top)
    lower_wick = max(0.0, body_bottom - low)
    body = abs(close - open_)
    total = max(1e-12, high - low)
    body_ratio = body / total

    upper_wick_atr = (upper_wick / atr) if atr and atr > 0 else None
    lower_wick_atr = (lower_wick / atr) if atr and atr > 0 else None

    # confidence is high when ATR is known AND a wick clearly dominates
    confidence: Confidence = "low"
    fact_type = "neutral"
    if upper_wick_atr is not None and lower_wick_atr is not None:
        if upper_wick_atr >= 1.0 and upper_wick >= 2 * body and upper_wick > lower_wick * 1.5:
            fact_type = "upper_rejection"
            confidence = "high" if upper_wick_atr >= 1.5 else "medium"
        elif lower_wick_atr >= 1.0 and lower_wick >= 2 * body and lower_wick > upper_wick * 1.5:
            fact_type = "lower_rejection"
            confidence = "high" if lower_wick_atr >= 1.5 else "medium"
        elif body_ratio < 0.25:
            fact_type = "indecision"
            confidence = "medium"
        else:
            fact_type = "directional_body"
            confidence = "medium" if body_ratio > 0.6 else "low"
    elif upper_wick > 2 * body and upper_wick > lower_wick * 1.5:
        fact_type = "upper_rejection"
        confidence = "low"  # no ATR to scale against
    elif lower_wick > 2 * body and lower_wick > upper_wick * 1.5:
        fact_type = "lower_rejection"
        confidence = "low"

    return {
        "type": fact_type,
        "upper_wick_atr": upper_wick_atr,
        "lower_wick_atr": lower_wick_atr,
        "body_ratio": body_ratio,
        "confidence": confidence,
    }


def derive_breakout_state(
    candles: list[dict] | None,
    level: float | None,
    *,
    direction: str | None = None,
) -> dict[str, Any]:
    """Classify the last 1-3 candles relative to a NAMED level.

    `direction` is the proposed trade direction ("LONG" / "SHORT"); when
    provided, "failed_breakout" is reported relative to that direction.
    """
    if not candles or level is None:
        return _unknown("missing_candles_or_level")
    level_f: float = float(level)
    del direction  # currently unused; signature kept for future per-direction rules

    bars = _last_n(candles, 3)
    if len(bars) < 2:
        return _unknown("not_enough_candles")

    closes_raw = [_candle_field(b, "close", "c") for b in bars]
    highs_raw = [_candle_field(b, "high", "h") for b in bars]
    lows_raw = [_candle_field(b, "low", "l") for b in bars]
    if any(v is None for v in closes_raw + highs_raw + lows_raw):
        return _unknown("incomplete_ohlc")
    closes: list[float] = [v for v in closes_raw if v is not None]
    highs: list[float] = [v for v in highs_raw if v is not None]
    lows: list[float] = [v for v in lows_raw if v is not None]

    last_close = closes[-1]
    prev_close = closes[-2]
    last_high = highs[-1]
    last_low = lows[-1]

    pierced_up = last_high > level_f and prev_close <= level_f
    pierced_down = last_low < level_f and prev_close >= level_f
    closed_back_inside_up = pierced_up and last_close <= level_f
    closed_back_inside_down = pierced_down and last_close >= level_f

    if closed_back_inside_up:
        return {
            "state": "failed_breakout",
            "side": "upper",
            "level": level_f,
            "close_back_inside": True,
            "last_close": last_close,
            "last_high": last_high,
            "confidence": "high",
        }
    if closed_back_inside_down:
        return {
            "state": "failed_breakout",
            "side": "lower",
            "level": level_f,
            "close_back_inside": True,
            "last_close": last_close,
            "last_low": last_low,
            "confidence": "high",
        }
    if pierced_up and last_close > level_f:
        return {
            "state": "breakout_holding",
            "side": "upper",
            "level": level_f,
            "last_close": last_close,
            "confidence": "medium",
        }
    if pierced_down and last_close < level_f:
        return {
            "state": "breakout_holding",
            "side": "lower",
            "level": level_f,
            "last_close": last_close,
            "confidence": "medium",
        }

    return {
        "state": "no_breakout",
        "level": level_f,
        "last_close": last_close,
        "confidence": "medium",
    }


def derive_acceptance_state(
    candles: list[dict] | None,
    level: float | None,
    *,
    min_bars: int = 3,
) -> dict[str, Any]:
    """Did price accept above/below a level (≥ min_bars closes on one side)?"""
    if not candles or level is None:
        return _unknown("missing_candles_or_level")
    level_f: float = float(level)
    closes_raw = [_candle_field(b, "close", "c") for b in _last_n(candles, min_bars)]
    if len(closes_raw) < min_bars or any(c is None for c in closes_raw):
        return _unknown("not_enough_closes")
    closes: list[float] = [c for c in closes_raw if c is not None]
    above = sum(1 for c in closes if c > level_f)
    below = sum(1 for c in closes if c < level_f)
    if above == min_bars:
        return {
            "state": "accepted_above",
            "level": level_f,
            "bars": min_bars,
            "confidence": "high",
        }
    if below == min_bars:
        return {
            "state": "accepted_below",
            "level": level_f,
            "bars": min_bars,
            "confidence": "high",
        }
    return {
        "state": "no_acceptance",
        "level": level_f,
        "above": above,
        "below": below,
        "confidence": "medium",
    }


def derive_liquidity_event(
    candles: list[dict] | None,
    pool_levels: list[float] | None,
    atr: float | None,
) -> dict[str, Any]:
    """Did the last candle sweep a named liquidity pool and reverse?

    A "sweep" requires the wick to pierce a pool extreme but the candle to
    close back through it. Without an identified pool we return unknown —
    we will NOT infer a liquidity pool from raw bars.
    """
    if not candles or not pool_levels:
        return _unknown("no_named_pool")
    last = candles[-1]
    high = _candle_field(last, "high", "h")
    low = _candle_field(last, "low", "l")
    close = _candle_field(last, "close", "c")
    if high is None or low is None or close is None:
        return _unknown("incomplete_ohlc")
    tolerance = (atr * 0.1) if atr else 0.0

    for pool in pool_levels:
        if pool is None:
            continue
        # upper sweep: wick above pool, close back below
        if high >= pool - tolerance and close < pool:
            return {
                "event": "upper_sweep",
                "pool_level": pool,
                "wick_high": high,
                "close": close,
                "confidence": "high",
            }
        # lower sweep: wick below pool, close back above
        if low <= pool + tolerance and close > pool:
            return {
                "event": "lower_sweep",
                "pool_level": pool,
                "wick_low": low,
                "close": close,
                "confidence": "high",
            }
    return {"event": "no_sweep", "pools_checked": list(pool_levels), "confidence": "medium"}


def derive_structure_event(engine_a_ctx: dict[str, Any]) -> dict[str, Any]:
    """Read structure event from existing Engine B context if present.

    This module does NOT reimplement Engine B. If no structure context is
    available we report unknown.
    """
    structure = engine_a_ctx.get("structure_context")
    if not isinstance(structure, dict) or not structure:
        return _unknown("no_structure_context")
    label = (
        structure.get("event")
        or structure.get("label")
        or structure.get("state")
        or structure.get("verdict")
    )
    if not label:
        return _unknown("no_structure_label")
    return {
        "state": str(label).lower(),
        "source": "engine_b_context",
        "raw": {k: v for k, v in structure.items() if k in {"event", "label", "state", "verdict", "bos", "choch"}},
        "confidence": "medium",
    }


def derive_profile_location(
    last_close: float | None,
    poc: float | None,
    vah: float | None,
    val: float | None,
    atr: float | None,
) -> dict[str, Any]:
    """Locate last close relative to POC / VAH / VAL."""
    if last_close is None or poc is None:
        return _unknown("missing_close_or_poc")
    distance_to_poc = last_close - poc
    distance_atr = (abs(distance_to_poc) / atr) if atr and atr > 0 else None

    inside_va = (
        vah is not None and val is not None and val <= last_close <= vah
    )
    above_vah = vah is not None and last_close > vah
    below_val = val is not None and last_close < val

    if above_vah:
        location = "above_vah"
    elif below_val:
        location = "below_val"
    elif inside_va:
        if distance_atr is not None and distance_atr <= 0.25:
            location = "near_poc"
        else:
            location = "inside_va"
    else:
        location = "unknown_va_bounds"

    confidence: Confidence = "high" if (vah is not None and val is not None) else "medium"
    return {
        "location": location,
        "poc": poc,
        "vah": vah,
        "val": val,
        "last_close": last_close,
        "distance_to_poc_atr": distance_atr,
        "confidence": confidence,
    }


def derive_volume_behavior(candles: list[dict] | None) -> dict[str, Any]:
    """Compare last candle volume to median of prior N."""
    if not candles or len(candles) < 6:
        return _unknown("not_enough_candles")
    vols_raw = [_candle_field(b, "volume", "v") for b in candles]
    if any(v is None for v in vols_raw[-6:]):
        return _unknown("missing_volume")
    vols: list[float] = [v for v in vols_raw if v is not None]
    last_vol = vols[-1]
    prior = sorted(vols[-6:-1])
    median = prior[len(prior) // 2]
    if median <= 0:
        return _unknown("median_zero")
    ratio = last_vol / median
    if ratio >= 1.75:
        state = "expansion"
        confidence: Confidence = "high"
    elif ratio <= 0.5:
        state = "contraction"
        confidence = "medium"
    else:
        state = "normal"
        confidence = "medium"
    return {
        "state": state,
        "last_volume": last_vol,
        "prior_median_volume": median,
        "ratio": ratio,
        "confidence": confidence,
    }


def derive_setup_candidates(
    *,
    wick_event: dict[str, Any],
    breakout_state: dict[str, Any],
    liquidity_event: dict[str, Any],
    profile_location: dict[str, Any],
) -> dict[str, Any]:
    """Surface raw archetype HINTS — NOT decisions. Classifier resolves these."""
    hints: list[str] = []
    if breakout_state.get("state") == "failed_breakout":
        hints.append("failed_breakout_hint")
    if breakout_state.get("state") == "breakout_holding":
        hints.append("breakout_continuation_hint")
    if wick_event.get("type") in ("upper_rejection", "lower_rejection"):
        hints.append("wick_rejection_hint")
    if liquidity_event.get("event") in ("upper_sweep", "lower_sweep"):
        hints.append("liquidity_sweep_hint")
    loc = profile_location.get("location")
    if loc == "near_poc":
        hints.append("balance_chop_hint")
    if loc in ("above_vah", "below_val"):
        hints.append("return_to_poc_hint")
    confidence: Confidence = "medium" if hints else "low"
    return {"hints": hints, "confidence": confidence}


# ----- top-level orchestrator -------------------------------------------------


def derive_price_action_facts(
    engine_a_ctx: dict[str, Any],
    *,
    ohlcv_window: list[dict] | None,
    direction: str | None = None,
    named_levels: dict[str, float | None] | None = None,
    liquidity_pools: list[float] | None = None,
) -> dict[str, Any]:
    """Compose the full self-describing fact dict for the AI payload.

    `engine_a_ctx` is the existing Engine A review context (so we can read
    structure_context, ema_levels, atr, etc.). `ohlcv_window` is the last N
    bars on the chart timeframe. `named_levels` lets the caller pass in a
    specific level for breakout analysis (e.g. nearest EMA, prior swing).
    """
    atr_block = engine_a_ctx.get("atr") or {}
    atr_value = _to_float(atr_block.get("atr_chart_tf") or atr_block.get("atr_value"))

    structure_ctx = engine_a_ctx.get("structure_context") or {}
    profile = structure_ctx.get("profile") if isinstance(structure_ctx, dict) else None
    if not isinstance(profile, dict):
        profile = engine_a_ctx.get("profile") or {}
    if not isinstance(profile, dict):
        profile = {}
    if isinstance(structure_ctx, dict):
        poc = _to_float(
            profile.get("poc")
            or profile.get("vp_poc")
            or structure_ctx.get("prev_session_poc")
            or structure_ctx.get("vp_poc")
        )
        vah = _to_float(
            profile.get("vah")
            or profile.get("vp_vah")
            or structure_ctx.get("prev_session_vah")
            or structure_ctx.get("vp_vah")
        )
        val = _to_float(
            profile.get("val")
            or profile.get("vp_val")
            or structure_ctx.get("prev_session_val")
            or structure_ctx.get("vp_val")
        )
    else:
        poc = _to_float(profile.get("poc") or profile.get("vp_poc"))
        vah = _to_float(profile.get("vah") or profile.get("vp_vah"))
        val = _to_float(profile.get("val") or profile.get("vp_val"))

    named_levels = named_levels or {}
    breakout_level = _to_float(named_levels.get("breakout_level"))
    if breakout_level is None:
        # fall back to nearest EMA if not supplied
        ema = engine_a_ctx.get("ema_levels") or {}
        breakout_level = _to_float(ema.get("ema50") or ema.get("ema200"))

    wick = derive_wick_event(ohlcv_window, atr_value)
    breakout = derive_breakout_state(ohlcv_window, breakout_level, direction=direction)
    acceptance = derive_acceptance_state(ohlcv_window, breakout_level)
    liquidity = derive_liquidity_event(ohlcv_window, liquidity_pools, atr_value)
    structure = derive_structure_event(engine_a_ctx)
    last_close = None
    if ohlcv_window:
        last_close = _candle_field(ohlcv_window[-1], "close", "c")
    profile_loc = derive_profile_location(last_close, poc, vah, val, atr_value)
    volume = derive_volume_behavior(ohlcv_window)
    candidates = derive_setup_candidates(
        wick_event=wick,
        breakout_state=breakout,
        liquidity_event=liquidity,
        profile_location=profile_loc,
    )

    return {
        "wick_event": wick,
        "breakout_state": breakout,
        "acceptance_state": acceptance,
        "liquidity_event": liquidity,
        "structure_event": structure,
        "profile_location": profile_loc,
        "volume_behavior": volume,
        "setup_candidates": candidates,
        "_meta": {
            "atr_value": atr_value,
            "breakout_level_used": breakout_level,
            "bars_provided": len(ohlcv_window) if ohlcv_window else 0,
        },
    }
