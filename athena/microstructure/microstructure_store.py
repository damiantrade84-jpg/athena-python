"""microstructure_store.py — Persistent storage for aggregated microstructure metrics.

Never stores raw order book updates; only stores aggregated metrics per interval.
"""

from contextlib import contextmanager
import sqlite3
import logging
import time
import threading
import os
import shutil
from typing import Dict, Iterator, List
from pathlib import Path

log = logging.getLogger("sentinel")

LEGACY_DB_PATH = Path(__file__).parent.parent.parent / "microstructure.db"


def _resolve_db_path() -> Path:
    """Resolve DB path with OneDrive-safe default on Windows.

    Priority:
    1) MICROSTRUCTURE_DB_PATH env var (explicit override)
    2) Windows LOCALAPPDATA\\Athena\\microstructure.db (outside OneDrive sync)
    3) Legacy repo-root path (non-Windows fallback)
    """
    env_path = os.environ.get("MICROSTRUCTURE_DB_PATH", "").strip()
    if env_path:
        p = Path(env_path).expanduser()
        p.parent.mkdir(parents=True, exist_ok=True)
        return p

    if os.name == "nt":
        local_app_data = os.environ.get("LOCALAPPDATA", "").strip()
        if local_app_data:
            p = Path(local_app_data) / "Athena" / "microstructure.db"
            p.parent.mkdir(parents=True, exist_ok=True)
            return p

    return LEGACY_DB_PATH


DB_PATH = _resolve_db_path()

_KEEP_HOURS = int(os.environ.get("MICROSTRUCTURE_KEEP_HOURS", "72"))
_MAINTENANCE_INTERVAL_SEC = int(
    os.environ.get("MICROSTRUCTURE_MAINT_INTERVAL_SEC", "1800")
)
_SQLITE_TIMEOUT_SEC = float(
    os.environ.get("MICROSTRUCTURE_SQLITE_TIMEOUT_SEC", "15.0")
)
_last_maintenance_ts = 0.0

_db_lock = threading.Lock()


@contextmanager
def _connect() -> Iterator[sqlite3.Connection]:
    con = sqlite3.connect(DB_PATH, timeout=_SQLITE_TIMEOUT_SEC)
    try:
        yield con
        con.commit()
    except Exception:
        con.rollback()
        raise
    finally:
        con.close()


def _maybe_migrate_legacy_db() -> None:
    """One-time copy from legacy repo DB to new DB path if needed."""
    try:
        if DB_PATH == LEGACY_DB_PATH:
            return
        if DB_PATH.exists():
            return
        if LEGACY_DB_PATH.exists():
            shutil.copy2(LEGACY_DB_PATH, DB_PATH)
            log.warning(
                f"[MicroStore] Migrated legacy DB to local path: {DB_PATH}"
            )
    except Exception as _e:
        log.warning(f"[MicroStore] Legacy migration warning: {_e}")


# Ensure table exists on first import
def _ensure_db() -> None:
    try:
        with _connect() as con:
            # Enable WAL mode for better concurrent write performance
            con.execute("PRAGMA journal_mode=WAL")
            con.execute("PRAGMA synchronous=NORMAL")
            con.execute("""
                CREATE TABLE IF NOT EXISTS metrics (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp REAL NOT NULL,
                    exchange TEXT NOT NULL,
                    symbol TEXT NOT NULL,
                    order_book_imbalance REAL,
                    liquidity_wall_detection REAL,
                    orderflow_delta REAL,
                    liquidity_pressure REAL,
                    UNIQUE(timestamp, exchange, symbol)
                )
            """)
            con.execute(
                "CREATE INDEX IF NOT EXISTS idx_time_symbol ON metrics(timestamp, symbol)"
            )
            con.execute(
                "CREATE INDEX IF NOT EXISTS idx_exchange_symbol ON metrics(exchange, symbol)"
            )
    except Exception as _e:
        log.warning(f"[MicroStore] DB init warning: {_e}")


def _maybe_maintain() -> None:
    """Periodic retention + WAL checkpoint to keep DB footprint bounded."""
    global _last_maintenance_ts
    now = time.time()
    if now - _last_maintenance_ts < _MAINTENANCE_INTERVAL_SEC:
        return
    _last_maintenance_ts = now

    try:
        cutoff = now - (_KEEP_HOURS * 3600)
        with _connect() as con:
            con.execute("DELETE FROM metrics WHERE timestamp < ?", (cutoff,))
            con.commit()
            # Reclaim WAL growth for cloud/local backup friendliness.
            con.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    except Exception as _e:
        log.warning(f"[MicroStore] Maintenance warning: {_e}")


