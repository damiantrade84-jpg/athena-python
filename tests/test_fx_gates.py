"""Tests for athena_fx.gates — volatility and cost hard gates."""
from __future__ import annotations

import inspect

import pytest

from athena_fx.gates import (
    cost_gate,
    data_quality_gate,
    realized_vol_metrics,
    volatility_gate,
)
from athena_fx.models import (
    DIAG_MISSING_CARRY,
    GATE_FAIL,
    GATE_PASS,
    GATE_REDUCED,
    Diagnostic,
    FactorDerivatives,
)


def _calm_returns(n: int, magnitude: float = 0.001) -> list[float]:
    return [magnitude if i % 2 == 0 else -magnitude for i in range(n)]


def _wild_tail(n: int, magnitude: float = 0.05) -> list[float]:
    return [magnitude if i % 2 == 0 else -magnitude for i in range(n)]


def _high_vol_returns() -> list[float]:
    """Calm history then wild tail → vol_z should exceed block threshold."""
    return _calm_returns(300) + _wild_tail(20)


def _moderate_vol_returns() -> list[float]:
    """Elevated but below flatten; above block for existing-position reduce test."""
    calm = _calm_returns(300, magnitude=0.001)
    tail = _wild_tail(20, magnitude=0.010)
    return calm + tail


def _vol_jump_returns() -> list[float]:
    """Calm history, moderate middle, then sudden spike for vol_jump >= 2."""
    calm = _calm_returns(300, magnitude=0.001)
    moderate = _calm_returns(15, magnitude=0.008)
    spike = _wild_tail(5, magnitude=0.08)
    return calm + moderate + spike


def _post_block_calm_returns() -> list[float]:
    """After a block event, calm enough for resume once hysteresis clears."""
    return _calm_returns(320, magnitude=0.0005)


def test_realized_vol_metrics_insufficient_history():
    assert realized_vol_metrics(_calm_returns(100)) is None


def test_vol_gate_blocks_new_entries():
    returns = _high_vol_returns()
    metrics = realized_vol_metrics(returns)
    assert metrics is not None
    assert metrics["vol_z"] > 2.0

    result, state = volatility_gate(returns, has_existing_position=False)
    assert result.result == GATE_FAIL
    assert result.recommended_action == "block"
    assert result.reason_code == "vol_block"
    assert result.size_multiplier == 0.0
    assert state["blocked"] is True


def test_vol_gate_derisks_existing_position(monkeypatch):
    # Patch the CONFIG object the module holds (immune to config reloads
    # performed by unrelated tests earlier in the suite).
    from athena_fx import gates as gates_module

    returns = _moderate_vol_returns()
    metrics = realized_vol_metrics(returns)
    assert metrics is not None
    vol_z = metrics["vol_z"]
    # Sudden tail spikes z-score; bracket it with config thresholds for this scenario.
    monkeypatch.setitem(gates_module.CONFIG, "FX_FACTOR_VOL_BLOCK_Z", vol_z - 0.5)
    monkeypatch.setitem(gates_module.CONFIG, "FX_FACTOR_VOL_FLATTEN_Z", vol_z + 0.5)
    monkeypatch.setitem(
        gates_module.CONFIG, "FX_FACTOR_VOL_JUMP_FLATTEN", metrics["vol_jump"] + 1.0
    )

    result, _state = volatility_gate(returns, has_existing_position=True)
    assert result.result == GATE_REDUCED
    assert result.recommended_action == "reduce"
    assert result.size_multiplier == 0.5


def test_vol_gate_flatten_on_vol_jump():
    returns = _vol_jump_returns()
    metrics = realized_vol_metrics(returns)
    assert metrics is not None
    assert metrics["vol_jump"] >= 2.0

    result, _state = volatility_gate(returns, has_existing_position=True)
    assert result.result == GATE_FAIL
    assert result.recommended_action == "flatten"
    assert result.reason_code == "vol_flatten"


def test_vol_gate_hysteresis_hold_then_resume():
    block_returns = _high_vol_returns()
    result, state = volatility_gate(block_returns, has_existing_position=False)
    assert result.result == GATE_FAIL
    assert state["blocked"] is True

    calm = _post_block_calm_returns()
    metrics = realized_vol_metrics(calm)
    assert metrics is not None
    assert metrics["vol_z"] < 0.5

    state = dict(state)
    for i in range(2):
        result, state = volatility_gate(calm, has_existing_position=False, hysteresis_state=state)
        assert result.result == GATE_FAIL
        assert result.reason_code == "hysteresis_hold"
        assert result.recommended_action == "block"

    result, state = volatility_gate(calm, has_existing_position=False, hysteresis_state=state)
    assert result.result == GATE_PASS
    assert result.recommended_action == "allow"
    assert state["blocked"] is False


def test_vol_gate_insufficient_history_fail_closed():
    result, _state = volatility_gate(_calm_returns(50))
    assert result.result == GATE_FAIL
    assert result.reason_code == "insufficient_vol_history"


def test_cost_gate_missing_spread_fail_closed():
    result = cost_gate(spread_pips=None, atr_pips=10.0)
    assert result.result == GATE_FAIL
    assert result.reason_code == "missing_spread"


def test_cost_gate_missing_atr_fail_closed():
    result = cost_gate(spread_pips=1.0, atr_pips=None)
    assert result.result == GATE_FAIL
    assert result.reason_code == "missing_atr"


def test_cost_gate_no_score_parameter():
    sig = inspect.signature(cost_gate)
    assert "score" not in sig.parameters


def test_cost_gate_rollover_and_news_windows_fail():
    r1 = cost_gate(spread_pips=1.0, atr_pips=100.0, rollover_window=True)
    assert r1.result == GATE_FAIL
    assert r1.reason_code == "rollover_window"

    r2 = cost_gate(spread_pips=1.0, atr_pips=100.0, news_window=True)
    assert r2.result == GATE_FAIL
    assert r2.reason_code == "news_window"


def test_cost_gate_passes_when_within_limit():
    result = cost_gate(spread_pips=1.0, atr_pips=100.0, slippage_pips=0.3)
    assert result.result == GATE_PASS
    assert result.numeric_inputs["total_cost_pips"] <= result.numeric_inputs["cost_limit_pips"]


def test_cost_gate_records_directional_swap_without_fail():
    result = cost_gate(
        spread_pips=1.0,
        atr_pips=100.0,
        directional_swap_annual=-0.05,
    )
    assert result.result == GATE_PASS
    assert result.numeric_inputs["directional_swap_annual"] == pytest.approx(-0.05)


def test_data_quality_gate_invalid_fail():
    deriv = FactorDerivatives(
        symbol="EURUSD",
        as_of_date="2026-01-15",
        status="invalid",
        diagnostics=[
            Diagnostic(DIAG_MISSING_CARRY, "error", "carry missing"),
        ],
    )
    result = data_quality_gate(deriv)
    assert result.result == GATE_FAIL
    assert DIAG_MISSING_CARRY in result.reason_code


def test_data_quality_gate_degraded_reduced():
    deriv = FactorDerivatives(
        symbol="EURUSD",
        as_of_date="2026-01-15",
        status="degraded",
    )
    result = data_quality_gate(deriv)
    assert result.result == GATE_REDUCED


def test_data_quality_gate_ok_passes():
    deriv = FactorDerivatives(symbol="EURUSD", as_of_date="2026-01-15", status="ok")
    result = data_quality_gate(deriv)
    assert result.result == GATE_PASS
