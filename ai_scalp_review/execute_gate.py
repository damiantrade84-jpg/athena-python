"""Server-side Engine D scalp execute gate — fresh AI ENTRY_NOW validation."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from ai_review.persistence import find_scalp_review_by_id
from ai_scalp_review.scalp_verdict import build_scalp_verdict_comparison
from symbol_matching import symbol_match_keys, symbols_match

log = logging.getLogger("sentinel.engine_d_execute_gate")

# Soft-warning marker stamped by scalp_engine when the TVQ execution gate
# demotes a passing candidate (tvq_blocks_execution). Deterministic — the AI
# review path must never re-open it.
_TVQ_BLOCK_WARNING = "tvq_below_threshold_proxy_volume"


def _normalize_symbol_keys(symbol: str) -> set[str]:
    return symbol_match_keys(symbol)


def _symbols_match(a: str, b: str) -> bool:
    return symbols_match(a, b)


def scalp_ai_review_allows_entry(ai_review: dict[str, Any] | None) -> bool:
    """True when persisted AI review authorizes immediate entry."""
    if not isinstance(ai_review, dict):
        return False
    if ai_review.get("parse_success") is not True:
        return False
    structured = ai_review.get("structured") or {}
    if not isinstance(structured, dict):
        structured = {}
    decision = str(
        ai_review.get("decision") or structured.get("decision") or ""
    ).upper()
    if "entryAllowedNow" in ai_review:
        entry_allowed = ai_review.get("entryAllowedNow")
    else:
        entry_allowed = structured.get("entryAllowedNow")
    if decision != "ENTRY_NOW":
        return False
    if entry_allowed is not True:
        return False
    verdict = str(ai_review.get("verdict") or "").upper()
    if verdict in ("NO_TRADE", "INVALID"):
        return False
    return True


def _scalp_verdict_comparison_block(
    review: dict[str, Any],
    *,
    engine_d_ctx: dict[str, Any],
    ai_review: dict[str, Any],
) -> str | None:
    """Mirror UI scalpAiReviewBlocksExecute concordance checks."""
    structured = ai_review.get("structured") or {}
    if not isinstance(structured, dict):
        structured = {}
    model_comparison = structured.get("scalpVerdictComparison") or structured.get(
        "scalp_verdict_comparison"
    )
    if not isinstance(model_comparison, dict):
        model_comparison = None
    comparison = (
        review.get("scalpVerdictComparison")
        or review.get("scalp_verdict_comparison")
        or model_comparison
    )
    if not isinstance(comparison, dict):
        comparison = build_scalp_verdict_comparison(
            engine_d_ctx,
            ai_review,
            model_comparison=model_comparison,
        )
    final_decision = str(comparison.get("finalDecision") or "").lower()
    if final_decision in ("reject", "watch"):
        return "AI_REVIEW_CONCORDANCE_REJECT"
    if comparison.get("chartContradictsEntryTiming") is True:
        return "AI_REVIEW_ENTRY_TIMING_CONTRADICTED"
    summary = (
        review.get("aiReviewSummary")
        or review.get("ai_review_summary")
        or structured.get("aiReviewSummary")
        or structured.get("ai_review_summary")
    )
    action = ""
    if isinstance(summary, dict):
        action = str(summary.get("humanAction") or "").lower()
    if not action:
        action = str(ai_review.get("human_action") or "").lower()
    if action in ("wait", "reject", "watch", "needs_fresher_data", "needs_better_rr"):
        return "AI_REVIEW_HUMAN_ACTION_BLOCK"
    return None


def _to_float(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        out = float(value)
    elif isinstance(value, str) and value.strip():
        try:
            out = float(value.strip())
        except ValueError:
            return None
    else:
        return None
    if out != out or abs(out) == float("inf"):
        return None
    return out


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _first_float(sources: list[dict[str, Any]], keys: tuple[str, ...]) -> float | None:
    for source in sources:
        for key in keys:
            val = _to_float(source.get(key))
            if val is not None:
                return val
    return None


def _signal_level(signal: dict[str, Any], name: str) -> float | None:
    setup = _dict(signal.get("scalpSetup"))
    if name == "entry":
        return _first_float([signal, setup], ("entry", "price"))
    if name == "sl":
        return _first_float([signal, setup], ("sl", "stopLoss"))
    if name == "tp1":
        return _first_float([signal, setup], ("tp1", "tp", "takeProfit1"))
    if name == "tp2":
        return _first_float([signal, setup], ("tp2", "takeProfit2"))
    return None


def _review_level(ctx: dict[str, Any], name: str) -> float | None:
    setup = _dict(ctx.get("scalpSetup"))
    signal = _dict(ctx.get("signal"))
    if name == "entry":
        return _first_float([ctx, setup, signal], ("entry", "price"))
    if name == "sl":
        return _first_float([ctx, setup, signal], ("sl", "stopLoss"))
    if name == "tp1":
        return _first_float([ctx, setup, signal], ("tp1", "tp", "takeProfit1"))
    if name == "tp2":
        return _first_float([ctx, setup, signal], ("tp2", "takeProfit2"))
    return None


def _levels_close(a: float, b: float) -> bool:
    return abs(a - b) <= max(1e-9, abs(a) * 1e-6)


def _ai_review_level_block(signal: dict[str, Any], ctx: dict[str, Any]) -> str | None:
    for name in ("entry", "sl", "tp1"):
        fresh = _signal_level(signal, name)
        reviewed = _review_level(ctx, name)
        if fresh is None or reviewed is None:
            return "AI_REVIEW_LEVEL_CONTEXT_MISSING"
        if not _levels_close(fresh, reviewed):
            return "AI_REVIEW_LEVEL_MISMATCH"
    for name in ("tp2",):
        fresh = _signal_level(signal, name)
        reviewed = _review_level(ctx, name)
        if fresh is not None and reviewed is not None and not _levels_close(fresh, reviewed):
            return "AI_REVIEW_LEVEL_MISMATCH"
    return None


def engine_d_signal_hard_block(signal: dict[str, Any]) -> str | None:
    """Fail-closed blocks that AI review cannot override."""
    grade = str(signal.get("ai_grade") or signal.get("grade") or "").upper()
    if grade == "D":
        return "ENGINE_D_GRADE_D_NOT_EXECUTABLE"
    gate = str(signal.get("gate_result", "PASS") or "PASS").upper()
    if gate == "BLOCKED":
        return str(signal.get("candidate_status") or "ENGINE_D_BLOCKED")
    soft_warnings = signal.get("soft_warnings")
    if (
        isinstance(soft_warnings, (list, tuple))
        and _TVQ_BLOCK_WARNING in soft_warnings
    ) or str(signal.get("candidate_status") or "") == _TVQ_BLOCK_WARNING:
        return "ENGINE_D_TVQ_BLOCKED_NOT_EXECUTABLE"
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


def _execution_levels_block(signal: dict[str, Any]) -> str | None:
    entry = _signal_level(signal, "entry")
    sl = _signal_level(signal, "sl")
    tp1 = _signal_level(signal, "tp1")
    if entry is None or sl is None or tp1 is None:
        return "ENGINE_D_LEVELS_MISSING"
    if entry <= 0 or sl <= 0 or tp1 <= 0:
        return "ENGINE_D_LEVELS_MISSING"

    direction = str(signal.get("direction") or "").upper()
    if direction == "LONG" and not (sl < entry < tp1):
        return "ENGINE_D_LEVELS_INVALID"
    if direction == "SHORT" and not (tp1 < entry < sl):
        return "ENGINE_D_LEVELS_INVALID"
    return None


def _paper_demo_direct_rr(signal: dict[str, Any]) -> float | None:
    for key in ("rr_gate_basis", "structural_rr"):
        value = _to_float(signal.get(key))
        if value is not None:
            return value
    entry = _signal_level(signal, "entry")
    sl = _signal_level(signal, "sl")
    tp1 = _signal_level(signal, "tp1")
    if entry is None or sl is None or tp1 is None:
        return None
    risk = abs(entry - sl)
    return abs(tp1 - entry) / risk if risk > 0 else None


def _paper_demo_direct_policy_block(signal: dict[str, Any], cfg: dict[str, Any]) -> str | None:
    """Keep raw paper/demo execution deterministic when AI review is skipped."""
    fail_reasons = signal.get("fail_reasons")
    if isinstance(fail_reasons, (list, tuple)) and fail_reasons:
        return str(signal.get("candidate_status") or ",".join(map(str, fail_reasons)))

    if signal.get("strict_fabio_pass") is False:
        return str(signal.get("strict_fabio_reason") or "ENGINE_D_STRICT_FABIO_FAILED")

    fee_guard = _dict(signal.get("fee_guard"))
    fee_reason = str(fee_guard.get("engine_d_reject_reason") or "").strip()
    if fee_reason:
        return fee_reason

    # Without AI, only the deliberately reduced RR floor may reopen an A/B
    # watchlist candidate. Other advisory warnings still require review.
    soft_warnings = signal.get("soft_warnings")
    gate_result = str(signal.get("gate_result") or "PASS").upper()
    already_passed = gate_result == "PASS" and signal.get("executable") is not False
    if not already_passed and isinstance(soft_warnings, (list, tuple)):
        disallowed = [str(reason) for reason in soft_warnings if str(reason) != "rr_below_min"]
        if disallowed:
            return disallowed[0]

    scalp_cfg = cfg.get("SCALP_ENGINE") or {}
    try:
        min_rr = max(0.0, float(scalp_cfg.get("PAPER_DEMO_DIRECT_MIN_RR", 1.0)))
    except (TypeError, ValueError):
        min_rr = 1.0
    rr = _paper_demo_direct_rr(signal)
    if rr is None:
        return "ENGINE_D_RR_UNAVAILABLE"
    if rr + 1e-9 < min_rr:
        return f"ENGINE_D_RR_BELOW_PAPER_DEMO_FLOOR:{rr:.2f}<{min_rr:.2f}"
    return None


def engine_d_paper_demo_direct_block_reason(
    signal: dict[str, Any],
    *,
    cfg: dict[str, Any] | None = None,
) -> str | None:
    """Return the complete deterministic block reason for no-AI paper/demo entry."""
    cfg = cfg or {}
    hard = engine_d_signal_hard_block(signal)
    if hard:
        return hard
    levels_block = _execution_levels_block(signal)
    if levels_block:
        return levels_block
    grade = str(signal.get("ai_grade") or signal.get("grade") or "").upper()
    if grade not in ("A", "B"):
        return "ENGINE_D_GRADE_BELOW_B_NOT_EXECUTABLE"
    return _paper_demo_direct_policy_block(signal, cfg)


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
    if not isinstance(ctx, dict):
        return "AI_REVIEW_CONTEXT_MISSING", None
    ctx_symbol = str(ctx.get("symbol") or "")
    ctx_direction = str(ctx.get("direction") or "").upper()
    if not symbol or not ctx_symbol or not ctx_direction:
        return "AI_REVIEW_CONTEXT_MISSING", None
    if ctx_direction != direction:
        return "AI_REVIEW_DIRECTION_MISMATCH", None
    if symbol and not _symbols_match(ctx_symbol, symbol):
        return "AI_REVIEW_SYMBOL_MISMATCH", None

    ai_review = review.get("ai_review") or {}
    if not scalp_ai_review_allows_entry(ai_review):
        return "AI_REVIEW_NOT_ENTRY_NOW", None

    concordance_block = _scalp_verdict_comparison_block(
        review,
        engine_d_ctx=ctx,
        ai_review=ai_review if isinstance(ai_review, dict) else {},
    )
    if concordance_block:
        return concordance_block, None

    grade = str(signal.get("ai_grade") or signal.get("grade") or "").upper()
    if grade in ("A", "B"):
        level_block = _ai_review_level_block(signal, ctx)
        if level_block:
            return level_block, None
        gate_result = str(signal.get("gate_result", "PASS") or "PASS").upper()
        if gate_result != "PASS" or signal.get("executable") is False:
            log.warning(
                "AI_REOPENED_WATCHLIST pair=%s grade=%s gate_result=%s "
                "candidate_status=%s review_id=%s",
                symbol,
                grade,
                gate_result,
                signal.get("candidate_status"),
                review_id,
            )
        return None, review

    return _legacy_mechanical_block(signal), None


def engine_d_paper_demo_execution_block_reason(
    signal: dict[str, Any],
    *,
    review_id: str | None = None,
    audit_db: str | None = None,
    cfg: dict[str, Any] | None = None,
    require_ai_review: bool | None = None,
) -> str | None:
    """Paper/demo scalp gate: allow A/B advisory demotions, keep hard blocks closed."""
    cfg = cfg or {}
    hard = engine_d_signal_hard_block(signal)
    if hard:
        return hard

    levels_block = _execution_levels_block(signal)
    if levels_block:
        return levels_block

    grade = str(signal.get("ai_grade") or signal.get("grade") or "").upper()
    if grade not in ("A", "B"):
        return "ENGINE_D_GRADE_BELOW_B_NOT_EXECUTABLE"

    ai_cfg = _ai_execute_cfg(cfg)
    configured_requires_ai = bool(
        ai_cfg.get(
            "PAPER_DEMO_EXECUTE_REQUIRES_AI_REVIEW",
            ai_cfg.get("EXECUTE_REQUIRES_AI_REVIEW", True),
        )
    )
    # A caller may opt into AI review, but cannot turn off a configured
    # paper/demo requirement through the request payload.
    requires_ai = configured_requires_ai or require_ai_review is True
    if not requires_ai:
        return _paper_demo_direct_policy_block(signal, cfg)

    reason, _review = resolve_engine_d_execute_gate(
        signal,
        review_id=review_id,
        audit_db=audit_db,
        cfg=cfg,
    )
    return reason


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
