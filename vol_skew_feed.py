"""vol_skew_feed.py - FRED-backed implied-volatility regime factor.

Returns point-in-time z-scores for mapped option-volatility indexes:

  Gold / silver: GVZ
  S&P / Dow:     VIX
  Nasdaq:        VXN, falling back to VIX if VXN is unavailable

Positive z = elevated or backwardated volatility (fear / risk-off).
This module is a confluence data feed only. It never sizes trades or executes.
"""

from __future__ import annotations

import csv
import logging
import os
import sqlite3
import threading
import time
from bisect import bisect_right
from io import StringIO
from typing import Optional

import requests

from config import CONFIG

log = logging.getLogger("sentinel")

_DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "vol_skew_cache.db")
_db_lock = threading.Lock()
_fetch_guard_lock = threading.Lock()
_fetching_in_progress: set[str] = set()

_FRED_URL = "https://fred.stlouisfed.org/graph/fredgraph.csv?id={series}"
_TIMEOUT = 30
_FAIL_COOLDOWN = 3600
_SERIES_MEM_TTL = 3600
_MEM_TTL = 3600

_mem_cache: dict[tuple[str, Optional[str]], tuple[Optional[float], float]] = {}
_series_mem_cache: dict[str, tuple[float, list[str], list[float]]] = {}

_PAIR_VOL_INDEX = {
    "XAU/USD": "GVZCLS",
    "GLD": "GVZCLS",
    "XAG/USD": "GVZCLS",
    "S&P 500": "VIXCLS",
    "SPY": "VIXCLS",
    "NASDAQ-100": "VXNCLS",
    "QQQ": "VXNCLS",
    "Dow Jones": "VIXCLS",
}

_TERM_PAIR = {"VIXCLS": "VXVCLS"}


def _refresh_ttl() -> float:
    try:
        hours = float(CONFIG.get("ENGINE_A_VOL_SKEW_REFRESH_TTL_HOURS", 24) or 24)
    except (TypeError, ValueError):
        hours = 24.0
    return max(1.0, hours) * 3600.0


def _window() -> int:
    try:
        return max(4, int(CONFIG.get("ENGINE_A_VOL_SKEW_Z_WINDOW", 252) or 252))
    except (TypeError, ValueError):
        return 252


def _term_weight() -> float:
    try:
        return max(0.0, float(CONFIG.get("ENGINE_A_VOL_SKEW_TERM_WEIGHT", 0.3) or 0.0))
    except (TypeError, ValueError):
        return 0.3


def _init_db() -> None:
    with _db_lock:
        con = sqlite3.connect(_DB_PATH, timeout=15.0)
        con.execute(
            """
            CREATE TABLE IF NOT EXISTS vol_series (
                series_id TEXT NOT NULL,
                obs_date  TEXT NOT NULL,
                value     REAL NOT NULL,
                PRIMARY KEY (series_id, obs_date)
            )
            """
        )
        con.execute(
            "CREATE INDEX IF NOT EXISTS idx_vol_series ON vol_series(series_id, obs_date)"
        )
        con.execute(
            """
            CREATE TABLE IF NOT EXISTS vol_meta (
                series_id  TEXT PRIMARY KEY,
                last_fetch REAL NOT NULL
            )
            """
        )
        con.commit()
        con.close()


_init_db()


def _fetch_fred(series_id: str) -> list[tuple[str, float]]:
    """Download FRED CSV. Returns list of (YYYY-MM-DD, value) sorted asc."""
    url = _FRED_URL.format(series=series_id)
    resp = requests.get(url, timeout=_TIMEOUT)
    resp.raise_for_status()
    rows: list[tuple[str, float]] = []
    reader = csv.reader(StringIO(resp.text))
    next(reader, None)
    for row in reader:
        if len(row) < 2:
            continue
        date_str, val_str = row[0].strip(), row[1].strip()
        if not date_str or not val_str or val_str == ".":
            continue
        try:
            rows.append((date_str, float(val_str)))
        except ValueError:
            continue
    return rows


def _has_rows(series_id: str) -> bool:
    with _db_lock:
        con = sqlite3.connect(_DB_PATH, timeout=15.0)
        row = con.execute(
            "SELECT 1 FROM vol_series WHERE series_id=? LIMIT 1", (series_id,)
        ).fetchone()
        con.close()
    return bool(row)


def _needs_refresh(series_id: str) -> bool:
    with _db_lock:
        con = sqlite3.connect(_DB_PATH, timeout=15.0)
        row = con.execute(
            "SELECT last_fetch FROM vol_meta WHERE series_id=?", (series_id,)
        ).fetchone()
        con.close()
    if not row:
        return True
    return time.time() - float(row[0]) > _refresh_ttl()


def _write_series(series_id: str, rows: list[tuple[str, float]]) -> None:
    with _db_lock:
        con = sqlite3.connect(_DB_PATH, timeout=15.0)
        con.executemany(
            "INSERT OR REPLACE INTO vol_series (series_id, obs_date, value) VALUES (?,?,?)",
            [(series_id, d, v) for d, v in rows],
        )
        con.execute(
            "INSERT OR REPLACE INTO vol_meta (series_id, last_fetch) VALUES (?,?)",
            (series_id, time.time()),
        )
        con.commit()
        con.close()
    _series_mem_cache.pop(series_id, None)
    _mem_cache.clear()


