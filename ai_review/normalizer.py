"""Normalize Claude chart review responses."""

from __future__ import annotations

import json
import re
from typing import Any

from ai_playbooks.trade_skill_normalizer import normalize_trade_skill_output, trade_skill_parse_failure
from ai_review.suggested_trade_plan import sanitize_suggested_trade_plan

_VALID_VERDICTS = {"VALID", "CAUTION", "INVALID", "NO_TRADE"}
_VALID_ACTIONS = {
    "take",
    "wait",
    "reject",
    "needs_fresher_data",
    "needs_better_rr",
}

_STRUCTURED_KEYS = (
    "aiReviewSummary",
    "ai_review_summary",
    "engineAVerdictComparison",
    "engine_a_verdict_comparison",
    "engineBVerdictComparison",
    "engine_b_verdict_comparison",
    "contextCompleteness",
    "context_completeness",
    "missingContextDetailed",
    "missing_context_detailed",
    "metadata",
)


def _coerce_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(v) for v in value]
    if isinstance(value, str) and value.strip():
        return [value.strip()]
    return []


def repair_json_once(raw_text: str) -> str:
    """Single-pass cleanup before JSON parse."""
    text = (raw_text or "").strip()
    if not text:
        return text
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.IGNORECASE)
        text = re.sub(r"\s*```\s*$", "", text)
    return text.strip()


def _extract_json(raw_text: str) -> tuple[dict[str, Any] | None, str | None]:
    text = repair_json_once(raw_text)
    if not text:
        return None, "empty response"
    try:
        parsed = json.loads(text)
        if isinstance(parsed, dict):
            return parsed, None
    except json.JSONDecodeError:
        pass
    match = re.search(r"\{[\s\S]*\}", text)
    if match:
        candidate = match.group(0)
        try:
            parsed = json.loads(candidate)
            if isinstance(parsed, dict):
                return parsed, None
        except json.JSONDecodeError:
            try:
                parsed = json.loads(repair_json_once(candidate))
                if isinstance(parsed, dict):
                    return parsed, None
            except json.JSONDecodeError as exc:
                return None, str(exc)
    return None, "no JSON object found"


