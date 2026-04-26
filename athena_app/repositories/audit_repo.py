"""Audit repository extraction."""

from __future__ import annotations

import sqlite3


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
    
    with sqlite3.connect(audit_db, timeout=15.0) as con:
        con.execute(
            "INSERT INTO audit_log("
            "ts,pair,score,direction,style,grade,error_tag,"
            "entry_price,sl,tp,volume,risk_amount,risk_pct,warnings_json"
            ") VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
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
                json.dumps(telemetry),
            ),
        )

