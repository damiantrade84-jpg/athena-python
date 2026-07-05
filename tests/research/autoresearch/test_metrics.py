"""Metric parsing and acceptance rule tests."""

from __future__ import annotations

from research.autoresearch.metrics import (
    compute_score,
    evaluate_acceptance,
    parse_command_output,
    parse_metrics_json,
)


def test_low_trade_count_rejected(default_config):
    baseline = {"trade_count": 50, "max_drawdown_R": 1.0, "oos_score": 0.1}
    candidate = {"trade_count": 10, "max_drawdown_R": 0.5, "oos_score": 0.2}
    accepted, reasons = evaluate_acceptance(
        baseline_metrics=baseline,
        candidate_metrics=candidate,
        baseline_score=10.0,
        candidate_score=15.0,
        config=default_config,
        safety_ok=True,
        safety_reasons=[],
        tests_passed=True,
        evaluation_ok=True,
        metrics_found=True,
    )
    assert accepted is False
    assert "low_trade_count" in reasons


def test_worse_drawdown_rejected(default_config):
    baseline = {"trade_count": 50, "max_drawdown_R": 1.0, "oos_score": 0.1}
    candidate = {"trade_count": 50, "max_drawdown_R": 4.5, "oos_score": 0.2}
    accepted, reasons = evaluate_acceptance(
        baseline_metrics=baseline,
        candidate_metrics=candidate,
        baseline_score=10.0,
        candidate_score=20.0,
        config=default_config,
        safety_ok=True,
        safety_reasons=[],
        tests_passed=True,
        evaluation_ok=True,
        metrics_found=True,
    )
    assert accepted is False
    assert "excessive_drawdown" in reasons


def test_worse_oos_rejected(default_config):
    baseline = {"trade_count": 50, "max_drawdown_R": 1.0, "oos_score": 0.20}
    candidate = {"trade_count": 50, "max_drawdown_R": 1.0, "oos_score": 0.05}
    accepted, reasons = evaluate_acceptance(
        baseline_metrics=baseline,
        candidate_metrics=candidate,
        baseline_score=10.0,
        candidate_score=20.0,
        config=default_config,
        safety_ok=True,
        safety_reasons=[],
        tests_passed=True,
        evaluation_ok=True,
        metrics_found=True,
    )
    assert accepted is False
    assert "oos_worse_than_baseline" in reasons


def test_parse_metrics_json_wrapper():
    raw = '{"metrics": {"expR": 0.2, "profit_factor": 1.5, "sqn": 1.8, "trade_count": 40}}'
    parsed = parse_metrics_json(raw)
    assert parsed["expR"] == 0.2
    assert parsed["trade_count"] == 40


def test_parse_command_output_fallback():
    stdout = "expectancy: 0.11\nPF: 1.4\nSQN: 1.0\ntrades: 35\n"
    parsed = parse_command_output(stdout, "")
    assert parsed["expR"] == 0.11
    assert parsed["profit_factor"] == 1.4
    assert parsed["trade_count"] == 35


def test_compute_score_increases_with_expR(default_config):
    low = compute_score({"expR": 0.1, "profit_factor": 1.0, "sqn": 1.0, "trade_count": 30}, default_config)
    high = compute_score({"expR": 0.3, "profit_factor": 1.0, "sqn": 1.0, "trade_count": 30}, default_config)
    assert high > low
