"""Diagnostic-first candle understanding layer — facts, gates, report-only scores.

Never emits trade signals, never sizes trades, never overrides Engine A/B/D gates.
"""

from __future__ import annotations

from typing import Any

from athena_ai.price_action_facts import (
    derive_candle_anatomy,
    derive_candle_timeframe_confidence,
    derive_effort_vs_result,
    derive_fvg_state,
    derive_ob_fvg_confluence,
    derive_profile_location,
    derive_regime_candle_gate,
    derive_sweep_event,
    derive_structure_event,
    derive_volume_behavior,
    derive_wick_event,
    _candle_field,
    _to_float,
)


def _location_quality_multiplier(
    profile_location: dict[str, Any],
    *,
    at_liquidity_pool: bool = False,
) -> float:
    loc = profile_location.get("location")
    if at_liquidity_pool:
        return 1.0
    if loc in ("above_vah", "below_val", "near_poc"):
        return 0.95
    if loc == "inside_va":
        return 0.55
    if profile_location.get("state") == "unknown":
        return 0.5
    return 0.65


def _regime_eligibility_multiplier(
    gate: dict[str, Any],
    direction: str | None,
) -> float:
    if not direction:
        return 1.0
    d = str(direction).upper()
    if d == "LONG" and not gate.get("allow_long"):
        return 0.0
    if d == "SHORT" and not gate.get("allow_short"):
        return 0.0
    if gate.get("suppression_reasons"):
        return 0.65
    return 1.0


def _raw_candle_score(
    anatomy: dict[str, Any],
    sweep: dict[str, Any],
    effort: dict[str, Any],
    wick: dict[str, Any],
) -> float:
    score = 0.0
    last = anatomy.get("last") or {}
    if last.get("is_displacement_shape"):
        score += 0.25
    if last.get("is_rejection_shape"):
        score += 0.15
    if sweep.get("has_sweep") and sweep.get("confidence") in ("high", "medium"):
        score += 0.35
    if effort.get("has_absorption") and effort.get("confidence") in ("high", "medium"):
        score += 0.2
    if wick.get("type") in ("upper_rejection", "lower_rejection"):
        score += 0.1
    return min(1.0, score)


def _extract_levels_from_ctx(engine_ctx: dict[str, Any]) -> tuple[dict[str, float | None], list[float]]:
    """Pull named levels and liquidity pools from engine context."""
    named: dict[str, float | None] = {}
    pools: list[float] = []
    structure = engine_ctx.get("structure_context") or {}
    profile = structure.get("profile") or engine_ctx.get("profile") or {}

    for key in ("poc", "vah", "val", "pdh", "pdl", "session_high", "session_low"):
        val = _to_float(profile.get(key) or structure.get(key))
        if val is not None:
            named[key] = val
            pools.append(val)

    swing = structure.get("swing") or {}
    for key in ("swing_high", "swing_low", "prior_high", "prior_low"):
        val = _to_float(swing.get(key) or structure.get(key))
        if val is not None:
            named[key] = val
            pools.append(val)

    for key in ("support", "resistance", "breakout_level"):
        val = _to_float((engine_ctx.get("named_levels") or {}).get(key))
        if val is not None:
            named[key] = val
            pools.append(val)

    return named, pools


