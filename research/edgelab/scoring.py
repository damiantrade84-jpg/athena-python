"""EdgeLab scoring with data-quality penalty."""

from __future__ import annotations

from typing import Any

from research.autoresearch.metrics import (
    evaluate_acceptance,
    normalize_metrics,
    parse_command_output,
)


def compute_score(metrics: dict[str, float], config: dict[str, Any]) -> float:
    weights = config.get("scoring_weights") or {}
    exp_r = metrics.get("expR", 0.0)
    pf = metrics.get("profit_factor", 0.0)
    sqn = metrics.get("sqn", 0.0)
    max_dd = metrics.get("max_drawdown_R", 0.0)
    trade_count = int(metrics.get("trade_count", 0))

    pf_cap = float(weights.get("profit_factor_cap", 3.0))
    pf_mult = float(weights.get("profit_factor_mult", 15))
    sqn_cap = float(weights.get("sqn_cap", 4.0))
    sqn_mult = float(weights.get("sqn_mult", 10))
    exp_mult = float(weights.get("expR", 40))
    dd_mult = float(weights.get("max_drawdown_mult", 8))

    trade_count_score = min(trade_count / 100.0, 1.0) * 5.0
    oos_score = metrics.get("oos_score", 0.0) * 10.0
    stability_score = metrics.get("stability_score", 0.0) * 5.0

    max_conc = float(config.get("max_symbol_concentration", 0.60))
    concentration = metrics.get("symbol_concentration", 0.0)
    concentration_penalty = max(0.0, concentration - max_conc) * 20.0

    cost_penalty = metrics.get("cost_sensitivity_penalty", 0.0)
    data_quality_penalty = metrics.get("data_quality_penalty", 0.0)

    score = (
        exp_r * exp_mult
        + min(pf, pf_cap) * pf_mult
        + min(sqn, sqn_cap) * sqn_mult
        - max_dd * dd_mult
        + trade_count_score
        + oos_score
        + stability_score
        - concentration_penalty
        - cost_penalty
        - data_quality_penalty
    )
    return round(score, 4)


def evaluate_candidate(
    *,
    baseline_metrics: dict[str, float],
    candidate_metrics: dict[str, float],
    baseline_score: float,
    candidate_score: float,
    config: dict[str, Any],
    safety_ok: bool,
    safety_reasons: list[str],
    tests_passed: bool,
    evaluation_ok: bool,
    metrics_found: bool,
    data_freshness_ok: bool,
    symbol_specific: bool = False,
) -> tuple[bool, list[str]]:
    reasons: list[str] = []
    if not metrics_found:
        reasons.append("no_metrics_found")
    if not safety_ok:
        reasons.extend(safety_reasons)
    if config.get("require_tests_pass", True) and not tests_passed:
        reasons.append("tests_failed")
    if not evaluation_ok:
        reasons.append("evaluation_command_failed")
    if not data_freshness_ok:
        reasons.append("data_freshness_failed")

    min_delta = float(config.get("min_score_delta", 0.05))
    if candidate_score <= baseline_score + min_delta:
        reasons.append("score_delta_insufficient")

    min_trades = int(config.get("min_trade_count", 30))
    if candidate_metrics.get("trade_count", 0) < min_trades:
        reasons.append("low_trade_count")

    max_dd = float(config.get("max_drawdown_R", 3.0))
    if candidate_metrics.get("max_drawdown_R", 0.0) > max_dd:
        reasons.append("excessive_drawdown")

    if config.get("require_oos_not_worse", True):
        base_oos = baseline_metrics.get("oos_score", baseline_score)
        cand_oos = candidate_metrics.get("oos_score", candidate_score)
        if cand_oos < base_oos:
            reasons.append("oos_worse_than_baseline")

    max_conc = float(config.get("max_symbol_concentration", 0.60))
    conc = candidate_metrics.get("symbol_concentration", 0.0)
    if conc > max_conc and not symbol_specific:
        reasons.append("symbol_concentration_too_high")

    return len(reasons) == 0, reasons


__all__ = [
    "compute_score",
    "evaluate_candidate",
    "normalize_metrics",
    "parse_command_output",
]
