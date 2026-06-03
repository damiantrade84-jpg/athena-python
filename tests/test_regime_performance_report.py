import builtins
import json
import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

import regime_performance_report as report


def _create_learning_log(db_path):
    with sqlite3.connect(db_path, timeout=15.0) as con:
        con.execute("PRAGMA journal_mode=WAL")
        con.execute(
            """CREATE TABLE learning_log (
                ts TEXT,
                pair TEXT,
                asset_type TEXT,
                direction TEXT,
                engine TEXT,
                r_multiple REAL,
                win INTEGER,
                score_pct REAL,
                ai_grade TEXT,
                regime TEXT
            )"""
        )
        con.commit()


def test_build_regime_performance_buckets_closed_trade_metrics():
    rows = [
        {
            "score_group": "Forex_Majors",
            "regime": "ranging",
            "direction": "buy",
            "engine": "Engine A",
            "r_multiple": 1.5,
            "win": 1,
            "score_pct": 0.82,
            "ai_grade": "A",
        },
        {
            "score_group": "forex_majors",
            "regime": "RANGING",
            "direction": "LONG",
            "engine": "engine-a",
            "r_multiple": -0.5,
            "win": 0,
            "score_pct": 0.61,
            "ai_grade": "b",
        },
        {
            "score_group": "forex_majors",
            "regime": "RANGING",
            "direction": "LONG",
            "engine": "engine_a",
            "r_multiple": 0.2,
            "win": None,
            "score_pct": None,
            "ai_grade": "",
        },
        {
            "score_group": "forex_majors",
            "regime": "RANGING",
            "direction": "LONG",
            "engine": "engine_a",
            "r_multiple": None,
            "win": 1,
        },
        {
            "score_group": "forex_majors",
            "regime": "RANGING",
            "direction": "LONG",
            "engine": "engine_a",
            "r_multiple": float("nan"),
            "win": 1,
        },
    ]

    summary = report.build_regime_performance(rows, min_trades=3)

    assert summary["total_closed_trades"] == 3
    assert summary["total_cells"] == 1
    assert summary["sufficient_cells"] == 0
    assert summary["min_trades_per_cell"] == 30
    cell = summary["cells"][0]
    assert cell["score_group"] == "forex_majors"
    assert cell["regime"] == "RANGING"
    assert cell["direction"] == "LONG"
    assert cell["engine"] == "engine_a"
    assert cell["n_closed"] == 3
    assert cell["win_rate"] == pytest.approx(0.6667)
    assert cell["avg_r"] == pytest.approx(0.4)
    assert cell["median_r"] == pytest.approx(0.2)
    assert cell["total_r"] == pytest.approx(1.2)
    assert cell["avg_score_pct"] == pytest.approx(0.715)
    assert cell["ai_grade_split"] == {"A": 1, "B": 1, "UNKNOWN": 1}
    assert cell["mae"] is None
    assert cell["mfe"] is None
    assert cell["excursion_status"] == "not_recorded"
    assert cell["sufficient"] is False


def test_sufficiency_floor_never_drops_below_minimum():
    rows = [
        {
            "score_group": "forex_majors",
            "regime": "RANGING",
            "direction": "LONG",
            "engine": "engine_a",
            "r_multiple": 1.0,
            "win": 1,
        }
        for _ in range(report.MIN_TRADES_PER_CELL - 1)
    ]

    thin = report.build_regime_performance(rows, min_trades=3)
    assert thin["cells"][0]["sufficient"] is False

    enough = report.build_regime_performance(
        rows
        + [
            {
                "score_group": "forex_majors",
                "regime": "RANGING",
                "direction": "LONG",
                "engine": "engine_a",
                "r_multiple": -0.5,
                "win": 0,
            }
        ],
        min_trades=3,
    )
    assert enough["cells"][0]["n_closed"] == report.MIN_TRADES_PER_CELL
    assert enough["cells"][0]["sufficient"] is True


