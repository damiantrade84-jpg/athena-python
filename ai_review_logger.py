"""ai_review_logger.py — JSONL audit trail for every AI review event in Sentinel Pro.

One record per AI call.  Never raises — logging failures must not interrupt
the trading pipeline.

Schema fields:
  timestamp, symbol, asset_type, review_type, model, provider, prompt_version,
  input_packet_hash, has_chart_image, candle_freshness_status,
  engine_a_state, engine_b_state, engine_c_state, engine_d_state, risk_state,
  ai_review_state, ai_confidence, contradictions_count, missing_information_count,
  parse_success, schema_valid, execution_allowed_before_ai, execution_allowed_after_ai,
  ai_changed_execution_permission, final_action

Rules:
  - ai_changed_execution_permission must never be True in the UPGRADE direction
    for non-vision paths (Vision CONFIRM → trade=True is the only sanctioned upgrade).
  - All callers must pass execution_allowed_before_ai and execution_allowed_after_ai
    so that any unexpected upgrade is immediately visible in the log.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
from datetime import datetime, timezone
from typing import Any

_LOG_DIR = os.path.join(os.path.dirname(__file__), "logs", "ai_review")
_LOG_FILE = os.path.join(_LOG_DIR, "ai_review_audit.jsonl")

log = logging.getLogger("athena.ai_review")

# Canonical review_type values
REVIEW_TYPE_ENGINE_B_AI = "engine_b_ai"
REVIEW_TYPE_CHART_VISION = "chart_vision"
REVIEW_TYPE_SIGNAL_DEBATE = "signal_debate"
REVIEW_TYPE_MARCUS_REID = "marcus_reid"
REVIEW_TYPE_NEWS_SENTIMENT = "news_sentiment"
REVIEW_TYPE_LOTTERY_AI = "lottery_ai"

# Canonical ai_review_state values
AI_STATE_CONFIRM = "CONFIRM"
AI_STATE_CAUTION = "CAUTION"
AI_STATE_REJECT = "REJECT"
AI_STATE_REVIEW_INCOMPLETE = "REVIEW_INCOMPLETE"

# Vision is the only sanctioned non-downgrade path
_VISION_UPGRADE_ALLOWED_TYPES = {REVIEW_TYPE_CHART_VISION}


def _ensure_log_dir() -> None:
    try:
        os.makedirs(_LOG_DIR, exist_ok=True)
    except Exception as _e:
        log.warning("[AI_AUDIT] Cannot create log dir: %s", _e)


def _hash_input(obj: Any) -> str:
    try:
        serialized = json.dumps(obj, sort_keys=True, default=str)
        return hashlib.sha256(serialized.encode()).hexdigest()[:16]
    except Exception:
        return "hash_error"


def log_ai_review(
    *,
    symbol: str,
    asset_type: str,
    review_type: str,
    model: str,
    provider: str,
    prompt_version: str,
    input_packet: Any,
    has_chart_image: bool,
    candle_freshness_status: str | None,
    engine_a_state: Any | None,
    engine_b_state: Any | None,
    engine_c_state: Any | None,
    engine_d_state: Any | None,
    risk_state: Any | None,
    ai_review_state: str,
    ai_confidence: float | None,
    contradictions_count: int,
    missing_information_count: int,
    parse_success: bool,
    schema_valid: bool,
    execution_allowed_before_ai: bool,
    execution_allowed_after_ai: bool,
    final_action: str,
) -> None:
    """Write one AI review audit record to JSONL.  Never raises."""
    ai_changed_execution_permission = (
        execution_allowed_before_ai != execution_allowed_after_ai
    )

    # Sanity-check: non-vision upgrades should never happen.
    if (
        ai_changed_execution_permission
        and execution_allowed_after_ai
        and review_type not in _VISION_UPGRADE_ALLOWED_TYPES
    ):
        log.warning(
            "[AI_AUDIT] UNEXPECTED UPGRADE: review_type=%s symbol=%s "
            "ai_state=%s before=%s after=%s",
            review_type,
            symbol,
            ai_review_state,
            execution_allowed_before_ai,
            execution_allowed_after_ai,
        )

    record: dict[str, Any] = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "symbol": symbol,
        "asset_type": asset_type,
        "review_type": review_type,
        "model": model,
        "provider": provider,
        "prompt_version": prompt_version,
        "input_packet_hash": _hash_input(input_packet),
        "has_chart_image": has_chart_image,
        "candle_freshness_status": candle_freshness_status,
        "engine_a_state": engine_a_state,
        "engine_b_state": engine_b_state,
        "engine_c_state": engine_c_state,
        "engine_d_state": engine_d_state,
        "risk_state": risk_state,
        "ai_review_state": ai_review_state,
        "ai_confidence": ai_confidence,
        "contradictions_count": contradictions_count,
        "missing_information_count": missing_information_count,
        "parse_success": parse_success,
        "schema_valid": schema_valid,
        "execution_allowed_before_ai": execution_allowed_before_ai,
        "execution_allowed_after_ai": execution_allowed_after_ai,
        "ai_changed_execution_permission": ai_changed_execution_permission,
        "final_action": final_action,
    }

    try:
        _ensure_log_dir()
        with open(_LOG_FILE, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, default=str) + "\n")
    except Exception as _e:
        log.warning("[AI_AUDIT] Failed to write audit record: %s", _e)


def map_debate_grade_to_ai_state(grade: str) -> str:
    """Convert signal_debate grade to canonical ai_review_state."""
    _g = (grade or "").upper()
    if _g == "STRONG_GO":
        return AI_STATE_CONFIRM
    if _g == "WEAK_GO":
        return AI_STATE_CAUTION
    if _g in ("PASS", "STRONG_AVOID"):
        return AI_STATE_REJECT
    if _g in ("SKIP", "ERROR"):
        return AI_STATE_REVIEW_INCOMPLETE
    return AI_STATE_REVIEW_INCOMPLETE


def map_engine_b_grade_to_ai_state(grade: str) -> str:
    """Convert Engine B AI grade to canonical ai_review_state."""
    _g = (grade or "").upper()
    if _g in ("A+", "A"):
        return AI_STATE_CONFIRM
    if _g == "B":
        return AI_STATE_CAUTION
    if _g in ("C", "D", "F"):
        return AI_STATE_REJECT
    return AI_STATE_REVIEW_INCOMPLETE


def map_vision_rating_to_ai_state(rating: str) -> str:
    """Convert Vision structured rating to canonical ai_review_state."""
    _r = (rating or "").upper()
    if _r in ("CONFIRMS",):
        return AI_STATE_CONFIRM
    if _r in ("REVIEW",):
        return AI_STATE_CAUTION
    if _r in ("POTENTIAL REVERSAL", "POTENTIAL_REVERSAL", "CONTRADICTS", "AVOID"):
        return AI_STATE_REJECT
    return AI_STATE_REVIEW_INCOMPLETE


REQUIRED_AUDIT_FIELDS = frozenset({
    "timestamp",
    "symbol",
    "asset_type",
    "review_type",
    "model",
    "provider",
    "prompt_version",
    "input_packet_hash",
    "has_chart_image",
    "candle_freshness_status",
    "engine_a_state",
    "engine_b_state",
    "engine_c_state",
    "engine_d_state",
    "risk_state",
    "ai_review_state",
    "ai_confidence",
    "contradictions_count",
    "missing_information_count",
    "parse_success",
    "schema_valid",
    "execution_allowed_before_ai",
    "execution_allowed_after_ai",
    "ai_changed_execution_permission",
    "final_action",
})


def validate_audit_record(record: dict) -> tuple[bool, list[str]]:
    """Return (valid, missing_fields).  Does not raise."""
    missing = [f for f in REQUIRED_AUDIT_FIELDS if f not in record]
    return len(missing) == 0, missing
