"""Tests for athena_ai.strategy_classifier.

Hard guarantees being tested:
  * rejected_models is always populated when a candidate is excluded, with a reason (FIX 1)
  * failed_breakout in trade direction never classifies as trend continuation
  * wick rejection without a named level is rejected with reason
  * high Engine A score does NOT suppress candidates, only emits a warning
  * balance/chop near POC prefers BALANCE_CHOP_NO_TRADE but does not delete directional models
"""

from __future__ import annotations

from typing import Any

import pytest

from athena_ai.strategy_classifier import classify_strategy


def _facts(**overrides) -> dict[str, Any]:
    base = {
        "wick_event": {"type": "neutral", "confidence": "low"},
        "breakout_state": {"state": "no_breakout", "level": None, "confidence": "low"},
        "acceptance_state": {"state": "unknown", "confidence": "low"},
        "liquidity_event": {"event": "no_sweep", "confidence": "low"},
        "structure_event": {"state": "unknown", "confidence": "low"},
        "profile_location": {"location": "inside_va", "poc": 100.0, "confidence": "medium"},
        "volume_behavior": {"state": "normal", "confidence": "medium"},
        "setup_candidates": {"hints": [], "confidence": "low"},
    }
    base.update(overrides)
    return base


def test_failed_breakout_in_long_direction_rejects_trend_continuation() -> None:
    facts = _facts(
        breakout_state={
            "state": "failed_breakout",
            "side": "upper",
            "level": 100.0,
            "close_back_inside": True,
            "confidence": "high",
        },
        setup_candidates={"hints": ["failed_breakout_hint", "breakout_continuation_hint"], "confidence": "medium"},
    )
    out = classify_strategy(
        price_action_facts=facts,
        engine_a_summary={"confluence_score": 5, "max_score_override": 10, "regime": "balance"},
        direction="LONG",
        asset_group="crypto",
    )
    candidate_ids = [c["id"] for c in out["candidate_models"]]
    rejected_ids = {r["id"] for r in out["rejected_models"]}
    assert "TREND_CONTINUATION_PULLBACK" not in candidate_ids
    assert "TREND_CONTINUATION_PULLBACK" in rejected_ids
    # FIX 1 — reason text must reference the failed breakout
    reason = next(r["rejection_reason"] for r in out["rejected_models"] if r["id"] == "TREND_CONTINUATION_PULLBACK")
    assert "failed" in reason.lower()


def test_wick_rejection_without_level_is_rejected_with_reason() -> None:
    facts = _facts(
        wick_event={"type": "upper_rejection", "upper_wick_atr": 1.5, "confidence": "medium"},
        breakout_state={"state": "no_breakout", "level": None, "confidence": "low"},
        profile_location={"location": "unknown_va_bounds", "poc": None, "confidence": "low"},
        setup_candidates={"hints": ["wick_rejection_hint"], "confidence": "medium"},
    )
    out = classify_strategy(
        price_action_facts=facts,
        engine_a_summary={"regime": "balance"},
        asset_group="crypto",
        direction="SHORT",
    )
    rejected_ids = {r["id"] for r in out["rejected_models"]}
    assert "WICK_REJECTION_AT_KEY_LEVEL" in rejected_ids
    reason = next(r["rejection_reason"] for r in out["rejected_models"] if r["id"] == "WICK_REJECTION_AT_KEY_LEVEL")
    assert "no named level" in reason.lower() or "mid-range" in reason.lower()


def test_high_engine_a_score_does_not_suppress_candidates_emits_warning() -> None:
    # No hints, but a high engine_a score percentage
    facts = _facts(setup_candidates={"hints": [], "confidence": "low"})
    out = classify_strategy(
        price_action_facts=facts,
        engine_a_summary={"confluence_score": 9, "max_score_override": 10, "regime": "balance"},
        asset_group="crypto",
        direction="LONG",
    )
    # candidates list should still contain the playbook universe (zero affinity is allowed)
    assert out["candidate_models"], "high engine A score must not produce empty candidate list"
    assert any("high" in w.lower() and "engine a" in w.lower() for w in out["classification_warnings"]), (
        "should warn when engine_a score is high but no hints are present"
    )


