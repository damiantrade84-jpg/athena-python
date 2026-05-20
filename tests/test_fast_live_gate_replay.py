from __future__ import annotations

import json
import shutil
import sys
import tempfile
from pathlib import Path

import numpy as np
import pandas as pd
import pytest


@pytest.fixture
def tmp_path():
    d = Path(tempfile.mkdtemp(prefix="athena_live_gate_replay_test_"))
    try:
        yield d
    finally:
        shutil.rmtree(d, ignore_errors=True)


def _make_ohlcv(periods: int = 160, freq: str = "4h") -> pd.DataFrame:
    rng = np.random.default_rng(13)
    idx = pd.date_range("2025-01-01", periods=periods, freq=freq, tz="UTC")
    close = 100 + rng.normal(0, 1, periods).cumsum()
    return pd.DataFrame(
        {
            "open": close + rng.normal(0, 0.2, periods),
            "high": close + rng.uniform(0.2, 1.0, periods),
            "low": close - rng.uniform(0.2, 1.0, periods),
            "close": close,
            "volume": rng.uniform(1000, 5000, periods),
        },
        index=idx,
    )


def test_fast_live_gate_replay_import_does_not_pull_execution_modules():
    baseline = {name for name in {"execution", "auto_trader", "mt5_executor", "bybit_executor"} if name in sys.modules}

    import athena_research.fast_live_gate_replay  # noqa: F401

    violations = [
        name
        for name in {"execution", "auto_trader", "mt5_executor", "bybit_executor"}
        if name in sys.modules and name not in baseline
    ]
    assert violations == []


def test_candle_conversion_preserves_utc_time_order():
    from athena_research.fast_live_gate_replay import dataframe_to_candles

    candles = dataframe_to_candles(_make_ohlcv(3))

    assert [c["time"] for c in candles] == sorted(c["time"] for c in candles)
    assert set(candles[0]) >= {"time", "open", "high", "low", "close", "volume"}


def test_run_fast_live_gate_replay_writes_reports(tmp_path, monkeypatch):
    from athena_research import fast_live_gate_replay as replay

    h4 = _make_ohlcv(180, "4h")
    h1 = _make_ohlcv(720, "1h")
    d1 = _make_ohlcv(120, "1D")

    def fake_load_frames(config):
        return {
            ("BTC/USDT", "H4"): h4,
            ("BTC/USDT", "H1"): h1,
            ("BTC/USDT", "D1"): d1,
        }, {
            "BTC/USDT|H4": {"data_source": "synthetic_test", "cached": False},
            "BTC/USDT|H1": {"data_source": "synthetic_test", "cached": False},
            "BTC/USDT|D1": {"data_source": "synthetic_test", "cached": False},
        }

    def fake_replay_symbol_engine(config, symbol, engine, timeframe, frames):
        return [
            {
                "engine": engine,
                "symbol": symbol,
                "timeframe": timeframe,
                "bars_evaluated": 10,
                "passes": 2 if engine == "A" else 1,
                "pass_rate": 0.2 if engine == "A" else 0.1,
                "near_miss": 3,
                "top_blocker": "below_score" if engine == "A" else "not_clear",
                "threshold": 2.0 if engine == "A" else 4.0,
                "max_score": 2.3,
                "mean_score": 1.4,
                "directions": "LONG=1;SHORT=1",
            }
        ], [
            {
                "engine": engine,
                "symbol": symbol,
                "timeframe": timeframe,
                "bar_time": "2025-01-01T00:00:00+00:00",
                "passed": engine == "A",
                "score": 2.2 if engine == "A" else 3.8,
                "threshold": 2.0 if engine == "A" else 4.0,
                "direction": "LONG",
                "blocker": "" if engine == "A" else "below_score",
            }
        ]

    monkeypatch.setattr(replay, "load_replay_frames", fake_load_frames)
    monkeypatch.setattr(replay, "_replay_symbol_engine", fake_replay_symbol_engine)

    result = replay.run_fast_live_gate_replay(
        replay.LiveGateReplayConfig(
            output_dir=tmp_path,
            run_id="unit_replay",
            symbols=("BTC/USDT",),
            engines=("A", "B"),
            timeframes=("H4",),
            lookback_days=30,
        )
    )

    run_dir = tmp_path / "unit_replay"
    assert result["run_dir"] == run_dir
    for name in {
        "run_meta.json",
        "gate_pass_matrix.csv",
        "blocker_summary.csv",
        "pair_timeframe_summary.csv",
        "top_replay_candidates.csv",
        "outcome_trades.csv",
        "outcome_summary.csv",
        "report.html",
    }:
        assert (run_dir / name).exists(), name

    meta = json.loads((run_dir / "run_meta.json").read_text(encoding="utf-8"))
    assert meta["read_only"] is True
    assert meta["live_gate_replay"] is True
    assert meta["engines"] == ["A", "B"]
    assert meta["outcome_bars"] == [6, 12, 24]


def test_forward_outcome_summary_calculates_wr_and_sqn():
    from athena_research import fast_live_gate_replay as replay

    df = pd.DataFrame(
        {
            "open": [100, 101, 102, 103],
            "high": [101, 103, 104, 105],
            "low": [99, 100, 101, 102],
            "close": [100, 102, 101, 104],
            "volume": [1, 1, 1, 1],
        },
        index=pd.date_range("2025-01-01", periods=4, freq="4h", tz="UTC"),
    )
    details = [
        {
            "engine": "A",
            "symbol": "BTC/USDT",
            "timeframe": "H4",
            "bar_time": df.index[0].isoformat(),
            "passed": True,
            "direction": "LONG",
            "score": 2.1,
            "threshold": 2.0,
        },
        {
            "engine": "A",
            "symbol": "BTC/USDT",
            "timeframe": "H4",
            "bar_time": df.index[2].isoformat(),
            "passed": True,
            "direction": "SHORT",
            "score": 2.1,
            "threshold": 2.0,
        },
    ]

    trades, summary = replay._outcome_dataframes(details, {("BTC/USDT", "H4"): df}, (1,))

    assert len(trades) == 2
    row = summary.iloc[0]
    assert row["trades"] == 2
    assert row["wr"] == 0.5
    assert "sqn" in summary.columns
