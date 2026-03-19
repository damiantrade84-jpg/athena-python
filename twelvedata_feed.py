"""
Twelvedata REST feed — commodity, metal, and index candle history.
Used as fallback (EODHD → Twelvedata → Polygon → yfinance) for live scanning
and as backtest data source for XAU/USD, XAG/USD and other commodities
where EODHD has no intraday and Polygon free plan returns only ~38 days.

Free tier: 800 credits/day. Each paginated call = 1 credit.
A full 730-day H1 backtest costs 4 credits per pair.
"""

import time
import logging
import requests
from datetime import datetime, timedelta, timezone
from typing import Optional

log = logging.getLogger("athena")

# Twelvedata symbol mapping — native symbols, no futures proxy needed
_TD_SYMBOLS = {
    # Precious metals
    "XAU/USD": "XAU/USD",
    "XAG/USD": "XAG/USD",
    "XPT/USD": "XPT/USD",
    "XPD/USD": "XPD/USD",
    # Energy / base metals
    "WTI Oil": "WTI/USD",
    "Brent Oil": "BRNT/USD",
    "Nat Gas": "NGAS/USD",
    "Copper": "XCU/USD",
    # Major indices (Twelvedata index symbols)
    "S&P 500": "SPX",
    "Nasdaq": "IXIC",
    "Dow Jones": "DJI",
    "DAX 40": "DAX",
    "UK100": "FTSE100",
    "Nikkei 225": "N225",
    "Hang Seng": "HSI",
    "ASX 200": "AS51",
    "Euro Stoxx 50": "STOXX50E",
}

_BASE_URL = "https://api.twelvedata.com/time_series"
_MAX_PER_CALL = 5000
_RATE_LIMIT_PAUSE = 0.5  # seconds between calls — stay under 8/min limit


def _get_api_key() -> str:
    from config import CONFIG

    key = CONFIG.get("TWELVEDATA_KEY", "")
    if not key:
        raise ValueError("TWELVEDATA_KEY not set in config.yaml")
    return key


def _td_symbol_for_display(display: str) -> Optional[str]:
    return _TD_SYMBOLS.get(display)


def _fetch_page(
    symbol: str, interval: str, start_dt: datetime, end_dt: datetime, api_key: str
) -> list:
    """Fetch one page of bars from Twelvedata. Returns list of OHLCV dicts."""
    params = {
        "symbol": symbol,
        "interval": interval,
        "start_date": start_dt.strftime("%Y-%m-%d %H:%M:%S"),
        "end_date": end_dt.strftime("%Y-%m-%d %H:%M:%S"),
        "outputsize": _MAX_PER_CALL,
        "format": "JSON",
        "order": "ASC",
        "apikey": api_key,
    }
    try:
        r = requests.get(_BASE_URL, params=params, timeout=15)
        if r.status_code != 200:
            log.warning(f"[TWELVEDATA] HTTP {r.status_code} for {symbol} {interval}")
            return []
        data = r.json()
        if data.get("status") == "error":
            log.warning(f"[TWELVEDATA] API error for {symbol}: {data.get('message')}")
            return []
        values = data.get("values", [])
        return values
    except Exception as e:
        log.warning(f"[TWELVEDATA] Request failed for {symbol}: {e}")
        return []


def _to_candle(bar: dict) -> Optional[dict]:
    """Convert Twelvedata bar to Athena candle format (uses 'time'/'vol' keys)."""
    try:
        return {
            "time": bar.get("datetime") or bar.get("time") or bar.get("date", ""),
            "open": float(bar["open"]),
            "high": float(bar["high"]),
            "low": float(bar["low"]),
            "close": float(bar["close"]),
            "vol": float(bar.get("volume", 0)),
        }
    except (KeyError, ValueError, TypeError):
        return None


