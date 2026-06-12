"""Artifact manifest tests."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from athena_ase.artifacts.manifest import ArtifactError, dataset_hash_from_bytes, verify_manifest


def test_dataset_hash_deterministic():
    data = b"same-bytes"
    assert dataset_hash_from_bytes(data) == dataset_hash_from_bytes(data)


def test_schema_mismatch_raises(tmp_path: Path):
    manifest: dict = {}
    with pytest.raises(ArtifactError):
        verify_manifest(manifest, tmp_path)
