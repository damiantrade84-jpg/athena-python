"""Evidence ladder constants and checks (ASE v2.1 §11).

Both tiers are enforced here. Every threshold declared in ``PROVISIONAL`` and
``VALIDATED`` has a corresponding check below; a constant with no check is a
gate that silently passes, which is worse than having no gate at all.

Missing, non-numeric, and NaN metrics fail closed. NaN matters specifically:
the statistics helpers return NaN on short or degenerate series, and a bare
``nan > limit`` comparison is ``False``, which would read as "passed".
"""

from __future__ import annotations

import math
from typing import Any

PROVISIONAL = {
    "min_oos_trades": 40,
    "min_instruments": 4,
    "folds_nonneg": 3,
    "max_instrument_profit_share": 0.40,
    "max_dd_R": 12,
    "brier_skill_min": 0.0,
}
# Cost stress is deliberately absent here and enforced only at VALIDATED.
# PROVISIONAL is the research-usable tier and is applied to already-frozen
# artifacts at load time; requiring a metric that did not exist when an
# artifact was frozen retires it retroactively rather than judging it.
# VALIDATED is the promotion tier, where surviving 2x costs is the point.

VALIDATED = {
    "min_oos_trades": 150,
    "bootstrap_lb_expectancy_gt": 0.0,
    "dsr_min": 0.95,
    "pbo_max": 0.20,
    "cost_stress_ok_at": 2.0,
    "reliability_monotone_tol": 0.05,
}


def cost_stress_metric_key(multiple: float) -> str:
    """Metric name holding expectancy (R) recomputed at ``multiple`` x costs."""
    return "cost_stress_expectancy_" + f"{float(multiple):.1f}".replace(".", "_")


def _finite(metrics: dict[str, Any], key: str) -> float | None:
    """Finite float for ``key``, or None when absent, non-numeric, or NaN."""
    try:
        value = float(metrics[key])
    except (KeyError, TypeError, ValueError):
        return None
    return value if math.isfinite(value) else None


def check_provisional(metrics: dict[str, Any]) -> tuple[bool, list[str]]:
    failures: list[str] = []

    oos_trades = _finite(metrics, "oos_trades")
    if oos_trades is None or oos_trades < PROVISIONAL["min_oos_trades"]:
        failures.append("oos_trades")

    instruments = _finite(metrics, "instruments")
    if instruments is None or instruments < PROVISIONAL["min_instruments"]:
        failures.append("instruments")

    folds_nonneg = _finite(metrics, "folds_nonneg")
    if folds_nonneg is None or folds_nonneg < PROVISIONAL["folds_nonneg"]:
        failures.append("folds_nonneg")

    concentration = _finite(metrics, "max_instrument_profit_share")
    if concentration is None or concentration > PROVISIONAL["max_instrument_profit_share"]:
        failures.append("concentration")

    max_dd = _finite(metrics, "max_dd_R")
    if max_dd is None or max_dd > PROVISIONAL["max_dd_R"]:
        failures.append("max_dd_R")

    brier_skill = _finite(metrics, "brier_skill")
    if brier_skill is None or brier_skill < PROVISIONAL["brier_skill_min"]:
        failures.append("brier_skill")

    return (not failures, failures)


def check_validated(metrics: dict[str, Any]) -> tuple[bool, list[str]]:
    """Top evidence tier: everything PROVISIONAL requires, plus §11 statistics.

    ``pbo`` has no producer yet — ``cscv_pbo`` needs a multi-configuration
    performance matrix that the trials registry does not retain. Until one
    exists the metric is absent and this tier fails closed on it, which is the
    honest state: no family has cleared VALIDATED.
    """
    failures: list[str] = []

    provisional_ok, provisional_failures = check_provisional(metrics)
    if not provisional_ok:
        failures.extend(f"provisional:{name}" for name in provisional_failures)

    oos_trades = _finite(metrics, "oos_trades")
    if oos_trades is None or oos_trades < VALIDATED["min_oos_trades"]:
        failures.append("oos_trades")

    bootstrap_lb = _finite(metrics, "bootstrap_lb_expectancy")
    if bootstrap_lb is None or bootstrap_lb <= VALIDATED["bootstrap_lb_expectancy_gt"]:
        failures.append("bootstrap_lb_expectancy")

    # Probability form (Bailey-Lopez de Prado), not the raw z-score: the 0.95
    # threshold is a confidence level. train.py emits both.
    dsr_prob = _finite(metrics, "dsr_prob")
    if dsr_prob is None or dsr_prob < VALIDATED["dsr_min"]:
        failures.append("dsr")

    pbo = _finite(metrics, "pbo")
    if pbo is None or pbo > VALIDATED["pbo_max"]:
        failures.append("pbo")

    stress_key = cost_stress_metric_key(VALIDATED["cost_stress_ok_at"])
    cost_stress = _finite(metrics, stress_key)
    if cost_stress is None or cost_stress <= 0.0:
        failures.append("cost_stress")

    deviation = _finite(metrics, "reliability_max_drop")
    if deviation is None or deviation > VALIDATED["reliability_monotone_tol"]:
        failures.append("reliability_monotone")

    return (not failures, failures)
