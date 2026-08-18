"""SQLite persistence for signals, fills, outcomes and calibration.

This is what makes OPUS self-improving rather than static: every signal it
emits is recorded, every resolved signal contributes a triple-barrier label,
and the probability model is refitted from those labels on a schedule.

Isolated in its own database file. It shares no table, no schema and no
connection with anything else in this repository.
"""

from __future__ import annotations

import json
import sqlite3
import threading
import time
from contextlib import contextmanager

import numpy as np

import opus.config as config
from opus.scoring import calibration as cal
from opus.scoring import validation as val
from opus.types import Signal
from opus.version import LABEL_MODEL_VERSION, SCORE_MODEL_VERSION

_SCHEMA = """
CREATE TABLE IF NOT EXISTS signals (
    signal_id       TEXT PRIMARY KEY,
    created_ts      REAL NOT NULL,
    symbol          TEXT NOT NULL,
    display         TEXT,
    asset_class     TEXT NOT NULL,
    venue           TEXT,
    direction       TEXT NOT NULL,
    archetype       TEXT NOT NULL,
    regime          TEXT,
    decision        TEXT NOT NULL,
    readiness       TEXT NOT NULL,
    conviction      REAL,
    coherence       REAL,
    probability     REAL,
    expectancy_r    REAL,
    cost_r          REAL,
    rr              REAL,
    entry           REAL,
    stop            REAL,
    target          REAL,
    bar_ts          REAL,
    score_model     TEXT,
    payload         TEXT
);
CREATE INDEX IF NOT EXISTS idx_signals_created ON signals(created_ts);
CREATE INDEX IF NOT EXISTS idx_signals_bucket  ON signals(archetype, asset_class);

CREATE TABLE IF NOT EXISTS outcomes (
    signal_id    TEXT PRIMARY KEY,
    resolved_ts  REAL NOT NULL,
    label        INTEGER NOT NULL,
    r_multiple   REAL NOT NULL,
    resolution   TEXT NOT NULL,
    bars_held    INTEGER,
    exit_price   REAL,
    label_model  TEXT
);

CREATE TABLE IF NOT EXISTS orders (
    order_id     TEXT PRIMARY KEY,
    signal_id    TEXT,
    submitted_ts REAL NOT NULL,
    mode         TEXT NOT NULL,
    broker       TEXT NOT NULL,
    symbol       TEXT NOT NULL,
    direction    TEXT NOT NULL,
    order_type   TEXT,
    units        REAL,
    entry        REAL,
    stop         REAL,
    target       REAL,
    status       TEXT NOT NULL,
    broker_ref   TEXT,
    detail       TEXT
);
CREATE INDEX IF NOT EXISTS idx_orders_signal ON orders(signal_id);
CREATE INDEX IF NOT EXISTS idx_orders_ts     ON orders(submitted_ts);

CREATE TABLE IF NOT EXISTS calibration (
    bucket      TEXT PRIMARY KEY,
    slope       REAL NOT NULL,
    intercept   REAL NOT NULL,
    samples     INTEGER NOT NULL,
    fitted      INTEGER NOT NULL,
    fitted_ts   REAL NOT NULL,
    score_model TEXT NOT NULL
);
"""


