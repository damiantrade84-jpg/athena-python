"""carry_feed.py — Interest rate carry and yield spread factor.

Provides normalized carry signals for:

  Forex:   Central bank policy rate differential (base rate - quote rate).
           High positive carry → base currency pays more → carry trade support.
           Source: FRED (Federal Reserve Economic Data) — free, no API key.

  Crypto:  Not computed here — funding rate already handled in derivatives factor
           via Binance/Bybit API in athena.py.

  Indices: FRED 10-Year Treasury yield z-score (inverted).
           Rising yields → risk-off → headwind for equity indices.
           Applies to: S&P 500, Nasdaq, Dow Jones, DAX, UK100, ASX 200, Nikkei.

  Stocks:  Same 10Y yield signal as indices (macro risk factor).

Data: FRED REST API — https://fred.stlouisfed.org/graph/fredgraph.csv?id={SERIES}
      No API key required. Monthly data, updated with ~1 month lag.
      USD: FEDFUNDS (daily effective, more current than monthly averages)

Cache: in-memory (24h TTL) + SQLite backup.

Usage:
    from carry_feed import get_carry_z
    z = get_carry_z("EUR/USD")   # −0.8 → EUR rate < USD rate (carry opposes LONG)
    z = get_carry_z("USD/MXN")   # −1.5 → MXN rate >> USD rate (carry opposes LONG USD)
    z = get_carry_z("S&P 500")   # −1.2 → elevated 10Y yield → risk-off headwind
"""
import os
import csv
import sqlite3
import logging
import threading
import time
import datetime
from io import StringIO
from typing import Optional

import requests

log = logging.getLogger("sentinel")

_DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "carry_cache.db")
_db_lock = threading.Lock()
_TIMEOUT = 30
_FETCH_TTL = 86400          # re-download daily (rates change monthly but freshness matters)

# In-memory cache: pair → (z_score, fetched_at)
_mem_cache: dict = {}
_MEM_TTL = 24 * 3600        # 24-hour in-memory cache (rates change slowly)

# ── FRED series IDs per currency ──────────────────────────────────────────────
_FRED_URL = "https://fred.stlouisfed.org/graph/fredgraph.csv?id={series}"

# FRED only provides clean, reliable data for USD, EUR, GBP (confirmed working).
# AUD/JPY/NZD/CHF/ZAR/MXN/CAD are not available cleanly — use hardcoded fallback.
_FRED_CURRENCY_SERIES: dict[str, str] = {
    "USD": "DFF",       # Fed Funds Rate (daily effective — most current)
    "EUR": "ECBDFR",    # ECB Deposit Facility Rate
    "GBP": "BOERUKM",   # Bank of England base rate (monthly)
}

# 10Y US Treasury yield — for index/gold risk signal
_FRED_10Y_SERIES = "DGS10"   # 10-Year Treasury Constant Maturity Rate
_STATIC_10Y = 4.20           # Fallback when FRED is unreachable (~current 10Y yield, update manually)

# ── Hardcoded policy rates for currencies without reliable FRED coverage ──────
# Update these when central banks change rates (meetings ~6-8x per year).
# Source: Each central bank's official website. Last updated: 2026-03.
# Update these when central banks change rates. USD/EUR/GBP also listed here
# as fallback when FRED is temporarily unreachable.
_STATIC_RATES: dict[str, float] = {
    "USD": 4.33,   # Fed Funds Rate (target range 4.25-4.50%)
    "EUR": 2.40,   # ECB Deposit Facility Rate
    "GBP": 4.50,   # Bank of England base rate
    "JPY": 0.50,   # Bank of Japan — raised to 0.5% Jan 2025
    "AUD": 4.10,   # RBA — easing cycle started Feb 2025
    "NZD": 3.75,   # RBNZ — cutting cycle
    "CAD": 3.00,   # Bank of Canada — cutting cycle
    "CHF": 0.50,   # SNB
    "ZAR": 7.50,   # SARB repo rate
    "MXN": 9.00,   # Banxico — cutting cycle
    "SGD": 3.00,   # MAS (proxy: SGD SORA ~3%)
}

