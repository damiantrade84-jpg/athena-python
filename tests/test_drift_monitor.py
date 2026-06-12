"""Drift monitor tests."""

from __future__ import annotations

import numpy as np

from athena_ase.inference.monitor import population_stability_index, should_auto_demote


def test_psi_threshold_auto_demote():
    psi = {f"f{i}": 0.3 for i in range(3)}
    assert should_auto_demote(psi) is True


def test_single_critical_psi_auto_demotes():
    psi = {"feature_a": 0.55}
    assert should_auto_demote(psi) is True


def test_psi_computation_non_negative():
    expected = np.linspace(0, 1, 100)
    actual = expected + 0.05
    assert population_stability_index(expected, actual) >= 0.0
