"""Threshold distribution drift monitor (pure helper).

The 2026-08-08 ENGINE_A_SCORE_GROUP_THRESHOLDS bars were set to the measured
p70 of the live score distribution, explicitly NOT outcome-backed. Distributions
drift; this quantifies how far each configured bar has moved from its
calibration percentile.
"""
from __future__ import annotations

import pytest

from engine_a_v3.audit import threshold_distribution_drift


def test_flags_bar_above_calibration_percentile():
    # p70 of 0..99 (n=100) is ~70.0 -> bar 80 sits well above it.
    scores = [float(i) for i in range(100)]
    rows = threshold_distribution_drift(
        {"forex_majors": scores},
        {"forex_majors": 80.0},
        percentile=0.70,
        tolerance_pct=0.10,
    )
    assert len(rows) == 1
    row = rows[0]
    assert row["sampleSize"] == 100
    assert abs(row["observedPercentile"] - 70.0) < 1.0
    assert row["driftPct"] > 0.10
    assert row["driftFlag"] is True


def test_within_tolerance_passes():
    scores = [float(i) for i in range(100)]
    rows = threshold_distribution_drift(
        {"crypto_btc": scores},
        {"crypto_btc": 70.0},
        percentile=0.70,
        tolerance_pct=0.10,
    )
    assert rows[0]["driftFlag"] is False


def test_falls_back_to_default_bar():
    scores = [float(i) for i in range(100)]
    rows = threshold_distribution_drift(
        {"unknown_group": scores},
        {"default": 35.0},
        percentile=0.70,
        tolerance_pct=0.10,
    )
    assert rows[0]["configuredBar"] == 35.0
    assert rows[0]["driftFlag"] is True  # 35 vs ~69 -> far below p70


def test_empty_scores_and_missing_bar_flagged():
    rows = threshold_distribution_drift(
        {"a": [], "b": [1.0, 2.0]},
        {},
        percentile=0.70,
    )
    by_group = {row["group"]: row for row in rows}
    assert by_group["a"]["reason"] == "no_scores"
    assert by_group["a"]["driftFlag"] is True
    assert by_group["b"]["reason"] == "no_configured_bar"
    assert by_group["b"]["driftFlag"] is True


def test_invalid_percentile_rejected():
    with pytest.raises(ValueError):
        threshold_distribution_drift({"g": [1.0]}, {"g": 1.0}, percentile=0.0)
