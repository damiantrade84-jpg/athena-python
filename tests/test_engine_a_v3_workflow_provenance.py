from __future__ import annotations

import json
import tempfile
from dataclasses import dataclass
from pathlib import Path

import pandas as pd

from engine_a_v3 import workflow


def test_record_validation_attempt_increments_per_lane():
    tmp_path = Path(tempfile.mkdtemp())
    first = workflow._record_validation_attempt(tmp_path, "crypto_btc", "swing")
    second = workflow._record_validation_attempt(tmp_path, "crypto_btc", "swing")
    other_lane = workflow._record_validation_attempt(tmp_path, "crypto_btc", "intraday")
    assert (first, second, other_lane) == (1, 2, 1)
    registry = json.loads((tmp_path / "validation_attempts.json").read_text(encoding="utf-8"))
    from engine_a_v3.profile import scorer_sha256

    assert registry[f"crypto_btc|swing|{scorer_sha256()}"] == 2


def test_record_validation_attempt_survives_corrupt_registry():
    tmp_path = Path(tempfile.mkdtemp())
    (tmp_path / "validation_attempts.json").write_text("not json", encoding="utf-8")
    assert workflow._record_validation_attempt(tmp_path, "g", "swing") == 1


@dataclass
class _FakeProvenance:
    data_source: str = "mt5"

    def to_dict(self):
        return {"data_source": self.data_source}


def _fake_frame():
    index = pd.date_range("2026-01-01", periods=3, freq="h", tz="UTC")
    return pd.DataFrame(
        {"open": [1.0, 1.1, 1.2], "high": [1.2, 1.3, 1.4],
         "low": [0.9, 1.0, 1.1], "close": [1.1, 1.2, 1.3], "volume": [10, 11, 12]},
        index=index,
    )


def _manifest_fixture():
    return {
        "groups": {
            "fx_eurusd": {
                "horizons": ["intraday", "swing"],
                "validationEnabled": True,
                "requiredProvider": "mt5",
                "costsVerified": True,
                "pairs": [
                    {"display": "EUR/USD", "symbol": "EURUSD", "type": "forex"}
                ],
                "costs": {
                    "spreadBps": 1.0,
                    "commissionBps": 0.5,
                    "slippageBps": 1.0,
                    "swapBpsPerDay": 0.1,
                },
            },
        },
        "limits": {"H1": 10, "H4": 10, "D1": 10},
    }


def test_frozen_data_lane_bypasses_live_load_and_labels_artifact(monkeypatch):
    import frozen_data

    tmp_path = Path(tempfile.mkdtemp())
    monkeypatch.setattr(workflow, "load_manifest", lambda path=None: _manifest_fixture())

    def _no_live(*args, **kwargs):
        raise AssertionError("live load_ohlcv must not run in frozen mode")

    monkeypatch.setattr(workflow, "load_ohlcv", _no_live)
    rows = [
        {"time": f"2026-01-0{day}T00:00:00+00:00", "open": 1.0, "high": 1.1,
         "low": 0.9, "close": 1.0, "vol": 1.0}
        for day in range(1, 5)
    ]
    seen = {}

    def _fake_frozen(data_as_of, pair, tf, provider, **kwargs):
        seen[tf] = (data_as_of, provider)
        return list(rows)

    monkeypatch.setattr(frozen_data, "read_frozen_candles", _fake_frozen)
    captured = {}

    def _fake_cohort(datasets, **kwargs):
        captured["datasets"] = datasets
        return {
            "artifactId": "frozen1",
            "status": "REJECTED",
            "horizon": kwargs["horizon"],
            "provenance": {"datasetId": kwargs["dataset_id"]},
        }

    monkeypatch.setattr(workflow, "validate_specialist_cohort", _fake_cohort)

    path = workflow.validate_manifest_group(
        "fx_eurusd", "swing", candidate_root=tmp_path, data_as_of="2026-05-30"
    )
    artifact = json.loads(path.read_text(encoding="utf-8"))
    assert artifact["provenance"]["dataFrozen"] is True
    assert artifact["provenance"]["dataAsOf"] == "2026-05-30"
    assert seen["H4"] == ("2026-05-30", "mt5")
    # Last frozen row (possibly forming at freeze) is dropped.
    assert captured["datasets"][0]["candles"]["H4"] == rows[:-1]


def test_frozen_data_lane_fails_closed_on_missing_series(monkeypatch):
    import frozen_data
    import pytest

    tmp_path = Path(tempfile.mkdtemp())
    monkeypatch.setattr(workflow, "load_manifest", lambda path=None: _manifest_fixture())
    monkeypatch.setattr(
        workflow, "load_ohlcv",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("no live load")),
    )

    def _missing(*args, **kwargs):
        raise frozen_data.FrozenDataError("missing frozen candle series")

    monkeypatch.setattr(frozen_data, "read_frozen_candles", _missing)
    with pytest.raises(frozen_data.FrozenDataError):
        workflow.validate_manifest_group(
            "fx_eurusd", "swing", candidate_root=tmp_path, data_as_of="2026-05-30"
        )


