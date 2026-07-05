"""SQLite persistence for EdgeLab suggestions and audit tables."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def connect(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path), timeout=15)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=15000")
    return conn


def init_db(db_path: Path) -> None:
    with connect(db_path) as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS suggestions (
                id TEXT PRIMARY KEY,
                timestamp TEXT NOT NULL,
                engine TEXT,
                symbol TEXT,
                timeframe TEXT,
                suggestion_type TEXT,
                title TEXT,
                summary TEXT,
                evidence TEXT,
                baseline_metrics_json TEXT,
                candidate_metrics_json TEXT,
                score_delta REAL,
                risk_flags TEXT,
                status TEXT NOT NULL,
                patch_path TEXT,
                rejection_reason TEXT,
                created_by TEXT
            );
            CREATE TABLE IF NOT EXISTS research_runs (
                id TEXT PRIMARY KEY,
                timestamp TEXT NOT NULL,
                cycle_number INTEGER,
                dry_run INTEGER,
                engines_run TEXT,
                duration_seconds REAL,
                summary TEXT
            );
            CREATE TABLE IF NOT EXISTS candidate_patches (
                id TEXT PRIMARY KEY,
                timestamp TEXT NOT NULL,
                patch_path TEXT,
                changed_files TEXT,
                accepted INTEGER,
                rejection_reasons TEXT,
                baseline_score REAL,
                candidate_score REAL
            );
            CREATE TABLE IF NOT EXISTS data_freshness (
                id TEXT PRIMARY KEY,
                timestamp TEXT NOT NULL,
                symbol TEXT,
                timeframe TEXT,
                freshness_score REAL,
                blocking_issues TEXT,
                warnings TEXT,
                recommended_action TEXT,
                details_json TEXT
            );
            CREATE TABLE IF NOT EXISTS engine_findings (
                id TEXT PRIMARY KEY,
                timestamp TEXT NOT NULL,
                engine TEXT,
                symbol TEXT,
                timeframe TEXT,
                finding_type TEXT,
                title TEXT,
                details_json TEXT
            );
            CREATE TABLE IF NOT EXISTS promotion_reviews (
                id TEXT PRIMARY KEY,
                timestamp TEXT NOT NULL,
                suggestion_id TEXT,
                action TEXT,
                reviewer TEXT,
                notes TEXT
            );
            CREATE TABLE IF NOT EXISTS rejected_ideas (
                id TEXT PRIMARY KEY,
                timestamp TEXT NOT NULL,
                source TEXT,
                title TEXT,
                reason TEXT,
                details_json TEXT
            );
            """
        )
        conn.commit()


