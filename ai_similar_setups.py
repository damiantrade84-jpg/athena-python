"""Read-only scaffold for similar historical AI trade setups.

This module never creates, mutates, or backfills learning data. It only reads
existing ATHENA learning/audit tables when available and returns a stable shape
that AI review can consume safely.
"""

from __future__ import annotations

import os
import sqlite3
from typing import Any


def _empty(reliability: str, warning: str | None, missing_fields: list[str] | None = None) -> dict[str, Any]:
    return {
        "examples": [],
        "aggregate_sample_size": 0,
        "win_rate": None,
        "avg_r": None,
        "profit_factor": None,
        "oos_decay": None,
        "reliability": reliability,
        "warning": warning,
        "missing_fields": missing_fields or [],
    }


def _value(payload: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        if key in payload and payload[key] not in (None, ""):
            return payload[key]
    market = payload.get("market_context")
    if isinstance(market, dict):
        for key in keys:
            if key in market and market[key] not in (None, ""):
                return market[key]
    return None


def _db_path(payload: dict[str, Any]) -> str:
    return str(
        payload.get("audit_db")
        or payload.get("_audit_db")
        or os.environ.get("ATHENA_AUDIT_DB")
        or os.path.join(os.path.dirname(__file__), "audit.db")
    )


def _table_exists(con: sqlite3.Connection, table: str) -> bool:
    row = con.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
        (table,),
    ).fetchone()
    return bool(row)


def _columns(con: sqlite3.Connection, table: str) -> set[str]:
    return {str(row[1]) for row in con.execute(f"PRAGMA table_info({table})").fetchall()}


def _reliability(sample_size: int) -> tuple[str, str | None]:
    if sample_size < 20:
        return "insufficient", "Fewer than 20 historical examples; do not calibrate probability from outcomes."
    if sample_size < 50:
        return "weak", None
    if sample_size < 150:
        return "moderate", None
    return "strong", None


def _profit_factor(rows: list[sqlite3.Row]) -> float | None:
    gains = sum(float(row["r_multiple"] or 0.0) for row in rows if float(row["r_multiple"] or 0.0) > 0)
    losses = abs(sum(float(row["r_multiple"] or 0.0) for row in rows if float(row["r_multiple"] or 0.0) < 0))
    if losses <= 0:
        return None if gains <= 0 else round(gains, 4)
    return round(gains / losses, 4)


def find_similar_setups(packet_or_signal: dict, limit: int = 5) -> dict[str, Any]:
    """Return historical examples and aggregates from existing learning data.

    Missing DB/table is a normal unavailable state, not an AI-review failure.
    """
    payload = packet_or_signal if isinstance(packet_or_signal, dict) else {}
    db_path = _db_path(payload)
    missing_fields: list[str] = []
    symbol = _value(payload, "symbol", "pair", "display")
    asset_type = _value(payload, "asset_type", "type")
    direction = _value(payload, "direction")
    engine = _value(payload, "engine_source", "engine")
    for name, value in (("symbol", symbol), ("asset_type", asset_type), ("direction", direction)):
        if not value:
            missing_fields.append(name)

    if not db_path or not os.path.exists(db_path):
        return _empty("unavailable", "Audit DB is unavailable for similar-setup lookup.", missing_fields)

    try:
        with sqlite3.connect(db_path, timeout=15.0) as con:
            con.row_factory = sqlite3.Row
            if not _table_exists(con, "learning_log"):
                return _empty("unavailable", "learning_log table is unavailable.", missing_fields)
            cols = _columns(con, "learning_log")
            required_cols = {"ts", "pair", "asset_type", "direction", "engine", "r_multiple", "win"}
            if not required_cols.issubset(cols):
                return _empty(
                    "unavailable",
                    "learning_log table is missing required outcome columns.",
                    sorted(required_cols - cols) + missing_fields,
                )

            where = ["r_multiple IS NOT NULL", "abs(r_multiple) <= 50"]
            params: list[Any] = []
            if symbol:
                where.append("pair = ?")
                params.append(str(symbol))
            if asset_type:
                where.append("asset_type = ?")
                params.append(str(asset_type))
            if direction:
                where.append("upper(direction) = upper(?)")
                params.append(str(direction))
            if engine:
                where.append("(engine IS NULL OR lower(engine) = lower(?))")
                params.append(str(engine))

            rows = con.execute(
                f"SELECT * FROM learning_log WHERE {' AND '.join(where)} ORDER BY ts DESC LIMIT 500",
                params,
            ).fetchall()
            if not rows and asset_type:
                rows = con.execute(
                    "SELECT * FROM learning_log WHERE r_multiple IS NOT NULL AND abs(r_multiple) <= 50 "
                    "AND asset_type = ? ORDER BY ts DESC LIMIT 500",
                    (str(asset_type),),
                ).fetchall()
    except Exception as exc:
        return _empty("unavailable", f"Similar-setup lookup failed: {exc}", missing_fields)

    sample_size = len(rows)
    if sample_size == 0:
        return _empty("unavailable", "No historical learning examples matched this setup.", missing_fields)

    wins = sum(1 for row in rows if int(row["win"] or 0) == 1)
    r_values = [float(row["r_multiple"] or 0.0) for row in rows]
    reliability, warning = _reliability(sample_size)
    examples = [
        {
            "ts": row["ts"],
            "pair": row["pair"],
            "asset_type": row["asset_type"],
            "direction": row["direction"],
            "engine": row["engine"],
            "r_multiple": row["r_multiple"],
            "win": bool(row["win"]),
            "regime": row["regime"] if "regime" in row.keys() else None,
        }
        for row in rows[: max(0, int(limit or 5))]
    ]
    return {
        "examples": examples,
        "aggregate_sample_size": sample_size,
        "win_rate": round(wins / sample_size, 4) if sample_size else None,
        "avg_r": round(sum(r_values) / sample_size, 4) if sample_size else None,
        "profit_factor": _profit_factor(rows),
        "oos_decay": None,
        "reliability": reliability,
        "warning": warning,
        "missing_fields": missing_fields,
    }
