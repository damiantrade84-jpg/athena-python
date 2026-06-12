"""Promotion requirements (ASE Phase 4)."""

from __future__ import annotations

from typing import Any

from athena_ase.registry.holdout import HoldoutRegistry
from athena_ase.registry.promotion import get_family_state
from athena_ase.validation.gates import check_provisional


def promotion_requirements(
    *,
    family: str,
    horizon: str,
    metrics: dict[str, Any],
    holdout: HoldoutRegistry | None = None,
) -> tuple[bool, list[str]]:
    reg = holdout or HoldoutRegistry.load()
    failures: list[str] = []
    if not reg.holdout_done(family, horizon):
        failures.append("holdout_not_evaluated")
    else:
        rec = reg.holdout[f"{family}:{horizon}"]
        if not rec.passed:
            failures.append("holdout_failed")
    ok, gate_failures = check_provisional(metrics)
    failures.extend(gate_failures)
    if get_family_state(family) == "DEMO":
        failures.append("already_promoted")
    return (not failures and ok, failures)
