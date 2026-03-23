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
) -> None:
    with sqlite3.connect(audit_db, timeout=15.0) as con:
        con.execute(
            "INSERT INTO audit_log(ts,pair,score,direction,style,grade,error_tag) VALUES(?,?,?,?,?,?,?)",
            (ts, pair, score, direction, style, "MANUAL-ERR", error_tag),
        )

