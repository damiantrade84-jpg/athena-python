"""Empirical threshold tooling for FX factor decisions (Phase 13)."""
from __future__ import annotations

import logging
import statistics
from typing import Any, Sequence

logger = logging.getLogger("athena_fx.threshold_tooling")


def score_distribution(
    decisions_or_scores: list[dict],
    *,
    key: str = "total_score",
    percentiles: Sequence[int] = (50, 60, 70, 80, 90),
) -> dict[str, Any]:
    """Empirical distribution of scores from decision dicts or score records."""
    values = [float(d[key]) for d in decisions_or_scores if d.get(key) is not None]
    if not values:
        return {
            "percentiles": {p: None for p in percentiles},
            "count": 0,
            "min": None,
            "max": None,
            "mean": None,
        }

    sorted_vals = sorted(values)
    n = len(sorted_vals)
    pct_map: dict[int, float] = {}
    for p in percentiles:
        if n == 1:
            pct_map[p] = sorted_vals[0]
            continue
        rank = (p / 100.0) * (n - 1)
        lo = int(rank)
        hi = min(lo + 1, n - 1)
        frac = rank - lo
        pct_map[p] = sorted_vals[lo] * (1 - frac) + sorted_vals[hi] * frac

    return {
        "percentiles": pct_map,
        "count": n,
        "min": min(values),
        "max": max(values),
        "mean": statistics.mean(values),
    }


def candidate_thresholds(distribution: dict) -> list[dict]:
    """One candidate threshold per percentile in the distribution."""
    percentiles = distribution.get("percentiles") or {}
    return [
        {"percentile": p, "threshold": v}
        for p, v in sorted(percentiles.items(), key=lambda x: x[0])
        if v is not None
    ]


def simulate_threshold(
    decisions: list[dict],
    threshold: float,
    *,
    key: str = "total_score",
) -> dict[str, Any]:
    """Simulate applying a score threshold to historical decisions."""
    kept: list[dict] = []
    rejected = 0
    for d in decisions:
        score = d.get(key)
        if score is None:
            rejected += 1
            continue
        if abs(float(score)) >= threshold:
            kept.append(d)
        else:
            rejected += 1

    total = len(decisions)
    outcomes = [d["outcome_r"] for d in kept if d.get("outcome_r") is not None]
    family_coverage: dict[str, int] = {}
    for d in kept:
        for fam in d.get("aligned_families") or []:
            family_coverage[fam] = family_coverage.get(fam, 0) + 1

    return {
        "threshold": threshold,
        "trade_count": len(kept),
        "feed_rate": len(kept) / total if total else 0.0,
        "expectancy_r": statistics.mean(outcomes) if outcomes else None,
        "rejected_bottom_tail": rejected,
        "family_coverage": family_coverage,
    }


def threshold_report(
    decisions: list[dict],
    *,
    percentiles: Sequence[int] = (50, 60, 70, 80, 90),
    key: str = "total_score",
) -> dict[str, Any]:
    """End-to-end distribution + per-candidate simulation report."""
    distribution = score_distribution(decisions, key=key, percentiles=percentiles)
    candidates = candidate_thresholds(distribution)
    simulations = [
        simulate_threshold(decisions, c["threshold"], key=key) for c in candidates
    ]
    return {
        "distribution": distribution,
        "candidates": candidates,
        "simulations": simulations,
    }
