"""HTTP session, EODHD client singleton, Binance/Bybit funding + OI helpers."""

from __future__ import annotations

import bisect
import logging
import os
import time
from typing import Any

import requests as _requests_mod
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry as _Retry

from eodhd import APIClient as _EODHDClient

log = logging.getLogger("sentinel")

_retry_strategy = _Retry(
    total=3,
    backoff_factor=1.0,
    status_forcelist=[429, 500, 502, 503, 504],
    allowed_methods=["GET"],
)
_retry_adapter = HTTPAdapter(max_retries=_retry_strategy)
http_requests = _requests_mod.Session()
http_requests.mount("https://", _retry_adapter)
http_requests.mount("http://", _retry_adapter)

_eodhd_client = None


def _get_eodhd_client():
    global _eodhd_client

    if _eodhd_client is None:
        _key = os.environ.get("EODHD_KEY", "")

        if _key:
            _eodhd_client = _EODHDClient(_key)

    return _eodhd_client


_funding_rate_cache: dict = {}
_FUNDING_CACHE_TTL = 300

_bybit_funding_cache: dict = {}
_BYBIT_FUNDING_CACHE_TTL = 300  # 5 minutes, same as Binance

_oi_cache: dict = {}
_OI_CACHE_TTL = 300

# Historical series for backtests (longer TTL — data is immutable once settled)
_funding_history_cache: dict[str, tuple[Any, float]] = {}
_FUNDING_HISTORY_CACHE_TTL = 3600.0

_oi_history_cache: dict[str, tuple[Any, float]] = {}
_OI_HISTORY_CACHE_TTL = 3600.0

_BYBIT_FUNDING_URL = "https://api.bybit.com/v5/market/funding/history"
_BYBIT_OI_URL = "https://api.bybit.com/v5/market/open-interest"


def clear_derivative_history_caches() -> None:
    """Clear in-memory caches for historical funding/OI (tests)."""
    _funding_history_cache.clear()
    _oi_history_cache.clear()


def _funding_history_cache_key(
    source: str,
    symbol: str,
    start_ms: int | None,
    end_ms: int | None,
    limit: int,
) -> str:
    return f"funding_hist:v1:{source}:{symbol}:{start_ms}:{end_ms}:{limit}"


def _oi_history_cache_key(
    source: str,
    symbol: str,
    interval: str,
    start_ms: int | None,
    end_ms: int | None,
    limit: int,
) -> str:
    return f"oi_hist:v1:{source}:{symbol}:{interval}:{start_ms}:{end_ms}:{limit}"


def _row_at_or_before(
    rows: list[dict], ts_ms: int, ts_key: str = "ts_ms"
) -> dict | None:
    """Latest row with ts <= ts_ms. Rows must be sorted ascending by ts_key."""
    if not rows:
        return None
    ts_list = [int(r[ts_key]) for r in rows]
    idx = bisect.bisect_right(ts_list, ts_ms) - 1
    if idx < 0:
        return None
    return rows[idx]


def point_in_time_funding_rate(
    funding_rows: list[dict], bar_ms: int
) -> float | None:
    """Most recent funding rate at or before bar_ms (no future leakage)."""
    r = _row_at_or_before(funding_rows, bar_ms, "ts_ms")
    if not r:
        return None
    return float(r["rate"])


def build_oi_data_for_divergence(
    oi_rows: list[dict], bar_ms: int, prev_bar_ms: int | None
) -> dict | None:
    """Build ``oi_data`` dict for :func:`_calc_oi_divergence` using OI at ``bar_ms`` vs ``prev_bar_ms``."""
    if prev_bar_ms is None or not oi_rows:
        return None
    rec_now = _row_at_or_before(oi_rows, bar_ms, "ts_ms")
    rec_prev = _row_at_or_before(oi_rows, prev_bar_ms, "ts_ms")
    if not rec_now or not rec_prev:
        return None
    if rec_prev["ts_ms"] >= rec_now["ts_ms"]:
        return None
    oi_now = float(rec_now["oi"])
    oi_prev = float(rec_prev["oi"])
    if oi_prev <= 0:
        return None
    oi_change = (oi_now - oi_prev) / oi_prev * 100
    return {
        "oi": oi_now,
        "oiChange": round(oi_change, 2),
        "price": 0.0,
        "ts": rec_now["ts_ms"] / 1000.0,
        "error": False,
        "symbol": "",
        "detail": "",
    }


