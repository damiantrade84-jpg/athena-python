"""Holdout evaluation (ASE Phase 4 T4.1)."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from athena_ase.exceptions import HoldoutAlreadyEvaluated
from athena_ase.paths import ase_audit_log_path
from athena_ase.registry.holdout import HoldoutRegistry
from athena_ase.validation.gates import check_provisional


def run_holdout_eval(
    *,
    family: str,
    horizon: str,
    metrics: dict[str, Any],
    operator: str = "cli",
    registry: HoldoutRegistry | None = None,
    registry_path: Path | None = None,
    save: bool = True,
    artifact_version: str = "",
    artifact_hash: str = "",
) -> dict[str, Any]:
    reg = registry or HoldoutRegistry.load(registry_path)
    passed, failures = check_provisional(metrics)
    key = reg._key(family, horizon)
    identity_bound = False
    if key in reg.holdout:
        rec = reg.holdout[key]
        stored_metrics = {
            name: value for name, value in rec.metrics.items() if name != "failures"
        }
        can_bind_legacy = bool(
            rec.passed
            and passed
            and not rec.artifact_version
            and not rec.artifact_hash
            and artifact_version
            and artifact_hash
            and stored_metrics == metrics
        )
        if not can_bind_legacy:
            raise HoldoutAlreadyEvaluated(family, horizon)
        rec.artifact_version = artifact_version
        rec.artifact_hash = artifact_hash.lower()
        identity_bound = True
    else:
        rec = reg.record_holdout_eval(
            family=family,
            horizon=horizon,
            passed=passed,
            metrics={**metrics, "failures": failures},
            operator=operator,
            artifact_version=artifact_version,
            artifact_hash=artifact_hash,
        )
    if save:
        reg.save(registry_path)
        path = ase_audit_log_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        row = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "action": "holdout_bind_artifact" if identity_bound else "holdout_eval",
            "family": family,
            "operator": operator,
            "detail": {
                "horizon": horizon,
                "passed": passed,
                "metrics": metrics,
                "artifact_version": artifact_version,
                "artifact_hash": artifact_hash,
            },
        }
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(row, sort_keys=True) + "\n")
    return {
        "family": family,
        "horizon": horizon,
        "passed": passed,
        "failures": failures,
        "evaluated_at": rec.evaluated_at,
        "identity_bound": identity_bound,
    }
