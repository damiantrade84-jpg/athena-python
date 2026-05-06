"""Audit repository extraction."""

from __future__ import annotations

import sqlite3

from sqlite_instrumentation import timed_sqlite_connect, timed_sqlite_execute_write


def insert_manual_error(
    audit_db: str,
    *,
    ts: str,
    pair: str,
    score: float,
    direction: str,
    style: str,
    error_tag: str,
    entry_price: float | None = None,
    sl: float | None = None,
    tp: float | None = None,
    volume: float | None = None,
    risk_amount: float | None = None,
    risk_pct: float | None = None,
) -> None:
    import json
    from telemetry import build_strategy_lab_telemetry

    telemetry = build_strategy_lab_telemetry(
        engine=None,
        strategy_family="UNKNOWN",
        regime=None,
        setup_type=None,
        timeframe=None,
        failure_reason=error_tag,
        entry_reason=None,
        exit_reason="MANUAL_OPERATOR_CLOSE",
        source_module="audit_repo",
        source_function="insert_manual_error",
    )
    
    with timed_sqlite_connect(audit_db, timeout=15.0, label="audit_repo.manual_error.connect") as con:
        columns = {row[1] for row in con.execute("PRAGMA table_info(audit_log)").fetchall()}
        base_columns = (
            "ts",
            "pair",
            "score",
            "direction",
            "style",
            "grade",
            "error_tag",
            "entry_price",
            "sl",
            "tp",
            "volume",
            "risk_amount",
            "risk_pct",
        )
        base_values = (
            ts,
            pair,
            score,
            direction,
            style,
            "MANUAL-ERR",
            error_tag,
            entry_price,
            sl,
            tp,
            volume,
            risk_amount,
            risk_pct,
        )
        if "warnings_json" in columns:
            insert_columns = base_columns + ("warnings_json",)
            insert_values = base_values + (json.dumps(telemetry),)
        else:
            insert_columns = base_columns
            insert_values = base_values
        placeholders = ",".join("?" for _ in insert_columns)
        timed_sqlite_execute_write(
            con,
            f"INSERT INTO audit_log({','.join(insert_columns)}) VALUES({placeholders})",
            insert_values,
            label="audit_repo.manual_error.insert",
        )


def get_outcome_summary_by_regime(
    db_path: str,
    pair: str,
    regime: str,
    engine: str,
    lookback_days: int = 90,
) -> dict:
    """Return outcome summary for a specific pair + regime + engine combination.

    Used by Conductor for dynamic engine weighting.
    """
    from datetime import datetime, timezone, timedelta
    cutoff = (datetime.now(timezone.utc) - timedelta(days=lookback_days)).isoformat()
    
    try:
        with sqlite3.connect(db_path, timeout=10.0) as con:
            con.row_factory = sqlite3.Row
            row = con.execute(
                """SELECT COUNT(*) as total, SUM(win) as wins, AVG(r_multiple) as avg_r
                   FROM learning_log
                   WHERE pair = ? AND regime = ? AND engine = ?
                   AND ts > ? AND (r_multiple IS NULL OR abs(r_multiple) <= 50)
                   AND outcome IS NOT NULL""",
                (pair, regime, engine, cutoff),
            ).fetchone()
            if row and row["total"] and row["total"] > 0:
                return {
                    "total": row["total"],
                    "wins": row["wins"] or 0,
                    "win_rate": (row["wins"] or 0) / row["total"],
                    "avg_r": row["avg_r"] or 0.0,
                    "valid": True,
                }
    except Exception:
        pass
    return {"total": 0, "wins": 0, "win_rate": 0.0, "avg_r": 0.0, "valid": False}

