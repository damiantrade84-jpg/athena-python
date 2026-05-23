"""Tests for Engine D AI-gated scalp execute contract."""

from __future__ import annotations

import tempfile
from datetime import datetime, timezone

from ai_review.persistence import ensure_schema, record_review
from ai_scalp_review.execute_gate import (
    resolve_engine_d_execute_gate,
    scalp_ai_review_allows_entry,
)


def _cfg(*, requires_ai: bool = True) -> dict:
    return {
        "AI_SCALP_CHART_REVIEW": {
            "EXECUTE_REQUIRES_AI_REVIEW": requires_ai,
            "EXECUTE_AI_REVIEW_MAX_AGE_SECONDS": 300,
        }
    }


def _seed_review(*, direction: str = "LONG", decision: str = "ENTRY_NOW") -> tuple[str, str]:
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tmp:
        audit_db = tmp.name
    ensure_schema(audit_db)
    engine_d_ctx = {
        "symbol": "EUR/USD",
        "direction": direction,
        "execution_tf": "M1",
        "scan_timestamp": datetime.now(timezone.utc).isoformat(),
        "chart_captured_at": datetime.now(timezone.utc).isoformat(),
    }
    ai_review = {
        "parse_success": True,
        "decision": decision,
        "entryAllowedNow": decision == "ENTRY_NOW",
        "structured": {"decision": decision, "entryAllowedNow": decision == "ENTRY_NOW"},
        "verdict": "VALID" if decision == "ENTRY_NOW" else "NO_TRADE",
    }
    row = record_review(
        symbol="EUR/USD",
        timeframe="M5",
        asset_group=None,
        provider="anthropic",
        model="test",
        latency_ms=10,
        screenshot_hash="abc123",
        screenshot_bytes=100,
        screenshot_meta={"native_chart": True, "chart_timeframe": "M5"},
        engine_d_context=engine_d_ctx,
        ai_review=ai_review,
        concordance={},
        mismatch_warnings=[],
        audit_db=audit_db,
        review_type="engine_d",
    )
    return row["review_id"], audit_db


def test_scalp_ai_review_allows_entry_now():
    assert scalp_ai_review_allows_entry(
        {"decision": "ENTRY_NOW", "entryAllowedNow": True, "verdict": "VALID"}
    )
    assert not scalp_ai_review_allows_entry(
        {"decision": "WATCH_ONLY", "entryAllowedNow": False, "verdict": "VALID"}
    )


def test_watchlist_ab_candidate_allowed_with_fresh_ai_review():
    review_id, audit_db = _seed_review()
    signal = {
        "pair": "EUR/USD",
        "direction": "LONG",
        "ai_grade": "B",
        "gate_result": "WATCHLIST",
        "executable": False,
        "fail_reasons": [],
        "soft_warnings": ["rr_below_min"],
    }
    reason, review = resolve_engine_d_execute_gate(
        signal,
        review_id=review_id,
        audit_db=audit_db,
        cfg=_cfg(),
    )
    assert reason is None
    assert review is not None


def test_execute_requires_review_id():
    _, audit_db = _seed_review()
    signal = {
        "pair": "EUR/USD",
        "direction": "LONG",
        "ai_grade": "B",
        "gate_result": "WATCHLIST",
        "executable": False,
    }
    reason, _ = resolve_engine_d_execute_gate(
        signal,
        review_id=None,
        audit_db=audit_db,
        cfg=_cfg(),
    )
    assert reason == "AI_REVIEW_REQUIRED"


def test_grade_d_always_blocked_even_with_ai_review():
    review_id, audit_db = _seed_review()
    signal = {
        "pair": "EUR/USD",
        "direction": "LONG",
        "ai_grade": "D",
        "gate_result": "BLOCKED",
        "executable": False,
    }
    reason, _ = resolve_engine_d_execute_gate(
        signal,
        review_id=review_id,
        audit_db=audit_db,
        cfg=_cfg(),
    )
    assert reason == "ENGINE_D_GRADE_D_NOT_EXECUTABLE"


def test_grade_c_still_requires_legacy_pass_without_ai_bypass():
    review_id, audit_db = _seed_review()
    signal = {
        "pair": "EUR/USD",
        "direction": "LONG",
        "ai_grade": "C",
        "gate_result": "WATCHLIST",
        "executable": False,
        "candidate_status": "grade_C_watchlist",
    }
    reason, _ = resolve_engine_d_execute_gate(
        signal,
        review_id=review_id,
        audit_db=audit_db,
        cfg=_cfg(),
    )
    assert reason == "grade_C_watchlist"


def test_ai_review_direction_mismatch():
    review_id, audit_db = _seed_review(direction="SHORT")
    signal = {
        "pair": "EUR/USD",
        "direction": "LONG",
        "ai_grade": "B",
        "gate_result": "WATCHLIST",
        "executable": False,
    }
    reason, _ = resolve_engine_d_execute_gate(
        signal,
        review_id=review_id,
        audit_db=audit_db,
        cfg=_cfg(),
    )
    assert reason == "AI_REVIEW_DIRECTION_MISMATCH"