_maybe_migrate_legacy_db()
_ensure_db()


def init_db() -> None:
    """Initialize SQLite database for microstructure metrics."""
    with _connect() as con:
        con.execute("PRAGMA journal_mode=WAL")
        con.execute("""
            CREATE TABLE IF NOT EXISTS metrics (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp REAL NOT NULL,
                exchange TEXT NOT NULL,
                symbol TEXT NOT NULL,
                order_book_imbalance REAL,
                liquidity_wall_detection REAL,
                orderflow_delta REAL,
                liquidity_pressure REAL,
                UNIQUE(timestamp, exchange, symbol)
            )
        """)
        con.execute(
            "CREATE INDEX IF NOT EXISTS idx_time_symbol ON metrics(timestamp, symbol)"
        )
        con.execute(
            "CREATE INDEX IF NOT EXISTS idx_exchange_symbol ON metrics(exchange, symbol)"
        )
    log.info("Microstructure DB initialized")


def store_metrics(metrics: Dict) -> None:
    """Store aggregated metrics; rejects raw orderbook data."""
    required_keys = {
        "timestamp",
        "exchange",
        "symbol",
        "order_book_imbalance",
        "liquidity_wall_detection",
        "orderflow_delta",
        "liquidity_pressure",
    }
    missing = required_keys - set(metrics.keys())
    if missing:
        log.warning(f"[MicroStore] Missing required keys: {missing}")
        return
    # Reject if raw orderbook present
    if "orderbook" in metrics:
        log.warning("[MicroStore] Rejected metrics containing raw orderbook")
        return
    try:
        with _db_lock:
            with _connect() as con:
                con.execute(
                    """
                    INSERT OR REPLACE INTO metrics
                    (timestamp, exchange, symbol, order_book_imbalance,
                     liquidity_wall_detection, orderflow_delta, liquidity_pressure)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                    (
                        metrics["timestamp"],
                        metrics["exchange"],
                        metrics["symbol"],
                        metrics["order_book_imbalance"],
                        metrics["liquidity_wall_detection"],
                        metrics["orderflow_delta"],
                        metrics["liquidity_pressure"],
                    ),
                )
            _maybe_maintain()
    except Exception as e:
        log.error(f"[MicroStore] Failed to store metrics: {e}")


def query_latest(symbol: str, exchange: str, limit: int = 100) -> List[Dict]:
    """Query latest aggregated metrics for a symbol."""
    try:
        with _connect() as con:
            cur = con.execute(
                """
                SELECT timestamp, exchange, symbol,
                       order_book_imbalance, liquidity_wall_detection,
                       orderflow_delta, liquidity_pressure
                FROM metrics
                WHERE symbol = ? AND exchange = ?
                ORDER BY timestamp DESC
                LIMIT ?
            """,
                (symbol, exchange, limit),
            )
            rows = cur.fetchall()
            return [
                {
                    "timestamp": r[0],
                    "exchange": r[1],
                    "symbol": r[2],
                    "order_book_imbalance": r[3],
                    "liquidity_wall_detection": r[4],
                    "orderflow_delta": r[5],
                    "liquidity_pressure": r[6],
                }
                for r in rows
            ]
    except Exception as e:
        log.error(f"[MicroStore] Query failed: {e}")
        return []


def purge_old(keep_hours: int = 24) -> None:
    """Purge metrics older than keep_hours."""
    cutoff = time.time() - keep_hours * 3600
    try:
        with _connect() as con:
            cur = con.execute("DELETE FROM metrics WHERE timestamp < ?", (cutoff,))
            deleted = cur.rowcount
            if deleted:
                log.info(
                    f"[MicroStore] Purged {deleted} old records (older than {keep_hours}h)"
                )
    except Exception as e:
        log.error(f"[MicroStore] Purge failed: {e}")


if __name__ == "__main__":
    init_db()
    # Example
    example = {
        "timestamp": time.time(),
        "exchange": "binance",
        "symbol": "BTCUSDT",
        "order_book_imbalance": 0.12,
        "liquidity_wall_detection": -0.05,
        "orderflow_delta": 0.34,
        "liquidity_pressure": 0.23,
    }
    store_metrics(example)
    print("Stored example metrics")
    print(query_latest("BTCUSDT", "binance", 5))
