"""Holdout single-shot guard tests."""

from __future__ import annotations

import pytest

from athena_ase.exceptions import HoldoutAlreadyEvaluated
from athena_ase.registry.holdout import HoldoutRegistry
from athena_ase.validation.holdout import run_holdout_eval


def test_holdout_single_shot_guard():
    reg = HoldoutRegistry()
    run_holdout_eval(
        family="crypto",
        horizon="intraday",
        metrics={
            "oos_trades": 45,
            "instruments": 5,
            "folds_nonneg": 3,
            "max_instrument_profit_share": 0.2,
            "max_dd_R": 5,
            "brier_skill": 0.01,
        },
        registry=reg,
        save=False,
    )
    with pytest.raises(HoldoutAlreadyEvaluated):
        reg.record_holdout_eval(family="crypto", horizon="intraday", passed=True, metrics={})


def test_passing_legacy_holdout_can_bind_identity_once_when_metrics_match():
    metrics = {
        "oos_trades": 45,
        "instruments": 5,
        "folds_nonneg": 3,
        "max_instrument_profit_share": 0.2,
        "max_dd_R": 5,
        "brier_skill": 0.01,
    }
    reg = HoldoutRegistry()
    run_holdout_eval(
        family="crypto",
        horizon="intraday",
        metrics=metrics,
        registry=reg,
        save=False,
    )

    result = run_holdout_eval(
        family="crypto",
        horizon="intraday",
        metrics=metrics,
        registry=reg,
        save=False,
        artifact_version="v1",
        artifact_hash="artifact-one",
    )

    record = reg.holdout["crypto:intraday"]
    assert result["identity_bound"] is True
    assert (record.artifact_version, record.artifact_hash) == ("v1", "artifact-one")
    with pytest.raises(HoldoutAlreadyEvaluated):
        run_holdout_eval(
            family="crypto",
            horizon="intraday",
            metrics=metrics,
            registry=reg,
            save=False,
            artifact_version="v2",
            artifact_hash="artifact-two",
        )
