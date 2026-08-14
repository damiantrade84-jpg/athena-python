"""Engine B vs chart vs AI verdict comparison block."""

from __future__ import annotations

import re
from typing import Any

from ai_review.engine_a_verdict import (
    _coerce_str_list,
    _filter_false_stale_downgrades,
    _infer_chart_contradicts_direction,
    _infer_entry_timing,
    _map_final_decision,
    _to_bool,
    _to_float,
)

def _infer_chart_confirms_direction(ai_review: dict[str, Any], engine_b_ctx: dict[str, Any]) -> bool | None:
    structured = ai_review.get("structured") or {}
    comparison = structured.get("engineBVerdictComparison") or structured.get("engine_b_verdict_comparison")
    if isinstance(comparison, dict) and comparison.get("chartConfirmsEngineBDirection") is not None:
        return _to_bool(comparison.get("chartConfirmsEngineBDirection"))
    direction = str(engine_b_ctx.get("direction") or "").upper()
    if direction not in ("LONG", "SHORT"):
        return None
    text = " ".join(
        str(ai_review.get(k) or "")
        for k in ("visual_confirmation", "chartReadSummary", "visualConfirmation")
    ).lower()
    if not text:
        return None
    if direction == "LONG" and (
        any(re.search(rf"\b{w}\b", text) for w in ("bearish", "short", "downside"))
        or "reject long" in text
    ):
        return False
    if direction == "SHORT" and (
        any(re.search(rf"\b{w}\b", text) for w in ("bullish", "long", "upside"))
        or "reject short" in text
    ):
        return False
    if re.search(r"\b(confirm|support|align)\b", text):
        return True
    return None


def _comparison_verdict(
    *,
    engine_b_provided: bool,
    chart_confirms: bool | None,
    chart_contradicts_dir: bool | None,
    chart_contradicts_timing: bool | None,
    engine_passed: bool | None,
    final_decision: str | None,
) -> str:
    if not engine_b_provided:
        return "engine_b_missing"
    if chart_contradicts_dir is True:
        return "engine_b_contradicted"
    if chart_confirms is True and engine_passed and final_decision == "trade":
        return "engine_b_confirmed"
    if chart_contradicts_timing is True and engine_passed:
        return "engine_b_direction_confirmed_entry_rejected"
    if chart_confirms is True and engine_passed and final_decision in ("wait", "watch"):
        return "engine_b_direction_confirmed_wait"
    if chart_confirms is False:
        return "engine_b_contradicted"
    return "mixed"