def _pick_structured(parsed: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key in _STRUCTURED_KEYS:
        if key in parsed and isinstance(parsed[key], dict):
            out[key] = parsed[key]
    aliases = (
        ("aiReviewSummary", "ai_review_summary"),
        ("engineAVerdictComparison", "engine_a_verdict_comparison"),
        ("engineBVerdictComparison", "engine_b_verdict_comparison"),
        ("contextCompleteness", "context_completeness"),
        ("missingContextDetailed", "missing_context_detailed"),
    )
    for camel, snake in aliases:
        if camel not in out and snake in parsed and isinstance(parsed[snake], dict):
            out[camel] = parsed[snake]
    return out


def _legacy_from_structured(parsed: dict[str, Any], structured: dict[str, Any]) -> dict[str, Any]:
    """Map nested model fields onto legacy flat ai_review keys when absent."""
    summary = structured.get("aiReviewSummary") or structured.get("ai_review_summary") or {}
    if not isinstance(summary, dict):
        summary = {}
    comparison = structured.get("engineAVerdictComparison") or structured.get("engine_a_verdict_comparison") or {}
    if not isinstance(comparison, dict):
        comparison = {}

    human_action = parsed.get("human_action")
    if not human_action and summary.get("humanAction"):
        ha = str(summary["humanAction"]).lower()
        rev_map = {"trade": "take", "watch": "needs_fresher_data"}
        human_action = rev_map.get(ha, ha)
    if not human_action and comparison.get("finalDecision"):
        fd = str(comparison["finalDecision"]).lower()
        rev_map = {"trade": "take", "watch": "wait", "reject": "reject"}
        human_action = rev_map.get(fd, "wait")

    setup_type = parsed.get("setup_type") or summary.get("setupType") or ""
    visual_confirmation = parsed.get("visual_confirmation") or parsed.get("visualConfirmation") or ""
    visual_contradiction = parsed.get("visual_contradiction") or parsed.get("visualContradiction") or ""
    entry_quality = parsed.get("entry_quality") or parsed.get("entryQuality") or ""
    atr_rr = parsed.get("atr_rr_assessment") or parsed.get("atrRrAssessment") or ""
    engine_align = parsed.get("engine_a_alignment") or parsed.get("engineAAlignment") or ""
    if not engine_align and comparison.get("comparisonVerdict"):
        engine_align = str(comparison.get("comparisonVerdict")).replace("_", " ")

    confidence = parsed.get("confidence")
    if confidence is None and summary.get("confidence") is not None:
        confidence = summary.get("confidence")

    verdict = parsed.get("verdict")
    if not verdict and summary.get("humanAction"):
        ha = str(summary["humanAction"]).lower()
        if ha == "trade":
            verdict = "VALID"
        elif ha == "reject":
            verdict = "INVALID"
        else:
            verdict = "CAUTION"

    return {
        "verdict": verdict,
        "confidence": confidence,
        "setup_type": setup_type,
        "visual_confirmation": str(visual_confirmation or ""),
        "visual_contradiction": str(visual_contradiction or ""),
        "engine_a_alignment": str(engine_align or ""),
        "atr_rr_assessment": str(atr_rr or ""),
        "freshness_assessment": str(parsed.get("freshness_assessment") or parsed.get("freshnessAssessment") or ""),
        "entry_quality": str(entry_quality or ""),
        "supporting_reasons": _coerce_list(parsed.get("supporting_reasons") or parsed.get("supportingReasons")),
        "risks": _coerce_list(parsed.get("risks")),
        "missing_context": _coerce_list(parsed.get("missing_context") or parsed.get("missingContext")),
        "human_action": human_action,
    }


_WAIT_DECISIONS = frozenset({"WAIT_FOR_PULLBACK", "WAIT_FOR_ACCEPTANCE"})


def _merge_trade_skill_fields(
    parsed: dict[str, Any],
    *,
    review_type: str,
    backend_levels: dict[str, Any] | None,
) -> dict[str, Any]:
    trade_skill, warnings = normalize_trade_skill_output(
        parsed,
        review_type=review_type,  # type: ignore[arg-type]
        backend_levels=backend_levels,
    )
    return trade_skill


def normalize_chart_review_response(
    raw_text: str,
    *,
    backend_levels: dict[str, Any] | None = None,
    review_type: str = "engine_a_chart",
) -> dict[str, Any]:
    parsed, err = _extract_json(raw_text)
    if parsed is None:
        fail_skill = trade_skill_parse_failure(review_type)  # type: ignore[arg-type]
        return {
            "verdict": fail_skill.get("verdict", "CAUTION"),
            "confidence": 0,
            "setup_type": "",
            "visual_confirmation": "",
            "visual_contradiction": "",
            "engine_a_alignment": "",
            "engine_b_alignment": "",
            "atr_rr_assessment": "",
            "freshness_assessment": "",
            "entry_quality": "",
            "supporting_reasons": [],
            "risks": [f"AI response JSON parse failed: {err}"],
            "missing_context": [],
            "human_action": fail_skill.get("human_action", "wait"),
            "raw_model_response": raw_text or "",
            "parse_success": False,
            "structured": {},
            "entryAllowedNow": False,
            "decision": fail_skill.get("decision", "WATCH_ONLY"),
            "tradeSkillVersion": fail_skill.get("tradeSkillVersion"),
            "reviewType": review_type,
            "tradeSkillWarnings": fail_skill.get("tradeSkillWarnings", []),
        }

    structured = _pick_structured(parsed)
    legacy_fields = _legacy_from_structured(parsed, structured)
    trade_skill = _merge_trade_skill_fields(
        parsed,
        review_type=review_type,  # type: ignore[arg-type]
        backend_levels=backend_levels,
    )

    verdict = str(trade_skill.get("verdict") or legacy_fields.get("verdict") or "CAUTION").upper()
    if verdict not in _VALID_VERDICTS:
        verdict = "CAUTION"

    try:
        confidence = int(trade_skill.get("confidence", legacy_fields.get("confidence", 0)))
    except (TypeError, ValueError):
        confidence = 0
    confidence = max(0, min(100, confidence))

    action = str(trade_skill.get("human_action") or legacy_fields.get("human_action") or "wait").strip().lower()
    if action not in _VALID_ACTIONS:
        action = "wait"

    decision = str(trade_skill.get("decision") or "WATCH_ONLY").upper()
    suggested_plan = None
    if decision in _WAIT_DECISIONS:
        suggested_plan = sanitize_suggested_trade_plan(
            parsed,
            source="ai_chart_review",
            symbol=str(parsed.get("symbol") or ""),
        )
    structured_out = structured
    if suggested_plan:
        structured_out = dict(structured)
        structured_out["suggestedTradePlan"] = suggested_plan

    result: dict[str, Any] = {
        "verdict": verdict,
        "confidence": confidence,
        "setup_type": str(legacy_fields.get("setup_type") or ""),
        "visual_confirmation": str(legacy_fields.get("visual_confirmation") or ""),
        "visual_contradiction": str(legacy_fields.get("visual_contradiction") or ""),
        "engine_a_alignment": str(legacy_fields.get("engine_a_alignment") or ""),
        "atr_rr_assessment": str(legacy_fields.get("atr_rr_assessment") or ""),
        "freshness_assessment": str(legacy_fields.get("freshness_assessment") or ""),
        "entry_quality": str(legacy_fields.get("entry_quality") or ""),
        "supporting_reasons": _coerce_list(legacy_fields.get("supporting_reasons")),
        "risks": _coerce_list(legacy_fields.get("risks")),
        "missing_context": _coerce_list(legacy_fields.get("missing_context")),
        "human_action": action,
        "raw_model_response": raw_text or "",
        "parse_success": True,
        "structured": structured_out,
        "suggestedTradePlan": suggested_plan,
        "suggested_trade_plan": suggested_plan,
    }
    result.update(trade_skill)
    return result
