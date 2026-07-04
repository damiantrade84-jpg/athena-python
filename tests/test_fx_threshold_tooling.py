"""Tests for athena_fx.threshold_tooling — empirical threshold analysis."""
from __future__ import annotations

from pathlib import Path

import pytest

from athena_fx.threshold_tooling import (
    candidate_thresholds,
    score_distribution,
    simulate_threshold,
    threshold_report,
)


def _score_fixture() -> list[dict]:
    return [{"total_score": float(i), "aligned_families": ["carry"]} for i in range(100)]


def test_score_distribution_percentiles():
    dist = score_distribution(_score_fixture(), percentiles=(50, 60, 70, 80, 90))
    assert dist["count"] == 100
    assert dist["min"] == pytest.approx(0.0)
    assert dist["max"] == pytest.approx(99.0)
    assert dist["mean"] == pytest.approx(49.5)
    assert dist["percentiles"][50] == pytest.approx(49.5)
    assert dist["percentiles"][60] == pytest.approx(59.4)
    assert dist["percentiles"][90] == pytest.approx(89.1)


def test_candidate_thresholds_match_percentiles():
    dist = score_distribution(_score_fixture())
    candidates = candidate_thresholds(dist)
    assert len(candidates) == len(dist["percentiles"])
    for c in candidates:
        assert c["threshold"] == dist["percentiles"][c["percentile"]]


def test_simulate_threshold_counts():
    decisions = [
        {"total_score": 10.0, "aligned_families": ["carry", "momentum"], "outcome_r": 1.5},
        {"total_score": 20.0, "aligned_families": ["carry"], "outcome_r": -0.5},
        {"total_score": 5.0, "aligned_families": ["value"]},
        {"total_score": 30.0, "aligned_families": ["momentum", "value"], "outcome_r": 2.0},
    ]
    sim = simulate_threshold(decisions, threshold=15.0)
    assert sim["threshold"] == pytest.approx(15.0)
    assert sim["trade_count"] == 2
    assert sim["feed_rate"] == pytest.approx(0.5)
    assert sim["rejected_bottom_tail"] == 2
    assert sim["expectancy_r"] == pytest.approx(0.75)
    assert sim["family_coverage"]["carry"] == 1
    assert sim["family_coverage"]["momentum"] == 1
    assert sim["family_coverage"]["value"] == 1


def test_threshold_report_end_to_end():
    decisions = _score_fixture()
    for i, d in enumerate(decisions):
        if i % 3 == 0:
            d["outcome_r"] = 0.5
        d["aligned_families"] = ["carry"] if i % 2 == 0 else ["momentum"]

    report = threshold_report(decisions)
    assert "distribution" in report
    assert "candidates" in report
    assert "simulations" in report
    assert len(report["simulations"]) == len(report["candidates"])
    assert report["distribution"]["count"] == 100


def test_threshold_tooling_no_hardcoded_literal():
    source = Path(__file__).resolve().parents[1] / "athena_fx" / "threshold_tooling.py"
    text = source.read_text(encoding="utf-8")
    assert "0.70" not in text
    assert "0.7 " not in text
