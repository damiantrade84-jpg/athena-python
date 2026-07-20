"""Persistence for rebuilt-backtester (v3) runs.

Own tables (`backtest_runs_v3` / `backtest_trades_v3`) in the existing
audit.db. The legacy `backtest_results` table and any `*_v2` tables from the
separately-built backtesting-v2 platform are never read or written here.
"""

from __future__ import annotations

import json
import os
import sqlite3
from datetime import datetime, timezone

_DEFAULT_DB = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "audit.db")

_SCHEMA = (
    """
    CREATE TABLE IF NOT EXISTS backtest_runs_v3 (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        created_at TEXT NOT NULL,
        symbol TEXT NOT NULL,
        engine TEXT NOT NULL,
        style TEXT NOT NULL,
        contract_version TEXT,
        wall_time_sec REAL,
        total_trades INTEGER,
        win_rate REAL,
        sqn REAL,
        deflated_sqn REAL,
        profit_factor REAL,
        expectancy_r REAL,
        total_r REAL,
        max_drawdown_r REAL,
        verdict TEXT,
        trial_count INTEGER,
        same_bar_policy TEXT,
        result_json TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS backtest_trades_v3 (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        run_id INTEGER NOT NULL REFERENCES backtest_runs_v3(id),
        seq INTEGER NOT NULL,
        entry_time TEXT,
        direction TEXT,
        entry REAL,
        sl REAL,
        tp1 REAL,
        tp2 REAL,
        outcome TEXT,
        result_r REAL,
        trade_json TEXT NOT NULL
    )
    """,
    "CREATE INDEX IF NOT EXISTS ix_bt_runs_v3_symbol ON backtest_runs_v3(symbol, engine)",
    "CREATE INDEX IF NOT EXISTS ix_bt_trades_v3_run ON backtest_trades_v3(run_id)",
)


def _connect(db_path: str | None = None) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path or _DEFAULT_DB, timeout=15.0)
    for statement in _SCHEMA:
        conn.execute(statement)
    return conn


def _f(value) -> float | None:
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return out if out == out else None


def save_run(
    result: dict,
    *,
    pair: dict,
    engine: str,
    style: str,
    db_path: str | None = None,
) -> int:
    """Persist one run + its trades; returns the run id."""
    trades = result.get("trades") or []
    metrics = result.get("metricsV3") or {}
    validation = result.get("validation") or {}
    stored_result = {k: v for k, v in result.items() if k != "trades"}
    with _connect(db_path) as conn:
        cursor = conn.execute(
            "INSERT INTO backtest_runs_v3 "
            "(created_at,symbol,engine,style,contract_version,wall_time_sec,"
            "total_trades,win_rate,sqn,deflated_sqn,profit_factor,expectancy_r,"
            "total_r,max_drawdown_r,verdict,trial_count,same_bar_policy,result_json) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                datetime.now(timezone.utc).isoformat(),
                str(pair.get("display") or pair.get("symbol") or ""),
                str(engine).upper(),
                str(style).lower(),
                str(result.get("contractVersion") or ""),
                _f(result.get("wallTimeSec")),
                int(result.get("totalTrades") or len(trades)),
                _f(result.get("winRate")),
                _f(result.get("sqn")),
                _f(metrics.get("deflatedSqn")),
                _f(result.get("profitFactor")),
                _f(metrics.get("expectancyR")),
                _f(result.get("totalR")),
                _f(result.get("maxDrawdownR")),
                str(validation.get("verdict") or ""),
                int(validation.get("trialCount") or 1),
                str(result.get("sameBarPolicy") or ""),
                json.dumps(stored_result, default=str),
            ),
        )
        run_id = int(cursor.lastrowid)
        conn.executemany(
            "INSERT INTO backtest_trades_v3 "
            "(run_id,seq,entry_time,direction,entry,sl,tp1,tp2,outcome,result_r,trade_json) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            [
                (
                    run_id,
                    seq,
                    str(trade.get("entryTime") or trade.get("date") or ""),
                    str(trade.get("direction") or ""),
                    _f(trade.get("entry")),
                    _f(trade.get("sl")),
                    _f(trade.get("tp1")),
                    _f(trade.get("tp2") or trade.get("tp")),
                    str(trade.get("outcome") or ""),
                    _f(trade.get("resultR")),
                    json.dumps(trade, default=str),
                )
                for seq, trade in enumerate(trades)
            ],
        )
        conn.commit()
    return run_id


def recent_runs(limit: int = 50, *, db_path: str | None = None) -> list[dict]:
    with _connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT id,created_at,symbol,engine,style,total_trades,win_rate,sqn,"
            "deflated_sqn,profit_factor,total_r,max_drawdown_r,verdict,trial_count,"
            "wall_time_sec "
            "FROM backtest_runs_v3 ORDER BY id DESC LIMIT ?",
            (max(1, min(500, int(limit))),),
        ).fetchall()
    return [dict(row) for row in rows]


def run_detail(run_id: int, *, db_path: str | None = None) -> dict | None:
    with _connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        run = conn.execute(
            "SELECT * FROM backtest_runs_v3 WHERE id = ?", (int(run_id),)
        ).fetchone()
        if run is None:
            return None
        trades = conn.execute(
            "SELECT trade_json FROM backtest_trades_v3 WHERE run_id = ? ORDER BY seq",
            (int(run_id),),
        ).fetchall()
    payload = dict(run)
    payload["result"] = json.loads(payload.pop("result_json"))
    payload["trades"] = [json.loads(row["trade_json"]) for row in trades]
    return payload
