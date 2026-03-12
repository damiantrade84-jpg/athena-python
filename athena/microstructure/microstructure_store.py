"""microstructure_store.py — Persistent storage for aggregated microstructure metrics.

Never stores raw order book updates; only stores aggregated metrics per interval.
"""
import sqlite3
import json
import logging
import time
from typing import Dict, List, Optional, Tuple
from pathlib import Path

log = logging.getLogger("sentinel")

DB_PATH = Path(__file__).parent.parent.parent / "microstructure.db"

# Ensure table exists on first import
def _ensure_db() -> None:
    try:
        with sqlite3.connect(DB_PATH, timeout=10.0) as con:
            # Enable WAL mode for better concurrent write performance
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
            con.execute("CREATE INDEX IF NOT EXISTS idx_time_symbol ON metrics(timestamp, symbol)")
            con.execute("CREATE INDEX IF NOT EXISTS idx_exchange_symbol ON metrics(exchange, symbol)")
    except Exception as _e:
        log.warning(f"[MicroStore] DB init warning: {_e}")

_ensure_db()


def init_db() -> None:
    """Initialize SQLite database for microstructure metrics."""
    with sqlite3.connect(DB_PATH, timeout=10.0) as con:
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
        con.execute("CREATE INDEX IF NOT EXISTS idx_time_symbol ON metrics(timestamp, symbol)")
        con.execute("CREATE INDEX IF NOT EXISTS idx_exchange_symbol ON metrics(exchange, symbol)")
    log.info("Microstructure DB initialized")

def store_metrics(metrics: Dict) -> None:
    """Store aggregated metrics; rejects raw orderbook data."""
    required_keys = {"timestamp", "exchange", "symbol", "order_book_imbalance",
                     "liquidity_wall_detection", "orderflow_delta", "liquidity_pressure"}
    missing = required_keys - set(metrics.keys())
    if missing:
        log.warning(f"[MicroStore] Missing required keys: {missing}")
        return
    # Reject if raw orderbook present
    if "orderbook" in metrics:
        log.warning("[MicroStore] Rejected metrics containing raw orderbook")
        return
    try:
        with sqlite3.connect(DB_PATH, timeout=10.0) as con:
            con.execute("""
                INSERT OR REPLACE INTO metrics
                (timestamp, exchange, symbol, order_book_imbalance,
                 liquidity_wall_detection, orderflow_delta, liquidity_pressure)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (
                metrics["timestamp"],
                metrics["exchange"],
                metrics["symbol"],
                metrics["order_book_imbalance"],
                metrics["liquidity_wall_detection"],
                metrics["orderflow_delta"],
                metrics["liquidity_pressure"],
            ))
    except Exception as e:
        log.error(f"[MicroStore] Failed to store metrics: {e}")

def query_latest(symbol: str, exchange: str, limit: int = 100) -> List[Dict]:
    """Query latest aggregated metrics for a symbol."""
    try:
        with sqlite3.connect(DB_PATH, timeout=10.0) as con:
            cur = con.execute("""
                SELECT timestamp, exchange, symbol,
                       order_book_imbalance, liquidity_wall_detection,
                       orderflow_delta, liquidity_pressure
                FROM metrics
                WHERE symbol = ? AND exchange = ?
                ORDER BY timestamp DESC
                LIMIT ?
            """, (symbol, exchange, limit))
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
        with sqlite3.connect(DB_PATH, timeout=10.0) as con:
            cur = con.execute("DELETE FROM metrics WHERE timestamp < ?", (cutoff,))
            deleted = cur.rowcount
            if deleted:
                log.info(f"[MicroStore] Purged {deleted} old records (older than {keep_hours}h)")
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
