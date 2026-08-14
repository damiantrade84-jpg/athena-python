"""Engine A vs AI concordance for chart review."""

from __future__ import annotations

from typing import Any

from ai_review.context_diagnostics import build_context_diagnostics
from ai_review.visual_text import (
    has_visual_contradiction_text,
    is_directional_visual_contradiction,
)

_DOWNGRADE = {
    "agree": "partial",
    "partial": "disagree",
    "disagree": "unknown",
    "unknown": "unknown",
}


def _downgrade(concordance: str) -> str:
    return _DOWNGRADE.get(concordance, "unknown")


def _missing_diagnostics(engine_a_ctx: dict[str, Any]) -> bool:
    atr = engine_a_ctx.get("atr") or {}
    geometry = engine_a_ctx.get("geometry") or {}
    if engine_a_ctx.get("confluence_score") is None:
        return True
    if engine_a_ctx.get("threshold") is None:
        return True
    if atr.get("atr_value") is None:
        return True
    if geometry.get("rr") is None:
        return True
    return False


def _required_missing_context(
    engine_ctx: dict[str, Any],
    ai_review: dict[str, Any],
) -> list[Any]:
    diagnostics = build_context_diagnostics(engine_ctx, ai_review)
    return diagnostics.get("missingContextDetailed", {}).get("required") or []


def _missing_context_note(required: list[Any]) -> str:
    labels = [
        str(item.get("label") or item.get("key")) if isinstance(item, dict) else str(item)
        for item in required
    ]
    return "Required context missing: " + ", ".join(labels)


def _engine_a_comparison(ai_review: dict[str, Any]) -> dict[str, Any]:
    structured = ai_review.get("structured") or {}
    if not isinstance(structured, dict):
        return {}
    comparison = structured.get("engineAVerdictComparison") or structured.get(
        "engine_a_verdict_comparison"
    )
    return comparison if isinstance(comparison, dict) else {}


def _divergence_from_ai(
    engine_a_ctx: dict[str, Any],
    ai_review: dict[str, Any],
) -> tuple[str, str]:
    """Classify the AI-side divergence and name the evidence behind it.

    The note is provenance for the UI chip: the same divergence_type can come
    from the model's structured comparison, free-text risks, or the wait
    catch-all, and previously they were indistinguishable.
    """
    required = _required_missing_context(engine_a_ctx, ai_review)
    if required:
        return "missing_context", _missing_context_note(required)

    comparison = _engine_a_comparison(ai_review)
    if comparison.get("chartContradictsEngineADirection") is True:
        return "visual_contradiction", "AI reported chart contradicts Engine A direction"
    if (
        comparison.get("chartContradictsEntryTiming") is True
        or comparison.get("comparisonVerdict")
        in {
            "engine_a_direction_confirmed_entry_rejected",
            "engine_b_direction_confirmed_entry_rejected",
        }
    ):
        return "entry_displacement", "AI reported chart contradicts entry timing"

    risks = [str(r).lower() for r in (ai_review.get("risks") or [])]
    if is_directional_visual_contradiction(ai_review.get("visual_contradiction")):
        return "visual_contradiction", "AI reported visual contradiction"
    for risk in risks:
        if "atr" in risk or "freshness" in risk:
            return "freshness_issue", "AI risk note references ATR/freshness"
        if "rr" in risk or "risk reward" in risk:
            return "atr_rr_issue", "AI risk note references risk/reward"
        if "displacement" in risk or "entry" in risk or "chasing" in risk or "late" in risk:
            return "entry_displacement", "AI risk note references entry timing/displacement"

    entry_text = str(ai_review.get("entry_quality") or "").lower()
    if any(
        marker in entry_text
        for marker in ("late", "chasing", "extended", "poor entry", "bad entry")
    ):
        return "entry_displacement", "AI flagged entry quality as late/chasing/extended"

    if has_visual_contradiction_text(ai_review.get("visual_contradiction")):
        return "other", "AI reported non-directional visual caveat"

    human_action = str(ai_review.get("human_action") or "wait").lower()
    if human_action in ("wait", "needs_fresher_data", "needs_better_rr"):
        return "entry_displacement", "AI suggested wait; no specific divergence evidence"
    return "other", ""


