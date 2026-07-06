"""Persistence for bulk Marcus Reid AI reviews in audit.db.

Every review from a bulk run is stored (grades A-F plus flagged safe-defaults)
so later win-rate analysis on A/B-graded signals has the full denominator.
Advisory only: nothing here feeds execution or gates.
"""

from __future__ import annotations

import json
import os
import sqlite3
import uuid
from datetime import datetime, timezone
from typing import Any

_DEFAULT_AUDIT_DB = os.path.join(os.path.dirname(os.path.dirname(__file__)), "audit.db")

_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS bulk_ai_reviews (
  review_id            TEXT PRIMARY KEY,
  scan_run_id          TEXT NOT NULL,
  created_at           TEXT NOT NULL,
  symbol               TEXT NOT NULL,
  pair                 TEXT,
  direction            TEXT,
  asset_class          TEXT,
  style                TEXT,
  confluence_score     REAL,
  rr                   REAL,
  engine_b_conviction  REAL,
  engine_c_conviction  REAL,
  grade                TEXT,
  edge_probability     REAL,
  risk_level           TEXT,
  verdict              TEXT,
  review_json          TEXT NOT NULL,
  is_safe_default      INTEGER NOT NULL DEFAULT 0,
  cache_hit            INTEGER NOT NULL DEFAULT 0,
  provider             TEXT,
  model                TEXT,
  latency_ms           INTEGER
);

CREATE INDEX IF NOT EXISTS idx_bulk_ai_reviews_run
  ON bulk_ai_reviews(scan_run_id);

CREATE INDEX IF NOT EXISTS idx_bulk_ai_reviews_symbol
  ON bulk_ai_reviews(symbol, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_bulk_ai_reviews_grade
  ON bulk_ai_reviews(grade, created_at DESC);
"""


def _audit_db_path(audit_db: str | None = None) -> str:
    return audit_db or os.environ.get("ATHENA_AUDIT_DB") or _DEFAULT_AUDIT_DB


def ensure_schema(audit_db: str | None = None) -> None:
    path = _audit_db_path(audit_db)
    directory = os.path.dirname(os.path.abspath(path))
    if directory:
        os.makedirs(directory, exist_ok=True)
    with sqlite3.connect(path, timeout=15.0) as con:
        con.execute("PRAGMA journal_mode=WAL")
        con.executescript(_SCHEMA_SQL)
        con.commit()


def insert_review(row: dict[str, Any], audit_db: str | None = None) -> str:
    """Insert one bulk review row; returns the generated review_id."""
    ensure_schema(audit_db)
    path = _audit_db_path(audit_db)
    review_id = uuid.uuid4().hex
    created_at = datetime.now(timezone.utc).isoformat()
    review_json = row.get("review_json")
    if not isinstance(review_json, str):
        review_json = json.dumps(row.get("review") or {}, default=str)
    with sqlite3.connect(path, timeout=15.0) as con:
        con.execute("PRAGMA journal_mode=WAL")
        con.execute(
            """
            INSERT INTO bulk_ai_reviews (
              review_id, scan_run_id, created_at, symbol, pair, direction,
              asset_class, style, confluence_score, rr,
              engine_b_conviction, engine_c_conviction,
              grade, edge_probability, risk_level, verdict,
              review_json, is_safe_default, cache_hit,
              provider, model, latency_ms
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                review_id,
                str(row.get("scan_run_id") or ""),
                created_at,
                str(row.get("symbol") or ""),
                row.get("pair"),
                row.get("direction"),
                row.get("asset_class"),
                row.get("style"),
                _to_float(row.get("confluence_score")),
                _to_float(row.get("rr")),
                _to_float(row.get("engine_b_conviction")),
                _to_float(row.get("engine_c_conviction")),
                row.get("grade"),
                _to_float(row.get("edge_probability")),
                row.get("risk_level"),
                _text_or_none(row.get("verdict")),
                review_json,
                1 if row.get("is_safe_default") else 0,
                1 if row.get("cache_hit") else 0,
                row.get("provider"),
                row.get("model"),
                _to_int(row.get("latency_ms")),
            ),
        )
        con.commit()
    return review_id


def list_reviews(
    scan_run_id: str | None = None,
    grade: str | None = None,
    symbol: str | None = None,
    limit: int = 100,
    audit_db: str | None = None,
) -> list[dict[str, Any]]:
    ensure_schema(audit_db)
    path = _audit_db_path(audit_db)
    clauses: list[str] = []
    params: list[Any] = []
    if scan_run_id:
        clauses.append("scan_run_id = ?")
        params.append(scan_run_id)
    if grade:
        clauses.append("grade = ?")
        params.append(str(grade).upper())
    if symbol:
        clauses.append("symbol = ?")
        params.append(symbol)
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    try:
        capped = max(1, min(int(limit), 500))
    except (TypeError, ValueError):
        capped = 100
    params.append(capped)
    with sqlite3.connect(path, timeout=15.0) as con:
        con.row_factory = sqlite3.Row
        rows = con.execute(
            f"""
            SELECT * FROM bulk_ai_reviews
            {where}
            ORDER BY created_at DESC
            LIMIT ?
            """,
            params,
        ).fetchall()
    out: list[dict[str, Any]] = []
    for row in rows:
        item = {key: row[key] for key in row.keys()}
        try:
            item["review"] = json.loads(row["review_json"])
        except (TypeError, ValueError):
            item["review"] = None
        item.pop("review_json", None)
        out.append(item)
    return out


def list_runs(limit: int = 20, audit_db: str | None = None) -> list[dict[str, Any]]:
    """Per-run summary counts for the history view."""
    ensure_schema(audit_db)
    path = _audit_db_path(audit_db)
    try:
        capped = max(1, min(int(limit), 200))
    except (TypeError, ValueError):
        capped = 20
    with sqlite3.connect(path, timeout=15.0) as con:
        con.row_factory = sqlite3.Row
        rows = con.execute(
            """
            SELECT scan_run_id,
                   MIN(created_at) AS created_at,
                   MAX(asset_class) AS asset_class,
                   MAX(style) AS style,
                   COUNT(*) AS reviewed,
                   SUM(CASE WHEN grade IN ('A+','A') THEN 1 ELSE 0 END) AS a_grades,
                   SUM(CASE WHEN grade = 'B' THEN 1 ELSE 0 END) AS b_grades,
                   SUM(is_safe_default) AS safe_defaults,
                   SUM(cache_hit) AS cache_hits
            FROM bulk_ai_reviews
            GROUP BY scan_run_id
            ORDER BY MIN(created_at) DESC
            LIMIT ?
            """,
            (capped,),
        ).fetchall()
    return [{key: row[key] for key in row.keys()} for row in rows]


def _to_float(value: Any) -> float | None:
    try:
        if value is None or value == "":
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _to_int(value: Any) -> int | None:
    try:
        if value is None or value == "":
            return None
        return int(value)
    except (TypeError, ValueError):
        return None


def _text_or_none(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None
