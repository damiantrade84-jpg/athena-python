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


_DEFAULT_CTX = object()
_DEFAULT_AI_REVIEW = object()


def _seed_review(
    *,
    direction: str = "LONG",
    decision: str = "ENTRY_NOW",
    engine_d_ctx: dict | object = _DEFAULT_CTX,
    ai_review: dict | object = _DEFAULT_AI_REVIEW,
) -> tuple[str, str]:
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tmp:
        audit_db = tmp.name
    ensure_schema(audit_db)
    if engine_d_ctx is _DEFAULT_CTX:
        engine_d_ctx = {
            "symbol": "EUR/USD",
            "direction": direction,
            "execution_tf": "M1",
            "scan_timestamp": datetime.now(timezone.utc).isoformat(),
            "chart_captured_at": datetime.now(timezone.utc).isoformat(),
            "latest_candle_ts": datetime.now(timezone.utc).isoformat(),
            "scalpSetup": {"entry": 1.1, "stopLoss": 1.095, "tp1": 1.11},
        }
    if ai_review is _DEFAULT_AI_REVIEW:
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
        {
            "parse_success": True,
            "decision": "ENTRY_NOW",
            "entryAllowedNow": True,
            "verdict": "VALID",
        }
    )
    assert not scalp_ai_review_allows_entry(
        {"decision": "WATCH_ONLY", "entryAllowedNow": False, "verdict": "VALID"}
    )