def compute_engine_a_ai_concordance(
    engine_a_ctx: dict[str, Any],
    ai_review: dict[str, Any],
    *,
    cfg: dict[str, Any] | None = None,
) -> dict[str, Any]:
    cfg = cfg or {}
    direction = str(engine_a_ctx.get("direction") or "NONE").upper()
    score = engine_a_ctx.get("confluence_score")
    threshold = engine_a_ctx.get("threshold")
    passed = bool(engine_a_ctx.get("passed"))
    ai_verdict = str(ai_review.get("verdict") or "CAUTION").upper()
    ai_action = str(ai_review.get("human_action") or "wait")

    concordance = "unknown"
    divergence_type = "none"
    divergence_note = ""

    if _missing_diagnostics(engine_a_ctx):
        concordance = "unknown"
        divergence_type = "missing_context"
        divergence_note = "Required Engine A diagnostics missing"
    elif passed and ai_verdict == "VALID":
        if is_directional_visual_contradiction(ai_review.get("visual_contradiction")):
            concordance = "partial"
            divergence_type = "visual_contradiction"
            divergence_note = "Engine A passed but AI reported visual contradiction"
        elif has_visual_contradiction_text(ai_review.get("visual_contradiction")):
            concordance = "partial"
            divergence_type = "other"
            divergence_note = "Engine A passed; AI noted non-directional visual caveat"
        else:
            concordance = "agree"
    elif passed and ai_verdict == "CAUTION":
        concordance = "partial"
        divergence_type, divergence_note = _divergence_from_ai(engine_a_ctx, ai_review)
    elif passed and ai_verdict in ("INVALID", "NO_TRADE"):
        concordance = "disagree"
        divergence_type, divergence_note = _divergence_from_ai(engine_a_ctx, ai_review)
    elif not passed and ai_verdict == "VALID":
        concordance = "disagree"
        divergence_type = "other"
        divergence_note = "Engine A did not pass but AI verdict is VALID"
    elif not passed and ai_verdict in ("INVALID", "NO_TRADE"):
        concordance = "agree"
    else:
        concordance = "partial"
        divergence_type, divergence_note = _divergence_from_ai(engine_a_ctx, ai_review)

    atr = engine_a_ctx.get("atr") or {}
    if str(atr.get("atr_freshness_status") or "").lower() == "stale":
        concordance = _downgrade(concordance)
        divergence_type = "freshness_issue"
        # Deterministic evidence owns the note so it always matches the type.
        divergence_note = "ATR freshness stale"

    geometry = engine_a_ctx.get("geometry") or {}
    displacement = geometry.get("price_displacement_from_candidate_entry")
    atr_value = atr.get("atr_value")
    max_mult = float(cfg.get("MAX_DISPLACEMENT_ATR_MULTIPLE") or 1.0)
    if (
        displacement is not None
        and atr_value is not None
        and float(atr_value) > 0
        and float(displacement) > max_mult * float(atr_value)
    ):
        concordance = _downgrade(concordance)
        divergence_type = "entry_displacement"
        # Deterministic evidence owns the note so it always matches the type.
        divergence_note = "Price displacement exceeds ATR multiple"

    return {
        "engine": "A",
        "engine_a_direction": direction,
        "engine_a_score": float(score) if score is not None else None,
        "engine_a_threshold": float(threshold) if threshold is not None else None,
        "engine_a_passed": passed,
        "ai_verdict": ai_verdict,
        "ai_human_action": ai_action,
        "concordance": concordance,
        "divergence_type": divergence_type,
        "divergence_note": divergence_note,
        "should_flag_for_review": concordance in ("disagree", "unknown"),
    }


def _missing_engine_b_diagnostics(engine_b_ctx: dict[str, Any]) -> bool:
    geometry = engine_b_ctx.get("geometry") or {}
    if engine_b_ctx.get("confluence_score") is None:
        return True
    if engine_b_ctx.get("threshold") is None:
        return True
    if geometry.get("rr") is None and geometry.get("stop_loss") is None:
        return True
    return False


def _engine_b_comparison(ai_review: dict[str, Any]) -> dict[str, Any]:
    structured = ai_review.get("structured") or {}
    if not isinstance(structured, dict):
        return {}
    comparison = structured.get("engineBVerdictComparison") or structured.get(
        "engine_b_verdict_comparison"
    )
    return comparison if isinstance(comparison, dict) else {}


def _divergence_from_ai_b(
    engine_b_ctx: dict[str, Any],
    ai_review: dict[str, Any],
) -> tuple[str, str]:
    """Engine B mirror of `_divergence_from_ai` (type plus evidence note)."""
    required = _required_missing_context(engine_b_ctx, ai_review)
    if required:
        return "missing_context", _missing_context_note(required)

    comparison = _engine_b_comparison(ai_review)
    if comparison.get("chartContradictsEngineBDirection") is True:
        return "visual_contradiction", "AI reported chart contradicts Engine B direction"
    if (
        comparison.get("chartContradictsEntryTiming") is True
        or comparison.get("comparisonVerdict") == "engine_b_direction_confirmed_entry_rejected"
    ):
        return "entry_displacement", "AI reported chart contradicts entry timing"

    risks = [str(r).lower() for r in (ai_review.get("risks") or [])]
    if is_directional_visual_contradiction(ai_review.get("visual_contradiction")):
        return "visual_contradiction", "AI reported visual contradiction"
    for risk in risks:
        if "atr" in risk or "freshness" in risk:
            return "freshness_issue", "AI risk note references ATR/freshness"
        if "rr" in risk or "risk reward" in risk:
            return "atr_rr_issue", "AI risk note references risk/reward"
        if "displacement" in risk or "entry" in risk or "chasing" in risk or "late" in risk:
            return "entry_displacement", "AI risk note references entry timing/displacement"

    entry_text = str(ai_review.get("entry_quality") or "").lower()
    if any(
        marker in entry_text
        for marker in ("late", "chasing", "extended", "poor entry", "bad entry")
    ):
        return "entry_displacement", "AI flagged entry quality as late/chasing/extended"

    if has_visual_contradiction_text(ai_review.get("visual_contradiction")):
        return "other", "AI reported non-directional visual caveat"

    human_action = str(ai_review.get("human_action") or "wait").lower()
    if human_action in ("wait", "needs_fresher_data", "needs_better_rr"):
        return "entry_displacement", "AI suggested wait; no specific divergence evidence"
    return "other", ""


