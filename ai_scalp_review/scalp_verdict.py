"""Engine D setup vs chart vs AI verdict comparison."""

from __future__ import annotations

from typing import Any

_VALID_VERDICTS = frozenset(
    {
        "setup_confirmed",
        "direction_confirmed_entry_rejected",
        "setup_contradicted",
        "source_quality_insufficient",
        "setup_missing",
        "mixed",
        "unknown",
    }
)


def _text_blob(ai_review: dict[str, Any]) -> str:
    parts: list[str] = []
    for key in ("visual_confirmation", "visual_contradiction", "entry_quality", "source_quality_assessment"):
        val = ai_review.get(key)
        if val:
            parts.append(str(val))
    for key in ("supporting_reasons", "risks"):
        items = ai_review.get(key)
        if isinstance(items, list):
            parts.extend(str(i) for i in items)
    return " ".join(parts).lower()


def build_scalp_verdict_comparison(
    engine_d_ctx: dict[str, Any],
    ai_review: dict[str, Any],
    *,
    model_comparison: dict[str, Any] | None = None,
) -> dict[str, Any]:
    mc = model_comparison if isinstance(model_comparison, dict) else {}
    setup = engine_d_ctx.get("scalpSetup") or {}
    source = engine_d_ctx.get("sourceContract") or {}
    direction = str(engine_d_ctx.get("direction") or "NONE").upper()
    text = _text_blob(ai_review)

    chart_confirms = mc.get("chartConfirmsDirection")
    if chart_confirms is None:
        if ai_review.get("visual_contradiction"):
            chart_confirms = False
        elif any(w in text for w in ("confirm", "aligned", "supports", "agree")):
            chart_confirms = True

    chart_contradicts = mc.get("chartContradictsDirection")
    if chart_contradicts is None:
        chart_contradicts = bool(ai_review.get("visual_contradiction"))

    entry_confirms = mc.get("chartConfirmsEntryTiming")
    entry_contradicts = mc.get("chartContradictsEntryTiming")
    if entry_contradicts is None:
        entry_contradicts = any(
            p in text for p in ("late entry", "poor entry", "chasing", "extended", "bad entry")
        )
    if entry_confirms is None and not entry_contradicts:
        entry_confirms = bool(ai_review.get("visual_confirmation"))

    source_ok = mc.get("sourceQualitySupportsReview")
    if source_ok is None:
        source_ok = source.get("strictOrderflowSourcePass") is not False

    comparison = str(mc.get("comparisonVerdict") or "unknown")
    if comparison not in _VALID_VERDICTS:
        comparison = "unknown"
    if not engine_d_ctx.get("setup_available"):
        comparison = "setup_missing"
    elif source_ok is False:
        comparison = "source_quality_insufficient"
    elif chart_contradicts:
        comparison = "setup_contradicted"
    elif chart_confirms and entry_contradicts:
        comparison = "direction_confirmed_entry_rejected"
    elif chart_confirms and not entry_contradicts:
        comparison = "setup_confirmed"

    human = str(ai_review.get("human_action") or "wait").lower()
    final_decision = mc.get("finalDecision")
    if not final_decision:
        rev = {"take": "trade", "needs_fresher_data": "watch", "needs_better_rr": "watch"}
        final_decision = rev.get(human, human)

    downgrade_reasons = list(mc.get("downgradeReasons") or [])
    if entry_contradicts and "entry timing rejected" not in downgrade_reasons:
        downgrade_reasons.append("entry timing rejected")
    if source_ok is False and "source quality insufficient" not in downgrade_reasons:
        downgrade_reasons.append("source quality insufficient")

    return {
        "setupProvided": bool(engine_d_ctx.get("setup_available")),
        "setupDirection": direction if direction in ("LONG", "SHORT") else None,
        "setupGrade": engine_d_ctx.get("ai_grade"),
        "setupScore": engine_d_ctx.get("ai_score"),
        "setupPassed": bool(engine_d_ctx.get("executable")),
        "chartConfirmsDirection": chart_confirms,
        "chartContradictsDirection": chart_contradicts,
        "chartConfirmsEntryTiming": entry_confirms,
        "chartContradictsEntryTiming": entry_contradicts,
        "sourceQualitySupportsReview": source_ok,
        "aiDowngradedSetup": bool(
            mc.get("aiDowngradedSetup")
            or (engine_d_ctx.get("executable") and final_decision in ("wait", "watch", "reject"))
        ),
        "comparisonVerdict": comparison,
        "downgradeReasons": downgrade_reasons,
        "finalDecision": final_decision,
        "finalReason": mc.get("finalReason") or ai_review.get("supporting_reasons", [""])[0]
        if isinstance(ai_review.get("supporting_reasons"), list) and ai_review.get("supporting_reasons")
        else "",
    }
