"""Persistent candle cache for backtest-only historical fetches.

This cache is intentionally separate from the live candle cache.  Backtests ask
for deep H1/H4 histories and can safely reuse closed historical bars across
runs, while live scans must keep using their existing freshness rules.
"""

from __future__ import annotations

import logging
import math
import sqlite3
import threading
import time
from datetime import datetime, timezone
from typing import Callable

from runtime_paths import resolve_backtest_candle_cache_db_path

log = logging.getLogger("sentinel")

_LOCK = threading.Lock()
_TF_SECONDS = {
    "M1": 60,
    "M5": 300,
    "M15": 900,
    "H1": 3600,
    "H4": 14400,
    "D1": 86400,
}


def _db_path() -> str:
    return str(resolve_backtest_candle_cache_db_path())


def _init_db() -> None:
    with sqlite3.connect(_db_path(), timeout=15.0) as con:
        con.execute("PRAGMA journal_mode=WAL")
        con.execute(
            """
            CREATE TABLE IF NOT EXISTS backtest_candles (
                cache_key TEXT NOT NULL,
                display TEXT,
                symbol TEXT,
                source TEXT,
                asset_type TEXT,
                timeframe TEXT NOT NULL,
                bar_time TEXT NOT NULL,
                open REAL NOT NULL,
                high REAL NOT NULL,
                low REAL NOT NULL,
                close REAL NOT NULL,
                volume REAL DEFAULT 0,
                provider TEXT,
                updated_at REAL NOT NULL,
                PRIMARY KEY (cache_key, timeframe, bar_time)
            )
            """
        )
        con.execute(
            "CREATE INDEX IF NOT EXISTS idx_bt_candles_lookup "
            "ON backtest_candles(cache_key, timeframe, bar_time)"
        )


def _cache_key(pair: dict) -> str:
    source = str(pair.get("source") or "").strip().lower() or "unknown"
    symbol = str(pair.get("symbol") or pair.get("display") or "").strip()
    return f"{source}:{symbol}"


def _scoped_pair(pair: dict, scope: str) -> dict:
    scoped = dict(pair)
    scoped["source"] = f"{pair.get('source') or 'unknown'}:{scope}"
    return scoped


def _parse_epoch(value) -> float | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        raw = float(value)
        return raw / 1000.0 if raw > 1e12 else raw
    text = str(value).strip()
    if not text:
        return None
    try:
        dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        try:
            dt = datetime.strptime(text[:19], "%Y-%m-%d %H:%M:%S")
        except ValueError:
            try:
                dt = datetime.strptime(text[:10], "%Y-%m-%d")
            except ValueError:
                return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.timestamp()


def _canonical_time(value, tf: str) -> str | None:
    epoch = _parse_epoch(value)
    if epoch is None:
        return None
    dt = datetime.fromtimestamp(epoch, tz=timezone.utc)
    if tf == "D1" and dt.hour == 0 and dt.minute == 0 and dt.second == 0:
        return dt.strftime("%Y-%m-%d") if dt.microsecond == 0 else dt.isoformat()
    return dt.isoformat()


def _read_rows(pair: dict, tf: str, limit: int) -> list[dict]:
    _init_db()
    key = _cache_key(pair)
    with sqlite3.connect(_db_path(), timeout=15.0) as con:
        con.row_factory = sqlite3.Row
        rows = con.execute(
            """
            SELECT bar_time, open, high, low, close, volume
            FROM backtest_candles
            WHERE cache_key=? AND timeframe=?
            ORDER BY bar_time DESC
            LIMIT ?
            """,
            (key, tf, int(limit)),
        ).fetchall()
    candles = [
        {
            "time": r["bar_time"],
            "open": float(r["open"]),
            "high": float(r["high"]),
            "low": float(r["low"]),
            "close": float(r["close"]),
            "vol": float(r["volume"] or 0),
        }
        for r in reversed(rows)
    ]
    return candles


def _cache_state(pair: dict, tf: str) -> tuple[int, str | None, float | None]:
    _init_db()
    key = _cache_key(pair)
    with sqlite3.connect(_db_path(), timeout=15.0) as con:
        row = con.execute(
            """
            SELECT COUNT(*), MAX(bar_time)
            FROM backtest_candles
            WHERE cache_key=? AND timeframe=?
            """,
            (key, tf),
        ).fetchone()
    count = int(row[0] or 0) if row else 0
    newest = row[1] if row else None
    return count, newest, _parse_epoch(newest)


def _is_fresh(tf: str, newest_epoch: float | None) -> bool:
    if newest_epoch is None:
        return False
    tf_sec = _TF_SECONDS.get(tf, 3600)
    # Once the newest cached bar is more than roughly two bar lengths old, top up
    # instead of assuming the deep historical series is current.
    max_age = tf_sec * 2
    return (time.time() - newest_epoch) <= max_age


def _topup_limit(tf: str, newest_epoch: float | None, requested_limit: int) -> int:
    if newest_epoch is None:
        return int(requested_limit)
    tf_sec = _TF_SECONDS.get(tf, 3600)
    missing = max(1, math.ceil((time.time() - newest_epoch) / tf_sec))
    return min(int(requested_limit), max(missing + 10, 100))