# ── Pair → carry formula ──────────────────────────────────────────────────────
# carry = sum of (sign × rate[currency]) for each leg
# positive carry = base pays more = carry trade supports the pair direction
# For index/stock pairs → use inverted 10Y yield signal ("INDEX_RISK")
_PAIR_CARRY_FORMULA: dict[str, list[tuple[float, str]]] = {
    # ── Forex ────────────────────────────────────────────────────────────────
    "EUR/USD": [(1.0, "EUR"), (-1.0, "USD")],
    "GBP/USD": [(1.0, "GBP"), (-1.0, "USD")],
    "USD/JPY": [(1.0, "USD"), (-1.0, "JPY")],
    "AUD/USD": [(1.0, "AUD"), (-1.0, "USD")],
    "NZD/USD": [(1.0, "NZD"), (-1.0, "USD")],
    "EUR/GBP": [(1.0, "EUR"), (-1.0, "GBP")],
    "USD/CAD": [(1.0, "USD"), (-1.0, "CAD")],
    "USD/CHF": [(1.0, "USD"), (-1.0, "CHF")],
    "EUR/JPY": [(1.0, "EUR"), (-1.0, "JPY")],
    "GBP/JPY": [(1.0, "GBP"), (-1.0, "JPY")],
    "AUD/JPY": [(1.0, "AUD"), (-1.0, "JPY")],
    "EUR/AUD": [(1.0, "EUR"), (-1.0, "AUD")],
    "GBP/AUD": [(1.0, "GBP"), (-1.0, "AUD")],
    "EUR/CHF": [(1.0, "EUR"), (-1.0, "CHF")],
    "USD/MXN": [(1.0, "USD"), (-1.0, "MXN")],
    "USD/ZAR": [(1.0, "USD"), (-1.0, "ZAR")],
    "USD/SGD": [(1.0, "USD"), (-1.0, "SGD")],
    # ── Crypto: carry handled by funding rate factor ───────────────────────
    "BTC/USDT": [], "ETH/USDT": [], "SOL/USDT": [], "BNB/USDT": [],
    "XRP/USDT": [], "AVAX/USDT": [], "LINK/USDT": [], "ADA/USDT": [],
    "DOGE/USDT": [], "DOT/USDT": [], "SUI/USDT": [], "APT/USDT": [],
    "LTC/USDT": [], "NEAR/USDT": [], "INJ/USDT": [], "FET/USDT": [],
    "RENDER/USDT": [], "ATOM/USDT": [], "OP/USDT": [], "ARB/USDT": [],
    # ── Indices — 10Y yield risk signal ────────────────────────────────────
    "S&P 500":     [(-1.0, "_10Y")],  # inverted: high yield = bearish equity
    "SPY":         [(-1.0, "_10Y")],
    "Nasdaq":      [(-1.0, "_10Y")],
    "QQQ":         [(-1.0, "_10Y")],
    "Dow Jones":   [(-1.0, "_10Y")],
    "UK100":       [(-1.0, "_10Y")],
    "DAX 40":      [(-1.0, "_10Y")],
    "ASX 200":     [(-1.0, "_10Y")],
    "Nikkei 225":  [(-1.0, "_10Y")],
    "Euro Stoxx 50": [(-1.0, "_10Y")],
    "Hang Seng":   [(-1.0, "_10Y")],
    # ── Commodities ────────────────────────────────────────────────────────
    "XAU/USD":     [(-1.0, "_10Y")],  # gold is inversely correlated with real yields
    "XAG/USD":     [(-1.0, "_10Y")],
    "WTI Oil":     [],
    "Brent Oil":   [],
}

# ── SQLite cache ──────────────────────────────────────────────────────────────