def compute_engine_b_ai_concordance(
    engine_b_ctx: dict[str, Any],
    ai_review: dict[str, Any],
    *,
    cfg: dict[str, Any] | None = None,
) -> dict[str, Any]:
    cfg = cfg or {}
    direction = str(engine_b_ctx.get("direction") or "NONE").upper()
    score = engine_b_ctx.get("confluence_score")
    threshold = engine_b_ctx.get("threshold")
    passed = bool(engine_b_ctx.get("passed"))
    ai_verdict = str(ai_review.get("verdict") or "CAUTION").upper()
    ai_action = str(ai_review.get("human_action") or "wait")

    concordance = "unknown"
    divergence_type = "none"
    divergence_note = ""

    if _missing_engine_b_diagnostics(engine_b_ctx):
        concordance = "unknown"
        divergence_type = "missing_context"
        divergence_note = "Required Engine B diagnostics missing"
    elif passed and ai_verdict == "VALID":
        if is_directional_visual_contradiction(ai_review.get("visual_contradiction")):
            concordance = "partial"
            divergence_type = "visual_contradiction"
            divergence_note = "Engine B passed but AI reported visual contradiction"
        elif has_visual_contradiction_text(ai_review.get("visual_contradiction")):
            concordance = "partial"
            divergence_type = "other"
            divergence_note = "Engine B passed; AI noted non-directional visual caveat"
        else:
            concordance = "agree"
    elif passed and ai_verdict == "CAUTION":
        concordance = "partial"
        divergence_type, divergence_note = _divergence_from_ai_b(engine_b_ctx, ai_review)
    elif passed and ai_verdict in ("INVALID", "NO_TRADE"):
        concordance = "disagree"
        divergence_type, divergence_note = _divergence_from_ai_b(engine_b_ctx, ai_review)
    elif not passed and ai_verdict == "VALID":
        concordance = "disagree"
        divergence_type = "other"
        divergence_note = "Engine B did not pass but AI verdict is VALID"
    elif not passed and ai_verdict in ("INVALID", "NO_TRADE"):
        concordance = "agree"
    else:
        concordance = "partial"
        divergence_type, divergence_note = _divergence_from_ai_b(engine_b_ctx, ai_review)

    atr = engine_b_ctx.get("atr") or {}
    if str(atr.get("atr_freshness_status") or "").lower() == "stale":
        concordance = _downgrade(concordance)
        divergence_type = "freshness_issue"
        # Deterministic evidence owns the note so it always matches the type.
        divergence_note = "ATR freshness stale"

    geometry = engine_b_ctx.get("geometry") or {}
    displacement = geometry.get("price_displacement_from_candidate_entry")
    atr_value = atr.get("atr_value")
    max_mult = float(cfg.get("MAX_DISPLACEMENT_ATR_MULTIPLE") or 1.0)
    if (
        displacement is not None
        and atr_value is not None
        and float(atr_value) > 0
        and float(displacement) > max_mult * float(atr_value)
    ):
        concordance = _downgrade(concordance)
        divergence_type = "entry_displacement"
        # Deterministic evidence owns the note so it always matches the type.
        divergence_note = "Price displacement exceeds ATR multiple"

    struct = engine_b_ctx.get("structure_context") or {}
    structural_verdict = struct.get("structural_verdict") if isinstance(struct, dict) else None

    return {
        "engine": "B",
        "engine_b_direction": direction,
        "engine_b_score": float(score) if score is not None else None,
        "engine_b_threshold": float(threshold) if threshold is not None else None,
        "engine_b_passed": passed,
        "engine_b_structural_verdict": structural_verdict,
        "ai_verdict": ai_verdict,
        "ai_human_action": ai_action,
        "concordance": concordance,
        "divergence_type": divergence_type,
        "divergence_note": divergence_note,
        "should_flag_for_review": concordance in ("disagree", "unknown"),
    }
