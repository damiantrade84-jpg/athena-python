"""Promotion requirements (ASE Phase 4)."""

from __future__ import annotations

from typing import Any

from athena_ase.registry.holdout import HoldoutRegistry
from athena_ase.registry.promotion import get_deployment_state
from athena_ase.validation.gates import check_provisional


def promotion_requirements(
    *,
    family: str,
    horizon: str,
    metrics: dict[str, Any],
    holdout: HoldoutRegistry | None = None,
    artifact_version: str = "",
    artifact_hash: str = "",
) -> tuple[bool, list[str]]:
    reg = holdout or HoldoutRegistry.load()
    failures: list[str] = []
    if not reg.holdout_done(family, horizon):
        failures.append("holdout_not_evaluated")
    else:
        rec = reg.holdout[f"{family}:{horizon}"]
        if not rec.passed:
            failures.append("holdout_failed")
        expected_metrics = {
            key: value
            for key, value in rec.metrics.items()
            if key not in {"failures", "family", "horizon"}
        }
        if expected_metrics != metrics:
            failures.append("holdout_metrics_mismatch")
        if artifact_version and rec.artifact_version != artifact_version:
            failures.append("holdout_artifact_version_mismatch")
        if artifact_hash and rec.artifact_hash != artifact_hash.lower():
            failures.append("holdout_artifact_hash_mismatch")
    ok, gate_failures = check_provisional(metrics)
    failures.extend(gate_failures)
    if get_deployment_state(family, horizon) == "DEMO":
        failures.append("already_promoted")
    return (not failures and ok, failures)
