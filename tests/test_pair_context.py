from __future__ import annotations

import sqlite3

import pair_context


def test_unknown_symbol_returns_stable_object(monkeypatch, tmp_path):
    monkeypatch.setenv("ATHENA_AUDIT_DB", str(tmp_path / "missing.db"))
    out = pair_context.build_pair_context("", None)
    assert out["symbol"] == "unknown"
    assert out["freshness_status"] == "unavailable"
    assert "symbol" in out["missing_fields"]


def test_recent_trades_are_included(monkeypatch, tmp_path):
    db = tmp_path / "audit.db"
    with sqlite3.connect(db) as con:
        con.execute(
            "CREATE TABLE learning_log(ts TEXT, pair TEXT, asset_type TEXT, direction TEXT, engine TEXT, r_multiple REAL, win INTEGER)"
        )
        con.execute(
            "INSERT INTO learning_log VALUES('2026-05-01T00:00:00Z','EUR/USD','forex','LONG','engine_a',1.2,1)"
        )
    monkeypatch.setenv("ATHENA_AUDIT_DB", str(db))
    out = pair_context.build_pair_context("EUR/USD", "forex")
    assert out["recent_trade_outcomes"][0]["r_multiple"] == 1.2
    assert out["freshness_status"] == "partial"


def test_audit_log_fallback_is_used_when_learning_log_absent(monkeypatch, tmp_path):
    db = tmp_path / "audit.db"
    with sqlite3.connect(db) as con:
        con.execute(
            "CREATE TABLE audit_log(pair TEXT, direction TEXT, entry_price REAL, exit_price REAL, r_multiple REAL)"
        )
        con.execute("INSERT INTO audit_log VALUES('GBP/USD','SHORT',1.25,1.24,1.5)")
    monkeypatch.setenv("ATHENA_AUDIT_DB", str(db))

    out = pair_context.build_pair_context("GBP/USD", "forex")

    assert out["recent_trade_outcomes"] == [
        {
            "pair": "GBP/USD",
            "direction": "SHORT",
            "entry_price": 1.25,
            "exit_price": 1.24,
            "r_multiple": 1.5,
        }
    ]
    assert out["freshness_status"] == "partial"


def test_incompatible_audit_tables_return_warning_not_exception(monkeypatch, tmp_path):
    db = tmp_path / "audit.db"
    with sqlite3.connect(db) as con:
        con.execute("CREATE TABLE audit_log(symbol TEXT, note TEXT)")
    monkeypatch.setenv("ATHENA_AUDIT_DB", str(db))

    out = pair_context.build_pair_context("USD/JPY", "forex")

    assert out["recent_trade_outcomes"] == []
    assert "No compatible learning_log or audit_log outcome table found." in out["warnings"]
