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
from bisect import bisect_right
from datetime import datetime, timezone
from io import StringIO
from typing import Optional

import requests

log = logging.getLogger("sentinel")

_static_carry_warned = False

_DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "carry_cache.db")
_db_lock = threading.Lock()
_TIMEOUT = 30
_FETCH_TTL = 86400  # re-download daily (rates change monthly but freshness matters)

# In-memory cache: pair → (z_score, fetched_at)
_mem_cache: dict = {}
_MEM_TTL = 24 * 3600  # 24-hour in-memory cache (rates change slowly)

# In-memory cache: series_id -> (fetched_at, obs_dates, rates).
# Historical Engine A backtests call carry lookups once per candidate bar. Keep
# the SQLite-backed source of truth, but avoid repeated per-bar SELECTs.
_series_mem_cache: dict[str, tuple[float, list[str], list[float]]] = {}
_SERIES_MEM_TTL = 3600

# ── FRED series IDs per currency ──────────────────────────────────────────────
_FRED_URL = "https://fred.stlouisfed.org/graph/fredgraph.csv?id={series}"

# FRED policy / short-rate proxies per currency (not all are daily like DFF).
# NZD/CAD/MXN/SGD still use _STATIC_RATES when not listed here.
_FRED_CURRENCY_SERIES: dict[str, str] = {
    "USD": "DFF",  # Fed Funds Rate (daily)
    "EUR": "ECBDFR",  # ECB Deposit Facility Rate
    "GBP": "IRSTCI01GBM156N",  # UK short-term interest rate (monthly)
    "JPY": "IRSTCI01JPM156N",  # Japan short-term interest rate (monthly)
    "AUD": "IRSTCI01AUM156N",  # Australia short-term interest rate (monthly)
    "CHF": "IRSTCI01CHM156N",  # Switzerland short-term rate (monthly)
    "ZAR": "IRSTCI01ZAM156N",  # South Africa short-term rate (monthly)
}

# 10Y government bond yields — for index/commodity risk signals
_FRED_10Y_SERIES = "DGS10"  # US 10-Year Treasury Constant Maturity Rate
_FRED_10Y_SERIES_GB = "IRLTLT01GBM156N"  # UK 10-Year Gilt yield (monthly)
_FRED_10Y_SERIES_DE = "IRLTLT01DEM156N"  # Germany 10-Year Bund yield (monthly)
_FRED_10Y_SERIES_AU = "IRLTLT01AUM156N"  # Australia 10-Year yield (monthly)
_FRED_10Y_SERIES_JP = "IRLTLT01JPM156N"  # Japan 10-Year JGB yield (monthly)
_FRED_10Y_SERIES_EZ = "IRLTLT01EZM156N"  # Euro Area 10-Year yield (monthly)

_STATIC_10Y = 4.55  # US 10Y Treasury, Jun 5 2026 (FRED DGS10) — fallback when FRED is unreachable
_STATIC_10Y_GB = 4.88  # UK 10Y Gilt, Jun 5 2026 (DMO)
_STATIC_10Y_DE = 3.03  # Germany 10Y Bund, Jun 5 2026 (Bundesbank)
_STATIC_10Y_AU = 4.94  # Australia 10Y, Jun 5 2026 (RBA)
_STATIC_10Y_JP = 2.67  # Japan 10Y JGB, Jun 5 2026 (BOJ)
_STATIC_10Y_EZ = 3.03  # Euro Area 10Y proxy (Germany Bund), Jun 5 2026

# ── Hardcoded policy rates for currencies without reliable FRED coverage ──────
# Update these when central banks change rates (meetings ~6-8x per year).
# Source: Each central bank's official website. Last updated: 2026-06-07.
# Update these when central banks change rates. USD/EUR/GBP also listed here
# as fallback when FRED is temporarily unreachable.
# Version date for static fallbacks — keep in sync with _STATIC_RATES comments.
# Config key CARRY_STATIC_RATES_AS_OF mirrors this; carry_feed warns when stale.
_STATIC_RATES_AS_OF = "2026-06-07"