def _merge_dedupe_by_ts(rows: list[dict], ts_key: str = "ts_ms") -> list[dict]:
    if not rows:
        return []
    rows = sorted(rows, key=lambda r: int(r[ts_key]))
    out: list[dict] = []
    seen: set[int] = set()
    for r in reversed(rows):
        t = int(r[ts_key])
        if t in seen:
            continue
        seen.add(t)
        out.append(r)
    out.reverse()
    return out


def _fetch_bybit_funding_history(
    symbol: str,
    start_ms: int | None = None,
    end_ms: int | None = None,
    limit: int = 200,
) -> dict:
    """Fetch linear perpetual funding history from Bybit (paginates via ``endTime``)."""
    cache_key = _funding_history_cache_key("bybit", symbol, start_ms, end_ms, limit)
    now = time.time()
    cached = _funding_history_cache.get(cache_key)
    if cached and now - cached[1] < _FUNDING_HISTORY_CACHE_TTL:
        return cached[0]

    merged: list[dict] = []
    cur_end = end_ms if end_ms is not None else int(time.time() * 1000)
    max_batches = 80
    first_batch = True
    for _ in range(max_batches):
        params: dict[str, str] = {
            "category": "linear",
            "symbol": symbol,
            "limit": str(min(max(limit, 1), 200)),
            "endTime": str(cur_end),
        }
        if first_batch and start_ms is not None:
            params["startTime"] = str(start_ms)
        first_batch = False
        try:
            r = http_requests.get(_BYBIT_FUNDING_URL, params=params, timeout=20)
        except Exception as e:
            out = {"error": True, "symbol": symbol, "detail": str(e), "list": []}
            _funding_history_cache[cache_key] = (out, now)
            return out
        if r.status_code != 200:
            out = {
                "error": True,
                "symbol": symbol,
                "detail": f"HTTP {r.status_code}",
                "list": [],
            }
            _funding_history_cache[cache_key] = (out, now)
            return out
        try:
            data = r.json()
        except Exception as e:
            out = {"error": True, "symbol": symbol, "detail": str(e), "list": []}
            _funding_history_cache[cache_key] = (out, now)
            return out
        if int(data.get("retCode", -1)) != 0:
            out = {
                "error": True,
                "symbol": symbol,
                "detail": str(data.get("retMsg", "retCode")),
                "list": [],
            }
            _funding_history_cache[cache_key] = (out, now)
            return out
        batch = (data.get("result") or {}).get("list") or []
        if not batch:
            break
        for row in batch:
            ts = int(row.get("fundingRateTimestamp") or row.get("timestamp") or 0)
            if ts:
                merged.append(
                    {"ts_ms": ts, "rate": float(row.get("fundingRate", 0) or 0)}
                )
        oldest = min(
            int(x.get("fundingRateTimestamp") or x.get("timestamp") or 0)
            for x in batch
        )
        if start_ms is not None and oldest <= start_ms:
            break
        if len(batch) < limit:
            break
        cur_end = oldest - 1
        if cur_end < (start_ms or 0):
            break

    deduped = _merge_dedupe_by_ts(merged)
    out = {"error": False, "symbol": symbol, "detail": "", "list": deduped}
    _funding_history_cache[cache_key] = (out, now)
    return out


