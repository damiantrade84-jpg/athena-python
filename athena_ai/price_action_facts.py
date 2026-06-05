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
    """Backward-compatible wrapper around derive_sweep_event."""
    if not candles or not pool_levels:
        return _unknown("no_named_pool")

    sweep = derive_sweep_event(
        candles,
        liquidity_pools=pool_levels,
        atr=atr,
    )
    if not sweep.get("has_sweep"):
        if sweep.get("warnings") and "no_named_pool" in sweep["warnings"]:
            return _unknown("no_named_pool")
        return {
            "event": "no_sweep",
            "pools_checked": list(pool_levels),
            "confidence": "medium",
            "sweep_detail": sweep,
        }

    side = sweep.get("side")
    pool_level = sweep.get("pool_level")
    if side == "bearish":
        return {
            "event": "upper_sweep",
            "pool_level": pool_level,
            "pool_source": sweep.get("pool_source"),
            "close": _candle_field(candles[-1], "close", "c"),
            "confidence": sweep.get("confidence", "medium"),
            "sweep_detail": sweep,
        }
    if side == "bullish":
        return {
            "event": "lower_sweep",
            "pool_level": pool_level,
            "pool_source": sweep.get("pool_source"),
            "close": _candle_field(candles[-1], "close", "c"),
            "confidence": sweep.get("confidence", "medium"),
            "sweep_detail": sweep,
        }
    return {"event": "no_sweep", "pools_checked": list(pool_levels), "confidence": "medium", "sweep_detail": sweep}


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


def _anatomy_single_bar(candle: dict) -> dict[str, Any] | None:
    """OHLC anatomy for one bar."""
    high = _candle_field(candle, "high", "h")
    low = _candle_field(candle, "low", "l")
    open_ = _candle_field(candle, "open", "o")
    close = _candle_field(candle, "close", "c")
    if high is None or low is None or open_ is None or close is None:
        return None

    body_top = max(open_, close)
    body_bottom = min(open_, close)
    upper_wick = max(0.0, high - body_top)
    lower_wick = max(0.0, body_bottom - low)
    body_size = abs(close - open_)
    range_size = max(1e-12, high - low)
    body_ratio = body_size / range_size
    upper_wick_ratio = upper_wick / range_size
    lower_wick_ratio = lower_wick / range_size
    close_location_ratio = (close - low) / range_size

    if upper_wick > lower_wick * 1.25:
        dominant_wick = "upper"
    elif lower_wick > upper_wick * 1.25:
        dominant_wick = "lower"
    else:
        dominant_wick = "balanced"

    if close > open_:
        direction = "bullish"
    elif close < open_:
        direction = "bearish"
    else:
        direction = "neutral"

    is_rejection_shape = (
        (upper_wick >= 2 * body_size and upper_wick > lower_wick * 1.5)
        or (lower_wick >= 2 * body_size and lower_wick > upper_wick * 1.5)
    )
    is_displacement_shape = body_ratio >= 0.65 and body_size > 0

    return {
        "body_size": body_size,
        "range_size": range_size,
        "upper_wick": upper_wick,
        "lower_wick": lower_wick,
        "body_ratio": body_ratio,
        "upper_wick_ratio": upper_wick_ratio,
        "lower_wick_ratio": lower_wick_ratio,
        "close_location_ratio": close_location_ratio,
        "dominant_wick": dominant_wick,
        "direction": direction,
        "is_rejection_shape": is_rejection_shape,
        "is_displacement_shape": is_displacement_shape,
    }


def derive_candle_anatomy(candles: list[dict] | None) -> dict[str, Any]:
    """Last candle and last-3 candle body-wick facts."""
    if not candles:
        return {"last": None, "last_3": [], "confidence": "low", "warnings": ["no_candles"]}

    last_bar = _anatomy_single_bar(candles[-1])
    last_3: list[dict[str, Any]] = []
    for bar in _last_n(candles, 3):
        parsed = _anatomy_single_bar(bar)
        if parsed:
            last_3.append(parsed)

    confidence: Confidence = "high" if last_bar and len(last_3) >= 2 else "medium"
    if not last_bar:
        return {
            "last": None,
            "last_3": last_3,
            "confidence": "low",
            "warnings": ["incomplete_ohlc"],
        }
    return {
        "last": last_bar,
        "last_3": last_3,
        "confidence": confidence,
        "warnings": [],
    }


