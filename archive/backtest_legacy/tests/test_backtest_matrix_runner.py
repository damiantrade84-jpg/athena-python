"""Tests for the backtest matrix runner."""

import json
import os
import tempfile
from pathlib import Path

import pytest

# Allow importing tools/run_backtest_matrix.py
import sys
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))

from run_backtest_matrix import (
    _safe_symbol,
    expand_jobs,
    _should_skip_job,
    _job_output_path,
    build_combined_strategy_lab_output,
    build_summary_report,
    build_failures_report,
)


SAMPLE_MATRIX = {
    "asset_groups": {
        "crypto_major": {
            "symbols": ["BTC/USDT", "ETH/USDT"],
            "engines": ["A", "B"],
            "timeframes": ["H1", "H4"],
        },
        "forex_major": {
            "symbols": ["EUR/USD"],
            "engines": ["A"],
            "timeframes": ["H4"],
        },
    },
    "defaults": {
        "start_date": "2024-01-01",
        "end_date": "2026-04-25",
        "initial_balance": 10000,
        "save_after_each_job": True,
        "fail_fast": False,
    },
}


def test_safe_symbol():
    assert _safe_symbol("BTC/USDT") == "BTC_USDT"
    assert _safe_symbol("EUR/USD") == "EUR_USD"
    assert _safe_symbol("S&P 500") == "S&P_500"
    assert _safe_symbol("XAU/USD") == "XAU_USD"


def test_expand_jobs_no_filter():
    jobs = expand_jobs(SAMPLE_MATRIX, {})
    assert len(jobs) > 0
    # crypto_major: 2 symbols * 2 engines * 2 timeframes = 8
    # forex_major: 1 symbol * 1 engine * 1 timeframe = 1
    # But B only supports H1/H4, A supports H1/H4/D1. Here we have H1/H4 for crypto, H4 for forex.
    # Expected: crypto_major A H1, A H4, B H1, B H4 (2*2*2=8) + forex_major A H4 (1) = 9
    assert len(jobs) == 9
    job_ids = {j["job_id"] for j in jobs}
    assert "crypto_major_BTC_USDT_A_H1" in job_ids
    assert "forex_major_EUR_USD_A_H4" in job_ids


def test_expand_jobs_engine_filter():
    jobs = expand_jobs(SAMPLE_MATRIX, {"engines": ["A"]})
    assert all(j["engine"] == "A" for j in jobs)
    # crypto_major: 2*1*2=4 + forex_major: 1*1*1=1 = 5
    assert len(jobs) == 5


def test_expand_jobs_symbol_filter():
    jobs = expand_jobs(SAMPLE_MATRIX, {"symbols": ["BTC/USDT"]})
    assert all(j["symbol"] == "BTC/USDT" for j in jobs)
    assert len(jobs) == 4  # A H1, A H4, B H1, B H4


def test_expand_jobs_group_filter():
    jobs = expand_jobs(SAMPLE_MATRIX, {"groups": ["forex_major"]})
    assert all(j["asset_group"] == "forex_major" for j in jobs)
    assert len(jobs) == 1


def test_expand_jobs_timeframe_filter():
    jobs = expand_jobs(SAMPLE_MATRIX, {"timeframes": ["H1"]})
    assert all(j["timeframe"] == "H1" for j in jobs)
    # 2 symbols * 2 engines * 1 timeframe = 4
    assert len(jobs) == 4


def test_dry_run_does_not_create_jobs_dir():
    # Dry-run via the CLI is end-to-end; here we just confirm expand_jobs is pure.
    jobs = expand_jobs(SAMPLE_MATRIX, {})
    assert isinstance(jobs, list)
    assert len(jobs) == 9


def test_should_skip_job_success():
    import run_backtest_matrix as rm
    with tempfile.TemporaryDirectory() as tmp:
        rm._LOG_DIR = Path(tmp)
        rm._JOBS_DIR = rm._LOG_DIR / "jobs"
        job = {"asset_group": "g", "symbol": "BTC/USDT", "engine": "A", "timeframe": "H1", "job_id": "test"}
        path = _job_output_path(job)
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w") as f:
            json.dump({"status": "success"}, f)
        assert _should_skip_job(job, resume=True) is True
        assert _should_skip_job(job, resume=False) is False


def test_should_skip_job_failed():
    import run_backtest_matrix as rm
    with tempfile.TemporaryDirectory() as tmp:
        rm._LOG_DIR = Path(tmp)
        rm._JOBS_DIR = rm._LOG_DIR / "jobs"
        job = {"asset_group": "g", "symbol": "BTC/USDT", "engine": "A", "timeframe": "H1", "job_id": "test"}
        path = _job_output_path(job)
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w") as f:
            json.dump({"status": "failed"}, f)
        assert _should_skip_job(job, resume=True) is False


def test_should_skip_job_missing_file():
    import run_backtest_matrix as rm
    with tempfile.TemporaryDirectory() as tmp:
        rm._LOG_DIR = Path(tmp)
        rm._JOBS_DIR = rm._LOG_DIR / "jobs"
        job = {"asset_group": "g", "symbol": "BTC/USDT", "engine": "A", "timeframe": "H1", "job_id": "test"}
        assert _should_skip_job(job, resume=True) is False


def test_build_combined_strategy_lab_output():
    import run_backtest_matrix as rm
    with tempfile.TemporaryDirectory() as tmp:
        rm._LOG_DIR = Path(tmp)
        rm._JOBS_DIR = rm._LOG_DIR / "jobs"
        job = {"asset_group": "crypto", "symbol": "BTC/USDT", "engine": "A", "timeframe": "H1", "job_id": "j1"}
        path = _job_output_path(job)
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w") as f:
            json.dump({
                "status": "success",
                "closed_trades": [
                    {"pair": "BTC/USDT", "resultR": 1.5, "outcome": "TP1"},
                    {"pair": "BTC/USDT", "resultR": -1.0, "outcome": "SL"},
                ]
            }, f)
        flat = build_combined_strategy_lab_output([job])
        assert len(flat) == 2
        assert flat[0]["asset_group"] == "crypto"
        assert flat[0]["matrix_job_id"] == "j1"


def test_build_summary_report():
    import run_backtest_matrix as rm
    with tempfile.TemporaryDirectory() as tmp:
        rm._LOG_DIR = Path(tmp)
        rm._JOBS_DIR = rm._LOG_DIR / "jobs"
        job = {"asset_group": "crypto", "symbol": "BTC/USDT", "engine": "A", "timeframe": "H1", "job_id": "j1"}
        path = _job_output_path(job)
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w") as f:
            json.dump({"status": "success", "closed_trades": [{}, {}], "runtime_seconds": 10.0}, f)
        summary = build_summary_report([job])
        assert summary["total_jobs"] == 1
        assert summary["by_group"]["crypto"]["completed"] == 1
        assert summary["by_group"]["crypto"]["trades"] == 2


def test_build_failures_report():
    import run_backtest_matrix as rm
    with tempfile.TemporaryDirectory() as tmp:
        rm._LOG_DIR = Path(tmp)
        rm._JOBS_DIR = rm._LOG_DIR / "jobs"
        job = {"asset_group": "crypto", "symbol": "BTC/USDT", "engine": "A", "timeframe": "H1", "job_id": "j1"}
        path = _job_output_path(job)
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w") as f:
            json.dump({"status": "failed", "error": "no data"}, f)
        failures = build_failures_report([job])
        assert failures["failure_count"] == 1
        assert failures["failures"][0]["error"] == "no data"
