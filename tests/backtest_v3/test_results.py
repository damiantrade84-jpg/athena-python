"""Stage 6/7 verification: v3 result persistence round-trip in its own tables."""

from __future__ import annotations

import os
import sqlite3
import tempfile

from athena_backtest.results import recent_runs, run_detail, save_run


def _db_path() -> str:
    # tmp_path fixture is environmentally broken in this repo (.pytest_tmp
    # lock); use tempfile.mkdtemp per project convention.
    return os.path.join(tempfile.mkdtemp(prefix="bt_v3_results_"), "audit.db")


def test_save_and_read_back_round_trip():
    db = _db_path()
    result = {
        "contractVersion": "engine_a.v3",
        "wallTimeSec": 12.3,
        "totalTrades": 2,
        "winRate": 50.0,
        "sqn": 1.1,
        "profitFactor": 1.4,
        "totalR": 0.8,
        "maxDrawdownR": 1.0,
        "sameBarPolicy": "ADVERSE_SL_FIRST",
        "metricsV3": {"deflatedSqn": 0.9, "expectancyR": 0.4},
        "validation": {"verdict": "INSUFFICIENT_SAMPLE", "trialCount": 3},
        "trades": [
            {"entryTime": "2025-01-01T00:00:00+00:00", "direction": "LONG",
             "entry": 100.0, "sl": 99.0, "tp1": 101.0, "tp2": 102.0,
             "outcome": "TP1", "resultR": 1.0},
            {"date": "2025-01-02T00:00:00+00:00", "direction": "SHORT",
             "entry": 100.0, "sl": 101.0, "tp": 98.0,
             "outcome": "SL", "resultR": -0.2},
        ],
    }
    pair = {"display": "EUR/USD", "symbol": "EURUSD", "type": "forex"}

    run_id = save_run(result, pair=pair, engine="A", style="intraday", db_path=db)
    assert run_id >= 1

    rows = recent_runs(db_path=db)
    assert len(rows) == 1
    assert rows[0]["symbol"] == "EUR/USD"
    assert rows[0]["deflated_sqn"] == 0.9
    assert rows[0]["verdict"] == "INSUFFICIENT_SAMPLE"

    detail = run_detail(run_id, db_path=db)
    assert detail is not None
    assert detail["result"]["contractVersion"] == "engine_a.v3"
    assert "trades" not in detail["result"]  # trades live in their own table
    assert len(detail["trades"]) == 2
    assert detail["trades"][1]["outcome"] == "SL"

    # v3 tables only -- the legacy/v2 tables are never created or touched.
    with sqlite3.connect(db) as conn:
        names = {
            row[0]
            for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
    assert "backtest_runs_v3" in names and "backtest_trades_v3" in names
    assert "backtest_results" not in names
    assert not any(name.endswith("_v2") for name in names)
