"""ASE model artifact loading — wraps registry/artifacts with version resolution."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np

from athena_ase.horizon import Horizon
from athena_ase.artifacts.monitor_ref import MONITOR_REFERENCE_FILE, load_monitor_reference
from athena_ase.registry.artifacts import (
    ArtifactManifest,
    active_artifact_version,
    artifact_content_hash,
    default_artifacts_root,
    load_artifact_bundle as _load_bundle,
    load_manifest,
    verify_manifest,
)
from athena_ase.registry.promotion import get_deployment_binding
from athena_ase.validation.gates import check_provisional

log = logging.getLogger("ase.artifacts")


@dataclass(frozen=True)
class ArtifactBundle:
    family: str
    horizon: Horizon
    version: str
    root: Path
    manifest: ArtifactManifest
    model_core: Any | None
    model_enriched: Any | None
    calibrator: Any | None
    calibrator_enriched: Any | None
    quantile_heads: dict[str, Any] | None
    quantile_heads_enriched: dict[str, Any] | None
    thr_family: float
    feature_names: tuple[str, ...]
    monitor_reference: dict[str, np.ndarray] | None = None

    @property
    def artifact_hash(self) -> str:
        return artifact_content_hash(self.manifest)


def _resolve_version_dir(family: str, horizon: Horizon, version: str | None = None) -> Path | None:
    base = default_artifacts_root() / family / horizon
    if not base.exists():
        return None
    if version is None:
        binding = get_deployment_binding(family, horizon)
        version = binding.version if binding is not None else active_artifact_version() or None
    if version:
        candidate = base / version
        return candidate if candidate.is_dir() else None
    dirs = sorted([p for p in base.iterdir() if p.is_dir()], key=lambda p: p.name, reverse=True)
    return dirs[0] if dirs else None


def load_artifact_bundle(
    family: str,
    horizon: Horizon,
    *,
    version: str | None = None,
) -> ArtifactBundle | None:
    runtime_load = version is None
    root = _resolve_version_dir(family, horizon, version)
    if root is None:
        return None
    if runtime_load:
        try:
            preview = load_manifest(root / "manifest.json")
        except (FileNotFoundError, TypeError, ValueError) as exc:
            log.warning("ASE artifact manifest read failed for %s/%s: %s", family, horizon, exc)
            return None
        metrics = (preview.validation_report or {}).get("metrics") or {}
        holdout_metrics = metrics.get("holdout_gate_metrics") or {}
        promoted, failures = check_provisional(holdout_metrics)
        if not promoted:
            log.warning(
                "ASE artifact remains research-only for %s/%s: %s",
                family,
                horizon,
                ",".join(failures) or "provisional_gate_missing",
            )
            return None
        binding = get_deployment_binding(family, horizon)
        if binding is not None and (
            binding.version != root.name
            or binding.artifact_hash != artifact_content_hash(preview)
        ):
            log.warning(
                "ASE promoted artifact binding mismatch for %s/%s",
                family,
                horizon,
            )
            return None
    try:
        manifest, models = _load_bundle(family, horizon, root.name, verify=True)
    except (FileNotFoundError, ValueError) as exc:
        log.warning("ASE artifact load failed for %s/%s: %s", family, horizon, exc)
        return None

    return ArtifactBundle(
        family=family,
        horizon=horizon,
        version=root.name,
        root=root,
        manifest=manifest,
        model_core=models.get("model_core.pkl"),
        model_enriched=models.get("model_enriched.pkl"),
        calibrator=models.get("calibrator.pkl"),
        calibrator_enriched=models.get("calibrator_enriched.pkl"),
        quantile_heads=models.get("quantile_heads.pkl"),
        quantile_heads_enriched=models.get("quantile_heads_enriched.pkl"),
        thr_family=float(manifest.thr_family),
        feature_names=tuple(manifest.feature_schema),
        monitor_reference=load_monitor_reference(root / MONITOR_REFERENCE_FILE) or None,
    )


def verify_manifest_hashes(root: Path, manifest: ArtifactManifest | dict[str, Any]) -> list[str]:
    if isinstance(manifest, dict):
        manifest = ArtifactManifest(**manifest)
    return verify_manifest(manifest, root)
