"""Quantile head and bracket tests."""

from __future__ import annotations

import numpy as np

from athena_ase.models.quantile_heads import (
    QUANTILE_LABELS,
    TARGETS,
    clamp_brackets,
    fix_quantile_crossing,
    predict_quantile_heads,
    train_quantile_heads,
)


def test_quantile_crossing_fix_sorts_values():
    row = {"q10": 0.5, "q50": 0.2, "q90": 0.8}
    fixed = fix_quantile_crossing(row)
    vals = [fixed["q10"], fixed["q50"], fixed["q90"]]
    assert vals == sorted(vals)


def test_rr_floor_watch_demotion():
    sl, tp1, reason = clamp_brackets(entry=100.0, direction=1, sl=99.0, tp1=100.1, r_unit=1.0)
    assert reason == "rr_floor_watch"


def test_trains_five_quantile_heads_for_each_target():
    x = np.arange(240, dtype=float).reshape(80, 3)
    targets = {
        "net_R": np.linspace(-1.0, 1.0, 80),
        "MAE_R": np.linspace(0.1, 1.2, 80),
        "MFE_R": np.linspace(0.2, 1.8, 80),
        "hold_bars": np.linspace(1.0, 16.0, 80),
    }

    heads = train_quantile_heads(x, targets)

    assert set(heads) == set(TARGETS)
    assert all(tuple(heads[target]) == QUANTILE_LABELS for target in TARGETS)
    assert sum(len(block) for block in heads.values()) == 20


def test_quantile_predictions_are_corrected_per_row():
    class ConstantHead:
        def __init__(self, value: float):
            self.value = value

        def predict(self, x):
            return np.full(len(x), self.value)

    heads = {
        target: {
            "q10": ConstantHead(0.9),
            "q25": ConstantHead(0.7),
            "q50": ConstantHead(0.5),
            "q75": ConstantHead(0.3),
            "q90": ConstantHead(0.1),
        }
        for target in TARGETS
    }

    predicted = predict_quantile_heads(heads, np.zeros((2, 3)))

    assert predicted["net_R"][0] == {
        "q10": 0.1,
        "q25": 0.3,
        "q50": 0.5,
        "q75": 0.7,
        "q90": 0.9,
    }