_STATIC_RATES: dict[str, float] = {
    "USD": 3.625,  # Fed funds midpoint 3.50-3.75%, held Apr 29 2026 (FOMC)
    "EUR": 2.00,  # ECB Deposit Facility Rate, held Apr 30 2026 (ECB)
    "GBP": 3.75,  # BoE Base Rate, held Apr 29 2026 (BoE MPC 8-1 vote)
    "JPY": 0.75,  # BOJ overnight target, held Apr 27-28 2026 (BOJ)
    "AUD": 4.35,  # RBA Cash Rate, +25bp hike May 2026 (RBA)
    "NZD": 2.25,  # RBNZ OCR, held Apr 2026 after 325bp cut cycle (RBNZ)
    "CAD": 2.25,  # BoC overnight rate, held Apr 29 2026 (BoC)
    "CHF": 0.00,  # SNB policy rate cut to 0% Jun 2025, held Mar 2026 (SNB)
    "ZAR": 7.00,  # SARB repo rate, +25bp hike to 7.00% May 28 2026 (SARB)
    "MXN": 6.50,  # Banxico target rate, cut to 6.50% May 7 2026 (Banxico)
    "SGD": 1.00,  # SORA overnight proxy ~1.00%, Jun 2026 (MAS)
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
    "BTC/USDT": [],
    "ETH/USDT": [],
    "SOL/USDT": [],
    "BNB/USDT": [],
    "XRP/USDT": [],
    "AVAX/USDT": [],
    "LINK/USDT": [],
    "ADA/USDT": [],
    "DOGE/USDT": [],
    "DOT/USDT": [],
    "SUI/USDT": [],
    "APT/USDT": [],
    "LTC/USDT": [],
    "NEAR/USDT": [],
    "INJ/USDT": [],
    "RENDER/USDT": [],
    "ATOM/USDT": [],
    "OP/USDT": [],
    "ARB/USDT": [],
    # ── Indices — 10Y yield risk signal ────────────────────────────────────
    "S&P 500": [(-1.0, "_10Y")],  # inverted: high yield = bearish equity
    "SPY": [(-1.0, "_10Y")],
    "NASDAQ-100": [(-1.0, "_10Y")],
    "QQQ": [(-1.0, "_10Y")],
    "Dow Jones": [(-1.0, "_10Y")],
    "UK100": [(-1.0, "_10Y_GB")],
    "DAX 40": [(-1.0, "_10Y_DE")],
    "ASX 200": [(-1.0, "_10Y_AU")],
    "Nikkei 225": [(-1.0, "_10Y_JP")],
    "Euro Stoxx 50": [(-1.0, "_10Y_EZ")],
    "Hang Seng": [(-1.0, "_10Y")],
    # ── Commodities ────────────────────────────────────────────────────────
    "XAU/USD": [(-1.0, "_10Y")],  # gold is inversely correlated with real yields
    "XAG/USD": [(-1.0, "_10Y")],
    "XPT/USD": [(-1.0, "_10Y")],  # platinum — precious metal, same yield dynamic
    "XPD/USD": [(-1.0, "_10Y")],  # palladium — precious metal, same yield dynamic
    "WTI Oil": [(-1.0, "_10Y")],
    "Brent Oil": [(-1.0, "_10Y")],
    "Nat Gas": [(-1.0, "_10Y")],
    "Copper": [(-1.0, "_10Y")],
    # ── US Stocks — inverted 10Y yield as macro risk signal ───────────────
    "AAPL": [(-1.0, "_10Y")],
    "TSLA": [(-1.0, "_10Y")],
    "NVDA": [(-1.0, "_10Y")],
    "MSFT": [(-1.0, "_10Y")],
    "AMZN": [(-1.0, "_10Y")],
    "META": [(-1.0, "_10Y")],
    "GOOG": [(-1.0, "_10Y")],
    "JPM": [(-1.0, "_10Y")],
    "V": [(-1.0, "_10Y")],
    "XOM": [(-1.0, "_10Y")],
    "NFLX": [(-1.0, "_10Y")],
    "AMD": [(-1.0, "_10Y")],
    "CRM": [(-1.0, "_10Y")],
    "DIS": [(-1.0, "_10Y")],
    "BA": [(-1.0, "_10Y")],
    "COIN": [(-1.0, "_10Y")],
    "PYPL": [(-1.0, "_10Y")],
    "INTC": [(-1.0, "_10Y")],
    "UBER": [(-1.0, "_10Y")],
    "PLTR": [(-1.0, "_10Y")],
    # ── ETFs ─────────────────────────────────────────────────────────────
    "GLD": [(-1.0, "_10Y")],   # gold ETF — same yield dynamic as XAU/USD
    "TLT": [(-1.0, "_10Y")],   # bond ETF — falls when yields rise
    "IWM": [(-1.0, "_10Y")],
    "EEM": [(-1.0, "_10Y")],
    "XLE": [(-1.0, "_10Y")],
    "SLV": [(-1.0, "_10Y")],   # silver ETF — same yield dynamic as XAG/USD
    "USO": [],                  # oil ETF — no reliable yield correlation
}