def test_live_lane_artifact_labeled_not_frozen(monkeypatch):
    tmp_path = Path(tempfile.mkdtemp())
    monkeypatch.setattr(workflow, "load_manifest", lambda path=None: _manifest_fixture())
    monkeypatch.setattr(
        workflow, "load_ohlcv",
        lambda *args, **kwargs: (_fake_frame(), _FakeProvenance()),
    )
    monkeypatch.setattr(
        workflow, "validate_specialist_cohort",
        lambda datasets, **kwargs: {
            "artifactId": "live1",
            "status": "REJECTED",
            "horizon": kwargs["horizon"],
            "provenance": {"datasetId": kwargs["dataset_id"]},
        },
    )
    path = workflow.validate_manifest_group(
        "fx_eurusd", "swing", candidate_root=tmp_path
    )
    assert json.loads(path.read_text(encoding="utf-8"))["provenance"]["dataFrozen"] is False


def test_cohort_provenance_records_dataset_spans(monkeypatch):
    from engine_a_v3 import validation

    monkeypatch.setattr(
        validation,
        "_validation_results",
        lambda *args, **kwargs: ([[0.5], [0.4], [0.3], [0.2]], [0.1] * 15),
    )
    candles = {
        tf: [
            {"time": f"2026-01-{day:02d}T00:00:00", "open": 1.0, "high": 1.1,
             "low": 0.9, "close": 1.0, "vol": 1.0}
            for day in range(1, 6)
        ]
        for tf in ("H1", "H4", "D1")
    }
    artifact = validation.validate_specialist_cohort(
        [{"pair": {"symbol": "EURUSD", "type": "forex"}, "candles": candles}],
        horizon="swing",
        dataset_id="ds-test",
        expected_symbols=["EURUSD"],
        spread_bps=1.0,
        commission_bps=0.5,
        slippage_bps=1.0,
        swap_bps_per_day=0.1,
    )
    span = artifact["provenance"]["datasetSpans"]["EURUSD"]
    assert span["primaryTf"] == "H4"
    assert span["bars"] == 5
    assert span["start"] == "2026-01-01T00:00:00"
    assert span["end"] == "2026-01-05T00:00:00"


def test_validate_manifest_group_writes_dataset_snapshot_and_attempt_index(
    monkeypatch,
):
    tmp_path = Path(tempfile.mkdtemp())
    manifest = {
        "groups": {
            "fx_eurusd": {
                "horizons": ["intraday", "swing"],
                "validationEnabled": True,
                "requiredProvider": "mt5",
                "costsVerified": True,
                "pairs": [
                    {"display": "EUR/USD", "symbol": "EURUSD", "type": "forex"}
                ],
                "costs": {
                    "spreadBps": 1.0,
                    "commissionBps": 0.5,
                    "slippageBps": 1.0,
                    "swapBpsPerDay": 0.1,
                },
            },
        },
        "limits": {"H1": 10, "H4": 10, "D1": 10},
    }
    monkeypatch.setattr(workflow, "load_manifest", lambda path=None: manifest)
    monkeypatch.setattr(
        workflow, "load_ohlcv",
        lambda *args, **kwargs: (_fake_frame(), _FakeProvenance()),
    )
    captured = {}

    def _fake_cohort(datasets, **kwargs):
        captured["datasets"] = datasets
        return {
            "artifactId": "abc123",
            "status": "REJECTED",
            "horizon": kwargs["horizon"],
            "provenance": {"datasetId": kwargs["dataset_id"]},
        }

    monkeypatch.setattr(workflow, "validate_specialist_cohort", _fake_cohort)

    path = workflow.validate_manifest_group(
        "fx_eurusd", "swing", candidate_root=tmp_path
    )
    artifact = json.loads(path.read_text(encoding="utf-8"))
    assert artifact["provenance"]["attemptIndex"] == 1
    snapshot_name = artifact["provenance"]["datasetSnapshotPath"]
    snapshot = json.loads((tmp_path / snapshot_name).read_text(encoding="utf-8"))
    # Snapshot holds exactly the confirmed candles handed to validation.
    assert snapshot["EURUSD"] == captured["datasets"][0]["candles"]
    assert set(snapshot["EURUSD"]) == {"H1", "H4", "D1"}

    second = workflow.validate_manifest_group(
        "fx_eurusd", "swing", candidate_root=tmp_path
    )
    assert json.loads(second.read_text(encoding="utf-8"))["provenance"]["attemptIndex"] == 2