def build_engine_b_verdict_comparison(
    engine_b_ctx: dict[str, Any],
    ai_review: dict[str, Any],
    *,
    model_comparison: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build comparison with immutable server-owned Engine B facts."""
    snapshots = engine_b_ctx.get("engine_snapshots") or {}
    eb = snapshots.get("engineB") or {}
    model = dict(model_comparison or {})

    direction = str(eb.get("direction") or engine_b_ctx.get("direction") or "NONE").upper()
    score = _to_float(eb.get("score") if eb.get("score") is not None else engine_b_ctx.get("confluence_score"))
    max_score = _to_float(eb.get("maxScore") if eb.get("maxScore") is not None else engine_b_ctx.get("max_score_override"))
    threshold = _to_float(eb.get("threshold") if eb.get("threshold") is not None else engine_b_ctx.get("threshold"))
    normalized = _to_float(eb.get("normalizedScore"))
    passed = _to_bool(eb.get("passed") if eb.get("passed") is not None else engine_b_ctx.get("passed"))
    engine_b_provided = direction in ("LONG", "SHORT") and score is not None
    bias_valid = direction in ("LONG", "SHORT") and (
        passed is True or (score is not None and threshold is not None)
    )

    chart_confirms = _to_bool(model.get("chartConfirmsEngineBDirection"))
    if chart_confirms is None:
        chart_confirms = _infer_chart_confirms_direction(ai_review, engine_b_ctx)

    chart_contradicts_dir = _to_bool(model.get("chartContradictsEngineBDirection"))
    if chart_contradicts_dir is None:
        chart_contradicts_dir = _infer_chart_contradicts_direction(ai_review)

    timing_confirms, timing_contradicts = _infer_entry_timing(ai_review)
    timing_confirms = _to_bool(model.get("chartConfirmsEntryTiming")) if model.get("chartConfirmsEntryTiming") is not None else timing_confirms
    timing_contradicts = _to_bool(model.get("chartContradictsEntryTiming")) if model.get("chartContradictsEntryTiming") is not None else timing_contradicts

    human_raw = str(ai_review.get("human_action") or "wait")
    final_decision = _map_final_decision(model.get("finalDecision"))
    if final_decision is None:
        final_decision = _map_final_decision(human_raw)

    ai_agrees = _to_bool(model.get("aiAgreesWithEngineB"))
    if ai_agrees is None:
        if chart_contradicts_dir is True:
            ai_agrees = False
        elif chart_confirms is True and final_decision == "trade" and passed:
            ai_agrees = True
        else:
            ai_agrees = None

    ai_downgraded = _to_bool(model.get("aiDowngradedEngineB"))
    if ai_downgraded is None:
        ai_downgraded = bool(
            passed
            and (
                final_decision in ("wait", "watch", "reject")
                or timing_contradicts is True
                or chart_contradicts_dir is True
            )
        )

    # AI review is advisory and cannot promote a deterministic Engine B failure.
    ai_upgraded = False

    comparison = _comparison_verdict(
        engine_b_provided=bool(engine_b_provided),
        chart_confirms=chart_confirms,
        chart_contradicts_dir=chart_contradicts_dir,
        chart_contradicts_timing=timing_contradicts,
        engine_passed=passed,
        final_decision=final_decision,
    )

    downgrade_reasons = _coerce_str_list(model.get("downgradeReasons"))
    if not downgrade_reasons and ai_downgraded:
        if timing_contradicts:
            downgrade_reasons.append("Chart entry timing poor or extended")
        if chart_contradicts_dir:
            downgrade_reasons.append("Chart contradicts Engine B direction")
        if final_decision in ("wait", "watch") and passed:
            downgrade_reasons.append("Engine B passed but human action is wait/watch")
    downgrade_reasons = _filter_false_stale_downgrades(downgrade_reasons, engine_b_ctx)

    upgrade_reasons: list[str] = []

    final_reason = str(model.get("finalReason") or "").strip() or None
    if not final_reason:
        if comparison == "engine_b_direction_confirmed_entry_rejected":
            final_reason = (
                f"Engine B {direction} is directionally confirmed, but immediate entry is downgraded. "
                f"Final decision: {(final_decision or 'wait').upper()}."
            )
        elif comparison == "engine_b_direction_confirmed_wait":
            final_reason = (
                f"Engine B {direction} is directionally confirmed; wait for location/timing. "
                f"Final decision: {(final_decision or 'wait').upper()}."
            )
        elif comparison == "engine_b_confirmed":
            final_reason = f"Engine B {direction} confirmed by chart. Final decision: {(final_decision or 'trade').upper()}."
        elif comparison == "engine_b_contradicted":
            final_reason = f"Chart contradicts Engine B {direction}. Final decision: {(final_decision or 'reject').upper()}."
        elif comparison == "engine_b_missing":
            final_reason = "Engine B context insufficient for comparison"
        else:
            reasons = ai_review.get("supporting_reasons") or []
            if isinstance(reasons, list) and reasons:
                final_reason = str(reasons[0])[:500]

    struct = engine_b_ctx.get("structure_context") or {}
    structural_verdict = struct.get("structural_verdict") if isinstance(struct, dict) else None

    return {
        "engineBProvided": bool(engine_b_provided),
        "engineBBiasValid": bias_valid,
        "engineBPassed": passed,
        "engineBDirection": direction if direction != "NONE" else None,
        "engineBScore": score,
        "engineBMaxScore": max_score,
        "engineBThreshold": threshold,
        "engineBNormalizedScore": normalized,
        "engineBStructuralVerdict": structural_verdict,
        "chartConfirmsEngineBDirection": chart_confirms,
        "chartContradictsEngineBDirection": chart_contradicts_dir,
        "chartConfirmsEntryTiming": timing_confirms,
        "chartContradictsEntryTiming": timing_contradicts,
        "aiAgreesWithEngineB": ai_agrees,
        "aiDowngradedEngineB": bool(ai_downgraded),
        "aiUpgradedEngineB": bool(ai_upgraded),
        "comparisonVerdict": comparison,
        "downgradeReasons": downgrade_reasons,
        "upgradeReasons": upgrade_reasons,
        "finalDecision": final_decision,
        "finalReason": final_reason,
        "modelClaims": {
            key: model.get(key)
            for key in (
                "engineBProvided",
                "engineBBiasValid",
                "engineBPassed",
                "engineBDirection",
                "engineBScore",
                "engineBMaxScore",
                "engineBThreshold",
                "engineBNormalizedScore",
                "comparisonVerdict",
                "aiUpgradedEngineB",
                "upgradeReasons",
            )
            if model.get(key) is not None
        },
    }
