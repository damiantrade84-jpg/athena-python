"""Promotion requirement tests."""

from __future__ import annotations

from athena_ase.registry.holdout import HoldoutRegistry
from athena_ase.validation.promotion import promotion_requirements


def _metrics():
    return {
        "oos_trades": 50,
        "instruments": 5,
        "folds_nonneg": 3,
        "max_instrument_profit_share": 0.25,
        "max_dd_R": 8.0,
        "brier_skill": 0.05,
        "cost_stress_expectancy_1_5": 0.04,
    }


def test_promotion_requires_passing_holdout():
    reg = HoldoutRegistry()
    ok, failures = promotion_requirements(
        family="forex",
        horizon="swing",
        metrics=_metrics(),
        holdout=reg,
    )
    assert ok is False
    assert "holdout_not_evaluated" in failures


def test_promotion_passes_after_holdout():
    reg = HoldoutRegistry()
    reg.record_holdout_eval(family="forex", horizon="swing", passed=True, metrics=_metrics())
    ok, failures = promotion_requirements(
        family="forex",
        horizon="swing",
        metrics=_metrics(),
        holdout=reg,
    )
    assert ok is True
    assert failures == []
