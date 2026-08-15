"""Artifact manifest tests."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import numpy as np

from athena_ase.data.ptis import dataset_hash_from_bytes
from athena_ase.registry.artifacts import (
    activate_artifact_version,
    freeze_artifact_bundle,
    load_artifact_bundle,
    load_manifest,
)
from athena_research.ase.train import select_expected_net_r_threshold, train_all


def test_dataset_hash_deterministic():
    data = b"same-bytes"
    assert dataset_hash_from_bytes(data) == dataset_hash_from_bytes(data)


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


def test_registry_loader_skips_hashed_non_pickle_monitor_reference(tmp_path: Path):
    out = freeze_artifact_bundle(
        family="forex",
        horizon="intraday",
        version="with-monitor",
        models={"model_core.pkl": {"route": "core"}},
        dataset_hash="dataset-sha256",
        monitor_reference={"ret_z_1": np.linspace(-1.0, 1.0, 20)},
        root=tmp_path,
    )

    manifest, models = load_artifact_bundle(
        "forex", "intraday", "with-monitor", root=tmp_path
    )

    assert "monitor_reference.npz" in manifest.file_hashes
    assert (out / "monitor_reference.npz").exists()
    assert models == {"model_core.pkl": {"route": "core"}}


def test_runtime_loader_honors_active_version_and_fails_closed_when_missing(
    tmp_path: Path, monkeypatch
):
    from athena_ase.artifacts.loader import _resolve_version_dir

    monkeypatch.setenv("ATHENA_ASE_MODELS_ROOT", str(tmp_path))
    (tmp_path / "forex" / "intraday" / "v1").mkdir(parents=True)
    (tmp_path / "forex" / "intraday" / "v2-integrity").mkdir()
    (tmp_path / "equity" / "intraday" / "v1").mkdir(parents=True)
    activate_artifact_version("v2-integrity", root=tmp_path)

    selected = _resolve_version_dir("forex", "intraday")
    assert selected is not None
    assert selected.name == "v2-integrity"
    assert _resolve_version_dir("equity", "intraday") is None


def test_runtime_loader_rejects_active_bundle_that_fails_provisional_gate(
    tmp_path: Path, monkeypatch
):
    from athena_ase.artifacts.loader import load_artifact_bundle as load_runtime_bundle

    monkeypatch.setenv("ATHENA_ASE_MODELS_ROOT", str(tmp_path))
    freeze_artifact_bundle(
        family="forex",
        horizon="intraday",
        version="v2-integrity",
        models={"model_core.pkl": {"route": "core"}},
        dataset_hash="dataset-sha256",
        validation_report={
            "metrics": {
                "holdout_gate_metrics": {
                    "oos_trades": 10,
                    "instruments": 4,
                    "folds_nonneg": 0,
                    "max_instrument_profit_share": 1.0,
                    "max_dd_R": 3.0,
                    "brier_skill": -0.1,
                }
            }
        },
        root=tmp_path,
    )
    activate_artifact_version("v2-integrity", root=tmp_path)

    assert load_runtime_bundle("forex", "intraday") is None


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
