"""Price-level trade bucket storage for crypto orderflow.

Stores aggregated taker volume by symbol/session/price bucket. Raw trades are
not persisted.
"""

from __future__ import annotations

from datetime import datetime, timezone
import logging
import os
from pathlib import Path
import sqlite3
import threading
import time
from typing import Iterable

log = logging.getLogger("sentinel")

_DB_LOCK = threading.Lock()
_KEEP_HOURS = int(os.environ.get("TRADE_BUCKET_KEEP_HOURS", "168"))


def _resolve_db_path() -> Path:
    env_path = os.environ.get("TRADE_BUCKET_DB_PATH", "").strip()
    if env_path:
        p = Path(env_path).expanduser()
        p.parent.mkdir(parents=True, exist_ok=True)
        return p
    if os.name == "nt":
        local_app_data = os.environ.get("LOCALAPPDATA", "").strip()
        if local_app_data:
            p = Path(local_app_data) / "Athena" / "trade_buckets.db"
            p.parent.mkdir(parents=True, exist_ok=True)
            return p
    return Path(__file__).parent.parent.parent / "trade_buckets.db"


DB_PATH = _resolve_db_path()


def _session_id(ts: float | None = None) -> str:
    dt = datetime.fromtimestamp(float(ts or time.time()), tz=timezone.utc)
    return dt.strftime("%Y-%m-%d")


def _ensure_db() -> None:
    try:
        with sqlite3.connect(DB_PATH, timeout=15.0) as con:
            con.execute("PRAGMA journal_mode=WAL")
            con.execute("PRAGMA synchronous=NORMAL")
            con.execute(
                """
                CREATE TABLE IF NOT EXISTS trade_buckets (
                    exchange TEXT NOT NULL,
                    symbol TEXT NOT NULL,
                    session_id TEXT NOT NULL,
                    price_bucket REAL NOT NULL,
                    bucket_size REAL NOT NULL,
                    buy_volume REAL NOT NULL DEFAULT 0,
                    sell_volume REAL NOT NULL DEFAULT 0,
                    total_volume REAL NOT NULL DEFAULT 0,
                    delta REAL NOT NULL DEFAULT 0,
                    first_ts REAL NOT NULL,
                    last_ts REAL NOT NULL,
                    trade_count INTEGER NOT NULL DEFAULT 0,
                    PRIMARY KEY(exchange, symbol, session_id, price_bucket)
                )
                """
            )
            con.execute(
                "CREATE INDEX IF NOT EXISTS idx_trade_buckets_lookup "
                "ON trade_buckets(exchange, symbol, session_id)"
            )
    except Exception as exc:
        log.warning("[TradeBuckets] DB init warning: %s", exc)


_ensure_db()


def default_bucket_size(price: float) -> float:
    """Return a conservative display-level bucket for crypto symbols."""
    p = abs(float(price or 0.0))
    if p >= 10_000:
        return 10.0
    if p >= 1_000:
        return 1.0
    if p >= 100:
        return 0.1
    if p >= 10:
        return 0.01
    if p >= 1:
        return 0.001
    return 0.0001


def bucket_price(price: float, bucket_size: float | None = None) -> tuple[float, float]:
    size = float(bucket_size or default_bucket_size(price))
    if size <= 0:
        size = default_bucket_size(price)
    bucket = round(round(float(price) / size) * size, 10)
    return bucket, size


def store_trade(
    *,
    exchange: str,
    symbol: str,
    price: float,
    quantity: float,
    is_buyer_maker: bool,
    ts: float | None = None,
    bucket_size: float | None = None,
) -> None:
    """Aggregate one trade into a price bucket.

    Binance ``m=True`` means buyer is maker, so the aggressor is a seller.
    """
    try:
        qty = float(quantity or 0.0)
        px = float(price or 0.0)
    except (TypeError, ValueError):
        return
    if qty <= 0 or px <= 0:
        return
    event_ts = float(ts or time.time())
    price_bucket, size = bucket_price(px, bucket_size)
    sess = _session_id(event_ts)
    buy_vol = 0.0 if is_buyer_maker else qty
    sell_vol = qty if is_buyer_maker else 0.0
    delta = buy_vol - sell_vol
    try:
        with _DB_LOCK:
            with sqlite3.connect(DB_PATH, timeout=15.0) as con:
                con.execute(
                    """
                    INSERT INTO trade_buckets
                    (exchange, symbol, session_id, price_bucket, bucket_size,
                     buy_volume, sell_volume, total_volume, delta, first_ts, last_ts, trade_count)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1)
                    ON CONFLICT(exchange, symbol, session_id, price_bucket) DO UPDATE SET
                        buy_volume = buy_volume + excluded.buy_volume,
                        sell_volume = sell_volume + excluded.sell_volume,
                        total_volume = total_volume + excluded.total_volume,
                        delta = delta + excluded.delta,
                        last_ts = MAX(last_ts, excluded.last_ts),
                        trade_count = trade_count + 1
                    """,
                    (
                        str(exchange).lower(),
                        str(symbol).replace("/", "").upper(),
                        sess,
                        price_bucket,
                        size,
                        buy_vol,
                        sell_vol,
                        qty,
                        delta,
                        event_ts,
                        event_ts,
                    ),
                )
    except Exception as exc:
        log.debug("[TradeBuckets] store failed: %s", exc)


def query_session_buckets(
    symbol: str,
    *,
    exchange: str = "binance",
    session_id: str | None = None,
    min_last_ts: float | None = None,
    max_last_ts: float | None = None,
) -> list[dict]:
    sess = session_id or _session_id()
    params: list = [str(exchange).lower(), str(symbol).replace("/", "").upper(), sess]
    clauses = []
    if min_last_ts is not None:
        clauses.append(" AND last_ts >= ?")
        params.append(float(min_last_ts))
    if max_last_ts is not None:
        clauses.append(" AND last_ts <= ?")
        params.append(float(max_last_ts))
    extra = "".join(clauses)
    try:
        with sqlite3.connect(DB_PATH, timeout=15.0) as con:
            con.row_factory = sqlite3.Row
            rows = con.execute(
                """
                SELECT price_bucket, bucket_size, buy_volume, sell_volume,
                       total_volume, delta, first_ts, last_ts, trade_count
                FROM trade_buckets
                WHERE exchange = ? AND symbol = ? AND session_id = ?
                """
                + extra
                + " ORDER BY price_bucket",
                params,
            ).fetchall()
            return [dict(r) for r in rows]
    except Exception as exc:
        log.debug("[TradeBuckets] query failed: %s", exc)
        return []


def purge_old(keep_hours: int | None = None) -> int:
    cutoff = time.time() - int(keep_hours or _KEEP_HOURS) * 3600
    try:
        with _DB_LOCK:
            with sqlite3.connect(DB_PATH, timeout=15.0) as con:
                cur = con.execute("DELETE FROM trade_buckets WHERE last_ts < ?", (cutoff,))
                return int(cur.rowcount or 0)
    except Exception as exc:
        log.debug("[TradeBuckets] purge failed: %s", exc)
        return 0