def _fetch_binance_funding_history(
    symbol: str,
    start_ms: int | None = None,
    end_ms: int | None = None,
    limit: int = 1000,
) -> dict:
    """Binance USDT-M funding history fallback (``/fapi/v1/fundingRate``)."""
    cache_key = _funding_history_cache_key("binance", symbol, start_ms, end_ms, limit)
    now = time.time()
    cached = _funding_history_cache.get(cache_key)
    if cached and now - cached[1] < _FUNDING_HISTORY_CACHE_TTL:
        return cached[0]

    params: dict[str, str | int] = {"symbol": symbol, "limit": min(max(limit, 1), 1000)}
    if start_ms is not None:
        params["startTime"] = int(start_ms)
    if end_ms is not None:
        params["endTime"] = int(end_ms)
    url = "https://fapi.binance.com/fapi/v1/fundingRate"
    try:
        r = http_requests.get(url, params=params, timeout=20)
    except Exception as e:
        out = {"error": True, "symbol": symbol, "detail": str(e), "list": []}
        _funding_history_cache[cache_key] = (out, now)
        return out
    if r.status_code != 200:
        out = {
            "error": True,
            "symbol": symbol,
            "detail": f"HTTP {r.status_code}",
            "list": [],
        }
        _funding_history_cache[cache_key] = (out, now)
        return out
    try:
        arr = r.json()
    except Exception as e:
        out = {"error": True, "symbol": symbol, "detail": str(e), "list": []}
        _funding_history_cache[cache_key] = (out, now)
        return out
    if not isinstance(arr, list):
        out = {"error": True, "symbol": symbol, "detail": "invalid json", "list": []}
        _funding_history_cache[cache_key] = (out, now)
        return out
    merged: list[dict] = []
    for row in arr:
        ts = int(row.get("fundingTime") or 0)
        if ts:
            merged.append(
                {"ts_ms": ts, "rate": float(row.get("fundingRate", 0) or 0)}
            )
    deduped = _merge_dedupe_by_ts(merged)
    out = {"error": False, "symbol": symbol, "detail": "", "list": deduped}
    _funding_history_cache[cache_key] = (out, now)
    return out


def _fetch_bybit_open_interest_history(
    symbol: str,
    interval: str,
    start_ms: int | None = None,
    end_ms: int | None = None,
    limit: int = 200,
) -> dict:
    """Bybit v5 open interest history (``interval`` e.g. ``1h``, ``4h``)."""
    cache_key = _oi_history_cache_key("bybit", symbol, interval, start_ms, end_ms, limit)
    now = time.time()
    cached = _oi_history_cache.get(cache_key)
    if cached and now - cached[1] < _OI_HISTORY_CACHE_TTL:
        return cached[0]

    merged: list[dict] = []
    cursor: str | None = None
    cur_end = end_ms if end_ms is not None else int(time.time() * 1000)
    max_pages = 80
    for _ in range(max_pages):
        params: dict[str, str] = {
            "category": "linear",
            "symbol": symbol,
            "intervalTime": interval,
            "limit": str(min(max(limit, 1), 200)),
        }
        if cursor:
            params["cursor"] = cursor
        else:
            params["endTime"] = str(cur_end)
            if start_ms is not None:
                params["startTime"] = str(start_ms)
        try:
            r = http_requests.get(_BYBIT_OI_URL, params=params, timeout=20)
        except Exception as e:
            out = {"error": True, "symbol": symbol, "detail": str(e), "list": []}
            _oi_history_cache[cache_key] = (out, now)
            return out
        if r.status_code != 200:
            out = {
                "error": True,
                "symbol": symbol,
                "detail": f"HTTP {r.status_code}",
                "list": [],
            }
            _oi_history_cache[cache_key] = (out, now)
            return out
        try:
            data = r.json()
        except Exception as e:
            out = {"error": True, "symbol": symbol, "detail": str(e), "list": []}
            _oi_history_cache[cache_key] = (out, now)
            return out
        if int(data.get("retCode", -1)) != 0:
            out = {
                "error": True,
                "symbol": symbol,
                "detail": str(data.get("retMsg", "retCode")),
                "list": [],
            }
            _oi_history_cache[cache_key] = (out, now)
            return out
        res = data.get("result") or {}
        batch = res.get("list") or []
        if not batch:
            break
        for row in batch:
            ts = int(row.get("timestamp") or 0)
            if ts:
                merged.append(
                    {"ts_ms": ts, "oi": float(row.get("openInterest", 0) or 0)}
                )
        cursor = (res.get("nextPageCursor") or "").strip()
        if not cursor:
            break
        oldest = min(int(x.get("timestamp") or 0) for x in batch)
        if start_ms is not None and oldest <= start_ms:
            break

    deduped = _merge_dedupe_by_ts(merged)
    out = {"error": False, "symbol": symbol, "detail": "", "list": deduped}
    _oi_history_cache[cache_key] = (out, now)
    return out


