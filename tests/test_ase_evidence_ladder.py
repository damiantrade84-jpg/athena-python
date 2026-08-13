"""Evidence ladder enforcement (ASE v2.1 §11).

Every constant declared in PROVISIONAL/VALIDATED must actually gate. These
tests pin the two failure modes that made the ladder decorative: a declared
threshold with no check, and a NaN metric slipping through a bare comparison.
"""

from __future__ import annotations

import math

from athena_ase.validation.gates import (
    PROVISIONAL,
    VALIDATED,
    check_provisional,
    check_validated,
    cost_stress_metric_key,
)


def _provisional_metrics(**overrides):
    metrics = {
        "oos_trades": 50,
        "instruments": 5,
        "folds_nonneg": 3,
        "max_instrument_profit_share": 0.25,
        "max_dd_R": 8.0,
        "brier_skill": 0.05,
        "cost_stress_expectancy_1_5": 0.04,
    }
    metrics.update(overrides)
    return metrics


def _validated_metrics(**overrides):
    metrics = _provisional_metrics(
        oos_trades=200,
        bootstrap_lb_expectancy=0.02,
        dsr_prob=0.97,
        pbo=0.10,
        reliability_max_drop=0.01,
    )
    metrics[cost_stress_metric_key(2.0)] = 0.02
    metrics.update(overrides)
    return metrics


def test_cost_stress_key_matches_declared_multiples():
    assert cost_stress_metric_key(PROVISIONAL["cost_stress_ok_at"]) == "cost_stress_expectancy_1_5"
    assert cost_stress_metric_key(VALIDATED["cost_stress_ok_at"]) == "cost_stress_expectancy_2_0"


def test_provisional_passes_on_complete_metrics():
    ok, failures = check_provisional(_provisional_metrics())
    assert ok, failures


def test_provisional_enforces_declared_cost_stress():
    metrics = _provisional_metrics()
    del metrics["cost_stress_expectancy_1_5"]
    ok, failures = check_provisional(metrics)
    assert not ok
    assert "cost_stress" in failures


def test_provisional_rejects_negative_cost_stress_expectancy():
    ok, failures = check_provisional(_provisional_metrics(cost_stress_expectancy_1_5=-0.01))
    assert not ok
    assert "cost_stress" in failures


def test_nan_metrics_fail_closed_rather_than_passing_comparisons():
    # A bare `nan > limit` is False, which previously read as "passed".
    for field in ("max_instrument_profit_share", "max_dd_R", "brier_skill"):
        ok, _ = check_provisional(_provisional_metrics(**{field: math.nan}))
        assert not ok, f"NaN {field} must fail closed"
    assert not check_provisional(_provisional_metrics(oos_trades=math.nan))[0]


def test_validated_passes_on_complete_metrics():
    ok, failures = check_validated(_validated_metrics())
    assert ok, failures


def test_validated_requires_the_higher_trade_floor():
    ok, failures = check_validated(_validated_metrics(oos_trades=100))
    assert not ok
    assert "oos_trades" in failures


def test_validated_fails_closed_when_pbo_is_absent():
    metrics = _validated_metrics()
    del metrics["pbo"]
    ok, failures = check_validated(metrics)
    assert not ok
    assert "pbo" in failures


def test_validated_gates_dsr_on_probability_not_raw_z_score():
    # A raw deflated-Sharpe z of 0.95 is ~83rd percentile, well under the
    # 0.95 confidence the threshold names.
    ok, failures = check_validated(_validated_metrics(dsr_prob=0.83))
    assert not ok
    assert "dsr" in failures


def test_validated_rejects_non_monotone_reliability():
    ok, failures = check_validated(
        _validated_metrics(reliability_max_drop=VALIDATED["reliability_monotone_tol"] + 0.01)
    )
    assert not ok
    assert "reliability_monotone" in failures


def test_validated_stresses_costs_harder_than_provisional():
    metrics = _validated_metrics()
    del metrics[cost_stress_metric_key(2.0)]
    ok, failures = check_validated(metrics)
    assert not ok
    assert "cost_stress" in failures


def test_validated_surfaces_provisional_failures_with_a_prefix():
    ok, failures = check_validated(_validated_metrics(instruments=1))
    assert not ok
    assert "provisional:instruments" in failures
