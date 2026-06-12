"""Artifact manifest, freeze, and load with hash verification (ASE v2.1 §15)."""

from __future__ import annotations

import hashlib
import json
import os
import pickle
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import sklearn

from athena_ase.features.build import FEATURE_SCHEMA_CORE, FEATURE_SCHEMA_ENRICHED, schema_hash
from athena_research.ase.trials_registry import registry_hash


def default_artifacts_root() -> Path:
    override = os.environ.get("ATHENA_ASE_MODELS_ROOT", "").strip()
    if override:
        return Path(override)
    local = os.environ.get("LOCALAPPDATA", "").strip()
    if not local:
        local = str(Path.home() / "AppData" / "Local")
    return Path(local) / "Athena" / "models" / "ase"


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _sha256_file(path: Path) -> str:
    return _sha256_bytes(path.read_bytes())


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class ArtifactManifest:
    family: str
    horizon: str
    version: str
    feature_schema_hash: str
    feature_schema: list[str]
    dataset_hash: str = ""
    ptis_snapshot_id: str = ""
    cost_model_version: str = "cm-2026.06.0"
    label_params: dict[str, float | int] = field(
        default_factory=lambda: {"k_sl": 1.0, "k_tp": 1.0, "H_intraday": 16, "H_swing": 10}
    )
    thr_family: float = 0.10
    trials_registry_hash: str = ""
    sklearn_version: str = field(default_factory=lambda: sklearn.__version__)
    trained_at: str = field(default_factory=_now_iso)
    adapters: list[str] = field(default_factory=list)
    file_hashes: dict[str, str] = field(default_factory=dict)
    validation_report: dict[str, Any] = field(default_factory=dict)
    signer: str = ""


def artifact_dir(
    family: str,
    horizon: str,
    version: str,
    *,
    root: Path | None = None,
) -> Path:
    base = root or default_artifacts_root()
    return base / family / horizon / version


def write_manifest(manifest: ArtifactManifest, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(asdict(manifest), indent=2, sort_keys=True), encoding="utf-8")


def load_manifest(path: Path) -> ArtifactManifest:
    data = json.loads(path.read_text(encoding="utf-8"))
    return ArtifactManifest(**data)


def freeze_artifact_bundle(
    *,
    family: str,
    horizon: str,
    version: str,
    models: dict[str, Any],
    route: str = "core",
    dataset_hash: str = "",
    ptis_snapshot_id: str = "",
    thr_family: float = 0.10,
    validation_report: dict[str, Any] | None = None,
    adapters: list[str] | None = None,
    root: Path | None = None,
) -> Path:
    """Persist model bundle + manifest with content hashes."""
    out_dir = artifact_dir(family, horizon, version, root=root)
    out_dir.mkdir(parents=True, exist_ok=True)

    schema = FEATURE_SCHEMA_ENRICHED if route == "enriched" else FEATURE_SCHEMA_CORE
    file_hashes: dict[str, str] = {}
    for name, obj in models.items():
        fpath = out_dir / name
        with fpath.open("wb") as fh:
            pickle.dump(obj, fh, protocol=pickle.HIGHEST_PROTOCOL)
        file_hashes[name] = _sha256_file(fpath)

    manifest = ArtifactManifest(
        family=family,
        horizon=horizon,
        version=version,
        feature_schema_hash=schema_hash(schema),
        feature_schema=list(schema),
        dataset_hash=dataset_hash,
        ptis_snapshot_id=ptis_snapshot_id,
        thr_family=thr_family,
        trials_registry_hash=registry_hash(),
        validation_report=validation_report or {},
        adapters=list(adapters or []),
        file_hashes=file_hashes,
    )
    write_manifest(manifest, out_dir / "manifest.json")
    return out_dir


def verify_manifest(manifest: ArtifactManifest, artifact_root: Path) -> list[str]:
    errors: list[str] = []
    expected_schema_hash = schema_hash(manifest.feature_schema)
    if manifest.feature_schema_hash != expected_schema_hash:
        errors.append("feature_schema_hash mismatch")
    for fname, expected in manifest.file_hashes.items():
        fpath = artifact_root / fname
        if not fpath.exists():
            errors.append(f"missing artifact file: {fname}")
            continue
        actual = _sha256_file(fpath)
        if actual != expected:
            errors.append(f"hash mismatch: {fname}")
    return errors


def load_artifact_bundle(
    family: str,
    horizon: str,
    version: str,
    *,
    root: Path | None = None,
    verify: bool = True,
) -> tuple[ArtifactManifest, dict[str, Any]]:
    out_dir = artifact_dir(family, horizon, version, root=root)
    manifest_path = out_dir / "manifest.json"
    if not manifest_path.exists():
        raise FileNotFoundError(f"manifest not found: {manifest_path}")
    manifest = load_manifest(manifest_path)
    if verify:
        errors = verify_manifest(manifest, out_dir)
        if errors:
            raise ValueError("; ".join(errors))

    models: dict[str, Any] = {}
    for fname in manifest.file_hashes:
        with (out_dir / fname).open("rb") as fh:
            models[fname] = pickle.load(fh)
    return manifest, models
