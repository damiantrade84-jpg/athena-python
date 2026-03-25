"""Unit tests for per-scan cross-sectional quantile floors."""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from scanner import compute_scan_quantile_floors, _linear_percentile


def test_linear_percentile_edges():
    assert _linear_percentile([5.0], 80) == 5.0
    assert _linear_percentile([0.0, 10.0], 50) == 5.0
    assert _linear_percentile([], 50) is None


def test_quantile_disabled_all_none():
    scores = {"crypto": [1.0, 2.0, 3.0]}
    out = compute_scan_quantile_floors(
        scores,
        enabled=False,
        min_samples=2,
        top_fraction_cfg={"crypto": 0.5},
    )
    assert out == {"crypto": None}


def test_quantile_min_samples():
    scores = {"crypto": [1.0, 2.0]}
    out = compute_scan_quantile_floors(
        scores,
        enabled=True,
        min_samples=5,
        top_fraction_cfg={"crypto": 0.2, "default": 0.2},
    )
    assert out["crypto"] is None


def test_quantile_top_fraction_uses_default():
    scores = {"crypto": [1.0, 2.0, 3.0, 4.0, 5.0]}
    out = compute_scan_quantile_floors(
        scores,
        enabled=True,
        min_samples=3,
        top_fraction_cfg={"default": 0.2},
    )
    assert out["crypto"] is not None
    assert 4.0 <= out["crypto"] <= 5.0


def test_quantile_invalid_fraction_skipped():
    scores = {"forex": [0.5, 0.6, 0.7, 0.8, 0.9]}
    out = compute_scan_quantile_floors(
        scores,
        enabled=True,
        min_samples=3,
        top_fraction_cfg={"forex": 1.0},
    )
    assert out["forex"] is None
