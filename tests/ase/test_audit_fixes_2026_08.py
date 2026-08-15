"""Regression tests for the 2026-08 ASE full-structure audit fixes."""

from __future__ import annotations

import math
import sys
import tempfile
import time
import types

import numpy as np
import pandas as pd
import pytest

from athena_ase.data.ingest.common import append_ptis_rows, price_series_id
from athena_ase.data.ptis import PTISStore, build_row
from athena_ase.instruments import instrument_by_symbol
from athena_ase.levels.brackets import compute_brackets


def _seed_trending_bars(
    store: PTISStore,
    source: str,
    symbol: str,
    tf: str,
    *,
    n: int = 300,
    end_close_ms: int,
    start_price: float = 100.0,
    drift: float = 0.001,
    noise: float = 0.0005,
) -> None:
    tf_ms = {"H1": 3_600_000, "D1": 86_400_000}[tf]
    fields: dict[str, list[dict]] = {f: [] for f in ("close", "open", "high", "low", "volume")}
    price = start_price
    for k in range(n):
        close_ms = end_close_ms - (n - 1 - k) * tf_ms
        ret = drift + (noise if k % 2 == 0 else -noise * 0.7)
        new_price = price * math.exp(ret)
        values = {
            "open": price,
            "high": max(price, new_price) * 1.0001,
            "low": min(price, new_price) * 0.9999,
            "close": new_price,
            "volume": 1000.0 + k,
        }
        for field, val in values.items():
            sid = price_series_id(source, symbol, tf, field)
            fields[field].append(build_row(sid, close_ms, close_ms + 90_000, float(val)))
        price = new_price
    for field, rows in fields.items():
        append_ptis_rows(store, price_series_id(source, symbol, tf, field), source, rows)


# ── #1 brackets TP2 units ────────────────────────────────────────────────────


def test_tp2_not_double_scaled_for_expensive_instruments():
    mae = {"q50": 0.3, "q90": 0.7}
    mfe = {"q50": 0.5, "q75": 0.8}
    xau = compute_brackets(
        direction=1, entry_reference=2400.0, sigma_bar=0.002,
        horizon="intraday", mae_q=mae, mfe_q=mfe,
    )
    r_unit_xau = 0.002 * math.sqrt(16) * 2400.0
    assert xau.tp2 == pytest.approx(2400.0 + 0.8 * r_unit_xau, rel=1e-9)
    assert xau.tp1 == pytest.approx(2400.0 + 0.5 * r_unit_xau, rel=1e-9)

    eur = compute_brackets(
        direction=1, entry_reference=1.10, sigma_bar=0.0015,
        horizon="intraday", mae_q=mae, mfe_q=mfe,
    )
    r_unit_eur = 0.0015 * math.sqrt(16) * 1.10
    assert eur.tp2 == pytest.approx(1.10 + 0.8 * r_unit_eur, rel=1e-9)


# ── #2 benchmark feed is optional, not core-gating ───────────────────────────


def test_missing_benchmark_degrades_to_nan_without_gating():
    from athena_ase.features.build import (
        FeatureBuildContext,
        build_features_for_candidate,
    )
    from athena_ase.inference.routing import route_for_feeds

    store = PTISStore(tempfile.mkdtemp(prefix="ase_bench_"))
    now = int(time.time() * 1000)
    _seed_trending_bars(store, "MT5", "GC=F", "H1", end_close_ms=now - 3_600_000)
    inst = instrument_by_symbol("GC=F")
    assert inst is not None and inst.benchmark == "XAUUSD"
    ctx = FeatureBuildContext(
        symbol=inst.symbol,
        family=inst.family,
        subclass=inst.subclass,
        horizon="intraday",
        decision_time_ms=now,
        direction=1,
        agreement_count=1,
        conflict_flag=False,
        benchmark_symbol=inst.benchmark,  # no XAUUSD series seeded
    )
    row = build_features_for_candidate(ctx, enriched=False, store=store)
    assert "benchmark" in ctx.optional_missing_feeds
    assert "benchmark" not in ctx.missing_feeds
    assert math.isnan(row["beta_bench"])
    routing = route_for_feeds(
        core_missing=ctx.missing_feeds,
        enriched_missing=ctx.enriched_missing_feeds,
    )
    assert routing["coreOk"] is True


