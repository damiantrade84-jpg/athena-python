"""T0.1 — expected-net-R threshold is selected train-side, never on eval."""

from __future__ import annotations

import inspect

import numpy as np
import pandas as pd
import pytest

from athena_ase.features.build import active_feature_schema
from athena_research.ase.train import (
    _fit_and_threshold,
    select_expected_net_r_threshold,
    train_family_horizon,
)


def _synthetic_frame(n: int = 600, seed: int = 3) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    schema = active_feature_schema(enriched=False)
    frame = pd.DataFrame(rng.normal(size=(n, len(schema))), columns=list(schema))
    signal = frame[schema[0]].to_numpy()
    frame["y"] = (signal + rng.normal(scale=1.0, size=n) > 0).astype(int)
    frame["net_R"] = np.where(frame["y"] == 1, 0.8, -1.0) + rng.normal(scale=0.1, size=n)
    frame["MAE_R"] = np.abs(rng.normal(scale=0.5, size=n))
    frame["MFE_R"] = np.abs(rng.normal(scale=0.8, size=n))
    frame["hold_bars"] = rng.integers(1, 10, size=n)
    frame["sample_weight"] = 1.0
    frame["decision_time_ms"] = np.arange(n) * 3_600_000
    return frame


def test_select_threshold_picks_optimum_of_its_own_inputs():
    expected = np.linspace(0.0, 1.0, 100)
    realized = np.where(expected >= 0.60, 2.0, -1.0)
    sel = select_expected_net_r_threshold(expected, realized, min_trades=10)
    # All thresholds >= 0.606 tie at expectancy 2.0; ties break to the highest.
    assert sel.threshold >= 0.60
    assert sel.expectancy == pytest.approx(2.0)
    assert not sel.fallback


def test_fit_and_threshold_takes_no_eval_input():
    # Structural guarantee: eval data cannot influence threshold selection
    # because it is not an input to the selecting function.
    params = inspect.signature(_fit_and_threshold).parameters
    assert set(params) == {"frame", "feature_names", "family", "horizon", "log_trials"}


def test_threshold_is_deterministic_and_independent_of_eval_mutation():
    schema = active_feature_schema(enriched=False)
    train_frame = _synthetic_frame()
    first = _fit_and_threshold(
        train_frame,
        feature_names=schema,
        family="forex",
        horizon="swing",
        log_trials=False,
    )
    # A hostile eval frame with a "magic" net_R structure exists but is never
    # passed anywhere — rerunning on the identical train frame must reproduce
    # the same frozen threshold.
    eval_frame = _synthetic_frame(seed=99)
    eval_frame["net_R"] = 100.0  # would dominate any eval-tuned sweep
    second = _fit_and_threshold(
        train_frame.copy(),
        feature_names=schema,
        family="forex",
        horizon="swing",
        log_trials=False,
    )
    assert first["threshold"].threshold == pytest.approx(second["threshold"].threshold)
    assert first["threshold_selected_on_rows"] == second["threshold_selected_on_rows"]
    assert first["threshold_selected_on_rows"] < len(train_frame)


def test_train_family_horizon_no_longer_selects_on_eval():
    src = inspect.getsource(train_family_horizon)
    assert "select_expected_net_r_threshold" not in src
    assert '"threshold_source": "train_calibration_slice"' in src
    assert '"eval_untouched": True' in src
    assert '"calibration_slice_dual_use": True' in src
