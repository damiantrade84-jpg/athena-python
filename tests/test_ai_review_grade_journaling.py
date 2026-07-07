"""ai_review_grade journaling: execution audit lookup + learning_log copy-through."""

import os
import sqlite3
import tempfile
from datetime import datetime, timedelta, timezone

from ai_learning import extract_learning_from_trade, init_learning_db
from execution import _resolve_ai_review_for_audit


def _tmp_db() -> str:
    d = tempfile.mkdtemp(prefix="ai_grade_journal_")
    return os.path.join(d, "audit.db")


def _make_audit_db(path: str) -> None:
    with sqlite3.connect(path) as con:
        con.execute(
            """CREATE TABLE audit_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts TEXT, pair TEXT, score REAL, engine TEXT, direction TEXT,
                grade TEXT, edge_prob REAL, regime TEXT, ticket TEXT,
                pnl REAL, r_multiple REAL, exit_reason TEXT,
                holding_period_hours REAL, asset_class TEXT, votes_json TEXT,
                factors_json TEXT, max_score REAL,
                ai_review_grade TEXT, ai_review_id TEXT
            )"""
        )
        con.execute(
            """CREATE TABLE bulk_ai_reviews (
                review_id TEXT, created_at TEXT, symbol TEXT, pair TEXT,
                grade TEXT, is_safe_default INTEGER DEFAULT 0
            )"""
        )
        con.execute(
            """CREATE TABLE ai_chart_reviews (
                review_id TEXT, created_at TEXT, symbol TEXT,
                ai_review_json TEXT, parse_success INTEGER
            )"""
        )
        con.commit()


def test_signal_payload_grade_wins():
    grade, rid = _resolve_ai_review_for_audit(
        {"pair": "EUR/USD", "ai_grade": "B", "review_id": "r1"}, None
    )
    assert grade == "B"
    assert rid == "r1"


def test_db_fallback_uses_recent_bulk_review():
    db = _tmp_db()
    _make_audit_db(db)
    now = datetime.now(timezone.utc)
    with sqlite3.connect(db) as con:
        # stale review (outside 24h) must be ignored
        con.execute(
            "INSERT INTO bulk_ai_reviews(review_id,created_at,symbol,pair,grade,is_safe_default) "
            "VALUES('old',?,'XAU/USD','XAU/USD','A',0)",
            ((now - timedelta(days=3)).isoformat(),),
        )
        con.execute(
            "INSERT INTO bulk_ai_reviews(review_id,created_at,symbol,pair,grade,is_safe_default) "
            "VALUES('fresh',?,'XAU/USD','XAU/USD','C',0)",
            ((now - timedelta(hours=1)).isoformat(),),
        )
        # safe-default rows must never be used
        con.execute(
            "INSERT INTO bulk_ai_reviews(review_id,created_at,symbol,pair,grade,is_safe_default) "
            "VALUES('safe',?,'XAU/USD','XAU/USD','F',1)",
            (now.isoformat(),),
        )
        con.commit()
    grade, rid = _resolve_ai_review_for_audit({"pair": "XAU/USD"}, db)
    assert grade == "C"
    assert rid == "fresh"


def test_no_review_returns_none_and_never_raises():
    db = _tmp_db()
    _make_audit_db(db)
    assert _resolve_ai_review_for_audit({"pair": "GBP/JPY"}, db) == (None, None)
    # missing db / malformed input fail safe
    assert _resolve_ai_review_for_audit({}, "Z:/nope/missing.db") == (None, None)


def test_learning_log_copies_ai_review_grade():
    db = _tmp_db()
    _make_audit_db(db)
    init_learning_db(db)
    with sqlite3.connect(db) as con:
        con.execute(
            "INSERT INTO audit_log(ts,pair,score,engine,direction,grade,regime,ticket,"
            "pnl,r_multiple,exit_reason,holding_period_hours,asset_class,ai_review_grade,ai_review_id) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                datetime.now(timezone.utc).isoformat(),
                "EUR/USD", 8.0, "engine_b", "LONG", "EXECUTED", "TREND",
                "T123", -50.0, -1.0, "SL_HIT", 5.0, "forex", "CAUTION", "rev42",
            ),
        )
        con.commit()
    extract_learning_from_trade(db, "T123", is_demo=True)
    with sqlite3.connect(db) as con:
        row = con.execute(
            "SELECT ai_review_grade, ai_review_id, ai_grade FROM learning_log WHERE ticket='T123'"
        ).fetchone()
    assert row is not None, "learning_log row not written"
    assert row[0] == "CAUTION"
    assert row[1] == "rev42"
    # legacy grade field untouched (still the execution marker)
    assert row[2] == "EXECUTED"
