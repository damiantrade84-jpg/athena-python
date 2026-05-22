"""Engine D setup vs AI concordance for scalp chart review."""

from __future__ import annotations

from typing import Any


def compute_scalp_ai_concordance(
    engine_d_ctx: dict[str, Any],
    ai_review: dict[str, Any],
) -> dict[str, Any]:
    direction = str(engine_d_ctx.get("direction") or "NONE").upper()
    ai_grade = str(engine_d_ctx.get("ai_grade") or "").upper()
    executable = bool(engine_d_ctx.get("executable"))
    ai_verdict = str(ai_review.get("verdict") or "CAUTION").upper()
    ai_action = str(ai_review.get("human_action") or "wait")

    concordance = "unknown"
    divergence_type = "none"
    divergence_note = ""

    source = engine_d_ctx.get("sourceContract") or {}
    if source.get("strictOrderflowSourcePass") is False:
        concordance = "partial"
        divergence_type = "source_quality_insufficient"
        divergence_note = "Orderflow source not verified real"

    missing = ai_review.get("missing_context") or []
    if missing:
        concordance = "partial" if concordance != "disagree" else concordance
        divergence_type = "missing_context"

    if executable and ai_verdict == "VALID":
        if ai_review.get("visual_contradiction"):
            concordance = "partial"
            divergence_type = "visual_contradiction"
            divergence_note = "Setup executable but AI reported visual contradiction"
        elif concordance == "unknown":
            concordance = "agree"
    elif executable and ai_verdict == "CAUTION":
        concordance = "partial"
    elif executable and ai_verdict in ("INVALID", "NO_TRADE"):
        concordance = "disagree"
    elif not executable and ai_verdict == "VALID":
        concordance = "disagree"
        divergence_note = "Setup not executable but AI verdict is VALID"
    elif not executable and ai_verdict in ("INVALID", "NO_TRADE"):
        concordance = "agree"
    elif concordance == "unknown":
        concordance = "partial"

    return {
        "engine": "D",
        "setup_direction": direction,
        "setup_grade": ai_grade or None,
        "setup_executable": executable,
        "ai_verdict": ai_verdict,
        "ai_human_action": ai_action,
        "concordance": concordance,
        "divergence_type": divergence_type,
        "divergence_note": divergence_note,
        "should_flag_for_review": concordance in ("disagree", "unknown"),
    }
