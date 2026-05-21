"""Normalize Claude chart review responses."""

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


def _coerce_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(v) for v in value]
    if isinstance(value, str) and value.strip():
        return [value.strip()]
    return []


def _extract_json(raw_text: str) -> tuple[dict[str, Any] | None, str | None]:
    text = (raw_text or "").strip()
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


def normalize_chart_review_response(raw_text: str) -> dict[str, Any]:
    parsed, err = _extract_json(raw_text)
    if parsed is None:
        return {
            "verdict": "CAUTION",
            "confidence": 0,
            "setup_type": "",
            "visual_confirmation": "",
            "visual_contradiction": "",
            "engine_a_alignment": "",
            "atr_rr_assessment": "",
            "freshness_assessment": "",
            "entry_quality": "",
            "supporting_reasons": [],
            "risks": [f"AI response JSON parse failed: {err}"],
            "missing_context": [],
            "human_action": "wait",
            "raw_model_response": raw_text or "",
            "parse_success": False,
        }

    verdict = str(parsed.get("verdict") or "CAUTION").upper()
    if verdict not in _VALID_VERDICTS:
        verdict = "CAUTION"

    try:
        confidence = int(parsed.get("confidence", 0))
    except (TypeError, ValueError):
        confidence = 0
    confidence = max(0, min(100, confidence))

    action = str(parsed.get("human_action") or "wait").strip().lower()
    if action not in _VALID_ACTIONS:
        action = "wait"

    return {
        "verdict": verdict,
        "confidence": confidence,
        "setup_type": str(parsed.get("setup_type") or ""),
        "visual_confirmation": str(parsed.get("visual_confirmation") or ""),
        "visual_contradiction": str(parsed.get("visual_contradiction") or ""),
        "engine_a_alignment": str(parsed.get("engine_a_alignment") or ""),
        "atr_rr_assessment": str(parsed.get("atr_rr_assessment") or ""),
        "freshness_assessment": str(parsed.get("freshness_assessment") or ""),
        "entry_quality": str(parsed.get("entry_quality") or ""),
        "supporting_reasons": _coerce_list(parsed.get("supporting_reasons")),
        "risks": _coerce_list(parsed.get("risks")),
        "missing_context": _coerce_list(parsed.get("missing_context")),
        "human_action": action,
        "raw_model_response": raw_text or "",
        "parse_success": True,
    }
