import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from research_metrics import (
    build_research_metrics,
    deflated_sharpe_ratio,
    enrich_backtest_summary,
    probability_of_backtest_overfitting,
    probabilistic_sharpe_ratio,
)


def test_probabilistic_sharpe_ratio_increases_for_stronger_returns():
    weak_returns = [0.1, -0.1, 0.05, -0.02, 0.03, 0.01]
    strong_returns = [0.5, 0.4, 0.45, 0.35, 0.5, 0.42]

    weak = probabilistic_sharpe_ratio(0.2, weak_returns)
    strong = probabilistic_sharpe_ratio(2.0, strong_returns)

    assert weak["available"] is True
    assert strong["available"] is True
    assert weak["value"] < strong["value"]


def test_deflated_sharpe_ratio_reports_trial_assumption_when_missing():
    returns = [0.4, 0.2, 0.5, -0.1, 0.3, 0.25, 0.35]
    result = deflated_sharpe_ratio(1.2, returns)

    assert result["available"] is True
    assert result["numTrials"] == 1
    assert "num_trials_unavailable_assumed_1" in result["assumptions"]


def test_pbo_returns_unavailable_without_trial_matrix():
    pbo = probability_of_backtest_overfitting()

    assert pbo["available"] is False
    assert pbo["value"] is None


def test_enrich_backtest_summary_adds_research_metrics():
    summary = {
        "pair": "BTCUSD",
        "sharpe": 1.1,
        "trades": [
            {"resultR": 0.5},
            {"resultR": -0.2},
            {"resultR": 0.3},
            {"resultR": 0.4},
        ],
    }

    enriched = enrich_backtest_summary(summary)
    metrics = enriched["researchMetrics"]

    assert "psr" in metrics
    assert "dsr" in metrics
    assert "pbo" in metrics
    assert metrics["tradeCount"] == 4


def test_build_research_metrics_marks_runtime_heavy_pbo_path():
    metrics = build_research_metrics(
        [0.3, 0.2, -0.1, 0.4],
        observed_sharpe=1.0,
    )

    assert "exact_cscv_pbo_not_run" in metrics["runtimeHeavy"]