def test_balance_chop_near_poc_is_preferred_but_directional_not_deleted() -> None:
    facts = _facts(
        profile_location={"location": "near_poc", "poc": 100.0, "distance_to_poc_atr": 0.1, "confidence": "high"},
        volume_behavior={"state": "contraction", "ratio": 0.4, "confidence": "medium"},
        setup_candidates={"hints": ["balance_chop_hint"], "confidence": "medium"},
    )
    out = classify_strategy(
        price_action_facts=facts,
        engine_a_summary={"regime": "balance"},
        asset_group="crypto",
        direction="LONG",
    )
    candidate_ids = [c["id"] for c in out["candidate_models"]]
    # BALANCE_CHOP_NO_TRADE should be the highest-affinity candidate
    assert candidate_ids[0] == "BALANCE_CHOP_NO_TRADE"
    # Directional models still represented in the candidate list at lower affinity
    # (not silently deleted) — they may be in rejected with a soft reason, but
    # at least one of trend/sweep/wick families is still considered somewhere
    surfaced_ids = set(candidate_ids) | {r["id"] for r in out["rejected_models"]}
    assert "TREND_CONTINUATION_PULLBACK" in surfaced_ids
    # warning about prefering balance was emitted
    assert any("balance" in w.lower() or "poc" in w.lower() for w in out["classification_warnings"])


def test_return_to_poc_rejected_in_trending_regime() -> None:
    facts = _facts(
        profile_location={"location": "above_vah", "poc": 100.0, "vah": 101.0, "val": 99.0, "confidence": "high"},
        setup_candidates={"hints": ["return_to_poc_hint"], "confidence": "medium"},
    )
    out = classify_strategy(
        price_action_facts=facts,
        engine_a_summary={"regime": "trend_up"},
        asset_group="crypto",
        direction="SHORT",
    )
    rejected_ids = {r["id"] for r in out["rejected_models"]}
    assert "RETURN_TO_POC_MEAN_REVERSION" in rejected_ids


def test_rejected_models_always_carry_reason_strings() -> None:
    facts = _facts()
    out = classify_strategy(
        price_action_facts=facts,
        engine_a_summary={"regime": "balance"},
        asset_group="crypto",
        direction="LONG",
    )
    for r in out["rejected_models"]:
        assert isinstance(r.get("rejection_reason"), str) and r["rejection_reason"].strip()


def test_no_setup_yields_a_candidate_list_but_classification_is_not_a_decision() -> None:
    """classifier returns opinion; downstream still routes through the AI."""
    facts = _facts()
    out = classify_strategy(
        price_action_facts=facts,
        engine_a_summary={"regime": "balance"},
        asset_group="crypto",
    )
    assert isinstance(out["candidate_models"], list)
    assert isinstance(out["rejected_models"], list)
    assert isinstance(out["classification_warnings"], list)


def test_engines_considered_reflects_provided_summaries() -> None:
    out = classify_strategy(
        price_action_facts=_facts(),
        engine_a_summary={},
        engine_b_summary={"available": True, "verdict": "x"},
        engine_d_summary={"available": False},
        asset_group="crypto",
    )
    assert "A" in out["engines_considered"]
    assert "B" in out["engines_considered"]
    assert "D" not in out["engines_considered"]


def test_b_only_review_does_not_claim_engine_a_context() -> None:
    out = classify_strategy(
        price_action_facts=_facts(),
        engine_a_summary=None,
        engine_b_summary={"available": True, "verdict": "CLEAR"},
        asset_group="crypto",
    )

    assert out["engines_considered"] == ["B"]


def test_classifier_does_not_mutate_inputs() -> None:
    facts = _facts()
    snapshot = {k: dict(v) if isinstance(v, dict) else v for k, v in facts.items()}
    classify_strategy(
        price_action_facts=facts,
        engine_a_summary={"regime": "balance"},
        asset_group="crypto",
    )
    for k in snapshot:
        assert facts[k] == snapshot[k], f"classifier mutated facts[{k}]"


def test_classifier_carries_selected_style_and_timeframe_into_candidates() -> None:
    out = classify_strategy(
        price_action_facts=_facts(),
        engine_a_summary={"regime": "balance"},
        asset_group="forex",
        style="intraday",
        timeframe="M30",
    )

    assert out["evaluation_style"] == "intraday"
    assert out["evaluation_timeframe"] == "M30"
    assert all(row["evaluation_style"] == "intraday" for row in out["candidate_models"])
    assert all(row["evaluation_timeframe"] == "M30" for row in out["candidate_models"])
