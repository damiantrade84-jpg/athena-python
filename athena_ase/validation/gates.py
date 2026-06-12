"""Evidence ladder constants (ASE v2.1 §11)."""

from __future__ import annotations

from typing import Any

PROVISIONAL = {
    "min_oos_trades": 40,
    "min_instruments": 4,
    "folds_nonneg": 3,
    "max_instrument_profit_share": 0.40,
    "max_dd_R": 12,
    "cost_stress_ok_at": 1.5,
    "brier_skill_min": 0.0,
}

VALIDATED = {
    "min_oos_trades": 150,
    "bootstrap_lb_expectancy_gt": 0.0,
    "dsr_min": 0.95,
    "pbo_max": 0.20,
    "cost_stress_ok_at": 2.0,
    "reliability_monotone_tol": 0.05,
}


def check_provisional(metrics: dict[str, Any]) -> tuple[bool, list[str]]:
    failures: list[str] = []
    if int(metrics.get("oos_trades", 0)) < PROVISIONAL["min_oos_trades"]:
        failures.append("oos_trades")
    if int(metrics.get("instruments", 0)) < PROVISIONAL["min_instruments"]:
        failures.append("instruments")
    if int(metrics.get("folds_nonneg", 0)) < PROVISIONAL["folds_nonneg"]:
        failures.append("folds_nonneg")
    if float(metrics.get("max_instrument_profit_share", 1.0)) > PROVISIONAL["max_instrument_profit_share"]:
        failures.append("concentration")
    if float(metrics.get("max_dd_R", 999.0)) > PROVISIONAL["max_dd_R"]:
        failures.append("max_dd_R")
    if float(metrics.get("brier_skill", -1.0)) < PROVISIONAL["brier_skill_min"]:
        failures.append("brier_skill")
    return (not failures, failures)
