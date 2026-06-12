"""Calibration tests."""

from __future__ import annotations

import numpy as np
import pytest

sklearn = pytest.importorskip("sklearn")

from athena_ase.models.calibrate import (
    brier_skill,
    chronological_calibration_split,
    fit_isotonic,
    reliability_monotone,
    reliability_table,
)
from athena_ase.models.meta import BASE_HGB_PARAMS, train_meta_classifier


def test_isotonic_improves_monotonicity_on_fixture():
    y_true = np.array([0, 0, 1, 1, 1, 0, 1, 1])
    y_prob = np.array([0.2, 0.3, 0.4, 0.45, 0.55, 0.6, 0.7, 0.8])
    model = fit_isotonic(y_prob, y_true)
    calibrated = model.predict(y_prob)
    rows = reliability_table(calibrated, y_true, n_bins=4)
    assert reliability_monotone(rows, tol=0.20)
    assert brier_skill(calibrated, y_true) >= brier_skill(y_prob, y_true) - 0.05


def test_chronological_calibration_split_uses_final_15_percent_only():
    fit_idx, calibration_idx = chronological_calibration_split(100)

    assert fit_idx.tolist() == list(range(85))
    assert calibration_idx.tolist() == list(range(85, 100))


def test_meta_classifier_uses_one_fixed_hgb_configuration():
    rows = [
        {
            "agreement_count": float(i % 3),
            "instrument_id": f"instrument-{i % 4}",
            "subclass": "fixture",
            "session": float(i % 4),
            "vol_regime": float(i % 3),
        }
        for i in range(240)
    ]
    y = np.array([i % 2 for i in range(240)], dtype=int)

    result = train_meta_classifier(rows, y, log_trials=False)

    assert result.config == BASE_HGB_PARAMS
    assert result.model.get_params()["learning_rate"] == 0.06
    assert result.model.get_params()["max_leaf_nodes"] == 31
    assert result.model.get_params()["min_samples_leaf"] == 200