def insert_suggestion(conn: sqlite3.Connection, row: dict[str, Any]) -> str:
    sid = row.get("id") or str(uuid4())
    conn.execute(
        """
        INSERT INTO suggestions (
            id, timestamp, engine, symbol, timeframe, suggestion_type, title, summary,
            evidence, baseline_metrics_json, candidate_metrics_json, score_delta,
            risk_flags, status, patch_path, rejection_reason, created_by
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            sid,
            row.get("timestamp") or _utcnow(),
            row.get("engine"),
            row.get("symbol"),
            row.get("timeframe"),
            row.get("suggestion_type"),
            row.get("title"),
            row.get("summary"),
            row.get("evidence"),
            json.dumps(row.get("baseline_metrics") or {}),
            json.dumps(row.get("candidate_metrics") or {}),
            row.get("score_delta"),
            json.dumps(row.get("risk_flags") or []),
            row.get("status") or "new",
            row.get("patch_path"),
            row.get("rejection_reason"),
            row.get("created_by") or "daemon",
        ),
    )
    return sid


def list_suggestions(
    conn: sqlite3.Connection,
    *,
    limit: int = 100,
    engine: str | None = None,
    symbol: str | None = None,
    status: str | None = None,
) -> list[dict[str, Any]]:
    clauses = ["1=1"]
    params: list[Any] = []
    if engine:
        clauses.append("engine = ?")
        params.append(engine)
    if symbol:
        clauses.append("symbol = ?")
        params.append(symbol)
    if status:
        clauses.append("status = ?")
        params.append(status)
    params.append(limit)
    sql = f"SELECT * FROM suggestions WHERE {' AND '.join(clauses)} ORDER BY timestamp DESC LIMIT ?"
    rows = conn.execute(sql, params).fetchall()
    return [_row_to_suggestion(r) for r in rows]


def update_suggestion_status(conn: sqlite3.Connection, sid: str, status: str) -> bool:
    cur = conn.execute("UPDATE suggestions SET status = ? WHERE id = ?", (status, sid))
    conn.commit()
    return cur.rowcount > 0


def _row_to_suggestion(row: sqlite3.Row) -> dict[str, Any]:
    d = dict(row)
    for key in ("baseline_metrics_json", "candidate_metrics_json", "risk_flags"):
        if d.get(key):
            try:
                d[key.replace("_json", "") if key.endswith("_json") else key] = json.loads(d[key])
            except json.JSONDecodeError:
                pass
    if d.get("baseline_metrics_json"):
        d["baseline_metrics"] = json.loads(d["baseline_metrics_json"])
    if d.get("candidate_metrics_json"):
        d["candidate_metrics"] = json.loads(d["candidate_metrics_json"])
    if d.get("risk_flags") and isinstance(d["risk_flags"], str):
        d["risk_flags"] = json.loads(d["risk_flags"])
    return d


def insert_research_run(conn: sqlite3.Connection, row: dict[str, Any]) -> str:
    rid = row.get("id") or str(uuid4())
    conn.execute(
        """
        INSERT INTO research_runs (id, timestamp, cycle_number, dry_run, engines_run, duration_seconds, summary)
        VALUES (?,?,?,?,?,?,?)
        """,
        (
            rid,
            row.get("timestamp") or _utcnow(),
            row.get("cycle_number"),
            1 if row.get("dry_run") else 0,
            json.dumps(row.get("engines_run") or []),
            row.get("duration_seconds"),
            json.dumps(row.get("summary") or {}),
        ),
    )
    return rid


def insert_data_freshness(conn: sqlite3.Connection, row: dict[str, Any]) -> str:
    fid = row.get("id") or str(uuid4())
    conn.execute(
        """
        INSERT INTO data_freshness (
            id, timestamp, symbol, timeframe, freshness_score,
            blocking_issues, warnings, recommended_action, details_json
        ) VALUES (?,?,?,?,?,?,?,?,?)
        """,
        (
            fid,
            row.get("timestamp") or _utcnow(),
            row.get("symbol"),
            row.get("timeframe"),
            row.get("freshness_score"),
            json.dumps(row.get("blocking_issues") or []),
            json.dumps(row.get("warnings") or []),
            row.get("recommended_action"),
            json.dumps(row.get("details") or {}),
        ),
    )
    return fid


def insert_engine_finding(conn: sqlite3.Connection, row: dict[str, Any]) -> str:
    fid = row.get("id") or str(uuid4())
    conn.execute(
        """
        INSERT INTO engine_findings (id, timestamp, engine, symbol, timeframe, finding_type, title, details_json)
        VALUES (?,?,?,?,?,?,?,?)
        """,
        (
            fid,
            row.get("timestamp") or _utcnow(),
            row.get("engine"),
            row.get("symbol"),
            row.get("timeframe"),
            row.get("finding_type"),
            row.get("title"),
            json.dumps(row.get("details") or {}),
        ),
    )
    return fid


def insert_candidate_patch(conn: sqlite3.Connection, row: dict[str, Any]) -> str:
    cid = row.get("id") or str(uuid4())
    conn.execute(
        """
        INSERT INTO candidate_patches (
            id, timestamp, patch_path, changed_files, accepted,
            rejection_reasons, baseline_score, candidate_score
        ) VALUES (?,?,?,?,?,?,?,?)
        """,
        (
            cid,
            row.get("timestamp") or _utcnow(),
            row.get("patch_path"),
            json.dumps(row.get("changed_files") or []),
            1 if row.get("accepted") else 0,
            json.dumps(row.get("rejection_reasons") or []),
            row.get("baseline_score"),
            row.get("candidate_score"),
        ),
    )
    return cid


def insert_rejected_idea(conn: sqlite3.Connection, row: dict[str, Any]) -> str:
    rid = row.get("id") or str(uuid4())
    conn.execute(
        """
        INSERT INTO rejected_ideas (id, timestamp, source, title, reason, details_json)
        VALUES (?,?,?,?,?,?)
        """,
        (
            rid,
            row.get("timestamp") or _utcnow(),
            row.get("source"),
            row.get("title"),
            row.get("reason"),
            json.dumps(row.get("details") or {}),
        ),
    )
    return rid


def insert_promotion_review(conn: sqlite3.Connection, row: dict[str, Any]) -> str:
    pid = row.get("id") or str(uuid4())
    conn.execute(
        """
        INSERT INTO promotion_reviews (id, timestamp, suggestion_id, action, reviewer, notes)
        VALUES (?,?,?,?,?,?)
        """,
        (
            pid,
            row.get("timestamp") or _utcnow(),
            row.get("suggestion_id"),
            row.get("action"),
            row.get("reviewer") or "manual",
            row.get("notes"),
        ),
    )
    return pid
