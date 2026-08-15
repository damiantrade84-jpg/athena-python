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

ACTIVE_VERSION_FILE = "ACTIVE_VERSION"
# Per-route list of feature columns that were entirely NaN at train time (and
# therefore 0.0-filled by _prepare_matrix). Stored as a hash-covered sidecar
# file rather than a manifest field so adding it does not change
# artifact_content_hash for already-frozen/promoted artifacts.
ZERO_FILL_FILE = "zero_fill_features.json"


def default_artifacts_root() -> Path:
    override = os.environ.get("ATHENA_ASE_MODELS_ROOT", "").strip()
    if override:
        return Path(override)
    local = os.environ.get("LOCALAPPDATA", "").strip()
    if not local:
        local = str(Path.home() / "AppData" / "Local")
    return Path(local) / "Athena" / "models" / "ase"


def active_artifact_version(*, root: Path | None = None) -> str:
    marker = (root or default_artifacts_root()) / ACTIVE_VERSION_FILE
    if not marker.exists():
        return ""
    try:
        return marker.read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def activate_artifact_version(version: str, *, root: Path | None = None) -> Path:
    """Atomically pin runtime loading to one train-all artifact version."""
    clean = str(version or "").strip()
    if not clean or Path(clean).name != clean or "/" in clean or "\\" in clean:
        raise ValueError(f"invalid artifact version: {version!r}")
    artifacts_root = root or default_artifacts_root()
    artifacts_root.mkdir(parents=True, exist_ok=True)
    marker = artifacts_root / ACTIVE_VERSION_FILE
    pending = artifacts_root / f".{ACTIVE_VERSION_FILE}.tmp"
    pending.write_text(clean + "\n", encoding="utf-8")
    pending.replace(marker)
    return marker


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _sha256_file(path: Path) -> str:
    return _sha256_bytes(path.read_bytes())


def artifact_content_hash(manifest: ArtifactManifest) -> str:
    """Stable identity for the exact manifest and hashed artifact contents."""
    payload = json.dumps(asdict(manifest), sort_keys=True, separators=(",", ":"))
    return _sha256_bytes(payload.encode("utf-8"))


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class ArtifactManifest:
    family: str
    horizon: str
    version: str
    feature_schema_hash: str
    feature_schema: list[str]
    feature_schema_hashes: dict[str, str] = field(default_factory=dict)
    feature_schemas: dict[str, list[str]] = field(default_factory=dict)
    dataset_hash: str = ""
    ptis_snapshot_id: str = ""
    cost_model_version: str = "cm-2026.06.0"
    label_params: dict[str, float | int] = field(
        default_factory=lambda: {"k_sl": 1.0, "k_tp": 1.0, "H_intraday": 16, "H_swing": 10}
    )
    thr_family: float = 0.10
    threshold_fallback: bool = False
    eval_summary: dict[str, Any] = field(default_factory=dict)
    model_params: dict[str, Any] = field(default_factory=dict)
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
    threshold_fallback: bool = False,
    eval_summary: dict[str, Any] | None = None,
    model_params: dict[str, Any] | None = None,
    adapters: list[str] | None = None,
    monitor_reference: dict[str, Any] | None = None,
    zero_fill_features: dict[str, list[str]] | None = None,
    root: Path | None = None,
) -> Path:
    """Persist model bundle + manifest with content hashes."""
    out_dir = artifact_dir(family, horizon, version, root=root)
    out_dir.mkdir(parents=True, exist_ok=True)

    from athena_ase.features.build import active_feature_schema

    schema = active_feature_schema(enriched=(route == "enriched"))
    feature_schemas = {"core": list(active_feature_schema(enriched=False))}
    if "model_enriched.pkl" in models:
        feature_schemas["enriched"] = list(active_feature_schema(enriched=True))
    feature_schema_hashes = {
        name: schema_hash(names)
        for name, names in feature_schemas.items()
    }

    for stale_path in out_dir.glob("*.pkl"):
        if stale_path.name not in models:
            stale_path.unlink()

    file_hashes: dict[str, str] = {}
    for name, obj in models.items():
        fpath = out_dir / name
        with fpath.open("wb") as fh:
            pickle.dump(obj, fh, protocol=pickle.HIGHEST_PROTOCOL)
        file_hashes[name] = _sha256_file(fpath)

    from athena_ase.artifacts.monitor_ref import MONITOR_REFERENCE_FILE, save_monitor_reference

    if monitor_reference is not None:
        ref_path = out_dir / MONITOR_REFERENCE_FILE
        file_hashes[MONITOR_REFERENCE_FILE] = save_monitor_reference(ref_path, monitor_reference)

    zf_path = out_dir / ZERO_FILL_FILE
    if zero_fill_features is not None:
        zf_path.write_text(
            json.dumps(zero_fill_features, indent=2, sort_keys=True), encoding="utf-8"
        )
        file_hashes[ZERO_FILL_FILE] = _sha256_file(zf_path)
    elif zf_path.exists():
        # Re-freeze without zero-fill metadata must not leave a stale sidecar.
        zf_path.unlink()

    manifest = ArtifactManifest(
        family=family,
        horizon=horizon,
        version=version,
        feature_schema_hash=schema_hash(schema),
        feature_schema=list(schema),
        feature_schema_hashes=feature_schema_hashes,
        feature_schemas=feature_schemas,
        dataset_hash=dataset_hash,
        ptis_snapshot_id=ptis_snapshot_id,
        thr_family=thr_family,
        threshold_fallback=threshold_fallback,
        eval_summary=eval_summary or {},
        model_params=model_params or {},
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
    for route, route_schema in manifest.feature_schemas.items():
        expected = schema_hash(route_schema)
        if manifest.feature_schema_hashes.get(route) != expected:
            errors.append(f"feature_schema_hash mismatch: {route}")
    if not manifest.dataset_hash:
        errors.append("missing dataset_hash")
    persisted_model_files = {path.name for path in artifact_root.glob("*.pkl")}
    model_hashes = {k: v for k, v in manifest.file_hashes.items() if k.endswith(".pkl")}
    if set(model_hashes) != persisted_model_files:
        errors.append("file_hashes do not cover all model files")
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
        if not fname.endswith(".pkl"):
            continue
        with (out_dir / fname).open("rb") as fh:
            models[fname] = pickle.load(fh)
    return manifest, models
