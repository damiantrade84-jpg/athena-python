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
_WRITE_STATS_LOCK = threading.Lock()
_WRITE_EVENT_TS: list[float] = []
_LAST_WRITE_TS: dict[str, float] = {}
_KEEP_HOURS = int(os.environ.get("TRADE_BUCKET_KEEP_HOURS", "168"))
_STORE_FAIL_LOG_INTERVAL_SEC = 60.0
_last_store_fail_log_ts = 0.0


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
    """Return a conservative display-level bucket for crypto symbols.

    Sized to ~0.01% of mid so high-priced majors (BTC/ETH) still accumulate
    enough distinct price levels for TRADE_BUCKET_MIN_LEVELS within tight ranges.
    """
    p = abs(float(price or 0.0))
    if p <= 0:
        return 0.0001
    target = p * 0.0001
    if target >= 1.0:
        return 1.0
    if target >= 0.1:
        return 0.1
    if target >= 0.01:
        return 0.01
    if target >= 0.001:
        return 0.001
    if target >= 0.0001:
        return 0.0001
    return 0.00001


def _record_write_success(exchange: str) -> None:
    now = time.time()
    ex = str(exchange or "").strip().lower() or "unknown"
    with _WRITE_STATS_LOCK:
        _LAST_WRITE_TS[ex] = now
        _WRITE_EVENT_TS.append(now)
        cutoff = now - 60.0
        if len(_WRITE_EVENT_TS) > 5000:
            _WRITE_EVENT_TS[:] = [t for t in _WRITE_EVENT_TS if t >= cutoff]
        else:
            _WRITE_EVENT_TS[:] = [t for t in _WRITE_EVENT_TS if t >= cutoff]


def write_stats_snapshot() -> dict:
    """In-memory trade-bucket write telemetry (not persisted)."""
    now = time.time()
    with _WRITE_STATS_LOCK:
        recent = sum(1 for t in _WRITE_EVENT_TS if t >= now - 60.0)
        newest = max(_LAST_WRITE_TS.values()) if _LAST_WRITE_TS else None
        by_exchange = dict(_LAST_WRITE_TS)
    return {
        "writes_1m": recent,
        "newest_write_age_sec": round(now - newest, 3) if newest else None,
        "last_write_by_exchange": {
            ex: round(now - ts, 3) for ex, ts in by_exchange.items()
        },
    }


def trade_bucket_health_snapshot(
    exchange: str,
    *,
    max_age_sec: int = 900,
    min_levels: int = 3,
    symbols: Iterable[str] | None = None,
) -> dict:
    """Summarise SQLite bucket freshness for microstructure health endpoints."""
    ex = str(exchange or "binance").strip().lower()
    now = time.time()
    fresh_cutoff = now - int(max_age_sec)
    session_id = _session_id(now)
    sym_list = [
        str(s).replace("/", "").upper()
        for s in (symbols or [])
        if str(s or "").strip()
    ]
    out = {
        "db_path": str(DB_PATH),
        "exchange": ex,
        "session_id": session_id,
        "max_age_sec": int(max_age_sec),
        "min_levels": int(min_levels),
        "newest_bucket_age_sec": None,
        "fresh_symbol_count": 0,
        "monitored_symbol_count": len(sym_list),
    }
    try:
        with sqlite3.connect(DB_PATH, timeout=15.0) as con:
            row = con.execute(
                "SELECT MAX(last_ts) FROM trade_buckets WHERE exchange = ?",
                (ex,),
            ).fetchone()
            newest = float(row[0]) if row and row[0] is not None else None
            if newest is not None:
                out["newest_bucket_age_sec"] = round(now - newest, 3)

            if not sym_list:
                return out

            fresh_symbols = 0
            for sym in sym_list:
                cnt_row = con.execute(
                    """
                    SELECT COUNT(*) FROM trade_buckets
                    WHERE exchange = ? AND symbol = ? AND session_id = ?
                      AND last_ts >= ?
                    """,
                    (ex, sym, session_id, fresh_cutoff),
                ).fetchone()
                fresh_count = int(cnt_row[0] or 0) if cnt_row else 0
                if fresh_count >= int(min_levels):
                    fresh_symbols += 1
            out["fresh_symbol_count"] = fresh_symbols
    except Exception as exc:
        log.warning("[TradeBuckets] health snapshot failed: %s", exc)
        out["error"] = str(exc)
    return out


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
    ex_key = str(exchange).lower()
    sym_key = str(symbol).replace("/", "").upper()
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
                        ex_key,
                        sym_key,
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
                con.commit()
        _record_write_success(ex_key)
    except Exception as exc:
        global _last_store_fail_log_ts
        now = time.time()
        if now - _last_store_fail_log_ts >= _STORE_FAIL_LOG_INTERVAL_SEC:
            _last_store_fail_log_ts = now
            log.warning(
                "[TradeBuckets] store failed exchange=%s symbol=%s: %s",
                ex_key,
                sym_key,
                exc,
            )


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
