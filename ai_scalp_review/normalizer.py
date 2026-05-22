"""Normalize Opus scalp chart review responses."""

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
    "scalpVerdictComparison",
    "scalp_verdict_comparison",
    "contextCompleteness",
    "context_completeness",
)


def repair_json_once(raw_text: str) -> str:
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
        try:
            parsed = json.loads(match.group(0))
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
        ("scalpVerdictComparison", "scalp_verdict_comparison"),
        ("contextCompleteness", "context_completeness"),
    )
    for camel, snake in aliases:
        if camel not in out and snake in parsed and isinstance(parsed[snake], dict):
            out[camel] = parsed[snake]
    return out


_WAIT_DECISIONS = frozenset({"WAIT_FOR_PULLBACK", "WAIT_FOR_ACCEPTANCE"})


def normalize_scalp_chart_review_response(
    raw_text: str,
    *,
    backend_levels: dict[str, Any] | None = None,
) -> dict[str, Any]:
    parsed, err = _extract_json(raw_text)
    if parsed is None:
        fail_skill = trade_skill_parse_failure("engine_d_scalp")
        return {
            "verdict": fail_skill.get("verdict", "CAUTION"),
            "confidence": 0,
            "setup_type": "",
            "visual_confirmation": "",
            "visual_contradiction": "",
            "entry_quality": "",
            "source_quality_assessment": "",
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
            "reviewType": "engine_d_scalp",
            "tradeSkillWarnings": fail_skill.get("tradeSkillWarnings", []),
        }

    structured = _pick_structured(parsed)
    summary = structured.get("aiReviewSummary") or structured.get("ai_review_summary") or {}
    if not isinstance(summary, dict):
        summary = {}

    trade_skill, _warnings = normalize_trade_skill_output(
        parsed,
        review_type="engine_d_scalp",
        backend_levels=backend_levels,
    )

    human_action = trade_skill.get("human_action") or parsed.get("human_action")
    if not human_action and summary.get("humanAction"):
        ha = str(summary["humanAction"]).lower()
        rev_map = {"trade": "take", "watch": "needs_fresher_data"}
        human_action = rev_map.get(ha, ha)

    try:
        confidence = int(trade_skill.get("confidence", parsed.get("confidence", summary.get("confidence", 0))))
    except (TypeError, ValueError):
        confidence = 0
    confidence = max(0, min(100, confidence))

    verdict = str(trade_skill.get("verdict") or parsed.get("verdict") or "CAUTION").upper()
    if verdict not in _VALID_VERDICTS:
        verdict = "CAUTION"

    action = str(human_action or "wait").strip().lower()
    if action not in _VALID_ACTIONS:
        action = "wait"

    decision = str(trade_skill.get("decision") or "WATCH_ONLY").upper()
    suggested_plan = None
    if decision in _WAIT_DECISIONS:
        suggested_plan = sanitize_suggested_trade_plan(
            parsed,
            source="ai_scalp_chart_review",
            symbol=str(parsed.get("symbol") or ""),
        )
    structured_out = structured
    if suggested_plan:
        structured_out = dict(structured)
        structured_out["suggestedTradePlan"] = suggested_plan

    result: dict[str, Any] = {
        "verdict": verdict,
        "confidence": confidence,
        "setup_type": str(parsed.get("setup_type") or summary.get("setupType") or ""),
        "visual_confirmation": str(parsed.get("visual_confirmation") or parsed.get("visualConfirmation") or ""),
        "visual_contradiction": str(parsed.get("visual_contradiction") or parsed.get("visualContradiction") or ""),
        "entry_quality": str(parsed.get("entry_quality") or parsed.get("entryQuality") or ""),
        "source_quality_assessment": str(
            parsed.get("source_quality_assessment")
            or parsed.get("sourceQualityAssessment")
            or ""
        ),
        "supporting_reasons": _coerce_list(parsed.get("supporting_reasons") or parsed.get("supportingReasons")),
        "risks": _coerce_list(parsed.get("risks")),
        "missing_context": _coerce_list(parsed.get("missing_context") or parsed.get("missingContext")),
        "human_action": action,
        "raw_model_response": raw_text or "",
        "parse_success": True,
        "structured": structured_out,
        "suggestedTradePlan": suggested_plan,
        "suggested_trade_plan": suggested_plan,
    }
    result.update(trade_skill)
    return result


def _coerce_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(v) for v in value]
    if isinstance(value, str) and value.strip():
        return [value.strip()]
    return []