def _binance_oi_period(interval: str) -> str:
    m = {
        "5min": "5m",
        "15min": "15m",
        "30min": "30m",
        "1h": "1h",
        "4h": "4h",
        "1d": "1d",
    }
    return m.get(interval, "1h")


def _fetch_binance_open_interest_history(
    symbol: str,
    interval: str,
    start_ms: int | None = None,
    end_ms: int | None = None,
    limit: int = 500,
) -> dict:
    """Binance futures open interest history fallback."""
    period = _binance_oi_period(interval)
    cache_key = _oi_history_cache_key(
        "binance", symbol, period, start_ms, end_ms, limit
    )
    now = time.time()
    cached = _oi_history_cache.get(cache_key)
    if cached and now - cached[1] < _OI_HISTORY_CACHE_TTL:
        return cached[0]

    params: dict[str, str | int] = {
        "symbol": symbol,
        "period": period,
        "limit": min(max(limit, 1), 500),
    }
    if start_ms is not None:
        params["startTime"] = int(start_ms)
    if end_ms is not None:
        params["endTime"] = int(end_ms)
    url = "https://fapi.binance.com/futures/data/openInterestHist"
    try:
        r = http_requests.get(url, params=params, timeout=20)
    except Exception as e:
        out = {"error": True, "symbol": symbol, "detail": str(e), "list": []}
        _oi_history_cache[cache_key] = (out, now)
        return out
    if r.status_code != 200:
        out = {
            "error": True,
            "symbol": symbol,
            "detail": f"HTTP {r.status_code}",
            "list": [],
        }
        _oi_history_cache[cache_key] = (out, now)
        return out
    try:
        arr = r.json()
    except Exception as e:
        out = {"error": True, "symbol": symbol, "detail": str(e), "list": []}
        _oi_history_cache[cache_key] = (out, now)
        return out
    if not isinstance(arr, list):
        out = {"error": True, "symbol": symbol, "detail": "invalid json", "list": []}
        _oi_history_cache[cache_key] = (out, now)
        return out
    merged: list[dict] = []
    for row in arr:
        ts = int(row.get("timestamp") or 0)
        if ts:
            oi_val = float(row.get("sumOpenInterest", 0) or 0)
            merged.append({"ts_ms": ts, "oi": oi_val})
    deduped = _merge_dedupe_by_ts(merged)
    out = {"error": False, "symbol": symbol, "detail": "", "list": deduped}
    _oi_history_cache[cache_key] = (out, now)
    return out


def _candle_series_time_bounds_ms(
    d1_raw: list | None, h4_raw: list | None, h1_raw: list | None
) -> tuple[int | None, int | None]:
    import pandas as pd

    times: list = []
    for arr in (d1_raw, h4_raw, h1_raw):
        if not arr:
            continue
        for c in arr:
            t = c.get("time")
            if not t:
                continue
            ts = pd.to_datetime(t, utc=True, errors="coerce")
            if pd.notna(ts):
                times.append(ts)
    if not times:
        return None, None
    return (
        int(min(times).timestamp() * 1000),
        int(max(times).timestamp() * 1000),
    )


