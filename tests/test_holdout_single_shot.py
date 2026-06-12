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