def _resample_h1_to_h4(h1_candles: list) -> list:
    """Resample H1 candles to H4 by grouping every 4 hours."""
    if not h1_candles:
        return []
    h4 = []
    bucket = []
    current_h4 = None
    for c in h1_candles:
        try:
            dt = datetime.fromisoformat(c["time"])
        except Exception:
            continue
        h4_slot = dt.replace(minute=0, second=0, microsecond=0)
        h4_slot = h4_slot.replace(hour=(dt.hour // 4) * 4)
        if current_h4 is None:
            current_h4 = h4_slot
        if h4_slot != current_h4:
            if bucket:
                h4.append(
                    {
                        "time": current_h4.isoformat(),
                        "open": bucket[0]["open"],
                        "high": max(b["high"] for b in bucket),
                        "low": min(b["low"] for b in bucket),
                        "close": bucket[-1]["close"],
                        "vol": sum(b["vol"] for b in bucket),
                    }
                )
            bucket = []
            current_h4 = h4_slot
        bucket.append(c)
    if bucket:
        h4.append(
            {
                "time": current_h4.isoformat(),
                "open": bucket[0]["open"],
                "high": max(b["high"] for b in bucket),
                "low": min(b["low"] for b in bucket),
                "close": bucket[-1]["close"],
                "vol": sum(b["vol"] for b in bucket),
            }
        )
    return h4


def fetch_twelvedata_bt(display_name: str, days: int = 730) -> tuple[list, list]:
    """
    Fetch H1 and H4 candles from Twelvedata for backtest.
    Returns (h4_candles, h1_candles) — same signature as _fetch_bt_yfinance().
    Returns ([], []) on failure.
    """
    td_symbol = _td_symbol_for_display(display_name)
    if not td_symbol:
        log.debug(f"[TWELVEDATA] No symbol mapping for {display_name}")
        return [], []

    try:
        api_key = _get_api_key()
    except ValueError as e:
        log.warning(f"[TWELVEDATA] {e}")
        return [], []

    end_dt = datetime.now(timezone.utc).replace(tzinfo=None)
    start_dt = end_dt - timedelta(days=days)

    log.info(
        f"[TWELVEDATA] Fetching {display_name} ({td_symbol}) H1 "
        f"{start_dt.date()} → {end_dt.date()}"
    )

    # Paginate — each call returns up to 5000 bars
    # 730 days * 24h = 17,520 H1 bars → 4 pages max
    all_h1 = []
    page_start = start_dt
    page_num = 0

    while page_start < end_dt:
        page_num += 1
        bars = _fetch_page(td_symbol, "1h", page_start, end_dt, api_key)
        if not bars:
            log.warning(f"[TWELVEDATA] No bars on page {page_num} for {display_name}")
            break
        candles = [c for c in (_to_candle(b) for b in bars) if c is not None]
        all_h1.extend(candles)
        # Advance window past last bar received
        try:
            last_dt = datetime.fromisoformat(
                bars[-1].get("datetime") or bars[-1].get("time") or bars[-1].get("date")
            )
            page_start = last_dt + timedelta(hours=1)
        except Exception:
            break
        if len(bars) < _MAX_PER_CALL:
            break  # No more pages
        time.sleep(_RATE_LIMIT_PAUSE)

    if not all_h1:
        log.warning(f"[TWELVEDATA] No H1 data returned for {display_name}")
        return [], []

    # Deduplicate and sort
    seen = set()
    unique_h1 = []
    for c in sorted(all_h1, key=lambda x: x["time"]):
        if c["time"] not in seen:
            seen.add(c["time"])
            unique_h1.append(c)

    h4_candles = _resample_h1_to_h4(unique_h1)

    log.info(
        f"[TWELVEDATA] {display_name}: {len(unique_h1)} H1 bars, "
        f"{len(h4_candles)} H4 bars"
    )
    return h4_candles, unique_h1


# Twelvedata interval strings for live scan fetches
_TD_INTERVAL = {"H1": "1h", "H4": "4h", "D1": "1day"}

# Approximate hours per bar — used to compute a date window from limit
_HOURS_PER_BAR = {"H1": 1, "H4": 4, "D1": 24}


def fetch_twelvedata_live(display_name: str, tf: str, limit: int) -> Optional[list]:
    """
    Fetch recent candles from Twelvedata for live scanning / EODHD fallback.
    Returns list of Athena candle dicts (time, open, high, low, close, vol),
    or None on failure / no symbol mapping.

    Costs 1 API credit per call — only triggered when EODHD returns no data.
    """
    td_symbol = _td_symbol_for_display(display_name)
    if not td_symbol:
        return None

    try:
        api_key = _get_api_key()
    except ValueError as e:
        log.warning(f"[TWELVEDATA] {e}")
        return None

    interval = _TD_INTERVAL.get(tf)
    if not interval:
        log.warning(f"[TWELVEDATA] Unknown timeframe: {tf}")
        return None

    # Fetch 2× the limit to allow for weekends / market closures, then trim
    hours_needed = _HOURS_PER_BAR.get(tf, 24) * limit * 2
    end_dt = datetime.now(timezone.utc).replace(tzinfo=None)
    start_dt = end_dt - timedelta(hours=hours_needed)

    log.info(
        f"[TWELVEDATA] Live fetch {display_name} ({td_symbol}) {tf} "
        f"start={start_dt.date()}"
    )

    bars = _fetch_page(td_symbol, interval, start_dt, end_dt, api_key)
    if not bars:
        log.warning(f"[TWELVEDATA] No live data for {display_name} {tf}")
        return None

    candles = [c for c in (_to_candle(b) for b in bars) if c is not None]
    if not candles:
        return None

    candles.sort(key=lambda x: x["time"])
    return candles[-limit:] if len(candles) > limit else candles
