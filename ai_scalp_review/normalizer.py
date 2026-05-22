"""Normalize Opus scalp chart review responses."""

from __future__ import annotations

import json
import re
from typing import Any

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


def normalize_scalp_chart_review_response(raw_text: str) -> dict[str, Any]:
    parsed, err = _extract_json(raw_text)
    if parsed is None:
        return {
            "verdict": "CAUTION",
            "confidence": 0,
            "setup_type": "",
            "visual_confirmation": "",
            "visual_contradiction": "",
            "entry_quality": "",
            "source_quality_assessment": "",
            "supporting_reasons": [],
            "risks": [f"AI response JSON parse failed: {err}"],
            "missing_context": [],
            "human_action": "wait",
            "raw_model_response": raw_text or "",
            "parse_success": False,
            "structured": {},
        }

    structured = _pick_structured(parsed)
    summary = structured.get("aiReviewSummary") or structured.get("ai_review_summary") or {}
    if not isinstance(summary, dict):
        summary = {}

    human_action = parsed.get("human_action")
    if not human_action and summary.get("humanAction"):
        ha = str(summary["humanAction"]).lower()
        rev_map = {"trade": "take", "watch": "needs_fresher_data"}
        human_action = rev_map.get(ha, ha)

    try:
        confidence = int(parsed.get("confidence", summary.get("confidence", 0)))
    except (TypeError, ValueError):
        confidence = 0
    confidence = max(0, min(100, confidence))

    verdict = str(parsed.get("verdict") or "CAUTION").upper()
    if verdict not in _VALID_VERDICTS:
        verdict = "CAUTION"

    action = str(human_action or "wait").strip().lower()
    if action not in _VALID_ACTIONS:
        action = "wait"

    return {
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
        "structured": structured,
    }


def _coerce_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(v) for v in value]
    if isinstance(value, str) and value.strip():
        return [value.strip()]
    return []
