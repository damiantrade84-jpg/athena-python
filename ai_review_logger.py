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
_PROMPT_CA_DIR = os.environ.get(
    "AI_PROMPT_STORE_DIR", os.path.join(_LOG_DIR, "prompt_store")
)
_VISION_IMG_DIR = os.environ.get(
    "VISION_IMAGE_STORE_DIR", os.path.join(_LOG_DIR, "vision_images")
)

log = logging.getLogger("athena.ai_review")

# Canonical review_type values
REVIEW_TYPE_ENGINE_B_AI = "engine_b_ai"
REVIEW_TYPE_CHART_VISION = "chart_vision"
REVIEW_TYPE_SIGNAL_DEBATE = "signal_debate"
REVIEW_TYPE_MARCUS_REID = "marcus_reid"
REVIEW_TYPE_NEWS_SENTIMENT = "news_sentiment"
REVIEW_TYPE_LOTTERY_AI = "lottery_ai"
REVIEW_TYPE_ENGINE_C_AI = "engine_c_ai"
REVIEW_TYPE_DECAY_AI = "decay_ai"
REVIEW_TYPE_RESEARCH_AI = "research_ai"
REVIEW_TYPE_META_ANALYSIS = "meta_analysis"

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


def _serialize_input_packet(input_packet: Any) -> str:
    if isinstance(input_packet, str):
        return input_packet
    try:
        return json.dumps(input_packet, sort_keys=True, ensure_ascii=False, default=str)
    except Exception:
        return str(input_packet)


def store_prompt_content(prompt_text: str) -> tuple[str, str]:
    """Write prompt text to content-addressable storage; return (sha256_hex, relative_key)."""
    raw = prompt_text or ""
    content_hash = hashlib.sha256(raw.encode("utf-8")).hexdigest()
    rel_parts = ("prompt", content_hash[:2], content_hash[2:4], content_hash)
    storage_path = os.path.join(_PROMPT_CA_DIR, *rel_parts)
    try:
        os.makedirs(os.path.dirname(storage_path), exist_ok=True)
        if not os.path.exists(storage_path):
            with open(storage_path, "w", encoding="utf-8") as wf:
                wf.write(raw)
    except Exception as _e:
        log.warning("[AI_AUDIT] prompt CAS write failed: %s", _e)
    rel_key = "/".join(rel_parts)
    return content_hash, rel_key


def retrieve_prompt_content(content_hash: str) -> str | None:
    """Load prompt text by full SHA-256 hex digest."""
    h = (content_hash or "").strip().lower()
    if len(h) < 64:
        return None
    storage_path = os.path.join(_PROMPT_CA_DIR, "prompt", h[:2], h[2:4], h)
    try:
        if os.path.exists(storage_path):
            with open(storage_path, "r", encoding="utf-8") as rf:
                return rf.read()
    except Exception as _e:
        log.warning("[AI_AUDIT] prompt CAS read failed: %s", _e)
    return None


def store_vision_image(image_bytes: bytes, metadata: dict[str, Any]) -> dict[str, Any]:
    """Persist chart bytes under SHA-256 key (Audit CRIT-007)."""
    data = image_bytes or b""
    image_hash = hashlib.sha256(data).hexdigest()
    rel_parts = ("vision", image_hash[:2], image_hash[2:4], f"{image_hash}.png")
    storage_path = os.path.join(_VISION_IMG_DIR, *rel_parts)
    out = {
        "image_hash": image_hash,
        "retrieval_key": "/".join(rel_parts),
        "storage_path": storage_path,
    }
    try:
        os.makedirs(os.path.dirname(storage_path), exist_ok=True)
        if data and not os.path.exists(storage_path):
            with open(storage_path, "wb") as wf:
                wf.write(data)
        meta_path = storage_path + ".meta.json"
        if not os.path.exists(meta_path):
            with open(meta_path, "w", encoding="utf-8") as mf:
                json.dump(metadata or {}, mf, default=str)
    except Exception as _e:
        log.warning("[AI_AUDIT] vision image store failed: %s", _e)
    return out


def log_ai_failure(
    *,
    surface: str,
    error: str,
    safe_default_used: bool = True,
    trace_id: str | None = None,
) -> None:
    """Append a minimal failure record — never raises."""
    record = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "review_type": f"{surface}_FAILURE",
        "error": error,
        "safe_default_used": safe_default_used,
        "parse_fallback_used": True,
        "trace_id": trace_id,
    }
    try:
        _ensure_log_dir()
        with open(_LOG_FILE, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, default=str) + "\n")
    except Exception as _e:
        log.warning("[AI_AUDIT] Failed to write failure record: %s", _e)


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
    trace_id: str | None = None,
    image_hash: str | None = None,
    image_retrieval_key: str | None = None,
    experiment_id: str | None = None,
    variant_id: str | None = None,
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

    _prompt_text = _serialize_input_packet(input_packet)
    _full_hash, _retrieval_key = store_prompt_content(_prompt_text)

    record: dict[str, Any] = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "symbol": symbol,
        "asset_type": asset_type,
        "review_type": review_type,
        "model": model,
        "provider": provider,
        "prompt_version": prompt_version,
        "input_packet_hash": _hash_input(input_packet),
        "input_content_sha256": _full_hash,
        "input_retrieval_key": _retrieval_key,
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
    if trace_id:
        record["trace_id"] = trace_id
    if image_hash:
        record["image_hash"] = image_hash
    if image_retrieval_key:
        record["image_retrieval_key"] = image_retrieval_key
    if experiment_id:
        record["experiment_id"] = experiment_id
    if variant_id:
        record["variant_id"] = variant_id

    try:
        _ensure_log_dir()
        with open(_LOG_FILE, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, default=str) + "\n")
    except Exception as _e:
        log.warning("[AI_AUDIT] Failed to write audit record: %s", _e)


def extract_engine_d_audit_state(signal: Any) -> dict[str, Any] | None:
    """Return a compact Engine D/scalp state for audit logs when present."""
    if not isinstance(signal, dict):
        return None

    for key in ("engine_d_state", "engine_d", "scalp_engine", "scalp_data"):
        value = signal.get(key)
        if isinstance(value, dict) and value:
            return value

    engine = str(signal.get("engine") or signal.get("engine_type") or "").lower()
    if engine not in ("scalp", "engine_d", "engine-d", "d"):
        return None

    keys = (
        "engine",
        "engine_type",
        "setup_type",
        "setupType",
        "quality_score",
        "qualityScore",
        "score",
        "grade",
        "trade",
        "passed",
        "market_state",
        "location",
        "aggression",
    )
    out = {key: signal.get(key) for key in keys if signal.get(key) is not None}
    return out or {"engine": signal.get("engine") or signal.get("engine_type")}


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


def map_engine_a_grade_to_ai_state(grade: str) -> str:
    """Convert Marcus Reid Engine A grade to canonical ai_review_state."""
    return map_engine_b_grade_to_ai_state(grade)


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
