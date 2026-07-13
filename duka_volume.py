"""
duka_volume.py — Dukascopy tick volume for forex pairs.

Downloads tick data from Dukascopy's public datafeed, aggregates to H1/H4
volume per bar, caches in SQLite. Provides the 'volume' factor for forex
in the factor scoring engine.

Volume source: Dukascopy ECN-aggregated tick flow (ask_vol + bid_vol per tick).
This is actual interbank volume, not MT5 tick count.

Usage:
    from duka_volume import get_forex_volume_bars, get_forex_vr

    # Get list of H1 {time, vol} bars for a pair
    bars = get_forex_volume_bars("EUR/USD", tf="H1", days=60)

    # Get current volume ratio (current bar vol / 20-bar SMA) — used in scoring
    vr = get_forex_vr("EUR/USD", tf="H1", lookback=20)
"""

import os
import sqlite3
import struct
import logging
import datetime
import threading
from collections import defaultdict
from lzma import LZMADecompressor, LZMAError, FORMAT_AUTO
from typing import Optional

import requests

log = logging.getLogger("sentinel")

# ── Symbol map: Athena display → Dukascopy symbol ────────────────────────────
DUKA_SYMBOL_MAP = {
    "EUR/USD": "EURUSD",
    "GBP/USD": "GBPUSD",
    "USD/JPY": "USDJPY",
    "AUD/USD": "AUDUSD",
    "NZD/USD": "NZDUSD",
    "EUR/GBP": "EURGBP",
    "USD/CAD": "USDCAD",
    "USD/CHF": "USDCHF",
    "EUR/JPY": "EURJPY",
    "GBP/JPY": "GBPJPY",
    "AUD/JPY": "AUDJPY",
    "EUR/AUD": "EURAUD",
    "GBP/AUD": "GBPAUD",
    "USD/ZAR": "USDZAR",
    "EUR/CHF": "EURCHF",
    "USD/MXN": "USDMXN",
    "USD/SGD": "USDSGD",
}

# Dukascopy URL — month is 0-indexed (Jan=0, Feb=1, ... Dec=11)
_URL = (
    "https://www.dukascopy.com/datafeed/"
    "{symbol}/{year}/{month:02d}/{day:02d}/{hour:02d}h_ticks.bi5"
)

_TOKEN_SIZE = 20  # bytes per raw tick record (struct !IIIff)
_TIMEOUT_S = 15  # per-hour HTTP timeout
_H1_SECONDS = 3600
_H4_SECONDS = 14400

_DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "duka_volume.db")
_db_lock = threading.Lock()


# ── SQLite cache ──────────────────────────────────────────────────────────────


def _init_db():
    with _db_lock:
        con = sqlite3.connect(_DB_PATH, timeout=15.0)
        con.execute("PRAGMA journal_mode=WAL")
        con.execute("""
            CREATE TABLE IF NOT EXISTS forex_volume (
                symbol  TEXT    NOT NULL,
                tf      TEXT    NOT NULL,
                bar_ts  INTEGER NOT NULL,
                volume  REAL    NOT NULL,
                PRIMARY KEY (symbol, tf, bar_ts)
            )
        """)
        con.execute(
            "CREATE INDEX IF NOT EXISTS idx_fv_sym_tf ON forex_volume (symbol, tf, bar_ts)"
        )
        con.commit()
        con.close()


_init_db()


# ── Low-level fetch + decompress ──────────────────────────────────────────────


def _fetch_hour_raw(duka_symbol: str, day: datetime.date, hour: int) -> bytes:
    """Download one hour of tick data. Returns empty bytes on failure (market closed etc)."""
    url = _URL.format(
        symbol=duka_symbol,
        year=day.year,
        month=day.month - 1,  # Dukascopy is 0-indexed
        day=day.day,
        hour=hour,
    )
    try:
        resp = requests.get(url, timeout=_TIMEOUT_S)
        if resp.status_code == 200 and resp.content:
            return resp.content
        return b""
    except Exception:
        return b""


def _decompress_bi5(data: bytes, day: datetime.date) -> list:
    """Decompress a .bi5 buffer and return list of (datetime, ask, bid, ask_vol, bid_vol)."""
    if not data:
        return []
    try:
        decomp = LZMADecompressor(FORMAT_AUTO)
        raw = decomp.decompress(data)
    except (LZMAError, Exception):
        return []

    n_tokens = len(raw) // _TOKEN_SIZE
    ticks = []
    for i in range(n_tokens):
        chunk = raw[i * _TOKEN_SIZE : (i + 1) * _TOKEN_SIZE]
        ms_offset, ask_raw, bid_raw, ask_vol_f, bid_vol_f = struct.unpack(
            "!IIIff", chunk
        )
        dt = datetime.datetime(day.year, day.month, day.day) + datetime.timedelta(
            milliseconds=ms_offset
        )
        ask = ask_raw / 100_000
        bid = bid_raw / 100_000
        ask_vol = round(ask_vol_f * 1_000_000)
        bid_vol = round(bid_vol_f * 1_000_000)
        ticks.append((dt, ask, bid, ask_vol, bid_vol))
    return ticks


