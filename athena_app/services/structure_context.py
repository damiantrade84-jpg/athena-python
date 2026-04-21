from __future__ import annotations

from typing import Any


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
        },
    }
    if not isinstance(structure_result, dict):
        return out

    verdict = structure_result.get("structural_verdict")
    components = out["components"]
    components["structural_verdict"] = verdict
    components["structure_tf"] = structure_result.get("structure_tf")
    components["independent_direction"] = structure_result.get("engine_b_independent_direction")

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

    bonus = 0.0
    if zone_proximity:
        bonus += 0.08
    if ob_at_zone:
        bonus += 0.05
    if fvg_overlap:
        bonus += 0.05
    if liquidity_sweep:
        bonus += 0.04

    if independent_direction in {"LONG", "SHORT"}:
        if independent_direction == direction_u:
            bonus += 0.04
            components["independent_direction_alignment"] = "aligned"
        else:
            bonus -= 0.08
            components["independent_direction_alignment"] = "opposed"

    multiplier = min(1.20, max(0.85, 1.0 + bonus))
    adjusted = base
    if cap > 0:
        adjusted = max(0.0, min(cap, base * multiplier))

    out["applied"] = True
    out["multiplier"] = round(multiplier, 6)
    out["adjusted_score"] = round(adjusted, 6)
    return out
