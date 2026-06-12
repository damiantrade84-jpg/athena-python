"""Calibration tests."""

from __future__ import annotations

import numpy as np
import pytest

sklearn = pytest.importorskip("sklearn")

from athena_ase.models.calibrate import (
    brier_skill,
    fit_isotonic,
    reliability_monotone,
    reliability_table,
)


def test_isotonic_improves_monotonicity_on_fixture():
    y_true = np.array([0, 0, 1, 1, 1, 0, 1, 1])
    y_prob = np.array([0.2, 0.3, 0.4, 0.45, 0.55, 0.6, 0.7, 0.8])
    model = fit_isotonic(y_prob, y_true)
    calibrated = model.predict(y_prob)
    rows = reliability_table(calibrated, y_true, n_bins=4)
    assert reliability_monotone(rows, tol=0.20)
    assert brier_skill(calibrated, y_true) >= brier_skill(y_prob, y_true) - 0.05