# ── Per-day fetch → H1/H4 volume aggregation ─────────────────────────────────


def _fetch_day_volumes(duka_symbol: str, day: datetime.date) -> dict:
    """
    Download all 24 hours for one day, aggregate tick volumes to H1 and H4 bars.
    Returns {(tf_seconds, bar_unix_ts): total_volume}.
    """
    h1_vols: dict[int, float] = defaultdict(float)

    for hour in range(24):
        raw = _fetch_hour_raw(duka_symbol, day, hour)
        ticks = _decompress_bi5(raw, day)
        for dt, _ask, _bid, ask_vol, bid_vol in ticks:
            unix = int(dt.timestamp())
            h1_bar = unix - (unix % _H1_SECONDS)
            h1_vols[h1_bar] += ask_vol + bid_vol

    # Build H4 from H1
    h4_vols: dict[int, float] = defaultdict(float)
    for h1_ts, vol in h1_vols.items():
        h4_bar = h1_ts - (h1_ts % _H4_SECONDS)
        h4_vols[h4_bar] += vol

    result = {}
    for ts, vol in h1_vols.items():
        result[(_H1_SECONDS, ts)] = vol
    for ts, vol in h4_vols.items():
        result[(_H4_SECONDS, ts)] = vol
    return result


# ── Cache write / read ────────────────────────────────────────────────────────


def _cache_write(duka_symbol: str, volumes: dict):
    tf_name = {_H1_SECONDS: "H1", _H4_SECONDS: "H4"}
    rows = [
        (duka_symbol, tf_name[tf_sec], bar_ts, vol)
        for (tf_sec, bar_ts), vol in volumes.items()
        if tf_sec in tf_name
    ]
    if not rows:
        return
    with _db_lock:
        con = sqlite3.connect(_DB_PATH, timeout=15.0)
        con.executemany(
            "INSERT OR REPLACE INTO forex_volume (symbol, tf, bar_ts, volume) VALUES (?,?,?,?)",
            rows,
        )
        con.commit()
        con.close()


def _cache_read(duka_symbol: str, tf: str, from_ts: int, to_ts: int) -> list:
    """Return [(bar_ts, volume)] sorted ascending."""
    with _db_lock:
        con = sqlite3.connect(_DB_PATH, timeout=15.0)
        rows = con.execute(
            "SELECT bar_ts, volume FROM forex_volume "
            "WHERE symbol=? AND tf=? AND bar_ts>=? AND bar_ts<=? ORDER BY bar_ts",
            (duka_symbol, tf, from_ts, to_ts),
        ).fetchall()
        con.close()
    return rows


def _newest_cached_ts(duka_symbol: str, tf: str) -> Optional[int]:
    with _db_lock:
        con = sqlite3.connect(_DB_PATH, timeout=15.0)
        row = con.execute(
            "SELECT MAX(bar_ts) FROM forex_volume WHERE symbol=? AND tf=?",
            (duka_symbol, tf),
        ).fetchone()
        con.close()
    return row[0] if row and row[0] else None


# ── Public: download and cache N days ────────────────────────────────────────


def fetch_and_cache(display: str, days: int = 90):
    """
    Download and cache the last `days` calendar days of tick volume for a forex pair.
    Skips days already cached. Safe to call repeatedly — only fetches missing days.

    Args:
        display: Athena display name e.g. "EUR/USD"
        days:    Number of calendar days to look back (default 90)
    """
    duka_sym = DUKA_SYMBOL_MAP.get(display)
    if not duka_sym:
        log.warning(f"[DUKA] No symbol mapping for '{display}'")
        return

    today = datetime.date.today()
    start_day = today - datetime.timedelta(days=days)

    # Find newest cached timestamp to skip already-fetched days
    newest_ts = _newest_cached_ts(duka_sym, "H1")
    if newest_ts:
        cached_up_to = datetime.date.fromtimestamp(newest_ts)
        # Re-fetch last 2 days in case the current day was incomplete
        start_day = max(start_day, cached_up_to - datetime.timedelta(days=2))

    current = start_day
    fetched = 0
    while current <= today:
        # Skip weekends (Dukascopy has no data Sat/Sun)
        if current.weekday() < 5:
            vols = _fetch_day_volumes(duka_sym, current)
            if vols:
                _cache_write(duka_sym, vols)
                fetched += 1
        current += datetime.timedelta(days=1)

    log.info(f"[DUKA] {display}: fetched {fetched} days, cached in {_DB_PATH}")


