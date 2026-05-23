"""Server-side Engine D scalp execute gate — fresh AI ENTRY_NOW validation."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from ai_review.persistence import find_scalp_review_by_id


def _normalize_symbol_keys(symbol: str) -> set[str]:
    val = str(symbol or "").strip().upper()
    if not val:
        return set()
    compact = val.replace("/", "").replace("-", "").replace(" ", "")
    return {val, compact}


def _symbols_match(a: str, b: str) -> bool:
    if not a or not b:
        return False
    return bool(_normalize_symbol_keys(a) & _normalize_symbol_keys(b))


def scalp_ai_review_allows_entry(ai_review: dict[str, Any] | None) -> bool:
    """True when persisted AI review authorizes immediate entry."""
    if not isinstance(ai_review, dict):
        return False
    structured = ai_review.get("structured") or {}
    if not isinstance(structured, dict):
        structured = {}
    decision = str(
        structured.get("decision") or ai_review.get("decision") or ""
    ).upper()
    entry_allowed = structured.get("entryAllowedNow")
    if entry_allowed is None:
        entry_allowed = ai_review.get("entryAllowedNow")
    if entry_allowed is False:
        return False
    if decision != "ENTRY_NOW":
        return False
    verdict = str(ai_review.get("verdict") or "").upper()
    if verdict in ("NO_TRADE", "INVALID"):
        return False
    return True


def engine_d_signal_hard_block(signal: dict[str, Any]) -> str | None:
    """Fail-closed blocks that AI review cannot override."""
    grade = str(signal.get("ai_grade") or signal.get("grade") or "").upper()
    if grade == "D":
        return "ENGINE_D_GRADE_D_NOT_EXECUTABLE"
    gate = str(signal.get("gate_result", "PASS") or "PASS").upper()
    if gate == "BLOCKED":
        return str(signal.get("candidate_status") or "ENGINE_D_BLOCKED")
    direction = str(signal.get("direction") or "").upper()
    if direction not in ("LONG", "SHORT"):
        return "ENGINE_D_DIRECTION_MISSING"
    return None


def _legacy_mechanical_block(signal: dict[str, Any]) -> str | None:
    gate_result = str(signal.get("gate_result", "PASS") or "PASS").upper()
    if signal.get("executable") is False:
        return str(signal.get("candidate_status") or "ENGINE_D_NOT_EXECUTABLE")
    if gate_result != "PASS":
        return str(signal.get("candidate_status") or gate_result or "ENGINE_D_GATE_FAILED")
    fail_reasons = signal.get("fail_reasons")
    if isinstance(fail_reasons, list) and fail_reasons:
        return str(signal.get("candidate_status") or ",".join(map(str, fail_reasons)))
    return None


def _review_age_seconds(created_at: str | None) -> float | None:
    if not created_at:
        return None
    try:
        ts = datetime.fromisoformat(str(created_at).replace("Z", "+00:00"))
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        return (datetime.now(timezone.utc) - ts).total_seconds()
    except (TypeError, ValueError):
        return None


def _ai_execute_cfg(cfg: dict[str, Any]) -> dict[str, Any]:
    return cfg.get("AI_SCALP_CHART_REVIEW") or {}


def resolve_engine_d_execute_gate(
    signal: dict[str, Any],
    *,
    review_id: str | None,
    audit_db: str | None,
    cfg: dict[str, Any],
) -> tuple[str | None, dict[str, Any] | None]:
    """Return (block_reason, persisted_review) for scalp execute."""
    hard = engine_d_signal_hard_block(signal)
    if hard:
        return hard, None

    ai_cfg = _ai_execute_cfg(cfg)
    requires_ai = bool(ai_cfg.get("EXECUTE_REQUIRES_AI_REVIEW", True))
    if not requires_ai:
        return _legacy_mechanical_block(signal), None

    if not review_id:
        return "AI_REVIEW_REQUIRED", None

    max_age = int(ai_cfg.get("EXECUTE_AI_REVIEW_MAX_AGE_SECONDS") or 300)
    review = find_scalp_review_by_id(
        str(review_id).strip(),
        audit_db=audit_db,
        review_type="engine_d",
    )
    if review is None:
        return "AI_REVIEW_NOT_FOUND", None

    age = _review_age_seconds(review.get("created_at"))
    if age is None or age > max_age:
        return "AI_REVIEW_STALE", None

    symbol = str(signal.get("pair") or signal.get("symbol") or signal.get("display") or "")
    direction = str(signal.get("direction") or "").upper()
    ctx = review.get("engine_d_context") or {}
    ctx_symbol = str(ctx.get("symbol") or "")
    ctx_direction = str(ctx.get("direction") or "").upper()
    if ctx_direction and ctx_direction != direction:
        return "AI_REVIEW_DIRECTION_MISMATCH", None
    if ctx_symbol and symbol and not _symbols_match(ctx_symbol, symbol):
        return "AI_REVIEW_SYMBOL_MISMATCH", None

    ai_review = review.get("ai_review") or {}
    if not scalp_ai_review_allows_entry(ai_review):
        return "AI_REVIEW_NOT_ENTRY_NOW", None

    grade = str(signal.get("ai_grade") or signal.get("grade") or "").upper()
    if grade in ("A", "B"):
        return None, review

    return _legacy_mechanical_block(signal), None


def engine_d_execution_block_reason(
    sig: dict[str, Any],
    *,
    review_id: str | None = None,
    audit_db: str | None = None,
    cfg: dict[str, Any] | None = None,
) -> str | None:
    """Modular execute path block reason with optional AI review gate."""
    cfg = cfg or {}
    ai_cfg = _ai_execute_cfg(cfg)
    if not ai_cfg.get("EXECUTE_REQUIRES_AI_REVIEW", True):
        hard = engine_d_signal_hard_block(sig)
        if hard:
            return hard
        return _legacy_mechanical_block(sig)
    reason, _ = resolve_engine_d_execute_gate(
        sig,
        review_id=review_id,
        audit_db=audit_db,
        cfg=cfg,
    )
    return reason
