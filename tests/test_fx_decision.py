"""Tests for athena_fx.decision — factor-first decision engine."""
from __future__ import annotations

import pytest

from athena_fx.decision import decide
from athena_fx.gates import cost_gate
from athena_fx.models import (
    ACTION_BUY,
    ACTION_FLATTEN,
    ACTION_NO_TRADE,
    ACTION_SELL,
    FAMILY_CARRY,
    FAMILY_MOMENTUM,
    FAMILY_VALUE,
    GATE_FAIL,
    SCORE_CAP_CARRY,
    SCORE_CAP_COT_MODIFIER,
    SCORE_CAP_MOMENTUM,
    SCORE_CAP_VALUE,
    CotOverlayResult,
    FamilyScore,
    GateResult,
)


def _family(
    family: str,
    direction: str,
    score: float,
    *,
    status: str = "ok",
) -> FamilyScore:
    return FamilyScore(
        family=family,
        symbol="EURUSD",
        as_of_date="2026-01-15",
        score=score,
        direction=direction,
        status=status,
    )


def _pass_gates() -> list[GateResult]:
    return [
        GateResult(gate_name="volatility", result="pass", reason_code="vol_ok"),
        GateResult(gate_name="cost", result="pass", reason_code="cost_ok"),
        GateResult(gate_name="data_quality", result="pass", reason_code="data_ok"),
    ]


def test_two_of_three_carry_momentum_bullish_buy():
    scores = {
        FAMILY_CARRY: _family(FAMILY_CARRY, "bullish", 0.04),
        FAMILY_MOMENTUM: _family(FAMILY_MOMENTUM, "bullish", 0.8),
        FAMILY_VALUE: _family(FAMILY_VALUE, "neutral", 0.0, status="ok"),
    }
    decision = decide(
        "EURUSD", "2026-01-15", family_scores=scores, cot=None, gate_results=_pass_gates()
    )
    assert decision.action == ACTION_BUY
    assert decision.direction == "LONG"
    assert set(decision.aligned_families) == {FAMILY_CARRY, FAMILY_MOMENTUM}


def test_two_of_three_momentum_value_bullish_buy():
    scores = {
        FAMILY_CARRY: _family(FAMILY_CARRY, "neutral", 0.0),
        FAMILY_MOMENTUM: _family(FAMILY_MOMENTUM, "bullish", 0.9),
        FAMILY_VALUE: _family(FAMILY_VALUE, "bullish", 1.5),
    }
    decision = decide(
        "EURUSD", "2026-01-15", family_scores=scores, cot=None, gate_results=_pass_gates()
    )
    assert decision.action == ACTION_BUY


def test_carry_only_no_trade():
    scores = {
        FAMILY_CARRY: _family(FAMILY_CARRY, "bullish", 0.04),
        FAMILY_MOMENTUM: _family(FAMILY_MOMENTUM, "neutral", 0.0),
        FAMILY_VALUE: _family(FAMILY_VALUE, "neutral", 0.0),
    }
    decision = decide(
        "EURUSD", "2026-01-15", family_scores=scores, cot=None, gate_results=_pass_gates()
    )
    assert decision.action == ACTION_NO_TRADE
    assert decision.reason == "insufficient_family_alignment"


def test_momentum_only_no_trade():
    scores = {
        FAMILY_CARRY: _family(FAMILY_CARRY, "neutral", 0.0),
        FAMILY_MOMENTUM: _family(FAMILY_MOMENTUM, "bullish", 0.9),
        FAMILY_VALUE: _family(FAMILY_VALUE, "neutral", 0.0),
    }
    decision = decide(
        "EURUSD", "2026-01-15", family_scores=scores, cot=None, gate_results=_pass_gates()
    )
    assert decision.action == ACTION_NO_TRADE


def test_tsmom_xs_momentum_keys_raise():
    scores = {
        "tsmom": _family(FAMILY_MOMENTUM, "bullish", 0.9),
        FAMILY_CARRY: _family(FAMILY_CARRY, "bullish", 0.04),
    }
    with pytest.raises(ValueError, match="tsmom"):
        decide("EURUSD", "2026-01-15", family_scores=scores, cot=None, gate_results=_pass_gates())


