from __future__ import annotations

from engine_a_v3.calibration import (
    candidate_parameter_grid,
    select_development_candidate,
    validate_holdout,
)


def test_candidate_grid_uses_approved_weights_deadbands_quantiles_and_exits():
    candidates = candidate_parameter_grid([float(i) / 100 for i in range(100, 300)])
    assert candidates
    assert {item["directionDeadband"] for item in candidates} == {0.05, 0.10, 0.15}
    assert {item["exitPolicy"] for item in candidates} == {"SINGLE_TP1", "SPLIT_50_50"}
    assert all(sum(item["weights"].values()) == 1.0 for item in candidates)
    assert all(0 <= item["weights"]["volume"] <= 0.3 for item in candidates)
    assert len({item["tradeThreshold"] for item in candidates}) == 7


def test_development_selection_is_expectancy_first_then_drawdown_then_trades():
    evaluations = [
        {"id": "a", "medianFoldExpectancyR": 0.10, "positiveFolds": 4, "oosTrades": 80, "maxDrawdownR": 8},
        {"id": "b", "medianFoldExpectancyR": 0.10, "positiveFolds": 4, "oosTrades": 90, "maxDrawdownR": 7},
        {"id": "c", "medianFoldExpectancyR": 0.11, "positiveFolds": 2, "oosTrades": 100, "maxDrawdownR": 4},
    ]
    assert select_development_candidate(evaluations)["id"] == "b"


def test_holdout_gate_requires_15_trades_and_positive_expectancy():
    assert validate_holdout({"holdoutTrades": 15, "holdoutExpectancyR": 0.01})
    assert not validate_holdout({"holdoutTrades": 14, "holdoutExpectancyR": 1.0})
    assert not validate_holdout({"holdoutTrades": 20, "holdoutExpectancyR": 0.0})