# ── #3 live/backtest intraday family parity ──────────────────────────────────


def test_live_scan_intraday_family_filter_matches_backfill():
    from athena_ase.runtime.scan import generate_live_candidates
    from athena_ase.signals.engine import iter_candidates

    store = PTISStore(tempfile.mkdtemp(prefix="ase_parity_"))
    now = int(time.time() * 1000)
    last_close = now - 3_600_000
    _seed_trending_bars(store, "MT5", "EURUSD", "H1", end_close_ms=last_close, start_price=1.10)
    _seed_trending_bars(store, "MT5", "AAPL", "H1", end_close_ms=last_close, start_price=200.0)

    eurusd = instrument_by_symbol("EURUSD")
    aapl = instrument_by_symbol("AAPL")
    assert eurusd is not None and aapl is not None

    # Equity is intraday-ineligible in both paths (default ASE_INTRADAY_FAMILIES).
    assert generate_live_candidates(store, (aapl,), horizon="intraday") == []
    backfill_equity = list(
        iter_candidates(
            store, (aapl,), horizon="intraday",
            start_ms=last_close - 25 * 86_400_000, end_ms=now,
        )
    )
    assert backfill_equity == []

    # Positive control: forex intraday still produces candidates on a trend.
    live_forex = generate_live_candidates(store, (eurusd,), horizon="intraday")
    assert len(live_forex) >= 1
    assert live_forex[0].direction == 1


# ── #4 execution idempotency + MT5-mode positive proof ───────────────────────


def _trade_signal(**over):
    from athena_ase.contracts import ASESignal

    base = dict(
        engineVersion="t", modelFamily="forex", modelVersion="v1", horizon="intraday",
        decisionStatus="TRADE", direction="LONG", expectedNetR=0.3, expectedNetBps=3000.0,
        probabilityPositive=0.7, decisionMargin=0.2, signalStrength=50,
        returnQ={}, maeQ={}, mfeQ={}, holdQ={}, entryReference=1.10,
        entryZone=(1.09, 1.10), sl=1.09, tp1=1.12, tp2=1.13, maxHoldBars=16,
        primarySignals=[], predictionDiagnostics={}, dataQuality={},
        modelHealth={"artifactHash": "abc"}, instrument="EURUSD", decisionTimeMs=123456,
    )
    base.update(over)
    return ASESignal(**base)


def test_execute_skips_duplicate_filled_decision(tmp_path, monkeypatch):
    from athena_ase.execution import bridge, journal

    journal_path = tmp_path / "journal.parquet"
    journal.append_trade_signals([_trade_signal()], path=journal_path)
    assert journal.append_execution_outcome(
        instrument="EURUSD", decision_time_ms=123456,
        status="filled", detail={}, order_id="t1", path=journal_path,
    )
    monkeypatch.setattr(journal, "trade_journal_path", lambda: journal_path)
    monkeypatch.setattr(bridge, "execution_binding_check", lambda **k: (True, "ok"))

    sent: list[dict] = []
    deps = bridge.ASEExecutionDeps(
        mt5_execute=lambda s, a: sent.append(s) or {"success": True, "ticket": 1},
    )
    out = bridge.execute_trade_signal(_trade_signal(), deps=deps, write_journal=False)
    assert out["executed"] is False
    assert out["reason"] == "duplicate_decision"
    assert sent == []