def test_scalp_ai_review_entry_now_requires_exact_bool_and_parse_success():
    assert not scalp_ai_review_allows_entry(
        {"decision": "ENTRY_NOW", "entryAllowedNow": True, "verdict": "VALID"}
    )
    assert not scalp_ai_review_allows_entry(
        {
            "parse_success": True,
            "decision": "ENTRY_NOW",
            "entryAllowedNow": "false",
            "verdict": "VALID",
        }
    )
    assert not scalp_ai_review_allows_entry(
        {
            "parse_success": True,
            "decision": "ENTRY_NOW",
            "verdict": "VALID",
        }
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
        "price": 1.1,
        "sl": 1.095,
        "tp1": 1.11,
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


def test_engine_d_hard_block_reason_matches_execute_gate():
    from ai_scalp_review.execute_gate import engine_d_signal_hard_block

    signal = {
        "pair": "EUR/USD",
        "direction": "LONG",
        "ai_grade": "D",
        "gate_result": "BLOCKED",
    }
    assert engine_d_signal_hard_block(signal) == "ENGINE_D_GRADE_D_NOT_EXECUTABLE"


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


def test_ai_review_requires_symbol_and_direction_context():
    review_id, audit_db = _seed_review(engine_d_ctx={})
    signal = {
        "pair": "EUR/USD",
        "direction": "LONG",
        "ai_grade": "B",
        "gate_result": "WATCHLIST",
        "executable": False,
        "price": 1.1,
        "sl": 1.095,
        "tp1": 1.11,
    }
    reason, _ = resolve_engine_d_execute_gate(
        signal,
        review_id=review_id,
        audit_db=audit_db,
        cfg=_cfg(),
    )
    assert reason == "AI_REVIEW_CONTEXT_MISSING"


def test_ai_review_rejects_missing_signal_symbol_context():
    review_id, audit_db = _seed_review()
    signal = {
        "direction": "LONG",
        "ai_grade": "B",
        "gate_result": "WATCHLIST",
        "executable": False,
        "price": 1.1,
        "sl": 1.095,
        "tp1": 1.11,
    }
    reason, _ = resolve_engine_d_execute_gate(
        signal,
        review_id=review_id,
        audit_db=audit_db,
        cfg=_cfg(),
    )
    assert reason == "AI_REVIEW_CONTEXT_MISSING"


def test_ai_review_matches_yahoo_forex_symbol_suffix():
    review_id, audit_db = _seed_review()
    signal = {
        "symbol": "EURUSD=X",
        "direction": "LONG",
        "ai_grade": "B",
        "gate_result": "WATCHLIST",
        "executable": False,
        "price": 1.1,
        "sl": 1.095,
        "tp1": 1.11,
    }
    reason, review = resolve_engine_d_execute_gate(
        signal,
        review_id=review_id,
        audit_db=audit_db,
        cfg=_cfg(),
    )
    assert reason is None
    assert review is not None


def test_ai_review_rejects_different_crypto_symbol_with_same_settlement_suffix():
    review_id, audit_db = _seed_review(
        engine_d_ctx={
            "symbol": "BTC/USDT:USDT",
            "direction": "LONG",
            "execution_tf": "M1",
            "scan_timestamp": datetime.now(timezone.utc).isoformat(),
            "chart_captured_at": datetime.now(timezone.utc).isoformat(),
            "latest_candle_ts": datetime.now(timezone.utc).isoformat(),
            "scalpSetup": {"entry": 1.1, "stopLoss": 1.095, "tp1": 1.11},
        }
    )
    signal = {
        "symbol": "ETH/USDT:USDT",
        "direction": "LONG",
        "ai_grade": "B",
        "gate_result": "WATCHLIST",
        "executable": False,
        "price": 1.1,
        "sl": 1.095,
        "tp1": 1.11,
    }
    reason, _ = resolve_engine_d_execute_gate(
        signal,
        review_id=review_id,
        audit_db=audit_db,
        cfg=_cfg(),
    )
    assert reason == "AI_REVIEW_SYMBOL_MISMATCH"


def test_ai_review_rejects_different_crypto_symbol_with_same_provider_prefix():
    review_id, audit_db = _seed_review(
        engine_d_ctx={
            "symbol": "BINANCE:BTCUSDT",
            "direction": "LONG",
            "execution_tf": "M1",
            "scan_timestamp": datetime.now(timezone.utc).isoformat(),
            "chart_captured_at": datetime.now(timezone.utc).isoformat(),
            "latest_candle_ts": datetime.now(timezone.utc).isoformat(),
            "scalpSetup": {"entry": 1.1, "stopLoss": 1.095, "tp1": 1.11},
        }
    )
    signal = {
        "symbol": "BINANCE:ETHUSDT",
        "direction": "LONG",
        "ai_grade": "B",
        "gate_result": "WATCHLIST",
        "executable": False,
        "price": 1.1,
        "sl": 1.095,
        "tp1": 1.11,
    }
    reason, _ = resolve_engine_d_execute_gate(
        signal,
        review_id=review_id,
        audit_db=audit_db,
        cfg=_cfg(),
    )
    assert reason == "AI_REVIEW_SYMBOL_MISMATCH"


def test_execute_blocks_when_concordance_final_decision_reject():
    review_id, audit_db = _seed_review(
        ai_review={
            "parse_success": True,
            "decision": "ENTRY_NOW",
            "entryAllowedNow": True,
            "structured": {
                "decision": "ENTRY_NOW",
                "entryAllowedNow": True,
                "scalpVerdictComparison": {
                    "finalDecision": "reject",
                    "chartContradictsEntryTiming": False,
                },
            },
            "verdict": "VALID",
        }
    )
    signal = {
        "pair": "EUR/USD",
        "direction": "LONG",
        "ai_grade": "B",
        "gate_result": "PASS",
        "executable": True,
        "price": 1.1,
        "sl": 1.095,
        "tp1": 1.11,
    }
    reason, _ = resolve_engine_d_execute_gate(
        signal,
        review_id=review_id,
        audit_db=audit_db,
        cfg=_cfg(),
    )
    assert reason == "AI_REVIEW_CONCORDANCE_REJECT"


def test_execute_blocks_when_chart_contradicts_entry_timing():
    review_id, audit_db = _seed_review(
        ai_review={
            "parse_success": True,
            "decision": "ENTRY_NOW",
            "entryAllowedNow": True,
            "structured": {
                "decision": "ENTRY_NOW",
                "entryAllowedNow": True,
                "scalpVerdictComparison": {
                    "finalDecision": "trade",
                    "chartContradictsEntryTiming": True,
                },
            },
            "verdict": "VALID",
        }
    )
    signal = {
        "pair": "EUR/USD",
        "direction": "LONG",
        "ai_grade": "B",
        "gate_result": "PASS",
        "executable": True,
        "price": 1.1,
        "sl": 1.095,
        "tp1": 1.11,
    }
    reason, _ = resolve_engine_d_execute_gate(
        signal,
        review_id=review_id,
        audit_db=audit_db,
        cfg=_cfg(),
    )
    assert reason == "AI_REVIEW_ENTRY_TIMING_CONTRADICTED"


def test_non_directional_visual_contradiction_does_not_block_execute():
    review_id, audit_db = _seed_review(
        ai_review={
            "parse_success": True,
            "decision": "ENTRY_NOW",
            "entryAllowedNow": True,
            "visual_contradiction": "liquidity thin near entry zone",
            "structured": {"decision": "ENTRY_NOW", "entryAllowedNow": True},
            "verdict": "VALID",
        }
    )
    signal = {
        "pair": "EUR/USD",
        "direction": "LONG",
        "ai_grade": "B",
        "gate_result": "PASS",
        "executable": True,
        "price": 1.1,
        "sl": 1.095,
        "tp1": 1.11,
    }
    reason, _ = resolve_engine_d_execute_gate(
        signal,
        review_id=review_id,
        audit_db=audit_db,
        cfg=_cfg(),
    )
    assert reason is None


def test_ai_review_rejects_review_level_mismatch():
    review_id, audit_db = _seed_review(
        engine_d_ctx={
            "symbol": "EUR/USD",
            "direction": "LONG",
            "execution_tf": "M1",
            "scan_timestamp": datetime.now(timezone.utc).isoformat(),
            "chart_captured_at": datetime.now(timezone.utc).isoformat(),
            "latest_candle_ts": datetime.now(timezone.utc).isoformat(),
            "scalpSetup": {"entry": 1.2, "stopLoss": 1.195, "tp1": 1.21},
        }
    )
    signal = {
        "pair": "EUR/USD",
        "direction": "LONG",
        "ai_grade": "B",
        "gate_result": "WATCHLIST",
        "executable": False,
        "price": 1.1,
        "sl": 1.095,
        "tp1": 1.11,
    }
    reason, _ = resolve_engine_d_execute_gate(
        signal,
        review_id=review_id,
        audit_db=audit_db,
        cfg=_cfg(),
    )
    assert reason == "AI_REVIEW_LEVEL_MISMATCH"
