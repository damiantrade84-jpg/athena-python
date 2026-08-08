"""Focused persistence and audit contracts for Tuning Lab pushes."""

from __future__ import annotations

import hashlib
import json

import pytest
import yaml

import athena_experiment.push as experiment_push


def test_push_preserves_existing_config_and_writes_audit_record(tmp_path, monkeypatch):
    config_path = tmp_path / "config.local.yaml"
    config_path.write_text("KEEP:\n  enabled: true\n", encoding="utf-8")
    monkeypatch.setattr(experiment_push, "local_config_path", lambda: config_path)

    overlay = {
        "ENGINE_A_V3_MOMENTUM_BLEND": {
            "BY_GROUP": {"forex_majors": {"ROC_WEIGHT": 0.5}}
        }
    }
    provenance = {
        "source": "tuning_lab",
        "engine": "A",
        "symbol": "EURUSD",
        "scoreGroup": "forex_majors",
        "style": "intraday",
        "runId": "run-123",
    }

    merged = experiment_push.push_overlay_to_local_config(
        overlay,
        provenance=provenance,
    )

    persisted = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    assert merged == persisted
    assert persisted["KEEP"] == {"enabled": True}
    assert persisted["ENGINE_A_V3_MOMENTUM_BLEND"] == overlay[
        "ENGINE_A_V3_MOMENTUM_BLEND"
    ]

    audit_path = tmp_path / "logs" / "tuning_lab_push_audit.jsonl"
    rows = [json.loads(line) for line in audit_path.read_text(encoding="utf-8").splitlines()]
    assert len(rows) == 1
    record = rows[0]
    canonical = json.dumps(
        overlay,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    )
    assert record["overlaySha256"] == hashlib.sha256(
        canonical.encode("utf-8")
    ).hexdigest()
    assert record["overlay"] == overlay
    assert record["provenance"] == provenance
    assert record["restartRequired"] is True
    assert record["configPath"] == str(config_path.resolve())
    assert list(tmp_path.glob(f".{config_path.name}.*.tmp")) == []


def test_push_rejects_empty_overlay(tmp_path, monkeypatch):
    config_path = tmp_path / "config.local.yaml"
    monkeypatch.setattr(experiment_push, "local_config_path", lambda: config_path)

    with pytest.raises(ValueError, match="non-empty mapping"):
        experiment_push.push_overlay_to_local_config({})

    assert not config_path.exists()
    assert not (tmp_path / "logs" / "tuning_lab_push_audit.jsonl").exists()