def _init_db():
    with _db_lock:
        con = sqlite3.connect(_DB_PATH)
        con.execute("""
            CREATE TABLE IF NOT EXISTS rate_series (
                series_id   TEXT    NOT NULL,
                obs_date    TEXT    NOT NULL,
                rate        REAL    NOT NULL,
                PRIMARY KEY (series_id, obs_date)
            )
        """)
        con.execute("CREATE INDEX IF NOT EXISTS idx_rs_series ON rate_series (series_id, obs_date)")
        con.execute("""
            CREATE TABLE IF NOT EXISTS carry_meta (
                series_id  TEXT PRIMARY KEY,
                last_fetch REAL NOT NULL
            )
        """)
        con.commit()
        con.close()


_init_db()


# ── FRED data fetch ───────────────────────────────────────────────────────────

def _fetch_fred(series_id: str) -> list[tuple[str, float]]:
    """Download FRED series CSV. Returns list of (YYYY-MM-DD, value) sorted asc."""
    url = _FRED_URL.format(series=series_id)
    resp = requests.get(url, timeout=_TIMEOUT)
    resp.raise_for_status()
    rows = []
    reader = csv.reader(StringIO(resp.text))
    next(reader, None)  # skip header
    for row in reader:
        if len(row) < 2:
            continue
        date_str, val_str = row[0].strip(), row[1].strip()
        if val_str == "." or not val_str:
            continue  # FRED uses "." for missing values
        try:
            rows.append((date_str, float(val_str)))
        except ValueError:
            continue
    return rows


def _needs_refresh(series_id: str) -> bool:
    with _db_lock:
        con = sqlite3.connect(_DB_PATH)
        row = con.execute(
            "SELECT last_fetch FROM carry_meta WHERE series_id=?", (series_id,)
        ).fetchone()
        con.close()
    if not row:
        return True
    return time.time() - row[0] > _FETCH_TTL


def _write_series(series_id: str, rows: list[tuple[str, float]]):
    with _db_lock:
        con = sqlite3.connect(_DB_PATH)
        con.executemany(
            "INSERT OR REPLACE INTO rate_series (series_id, obs_date, rate) VALUES (?,?,?)",
            [(series_id, d, v) for d, v in rows]
        )
        con.execute(
            "INSERT OR REPLACE INTO carry_meta (series_id, last_fetch) VALUES (?,?)",
            (series_id, time.time())
        )
        con.commit()
        con.close()


_fetching_in_progress: set = set()
_fetch_guard_lock = threading.Lock()
_FAIL_COOLDOWN = 3600  # 1 hour cooldown after FRED failure (static rates cover us)


def _mark_fetch_attempted(series_id: str):
    """Write a timestamp to carry_meta even on failure, so _needs_refresh returns False
    for _FAIL_COOLDOWN seconds. Prevents thread storm when FRED is unreachable."""
    with _db_lock:
        con = sqlite3.connect(_DB_PATH)
        con.execute(
            "INSERT OR REPLACE INTO carry_meta (series_id, last_fetch) VALUES (?,?)",
            (series_id, time.time() - _FETCH_TTL + _FAIL_COOLDOWN)
        )
        con.commit()
        con.close()


def _ensure_series(series_id: str, blocking: bool = False):
    """Fetch FRED series if stale. Non-blocking by default (background thread).

    Guards against thread storms: deduplicates in-flight requests and writes
    a cooldown marker on failure so we don't retry for _FAIL_COOLDOWN seconds.
    """
    if not _needs_refresh(series_id):
        return

    with _fetch_guard_lock:
        if series_id in _fetching_in_progress:
            return  # Already being fetched by another thread
        _fetching_in_progress.add(series_id)

    def _do_fetch():
        try:
            rows = _fetch_fred(series_id)
            if rows:
                _write_series(series_id, rows)
                log.info(f"[CARRY] FRED {series_id}: {len(rows)} observations cached")
            else:
                _mark_fetch_attempted(series_id)
        except Exception as e:
            log.debug(f"[CARRY] FRED {series_id} fetch failed: {e}")
            _mark_fetch_attempted(series_id)
        finally:
            with _fetch_guard_lock:
                _fetching_in_progress.discard(series_id)

    if blocking:
        _do_fetch()
    else:
        threading.Thread(target=_do_fetch, daemon=True, name=f"FRED-{series_id}").start()