def test_gate_failure_blocks_despite_aligned_families():
    scores = {
        FAMILY_CARRY: _family(FAMILY_CARRY, "bullish", 0.05),
        FAMILY_MOMENTUM: _family(FAMILY_MOMENTUM, "bullish", 1.0),
        FAMILY_VALUE: _family(FAMILY_VALUE, "bullish", 2.0),
    }
    gates = _pass_gates()
    gates[0] = GateResult(
        gate_name="volatility",
        result=GATE_FAIL,
        reason_code="vol_block",
        recommended_action="block",
        size_multiplier=0.0,
    )
    decision = decide(
        "EURUSD", "2026-01-15", family_scores=scores, cot=None, gate_results=gates
    )
    assert decision.action == ACTION_NO_TRADE
    assert "gate failure" in decision.reason


def test_gate_failure_keeps_factor_score_context():
    scores = {
        FAMILY_CARRY: _family(FAMILY_CARRY, "bullish", 0.05),
        FAMILY_MOMENTUM: _family(FAMILY_MOMENTUM, "bullish", 1.0),
        FAMILY_VALUE: _family(FAMILY_VALUE, "neutral", 0.0),
    }
    gates = _pass_gates()
    gates[1] = GateResult(
        gate_name="cost",
        result=GATE_FAIL,
        reason_code="missing_atr",
        recommended_action="block",
        size_multiplier=0.0,
    )

    decision = decide(
        "EURUSD", "2026-01-15", family_scores=scores, cot=None, gate_results=gates
    )

    assert decision.action == ACTION_NO_TRADE
    assert decision.reason == "gate failure: cost (missing_atr)"
    assert set(decision.aligned_families) == {FAMILY_CARRY, FAMILY_MOMENTUM}
    assert decision.factor_scores[FAMILY_CARRY]["points"] > 0
    assert decision.factor_scores[FAMILY_MOMENTUM]["points"] > 0


def test_cot_veto_blocks_trade():
    scores = {
        FAMILY_CARRY: _family(FAMILY_CARRY, "bullish", 0.04),
        FAMILY_MOMENTUM: _family(FAMILY_MOMENTUM, "bullish", 0.8),
        FAMILY_VALUE: _family(FAMILY_VALUE, "bullish", 1.5),
    }
    cot = CotOverlayResult(
        symbol="EURUSD",
        as_of_date="2026-01-15",
        base_currency_cot_percentile=95.0,
        quote_currency_cot_percentile=92.0,
        pair_crowding_status="double_crowded",
        action="veto",
        modifier=0.0,
        cot_release_used="2026-01-07",
        cot_lag_days=8,
    )
    decision = decide(
        "EURUSD", "2026-01-15", family_scores=scores, cot=cot, gate_results=_pass_gates()
    )
    assert decision.action == ACTION_NO_TRADE
    assert decision.reason == "cot_double_crowded_veto"


def test_cot_downweight_halves_size():
    scores = {
        FAMILY_CARRY: _family(FAMILY_CARRY, "bullish", 0.04),
        FAMILY_MOMENTUM: _family(FAMILY_MOMENTUM, "bullish", 0.8),
        FAMILY_VALUE: _family(FAMILY_VALUE, "bullish", 1.5),
    }
    cot = CotOverlayResult(
        symbol="EURUSD",
        as_of_date="2026-01-15",
        base_currency_cot_percentile=85.0,
        quote_currency_cot_percentile=50.0,
        pair_crowding_status="one_leg",
        action="downweight",
        modifier=-5.0,
        cot_release_used="2026-01-07",
        cot_lag_days=8,
    )
    decision = decide(
        "EURUSD", "2026-01-15", family_scores=scores, cot=cot, gate_results=_pass_gates()
    )
    assert decision.action == ACTION_BUY
    assert decision.size_multiplier == pytest.approx(0.5)


def test_cot_alone_does_not_create_trade():
    cot = CotOverlayResult(
        symbol="EURUSD",
        as_of_date="2026-01-15",
        base_currency_cot_percentile=85.0,
        quote_currency_cot_percentile=50.0,
        pair_crowding_status="one_leg",
        action="downweight",
        modifier=5.0,
        cot_release_used="2026-01-07",
        cot_lag_days=8,
    )
    scores = {
        FAMILY_CARRY: _family(FAMILY_CARRY, "neutral", 0.0),
        FAMILY_MOMENTUM: _family(FAMILY_MOMENTUM, "neutral", 0.0),
        FAMILY_VALUE: _family(FAMILY_VALUE, "neutral", 0.0),
    }
    decision = decide(
        "EURUSD", "2026-01-15", family_scores=scores, cot=cot, gate_results=_pass_gates()
    )
    assert decision.action == ACTION_NO_TRADE