def derive_candle_understanding(
    *,
    candles: list[dict] | None,
    engine_ctx: dict[str, Any] | None = None,
    direction: str | None = None,
    timeframe: str | None = None,
    engine: str = "A",
    named_levels: dict[str, float | None] | None = None,
    liquidity_pools: list[float] | None = None,
    swing_levels: dict[str, float | None] | None = None,
    order_blocks: list[dict] | None = None,
    fvgs: list[dict] | None = None,
    cvd: dict[str, Any] | None = None,
    correlated_ctx: dict[str, Any] | None = None,
    config: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Compose full candle-understanding diagnostics and report-only scores."""
    from config import CONFIG

    cfg = config or CONFIG
    enabled = bool(cfg.get("CANDLE_UNDERSTANDING_ENABLED", True))
    report_only = bool(cfg.get("CANDLE_UNDERSTANDING_REPORT_ONLY", True))
    live_weight = float(cfg.get("CANDLE_UNDERSTANDING_LIVE_WEIGHT", 0.0) or 0.0)

    engine_ctx = engine_ctx or {}
    ctx_named, ctx_pools = _extract_levels_from_ctx(engine_ctx)
    merged_named = {**ctx_named, **(named_levels or {})}
    merged_pools = list(dict.fromkeys((liquidity_pools or []) + ctx_pools))

    atr_block = engine_ctx.get("atr") or {}
    atr_value = _to_float(atr_block.get("atr_chart_tf") or atr_block.get("atr_value"))

    structure_ctx = engine_ctx.get("structure_context") or {}
    profile = structure_ctx.get("profile") or engine_ctx.get("profile") or {}
    poc = _to_float(merged_named.get("poc") or profile.get("poc"))
    vah = _to_float(merged_named.get("vah") or profile.get("vah"))
    val = _to_float(merged_named.get("val") or profile.get("val"))

    last_close = _candle_field(candles[-1], "close", "c") if candles else None
    profile_loc = derive_profile_location(last_close, poc, vah, val, atr_value)

    anatomy = derive_candle_anatomy(candles)
    regime_gate = derive_regime_candle_gate(
        candles,
        engine_ctx,
        merged_named,
        profile_location=profile_loc,
    )
    sweep = derive_sweep_event(
        candles,
        swing_levels=swing_levels,
        named_levels=merged_named,
        liquidity_pools=merged_pools or None,
        atr=atr_value,
        correlated_ctx=correlated_ctx,
        engine_ctx=engine_ctx,
    )
    effort = derive_effort_vs_result(candles, cvd=cvd)
    tf_conf = derive_candle_timeframe_confidence(timeframe, engine=engine)
    wick = derive_wick_event(candles, atr_value)
    volume = derive_volume_behavior(candles)
    structure = derive_structure_event(engine_ctx)

    ob_list = order_blocks
    fvg_list = fvgs
    if ob_list is None:
        ob_list = structure_ctx.get("order_blocks") or []
    if fvg_list is None:
        fvg_list = structure_ctx.get("active_fvgs") or structure_ctx.get("fvgs") or []

    confluence = derive_ob_fvg_confluence(ob_list, fvg_list)

    fvg_states: list[dict[str, Any]] = []
    if last_close is not None and isinstance(fvg_list, list):
        for fvg in fvg_list[:5]:
            if isinstance(fvg, dict):
                fvg_states.append(derive_fvg_state(fvg, last_close))

    location_mult = _location_quality_multiplier(
        profile_loc,
        at_liquidity_pool=bool(sweep.get("has_sweep")),
    )
    regime_mult = _regime_eligibility_multiplier(regime_gate, direction)
    raw_score = _raw_candle_score(anatomy, sweep, effort, wick)
    diagnostic_score = raw_score * tf_conf.get("multiplier", 1.0) * location_mult * regime_mult
    effective_score = diagnostic_score * live_weight

    suppression = list(regime_gate.get("suppression_reasons") or [])
    if not sweep.get("has_sweep") and sweep.get("warnings"):
        if "no_named_pool" in sweep["warnings"]:
            suppression.append("sweep_unconfirmed_no_named_pool")

    score_block = {
        "raw_candle_score": round(raw_score, 4),
        "diagnostic_candle_score": round(diagnostic_score, 4),
        "effective_candle_score": round(effective_score, 4),
        "timeframe_confidence": tf_conf.get("multiplier"),
        "regime_eligibility_multiplier": regime_mult,
        "location_quality_multiplier": location_mult,
        "suppression_reasons": suppression,
        "research_only": True,
        "report_only": report_only,
        "live_weight_applied": live_weight,
    }

    if not enabled:
        return {
            "enabled": False,
            "read_order": [],
            "facts": {},
            "scores": score_block,
            "advisory_only": True,
        }

    return {
        "enabled": True,
        "advisory_only": True,
        "read_order": [
            "regime_gate",
            "profile_location",
            "candle_anatomy",
            "sweep_event",
            "structure_event",
            "fvg_states",
            "ob_fvg_confluence",
            "effort_vs_result",
            "volume_behavior",
            "directional_view",
        ],
        "facts": {
            "regime_gate": regime_gate,
            "profile_location": profile_loc,
            "candle_anatomy": anatomy,
            "sweep_event": sweep,
            "structure_event": structure,
            "fvg_states": fvg_states,
            "ob_fvg_confluence": confluence,
            "effort_vs_result": effort,
            "volume_behavior": volume,
            "wick_event": wick,
        },
        "scores": score_block,
        "timeframe_confidence": tf_conf,
        "_meta": {
            "timeframe": timeframe,
            "engine": engine,
            "bars_provided": len(candles) if candles else 0,
            "atr_value": atr_value,
        },
    }


def render_candle_understanding_summary(block: dict[str, Any]) -> str:
    """Compact text summary for Vision algo_context."""
    if not block or not block.get("enabled"):
        return ""
    facts = block.get("facts") or {}
    scores = block.get("scores") or {}
    lines = ["CANDLE_UNDERSTANDING (diagnostic, report-only):"]
    gate = facts.get("regime_gate") or {}
    lines.append(
        f"  regime={gate.get('regime')} adx={gate.get('adx')} ha_streak={gate.get('ha_streak')} "
        f"allow_long={gate.get('allow_long')} allow_short={gate.get('allow_short')}"
    )
    loc = facts.get("profile_location") or {}
    lines.append(f"  location={loc.get('location')} poc_dist_atr={loc.get('distance_to_poc_atr')}")
    anat = facts.get("candle_anatomy") or {}
    last = anat.get("last") or {}
    if last:
        lines.append(
            f"  last_candle: dir={last.get('direction')} body_ratio={last.get('body_ratio'):.2f} "
            f"dominant_wick={last.get('dominant_wick')} rejection={last.get('is_rejection_shape')}"
        )
    sweep = facts.get("sweep_event") or {}
    lines.append(
        f"  sweep: has={sweep.get('has_sweep')} side={sweep.get('side')} "
        f"pool={sweep.get('pool_source')}@{sweep.get('pool_level')} conf={sweep.get('confidence')}"
    )
    effort = facts.get("effort_vs_result") or {}
    lines.append(
        f"  effort_vs_result: absorption={effort.get('has_absorption')} side={effort.get('side')} "
        f"vol_ratio={effort.get('volume_ratio')}"
    )
    lines.append(
        f"  scores: raw={scores.get('raw_candle_score')} effective={scores.get('effective_candle_score')} "
        f"(live_weight={scores.get('live_weight_applied')})"
    )
    return "\n".join(lines)
