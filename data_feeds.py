"""HTTP session, EODHD client singleton, Binance/Bybit funding + OI helpers."""

from __future__ import annotations

import logging
import os
import time

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