def prepare_crypto_backtest_derivative_series(
    pair: dict,
    d1_raw: list | None,
    h4_raw: list | None,
    h1_raw: list | None,
    oi_interval: str = "1h",
) -> tuple[list[dict], list[dict]]:
    """Load normalized funding + OI rows for the candle range (Bybit with Binance fallback)."""
    if pair.get("type") != "crypto":
        return [], []
    sym = (pair.get("symbol") or "").replace("/", "").upper()
    if not sym:
        return [], []
    start_ms, end_ms = _candle_series_time_bounds_ms(d1_raw, h4_raw, h1_raw)
    if start_ms is None or end_ms is None:
        return [], []

    fr = _fetch_bybit_funding_history(sym, start_ms=start_ms, end_ms=end_ms, limit=200)
    funding_rows: list[dict] = []
    if isinstance(fr, dict) and not fr.get("error"):
        funding_rows = list(fr.get("list") or [])
    if not funding_rows:
        fr2 = _fetch_binance_funding_history(
            sym, start_ms=start_ms, end_ms=end_ms, limit=1000
        )
        if isinstance(fr2, dict) and not fr2.get("error"):
            funding_rows = list(fr2.get("list") or [])

    oi = _fetch_bybit_open_interest_history(
        sym, oi_interval, start_ms=start_ms, end_ms=end_ms, limit=200
    )
    oi_rows: list[dict] = []
    if isinstance(oi, dict) and not oi.get("error"):
        oi_rows = list(oi.get("list") or [])
    if not oi_rows:
        oi2 = _fetch_binance_open_interest_history(
            sym, oi_interval, start_ms=start_ms, end_ms=end_ms, limit=500
        )
        if isinstance(oi2, dict) and not oi2.get("error"):
            oi_rows = list(oi2.get("list") or [])

    return funding_rows, oi_rows


def _fetch_funding_rate(binance_symbol: str) -> dict:
    """Fetch current perpetual funding rate from Binance public API. Cached 5 min."""
    now = time.time()

    cached = _funding_rate_cache.get(binance_symbol)

    if cached and now - cached[1] < _FUNDING_CACHE_TTL:
        return {
            "error": False,
            "symbol": binance_symbol,
            "detail": "",
            "rate": cached[0],
        }

    try:
        url = f"https://fapi.binance.com/fapi/v1/premiumIndex?symbol={binance_symbol}"

        r = http_requests.get(url, timeout=4)

        if r.status_code == 200:
            rate = float(r.json().get("lastFundingRate", 0))

            _funding_rate_cache[binance_symbol] = (rate, now)

            return {
                "error": False,
                "symbol": binance_symbol,
                "detail": "",
                "rate": rate,
            }

        else:
            return {
                "error": True,
                "symbol": binance_symbol,
                "detail": f"HTTP {r.status_code}",
            }

    except Exception as _e:
        log.debug(f"[FUNDING] {binance_symbol} fetch failed: {_e}")

        return {"error": True, "symbol": binance_symbol, "detail": str(_e)}


def _fetch_bybit_funding_rate(symbol: str) -> dict:
    """Fetch current perpetual funding rate from Bybit public API.
    Uses /v5/market/tickers — no auth required. Cached 5 min.
    Symbol format: BTCUSDT (no slash).
    """
    now = time.time()
    cached = _bybit_funding_cache.get(symbol)
    if cached and now - cached[1] < _BYBIT_FUNDING_CACHE_TTL:
        return {
            "error": False,
            "symbol": symbol,
            "detail": "",
            "rate": cached[0],
        }

    try:
        url = f"https://api.bybit.com/v5/market/tickers?category=linear&symbol={symbol}"
        r = http_requests.get(url, timeout=4)
        if r.status_code == 200:
            data = r.json()
            items = data.get("result", {}).get("list", [])
            if items:
                rate = float(items[0].get("fundingRate", 0))
                _bybit_funding_cache[symbol] = (rate, now)
                return {
                    "error": False,
                    "symbol": symbol,
                    "detail": "",
                    "rate": rate,
                }
            return {"error": True, "symbol": symbol, "detail": "empty tickers"}
        return {"error": True, "symbol": symbol, "detail": f"HTTP {r.status_code}"}
    except Exception as _e:
        log.debug(f"[FUNDING-BYBIT] {symbol} fetch failed: {_e}")
        return {"error": True, "symbol": symbol, "detail": str(_e)}


