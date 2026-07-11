"""ASE inference uses one PTIS snapshot and fails closed on missing core data."""

from __future__ import annotations

import numpy as np

from athena_ase.artifacts.loader import ArtifactBundle
from athena_ase.data.ptis import PTIS_VALUE_DTYPE
from athena_ase.features import build as feature_build
from athena_ase.features.build import FeatureBuildContext
from athena_ase.gates.demo_only import GateResult
from athena_ase.inference import predict as predict_mod
from athena_ase.registry.artifacts import ArtifactManifest
from athena_ase.signals.arbitrate import Candidate
from athena_ase.universe import UNIVERSE


class _RecordingStore:
    def __init__(self):
        self.calls: list[str] = []

    def asof(self, series_id: str, decision_time_ms: int, n: int = 1):
        self.calls.append(series_id)
        size = max(2, n)
        rows = np.empty(size, dtype=PTIS_VALUE_DTYPE)
        rows["value_time"] = np.arange(size, dtype=np.int64) + decision_time_ms - size
        rows["available_time"] = rows["value_time"]
        rows["value"] = np.linspace(100.0, 101.0, size)
        rows["revision"] = 0
        rows["ingest_time"] = rows["available_time"]
        return rows


class _Mt5ForexStore(_RecordingStore):
    def asof(self, series_id: str, decision_time_ms: int, n: int = 1):
        self.calls.append(series_id)
        if series_id.startswith("DUKASCOPY:"):
            return np.empty(0, dtype=PTIS_VALUE_DTYPE)
        if not series_id.startswith("MT5:"):
            return np.empty(0, dtype=PTIS_VALUE_DTYPE)
        size = max(2, n)
        rows = np.empty(size, dtype=PTIS_VALUE_DTYPE)
        rows["value_time"] = np.arange(size, dtype=np.int64) + decision_time_ms - size
        rows["available_time"] = rows["value_time"]
        rows["value"] = np.linspace(100.0, 101.0, size)
        rows["revision"] = 0
        rows["ingest_time"] = rows["available_time"]
        return rows


def _context() -> FeatureBuildContext:
    return FeatureBuildContext(
        symbol="EURUSD",
        family="forex",
        subclass="major",
        horizon="intraday",
        decision_time_ms=1_700_000_000_000,
        direction=1,
        agreement_count=1,
        conflict_flag=False,
        benchmark_symbol="USDX",
    )


def test_feature_builder_reads_only_the_supplied_ptis_store(monkeypatch):
    store = _RecordingStore()

    def unexpected_default_read(*_args, **_kwargs):
        raise AssertionError("default PTIS store must not be read")

    monkeypatch.setattr(feature_build, "asof", unexpected_default_read)

    row = feature_build.build_features_for_candidate(
        _context(), enriched=False, store=store
    )

    assert store.calls
    assert row["ret_z_1"] == row["ret_z_1"]


def test_forex_features_fall_back_to_mt5_volume_when_dukascopy_is_absent():
    store = _Mt5ForexStore()
    context = _context()

    row = feature_build.build_features_for_candidate(
        context,
        enriched=False,
        store=store,
    )

    assert "DUKASCOPY:EURUSD:H1:volume" in store.calls
    assert "MT5:EURUSD:H1:volume" in store.calls
    assert "volume" not in context.missing_feeds
    assert row["volu_z"] == row["volu_z"]


class _PositiveModel:
    def predict_proba(self, _features):
        return np.array([[0.2, 0.8]])


def test_missing_core_feed_forces_flat_in_production_inference(monkeypatch):
    manifest = ArtifactManifest(
        family="forex",
        horizon="intraday",
        version="v-test",
        feature_schema_hash="schema",
        feature_schema=["ret_z_1"],
        feature_schemas={"core": ["ret_z_1"]},
        feature_schema_hashes={"core": "schema"},
        dataset_hash="dataset",
        eval_summary={"expectancy": 0.2},
    )
    bundle = ArtifactBundle(
        family="forex",
        horizon="intraday",
        version="v-test",
        root=None,  # type: ignore[arg-type]
        manifest=manifest,
        model_core=_PositiveModel(),
        model_enriched=None,
        calibrator=None,
        calibrator_enriched=None,
        quantile_heads=None,
        quantile_heads_enriched=None,
        thr_family=0.1,
        feature_names=("ret_z_1",),
    )
    candidate = Candidate(
        instrument="EURUSD",
        family="forex",
        horizon="intraday",
        decision_time_ms=1_700_000_000_000,
        bar_index=100,
        direction=1,
        sigma_bar=0.01,
        entry_log=0.0,
    )

    def missing_core(ctx, *, enriched=False, store=None):
        if "volume" not in ctx.missing_feeds:
            ctx.missing_feeds.append("volume")
        return {"ret_z_1": 1.0}

    monkeypatch.setattr(predict_mod, "build_features_for_candidate", missing_core)
    monkeypatch.setattr(predict_mod, "verify_manifest_hashes", lambda *_args: [])
    monkeypatch.setattr(
        predict_mod,
        "assert_demo",
        lambda: GateResult(ok=True, reason="ok", checks={}),
    )

    feature_audit_rows: list[dict] = []
    signal = predict_mod.predict_one(
        candidate,
        store=object(),
        instrument=UNIVERSE[0],
        bundle=bundle,
        feature_audit_rows=feature_audit_rows,
    )

    assert signal.decisionStatus == "FLAT"
    assert signal.dataQuality["coreOk"] is False
    assert signal.dataQuality["missingFeeds"] == ["volume"]
    assert feature_audit_rows == [
        {
            "instrument": "EURUSD",
            "decisionTimeMs": 1_700_000_000_000,
            "route": "core",
            "schema": ["ret_z_1"],
            "values": [1.0],
        }
    ]