def _get_latest_rate(series_id: str) -> Optional[float]:
    """Return most recent non-null rate for a FRED series."""
    _ensure_series(series_id)
    with _db_lock:
        con = sqlite3.connect(_DB_PATH)
        row = con.execute(
            "SELECT rate FROM rate_series WHERE series_id=? ORDER BY obs_date DESC LIMIT 1",
            (series_id,)
        ).fetchone()
        con.close()
    return row[0] if row else None


def _get_rate_series(series_id: str, months: int = 36) -> list[float]:
    """Return last `months` monthly rate values (most recent last)."""
    _ensure_series(series_id)
    with _db_lock:
        con = sqlite3.connect(_DB_PATH)
        rows = con.execute(
            "SELECT rate FROM rate_series WHERE series_id=? ORDER BY obs_date DESC LIMIT ?",
            (series_id, months)
        ).fetchall()
        con.close()
    return [r[0] for r in reversed(rows)]


# ── Z-score helpers ───────────────────────────────────────────────────────────

def _carry_zscore(carry: float, history: list[float]) -> Optional[float]:
    """Z-score of carry value vs historical carry distribution."""
    if len(history) < 6:
        return None
    mean = sum(history) / len(history)
    variance = sum((x - mean) ** 2 for x in history) / len(history)
    std = variance ** 0.5
    if std < 0.01:
        # Rates nearly unchanged over period — return sign of carry (weak signal)
        return max(-1.0, min(1.0, carry))
    return max(-3.0, min(3.0, (carry - mean) / std))


# ── Public API ────────────────────────────────────────────────────────────────

_rate_cache: dict[str, tuple[float, float]] = {}  # series_id → (rate, fetched_at)


def _get_rate_for_key(key: str) -> Optional[float]:
    """Get latest rate for a currency key or special key like _10Y.

    Priority:
      1. FRED (live data) if a series is configured for this key
      2. _STATIC_RATES dict for currencies without FRED coverage
    """
    now = time.time()

    if key == "_10Y":
        series_id = _FRED_10Y_SERIES
        cached = _rate_cache.get(series_id)
        if cached and now - cached[1] < 3600:
            return cached[0]
        rate = _get_latest_rate(series_id)   # non-blocking (background fetch)
        if rate is not None:
            _rate_cache[series_id] = (rate, now)
            return rate
        return _STATIC_10Y  # fallback: hardcoded 10Y yield

    # FRED-backed currency
    series_id = _FRED_CURRENCY_SERIES.get(key)
    if series_id:
        cached = _rate_cache.get(series_id)
        if cached and now - cached[1] < 3600:
            return cached[0]
        rate = _get_latest_rate(series_id)
        if rate is not None:
            _rate_cache[series_id] = (rate, now)
            return rate
        # FRED failed — fall through to static

    # Static fallback (hardcoded central bank rates)
    return _STATIC_RATES.get(key)


def _get_rate_series_for_key(key: str, months: int = 36) -> list[float]:
    """Return historical rate series for a currency key.

    For FRED-backed keys: returns real historical data.
    For static-rate keys: returns a flat series at the current static rate
    (correct for z-score when rate has been stable; slightly inaccurate
    during easing/hiking cycles, but acceptable for a weekly signal).
    """
    if key == "_10Y":
        s = _get_rate_series(_FRED_10Y_SERIES, months)
        if len(s) >= 4:
            return s
        return [_STATIC_10Y] * months  # flat fallback

    series_id = _FRED_CURRENCY_SERIES.get(key)
    if series_id:
        s = _get_rate_series(series_id, months)
        if len(s) >= 4:
            return s

    # Static fallback: build synthetic flat history
    static_rate = _STATIC_RATES.get(key)
    if static_rate is not None:
        return [static_rate] * months
    return []


