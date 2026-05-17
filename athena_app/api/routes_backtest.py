"""Backtest API route helpers and read-only history route registration."""

from __future__ import annotations

import sqlite3
from types import SimpleNamespace

from flask import jsonify


_RUNTIME = SimpleNamespace()


def api_backtest_impl(payload: dict, *, service_handle):
    return service_handle(payload)


def api_backtest_history():
    """Return all stored backtest results, newest first."""
    try:
        with sqlite3.connect(_RUNTIME.AUDIT_DB, timeout=15.0) as con:
            con.row_factory = sqlite3.Row
            rows = con.execute("""
                SELECT * FROM backtest_results
                ORDER BY run_date DESC
                LIMIT 500
            """).fetchall()
            return jsonify([dict(r) for r in rows])
    except Exception as e:
        return jsonify({"error": str(e)}), 500


def api_backtest_history_pair(pair_name):
    """Return backtest history for a specific pair."""
    try:
        with sqlite3.connect(_RUNTIME.AUDIT_DB, timeout=15.0) as con:
            con.row_factory = sqlite3.Row
            rows = con.execute(
                """
                SELECT * FROM backtest_results
                WHERE pair = ?
                ORDER BY run_date DESC
                LIMIT 50
            """,
                (pair_name,),
            ).fetchall()
            return jsonify([dict(r) for r in rows])
    except Exception as e:
        return jsonify({"error": str(e)}), 500


def api_backtest_best():
    """Return best result per pair (highest SQN from most recent run)."""
    try:
        with sqlite3.connect(_RUNTIME.AUDIT_DB, timeout=15.0) as con:
            con.row_factory = sqlite3.Row
            rows = con.execute("""
                SELECT b.*
                FROM backtest_results b
                INNER JOIN (
                    SELECT pair, MAX(run_date) as latest
                    FROM backtest_results
                    GROUP BY pair
                ) latest ON b.pair = latest.pair
                AND b.run_date = latest.latest
                ORDER BY b.sqn DESC
            """).fetchall()
            return jsonify([dict(r) for r in rows])
    except Exception as e:
        return jsonify({"error": str(e)}), 500


def register_backtest_history_routes(app, runtime: SimpleNamespace) -> None:
    """Register read-only backtest history routes using athena.py runtime state."""
    global _RUNTIME

    _RUNTIME = runtime

    app.add_url_rule("/api/backtest-history", "api_backtest_history", api_backtest_history)
    app.add_url_rule(
        "/api/backtest-history/<pair_name>",
        "api_backtest_history_pair",
        api_backtest_history_pair,
    )
    app.add_url_rule("/api/backtest-best", "api_backtest_best", api_backtest_best)