# ── SQLite cache ──────────────────────────────────────────────────────────────


def _init_db():
    with _db_lock:
        con = sqlite3.connect(_DB_PATH, timeout=15.0)
        con.execute("PRAGMA journal_mode=WAL")
        con.execute("""
            CREATE TABLE IF NOT EXISTS rate_series (
                series_id   TEXT    NOT NULL,
                obs_date    TEXT    NOT NULL,
                rate        REAL    NOT NULL,
                PRIMARY KEY (series_id, obs_date)
            )
        """)
        con.execute(
            "CREATE INDEX IF NOT EXISTS idx_rs_series ON rate_series (series_id, obs_date)"
        )
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
        con = sqlite3.connect(_DB_PATH, timeout=15.0)
        row = con.execute(
            "SELECT last_fetch FROM carry_meta WHERE series_id=?", (series_id,)
        ).fetchone()
        con.close()
    if not row:
        return True
    return time.time() - row[0] > _FETCH_TTL


def _write_series(series_id: str, rows: list[tuple[str, float]]):
    with _db_lock:
        con = sqlite3.connect(_DB_PATH, timeout=15.0)
        con.executemany(
            "INSERT OR REPLACE INTO rate_series (series_id, obs_date, rate) VALUES (?,?,?)",
            [(series_id, d, v) for d, v in rows],
        )
        con.execute(
            "INSERT OR REPLACE INTO carry_meta (series_id, last_fetch) VALUES (?,?)",
            (series_id, time.time()),
        )
        con.commit()
        con.close()
    _series_mem_cache.pop(series_id, None)


_fetching_in_progress: set = set()
_fetch_guard_lock = threading.Lock()
_FAIL_COOLDOWN = 3600  # 1 hour cooldown after FRED failure (static rates cover us)


