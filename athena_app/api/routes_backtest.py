"""Backtest API route helpers and read-only history route registration."""

from __future__ import annotations

import sqlite3
from types import SimpleNamespace

from flask import jsonify


_RUNTIME = SimpleNamespace()


# v3 rebuild: history reads come from backtest_runs_v3 (written by
# athena_backtest.results). Legacy field aliases (run_date, pair, trades,
# expectancy) are kept so existing panel code continues to render rows.
_V3_HISTORY_COLUMNS = (
    "id, created_at AS run_date, symbol AS pair, symbol, engine, style, "
    "total_trades AS trades, win_rate, profit_factor, expectancy_r AS expectancy, "
    "sqn, deflated_sqn, total_r, max_drawdown_r, verdict, trial_count, wall_time_sec"
)


def api_backtest_history():
    """Return stored v3 backtest runs, newest first."""
    try:
        with sqlite3.connect(_RUNTIME.AUDIT_DB, timeout=15.0) as con:
            con.row_factory = sqlite3.Row
            rows = con.execute(f"""
                SELECT {_V3_HISTORY_COLUMNS} FROM backtest_runs_v3
                ORDER BY created_at DESC
                LIMIT 500
            """).fetchall()
            return jsonify([dict(r) for r in rows])
    except Exception as e:
        return jsonify({"error": str(e)}), 500


def api_backtest_history_pair(pair_name):
    """Return v3 backtest history for a specific pair."""
    try:
        with sqlite3.connect(_RUNTIME.AUDIT_DB, timeout=15.0) as con:
            con.row_factory = sqlite3.Row
            rows = con.execute(
                f"""
                SELECT {_V3_HISTORY_COLUMNS} FROM backtest_runs_v3
                WHERE symbol = ?
                ORDER BY created_at DESC
                LIMIT 50
            """,
                (pair_name,),
            ).fetchall()
            return jsonify([dict(r) for r in rows])
    except Exception as e:
        return jsonify({"error": str(e)}), 500


def api_backtest_best():
    """Return best v3 result per pair (most recent run per symbol, by SQN)."""
    try:
        with sqlite3.connect(_RUNTIME.AUDIT_DB, timeout=15.0) as con:
            con.row_factory = sqlite3.Row
            rows = con.execute(f"""
                SELECT {_V3_HISTORY_COLUMNS}
                FROM backtest_runs_v3 b
                INNER JOIN (
                    SELECT symbol AS latest_symbol, MAX(created_at) as latest
                    FROM backtest_runs_v3
                    GROUP BY symbol
                ) latest ON b.symbol = latest.latest_symbol
                AND b.created_at = latest.latest
                ORDER BY b.sqn DESC
            """).fetchall()
            return jsonify([dict(r) for r in rows])
    except Exception as e:
        return jsonify({"error": str(e)}), 500


def register_backtest_history_routes(app, runtime: SimpleNamespace) -> None:
    """Register read-only backtest history routes using athena.py runtime state."""
    global _RUNTIME

    _RUNTIME = runtime

    app.add_url_rule("/api/v3/backtest-history", "api_backtest_history", api_backtest_history)
    app.add_url_rule(
        "/api/v3/backtest-history/<pair_name>",
        "api_backtest_history_pair",
        api_backtest_history_pair,
    )
    app.add_url_rule("/api/v3/backtest-best", "api_backtest_best", api_backtest_best)