def _store_rows(pair: dict, tf: str, candles: list[dict] | None, provider: str) -> int:
    if not candles:
        return 0
    _init_db()
    key = _cache_key(pair)
    now = time.time()
    rows = []
    for candle in candles:
        bar_time = _canonical_time(candle.get("time") or candle.get("datetime"), tf)
        if not bar_time:
            continue
        try:
            rows.append(
                (
                    key,
                    pair.get("display"),
                    pair.get("symbol"),
                    pair.get("source"),
                    pair.get("type"),
                    tf,
                    bar_time,
                    float(candle.get("open")),
                    float(candle.get("high")),
                    float(candle.get("low")),
                    float(candle.get("close")),
                    float(candle.get("vol", candle.get("volume", 0)) or 0),
                    provider,
                    now,
                )
            )
        except (TypeError, ValueError):
            continue
    if not rows:
        return 0
    with _LOCK:
        with sqlite3.connect(_db_path(), timeout=15.0) as con:
            con.execute("PRAGMA journal_mode=WAL")
            con.executemany(
                """
                INSERT OR REPLACE INTO backtest_candles
                (cache_key, display, symbol, source, asset_type, timeframe, bar_time,
                 open, high, low, close, volume, provider, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                rows,
            )
    return len(rows)


def fetch_backtest_candles(
    pair: dict,
    tf: str,
    limit: int,
    fetcher: Callable[[int], list[dict] | None],
    *,
    provider: str,
    min_bars: int | None = None,
) -> list[dict] | None:
    """Return backtest candles from disk, fetching only missing recent bars when possible."""
    tf = str(tf or "").upper()
    need = int(min_bars or limit)
    try:
        count, newest, newest_epoch = _cache_state(pair, tf)
        display = pair.get("display", pair.get("symbol", "unknown"))
        if count >= need and _is_fresh(tf, newest_epoch):
            log.info("[BT-CACHE] hit %s %s bars=%s newest=%s", display, tf, count, newest)
            return _read_rows(pair, tf, limit)

        fetch_limit = int(limit) if count < need else _topup_limit(tf, newest_epoch, int(limit))
        log.info(
            "[BT-CACHE] refresh %s %s cached=%s need=%s newest=%s fetch_limit=%s",
            display,
            tf,
            count,
            need,
            newest,
            fetch_limit,
        )
        fresh = fetcher(fetch_limit)
        _store_rows(pair, tf, fresh, provider)
        rows = _read_rows(pair, tf, limit)
        return rows if rows else fresh
    except Exception as exc:
        log.warning(
            "[BT-CACHE] bypass %s %s provider=%s: %s",
            pair.get("display", pair.get("symbol", "unknown")),
            tf,
            provider,
            exc,
        )
        return fetcher(int(limit))


def fetch_backtest_eodhd_intraday(
    pair: dict,
    days: int,
    fetcher: Callable[[int], tuple[list[dict] | None, list[dict] | None]],
    *,
    h4_limit: int = 4400,
    h1_limit: int = 17600,
) -> tuple[list[dict] | None, list[dict] | None]:
    """Cache EODHD backtest H1/H4 intraday pulls and top up stale history."""
    cache_pair = _scoped_pair(pair, "eodhd_intraday")
    try:
        h1_count, h1_newest, h1_epoch = _cache_state(cache_pair, "H1")
        h4_count, h4_newest, h4_epoch = _cache_state(cache_pair, "H4")
        h1_ok = h1_count >= min(h1_limit, int(days) * 20) and _is_fresh("H1", h1_epoch)
        h4_ok = h4_count >= min(h4_limit, int(days) * 5) and _is_fresh("H4", h4_epoch)
        display = pair.get("display", pair.get("symbol", "unknown"))
        if h1_ok and h4_ok:
            log.info(
                "[BT-CACHE] hit %s EODHD intraday H1=%s H4=%s newest=%s/%s",
                display,
                h1_count,
                h4_count,
                h1_newest,
                h4_newest,
            )
            return _read_rows(cache_pair, "H4", h4_limit), _read_rows(cache_pair, "H1", h1_limit)

        newest_epoch = min([e for e in (h1_epoch, h4_epoch) if e is not None], default=None)
        if h1_count <= 0 or h4_count <= 0:
            fetch_days = int(days)
        else:
            behind_days = max(1, math.ceil((time.time() - (newest_epoch or 0)) / 86400))
            fetch_days = min(int(days), max(behind_days + 3, 7))
        log.info(
            "[BT-CACHE] refresh %s EODHD intraday H1=%s H4=%s fetch_days=%s",
            display,
            h1_count,
            h4_count,
            fetch_days,
        )
        h4, h1 = fetcher(fetch_days)
        _store_rows(cache_pair, "H4", h4, "eodhd_intraday")
        _store_rows(cache_pair, "H1", h1, "eodhd_intraday")
        cached_h4 = _read_rows(cache_pair, "H4", h4_limit)
        cached_h1 = _read_rows(cache_pair, "H1", h1_limit)
        return cached_h4 if cached_h4 else h4, cached_h1 if cached_h1 else h1
    except Exception as exc:
        log.warning(
            "[BT-CACHE] bypass %s EODHD intraday: %s",
            pair.get("display", pair.get("symbol", "unknown")),
            exc,
        )
        return fetcher(int(days))
