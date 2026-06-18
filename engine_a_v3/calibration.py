from __future__ import annotations

from statistics import median
from typing import Any, Iterable


def _quantile(values: list[float], percentile: int) -> float:
    ordered = sorted(float(value) for value in values)
    if not ordered:
        raise ValueError("calibration_scores_empty")
    position = (len(ordered) - 1) * percentile / 100.0
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    fraction = position - lower
    return ordered[lower] + (ordered[upper] - ordered[lower]) * fraction


def candidate_parameter_grid(development_scores: Iterable[float]) -> list[dict[str, Any]]:
    scores = [float(value) for value in development_scores]
    thresholds = sorted({round(_quantile(scores, q), 6) for q in (60, 65, 70, 75, 80, 85, 90)})
    weights = []
    for trend_i in range(1, 7):
        for momentum_i in range(1, 7):
            for location_i in range(1, 7):
                for volume_i in range(0, 4):
                    if trend_i + momentum_i + location_i + volume_i == 10:
                        weights.append(
                            {
                                "trend": trend_i / 10.0,
                                "momentum": momentum_i / 10.0,
                                "location": location_i / 10.0,
                                "volume": volume_i / 10.0,
                            }
                        )
    return [
        {
            "weights": weight,
            "directionDeadband": deadband,
            "tradeThreshold": threshold,
            "exitPolicy": exit_policy,
        }
        for weight in weights
        for deadband in (0.05, 0.10, 0.15)
        for threshold in thresholds
        for exit_policy in ("SINGLE_TP1", "SPLIT_50_50")
    ]


def select_development_candidate(evaluations: Iterable[dict[str, Any]]) -> dict[str, Any]:
    eligible = []
    for item in evaluations:
        if int(item.get("positiveFolds") or 0) < 3:
            continue
        if int(item.get("oosTrades") or 0) < 60:
            continue
        if float(item.get("medianFoldExpectancyR") or 0) < 0.08:
            continue
        if float(item.get("maxDrawdownR") or float("inf")) > 12:
            continue
        if "profitFactor" in item and float(item.get("profitFactor") or 0) < 1.2:
            continue
        if "sqn" in item and float(item.get("sqn") or 0) < 1.5:
            continue
        if "bootstrapLower95ExpectancyR" in item and float(item.get("bootstrapLower95ExpectancyR") or 0) <= 0:
            continue
        eligible.append(item)
    if not eligible:
        raise ValueError("calibration_no_development_candidate")
    return max(
        eligible,
        key=lambda item: (
            float(item["medianFoldExpectancyR"]),
            -float(item["maxDrawdownR"]),
            int(item["oosTrades"]),
        ),
    )


def validate_holdout(metrics: dict[str, Any]) -> bool:
    return bool(
        int(metrics.get("holdoutTrades") or 0) >= 15
        and float(metrics.get("holdoutExpectancyR") or 0) > 0
    )


def fold_selection_metrics(fold_results: list[list[float]], summary: dict[str, Any]) -> dict[str, Any]:
    fold_expectancies = [sum(values) / len(values) if values else 0.0 for values in fold_results]
    return {
        **summary,
        "medianFoldExpectancyR": median(fold_expectancies),
        "positiveFolds": sum(value > 0 for value in fold_expectancies),
    }
