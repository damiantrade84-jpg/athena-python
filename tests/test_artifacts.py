"""Artifact manifest tests."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from athena_ase.artifacts.manifest import ArtifactError, dataset_hash_from_bytes, verify_manifest
from athena_ase.registry.artifacts import (
    freeze_artifact_bundle,
    load_artifact_bundle,
    load_manifest,
)
from athena_research.ase.train import select_expected_net_r_threshold, train_all


def test_dataset_hash_deterministic():
    data = b"same-bytes"
    assert dataset_hash_from_bytes(data) == dataset_hash_from_bytes(data)


def test_schema_mismatch_raises(tmp_path: Path):
    manifest: dict = {}
    with pytest.raises(ArtifactError):
        verify_manifest(manifest, tmp_path)


def test_registry_manifest_hashes_every_model_and_rejects_tampering(tmp_path: Path):
    out = freeze_artifact_bundle(
        family="forex",
        horizon="intraday",
        version="fixture",
        models={
            "model_core.pkl": {"route": "core"},
            "model_enriched.pkl": {"route": "enriched"},
            "calibrator.pkl": {"kind": "isotonic"},
            "quantile_heads.pkl": {"net_R": {}},
        },
        dataset_hash="dataset-sha256",
        thr_family=0.17,
        threshold_fallback=False,
        eval_summary={"trade_count": 40, "expectancy": 0.12},
        root=tmp_path,
    )
    manifest = load_manifest(out / "manifest.json")

    assert set(manifest.file_hashes) == {
        "model_core.pkl",
        "model_enriched.pkl",
        "calibrator.pkl",
        "quantile_heads.pkl",
    }
    assert manifest.dataset_hash == "dataset-sha256"
    assert manifest.threshold_fallback is False
    assert manifest.eval_summary["trade_count"] == 40
    assert set(manifest.feature_schema_hashes) == {"core", "enriched"}

    (out / "model_core.pkl").write_bytes(b"tampered")
    with pytest.raises(ValueError, match="hash mismatch: model_core.pkl"):
        load_artifact_bundle("forex", "intraday", "fixture", root=tmp_path)


def test_threshold_defaults_and_flags_when_eval_has_fewer_than_40_rows():
    result = select_expected_net_r_threshold(
        expected_net_r=[0.3] * 39,
        realized_net_r=[0.2] * 39,
    )

    assert result.threshold == 0.10
    assert result.fallback is True
    assert result.retained_trades == 39


def test_train_all_continues_after_one_family_failure(tmp_path: Path):
    calls: list[tuple[str, str]] = []

    def fake_train(family: str, horizon: str, **kwargs):
        calls.append((family, horizon))
        if family == "crypto" and horizon == "intraday":
            raise ValueError("insufficient candidates")
        return {
            "family": family,
            "horizon": horizon,
            "metrics": {"eval_expectancy": 0.1, "eval_trades": 40},
            "artifact_path": f"{family}/{horizon}",
        }

    result = train_all(
        version="fixture",
        report_path=tmp_path / "ase_training_report.md",
        trainer=fake_train,
    )

    assert len(calls) == 10
    assert len(result["trained"]) == 9
    assert result["failed"] == [
        {
            "family": "crypto",
            "horizon": "intraday",
            "reason": "insufficient candidates",
        }
    ]
    assert (tmp_path / "ase_training_report.md").exists()
