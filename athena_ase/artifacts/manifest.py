"""ASE artifact manifest verification (v2.1 §15)."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any


class ArtifactError(Exception):
    """Artifact integrity or schema failure."""


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def load_manifest(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def verify_manifest(manifest: dict[str, Any], artifact_dir: Path) -> None:
    schema_hash = manifest.get("feature_schema_hash")
    if not schema_hash:
        raise ArtifactError("missing feature_schema_hash")
    for key in ("model_core", "calibrator"):
        rel = manifest.get(key)
        if not rel:
            continue
        p = artifact_dir / rel
        if not p.exists():
            raise ArtifactError(f"missing artifact file: {rel}")
        recorded = (manifest.get("hashes") or {}).get(key)
        if recorded and sha256_file(p) != recorded:
            raise ArtifactError(f"hash mismatch for {key}")


def dataset_hash_from_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()