def _fetch_open_interest(binance_symbol: str) -> dict:
    """Fetch open interest + price from Binance Futures public API. Cached 5 min."""
    now = time.time()

    cached = _oi_cache.get(binance_symbol)

    if cached and now - cached["ts"] < _OI_CACHE_TTL:
        return {"error": False, "symbol": binance_symbol, "detail": "", **cached}

    try:
        url = f"https://fapi.binance.com/fapi/v1/openInterest?symbol={binance_symbol}"

        r = http_requests.get(url, timeout=4)

        if r.status_code != 200:
            if cached:
                return {
                    "error": True,
                    "symbol": binance_symbol,
                    "detail": f"HTTP {r.status_code} (using stale)",
                    **cached,
                }

            return {
                "error": True,
                "symbol": binance_symbol,
                "detail": f"HTTP {r.status_code}",
            }

        oi_val = float(r.json().get("openInterest", 0))

        pr = http_requests.get(
            f"https://fapi.binance.com/fapi/v1/ticker/price?symbol={binance_symbol}",
            timeout=4,
        )

        price = float(pr.json().get("price", 0)) if pr.status_code == 200 else 0

        prev = _oi_cache.get(binance_symbol)

        oi_change = None

        if prev and prev.get("oi") and prev["oi"] > 0:
            oi_change = round((oi_val - prev["oi"]) / prev["oi"] * 100, 2)

        entry = {"oi": oi_val, "oiChange": oi_change, "price": price, "ts": now}

        _oi_cache[binance_symbol] = entry

        return {"error": False, "symbol": binance_symbol, "detail": "", **entry}

    except Exception as _e:
        log.debug(f"[OI] {binance_symbol} fetch failed: {_e}")

        if cached:
            return {
                "error": True,
                "symbol": binance_symbol,
                "detail": str(_e),
                **cached,
            }

        return {"error": True, "symbol": binance_symbol, "detail": str(_e)}


def _calc_oi_divergence(
    oi_data: dict | None, current_price: float, prev_price: float | None
) -> dict | None:
    """Detect OI + price divergence. Returns signal dict or None."""
    if not oi_data or oi_data.get("oiChange") is None or not prev_price:
        return None

    oi_chg = oi_data["oiChange"]

    price_chg = (current_price - prev_price) / prev_price * 100 if prev_price else 0

    result = {
        "oiChange": oi_chg,
        "priceChange": round(price_chg, 2),
        "signal": "neutral",
    }

    if oi_chg > 3 and price_chg < -1:
        result["signal"] = "bearish_divergence"

        result["warning"] = (
            f"OI rising +{oi_chg:.1f}% while price falling {price_chg:.1f}% — overleveraged longs, liquidation risk"
        )

    elif oi_chg < -3 and price_chg > 1:
        result["signal"] = "exhaustion"

        result["warning"] = (
            f"OI falling {oi_chg:.1f}% while price rising +{price_chg:.1f}% — short squeeze exhaustion, rally losing steam"
        )

    elif oi_chg > 3 and price_chg > 1:
        result["signal"] = "bullish_conviction"

    elif oi_chg < -3 and price_chg < -1:
        result["signal"] = "capitulation"

        result["warning"] = (
            f"OI falling {oi_chg:.1f}% + price falling {price_chg:.1f}% — capitulation, potential bottom"
        )

    return result


def _load_monolith():
    import athena_legacy as _al

    return _al.load()


def start_price_poller() -> None:
    fn = getattr(_load_monolith(), "_start_price_poller", None)
    if callable(fn):
        fn()


def start_eodhd_ws() -> None:
    fn = getattr(_load_monolith(), "_start_eodhd_ws", None)
    if callable(fn):
        fn()


def start_binance_ws() -> None:
    fn = getattr(_load_monolith(), "_start_binance_ws", None)
    if callable(fn):
        fn()


def stop_data_feeds() -> None:
    m = _load_monolith()
    for name in ("_stop_eodhd_ws", "_stop_binance_ws", "_stop_price_poller"):
        fn = getattr(m, name, None)
        if callable(fn):
            fn()
