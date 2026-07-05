"""Scoring and acceptance tests."""

from __future__ import annotations

from research.edgelab.scoring import compute_score, evaluate_candidate


def test_low_trade_count_rejected(default_config):
    accepted, reasons = evaluate_candidate(
        baseline_metrics={"trade_count": 50, "max_drawdown_R": 1.0, "oos_score": 0.1},
        candidate_metrics={"trade_count": 10, "max_drawdown_R": 0.5, "oos_score": 0.2},
        baseline_score=10.0,
        candidate_score=20.0,
        config=default_config,
        safety_ok=True,
        safety_reasons=[],
        tests_passed=True,
        evaluation_ok=True,
        metrics_found=True,
        data_freshness_ok=True,
    )
    assert accepted is False
    assert "low_trade_count" in reasons


def test_worse_drawdown_rejected(default_config):
    accepted, reasons = evaluate_candidate(
        baseline_metrics={"trade_count": 50, "max_drawdown_R": 1.0, "oos_score": 0.1},
        candidate_metrics={"trade_count": 50, "max_drawdown_R": 4.5, "oos_score": 0.2},
        baseline_score=10.0,
        candidate_score=20.0,
        config=default_config,
        safety_ok=True,
        safety_reasons=[],
        tests_passed=True,
        evaluation_ok=True,
        metrics_found=True,
        data_freshness_ok=True,
    )
    assert accepted is False
    assert "excessive_drawdown" in reasons


def test_worse_oos_rejected(default_config):
    accepted, reasons = evaluate_candidate(
        baseline_metrics={"trade_count": 50, "max_drawdown_R": 1.0, "oos_score": 0.20},
        candidate_metrics={"trade_count": 50, "max_drawdown_R": 1.0, "oos_score": 0.05},
        baseline_score=10.0,
        candidate_score=20.0,
        config=default_config,
        safety_ok=True,
        safety_reasons=[],
        tests_passed=True,
        evaluation_ok=True,
        metrics_found=True,
        data_freshness_ok=True,
    )
    assert accepted is False
    assert "oos_worse_than_baseline" in reasons


def test_data_freshness_fail_rejects(default_config):
    accepted, reasons = evaluate_candidate(
        baseline_metrics={"trade_count": 50, "max_drawdown_R": 1.0, "oos_score": 0.1},
        candidate_metrics={"trade_count": 50, "max_drawdown_R": 1.0, "oos_score": 0.2},
        baseline_score=10.0,
        candidate_score=20.0,
        config=default_config,
        safety_ok=True,
        safety_reasons=[],
        tests_passed=True,
        evaluation_ok=True,
        metrics_found=True,
        data_freshness_ok=False,
    )
    assert accepted is False
    assert "data_freshness_failed" in reasons