def get_carry_z(display: str) -> float:
    """Return interest rate carry z-score for a pair.

    Positive = base currency / asset has carry tailwind.
    Returns 0.0 if not applicable or data unavailable.

    Args:
        display: Athena display name e.g. "EUR/USD", "S&P 500", "XAU/USD"

    Returns:
        float in [-3.0, 3.0]; 0.0 = neutral / no coverage
    """
    now = time.time()
    cached = _mem_cache.get(display)
    if cached and now - cached[1] < _MEM_TTL:
        return cached[0]

    formula = _PAIR_CARRY_FORMULA.get(display, [])
    if not formula:
        _mem_cache[display] = (0.0, now)
        return 0.0

    # Compute current carry value
    carry = 0.0
    for sign, key in formula:
        rate = _get_rate_for_key(key)
        if rate is None:
            _mem_cache[display] = (0.0, now)
            return 0.0
        carry += sign * rate

    # Build historical carry series for z-score (36 months)
    history_lists = []
    for sign, key in formula:
        s = _get_rate_series_for_key(key, months=36)
        if s:
            history_lists.append((sign, s))

    # Compute carry for each historical date
    if not history_lists:
        _mem_cache[display] = (0.0, now)
        return 0.0

    min_len = min(len(s) for _, s in history_lists)
    if min_len < 4:
        _mem_cache[display] = (0.0, now)
        return 0.0

    carry_history = []
    for i in range(min_len):
        c = sum(sign * series[i] for sign, series in history_lists)
        carry_history.append(c)

    z = _carry_zscore(carry, carry_history)
    result = round(z, 3) if z is not None else 0.0
    _mem_cache[display] = (result, now)
    return result


def get_carry_differential(display: str) -> Optional[float]:
    """Return raw carry differential in percentage points (for display purposes).

    E.g. USD/MXN → -4.7 means MXN rate is 4.7pp higher than USD.
    """
    formula = _PAIR_CARRY_FORMULA.get(display, [])
    if not formula:
        return None
    carry = 0.0
    for sign, key in formula:
        rate = _get_rate_for_key(key)
        if rate is None:
            return None
        carry += sign * rate
    return round(carry, 3)


def seed_carry_background():
    """Trigger background fetch of all FRED rate series on startup."""
    def _seed():
        all_series = set(_FRED_CURRENCY_SERIES.values()) | {_FRED_10Y_SERIES}
        for sid in sorted(all_series):
            try:
                _ensure_series(sid, blocking=True)
            except Exception as e:
                log.warning(f"[CARRY] Seed {sid} failed: {e}")
        log.info(f"[CARRY] Rate seed complete ({len(all_series)} series)")
    t = threading.Thread(target=_seed, daemon=True, name="CarrySeed")
    t.start()


if __name__ == "__main__":
    import sys
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    print("Fetching FRED rate data...")
    seed_carry_background()
    import time as _time
    _time.sleep(15)   # wait for background fetch
    pairs = [
        "EUR/USD", "GBP/USD", "USD/JPY", "AUD/USD", "NZD/USD",
        "USD/MXN", "USD/ZAR", "USD/CHF", "EUR/JPY", "GBP/JPY",
        "S&P 500", "Nasdaq", "XAU/USD",
    ]
    print("\nCarry z-scores (+ = tailwind for LONG, − = headwind):")
    for p in pairs:
        z = get_carry_z(p)
        diff = get_carry_differential(p)
        diff_str = f"({diff:+.2f}pp)" if diff is not None else ""
        sign = "+" if z > 0 else ""
        print(f"  {p:18s}  z={sign}{z:+.3f}  {diff_str}")