def test_execute_rejects_unverified_mt5_trade_mode(tmp_path, monkeypatch):
    from athena_ase.execution import bridge, journal

    monkeypatch.setattr(journal, "trade_journal_path", lambda: tmp_path / "none.parquet")
    monkeypatch.setattr(bridge, "execution_binding_check", lambda **k: (True, "ok"))
    deps = bridge.ASEExecutionDeps(get_mt5_trade_mode=lambda: None)
    out = bridge.execute_trade_signal(_trade_signal(), deps=deps, write_journal=False)
    assert out["executed"] is False
    assert out["reason"] == "venue:mt5_trade_mode_unverified"


# ── #5 train final-fit purge/embargo vs eval holdout ─────────────────────────


def test_final_fit_train_slice_is_purged_against_eval():
    from athena_research.ase.train import chronological_train_eval_split
    from athena_research.ase.walkforward import purge_embargo_mask

    day = 86_400_000
    rows = []
    for i in range(10):
        start = i * 30 * day
        rows.append(
            {
                "decision_time_ms": start,
                "label_start_ms": start,
                "label_end_ms": start + 40 * day,
                "horizon": "swing",
                "y": i % 2,
            }
        )
    labeled = pd.DataFrame(rows)
    train, eval_frame = chronological_train_eval_split(labeled)
    combined = pd.concat([train, eval_frame])
    kept_idx = purge_embargo_mask(
        combined,
        train_idx=np.arange(len(train)),
        test_idx=np.arange(len(train), len(combined)),
        max_hold_bars=40,
    )
    kept = combined.iloc[kept_idx]
    eval_start = int(eval_frame["decision_time_ms"].min())
    assert len(kept) < len(train)
    assert (kept["label_end_ms"] < eval_start).all()


# ── #7 zero-fill train/serve parity plumbing ─────────────────────────────────


def test_all_nan_columns_and_inference_zero_fill():
    from athena_ase.inference.predict import _row_to_vector
    from athena_ase.models.meta import all_nan_feature_columns

    rows = [{"a": 1.0, "b": float("nan")}, {"a": 2.0, "b": float("nan")}]
    assert all_nan_feature_columns(rows, ("a", "b")) == ["b"]
    filled = _row_to_vector({"a": 1.5}, ("a", "b"), frozenset({"b"}))
    assert filled.tolist() == [1.5, 0.0]
    unfilled = _row_to_vector({"a": 1.5}, ("a", "b"))
    assert math.isnan(unfilled[1])


def test_zero_fill_sidecar_roundtrip(tmp_path, monkeypatch):
    from athena_ase.artifacts.loader import load_artifact_bundle as load_runtime_bundle
    from athena_ase.registry.artifacts import (
        ZERO_FILL_FILE,
        freeze_artifact_bundle,
        load_manifest,
        verify_manifest,
    )

    out = freeze_artifact_bundle(
        family="forex", horizon="intraday", version="zf1",
        models={"model_core.pkl": {"m": 1}},
        dataset_hash="d",
        zero_fill_features={"core": ["cot_pct"]},
        root=tmp_path,
    )
    assert (out / ZERO_FILL_FILE).exists()
    manifest = load_manifest(out / "manifest.json")
    assert ZERO_FILL_FILE in manifest.file_hashes
    assert verify_manifest(manifest, out) == []

    monkeypatch.setenv("ATHENA_ASE_MODELS_ROOT", str(tmp_path))
    bundle = load_runtime_bundle("forex", "intraday", version="zf1")
    assert bundle is not None
    assert bundle.zero_fill_features == {"core": ["cot_pct"]}

    # Re-freeze without zero-fill metadata removes the stale sidecar.
    freeze_artifact_bundle(
        family="forex", horizon="intraday", version="zf1",
        models={"model_core.pkl": {"m": 1}},
        dataset_hash="d",
        root=tmp_path,
    )
    assert not (out / ZERO_FILL_FILE).exists()


# ── #8 predict_batch isolation ───────────────────────────────────────────────