class Store:
    """Thread-safe SQLite wrapper. One connection per thread."""

    def __init__(self, path: str | None = None):
        self.path = path or config.store_path()
        self._local = threading.local()
        self._refit_lock = threading.Lock()
        self._last_refit = 0.0
        self._init_schema()

    # -- connection -------------------------------------------------------

    def _connect(self) -> sqlite3.Connection:
        conn = getattr(self._local, "conn", None)
        if conn is None:
            conn = sqlite3.connect(self.path, timeout=15.0)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA synchronous=NORMAL")
            self._local.conn = conn
        return conn

    @contextmanager
    def _cursor(self):
        conn = self._connect()
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise

    def _init_schema(self) -> None:
        with self._cursor() as conn:
            conn.executescript(_SCHEMA)

    def close(self) -> None:
        conn = getattr(self._local, "conn", None)
        if conn is not None:
            conn.close()
            self._local.conn = None

    # -- writes -----------------------------------------------------------

    def record_signals(self, signals: list[Signal]) -> int:
        """Persist emitted signals. Re-scanning the same bar is idempotent."""
        if not signals:
            return 0
        rows = []
        for s in signals:
            payload = s.as_dict()
            rows.append((
                s.signal_id, float(s.created_ts), s.symbol, s.display,
                s.asset_class, str(s.provenance.get("venue", "")),
                s.direction.value, s.archetype.value, s.regime.value,
                s.decision.value, s.readiness.value,
                float(s.conviction), float(s.coherence), float(s.probability),
                _finite(s.expectancy_r), _finite(s.cost_r), _finite(s.rr),
                float(s.levels.entry), float(s.levels.stop),
                float(s.levels.targets[0]) if s.levels.targets else None,
                float(s.provenance.get("barTs") or 0.0),
                SCORE_MODEL_VERSION, json.dumps(payload, default=str),
            ))
        with self._cursor() as conn:
            conn.executemany(
                "INSERT OR REPLACE INTO signals VALUES "
                "(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                rows,
            )
        return len(rows)

    def record_order(self, order: dict) -> None:
        with self._cursor() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO orders VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    str(order["order_id"]), order.get("signal_id"),
                    float(order.get("submitted_ts") or time.time()),
                    str(order.get("mode", "paper")), str(order.get("broker", "")),
                    str(order.get("symbol", "")), str(order.get("direction", "")),
                    str(order.get("order_type", "")),
                    _finite(order.get("units")), _finite(order.get("entry")),
                    _finite(order.get("stop")), _finite(order.get("target")),
                    str(order.get("status", "unknown")),
                    order.get("broker_ref"),
                    json.dumps(order.get("detail") or {}, default=str),
                ),
            )

    def record_outcome(self, signal_id: str, outcome: cal.Outcome) -> None:
        with self._cursor() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO outcomes VALUES (?,?,?,?,?,?,?,?)",
                (
                    signal_id, time.time(), int(outcome.label),
                    float(outcome.r_multiple), outcome.resolution,
                    int(outcome.bars_held), float(outcome.exit_price),
                    LABEL_MODEL_VERSION,
                ),
            )

    # -- reads ------------------------------------------------------------

    def recent_signals(self, limit: int = 100, decision: str | None = None) -> list[dict]:
        sql = "SELECT payload FROM signals"
        args: list = []
        if decision:
            sql += " WHERE decision = ?"
            args.append(decision)
        sql += " ORDER BY created_ts DESC LIMIT ?"
        args.append(int(limit))
        with self._cursor() as conn:
            rows = conn.execute(sql, args).fetchall()
        out = []
        for row in rows:
            try:
                out.append(json.loads(row["payload"]))
            except (ValueError, TypeError):
                continue
        return out

    def orders(self, limit: int = 100) -> list[dict]:
        with self._cursor() as conn:
            rows = conn.execute(
                "SELECT * FROM orders ORDER BY submitted_ts DESC LIMIT ?", (int(limit),)
            ).fetchall()
        out = []
        for row in rows:
            item = dict(row)
            try:
                item["detail"] = json.loads(item.get("detail") or "{}")
            except (ValueError, TypeError):
                item["detail"] = {}
            out.append(item)
        return out

    def labelled_samples(self, bucket: str) -> tuple[np.ndarray, np.ndarray]:
        """(conviction, label) pairs for one archetype|class bucket.

        Only samples scored under the CURRENT score model are returned: a
        conviction produced by different indicator maths is not comparable, and
        mixing them silently corrupts the calibration.
        """
        archetype, _, asset_class = bucket.partition("|")
        with self._cursor() as conn:
            rows = conn.execute(
                """
                SELECT s.conviction AS conviction, o.label AS label
                FROM signals s JOIN outcomes o ON o.signal_id = s.signal_id
                WHERE s.archetype = ? AND s.asset_class = ?
                  AND s.score_model = ? AND o.label_model = ?
                ORDER BY s.created_ts ASC
                """,
                (archetype, asset_class, SCORE_MODEL_VERSION, LABEL_MODEL_VERSION),
            ).fetchall()
        if not rows:
            return np.zeros(0), np.zeros(0)
        x = np.array([float(r["conviction"]) for r in rows], dtype=float)
        y = np.array([float(r["label"]) for r in rows], dtype=float)
        return x, y

    def r_multiples(self, bucket: str) -> np.ndarray:
        archetype, _, asset_class = bucket.partition("|")
        with self._cursor() as conn:
            rows = conn.execute(
                """
                SELECT o.r_multiple AS r
                FROM signals s JOIN outcomes o ON o.signal_id = s.signal_id
                WHERE s.archetype = ? AND s.asset_class = ?
                  AND s.score_model = ? AND o.label_model = ?
                ORDER BY s.created_ts ASC
                """,
                (archetype, asset_class, SCORE_MODEL_VERSION, LABEL_MODEL_VERSION),
            ).fetchall()
        return np.array([float(r["r"]) for r in rows], dtype=float)

    def known_buckets(self) -> list[str]:
        with self._cursor() as conn:
            rows = conn.execute(
                "SELECT DISTINCT archetype, asset_class FROM signals WHERE score_model = ?",
                (SCORE_MODEL_VERSION,),
            ).fetchall()
        return [cal.bucket_key(r["archetype"], r["asset_class"]) for r in rows]

    # -- calibration ------------------------------------------------------

    def calibrators(self, force_refit: bool = False) -> dict[str, cal.Calibrator]:
        """Effective calibrators per bucket, refitted on the configured cadence."""
        cfg = config.load()["CALIBRATION"]
        interval = float(cfg["refit_interval_sec"])
        now = time.time()

        with self._refit_lock:
            stale = force_refit or (now - self._last_refit) >= interval
            if stale:
                self._refit_all()
                self._last_refit = now

        with self._cursor() as conn:
            rows = conn.execute(
                "SELECT * FROM calibration WHERE score_model = ?", (SCORE_MODEL_VERSION,)
            ).fetchall()

        out: dict[str, cal.Calibrator] = {}
        for row in rows:
            out[row["bucket"]] = cal.Calibrator(
                slope=float(row["slope"]), intercept=float(row["intercept"]),
                samples=int(row["samples"]), fitted=bool(row["fitted"]),
                bucket=row["bucket"],
            )
        return out

    def _refit_all(self) -> None:
        buckets = self.known_buckets()
        if not buckets:
            return
        rows = []
        for bucket in buckets:
            x, y = self.labelled_samples(bucket)
            calibrator = cal.build_calibrator(
                bucket, x if x.size else None, y if y.size else None
            )
            rows.append((
                bucket, float(calibrator.slope), float(calibrator.intercept),
                int(calibrator.samples), int(bool(calibrator.fitted)),
                time.time(), SCORE_MODEL_VERSION,
            ))
        with self._cursor() as conn:
            conn.executemany(
                "INSERT OR REPLACE INTO calibration VALUES (?,?,?,?,?,?,?)", rows
            )

    def validations(self) -> dict[str, val.ValidationResult]:
        out: dict[str, val.ValidationResult] = {}
        buckets = self.known_buckets()
        trials = max(len(buckets), int(config.load()["VALIDATION"]["trials_assumed"]))
        for bucket in buckets:
            out[bucket] = val.validate(bucket, self.r_multiples(bucket), trials=trials)
        return out

    # -- maintenance ------------------------------------------------------

    def prune(self) -> int:
        retain = float(config.load()["STORE"].get("retain_days", 400))
        cutoff = time.time() - retain * 86400
        with self._cursor() as conn:
            cur = conn.execute("DELETE FROM signals WHERE created_ts < ?", (cutoff,))
            conn.execute(
                "DELETE FROM outcomes WHERE signal_id NOT IN (SELECT signal_id FROM signals)"
            )
            return int(cur.rowcount or 0)

    def stats(self) -> dict:
        with self._cursor() as conn:
            signals = conn.execute("SELECT COUNT(*) AS n FROM signals").fetchone()["n"]
            outcomes = conn.execute("SELECT COUNT(*) AS n FROM outcomes").fetchone()["n"]
            orders = conn.execute("SELECT COUNT(*) AS n FROM orders").fetchone()["n"]
            trades = conn.execute(
                "SELECT COUNT(*) AS n FROM signals WHERE decision = 'TRADE'"
            ).fetchone()["n"]
        return {
            "signals": int(signals), "tradeSignals": int(trades),
            "outcomes": int(outcomes), "orders": int(orders),
            "path": self.path, "scoreModel": SCORE_MODEL_VERSION,
        }


def _finite(value) -> float | None:
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    return f if np.isfinite(f) else None


_default: Store | None = None
_default_lock = threading.Lock()


def default_store() -> Store:
    global _default
    with _default_lock:
        if _default is None:
            _default = Store()
        return _default


__all__ = ["Store", "default_store"]
