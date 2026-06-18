"""Persistence for AI chart reviews in audit.db."""

from __future__ import annotations

import json
import os
import sqlite3
import uuid
from datetime import datetime, timezone
from typing import Any

_DEFAULT_AUDIT_DB = os.path.join(os.path.dirname(os.path.dirname(__file__)), "audit.db")

_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS ai_chart_reviews (
  review_id              TEXT PRIMARY KEY,
  created_at             TEXT NOT NULL,
  symbol                 TEXT NOT NULL,
  timeframe              TEXT NOT NULL,
  asset_group            TEXT,
  provider               TEXT NOT NULL,
  model                  TEXT NOT NULL,
  latency_ms             INTEGER,
  screenshot_hash        TEXT NOT NULL,
  screenshot_bytes       INTEGER,
  screenshot_meta_json   TEXT NOT NULL,
  engine_a_snapshot_json TEXT NOT NULL,
  ai_review_json         TEXT NOT NULL,
  concordance_json       TEXT NOT NULL,
  scan_timestamp         TEXT,
  chart_captured_at      TEXT,
  latest_candle_ts       TEXT,
  mismatch_warnings_json TEXT,
  parse_success          INTEGER NOT NULL,
  review_type            TEXT NOT NULL DEFAULT 'engine_a'
);