def test_predict_batch_isolates_candidate_errors(monkeypatch):
    from athena_ase.inference import predict as predict_mod
    from athena_ase.signals.arbitrate import Candidate

    monkeypatch.setattr(predict_mod, "load_artifact_bundle", lambda *a, **k: None)

    def _boom(*a, **k):
        raise RuntimeError("model exploded")

    monkeypatch.setattr(predict_mod, "predict_one", _boom)
    inst = instrument_by_symbol("EURUSD")
    cand = Candidate(
        instrument="EURUSD", family="forex", horizon="intraday",
        decision_time_ms=1, bar_index=0, direction=1,
    )
    out = predict_mod.predict_batch([cand], None, {"EURUSD": inst})
    assert len(out) == 1
    assert out[0].decisionStatus == "ERROR"
    assert "predict_failed" in str(out[0].predictionDiagnostics.get("error"))


# ── #9 masked crashes ────────────────────────────────────────────────────────


def test_bybit_microstructure_snapshot_writes_rows(tmp_path, monkeypatch):
    from athena_ase.data.ingest import bybit as bybit_mod
    from athena_ase import settings as settings_mod

    monkeypatch.setattr(settings_mod, "microstructure_enabled", lambda: True)

    cfg_stub = types.ModuleType("config")
    cfg_stub.CONFIG = {}
    agg_stub = types.ModuleType("athena.microstructure.aggtrade_volume")
    agg_stub.check_trade_bucket_cvd = lambda *a, **k: {
        "cvd_slope": 1.5,
        "cvd_value": 10.0,
        "bucket_count": 4.0,
    }
    micro_stub = types.ModuleType("athena.microstructure")
    micro_stub.aggtrade_volume = agg_stub
    pkg_stub = types.ModuleType("athena")
    pkg_stub.microstructure = micro_stub
    monkeypatch.setitem(sys.modules, "config", cfg_stub)
    monkeypatch.setitem(sys.modules, "athena", pkg_stub)
    monkeypatch.setitem(sys.modules, "athena.microstructure", micro_stub)
    monkeypatch.setitem(sys.modules, "athena.microstructure.aggtrade_volume", agg_stub)

    store = PTISStore(str(tmp_path / "ptis"))
    now_ms = 1_700_000_000_000
    counts = bybit_mod.ingest_microstructure_snapshot(store, "BTCUSDT", now_ms=now_ms)
    assert set(counts) == {
        "BYBIT:BTCUSDT:trade_imbalance_z",
        "BYBIT:BTCUSDT:bucket_delta_z",
    }
    rows = store.asof("BYBIT:BTCUSDT:trade_imbalance_z", now_ms + 60_000, 1)
    assert len(rows) == 1
    assert float(rows[0]["value"]) == pytest.approx(1.5)


def test_run_ingest_isolates_source_failures(tmp_path, monkeypatch):
    from athena_ase.data.ingest import runner

    def _raise(*a, **k):
        raise RuntimeError("boom")

    monkeypatch.setattr(runner.eodhd, "ingest_all", _raise)
    monkeypatch.setattr(runner.bybit, "ingest_all", lambda *a, **k: {"BYBIT:X": 1})
    store = PTISStore(str(tmp_path / "ptis"))
    out = runner.run_ingest(store, sources=["eodhd", "bybit"], write_audit=False)
    assert "error" in out["eodhd"]
    assert out["bybit"] == {"BYBIT:X": 1}


def test_realized_payoff_tolerates_missing_realized_column(monkeypatch):
    from athena_ase.inference import realized_payoff

    monkeypatch.setattr(
        realized_payoff,
        "_journal_frame",
        lambda: pd.DataFrame({"modelFamily": ["forex"], "horizon": ["swing"]}),
    )
    assert realized_payoff.realized_exit_payoff("forex", "swing") is None
    assert realized_payoff.journal_calibration_snapshot("forex", "swing") is None


# ── #10 eodhd canonical symbol ───────────────────────────────────────────────


