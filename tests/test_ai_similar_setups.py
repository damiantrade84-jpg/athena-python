import sqlite3

from ai_similar_setups import find_similar_setups


def test_missing_db_returns_unavailable(tmp_path):
    result = find_similar_setups({"symbol": "EUR/USD", "audit_db": str(tmp_path / "missing.db")})

    assert result["reliability"] == "unavailable"
    assert result["aggregate_sample_size"] == 0
    assert result["warning"]


def test_sample_under_twenty_is_insufficient(tmp_path):
    db = tmp_path / "audit.db"
    with sqlite3.connect(db) as con:
        con.execute(
            """CREATE TABLE learning_log (
                id INTEGER PRIMARY KEY,
                ts TEXT,
                pair TEXT,
                asset_type TEXT,
                direction TEXT,
                engine TEXT,
                r_multiple REAL,
                win INTEGER,
                regime TEXT
            )"""
        )
        for idx in range(3):
            con.execute(
                "INSERT INTO learning_log(ts,pair,asset_type,direction,engine,r_multiple,win,regime) VALUES(?,?,?,?,?,?,?,?)",
                (f"2026-05-14T00:0{idx}:00Z", "EUR/USD", "forex", "LONG", "engine_a", 1.0 if idx else -1.0, 1 if idx else 0, "trend"),
            )

    result = find_similar_setups({"symbol": "EUR/USD", "asset_type": "forex", "direction": "LONG", "audit_db": str(db)})

    assert result["aggregate_sample_size"] == 3
    assert result["reliability"] == "insufficient"
    assert result["examples"]
    assert set(result) >= {
        "examples",
        "aggregate_sample_size",
        "win_rate",
        "avg_r",
        "profit_factor",
        "oos_decay",
        "reliability",
        "warning",
        "missing_fields",
    }