def _mark_fetch_attempted(series_id: str):
    """Write a timestamp to carry_meta even on failure, so _needs_refresh returns False
    for _FAIL_COOLDOWN seconds. Prevents thread storm when FRED is unreachable."""
    with _db_lock:
        con = sqlite3.connect(_DB_PATH, timeout=15.0)
        con.execute(
            "INSERT OR REPLACE INTO carry_meta (series_id, last_fetch) VALUES (?,?)",
            (series_id, time.time() - _FETCH_TTL + _FAIL_COOLDOWN),
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
        threading.Thread(
            target=_do_fetch, daemon=True, name=f"FRED-{series_id}"
        ).start()


def _get_latest_rate(series_id: str, as_of_date: str = None) -> Optional[float]:
    """Return most recent non-null rate for a FRED series."""
    _ensure_series(series_id)
    with _db_lock:
        con = sqlite3.connect(_DB_PATH, timeout=15.0)
        if as_of_date:
            row = con.execute(
                "SELECT rate FROM rate_series WHERE series_id=? AND obs_date<=? ORDER BY obs_date DESC LIMIT 1",
                (series_id, as_of_date),
            ).fetchone()
        else:
            row = con.execute(
                "SELECT rate FROM rate_series WHERE series_id=? ORDER BY obs_date DESC LIMIT 1",
                (series_id,),
            ).fetchone()
        con.close()
    return row[0] if row else None


def _get_series_rows_cached(series_id: str) -> tuple[list[str], list[float]]:
    """Return full DB-backed series from memory when fresh."""
    now = time.time()
    cached = _series_mem_cache.get(series_id)
    if cached and now - cached[0] < _SERIES_MEM_TTL:
        return cached[1], cached[2]

    _ensure_series(series_id)
    with _db_lock:
        con = sqlite3.connect(_DB_PATH, timeout=15.0)
        rows = con.execute(
            "SELECT obs_date, rate FROM rate_series WHERE series_id=? ORDER BY obs_date ASC",
            (series_id,),
        ).fetchall()
        con.close()
    dates = [str(r[0]) for r in rows]
    rates = [float(r[1]) for r in rows]
    _series_mem_cache[series_id] = (now, dates, rates)
    return dates, rates


def _get_latest_rate_cached(series_id: str, as_of_date: str) -> Optional[float]:
    dates, rates = _get_series_rows_cached(series_id)
    if not dates:
        return None
    idx = bisect_right(dates, as_of_date)
    if idx <= 0:
        return None
    return rates[idx - 1]


def _get_rate_series_cached(
    series_id: str, months: int = 36, as_of_date: str = None
) -> list[float]:
    dates, rates = _get_series_rows_cached(series_id)
    if not dates:
        return []
    end = bisect_right(dates, as_of_date) if as_of_date else len(dates)
    if end <= 0:
        return []
    start = max(0, end - int(months))
    return rates[start:end]


def _resample_to_monthly(dates: list[str], rates: list[float]) -> dict[str, float]:
    """Collapse observations to one rate per YYYY-MM (last obs in month wins)."""
    by_month: dict[str, float] = {}
    for d, r in zip(dates, rates):
        if len(d) >= 7:
            by_month[d[:7]] = r
    return by_month


def _month_keys_before(month_key: str, count: int) -> list[str]:
    """Return ``count`` ascending YYYY-MM keys ending at ``month_key``."""
    year, month = int(month_key[:4]), int(month_key[5:7])
    keys: list[str] = []
    for _ in range(count):
        keys.append(f"{year:04d}-{month:02d}")
        month -= 1
        if month == 0:
            month = 12
            year -= 1
    keys.reverse()
    return keys


def _anchor_month_key(as_of_date: str | None) -> str:
    if as_of_date and len(as_of_date) >= 7:
        return as_of_date[:7]
    return datetime.now(timezone.utc).strftime("%Y-%m")


def _get_monthly_rate_map_for_key(
    key: str, months: int = 36, as_of_date: str | None = None
) -> dict[str, float]:
    """Return {YYYY-MM: rate} for the last ``months`` months (inclusive anchor)."""
    anchor = _anchor_month_key(as_of_date)
    month_keys = _month_keys_before(anchor, months)

    if key in _10Y_SERIES_MAP:
        series_id, static_fallback = _10Y_SERIES_MAP[key]
        dates, rates = _get_series_rows_cached(series_id)
        monthly = _resample_to_monthly(dates, rates)
        if monthly:
            return {m: monthly[m] for m in month_keys if m in monthly}
        return {m: static_fallback for m in month_keys}

    series_id = _FRED_CURRENCY_SERIES.get(key)
    if series_id:
        dates, rates = _get_series_rows_cached(series_id)
        monthly = _resample_to_monthly(dates, rates)
        if monthly:
            return {m: monthly[m] for m in month_keys if m in monthly}

    static_rate = _STATIC_RATES.get(key)
    if static_rate is not None:
        return {m: static_rate for m in month_keys}
    return {}


def _get_rate_series(
    series_id: str, months: int = 36, as_of_date: str = None
) -> list[float]:
    """Return last ``months`` observations (most recent last)."""
    _ensure_series(series_id)
    with _db_lock:
        con = sqlite3.connect(_DB_PATH, timeout=15.0)
        if as_of_date:
            rows = con.execute(
                "SELECT rate FROM rate_series WHERE series_id=? AND obs_date<=? ORDER BY obs_date DESC LIMIT ?",
                (series_id, as_of_date, months),
            ).fetchall()
        else:
            rows = con.execute(
                "SELECT rate FROM rate_series WHERE series_id=? ORDER BY obs_date DESC LIMIT ?",
                (series_id, months),
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
    std = variance**0.5
    if std < 0.01:
        # Rates nearly unchanged over period — return sign of carry (weak signal)
        return max(-1.0, min(1.0, carry))
    return max(-3.0, min(3.0, (carry - mean) / std))


# ── Public API ────────────────────────────────────────────────────────────────

_rate_cache: dict[str, tuple[float, float]] = {}  # series_id → (rate, fetched_at)


_10Y_SERIES_MAP: dict[str, tuple[str, float]] = {
    "_10Y": (_FRED_10Y_SERIES, _STATIC_10Y),
    "_10Y_GB": (_FRED_10Y_SERIES_GB, _STATIC_10Y_GB),
    "_10Y_DE": (_FRED_10Y_SERIES_DE, _STATIC_10Y_DE),
    "_10Y_AU": (_FRED_10Y_SERIES_AU, _STATIC_10Y_AU),
    "_10Y_JP": (_FRED_10Y_SERIES_JP, _STATIC_10Y_JP),
    "_10Y_EZ": (_FRED_10Y_SERIES_EZ, _STATIC_10Y_EZ),
}


def _carry_static_rates_as_of() -> str:
    try:
        from config import CONFIG

        return str(CONFIG.get("CARRY_STATIC_RATES_AS_OF", _STATIC_RATES_AS_OF) or _STATIC_RATES_AS_OF).strip()
    except Exception:
        return _STATIC_RATES_AS_OF


def _carry_static_rates_max_age_days() -> int:
    try:
        from config import CONFIG

        return max(1, int(CONFIG.get("CARRY_STATIC_RATES_MAX_AGE_DAYS", 30) or 30))
    except Exception:
        return 30


def _static_rates_age_days() -> Optional[int]:
    as_of = _carry_static_rates_as_of()
    try:
        dt = datetime.strptime(as_of, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        return (datetime.now(timezone.utc) - dt).days
    except ValueError:
        return None


def _maybe_warn_static_carry_fallback(key: str) -> None:
    """Emit once per process when FRED failed and hardcoded policy rates are used."""
    global _static_carry_warned
    if _static_carry_warned:
        return
    age = _static_rates_age_days()
    if age is None:
        return
    max_age = _carry_static_rates_max_age_days()
    if age <= max_age:
        return
    _static_carry_warned = True
    log.warning(
        "[CARRY] Using static policy rate for %s; CARRY_STATIC_RATES_AS_OF=%s is %d days old "
        "(max %d). Verify FRED connectivity and refresh static rates.",
        key,
        _carry_static_rates_as_of(),
        age,
        max_age,
    )


def _get_rate_for_key(key: str, as_of_date: str = None) -> Optional[float]:
    """Get latest rate for a currency key or special key like _10Y / _10Y_GB etc.

    Priority:
      1. FRED (live data) if a series is configured for this key
      2. _STATIC_RATES dict for currencies without FRED coverage
    """
    now = time.time()

    if key in _10Y_SERIES_MAP:
        series_id, static_fallback = _10Y_SERIES_MAP[key]
        if not as_of_date:
            cached = _rate_cache.get(series_id)
            if cached and now - cached[1] < 3600:
                return cached[0]
        if as_of_date:
            rate = _get_latest_rate_cached(series_id, as_of_date)
        else:
            rate = _get_latest_rate(series_id)  # non-blocking (background fetch)
        if rate is not None:
            if not as_of_date:
                _rate_cache[series_id] = (rate, now)
            return rate
        if rate is None and not as_of_date:
            _maybe_warn_static_carry_fallback(key)
        return static_fallback if rate is None else rate

    # FRED-backed currency
    series_id = _FRED_CURRENCY_SERIES.get(key)
    if series_id:
        if not as_of_date:
            cached = _rate_cache.get(series_id)
            if cached and now - cached[1] < 3600:
                return cached[0]
        if as_of_date:
            rate = _get_latest_rate_cached(series_id, as_of_date)
        else:
            rate = _get_latest_rate(series_id)
        if rate is not None:
            if not as_of_date:
                _rate_cache[series_id] = (rate, now)
            return rate
        # FRED failed — fall through to static

    # Static fallback (hardcoded central bank rates)
    static_rate = _STATIC_RATES.get(key)
    if static_rate is not None and not as_of_date:
        _maybe_warn_static_carry_fallback(key)
    return static_rate


def _get_rate_series_for_key(
    key: str, months: int = 36, as_of_date: str = None
) -> list[float]:
    """Return historical rate series for a currency key.

    For FRED-backed keys: returns real historical data.
    For static-rate keys: returns a flat series at the current static rate
    (correct for z-score when rate has been stable; slightly inaccurate
    during easing/hiking cycles, but acceptable for a weekly signal).
    """
    if key in _10Y_SERIES_MAP:
        series_id, static_fallback = _10Y_SERIES_MAP[key]
        if as_of_date:
            s = _get_rate_series_cached(series_id, months, as_of_date=as_of_date)
        else:
            s = _get_rate_series(series_id, months)
        if len(s) >= 4:
            return s
        return [static_fallback] * months  # flat fallback

    series_id = _FRED_CURRENCY_SERIES.get(key)
    if series_id:
        if as_of_date:
            s = _get_rate_series_cached(series_id, months, as_of_date=as_of_date)
        else:
            s = _get_rate_series(series_id, months)
        if len(s) >= 4:
            return s

    # Static fallback: build synthetic flat history
    static_rate = _STATIC_RATES.get(key)
    if static_rate is not None:
        return [static_rate] * months
    return []


def get_carry_z(display: str, as_of_date: str = None) -> float:
    """Return interest rate carry z-score for a pair.

    Positive = base currency / asset has carry tailwind.
    Returns 0.0 if not applicable or data unavailable.

    Args:
        display: Athena display name e.g. "EUR/USD", "S&P 500", "XAU/USD"
        as_of_date: Optional date string for historical lookup (YYYY-MM-DD)

    Returns:
        float in [-3.0, 3.0]; 0.0 = neutral / no coverage
    """
    from frozen_data import active_data_as_of, read_frozen_factor_value

    data_as_of = active_data_as_of()
    if data_as_of:
        value = read_frozen_factor_value(data_as_of, display, "carry_z", as_of_date)
        return 0.0 if value is None else float(value)

    now = time.time()
    if not as_of_date:
        cached = _mem_cache.get(display)
        if cached and now - cached[1] < _MEM_TTL:
            return cached[0]

    formula = _PAIR_CARRY_FORMULA.get(display, [])
    if not formula:
        if not as_of_date:
            _mem_cache[display] = (0.0, now)
        return 0.0

    # Compute current carry value
    carry = 0.0
    for sign, key in formula:
        rate = _get_rate_for_key(key, as_of_date=as_of_date)
        if rate is None:
            if not as_of_date:
                _mem_cache[display] = (0.0, now)
            return 0.0
        carry += sign * rate

    # Build monthly-aligned carry history for z-score (36 months).
    monthly_maps: list[tuple[float, dict[str, float]]] = []
    for sign, key in formula:
        month_map = _get_monthly_rate_map_for_key(key, months=36, as_of_date=as_of_date)
        if month_map:
            monthly_maps.append((sign, month_map))

    if not monthly_maps:
        if not as_of_date:
            _mem_cache[display] = (0.0, now)
        return 0.0

    common_months = sorted(
        set.intersection(*[set(m.keys()) for _, m in monthly_maps])
    )
    if len(common_months) < 4:
        if not as_of_date:
            _mem_cache[display] = (0.0, now)
        return 0.0

    carry_history = [
        sum(sign * month_map[month] for sign, month_map in monthly_maps)
        for month in common_months
    ]

    z = _carry_zscore(carry, carry_history)
    result = round(z, 3) if z is not None else 0.0
    if not as_of_date:
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
        all_series = set(_FRED_CURRENCY_SERIES.values()) | set(
            sid for sid, _ in _10Y_SERIES_MAP.values()
        )
        for sid in sorted(all_series):
            try:
                _ensure_series(sid, blocking=True)
            except Exception as e:
                log.warning(f"[CARRY] Seed {sid} failed: {e}")
        log.info(f"[CARRY] Rate seed complete ({len(all_series)} series)")

    t = threading.Thread(target=_seed, daemon=True, name="CarrySeed")
    t.start()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    print("Fetching FRED rate data...")
    seed_carry_background()
    import time as _time

    _time.sleep(15)  # wait for background fetch
    pairs = [
        "EUR/USD",
        "GBP/USD",
        "USD/JPY",
        "AUD/USD",
        "NZD/USD",
        "USD/MXN",
        "USD/ZAR",
        "USD/CHF",
        "EUR/JPY",
        "GBP/JPY",
        "S&P 500",
        "Nasdaq",
        "XAU/USD",
    ]
    print("\nCarry z-scores (+ = tailwind for LONG, − = headwind):")
    for p in pairs:
        z = get_carry_z(p)
        diff = get_carry_differential(p)
        diff_str = f"({diff:+.2f}pp)" if diff is not None else ""
        sign = "+" if z > 0 else ""
        print(f"  {p:18s}  z={sign}{z:+.3f}  {diff_str}")
