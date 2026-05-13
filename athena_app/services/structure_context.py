from __future__ import annotations

from typing import Any

import config


_DEFAULT_COMPONENT_BONUSES = {
    "zone_proximity": 0.08,
    "ob_at_zone": 0.05,
    "fvg_overlap": 0.05,
    "liquidity_sweep": 0.04,
    "independent_direction_aligned": 0.04,
    "independent_direction_opposed": -0.08,
}
_DEFAULT_MULT_BOUNDS = {"min": 0.85, "max": 1.20}


def _float_value(value: Any, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return float(default)


def _component_bonuses() -> dict[str, float]:
    cfg = config.CONFIG.get("ENGINE_B_STRUCTURE_SCORE_COMPONENT_BONUSES", {}) or {}
    if not isinstance(cfg, dict):
        cfg = {}
    return {
        key: _float_value(cfg.get(key), default)
        for key, default in _DEFAULT_COMPONENT_BONUSES.items()
    }


def _mult_bounds() -> tuple[float, float]:
    cfg = config.CONFIG.get("ENGINE_B_STRUCTURE_SCORE_MULT_BOUNDS", {}) or {}
    if not isinstance(cfg, dict):
        cfg = {}
    lower = _float_value(cfg.get("min"), _DEFAULT_MULT_BOUNDS["min"])
    upper = _float_value(cfg.get("max"), _DEFAULT_MULT_BOUNDS["max"])
    if upper < lower:
        lower, upper = upper, lower
    return lower, upper


def _forex_session_score_bonus(structure_result: dict[str, Any]) -> float:
    session_cfg = config.CONFIG.get("ENGINE_B_FOREX_SESSION_STRUCTURE_WEIGHTING", {}) or {}
    if not isinstance(session_cfg, dict):
        session_cfg = {}
    if not bool(session_cfg.get("SCORE_INFLUENCE_ENABLED", False)):
        return 0.0
    if str(structure_result.get("asset_type") or "").lower() != "forex":
        return 0.0
    session_ctx = structure_result.get("forex_session_structure") or {}
    if not isinstance(session_ctx, dict):
        return 0.0
    return _float_value(session_ctx.get("score_bonus"), 0.0)


def _equity_session_score_bonus(structure_result: dict[str, Any]) -> float:
    session_cfg = config.CONFIG.get("ENGINE_B_EQUITY_SESSION_STRUCTURE_WEIGHTING", {}) or {}
    if not isinstance(session_cfg, dict):
        session_cfg = {}
    if not bool(session_cfg.get("SCORE_INFLUENCE_ENABLED", False)):
        return 0.0
    session_ctx = structure_result.get("equity_session_structure") or {}
    if not isinstance(session_ctx, dict):
        return 0.0
    if str(session_ctx.get("asset_class") or "").lower() not in {
        "stock",
        "index",
        "etf",
        "etf_bond",
    }:
        return 0.0
    return _float_value(session_ctx.get("score_bonus"), 0.0)


def _influence_level_multiplier() -> float:
    level = str(config.CONFIG.get("ENGINE_B_STRUCTURE_INFLUENCE_LEVEL", "standard") or "standard").lower()
    cfg = config.CONFIG.get("ENGINE_B_STRUCTURE_INFLUENCE_LEVEL_MULTIPLIERS", {}) or {}
    if not isinstance(cfg, dict):
        cfg = {}
    return max(0.0, _float_value(cfg.get(level), 1.0))


def apply_structure_context_to_score(
    structure_result: dict[str, Any] | None,
    *,
    direction: str,
    base_score: float,
    max_score: float,
) -> dict[str, Any]:
    """Apply a modest score adjustment from explicit structural-zone context.

    The input is the output of ``market_structure.NakedEngine.analyze_structure``.
    This keeps Engine A / forex scoring aware of explicit H1/H4/D1 structural
    context without replacing their primary scoring models.
    """
    base = float(base_score or 0.0)
    cap = float(max_score or 0.0)
    out: dict[str, Any] = {
        "applied": False,
        "multiplier": 1.0,
        "adjusted_score": base,
        "components": {
            "structural_verdict": None,
            "zone_proximity": False,
            "ob_at_zone": False,
            "fvg_overlap": False,
            "liquidity_sweep": False,
            "independent_direction": None,
            "independent_direction_alignment": "none",
            "structure_tf": None,
            "asset_type": None,
            "forex_session": None,
            "forex_session_score_bonus": 0.0,
            "equity_session": None,
            "equity_session_score_bonus": 0.0,
            "structure_influence_level": None,
            "structure_influence_multiplier": 1.0,
            "confluence_bonus": 0.0,
        },
    }
    if not isinstance(structure_result, dict):
        return out

    verdict = structure_result.get("structural_verdict")
    components = out["components"]
    components["structural_verdict"] = verdict
    components["structure_tf"] = structure_result.get("structure_tf")
    components["independent_direction"] = structure_result.get("engine_b_independent_direction")
    components["asset_type"] = structure_result.get("asset_type")
    forex_session_ctx = structure_result.get("forex_session_structure") or {}
    if isinstance(forex_session_ctx, dict):
        components["forex_session"] = forex_session_ctx.get("session")
    equity_session_ctx = structure_result.get("equity_session_structure") or {}
    if isinstance(equity_session_ctx, dict):
        components["equity_session"] = equity_session_ctx.get("session")

    if verdict != "CLEAR":
        return out

    zone_proximity = bool(
        structure_result.get("zone_touched") or structure_result.get("near_active_zone")
    )
    ob_at_zone = bool(structure_result.get("ob_at_zone"))
    fvg_overlap = bool(structure_result.get("fvg_overlap"))
    liquidity_sweep = bool(structure_result.get("liquidity_sweep"))
    independent_direction = str(structure_result.get("engine_b_independent_direction") or "").upper()
    direction_u = str(direction or "").upper()

    components["zone_proximity"] = zone_proximity
    components["ob_at_zone"] = ob_at_zone
    components["fvg_overlap"] = fvg_overlap
    components["liquidity_sweep"] = liquidity_sweep

    if not bool(config.CONFIG.get("ENGINE_B_STRUCTURE_SCORE_INFLUENCE_ENABLED", False)):
        return out

    influence_mult = _influence_level_multiplier()
    components["structure_influence_level"] = str(
        config.CONFIG.get("ENGINE_B_STRUCTURE_INFLUENCE_LEVEL", "standard") or "standard"
    ).lower()
    components["structure_influence_multiplier"] = round(influence_mult, 6)

    bonuses = _component_bonuses()
    bonus = 0.0
    if zone_proximity:
        bonus += bonuses["zone_proximity"]
    if ob_at_zone:
        bonus += bonuses["ob_at_zone"]
    if fvg_overlap:
        bonus += bonuses["fvg_overlap"]
    if liquidity_sweep:
        bonus += bonuses["liquidity_sweep"]

    if independent_direction in {"LONG", "SHORT"}:
        if independent_direction == direction_u:
            bonus += bonuses["independent_direction_aligned"]
            components["independent_direction_alignment"] = "aligned"
        else:
            bonus += bonuses["independent_direction_opposed"]
            components["independent_direction_alignment"] = "opposed"

    # Optional, conservative forex uplift for genuine multi-factor structure.
    # Disabled by default so current score behavior is unchanged.
    confluence_cfg = config.CONFIG.get("ENGINE_B_STRUCTURE_SCORE_CONFLUENCE", {}) or {}
    if not isinstance(confluence_cfg, dict):
        confluence_cfg = {}
    if (
        bool(confluence_cfg.get("ENABLED", False))
        and zone_proximity
        and ob_at_zone
        and fvg_overlap
        and components["independent_direction_alignment"] == "aligned"
    ):
        confluence_bonus = _float_value(confluence_cfg.get("BONUS"), 0.03)
        components["confluence_bonus"] = round(confluence_bonus, 6)
        bonus += confluence_bonus

    session_bonus = _forex_session_score_bonus(structure_result)
    if session_bonus:
        components["forex_session_score_bonus"] = round(session_bonus, 6)
        bonus += session_bonus
    equity_session_bonus = _equity_session_score_bonus(structure_result)
    if equity_session_bonus:
        components["equity_session_score_bonus"] = round(equity_session_bonus, 6)
        bonus += equity_session_bonus

    bonus *= influence_mult
    min_mult, max_mult = _mult_bounds()
    multiplier = min(max_mult, max(min_mult, 1.0 + bonus))
    adjusted = base
    if cap > 0:
        adjusted = max(0.0, min(cap, base * multiplier))

    out["applied"] = True
    out["multiplier"] = round(multiplier, 6)
    out["adjusted_score"] = round(adjusted, 6)
    return out