def _compute_ha_streak(candles: list[dict] | None, lookback: int = 10) -> int | None:
    """Signed consecutive Heikin-Ashi color streak from the last bar."""
    if not candles or len(candles) < 2:
        return None
    bars = _last_n(candles, lookback)
    ha_open: float | None = None
    colors: list[int] = []
    for bar in bars:
        o = _candle_field(bar, "open", "o")
        h = _candle_field(bar, "high", "h")
        l = _candle_field(bar, "low", "l")
        c = _candle_field(bar, "close", "c")
        if o is None or h is None or l is None or c is None:
            continue
        if ha_open is None:
            ha_open = (o + c) / 2.0
        ha_close = (o + h + l + c) / 4.0
        ha_high = max(h, ha_open, ha_close)
        ha_low = min(l, ha_open, ha_close)
        colors.append(1 if ha_close >= ha_open else -1)
        ha_open = (ha_open + ha_close) / 2.0
        _ = (ha_high, ha_low)

    if not colors:
        return None
    streak = 0
    last_color = colors[-1]
    for color in reversed(colors):
        if color == last_color:
            streak += color
        else:
            break
    return streak


def derive_regime_candle_gate(
    candles: list[dict] | None,
    engine_ctx: dict[str, Any] | None = None,
    levels: dict[str, float | None] | None = None,
    *,
    profile_location: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Regime/location eligibility object — diagnostics always emitted."""
    from config import CONFIG

    engine_ctx = engine_ctx or {}
    levels = levels or {}
    warnings: list[str] = []
    suppression_reasons: list[str] = []

    regime_raw = (
        engine_ctx.get("regime")
        or engine_ctx.get("regime_name")
        or (engine_ctx.get("structure_context") or {}).get("regime")
    )
    adx = _to_float(engine_ctx.get("adx_value"))
    if adx is None:
        adx = _to_float((engine_ctx.get("h4_snap") or {}).get("adx"))

    if regime_raw:
        regime_key = str(regime_raw).upper().replace(" ", "_")
        if regime_key in ("TRENDING", "RANGING", "HIGH_VOLATILITY", "LOW_VOLATILITY"):
            regime = regime_key if regime_key != "LOW_VOLATILITY" else "RANGING"
        elif regime_key in ("BALANCE", "BALANCED", "ROTATIONAL"):
            regime = "RANGING"
        elif "TREND" in regime_key:
            regime = "TRENDING"
        else:
            regime = "UNKNOWN"
    elif engine_ctx.get("h4_snap"):
        try:
            from regime import detect_regime

            pair_type = str(engine_ctx.get("asset_type") or engine_ctx.get("asset_group") or "forex")
            detected = detect_regime(engine_ctx["h4_snap"], pair_type)
            regime = str(detected.get("regime") or "UNKNOWN").upper()
            if adx is None:
                adx = _to_float(detected.get("adx_value"))
        except Exception:
            regime = "UNKNOWN"
            warnings.append("regime_detection_failed")
    else:
        regime = "UNKNOWN"
        warnings.append("missing_adx_and_regime")

    ha_streak = _compute_ha_streak(candles)
    if ha_streak is None:
        warnings.append("ha_streak_unavailable")

    trend_bias = "UNKNOWN"
    if ha_streak is not None:
        if ha_streak >= 3:
            trend_bias = "LONG"
        elif ha_streak <= -3:
            trend_bias = "SHORT"
        else:
            trend_bias = "NEUTRAL"

    allow_long = True
    allow_short = True
    gate_cfg = CONFIG.get("CANDLE_UNDERSTANDING") or {}
    ha_trend_min = int(gate_cfg.get("HA_TREND_STREAK_MIN", 3))

    if regime == "TRENDING":
        if ha_streak is not None and ha_streak >= ha_trend_min:
            allow_short = False
            suppression_reasons.append("trending_long_ha_streak_suppresses_short")
        elif ha_streak is not None and ha_streak <= -ha_trend_min:
            allow_long = False
            suppression_reasons.append("trending_short_ha_streak_suppresses_long")
    elif regime == "RANGING":
        loc = (profile_location or {}).get("location")
        at_named = any(
            _to_float(levels.get(k)) is not None
            for k in ("vah", "val", "poc", "pdh", "pdl", "session_high", "session_low", "support", "resistance")
        )
        at_value_edge = loc in ("above_vah", "below_val", "near_poc")
        if not at_value_edge and not at_named:
            allow_long = False
            allow_short = False
            suppression_reasons.append("ranging_mid_range_no_named_level")
            warnings.append("ranging_requires_value_edge_or_named_level")

    return {
        "regime": regime if regime in ("TRENDING", "RANGING", "UNKNOWN") else "UNKNOWN",
        "adx": adx,
        "ha_streak": ha_streak,
        "trend_bias": trend_bias,
        "allow_long": allow_long,
        "allow_short": allow_short,
        "suppression_reasons": suppression_reasons,
        "warnings": warnings,
        "confidence": "high" if regime != "UNKNOWN" and adx is not None else "medium" if regime != "UNKNOWN" else "low",
    }


def _collect_liquidity_pools(
    swing_levels: dict[str, float | None] | None = None,
    named_levels: dict[str, float | None] | None = None,
    liquidity_pools: list[float] | None = None,
    engine_ctx: dict[str, Any] | None = None,
) -> list[tuple[float, str]]:
    """Return (level, source) pairs in priority order."""
    pools: list[tuple[float, str]] = []
    seen: set[float] = set()

    def _add(level: Any, source: str) -> None:
        val = _to_float(level)
        if val is None:
            return
        key = round(val, 8)
        if key in seen:
            return
        seen.add(key)
        pools.append((val, source))

    swing_levels = swing_levels or {}
    for key in ("swing_high", "swing_low", "prior_high", "prior_low"):
        _add(swing_levels.get(key), f"swing:{key}")

    named_levels = named_levels or {}
    for key in (
        "pdh", "pdl", "session_high", "session_low",
        "vah", "val", "poc", "support", "resistance",
    ):
        _add(named_levels.get(key), f"named:{key}")

    for pool in liquidity_pools or []:
        _add(pool, "liquidity_pool")

    structure = (engine_ctx or {}).get("structure_context") or {}
    for ob in structure.get("order_blocks") or []:
        if isinstance(ob, dict):
            _add(ob.get("level") or ob.get("center"), "order_block")
    for fvg in structure.get("active_fvgs") or structure.get("fvgs") or []:
        if isinstance(fvg, dict):
            _add(fvg.get("top"), "fvg_top")
            _add(fvg.get("bottom"), "fvg_bottom")

    return pools


def derive_sweep_event(
    candles: list[dict] | None,
    *,
    swing_levels: dict[str, float | None] | None = None,
    named_levels: dict[str, float | None] | None = None,
    liquidity_pools: list[float] | None = None,
    atr: float | None = None,
    correlated_ctx: dict[str, Any] | None = None,
    engine_ctx: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Hardened sweep detection requiring a named liquidity pool and reclaim."""
    empty = {
        "has_sweep": False,
        "side": None,
        "pool_source": None,
        "pool_level": None,
        "wick_pierced_pool": False,
        "closed_back_inside": False,
        "next_bar_confirmed": False,
        "acceptance_beyond_level": False,
        "bos_conflict": False,
        "confidence": "none",
        "invalidation_reason": None,
        "warnings": [],
    }
    if not candles:
        empty["warnings"] = ["no_candles"]
        return empty

    pools = _collect_liquidity_pools(swing_levels, named_levels, liquidity_pools, engine_ctx)
    if not pools:
        empty["warnings"] = ["no_named_pool"]
        return empty

    from config import CONFIG

    gate_cfg = CONFIG.get("CANDLE_UNDERSTANDING") or {}
    wick_min_ratio = float(gate_cfg.get("SWEEP_WICK_MIN_RANGE_RATIO", 0.25))
    tolerance = (atr * 0.1) if atr and atr > 0 else 0.0

    last = candles[-1]
    prev = candles[-2] if len(candles) >= 2 else None
    next_bar = None
    high = _candle_field(last, "high", "h")
    low = _candle_field(last, "low", "l")
    close = _candle_field(last, "close", "c")
    open_ = _candle_field(last, "open", "o")
    if high is None or low is None or close is None or open_ is None:
        empty["warnings"] = ["incomplete_ohlc"]
        return empty

    anatomy = _anatomy_single_bar(last) or {}
    range_size = anatomy.get("range_size") or max(1e-12, high - low)

    best: dict[str, Any] | None = None
    for pool_level, pool_source in pools:
        # Bearish upper sweep
        if high >= pool_level - tolerance and close < pool_level:
            upper_wick = anatomy.get("upper_wick") or 0.0
            wick_meaningful = upper_wick / range_size >= wick_min_ratio
            acceptance = derive_acceptance_state(candles, pool_level, min_bars=2)
            bos_conflict = acceptance.get("state") == "accepted_above"
            closed_back = True
            next_confirmed = False
            if prev is not None:
                prev_high = _candle_field(prev, "high", "h")
                prev_close = _candle_field(prev, "close", "c")
                if prev_high is not None and prev_close is not None:
                    if prev_high > pool_level and close < pool_level:
                        next_confirmed = True
            conf = "high" if wick_meaningful and not bos_conflict else "medium" if wick_meaningful else "low"
            if next_confirmed and conf == "high":
                conf = "high"
            elif next_confirmed and conf == "medium":
                conf = "medium"
            candidate = {
                "has_sweep": wick_meaningful and not bos_conflict,
                "side": "bearish",
                "pool_source": pool_source,
                "pool_level": pool_level,
                "wick_pierced_pool": high >= pool_level - tolerance,
                "closed_back_inside": closed_back,
                "next_bar_confirmed": next_confirmed,
                "acceptance_beyond_level": bos_conflict,
                "bos_conflict": bos_conflict,
                "confidence": conf if wick_meaningful else "none",
                "invalidation_reason": "acceptance_beyond_level" if bos_conflict else None,
                "warnings": [] if wick_meaningful else ["wick_not_meaningful_vs_range"],
            }
            if best is None or (candidate["has_sweep"] and conf in ("high", "medium")):
                best = candidate

        # Bullish lower sweep
        if low <= pool_level + tolerance and close > pool_level:
            lower_wick = anatomy.get("lower_wick") or 0.0
            wick_meaningful = lower_wick / range_size >= wick_min_ratio
            acceptance = derive_acceptance_state(candles, pool_level, min_bars=2)
            bos_conflict = acceptance.get("state") == "accepted_below"
            closed_back = True
            next_confirmed = False
            if prev is not None:
                prev_low = _candle_field(prev, "low", "l")
                if prev_low is not None:
                    if prev_low < pool_level and close > pool_level:
                        next_confirmed = True
            conf = "high" if wick_meaningful and not bos_conflict else "medium" if wick_meaningful else "low"
            candidate = {
                "has_sweep": wick_meaningful and not bos_conflict,
                "side": "bullish",
                "pool_source": pool_source,
                "pool_level": pool_level,
                "wick_pierced_pool": low <= pool_level + tolerance,
                "closed_back_inside": closed_back,
                "next_bar_confirmed": next_confirmed,
                "acceptance_beyond_level": bos_conflict,
                "bos_conflict": bos_conflict,
                "confidence": conf if wick_meaningful else "none",
                "invalidation_reason": "acceptance_beyond_level" if bos_conflict else None,
                "warnings": [] if wick_meaningful else ["wick_not_meaningful_vs_range"],
            }
            if best is None or (candidate["has_sweep"] and not best.get("has_sweep")):
                best = candidate

    result = best or empty
    if correlated_ctx:
        corr_side = str(correlated_ctx.get("bias") or correlated_ctx.get("direction") or "").upper()
        sweep_side = result.get("side")
        if sweep_side == "bullish" and corr_side == "LONG":
            if result["confidence"] == "medium":
                result["confidence"] = "high"
        elif sweep_side == "bearish" and corr_side == "SHORT":
            if result["confidence"] == "medium":
                result["confidence"] = "high"
    else:
        result.setdefault("warnings", []).append("cross_asset_confirmation_unavailable")

    return result


def derive_fvg_state(fvg: dict[str, Any] | None, price: float | None) -> dict[str, Any]:
    """Graded FVG fill state — full fill decays significance but keeps diagnostics."""
    if not isinstance(fvg, dict) or price is None:
        return {
            "fill_state": "unknown",
            "fill_pct": None,
            "ce_level": None,
            "ce_respected": None,
            "score_decay": 0.0,
            "confidence": "low",
            "warnings": ["missing_fvg_or_price"],
        }

    top = _to_float(fvg.get("top"))
    bottom = _to_float(fvg.get("bottom"))
    if top is None or bottom is None:
        return {
            "fill_state": "unknown",
            "fill_pct": None,
            "ce_level": None,
            "ce_respected": None,
            "score_decay": 0.0,
            "confidence": "low",
            "warnings": ["malformed_fvg_bounds"],
        }

    gap_low = min(top, bottom)
    gap_high = max(top, bottom)
    gap_size = max(1e-12, gap_high - gap_low)
    ce_level = (gap_high + gap_low) / 2.0
    mitigated = bool(fvg.get("mitigated"))

    if gap_low <= price <= gap_high:
        fill_pct = (price - gap_low) / gap_size
        if abs(price - ce_level) <= gap_size * 0.05:
            fill_state = "ce_touched"
            score_decay = 0.35
            ce_respected = True
        elif fill_pct >= 0.95 or mitigated:
            fill_state = "full"
            score_decay = 0.75
            ce_respected = fill_pct < 0.5
        else:
            fill_state = "partial"
            score_decay = 0.15 + fill_pct * 0.4
            ce_respected = price < ce_level if fvg.get("type") == "bullish" else price > ce_level
    elif mitigated:
        fill_pct = 1.0
        fill_state = "full"
        score_decay = 0.85
        ce_respected = False
    else:
        fill_pct = 0.0
        fill_state = "unfilled"
        score_decay = 0.0
        ce_respected = None

    return {
        "fill_state": fill_state,
        "fill_pct": round(fill_pct, 4),
        "ce_level": ce_level,
        "ce_respected": ce_respected,
        "score_decay": round(score_decay, 4),
        "fvg_id": fvg.get("id"),
        "confidence": "high",
        "warnings": [],
    }


def derive_ob_fvg_confluence(
    order_blocks: list[dict] | None,
    fvgs: list[dict] | None,
) -> dict[str, Any]:
    """Detect OB/FVG overlap."""
    notes: list[str] = []
    warnings: list[str] = []
    if not order_blocks or not fvgs:
        return {
            "has_confluence": False,
            "overlap_range": None,
            "ob_id": None,
            "fvg_id": None,
            "confidence_bonus": 0.0,
            "notes": notes,
            "warnings": warnings or ["missing_ob_or_fvg"],
            "confidence": "low",
        }

    best_overlap: tuple[float, float] | None = None
    best_ob_id = None
    best_fvg_id = None

    for ob in order_blocks:
        if not isinstance(ob, dict):
            warnings.append("malformed_order_block")
            continue
        ob_upper = _to_float(ob.get("upper") or ob.get("top"))
        ob_lower = _to_float(ob.get("lower") or ob.get("bottom") or ob.get("level"))
        if ob_upper is None or ob_lower is None:
            level = _to_float(ob.get("level") or ob.get("center"))
            if level is None:
                warnings.append("malformed_order_block_bounds")
                continue
            ob_upper = level
            ob_lower = level
        ob_lo, ob_hi = min(ob_lower, ob_upper), max(ob_lower, ob_upper)

        for fvg in fvgs:
            if not isinstance(fvg, dict):
                warnings.append("malformed_fvg")
                continue
            f_top = _to_float(fvg.get("top"))
            f_bot = _to_float(fvg.get("bottom"))
            if f_top is None or f_bot is None:
                warnings.append("malformed_fvg_bounds")
                continue
            f_lo, f_hi = min(f_bot, f_top), max(f_bot, f_top)
            if ob_hi < f_lo or ob_lo > f_hi:
                continue
            overlap_lo = max(ob_lo, f_lo)
            overlap_hi = min(ob_hi, f_hi)
            size = overlap_hi - overlap_lo
            if best_overlap is None or size > (best_overlap[1] - best_overlap[0]):
                best_overlap = (overlap_lo, overlap_hi)
                best_ob_id = ob.get("id")
                best_fvg_id = fvg.get("id")

    if best_overlap is None:
        return {
            "has_confluence": False,
            "overlap_range": None,
            "ob_id": None,
            "fvg_id": None,
            "confidence_bonus": 0.0,
            "notes": ["no_overlap"],
            "warnings": warnings,
            "confidence": "medium",
        }

    bonus = min(0.25, (best_overlap[1] - best_overlap[0]) / max(best_overlap[1], 1e-12))
    notes.append("ob_fvg_overlap_detected")
    return {
        "has_confluence": True,
        "overlap_range": [best_overlap[0], best_overlap[1]],
        "ob_id": best_ob_id,
        "fvg_id": best_fvg_id,
        "confidence_bonus": round(bonus, 4),
        "notes": notes,
        "warnings": warnings,
        "confidence": "high",
    }


def derive_effort_vs_result(
    candles: list[dict] | None,
    volume: list[float] | None = None,
    cvd: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Bar-level effort-vs-result / absorption proxy."""
    warnings: list[str] = []
    if not candles:
        return {
            "has_absorption": False,
            "side": "unknown",
            "volume_ratio": None,
            "norm_body": None,
            "cvd_divergence": None,
            "confidence": "none",
            "warnings": ["no_candles"],
        }

    last = candles[-1]
    anatomy = _anatomy_single_bar(last)
    if not anatomy:
        return {
            "has_absorption": False,
            "side": "unknown",
            "volume_ratio": None,
            "norm_body": None,
            "cvd_divergence": None,
            "confidence": "none",
            "warnings": ["incomplete_ohlc"],
        }

    from config import CONFIG

    gate_cfg = CONFIG.get("CANDLE_UNDERSTANDING") or {}
    vol_mult = float(gate_cfg.get("ABSORPTION_VOL_RATIO_MIN", 1.75))
    max_body_ratio = float(gate_cfg.get("ABSORPTION_MAX_BODY_RATIO", 0.35))

    vols_raw = [_candle_field(b, "volume", "v", "vol") for b in candles]
    volume_ratio: float | None = None
    if volume:
        vols = [v for v in volume if v is not None]
        if vols:
            avg = sum(vols[:-1]) / max(1, len(vols) - 1) if len(vols) > 1 else vols[0]
            volume_ratio = vols[-1] / avg if avg > 0 else None
    elif not any(v is None for v in vols_raw[-6:]) and len(vols_raw) >= 6:
        vols = [v for v in vols_raw if v is not None]
        prior = sorted(vols[-6:-1])
        median = prior[len(prior) // 2]
        if median > 0:
            volume_ratio = vols[-1] / median
    else:
        warnings.append("missing_volume")

    norm_body = anatomy["body_ratio"]
    high_effort = volume_ratio is not None and volume_ratio >= vol_mult
    small_result = norm_body <= max_body_ratio
    close_loc = anatomy["close_location_ratio"]

    cvd_divergence: bool | None = None
    if isinstance(cvd, dict):
        slope = _to_float(cvd.get("slope") or cvd.get("cvdSlope"))
        if slope is not None:
            if anatomy["direction"] == "bullish" and slope < 0:
                cvd_divergence = True
            elif anatomy["direction"] == "bearish" and slope > 0:
                cvd_divergence = True
            else:
                cvd_divergence = False
        else:
            warnings.append("cvd_slope_unavailable")
    else:
        warnings.append("cvd_unavailable")

    has_absorption = bool(high_effort and small_result)
    side = "unknown"
    if has_absorption:
        if close_loc >= 0.6:
            side = "bullish"
        elif close_loc <= 0.4:
            side = "bearish"
        else:
            side = "neutral"

    confidence = "none"
    if has_absorption:
        confidence = "high" if cvd_divergence else "medium" if volume_ratio and volume_ratio >= vol_mult * 1.2 else "low"

    return {
        "has_absorption": has_absorption,
        "side": side,
        "volume_ratio": round(volume_ratio, 4) if volume_ratio is not None else None,
        "norm_body": round(norm_body, 4),
        "cvd_divergence": cvd_divergence,
        "confidence": confidence,
        "warnings": warnings,
    }


def derive_candle_timeframe_confidence(
    timeframe: str | None,
    engine: str = "A",
) -> dict[str, Any]:
    """Config-driven timeframe confidence multipliers."""
    from config import CONFIG

    tf = str(timeframe or "").upper().strip()
    if engine.upper() == "D":
        table = CONFIG.get("ENGINE_D_CANDLE_EXECUTION_CONFIDENCE") or {}
        default = 0.9
    else:
        table = CONFIG.get("ENGINE_A_CANDLE_TIMEFRAME_CONFIDENCE") or {}
        default = 0.5

    multiplier = _to_float(table.get(tf))
    warnings: list[str] = []
    if multiplier is None:
        multiplier = default
        warnings.append(f"unknown_timeframe_{tf or 'empty'}")

    return {
        "timeframe": tf or None,
        "engine": engine.upper(),
        "multiplier": multiplier,
        "confidence": "high" if not warnings else "low",
        "warnings": warnings,
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
    profile_vp_context = {}
    if isinstance(structure_ctx, dict):
        profile_vp_context = structure_ctx.get("profile_vp_context") or {}
    profile_vp_enabled = bool(profile_vp_context.get("enabled"))
    profile = structure_ctx.get("profile") if isinstance(structure_ctx, dict) else None
    if not isinstance(profile, dict):
        profile = engine_a_ctx.get("profile") or {}
    if not isinstance(profile, dict):
        profile = {}
    if profile_vp_enabled and isinstance(structure_ctx, dict):
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
    elif profile_vp_enabled:
        poc = _to_float(profile.get("poc") or profile.get("vp_poc"))
        vah = _to_float(profile.get("vah") or profile.get("vp_vah"))
        val = _to_float(profile.get("val") or profile.get("vp_val"))
    else:
        poc = vah = val = None

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

    result = {
        "wick_event": wick,
        "breakout_state": breakout,
        "acceptance_state": acceptance,
        "liquidity_event": liquidity,
        "structure_event": structure,
        "profile_location": profile_loc,
        "volume_behavior": volume,
        "setup_candidates": candidates,
        "profile_vp_context": profile_vp_context,
        "_meta": {
            "atr_value": atr_value,
            "breakout_level_used": breakout_level,
            "bars_provided": len(ohlcv_window) if ohlcv_window else 0,
            "profile_vp_trusted": profile_vp_enabled,
        },
    }

    try:
        from config import CONFIG

        if CONFIG.get("CANDLE_UNDERSTANDING_ENABLED", True):
            from athena_ai.candle_understanding import derive_candle_understanding

            result["candle_understanding"] = derive_candle_understanding(
                candles=ohlcv_window,
                engine_ctx=engine_a_ctx,
                direction=direction,
                timeframe=engine_a_ctx.get("timeframe") or engine_a_ctx.get("chart_timeframe"),
                engine="A",
                named_levels=named_levels,
                liquidity_pools=liquidity_pools,
            )
    except Exception:
        pass

    return result
