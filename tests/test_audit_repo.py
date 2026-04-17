import sqlite3
import time
from pathlib import Path

from athena_app.repositories.audit_repo import insert_manual_error


def test_insert_manual_error_persists_rejection_context():
    db_path = Path("tests/_audit_repo_test.db")
    try:
        if db_path.exists():
            db_path.unlink()
        with sqlite3.connect(db_path) as con:
            con.execute(
                """
                CREATE TABLE audit_log (
                    ts TEXT,
                    pair TEXT,
                    score REAL,
                    direction TEXT,
                    style TEXT,
                    grade TEXT,
                    error_tag TEXT,
                    entry_price REAL,
                    sl REAL,
                    tp REAL,
                    volume REAL,
                    risk_amount REAL,
                    risk_pct REAL
                )
                """
            )

        insert_manual_error(
            str(db_path),
            ts="2026-04-17T07:12:48+00:00",
            pair="Nickel",
            score=3.0,
            direction="LONG",
            style="swing",
            error_tag="ZERO_VOLUME",
            entry_price=18469.0,
            sl=17594.890704,
            tp=19925.848827,
            volume=0.0,
            risk_amount=0.0,
            risk_pct=0.0,
        )

        with sqlite3.connect(db_path) as con:
            row = con.execute(
                """
                SELECT pair, error_tag, entry_price, sl, tp, volume, risk_amount, risk_pct
                FROM audit_log
                """
            ).fetchone()

        assert row == (
            "Nickel",
            "ZERO_VOLUME",
            18469.0,
            17594.890704,
            19925.848827,
            0.0,
            0.0,
            0.0,
        )
    finally:
        for _ in range(5):
            try:
                if db_path.exists():
                    db_path.unlink()
                break
            except PermissionError:
                time.sleep(0.1)