CREATE INDEX IF NOT EXISTS idx_ai_chart_reviews_symbol_tf
  ON ai_chart_reviews(symbol, timeframe, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_ai_chart_reviews_concordance
  ON ai_chart_reviews(json_extract(concordance_json, '$.concordance'), created_at DESC);
"""

_DEDUP_INDEX_SQL = """
CREATE INDEX IF NOT EXISTS idx_ai_chart_reviews_dedup
  ON ai_chart_reviews(symbol, timeframe, screenshot_hash, review_type, created_at DESC);
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
        cols = {row[1] for row in con.execute("PRAGMA table_info(ai_chart_reviews)").fetchall()}
        if "review_type" not in cols:
            con.execute(
                "ALTER TABLE ai_chart_reviews ADD COLUMN review_type TEXT NOT NULL DEFAULT 'engine_a'"
            )
        con.executescript(_DEDUP_INDEX_SQL)
        con.commit()


def _row_to_response(row: sqlite3.Row) -> dict[str, Any]:
    review_type = row["review_type"] if "review_type" in row.keys() else "engine_a"
    engine_snapshot = json.loads(row["engine_a_snapshot_json"])
    out: dict[str, Any] = {
        "review_id": row["review_id"],
        "provider": row["provider"],
        "model": row["model"],
        "latency_ms": row["latency_ms"],
        "review_type": review_type,
        "ai_review": json.loads(row["ai_review_json"]),
        "concordance": json.loads(row["concordance_json"]),
        "timestamps": {
            "scan_timestamp": row["scan_timestamp"],
            "chart_captured_at": row["chart_captured_at"],
            "latest_candle_ts": row["latest_candle_ts"],
        },
        "mismatch_warnings": json.loads(row["mismatch_warnings_json"] or "[]"),
        "dedup_hit": False,
    }
    if review_type == "engine_d":
        out["engine_d_context"] = engine_snapshot
    elif review_type == "engine_b":
        out["engine_b_context"] = engine_snapshot
        out["engineBContext"] = engine_snapshot
        out["primaryEngine"] = "B"
    else:
        out["engine_a_context"] = engine_snapshot
        out["primaryEngine"] = "A"
    return out


def find_scalp_review_by_id(
    review_id: str,
    *,
    audit_db: str | None = None,
    review_type: str = "engine_d",
) -> dict[str, Any] | None:
    """Load a persisted scalp chart review by review_id."""
    if not review_id:
        return None
    ensure_schema(audit_db)
    path = _audit_db_path(audit_db)
    with sqlite3.connect(path, timeout=15.0) as con:
        con.row_factory = sqlite3.Row
        row = con.execute(
            """
            SELECT * FROM ai_chart_reviews
            WHERE review_id = ? AND review_type = ?
            LIMIT 1
            """,
            (review_id, review_type),
        ).fetchone()
    if not row:
        return None
    out = _row_to_response(row)
    out["created_at"] = row["created_at"]
    return out


def find_recent_review_by_hash(
    symbol: str,
    timeframe: str,
    screenshot_hash: str,
    window_seconds: int,
    *,
    audit_db: str | None = None,
    review_type: str = "engine_a",
) -> dict[str, Any] | None:
    ensure_schema(audit_db)
    path = _audit_db_path(audit_db)
    with sqlite3.connect(path, timeout=15.0) as con:
        con.row_factory = sqlite3.Row
        row = con.execute(
            """
            SELECT * FROM ai_chart_reviews
            WHERE symbol = ? AND timeframe = ? AND screenshot_hash = ?
              AND review_type = ?
              AND datetime(created_at) >= datetime('now', ?)
            ORDER BY created_at DESC
            LIMIT 1
            """,
            (
                symbol,
                timeframe,
                screenshot_hash,
                review_type,
                f"-{int(window_seconds)} seconds",
            ),
        ).fetchone()
    if not row:
        return None
    return _row_to_response(row)


def record_review(
    *,
    symbol: str,
    timeframe: str,
    asset_group: str | None,
    provider: str,
    model: str,
    latency_ms: int | None,
    screenshot_hash: str,
    screenshot_bytes: int,
    screenshot_meta: dict[str, Any],
    engine_a_context: dict[str, Any] | None = None,
    engine_b_context: dict[str, Any] | None = None,
    engine_d_context: dict[str, Any] | None = None,
    ai_review: dict[str, Any],
    concordance: dict[str, Any],
    mismatch_warnings: list[str],
    audit_db: str | None = None,
    review_type: str = "engine_a",
) -> dict[str, Any]:
    ensure_schema(audit_db)
    path = _audit_db_path(audit_db)
    review_id = uuid.uuid4().hex
    created_at = datetime.now(timezone.utc).isoformat()
    parse_success = 1 if ai_review.get("parse_success", True) else 0
    engine_context = (
        engine_d_context
        if review_type == "engine_d"
        else engine_b_context
        if review_type == "engine_b"
        else engine_a_context
    )
    if engine_context is None:
        raise ValueError("engine context is required for record_review")

    with sqlite3.connect(path, timeout=15.0) as con:
        con.execute("PRAGMA journal_mode=WAL")
        con.execute(
            """
            INSERT INTO ai_chart_reviews (
              review_id, created_at, symbol, timeframe, asset_group,
              provider, model, latency_ms, screenshot_hash, screenshot_bytes,
              screenshot_meta_json, engine_a_snapshot_json, ai_review_json,
              concordance_json, scan_timestamp, chart_captured_at,
              latest_candle_ts, mismatch_warnings_json, parse_success,
              review_type
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                review_id,
                created_at,
                symbol,
                timeframe,
                asset_group,
                provider,
                model,
                latency_ms,
                screenshot_hash,
                screenshot_bytes,
                json.dumps(screenshot_meta, default=str),
                json.dumps(engine_context, default=str),
                json.dumps(ai_review, default=str),
                json.dumps(concordance, default=str),
                engine_context.get("scan_timestamp"),
                engine_context.get("chart_captured_at"),
                engine_context.get("latest_candle_ts"),
                json.dumps(mismatch_warnings, default=str),
                parse_success,
                review_type,
            ),
        )
        con.commit()

    response: dict[str, Any] = {
        "review_id": review_id,
        "provider": provider,
        "model": model,
        "latency_ms": latency_ms,
        "review_type": review_type,
        "ai_review": ai_review,
        "concordance": concordance,
        "timestamps": {
            "scan_timestamp": engine_context.get("scan_timestamp"),
            "chart_captured_at": engine_context.get("chart_captured_at"),
            "latest_candle_ts": engine_context.get("latest_candle_ts"),
        },
        "mismatch_warnings": mismatch_warnings,
        "dedup_hit": False,
    }
    if review_type == "engine_d":
        response["engine_d_context"] = engine_context
    elif review_type == "engine_b":
        response["engine_b_context"] = engine_context
        response["engineBContext"] = engine_context
        response["primaryEngine"] = "B"
    else:
        response["engine_a_context"] = engine_context
        response["primaryEngine"] = "A"
    return response