def test_missing_score_group_falls_back_without_importing_scoring(monkeypatch):
    real_import = builtins.__import__

    def guarded_import(name, *args, **kwargs):
        if name == "scoring":
            raise AssertionError("scoring.py must not be imported by this report")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", guarded_import)

    summary = report.build_regime_performance(
        [
            {
                "pair": "EUR/USD",
                "asset_type": "forex",
                "regime": "RANGING",
                "direction": "LONG",
                "engine": "engine_a",
                "r_multiple": 1.0,
                "win": 1,
            }
        ]
    )

    assert summary["cells"][0]["score_group"] == "forex"


def test_blank_score_group_falls_back_to_asset_type():
    summary = report.build_regime_performance(
        [
            {
                "score_group": "   ",
                "pair": "EUR/USD",
                "asset_type": "forex",
                "regime": "RANGING",
                "direction": "LONG",
                "engine": "engine_a",
                "r_multiple": 1.0,
                "win": 1,
            }
        ]
    )

    assert summary["cells"][0]["score_group"] == "forex"


def test_malformed_win_values_fall_back_to_r_multiple():
    rows = [
        {
            "score_group": "forex_majors",
            "regime": "RANGING",
            "direction": "LONG",
            "engine": "engine_a",
            "r_multiple": -1.0,
            "win": -1,
        },
        {
            "score_group": "forex_majors",
            "regime": "RANGING",
            "direction": "LONG",
            "engine": "engine_a",
            "r_multiple": -0.5,
            "win": 2,
        },
    ]

    summary = report.build_regime_performance(rows)

    assert summary["cells"][0]["win_rate"] == pytest.approx(0.0)


def test_direct_aggregation_excludes_nonfinite_and_outlier_r_values():
    rows = [
        {
            "score_group": "forex_majors",
            "regime": "RANGING",
            "direction": "LONG",
            "engine": "engine_a",
            "r_multiple": 1.0,
            "win": 1,
        },
        {
            "score_group": "forex_majors",
            "regime": "RANGING",
            "direction": "LONG",
            "engine": "engine_a",
            "r_multiple": float("inf"),
            "win": 1,
        },
        {
            "score_group": "forex_majors",
            "regime": "RANGING",
            "direction": "LONG",
            "engine": "engine_a",
            "r_multiple": "inf",
            "win": 1,
        },
        {
            "score_group": "forex_majors",
            "regime": "RANGING",
            "direction": "LONG",
            "engine": "engine_a",
            "r_multiple": 51.0,
            "win": 1,
        },
    ]

    summary = report.build_regime_performance(rows)

    assert summary["total_closed_trades"] == 1
    assert summary["cells"][0]["total_r"] == pytest.approx(1.0)


def test_readonly_connect_uses_sqlite_uri_readonly_mode(monkeypatch, tmp_path):
    db_path = tmp_path / "audit.db"
    db_path.write_text("", encoding="utf-8")
    calls = []

    class DummyConnection:
        pass

    def fake_connect(database, **kwargs):
        calls.append((database, kwargs))
        return DummyConnection()

    monkeypatch.setattr(report.sqlite3, "connect", fake_connect)

    con = report._readonly_connect(db_path)

    assert isinstance(con, DummyConnection)
    assert calls
    database, kwargs = calls[0]
    assert str(database).startswith("file:")
    assert "mode=ro" in str(database)
    assert kwargs == {"uri": True, "timeout": 15.0}