def _mark_fetch_attempted(series_id: str) -> None:
    with _db_lock:
        con = sqlite3.connect(_DB_PATH, timeout=15.0)
        con.execute(
            "INSERT OR REPLACE INTO vol_meta (series_id, last_fetch) VALUES (?,?)",
            (series_id, time.time() - _refresh_ttl() + _FAIL_COOLDOWN),
        )
        con.commit()
        con.close()


def _ensure_series(series_id: str, *, blocking: bool = False) -> None:
    if not _needs_refresh(series_id):
        return

    with _fetch_guard_lock:
        if series_id in _fetching_in_progress:
            return
        _fetching_in_progress.add(series_id)

    def _do_fetch() -> None:
        try:
            rows = _fetch_fred(series_id)
            if rows:
                _write_series(series_id, rows)
                log.info("[VOL-SKEW] FRED %s: %s observations cached", series_id, len(rows))
            else:
                _mark_fetch_attempted(series_id)
        except Exception as exc:
            log.debug("[VOL-SKEW] FRED %s fetch failed: %s", series_id, exc)
            _mark_fetch_attempted(series_id)
        finally:
            with _fetch_guard_lock:
                _fetching_in_progress.discard(series_id)

    if blocking:
        _do_fetch()
    else:
        threading.Thread(target=_do_fetch, daemon=True, name=f"VOL-FRED-{series_id}").start()


def _series_rows(series_id: str, *, require_seed: bool = False) -> tuple[list[str], list[float]]:
    now = time.time()
    cached = _series_mem_cache.get(series_id)
    if cached and now - cached[0] < _SERIES_MEM_TTL:
        return cached[1], cached[2]

    _ensure_series(series_id, blocking=require_seed and not _has_rows(series_id))
    with _db_lock:
        con = sqlite3.connect(_DB_PATH, timeout=15.0)
        rows = con.execute(
            "SELECT obs_date, value FROM vol_series WHERE series_id=? ORDER BY obs_date ASC",
            (series_id,),
        ).fetchall()
        con.close()

    dates = [str(r[0]) for r in rows]
    values = [float(r[1]) for r in rows]
    _series_mem_cache[series_id] = (now, dates, values)
    return dates, values


def _value_series(series_id: str, as_of_date: str | None, window: int) -> list[float]:
    dates, values = _series_rows(series_id, require_seed=True)
    if not dates:
        return []
    end = bisect_right(dates, as_of_date) if as_of_date else len(dates)
    if end <= 0:
        return []
    start = max(0, end - int(window))
    return values[start:end]


def _ratio_series(
    front_id: str,
    term_id: str,
    as_of_date: str | None,
    window: int,
) -> list[float]:
    front_dates, front_values = _series_rows(front_id, require_seed=True)
    term_dates, term_values = _series_rows(term_id, require_seed=True)
    if not front_dates or not term_dates:
        return []

    ratios: list[float] = []
    for date_str, front in zip(front_dates, front_values):
        if as_of_date and date_str > as_of_date:
            break
        idx = bisect_right(term_dates, date_str)
        if idx <= 0:
            continue
        term = term_values[idx - 1]
        if term > 0:
            ratios.append(front / term)
    return ratios[-int(window):]


def _zscore(series: list[float], window: int = 252) -> Optional[float]:
    if len(series) < max(window // 2, 4):
        return None
    data = series[-window:]
    latest = data[-1]
    history = data[:-1]
    if len(history) < 3:
        return None
    mean = sum(history) / len(history)
    std = (sum((x - mean) ** 2 for x in history) / len(history)) ** 0.5
    if std <= 0:
        return None
    return max(-3.0, min(3.0, (latest - mean) / std))


def _level_z_for_series(series_id: str, as_of_date: str | None, window: int) -> Optional[float]:
    return _zscore(_value_series(series_id, as_of_date, window), window)


def get_vol_skew_z(display: str, as_of_date: str | None = None) -> float | None:
    """Return vol-regime z-score for a mapped pair.

    Positive = elevated/backwardated vol, a risk-off headwind for LONG risk assets.
    Returns None when no mapping, no data, insufficient history, or fetch errors.
    """
    display_key = str(display or "").strip()
    series_id = _PAIR_VOL_INDEX.get(display_key)
    if not series_id:
        return None

    as_of = (str(as_of_date or "")[:10] or None)
    cache_key = (display_key, as_of)
    now = time.time()
    if as_of is None:
        cached = _mem_cache.get(cache_key)
        if cached and now - cached[1] < _MEM_TTL:
            return cached[0]

    try:
        window = _window()
        level_id = series_id
        level_z = _level_z_for_series(level_id, as_of, window)
        if level_z is None and series_id == "VXNCLS":
            level_id = "VIXCLS"
            level_z = _level_z_for_series(level_id, as_of, window)
        if level_z is None:
            if as_of is None:
                _mem_cache[cache_key] = (None, now)
            return None

        result = float(level_z)
        term_id = _TERM_PAIR.get(level_id)
        if term_id:
            ratio_z = _zscore(_ratio_series(level_id, term_id, as_of, window), window)
            if ratio_z is not None:
                result += _term_weight() * ratio_z

        result = round(max(-3.0, min(3.0, result)), 3)
        if as_of is None:
            _mem_cache[cache_key] = (result, now)
        return result
    except Exception as exc:
        log.warning("[VOL-SKEW] %s lookup failed: %s", display_key, exc)
        if as_of is None:
            _mem_cache[cache_key] = (None, now)
        return None