# ── Public: get volume bars ───────────────────────────────────────────────────


def get_forex_volume_bars(
    display: str, tf: str = "H1", days: int = 60, blocking_fetch: bool = True
) -> list:
    """
    Return a list of dicts [{time_unix, volume}] for the last `days` days.

    Args:
        display:        Athena display name e.g. "EUR/USD"
        tf:             "H1" or "H4"
        days:           lookback in calendar days
        blocking_fetch: If True, downloads missing data synchronously (slow, use for CLI/seed).
                        If False, returns whatever's cached (fast, use during scan).

    Returns:
        List of {time: unix_ts, vol: float} sorted ascending, or [] on error.
    """
    duka_sym = DUKA_SYMBOL_MAP.get(display)
    if not duka_sym:
        return []

    now = datetime.datetime.utcnow()
    to_ts = int(now.timestamp())
    from_ts = int((now - datetime.timedelta(days=days)).timestamp())

    rows = _cache_read(duka_sym, tf, from_ts, to_ts)
    if len(rows) < 20 and blocking_fetch:
        log.info(
            f"[DUKA] {display} {tf}: cache insufficient ({len(rows)} bars), fetching..."
        )
        fetch_and_cache(display, days=days + 5)
        rows = _cache_read(duka_sym, tf, from_ts, to_ts)

    return [{"time": r[0], "vol": r[1]} for r in rows]


# ── Public: get volume ratio (current / SMA) for scoring ─────────────────────


def get_forex_vr(
    display: str,
    tf: str = "H1",
    lookback: int = 20,
    *,
    force_refresh: bool = False,
) -> float:
    """
    Return current volume ratio = latest_bar_volume / SMA(lookback bars).

    Used as the `vr` parameter in calc_confluence for forex pairs.
    Returns 1.0 (neutral) if data is unavailable.

    Args:
        display:  Athena display name e.g. "EUR/USD"
        tf:       "H1" or "H4"
        lookback: SMA window (default 20 bars)

    Returns:
        float >= 0.0; typical range 0.3–3.0; 1.0 = average volume.
    """
    if force_refresh:
        # Manual scans refresh the two most recent trading days before using the
        # local series. Background scans remain read-only and inexpensive.
        fetch_and_cache(display, days=60)
    bars = get_forex_volume_bars(display, tf=tf, days=60, blocking_fetch=False)
    if len(bars) < lookback + 1:
        return 1.0

    recent_bar = bars[-1]
    if recent_bar["vol"] <= 0:
        return 1.0

    import datetime

    try:
        recent_dt = datetime.datetime.fromtimestamp(
            recent_bar["time"], datetime.timezone.utc
        )
        recent_hour = recent_dt.hour
    except Exception:
        return 1.0

    tod_vols = []
    for b in reversed(bars[:-1]):
        if b["vol"] > 0:
            try:
                dt = datetime.datetime.fromtimestamp(b["time"], datetime.timezone.utc)
                if dt.hour == recent_hour:
                    tod_vols.append(b["vol"])
                    if len(tod_vols) >= lookback:
                        break
            except Exception:
                continue

    if len(tod_vols) < 3:
        return 1.0

    sma = sum(tod_vols) / len(tod_vols)
    if sma <= 0:
        return 1.0

    return round(min(recent_bar["vol"] / sma, 5.0), 3)


# ── CLI: bulk seed all forex pairs ───────────────────────────────────────────


def seed_all_forex(days: int = 90, workers: int = 4):
    """
    Download and cache `days` of volume data for all 17 forex pairs.
    Uses thread pool for parallel fetching.

    Args:
        days:    Calendar days to fetch (default 90)
        workers: Parallel threads (default 4; be polite to Dukascopy)
    """
    from concurrent.futures import ThreadPoolExecutor, as_completed

    pairs = list(DUKA_SYMBOL_MAP.keys())
    log.info(f"[DUKA] Seeding {len(pairs)} forex pairs, {days} days each...")
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futs = {ex.submit(fetch_and_cache, p, days): p for p in pairs}
        for fut in as_completed(futs):
            pair = futs[fut]
            exc = fut.exception()
            if exc:
                log.warning(f"[DUKA] {pair} seed failed: {exc}")
            else:
                log.info(f"[DUKA] {pair} seeded OK")
    log.info("[DUKA] Seed complete")


if __name__ == "__main__":
    import sys

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    days = int(sys.argv[1]) if len(sys.argv) > 1 else 90
    workers = int(sys.argv[2]) if len(sys.argv) > 2 else 3
    seed_all_forex(days=days, workers=workers)
    print("\nDone. Testing EUR/USD VR...")
    vr = get_forex_vr("EUR/USD")
    print(f"EUR/USD H1 volume ratio: {vr}")
