"""Phase 2 quality diagnostics for Engine B.

These helpers grade evidence; they never create direction, structure, readiness,
or execution permission. All scores are deterministic, bounded, and emitted so
replay/A-B tooling can measure each upgrade independently.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any


def _number(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return float(default)


def _clamp01(value: float) -> float:
    return max(0.0, min(1.0, float(value)))


def _cfg() -> dict[str, Any]:
    try:
        from config import CONFIG

        value = CONFIG.get("ENGINE_B_PHASE2_QUALITY") or {}
        return value if isinstance(value, dict) else {}
    except Exception:
        return {}


def _last_time(candle: dict[str, Any]) -> str | None:
    for key in ("time", "timestamp", "datetime", "date"):
        if candle.get(key) is not None:
            return str(candle[key])
    return None


def _volume_grade(
    res: dict[str, Any], *, asset_type: str, aggtrade_available: bool = False
) -> dict[str, Any]:
    """Grade volume while preserving source hierarchy and missing-data truth."""
    bos = res.get("bos_data") if isinstance(res.get("bos_data"), dict) else {}
    ratio = _number(bos.get("bos_volume_ratio"), 0.0)
    threshold = max(1e-9, _number(bos.get("bos_volume_threshold"), 1.3))
    available = bool(bos.get("bos_volume_available"))
    asset = str(asset_type or res.get("asset_type") or "").lower()
    source = str(
        res.get("bos_volume_source")
        or bos.get("bos_volume_source")
        or bos.get("volume_source")
        or ""
    ).lower()

    if available and asset == "crypto":
        source_tier = "real_volume"
        authority = 1.0
    elif aggtrade_available:
        source_tier = "cvd_aggtrade"
        authority = 0.90
    elif available and asset == "forex":
        source_tier = "tick_volume"
        authority = 0.45
    elif available:
        source_tier = "relative_volume"
        authority = 0.65
    else:
        source_tier = source or "unavailable"
        authority = 0.0

    relative_strength = ratio / threshold if available and threshold > 0 else 0.0
    if not available and not aggtrade_available:
        grade, score = "unavailable", 0.0
    elif relative_strength >= 1.0 or (aggtrade_available and res.get("aggtrade_cvd_aligned")):
        grade, score = "strong", 1.0
    elif relative_strength >= 0.75 or aggtrade_available:
        grade, score = "moderate", 0.60
    else:
        grade, score = "weak", 0.25

    return {
        "grade": grade,
        "score": round(_clamp01(score * authority), 4),
        "raw_score": round(score, 4),
        "source_tier": source_tier,
        "source_authority": round(authority, 4),
        "ratio": round(ratio, 4) if available else None,
        "threshold": round(threshold, 4) if available else None,
        "hard_rejection": False,
    }


def _pullback_quality(
    candles: list[dict[str, Any]], direction: str, atr: float, anchor: float | None
) -> dict[str, Any]:
    if len(candles) < 4 or atr <= 0 or anchor is None:
        return {"available": False, "score": 0.0, "reason": "insufficient_data"}
    recent = candles[-6:]
    direction_u = str(direction or "").upper()
    lows = [_number(c.get("low")) for c in recent]
    highs = [_number(c.get("high")) for c in recent]
    ranges = [max(0.0, h - l) for h, l in zip(highs, lows)]
    close = _number(recent[-1].get("close"))
    open_ = _number(recent[-1].get("open"))
    high = highs[-1]
    low = lows[-1]
    bar_range = max(high - low, 1e-12)

    extreme = min(lows) if direction_u == "LONG" else max(highs)
    depth_atr = abs(float(anchor) - extreme) / atr
    # Best pullbacks are material but not deep enough to threaten H4 invalidation.
    depth_score = _clamp01(1.0 - abs(depth_atr - 0.50) / 0.75)
    prior_speed = sum(ranges[:-2]) / max(1, len(ranges[:-2]))
    recent_speed = sum(ranges[-2:]) / 2.0
    speed_ratio = recent_speed / max(prior_speed, 1e-12)
    speed_score = _clamp01(1.25 - speed_ratio * 0.5)
    if direction_u == "LONG":
        micro_hold = lows[-1] >= min(lows[-3:-1])
        reaction = close > open_ and (close - low) / bar_range
    else:
        micro_hold = highs[-1] <= max(highs[-3:-1])
        reaction = close < open_ and (high - close) / bar_range
    reaction_score = _clamp01(float(reaction) if reaction is not False else 0.0)
    score = 0.30 * depth_score + 0.20 * speed_score + 0.20 * float(micro_hold) + 0.30 * reaction_score
    return {
        "available": True,
        "score": round(_clamp01(score), 4),
        "depth_atr": round(depth_atr, 4),
        "depth_score": round(depth_score, 4),
        "speed_ratio": round(speed_ratio, 4),
        "speed_score": round(speed_score, 4),
        "micro_structure_hold": bool(micro_hold),
        "reaction_score": round(reaction_score, 4),
    }


def _sweep_quality(
    candles: list[dict[str, Any]], direction: str, atr: float, sweep_active: bool,
    session_quality: str, structural_overlap: bool,
    direction_aligned: bool | None = None,
) -> dict[str, Any]:
    """Grade sweep quality for the candidate direction.

    ``direction_aligned`` carries the producer's sweep_direction check:
    True/False when the raid direction is known, None when unknown. A known
    OPPOSING raid is evidence against the candidate — it must not grade as
    full-quality confluence for this side (structure gating elsewhere already
    refuses opposing sweeps; this only stops the quality layer from paying
    them). Unknown direction keeps neutral treatment.
    """
    if not sweep_active or len(candles) < 2 or atr <= 0:
        return {"available": bool(sweep_active), "score": 0.0, "grade": "none"}
    last = candles[-1]
    open_ = _number(last.get("open"))
    close = _number(last.get("close"))
    high = _number(last.get("high"))
    low = _number(last.get("low"))
    bar_range = max(high - low, 1e-12)
    direction_u = str(direction or "").upper()
    wick = min(open_, close) - low if direction_u == "LONG" else high - max(open_, close)
    wick_atr = max(0.0, wick) / atr
    close_strength = (close - low) / bar_range if direction_u == "LONG" else (high - close) / bar_range
    level = low if direction_u == "LONG" else high
    tolerance = atr * 0.10
    tests = sum(
        1
        for c in candles[-12:]
        if abs((_number(c.get("low")) if direction_u == "LONG" else _number(c.get("high"))) - level)
        <= tolerance
    )
    session_score = {"high": 1.0, "medium": 0.65, "low": 0.25}.get(str(session_quality).lower(), 0.4)
    score = (
        0.30 * _clamp01(wick_atr / 0.50)
        + 0.25 * _clamp01(close_strength)
        + 0.15 * _clamp01(tests / 3.0)
        + 0.15 * session_score
        + 0.15 * float(structural_overlap)
    )
    misaligned = direction_aligned is False
    if misaligned:
        score *= 0.30
    grade = "strong" if score >= 0.75 else "moderate" if score >= 0.50 else "weak"
    return {
        "available": True,
        "score": round(_clamp01(score), 4),
        "grade": grade,
        "direction_aligned": direction_aligned,
        "direction_misaligned": bool(misaligned),
        "wick_atr": round(wick_atr, 4),
        "close_strength": round(_clamp01(close_strength), 4),
        "tests": int(tests),
        "liquidity_type": "session" if str(session_quality).lower() in {"high", "medium"} else "structural",
        "session_quality": str(session_quality or "unknown").lower(),
        "structural_overlap": bool(structural_overlap),
    }


def _freshness_score(zone: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(zone, dict):
        return {"available": False, "score": 0.0, "age_hours": None}
    created = zone.get("created_at") or zone.get("detected_at")
    age_hours = None
    if created:
        try:
            dt = datetime.fromisoformat(str(created).replace("Z", "+00:00"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            age_hours = max(0.0, (datetime.now(timezone.utc) - dt.astimezone(timezone.utc)).total_seconds() / 3600.0)
        except Exception:
            age_hours = None
    scans = max(0.0, _number(zone.get("scan_count"), 1.0))
    if age_hours is not None:
        score = _clamp01(1.0 - age_hours / max(1.0, _number(_cfg().get("ZONE_FRESHNESS_DECAY_HOURS"), 168.0)))
    else:
        score = _clamp01(1.0 - max(0.0, scans - 1.0) / 20.0)
    return {"available": True, "score": round(score, 4), "age_hours": round(age_hours, 2) if age_hours is not None else None, "scan_count": int(scans)}


def build_phase2_context(
    *,
    res: dict[str, Any],
    candles: list[dict[str, Any]] | None,
    direction: str,
    atr: float,
    active_zone: dict[str, Any] | None,
    session_quality: str = "unknown",
    asset_type: str = "",
    aggtrade_available: bool = False,
) -> dict[str, Any]:
    """Build independently measurable Phase 2 evidence without changing gates."""
    candles = candles or []
    bos = res.get("bos_data") if isinstance(res.get("bos_data"), dict) else {}
    anchor = bos.get("last_broken_high") if str(direction).upper() == "LONG" else bos.get("last_broken_low")
    if anchor is None and isinstance(active_zone, dict):
        anchor = active_zone.get("center")
    structural_overlap = bool(res.get("ob_at_zone") or res.get("fvg_overlap") or res.get("breaker_block"))
    volume = _volume_grade(res, asset_type=asset_type, aggtrade_available=aggtrade_available)
    pullback = _pullback_quality(candles, direction, atr, _number(anchor) if anchor is not None else None)
    # Direction-resolve the raid: an opposing sweep must not grade as full
    # confluence for this side. Unknown direction stays neutral.
    _sweep_dir = str(res.get("sweep_direction") or "").upper() or None
    _sweep_aligned = None if _sweep_dir is None else (_sweep_dir == str(direction or "").upper())
    sweep = _sweep_quality(
        candles, direction, atr, bool(res.get("liquidity_sweep")), session_quality, structural_overlap,
        direction_aligned=_sweep_aligned,
    )
    lifecycle = _freshness_score(active_zone)
    breakers = res.get("breaker_blocks") if isinstance(res.get("breaker_blocks"), list) else []
    breaker_strength = 0.0
    for breaker in breakers:
        if not isinstance(breaker, dict):
            continue
        measured = max(
            _number(breaker.get("displacement_body_atr"), 0.0) / 2.0,
            _number(breaker.get("strength"), 0.0) / 100.0,
        )
        breaker_strength = max(breaker_strength, _clamp01(measured))
    lifecycle["breaker_strength"] = round(breaker_strength, 4)
    lifecycle["invalidation_rule"] = "close_beyond_far_side_with_displacement"
    return {
        "version": "engine_b.phase2.v1",
        "pullback_quality": pullback,
        "sweep_quality": sweep,
        "volume_confirmation": volume,
        "zone_lifecycle": lifecycle,
        "evaluated_at": _last_time(candles[-1]) if candles else None,
    }


def compute_confluence_density(res: dict[str, Any], direction: str) -> dict[str, Any]:
    """Weighted independent H4/M15 evidence density; never a direction source."""
    cfg = _cfg()
    weights = cfg.get("CONFLUENCE_WEIGHTS") if isinstance(cfg.get("CONFLUENCE_WEIGHTS"), dict) else {}
    defaults = {
        "structure_break": 24.0,
        "order_block": 14.0,
        "fvg": 13.0,
        "breaker": 9.0,
        "liquidity_sweep": 13.0,
        "sequence": 8.0,
        "volume": 10.0,
        "pullback": 9.0,
    }
    weights = {key: max(0.0, _number(weights.get(key), value)) for key, value in defaults.items()}
    direction_u = str(direction or "").upper()
    seq = str(res.get("current_swing_sequence") or "")
    seq_aligned = (direction_u == "LONG" and seq == "HH_HL") or (direction_u == "SHORT" and seq == "LH_LL")
    phase2 = res.get("phase2_quality") if isinstance(res.get("phase2_quality"), dict) else {}
    volume = phase2.get("volume_confirmation") if isinstance(phase2.get("volume_confirmation"), dict) else {}
    pullback = phase2.get("pullback_quality") if isinstance(phase2.get("pullback_quality"), dict) else {}
    components = {
        "structure_break": 1.0 if (res.get("bos_confirmed") or res.get("choch_confirmed")) else 0.0,
        "order_block": 1.0 if res.get("ob_at_zone") else 0.0,
        "fvg": _number((res.get("fvg_context") or {}).get("reaction_confirmed"), 0.0) if isinstance(res.get("fvg_context"), dict) else float(bool(res.get("fvg_overlap"))),
        "breaker": 1.0 if res.get("breaker_block") else 0.0,
        "liquidity_sweep": _number((phase2.get("sweep_quality") or {}).get("score"), 0.0),
        "sequence": 1.0 if seq_aligned else 0.0,
        "volume": _number(volume.get("score"), 0.0),
        "pullback": _number(pullback.get("score"), 0.0),
    }
    max_score = sum(weights.values()) or 1.0
    score = sum(weights[key] * _clamp01(components[key]) for key in weights) / max_score * 100.0
    threshold = _number(cfg.get("HIGH_CONVICTION_MIN_DENSITY"), 65.0)
    return {
        "score": round(max(0.0, min(100.0, score)), 2),
        "components": {key: round(_clamp01(value), 4) for key, value in components.items()},
        "weights": weights,
        "high_conviction_min": round(threshold, 2),
        "high_conviction_eligible": bool(score >= threshold),
        "hard_gate": False,
    }


def resolve_adaptive_thresholds(
    res: dict[str, Any], direction: str, *, regime: str, base_min_room: float, base_min_rr: float
) -> dict[str, Any]:
    """Bounded regime/quality adjustment for room and RR; config-reversible."""
    cfg = _cfg()
    density = compute_confluence_density(res, direction)
    regime_key = str(regime or "").upper()
    # Forex uses the same ADX boundaries as Engine B's live regime diagnostic,
    # so room/RR and min-score regime semantics cannot silently disagree.
    if str(res.get("asset_type") or "").lower() == "forex":
        adx = res.get("d1_adx") if res.get("d1_adx") is not None else res.get("h4_adx")
        if adx is not None:
            try:
                from config import CONFIG

                naked = CONFIG.get("NAKED_ENGINE") or {}
                trending_at = _number(naked.get("forex_adx_trending_threshold"), 30.0)
                normal_at = _number(naked.get("forex_adx_normal_threshold"), 20.0)
                adx_value = float(adx)
                regime_key = "TRENDING" if adx_value >= trending_at else "NORMAL" if adx_value >= normal_at else "RANGING"
            except Exception:
                pass
    phase2_available = isinstance(res.get("phase2_quality"), dict) and not res["phase2_quality"].get("error")
    enabled = bool(cfg.get("APPLY_ADAPTIVE_ROOM_RR", False)) and phase2_available and regime_key in {
        "TRENDING", "RANGING", "HIGH_VOLATILITY", "LOW_VOLATILITY"
    }
    room_mult = {"TRENDING": 0.90, "RANGING": 1.10, "HIGH_VOLATILITY": 1.10}.get(regime_key, 1.0)
    rr_mult = {"TRENDING": 0.97, "RANGING": 1.05, "HIGH_VOLATILITY": 1.05}.get(regime_key, 1.0)
    density_score = _number(density.get("score"), 0.0)
    if density_score >= 75.0:
        rr_mult *= 0.97
    elif density_score < 40.0:
        rr_mult *= 1.05
    room_mult = max(0.85, min(1.15, room_mult))
    rr_mult = max(0.90, min(1.10, rr_mult))
    if not enabled:
        room_mult = rr_mult = 1.0
    return {
        "enabled": enabled,
        "regime": regime_key or "UNKNOWN",
        "base_min_room_atr": round(float(base_min_room), 4),
        "effective_min_room_atr": round(max(0.0, float(base_min_room) * room_mult), 4),
        "room_multiplier": round(room_mult, 4),
        "base_min_rr": round(float(base_min_rr), 4),
        "effective_min_rr": round(max(0.0, float(base_min_rr) * rr_mult), 4),
        "rr_multiplier": round(rr_mult, 4),
        "confluence_density": density,
    }


def build_invalidation_context(res: dict[str, Any], direction: str) -> dict[str, Any]:
    """Explicit HTF/M15 invalidation and early-warning state."""
    direction_u = str(direction or "").upper()
    bos = res.get("bos_data") if isinstance(res.get("bos_data"), dict) else {}
    opposing_bos = bool(bos.get("bos_bear")) if direction_u == "LONG" else bool(bos.get("bos_bull"))
    opposing_trigger = bool(res.get("opposing_trigger_ok")) or (
        str(res.get("opposing_trigger_pattern") or "NONE").upper() not in {"", "NONE"}
    )
    try:
        from config import CONFIG

        time_bars = CONFIG.get("ENGINE_B_TIME_EXIT_BARS") or {}
    except Exception:
        time_bars = {}
    style = str(res.get("style") or "intraday").lower()
    return {
        "h4_invalidation_price": res.get("recommended_stop_loss"),
        "h4_invalidation_condition": "close_beyond_structural_stop_with_displacement",
        "m15_invalidation_condition": "opposing_bos_or_choch_after_entry",
        "opposing_structure_warning": bool(opposing_bos or opposing_trigger),
        "opposing_bos": opposing_bos,
        "opposing_trigger": opposing_trigger,
        "time_exit_bars": time_bars.get(style, 18) if isinstance(time_bars, dict) else 18,
    }