def test_flatten_for_existing_position_on_vol_gate():
    scores = {
        FAMILY_CARRY: _family(FAMILY_CARRY, "bullish", 0.04),
        FAMILY_MOMENTUM: _family(FAMILY_MOMENTUM, "bullish", 0.8),
        FAMILY_VALUE: _family(FAMILY_VALUE, "bullish", 1.5),
    }
    gates = [
        GateResult(
            gate_name="volatility",
            result=GATE_FAIL,
            reason_code="vol_flatten",
            recommended_action="flatten",
            size_multiplier=0.0,
        ),
    ]
    decision = decide(
        "EURUSD",
        "2026-01-15",
        family_scores=scores,
        cot=None,
        gate_results=gates,
        has_existing_position=True,
    )
    assert decision.action == ACTION_FLATTEN


def test_score_caps_respected():
    scores = {
        FAMILY_CARRY: _family(FAMILY_CARRY, "bullish", 999.0),
        FAMILY_MOMENTUM: _family(FAMILY_MOMENTUM, "bullish", 999.0),
        FAMILY_VALUE: _family(FAMILY_VALUE, "bullish", 999.0),
    }
    decision = decide(
        "EURUSD", "2026-01-15", family_scores=scores, cot=None, gate_results=_pass_gates()
    )
    max_total = SCORE_CAP_CARRY + SCORE_CAP_MOMENTUM + SCORE_CAP_VALUE + SCORE_CAP_COT_MODIFIER
    assert decision.total_score is not None
    assert decision.total_score <= max_total
    assert decision.factor_scores[FAMILY_CARRY]["points"] <= SCORE_CAP_CARRY
    assert decision.factor_scores[FAMILY_MOMENTUM]["points"] <= SCORE_CAP_MOMENTUM
    assert decision.factor_scores[FAMILY_VALUE]["points"] <= SCORE_CAP_VALUE


def test_bearish_alignment_sell():
    scores = {
        FAMILY_CARRY: _family(FAMILY_CARRY, "bearish", -0.04),
        FAMILY_MOMENTUM: _family(FAMILY_MOMENTUM, "bearish", -0.8),
        FAMILY_VALUE: _family(FAMILY_VALUE, "neutral", 0.0),
    }
    decision = decide(
        "EURUSD", "2026-01-15", family_scores=scores, cot=None, gate_results=_pass_gates()
    )
    assert decision.action == ACTION_SELL
    assert decision.direction == "SHORT"


def test_full_decision_integration_from_family_score_objects():
    scores = {
        FAMILY_CARRY: FamilyScore(
            family=FAMILY_CARRY,
            symbol="GBPUSD",
            as_of_date="2026-02-01",
            score=0.03,
            direction="bullish",
            status="ok",
            subcomponents={"net_carry": 0.03},
        ),
        FAMILY_MOMENTUM: FamilyScore(
            family=FAMILY_MOMENTUM,
            symbol="GBPUSD",
            as_of_date="2026-02-01",
            score=0.6,
            direction="bullish",
            status="ok",
        ),
        FAMILY_VALUE: FamilyScore(
            family=FAMILY_VALUE,
            symbol="GBPUSD",
            as_of_date="2026-02-01",
            score=0.5,
            direction="neutral",
            status="ok",
        ),
    }
    cost = cost_gate(spread_pips=1.2, atr_pips=80.0)
    gates = [
        GateResult(gate_name="volatility", result="pass", reason_code="vol_ok"),
        cost,
        GateResult(gate_name="data_quality", result="pass", reason_code="data_ok"),
    ]
    decision = decide(
        "GBPUSD",
        "2026-02-01",
        family_scores=scores,
        cot=None,
        gate_results=gates,
    )
    d = decision.to_dict()
    assert d["symbol"] == "GBPUSD"
    assert d["action"] == ACTION_BUY
    assert len(d["aligned_families"]) == 2
    assert d["cost_diagnostics"]
    assert d["total_score"] is not None
