"""SQLite persistence for the athena_fx lane.

Follows repo conventions (see carry_feed.py / cot_feed.py): one repo-root
SQLite file, WAL mode, 15s busy timeout. Path is overridable via the
ATHENA_FX_DB_PATH env var so tests never touch the production file.
"""
from __future__ import annotations

import json
import os
import sqlite3
from typing import Any, Iterable, Optional

_DEFAULT_DB_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "fx_factor.db"
)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS swap_audit (
    symbol TEXT NOT NULL,
    generated_at TEXT NOT NULL,
    payload TEXT NOT NULL,
    status TEXT NOT NULL,
    PRIMARY KEY (symbol, generated_at)
);
CREATE TABLE IF NOT EXISTS total_return (
    date TEXT NOT NULL,
    symbol TEXT NOT NULL,
    spot_return REAL NOT NULL,
    long_swap_return REAL NOT NULL,
    short_swap_return REAL NOT NULL,
    long_total_return REAL NOT NULL,
    short_total_return REAL NOT NULL,
    long_total_return_index REAL NOT NULL,
    short_total_return_index REAL NOT NULL,
    data_quality_status TEXT NOT NULL,
    PRIMARY KEY (date, symbol)
);
CREATE TABLE IF NOT EXISTS family_scores (
    as_of_date TEXT NOT NULL,
    symbol TEXT NOT NULL,
    family TEXT NOT NULL,
    payload TEXT NOT NULL,
    PRIMARY KEY (as_of_date, symbol, family)
);
CREATE TABLE IF NOT EXISTS decisions (
    as_of_date TEXT NOT NULL,
    symbol TEXT NOT NULL,
    payload TEXT NOT NULL,
    PRIMARY KEY (as_of_date, symbol)
);
CREATE TABLE IF NOT EXISTS reer_observations (
    currency TEXT NOT NULL,
    observation_month TEXT NOT NULL,
    reer_value REAL NOT NULL,
    available_date TEXT NOT NULL,
    lag_method TEXT NOT NULL,
    source TEXT NOT NULL,
    PRIMARY KEY (currency, observation_month)
);
CREATE TABLE IF NOT EXISTS backtest_runs (
    run_id TEXT PRIMARY KEY,
    created_at TEXT NOT NULL,
    status TEXT NOT NULL,
    request TEXT NOT NULL,
    report TEXT
);
CREATE TABLE IF NOT EXISTS scans (
    scan_id TEXT PRIMARY KEY,
    generated_at TEXT NOT NULL,
    payload TEXT NOT NULL
);
"""


def resolve_db_path() -> str:
    return os.environ.get("ATHENA_FX_DB_PATH", "").strip() or _DEFAULT_DB_PATH


def connect(db_path: Optional[str] = None) -> sqlite3.Connection:
    con = sqlite3.connect(db_path or resolve_db_path(), timeout=15)
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA busy_timeout=15000")
    con.executescript(_SCHEMA)
    con.row_factory = sqlite3.Row
    return con


# ── swap audit ────────────────────────────────────────────────────────────────

def save_swap_audit_rows(rows: Iterable[Any], db_path: Optional[str] = None) -> int:
    """Persist SwapAuditRow objects (anything with .to_dict()). Returns count."""
    n = 0
    with connect(db_path) as con:
        for row in rows:
            d = row.to_dict() if hasattr(row, "to_dict") else dict(row)
            con.execute(
                "INSERT OR REPLACE INTO swap_audit (symbol, generated_at, payload, status)"
                " VALUES (?,?,?,?)",
                (d["symbol"], d.get("generated_at") or "", json.dumps(d), d.get("status", "ok")),
            )
            n += 1
    return n


def load_latest_swap_audit(
    symbol: Optional[str] = None, db_path: Optional[str] = None
) -> list[dict[str, Any]]:
    """Latest audit row per symbol (optionally filtered to one symbol)."""
    query = (
        "SELECT payload FROM swap_audit a WHERE generated_at ="
        " (SELECT MAX(generated_at) FROM swap_audit b WHERE b.symbol = a.symbol)"
    )
    args: tuple = ()
    if symbol:
        query += " AND a.symbol = ?"
        args = (symbol,)
    with connect(db_path) as con:
        return [json.loads(r["payload"]) for r in con.execute(query, args)]


# ── total return ──────────────────────────────────────────────────────────────

def save_total_return_rows(rows: Iterable[Any], db_path: Optional[str] = None) -> int:
    n = 0
    with connect(db_path) as con:
        for row in rows:
            d = row.to_dict() if hasattr(row, "to_dict") else dict(row)
            con.execute(
                "INSERT OR REPLACE INTO total_return VALUES (?,?,?,?,?,?,?,?,?,?)",
                (
                    d["date"], d["symbol"], d["spot_return"],
                    d["long_swap_return"], d["short_swap_return"],
                    d["long_total_return"], d["short_total_return"],
                    d["long_total_return_index"], d["short_total_return_index"],
                    d["data_quality_status"],
                ),
            )
            n += 1
    return n


def load_total_return(
    symbol: str,
    start: Optional[str] = None,
    end: Optional[str] = None,
    db_path: Optional[str] = None,
) -> list[dict[str, Any]]:
    query = "SELECT * FROM total_return WHERE symbol = ?"
    args: list[Any] = [symbol]
    if start:
        query += " AND date >= ?"
        args.append(start)
    if end:
        query += " AND date <= ?"
        args.append(end)
    query += " ORDER BY date"
    with connect(db_path) as con:
        return [dict(r) for r in con.execute(query, args)]


# ── family scores / decisions ─────────────────────────────────────────────────

def save_family_scores(scores: Iterable[Any], db_path: Optional[str] = None) -> int:
    n = 0
    with connect(db_path) as con:
        for score in scores:
            d = score.to_dict() if hasattr(score, "to_dict") else dict(score)
            con.execute(
                "INSERT OR REPLACE INTO family_scores (as_of_date, symbol, family, payload)"
                " VALUES (?,?,?,?)",
                (d["as_of_date"], d["symbol"], d["family"], json.dumps(d)),
            )
            n += 1
    return n


def load_family_scores(
    as_of_date: Optional[str] = None,
    symbol: Optional[str] = None,
    family: Optional[str] = None,
    db_path: Optional[str] = None,
) -> list[dict[str, Any]]:
    query = "SELECT payload FROM family_scores WHERE 1=1"
    args: list[Any] = []
    for column, value in (("as_of_date", as_of_date), ("symbol", symbol), ("family", family)):
        if value:
            query += f" AND {column} = ?"
            args.append(value)
    query += " ORDER BY as_of_date, symbol, family"
    with connect(db_path) as con:
        return [json.loads(r["payload"]) for r in con.execute(query, args)]


def save_decisions(decisions: Iterable[Any], db_path: Optional[str] = None) -> int:
    n = 0
    with connect(db_path) as con:
        for decision in decisions:
            d = decision.to_dict() if hasattr(decision, "to_dict") else dict(decision)
            con.execute(
                "INSERT OR REPLACE INTO decisions (as_of_date, symbol, payload) VALUES (?,?,?)",
                (d["as_of_date"], d["symbol"], json.dumps(d)),
            )
            n += 1
    return n


def load_decisions(
    as_of_date: Optional[str] = None,
    symbol: Optional[str] = None,
    db_path: Optional[str] = None,
) -> list[dict[str, Any]]:
    query = "SELECT payload FROM decisions WHERE 1=1"
    args: list[Any] = []
    if as_of_date:
        query += " AND as_of_date = ?"
        args.append(as_of_date)
    if symbol:
        query += " AND symbol = ?"
        args.append(symbol)
    query += " ORDER BY as_of_date, symbol"
    with connect(db_path) as con:
        return [json.loads(r["payload"]) for r in con.execute(query, args)]


# ── REER ──────────────────────────────────────────────────────────────────────

def save_reer_observations(
    rows: Iterable[dict[str, Any]], db_path: Optional[str] = None
) -> int:
    n = 0
    with connect(db_path) as con:
        for d in rows:
            con.execute(
                "INSERT OR REPLACE INTO reer_observations VALUES (?,?,?,?,?,?)",
                (
                    d["currency"], d["observation_month"], d["reer_value"],
                    d["available_date"], d.get("lag_method", "conservative"),
                    d.get("source", "unknown"),
                ),
            )
            n += 1
    return n


def load_reer_observations(
    currency: Optional[str] = None,
    available_on_or_before: Optional[str] = None,
    db_path: Optional[str] = None,
) -> list[dict[str, Any]]:
    """Point-in-time read: only rows already available at the given date."""
    query = "SELECT * FROM reer_observations WHERE 1=1"
    args: list[Any] = []
    if currency:
        query += " AND currency = ?"
        args.append(currency)
    if available_on_or_before:
        query += " AND available_date <= ?"
        args.append(available_on_or_before)
    query += " ORDER BY currency, observation_month"
    with connect(db_path) as con:
        return [dict(r) for r in con.execute(query, args)]


# ── backtest runs ─────────────────────────────────────────────────────────────

def save_backtest_run(
    run_id: str,
    created_at: str,
    status: str,
    request: dict[str, Any],
    report: Optional[dict[str, Any]] = None,
    db_path: Optional[str] = None,
) -> None:
    with connect(db_path) as con:
        con.execute(
            "INSERT OR REPLACE INTO backtest_runs (run_id, created_at, status, request, report)"
            " VALUES (?,?,?,?,?)",
            (
                run_id, created_at, status, json.dumps(request),
                json.dumps(report) if report is not None else None,
            ),
        )


def load_backtest_run(run_id: str, db_path: Optional[str] = None) -> Optional[dict[str, Any]]:
    with connect(db_path) as con:
        row = con.execute(
            "SELECT * FROM backtest_runs WHERE run_id = ?", (run_id,)
        ).fetchone()
    if row is None:
        return None
    return {
        "run_id": row["run_id"],
        "created_at": row["created_at"],
        "status": row["status"],
        "request": json.loads(row["request"]),
        "report": json.loads(row["report"]) if row["report"] else None,
    }


def save_scan(scan: dict[str, Any], db_path: Optional[str] = None) -> None:
    scan_id = str(scan.get("scan_id") or "")
    generated_at = str(scan.get("generated_at") or "")
    if not scan_id or not generated_at:
        raise ValueError("scan_id and generated_at are required")
    with connect(db_path) as con:
        con.execute(
            "INSERT OR REPLACE INTO scans (scan_id, generated_at, payload) VALUES (?,?,?)",
            (scan_id, generated_at, json.dumps(scan)),
        )


def load_latest_scan(db_path: Optional[str] = None) -> Optional[dict[str, Any]]:
    with connect(db_path) as con:
        row = con.execute(
            "SELECT payload FROM scans ORDER BY generated_at DESC LIMIT 1"
        ).fetchone()
    if row is None:
        return None
    return json.loads(row["payload"])