def test_load_closed_trades_uses_readonly_mode_and_filters(tmp_path):
    db_path = tmp_path / "audit.db"
    _create_learning_log(db_path)
    now = datetime.now(timezone.utc)
    rows = [
        ((now - timedelta(days=1)).isoformat(), "EUR/USD", "forex", "LONG", "Engine A", 1.0, 1, 0.70, "A", "RANGING"),
        ((now - timedelta(days=2)).isoformat(), "BTC/USDT", "crypto", "SHORT", "engine-b", 2.0, 1, 0.80, "B", "TRENDING"),
        ((now - timedelta(days=40)).isoformat(), "GBP/USD", "forex", "LONG", "engine_a", 1.2, 1, 0.65, "A", "RANGING"),
        ((now - timedelta(days=1)).isoformat(), "USD/JPY", "forex", "LONG", "engine_a", None, 0, 0.50, "C", "RANGING"),
        ((now - timedelta(days=1)).isoformat(), "AAPL", "stock", "LONG", "engine_a", 51.0, 1, 0.90, "A", "HIGH_VOLATILITY"),
    ]
    with sqlite3.connect(db_path, timeout=15.0) as con:
        con.executemany(
            """INSERT INTO learning_log
               (ts, pair, asset_type, direction, engine, r_multiple, win, score_pct, ai_grade, regime)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            rows,
        )
        con.commit()

    loaded = report.load_closed_trades(db_path, days=7, engine="ENGINE-A")

    assert len(loaded) == 1
    assert loaded[0]["pair"] == "EUR/USD"
    assert loaded[0]["r_multiple"] == pytest.approx(1.0)


def test_load_closed_trades_returns_empty_without_creating_missing_db(tmp_path):
    db_path = tmp_path / "missing.db"

    assert report.load_closed_trades(db_path) == []
    assert not db_path.exists()


def test_load_closed_trades_skips_days_filter_when_timestamp_absent(tmp_path):
    db_path = tmp_path / "audit.db"
    with sqlite3.connect(db_path, timeout=15.0) as con:
        con.execute("PRAGMA journal_mode=WAL")
        con.execute("CREATE TABLE learning_log (pair TEXT, engine TEXT, r_multiple REAL)")
        con.execute("INSERT INTO learning_log VALUES (?, ?, ?)", ("EUR/USD", "engine_a", 0.5))
        con.commit()

    loaded = report.load_closed_trades(db_path, days=1, engine="engine_a")

    assert len(loaded) == 1
    assert loaded[0]["pair"] == "EUR/USD"


def test_report_renders_thin_sample_and_excursion_warnings():
    summary = report.build_regime_performance(
        [
            {
                "score_group": "crypto_btc",
                "regime": "RANGING",
                "direction": "SHORT",
                "engine": "engine_a",
                "r_multiple": -0.25,
                "win": 0,
                "score_pct": 72.0,
                "ai_grade": "C",
            }
        ]
    )

    markdown = report.build_regime_performance_report(summary)

    assert "Regime-Bucketed Closed-Trade Performance (READ-ONLY)" in markdown
    assert "ENGINE_A_VOLATILITY_REGIME_MULTIPLIERS at 1.00" in markdown
    assert "MAE/MFE are unavailable" in markdown
    assert "sub-1.0 RANGING multiplier" in markdown
    assert "| crypto_btc | RANGING | SHORT | engine_a | 1 | 0.0 | -0.250" in markdown
    assert "| C:1 | no |" in markdown
    assert "| no |" in markdown


def test_run_can_emit_json_for_temp_db(tmp_path):
    db_path = tmp_path / "audit.db"
    _create_learning_log(db_path)
    with sqlite3.connect(db_path, timeout=15.0) as con:
        con.execute(
            """INSERT INTO learning_log
               (ts, pair, asset_type, direction, engine, r_multiple, win, score_pct, ai_grade, regime)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                datetime.now(timezone.utc).isoformat(),
                "EUR/USD",
                "forex",
                "LONG",
                "engine_a",
                1.0,
                1,
                0.75,
                "A",
                "TRENDING",
            ),
        )
        con.commit()

    payload = json.loads(report.run(db_path, engine="engine_a"))

    assert payload["total_closed_trades"] == 1
    assert payload["cells"][0]["engine"] == "engine_a"