def test_eodhd_ingest_canonicalizes_symbol_and_skips_closeless_bars(
    tmp_path, monkeypatch
):
    from athena_ase.data.ingest import eodhd

    bars = [
        {"time": "2026-01-01T00:00:00Z", "open": 1.1, "high": 1.11, "low": 1.09, "close": 1.105, "volume": 5},
        {"time": "2026-01-01T01:00:00Z", "open": None, "high": None, "low": None, "close": None, "volume": 3},
    ]
    monkeypatch.setattr(eodhd, "iter_backtest_bars", lambda *a, **k: bars)
    store = PTISStore(str(tmp_path / "ptis"))
    counts = eodhd.ingest_pair_tf(
        store,
        {"symbol": "EURUSD.FOREX", "display": "EUR/USD"},
        "H1",
        "eodhd_intraday",
    )
    assert "EODHD:EURUSD:H1:close" in counts
    rows = store.asof("EODHD:EURUSD:H1:close", 2_000_000_000_000, 10)
    assert len(rows) == 1
    assert float(rows[0]["value"]) == pytest.approx(1.105)


# ── #11 reconcile retry + holdout guard + CLI clear ──────────────────────────


def test_reconcile_retries_legacy_unresolved_rows(tmp_path):
    from athena_ase.data.parquet_io import write_parquet_atomic
    from athena_ase.execution import reconcile as rec
    from athena_ase.execution.journal import load_trade_journal

    t0 = 1_700_000_000_000
    journal_path = tmp_path / "journal.parquet"
    df = pd.DataFrame(
        [
            {
                "instrument": "EURUSD",
                "horizon": "intraday",
                "decisionTimeMs": t0,
                "entryReference": 1.10,
                "sl": 1.09,
                "tp1": 1.12,
                "maxHoldBars": 16,
                "direction": "LONG",
                "executionStatus": "filled",
                "realizedNetR": None,
                "realizedWin": None,
                # Legacy unresolved stamp: previously skipped forever.
                "reconciledAt": "2026-01-01T00:00:00+00:00",
            }
        ]
    )
    write_parquet_atomic(journal_path, df)

    store = PTISStore(str(tmp_path / "ptis"))
    hour = 3_600_000
    fields: dict[str, list[dict]] = {f: [] for f in ("close", "open", "high", "low")}
    bars = [
        (t0, 1.10, 1.101, 1.099),
        (t0 + hour, 1.105, 1.106, 1.100),
        (t0 + 2 * hour, 1.118, 1.1205, 1.104),
    ]
    for vt, close, high, low in bars:
        for field, val in (("close", close), ("open", close), ("high", high), ("low", low)):
            sid = price_series_id("MT5", "EURUSD", "H1", field)
            fields[field].append(build_row(sid, vt, vt + 90_000, val))
    for field, rows in fields.items():
        append_ptis_rows(store, price_series_id("MT5", "EURUSD", "H1", field), "MT5", rows)

    updated = rec.reconcile_filled_from_ptis(
        path=journal_path, store=store, now_ms=t0 + 20 * hour
    )
    assert updated == 1
    out = load_trade_journal(journal_path)
    assert float(out.iloc[0]["realizedNetR"]) == pytest.approx(2.0)
    assert out.iloc[0]["reconciledAt"] != "2026-01-01T00:00:00+00:00"


def test_holdout_registry_corrupt_file_fails_closed(tmp_path):
    from athena_ase.registry.holdout import HoldoutRegistry

    bad = tmp_path / "holdout.json"
    bad.write_text("{not valid json", encoding="utf-8")
    reg = HoldoutRegistry.load(bad)
    assert reg.holdout == {}


def test_clear_watch_max_cli(tmp_path, monkeypatch, capsys):
    import ase_cli
    from athena_ase.registry.promotion import is_watch_max, set_watch_max

    monkeypatch.setenv("ATHENA_ASE_STATE_ROOT", str(tmp_path))
    set_watch_max("forex", True)
    assert is_watch_max("forex") is True
    rc = ase_cli.main(["clear-watch-max", "--family", "forex"])
    assert rc == 0
    assert is_watch_max("forex") is False
