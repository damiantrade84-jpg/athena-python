"""ASE scan diagnostics and blocker metadata (empty PTIS / missing artifacts)."""

from __future__ import annotations

import time

import pytest

import athena_ase.runtime.scan as scan_module
from athena_ase.data.availability import AvailabilityRuleId, compute_available_time_ms
from athena_ase.data.ptis import PTISStore, build_row
from athena_ase.inference.predict import predict_no_candidate
from athena_ase.instruments import instrument_by_symbol
from athena_ase.runtime.health import ase_health, scan_diagnostics
from athena_ase.runtime.scan import DEFAULT_SCAN_INGEST_SOURCES, run_ase_scan
from athena_ase.data.ingest.common import eodhd_series_id


def _seed_forex_h1_bars(
    store: PTISStore,
    symbol: str = "EURUSD",
    *,
    n: int = 300,
    start_price: float = 1.08,
    step: float = 0.0003,
) -> None:
    now_ms = int(time.time() * 1000)
    bar_ms = 3_600_000
    start = now_ms - n * bar_ms

    for field in ("close", "high", "low"):
        sid = eodhd_series_id(symbol, "H1", field)
        store.register_series(sid, "EODHD")
        rows = []
        for i in range(n):
            vt = start + i * bar_ms
            avail = compute_available_time_ms(
                AvailabilityRuleId.EODHD_BAR,
                value_time_ms=vt,
                bar_close_ms=vt,
            )
            price = start_price + i * step
            if field == "high":
                val = price * 1.001
            elif field == "low":
                val = price * 0.999
            else:
                val = price
            rows.append(build_row(sid, vt, avail, val))
        store.append_rows(sid, rows, source="EODHD", auto_register=False)


@pytest.fixture
def ptis(tmp_path):
    return PTISStore(tmp_path / "ptis")


def test_scan_empty_ptis_returns_flat_with_ptis_blocker(ptis: PTISStore):
    result = run_ase_scan(
        symbols=["EURUSD", "GBPUSD"],
        horizon="intraday",
        write_journal=False,
        ptis_root=str(ptis.root),
        ingest_sources=(),
    )
    assert result["success"] is True
    assert result["candidateCount"] == 0
    assert result["signalCount"] == 2
    assert result["statusCounts"]["FLAT"] == 2
    assert result["diagnostics"]["ptisSeriesCount"] == 0

    row = result["signals"][0]
    assert row["decisionStatus"] == "FLAT"
    assert row["dataQuality"]["blocker"] == "ptis_bars_missing"
    assert row["dataQuality"]["ptisOk"] is False
    assert row["dataQuality"]["artifactsPresent"] is False
    assert row["modelHealth"]["errorReason"] == "artifact_missing"


def test_scan_with_bars_and_no_artifacts_yields_error_candidates(ptis: PTISStore):
    _seed_forex_h1_bars(ptis, "EURUSD")
    result = run_ase_scan(
        symbols=["EURUSD"],
        horizon="intraday",
        write_journal=False,
        ptis_root=str(ptis.root),
        ingest_sources=(),
    )
    assert result["candidateCount"] >= 1
    flat = next(s for s in result["signals"] if s["decisionStatus"] == "FLAT")
    assert flat["dataQuality"]["blocker"] == "model_not_trained"
    assert flat["dataQuality"]["artifactsPresent"] is False
    assert flat["modelHealth"]["errorReason"] == "artifact_missing"
    assert result["diagnostics"]["eurusdH1CloseRows"] > 100


def test_predict_no_candidate_blocker_metadata(ptis: PTISStore):
    inst = instrument_by_symbol("EURUSD")
    assert inst is not None
    sig = predict_no_candidate(inst, "intraday", store=ptis)
    assert sig.decisionStatus == "FLAT"
    assert sig.dataQuality["blocker"] == "ptis_bars_missing"
    assert sig.modelHealth["errorReason"] == "artifact_missing"


def test_ase_health_reports_blockers(ptis: PTISStore):
    health = ase_health(ptis_root=str(ptis.root), horizon="intraday")
    assert health["success"] is True
    assert health["ready"] is False
    assert "ptis_empty" in health["blockers"]
    assert "no_frozen_artifacts" in health["blockers"]

    _seed_forex_h1_bars(ptis, "EURUSD")
    health2 = ase_health(ptis_root=str(ptis.root), horizon="intraday")
    assert "ptis_empty" not in health2["blockers"]
    assert health2["eurusdH1CloseRows"] > 100
    diag = scan_diagnostics(ptis, horizon="intraday")
    assert diag["ptisSeriesCount"] >= 3


def test_scan_runs_ingest_by_default(ptis: PTISStore, monkeypatch):
    calls: list[dict[str, Any]] = []

    def fake_run_ingest(*, store, sources, write_audit):
        calls.append({"store": store, "sources": tuple(sources), "write_audit": write_audit})
        return {"mt5_live": {"inserted": 5}}

    monkeypatch.setattr(scan_module, "run_ingest", fake_run_ingest)

    result = run_ase_scan(
        symbols=["EURUSD"],
        horizon="intraday",
        write_journal=False,
        ptis_root=str(ptis.root),
    )
    assert len(calls) == 1
    assert calls[0]["sources"] == DEFAULT_SCAN_INGEST_SOURCES
    assert calls[0]["write_audit"] is False
    assert result["ingestResult"]["result"] == {"mt5_live": {"inserted": 5}}


def test_scan_skips_ingest_when_sources_empty(ptis: PTISStore, monkeypatch):
    calls: list[dict[str, Any]] = []

    def fake_run_ingest(*, store, sources, write_audit):
        calls.append({"store": store, "sources": tuple(sources), "write_audit": write_audit})
        return {}

    monkeypatch.setattr(scan_module, "run_ingest", fake_run_ingest)

    result = run_ase_scan(
        symbols=["EURUSD"],
        horizon="intraday",
        write_journal=False,
        ptis_root=str(ptis.root),
        ingest_sources=(),
    )
    assert calls == []
    assert "ingestResult" not in result


def test_scan_ingest_failure_is_fail_open(ptis: PTISStore, monkeypatch):
    def fake_run_ingest(*, store, sources, write_audit):
        raise RuntimeError("bybit timeout")

    monkeypatch.setattr(scan_module, "run_ingest", fake_run_ingest)

    result = run_ase_scan(
        symbols=["EURUSD", "GBPUSD"],
        horizon="intraday",
        write_journal=False,
        ptis_root=str(ptis.root),
        ingest_sources=["bybit"],
    )
    assert result["success"] is True
    assert result["ingestResult"]["error"] == "bybit timeout"
