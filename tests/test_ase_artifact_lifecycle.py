"""ASE artifact identity and activation remain manual and immutable."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import ase_cli
from athena_ase.artifacts import loader as runtime_loader
from athena_ase.registry import artifacts
from athena_ase.registry.holdout import HoldoutRegistry
from athena_ase.registry.promotion import get_deployment_binding, promote_family
from athena_research.ase import train as train_mod


PASSING_METRICS = {
    "oos_trades": 50,
    "instruments": 5,
    "folds_nonneg": 4,
    "max_instrument_profit_share": 0.30,
    "max_dd_R": 4.0,
    "brier_skill": 0.1,
}


def _freeze(root: Path, version: str, model_value: int, *, passing: bool = True):
    metrics = PASSING_METRICS if passing else {**PASSING_METRICS, "oos_trades": 1}
    return artifacts.freeze_artifact_bundle(
        family="forex",
        horizon="intraday",
        version=version,
        models={"model_core.pkl": {"value": model_value}},
        dataset_hash=f"dataset-{version}",
        validation_report={"metrics": {"holdout_gate_metrics": metrics}},
        root=root,
    )


def test_artifact_hash_changes_when_model_content_changes(tmp_path):
    first = _freeze(tmp_path, "v1", 1)
    second = _freeze(tmp_path, "v2", 2)

    first_hash = artifacts.artifact_content_hash(
        artifacts.load_manifest(first / "manifest.json")
    )
    second_hash = artifacts.artifact_content_hash(
        artifacts.load_manifest(second / "manifest.json")
    )

    assert first_hash != second_hash


def test_train_all_never_activates_artifacts(tmp_path, monkeypatch):
    calls: list[tuple[str, str]] = []

    def fake_train(family: str, horizon: str, **_kwargs):
        calls.append((family, horizon))
        return {"family": family, "horizon": horizon, "trained": True}

    monkeypatch.setattr(train_mod, "MODEL_FAMILIES", ("forex",))
    monkeypatch.setattr(train_mod, "MODEL_HORIZONS", ("intraday",))
    monkeypatch.setattr(train_mod, "train_family_horizon", fake_train)
    monkeypatch.setenv("ATHENA_ASE_MODELS_ROOT", str(tmp_path / "models"))

    result = train_mod.train_all(
        version="v-manual",
        report_path=tmp_path / "report.md",
        trainer=None,
    )

    assert calls == [("forex", "intraday")]
    assert result["failed"] == []
    assert "active_version" not in result
    assert not (tmp_path / "models" / artifacts.ACTIVE_VERSION_FILE).exists()


def test_runtime_loader_uses_exact_promoted_binding(tmp_path, monkeypatch):
    monkeypatch.setenv("ATHENA_ASE_MODELS_ROOT", str(tmp_path / "models"))
    monkeypatch.setenv("ATHENA_ASE_STATE_ROOT", str(tmp_path / "state"))
    v1 = _freeze(tmp_path / "models", "v1", 1)
    _freeze(tmp_path / "models", "v2", 2)
    manifest = artifacts.load_manifest(v1 / "manifest.json")
    promote_family(
        "forex",
        horizon="intraday",
        version="v1",
        artifact_hash=artifacts.artifact_content_hash(manifest),
        operator="test",
    )

    bundle = runtime_loader.load_artifact_bundle("forex", "intraday")

    assert bundle is not None
    assert bundle.version == "v1"
    assert bundle.artifact_hash == artifacts.artifact_content_hash(manifest)


def test_runtime_loader_rejects_unvalidated_latest_fallback(tmp_path, monkeypatch):
    monkeypatch.setenv("ATHENA_ASE_MODELS_ROOT", str(tmp_path))
    monkeypatch.setenv("ATHENA_ASE_STATE_ROOT", str(tmp_path / "state"))
    _freeze(tmp_path, "v-unvalidated", 1, passing=False)

    assert runtime_loader.load_artifact_bundle("forex", "intraday") is None


def _promotion_args(
    tmp_path: Path,
    artifact_hash: str,
    *,
    holdout_artifact_hash: str | None = None,
):
    metrics_path = tmp_path / "metrics.json"
    metrics_path.write_text(json.dumps(PASSING_METRICS), encoding="utf-8")
    registry_path = tmp_path / "holdout.json"
    registry = HoldoutRegistry()
    registry.record_holdout_eval(
        family="forex",
        horizon="intraday",
        passed=True,
        metrics=PASSING_METRICS,
        operator="test",
        artifact_version="v1",
        artifact_hash=holdout_artifact_hash or artifact_hash,
    )
    registry.save(registry_path)
    return SimpleNamespace(
        family="forex",
        horizon="intraday",
        metrics_file=str(metrics_path),
        artifact_version="v1",
        artifact_hash=artifact_hash,
        operator="test",
        registry=str(registry_path),
        dry_run=False,
    )


def test_cli_promotion_binds_the_verified_artifact(tmp_path, monkeypatch):
    models = tmp_path / "models"
    state = tmp_path / "state"
    monkeypatch.setenv("ATHENA_ASE_MODELS_ROOT", str(models))
    monkeypatch.setenv("ATHENA_ASE_STATE_ROOT", str(state))
    root = _freeze(models, "v1", 1)
    manifest = artifacts.load_manifest(root / "manifest.json")
    digest = artifacts.artifact_content_hash(manifest)

    assert ase_cli._cmd_promote(_promotion_args(tmp_path, digest)) == 0
    binding = get_deployment_binding("forex", "intraday")
    assert binding is not None
    assert (binding.version, binding.artifact_hash) == ("v1", digest)


def test_cli_promotion_rejects_wrong_artifact_hash(tmp_path, monkeypatch):
    models = tmp_path / "models"
    monkeypatch.setenv("ATHENA_ASE_MODELS_ROOT", str(models))
    monkeypatch.setenv("ATHENA_ASE_STATE_ROOT", str(tmp_path / "state"))
    _freeze(models, "v1", 1)

    assert ase_cli._cmd_promote(_promotion_args(tmp_path, "wrong-hash")) == 1
    assert get_deployment_binding("forex", "intraday") is None


def test_cli_promotion_rejects_holdout_from_another_artifact(tmp_path, monkeypatch):
    models = tmp_path / "models"
    monkeypatch.setenv("ATHENA_ASE_MODELS_ROOT", str(models))
    monkeypatch.setenv("ATHENA_ASE_STATE_ROOT", str(tmp_path / "state"))
    root = _freeze(models, "v1", 1)
    digest = artifacts.artifact_content_hash(
        artifacts.load_manifest(root / "manifest.json")
    )

    args = _promotion_args(
        tmp_path,
        digest,
        holdout_artifact_hash="different-artifact",
    )
    assert ase_cli._cmd_promote(args) == 1
    assert get_deployment_binding("forex", "intraday") is None


def test_holdout_eval_records_artifact_identity_from_metrics_file(tmp_path, monkeypatch):
    monkeypatch.setenv("ATHENA_ASE_STATE_ROOT", str(tmp_path / "state"))
    metrics_path = tmp_path / "metrics.json"
    metrics_path.write_text(
        json.dumps(
            {
                **PASSING_METRICS,
                "family": "forex",
                "horizon": "intraday",
                "artifact_version": "v1",
                "artifact_hash": "artifact-one",
            }
        ),
        encoding="utf-8",
    )
    registry_path = tmp_path / "holdout.json"
    args = SimpleNamespace(
        family="forex",
        horizon="intraday",
        metrics_file=str(metrics_path),
        artifact_version="",
        artifact_hash="",
        operator="test",
        registry=str(registry_path),
        dry_run=False,
    )

    assert ase_cli._cmd_holdout_eval(args) == 0
    record = HoldoutRegistry.load(registry_path).holdout["forex:intraday"]
    assert record.artifact_version == "v1"
    assert record.artifact_hash == "artifact-one"
    assert record.metrics["oos_trades"] == PASSING_METRICS["oos_trades"]
    assert "artifact_hash" not in record.metrics


def test_health_provenance_reports_the_exact_promoted_artifact(tmp_path, monkeypatch):
    from athena_app.api.routes_ase import _model_provenance

    models = tmp_path / "models"
    monkeypatch.setenv("ATHENA_ASE_MODELS_ROOT", str(models))
    monkeypatch.setenv("ATHENA_ASE_STATE_ROOT", str(tmp_path / "state"))
    v1 = _freeze(models, "v1", 1)
    _freeze(models, "v2", 2)
    manifest = artifacts.load_manifest(v1 / "manifest.json")
    digest = artifacts.artifact_content_hash(manifest)
    promote_family(
        "forex",
        horizon="intraday",
        version="v1",
        artifact_hash=digest,
        operator="test",
    )

    row = next(
        item
        for item in _model_provenance()
        if item["family"] == "forex" and item["horizon"] == "intraday"
    )

    assert row["version"] == "v1"
    assert row["artifactHash"] == digest
    assert row["runtimeEligible"] is True
