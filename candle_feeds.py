"""Live prices, EODHD/Binance WebSockets, and CandleBuilder (WS → SQLite).

Uses `athena_runtime.rt()` for pair lists and `eodhd_ticker_for_pair` so this module
imports before `ALL_PAIRS` / `_NON_WS_EODHD` exist in the monolith.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import random
import sqlite3
import threading
import time
from datetime import datetime, timedelta, timezone

from athena_runtime import rt
from athena.datafeeds.ws_ssl import default_ssl_context
from candles_cache import forex_h4_resample_offset_hours
from data_feeds import _get_eodhd_client, http_requests
from runtime_paths import ensure_candle_cache_db_ready

log = logging.getLogger("sentinel")


def bulk_d1_skip_exchanges() -> frozenset[str]:
    """EODHD ``/api/eod-bulk-last-day/{EXCH}`` supports stock-style exchanges only.

    Suffixes such as ``COMM`` (commodity tickers like ``GC.COMM``) return HTTP 404.
    Those assets are refreshed via standard per-symbol EOD paths elsewhere; bulk D1
    must not call a non-existent bulk route.
    """
    try:
        from config import CONFIG as _cfg
    except ImportError:
        return frozenset({"COMM"})
    raw = _cfg.get("CANDLE_BUILDER_BULK_D1_SKIP_EXCHANGES", ["COMM"])
    if not isinstance(raw, (list, tuple, set)):
        return frozenset({"COMM"})
    out = {str(x).strip().upper() for x in raw if x and str(x).strip()}
    return frozenset(out) if out else frozenset({"COMM"})


_live_prices = {}  # protected by _live_prices_lock for compound writes

_live_prices_lock = threading.Lock()

_ws_manager_started = False

_PRICE_POLL_FIRST_RUN = True


def _eodhd_rt_symbol(pair):
    """Convert Athena pair → EODHD real-time API symbol."""
    disp = pair.get("display", "")
    ptype = pair.get("type", "")
    sym = pair.get("symbol", "")

    # Explicit overrides for commodities and indices
    _OVERRIDES = {
        # Precious metals — confirmed .FOREX format works
        "XAU/USD": "XAUUSD.FOREX",
        "XAG/USD": "XAGUSD.FOREX",
        "XPT/USD": "XPTUSD.FOREX",
        "XPD/USD": "XPDUSD.FOREX",
        # Energy — FOREX format confirmed for WTI
        "WTI Oil": "WTICOUSD.FOREX",
        "Brent Oil": "BRENTOIL.FOREX",
        "Nat Gas": "NATGASUSD.FOREX",
        "Copper": "XCUUSD.FOREX",
        # Indices — confirmed .INDX format works
        "S&P 500": "GSPC.INDX",
        "Nasdaq": "IXIC.INDX",
        "NASDAQ-100": "IXIC.INDX",
        "Dow Jones": "DJI.INDX",
        "DAX 40": "GDAXI.INDX",
        "UK100": "FTSE.INDX",
        "Nikkei 225": "N225.INDX",
        "Hang Seng": "HSI.INDX",
        "ASX 200": "AXJO.INDX",
    }
    if disp in _OVERRIDES:
        return _OVERRIDES[disp]

    if ptype == "forex":
        return disp.replace("/", "") + ".FOREX"
    if ptype == "stock":
        return sym.replace("=X", "").replace(".US", "") + ".US"
    if ptype == "crypto":
        base = disp.replace("/USDT", "").replace("/USD", "")
        return f"{base}-USD.CC"
    if ptype == "index":
        # Special case for FTSE and other indices
        if disp == "FTSE":
            return "FTSE.INDX"
        return sym.lstrip("^") + ".INDX"

    return None


def _fetch_eodhd_live_prices(pairs: list) -> None:
    """Batch-fetch EODHD real-time prices for non-WS pairs.
    Covers forex (~1min delay) and stocks (15-20min delay).
    Optimized for API efficiency: groups by type, uses 15-symbol batches.
    Commodities/indices: tested via COMM/INDX suffix — logs failures."""
    import requests as _req

    _key = os.environ.get("EODHD_KEY", "")
    if not _key:
        return

    # Filter and group pairs by type for optimal API usage
    forex_pairs = []
    stock_pairs = []
    other_pairs = []

    for p in pairs:
        s = _eodhd_rt_symbol(p)
        if not s:
            continue

        if p.get("type") == "forex":
            forex_pairs.append((s, p["display"]))
        elif p.get("type") == "stock":
            stock_pairs.append((s, p["display"]))
        else:
            other_pairs.append((s, p["display"]))

    all_pairs = forex_pairs + stock_pairs + other_pairs
    if not all_pairs:
        return

    log.debug(
        f"[PRICE-POLL] Fetching {len(all_pairs)} pairs: {len(forex_pairs)} forex, {len(stock_pairs)} stocks, {len(other_pairs)} other"
    )

    # Process in optimal batches of 15 (EODHD recommendation)
    symbols = [s for s, _ in all_pairs]
    display_map = {s: d for s, d in all_pairs}

    for i in range(0, len(symbols), 15):
        batch = symbols[i : i + 15]
        try:
            # Use efficient batch API call
            url = (
                f"https://eodhd.com/api/real-time/{batch[0]}"
                f"?s={','.join(batch[1:])}&api_token={_key}&fmt=json"
            )
            resp = _req.get(url, timeout=8)
            if resp.status_code != 200:
                log.warning(
                    f"[PRICE-POLL] EODHD batch {i // 15 + 1}/{(len(symbols) - 1) // 15 + 1}: HTTP {resp.status_code}"
                )
                continue
            try:
                items = resp.json()
            except ValueError as je:
                log.warning(
                    f"[PRICE-POLL] EODHD batch {i // 15 + 1}: invalid JSON (404/HTML?) — {je}"
                )
                continue
            if not isinstance(items, list):
                items = [items]
            updated = 0
            for item in items:
                code = item.get("code", "")
                px = item.get("close") or item.get("adjusted_close")
                disp = display_map.get(code)
                if disp and px:
                    with _live_prices_lock:
                        _live_prices[disp] = {"price": float(px), "ts": time.time()}
                    updated += 1
                elif disp:
                    if _PRICE_POLL_FIRST_RUN:
                        log.info(
                            f"[PRICE-POLL] {disp} ({code}): no price — not on plan, will use candle fallback"
                        )
                    else:
                        log.debug(
                            f"[PRICE-POLL] {disp} ({code}): no price in response — symbol may not be on plan"
                        )
            log.info(
                f"[PRICE-POLL] EODHD batch {i // 15 + 1}: {updated}/{len(batch)} updated"
            )
        except Exception as _e:
            log.warning(f"[PRICE-POLL] EODHD batch {i // 15 + 1} error: {_e}")


# Tracks last successful WS recv per feed so health checks can distinguish
# "thread alive" from "actually receiving messages".
_binance_ws_last_recv_ts: float = 0.0


_BINANCE_WS_PRICE_STALE_SEC_DEFAULT = 10.0


def _epoch_seconds(value) -> float | None:
    try:
        ts = float(value)
    except (TypeError, ValueError):
        return None
    if ts <= 0:
        return None
    return ts / 1000.0 if ts > 1e12 else ts


def _age_seconds(now_ts: float, value) -> float | None:
    ts = _epoch_seconds(value)
    if ts is None:
        return None
    return max(0.0, now_ts - ts)


def _binance_ws_price_stale_sec() -> float:
    try:
        cfg = getattr(rt(), "CONFIG", {}) or {}
    except RuntimeError:
        cfg = {}
    raw = cfg.get("BINANCE_WS_PRICE_STALE_SEC", _BINANCE_WS_PRICE_STALE_SEC_DEFAULT)
    try:
        value = float(raw)
    except (TypeError, ValueError):
        value = _BINANCE_WS_PRICE_STALE_SEC_DEFAULT
    return value if value > 0 else _BINANCE_WS_PRICE_STALE_SEC_DEFAULT


def _normalize_binance_symbol(value) -> str:
    return (
        str(value or "")
        .strip()
        .upper()
        .replace("/", "")
        .replace(" ", "")
        .replace("_", "")
        .replace("-", "")
        .replace(":", "")
    )


def _binance_crypto_symbol_map(pairs=None) -> dict[str, str]:
    if pairs is None:
        try:
            pairs = rt().CRYPTO_PAIRS
        except RuntimeError:
            pairs = []
    out: dict[str, str] = {}
    for pair in pairs or []:
        if not isinstance(pair, dict) or not pair.get("enabled", True):
            continue
        display = str(pair.get("display") or "").strip()
        if not display:
            continue
        for raw in (pair.get("symbol"), display):
            norm = _normalize_binance_symbol(raw)
            if norm:
                out[norm] = display
    return out


def _decorate_binance_price_diagnostics(entry: dict, now_ts: float) -> dict:
    out = dict(entry)
    ws_age = _age_seconds(now_ts, out.get("last_ws_ts"))
    rest_age = _age_seconds(now_ts, out.get("last_rest_ts"))
    out["ws_age_sec"] = round(ws_age, 3) if ws_age is not None else None
    out["rest_age_sec"] = round(rest_age, 3) if rest_age is not None else None
    out.setdefault("preferred_source", out.get("source"))
    return out


def _merge_binance_ws_price(existing, price: float, bid, ask, now_ts: float) -> dict:
    old = existing if isinstance(existing, dict) else {}
    entry = {
        "price": float(price),
        "ts": now_ts,
        "source": "binance_ws",
        "last_ws_ts": now_ts,
        "ws_price": float(price),
        "last_rest_ts": old.get("last_rest_ts"),
        "rest_price": old.get("rest_price"),
        "preferred_source": "binance_ws",
        "overwrite_reason": "ws_update_preferred",
    }
    if bid is not None and float(bid) > 0:
        entry["bid"] = float(bid)
    if ask is not None and float(ask) > 0:
        entry["ask"] = float(ask)
    return _decorate_binance_price_diagnostics(entry, now_ts)


def _merge_binance_rest_price(
    existing,
    price: float,
    now_ts: float,
    ws_stale_sec: float | None = None,
) -> dict:
    old = existing if isinstance(existing, dict) else {}
    ws_stale_sec = ws_stale_sec or _binance_ws_price_stale_sec()
    last_ws_ts = old.get("last_ws_ts")
    if last_ws_ts is None and old.get("source") == "binance_ws":
        last_ws_ts = old.get("ts")
    ws_age = _age_seconds(now_ts, last_ws_ts)
    ws_price = old.get("ws_price")
    if ws_price is None and old.get("source") == "binance_ws":
        ws_price = old.get("price")

    if ws_age is not None and ws_age <= ws_stale_sec and ws_price is not None:
        entry = dict(old)
        entry.update(
            {
                "price": float(ws_price),
                "source": "binance_ws",
                "last_ws_ts": last_ws_ts,
                "ws_price": float(ws_price),
                "last_rest_ts": now_ts,
                "rest_price": float(price),
                "preferred_source": "binance_ws",
                "overwrite_reason": "fresh_ws_preferred_over_rest",
            }
        )
        return _decorate_binance_price_diagnostics(entry, now_ts)

    reason = "ws_missing"
    if ws_age is not None:
        reason = "ws_stale"
    entry = {
        "price": float(price),
        "ts": now_ts,
        "source": "binance_rest",
        "last_ws_ts": last_ws_ts,
        "ws_price": ws_price,
        "last_rest_ts": now_ts,
        "rest_price": float(price),
        "preferred_source": "binance_rest",
        "overwrite_reason": reason,
    }
    return _decorate_binance_price_diagnostics(entry, now_ts)


def _record_binance_ws_price(symbol, price, bid=None, ask=None, *, now_ts=None, pairs=None) -> bool:
    display = _binance_crypto_symbol_map(pairs).get(_normalize_binance_symbol(symbol))
    if not display:
        return False
    try:
        price_f = float(price)
    except (TypeError, ValueError):
        return False
    if price_f <= 0:
        return False
    now = float(now_ts) if now_ts is not None else time.time()
    with _live_prices_lock:
        _live_prices[display] = _merge_binance_ws_price(
            _live_prices.get(display), price_f, bid, ask, now
        )
    return True


def _record_binance_rest_price(
    symbol,
    price,
    *,
    now_ts=None,
    pairs=None,
    ws_stale_sec: float | None = None,
) -> bool:
    display = _binance_crypto_symbol_map(pairs).get(_normalize_binance_symbol(symbol))
    if not display:
        return False
    try:
        price_f = float(price)
    except (TypeError, ValueError):
        return False
    if price_f <= 0:
        return False
    now = float(now_ts) if now_ts is not None else time.time()
    with _live_prices_lock:
        _live_prices[display] = _merge_binance_rest_price(
            _live_prices.get(display), price_f, now, ws_stale_sec=ws_stale_sec
        )
    return True


_BYBIT_WS_PRICE_STALE_SEC_DEFAULT = 10.0
_BYBIT_SYMBOL_MAP = {
    "MATICUSDT": "POLUSDT",
}
_bybit_ws_last_recv_ts: float = 0.0
_bybit_live_price_ws: "BybitLivePriceWS | None" = None


def _bybit_config() -> dict:
    try:
        return getattr(rt(), "CONFIG", {}) or {}
    except RuntimeError:
        return {}


def _crypto_execution_provider(cfg: dict | None = None) -> str:
    cfg = cfg if cfg is not None else _bybit_config()
    explicit = cfg.get("CRYPTO_EXECUTION_PROVIDER")
    if explicit:
        return str(explicit).strip().lower()
    by_class = cfg.get("EXECUTION_PROVIDER_BY_ASSET")
    if isinstance(by_class, dict) and by_class.get("crypto"):
        return str(by_class.get("crypto")).strip().lower()
    return "bybit"


def _bybit_ws_enabled(cfg: dict | None = None) -> bool:
    cfg = cfg if cfg is not None else _bybit_config()
    return bool(cfg.get("BYBIT_WS_ENABLED", True))


def _bybit_rest_fallback_enabled(cfg: dict | None = None) -> bool:
    cfg = cfg if cfg is not None else _bybit_config()
    return bool(cfg.get("BYBIT_REST_FALLBACK_ENABLED", True))


def _bybit_ws_price_stale_sec(cfg: dict | None = None) -> float:
    cfg = cfg if cfg is not None else _bybit_config()
    raw = cfg.get("BYBIT_WS_STALE_AFTER_SECONDS", _BYBIT_WS_PRICE_STALE_SEC_DEFAULT)
    try:
        value = float(raw)
    except (TypeError, ValueError):
        value = _BYBIT_WS_PRICE_STALE_SEC_DEFAULT
    return value if value > 0 else _BYBIT_WS_PRICE_STALE_SEC_DEFAULT


def _normalize_bybit_symbol(value) -> str:
    return _normalize_binance_symbol(value)


def _bybit_stream_symbol(bybit_symbol: str) -> str:
    sym = _normalize_bybit_symbol(bybit_symbol)
    return _BYBIT_SYMBOL_MAP.get(sym, sym)


def _bybit_category_for_pair(pair: dict, cfg: dict | None = None) -> str:
    cfg = cfg if cfg is not None else _bybit_config()
    return str(
        pair.get("bybit_category") or cfg.get("CRYPTO_CHART_BYBIT_CATEGORY") or "linear"
    ).strip().lower()


def _bybit_ws_public_url(category: str) -> str:
    cat = str(category or "linear").strip().lower()
    if cat not in {"linear", "spot", "inverse"}:
        cat = "linear"
    return f"wss://stream.bybit.com/v5/public/{cat}"


def _bybit_crypto_symbol_map(pairs=None, *, cfg: dict | None = None) -> dict[str, str]:
    if pairs is None:
        try:
            pairs = rt().CRYPTO_PAIRS
        except RuntimeError:
            pairs = []
    cfg = cfg if cfg is not None else _bybit_config()
    if _crypto_execution_provider(cfg) != "bybit":
        return {}
    out: dict[str, str] = {}
    for pair in pairs or []:
        if not isinstance(pair, dict) or not pair.get("enabled", True):
            continue
        if str(pair.get("type") or "").lower() != "crypto":
            continue
        display = str(pair.get("display") or "").strip()
        if not display:
            continue
        for raw in (pair.get("bybit_symbol"), pair.get("symbol"), display):
            norm = _normalize_bybit_symbol(raw)
            if norm:
                out[norm] = display
    return out


def _decorate_bybit_price_diagnostics(entry: dict, now_ts: float) -> dict:
    out = dict(entry)
    ws_age = _age_seconds(now_ts, out.get("last_ws_ts"))
    rest_age = _age_seconds(now_ts, out.get("last_rest_ts"))
    out["ws_age_sec"] = round(ws_age, 3) if ws_age is not None else None
    out["rest_age_sec"] = round(rest_age, 3) if rest_age is not None else None
    out.setdefault("preferred_source", out.get("source"))
    return out


def _merge_bybit_ws_price(
    existing,
    price: float,
    bid,
    ask,
    now_ts: float,
    *,
    bybit_symbol: str,
    category: str,
) -> dict:
    old = existing if isinstance(existing, dict) else {}
    entry = {
        "price": float(price),
        "ts": now_ts,
        "source": "bybit_ws",
        "last_ws_ts": now_ts,
        "ws_price": float(price),
        "last_rest_ts": old.get("last_rest_ts"),
        "rest_price": old.get("rest_price"),
        "preferred_source": "bybit_ws",
        "overwrite_reason": "ws_update_preferred",
        "bybit_symbol": _normalize_bybit_symbol(bybit_symbol),
        "bybit_category": str(category or "linear").lower(),
    }
    if bid is not None and float(bid) > 0:
        entry["bid"] = float(bid)
    if ask is not None and float(ask) > 0:
        entry["ask"] = float(ask)
    return _decorate_bybit_price_diagnostics(entry, now_ts)


def _bybit_entry_ws_fields(entry: dict | None, now_ts: float) -> tuple[float | None, float | None, float | None]:
    """Return (ws_price, last_ws_ts, ws_age) from a live-prices cache entry."""
    old = entry if isinstance(entry, dict) else {}
    last_ws_ts = old.get("last_ws_ts")
    if last_ws_ts is None and str(old.get("source") or "") == "bybit_ws":
        last_ws_ts = old.get("ts")
    ws_price = old.get("ws_price")
    if ws_price is None and str(old.get("source") or "") == "bybit_ws":
        ws_price = old.get("price")
    ws_age = _age_seconds(now_ts, last_ws_ts)
    return ws_price, last_ws_ts, ws_age


def _merge_bybit_rest_price(
    existing,
    price: float,
    now_ts: float,
    *,
    bybit_symbol: str,
    category: str,
    ws_stale_sec: float | None = None,
) -> dict:
    old = existing if isinstance(existing, dict) else {}
    ws_stale_sec = ws_stale_sec or _bybit_ws_price_stale_sec()
    ws_price, last_ws_ts, ws_age = _bybit_entry_ws_fields(old, now_ts)

    # While the global Bybit WS feed is live, keep WS as the primary cache price.
    # REST poll updates rest metadata only so chart/API stay on bybit_ws between ticks.
    if _bybit_ws_runtime_state().get("websocket_active") and ws_price is not None:
        entry = dict(old)
        entry.update(
            {
                "last_rest_ts": now_ts,
                "rest_price": float(price),
                "overwrite_reason": "rest_metadata_while_ws_active",
                "bybit_symbol": _normalize_bybit_symbol(bybit_symbol),
                "bybit_category": str(category or "linear").lower(),
            }
        )
        return _decorate_bybit_price_diagnostics(entry, now_ts)

    if ws_age is not None and ws_age <= ws_stale_sec and ws_price is not None:
        entry = dict(old)
        entry.update(
            {
                "price": float(ws_price),
                "source": "bybit_ws",
                "last_ws_ts": last_ws_ts,
                "ws_price": float(ws_price),
                "last_rest_ts": now_ts,
                "rest_price": float(price),
                "preferred_source": "bybit_ws",
                "overwrite_reason": "fresh_ws_preferred_over_rest",
                "bybit_symbol": _normalize_bybit_symbol(bybit_symbol),
                "bybit_category": str(category or "linear").lower(),
            }
        )
        return _decorate_bybit_price_diagnostics(entry, now_ts)

    reason = "ws_missing"
    if ws_age is not None:
        reason = "ws_stale"
    entry = {
        "price": float(price),
        "ts": now_ts,
        "source": "bybit_rest",
        "last_ws_ts": last_ws_ts,
        "ws_price": ws_price,
        "last_rest_ts": now_ts,
        "rest_price": float(price),
        "preferred_source": "bybit_rest",
        "overwrite_reason": reason,
        "bybit_symbol": _normalize_bybit_symbol(bybit_symbol),
        "bybit_category": str(category or "linear").lower(),
    }
    return _decorate_bybit_price_diagnostics(entry, now_ts)


def _record_bybit_ws_price(
    bybit_symbol,
    price,
    bid=None,
    ask=None,
    *,
    now_ts=None,
    pairs=None,
    category: str = "linear",
) -> bool:
    display = _bybit_crypto_symbol_map(pairs).get(_normalize_bybit_symbol(bybit_symbol))
    if not display:
        return False
    try:
        price_f = float(price)
    except (TypeError, ValueError):
        return False
    if price_f <= 0:
        return False
    now = float(now_ts) if now_ts is not None else time.time()
    with _live_prices_lock:
        _live_prices[display] = _merge_bybit_ws_price(
            _live_prices.get(display),
            price_f,
            bid,
            ask,
            now,
            bybit_symbol=bybit_symbol,
            category=category,
        )
    return True


def _record_bybit_rest_price(
    bybit_symbol,
    price,
    *,
    now_ts=None,
    pairs=None,
    category: str = "linear",
    ws_stale_sec: float | None = None,
) -> bool:
    display = _bybit_crypto_symbol_map(pairs).get(_normalize_bybit_symbol(bybit_symbol))
    if not display:
        return False
    try:
        price_f = float(price)
    except (TypeError, ValueError):
        return False
    if price_f <= 0:
        return False
    now = float(now_ts) if now_ts is not None else time.time()
    with _live_prices_lock:
        _live_prices[display] = _merge_bybit_rest_price(
            _live_prices.get(display),
            price_f,
            now,
            bybit_symbol=bybit_symbol,
            category=category,
            ws_stale_sec=ws_stale_sec,
        )
    return True


def _bybit_ws_runtime_state() -> dict:
    ws = _bybit_live_price_ws
    now = time.time()
    last_recv = None
    if ws is not None and ws.last_recv_ts:
        last_recv = float(ws.last_recv_ts)
    elif _bybit_ws_last_recv_ts > 0:
        last_recv = float(_bybit_ws_last_recv_ts)
    thread_alive = bool(ws and ws._thread and ws._thread.is_alive())
    recv_age = _age_seconds(now, last_recv) if last_recv else None
    stale_sec = _bybit_ws_price_stale_sec()
    websocket_active = bool(
        _bybit_ws_enabled()
        and thread_alive
        and last_recv is not None
        and recv_age is not None
        and recv_age <= stale_sec
    )
    websocket_stale = bool(
        _bybit_ws_enabled()
        and (last_recv is None or recv_age is None or recv_age > stale_sec)
    )
    return {
        "websocket_active": websocket_active,
        "websocket_stale": websocket_stale,
        "websocket_last_message_ts": last_recv,
        "ws_thread_alive": thread_alive,
        "ws_last_error": getattr(ws, "last_error", None) if ws else None,
    }


def get_bybit_live_tick(
    display: str,
    *,
    bybit_symbol: str,
    category: str = "linear",
    pairs=None,
) -> dict:
    """Return WS-first live tick diagnostics for chart/API (no REST fetch)."""
    cfg = _bybit_config()
    preference = str(cfg.get("CRYPTO_LIVE_TICK_PROVIDER_PREFERENCE") or "bybit_ws").strip().lower()
    ws_state = _bybit_ws_runtime_state()
    now = time.time()
    stale_sec = _bybit_ws_price_stale_sec(cfg)

    with _live_prices_lock:
        entry = _live_prices.get(display)

    if not _bybit_ws_enabled(cfg) or preference != "bybit_ws":
        reason = "ws_disabled" if not _bybit_ws_enabled(cfg) else "ws_preference_not_bybit_ws"
        return {
            "tick": None,
            "fallback_reason": reason,
            **ws_state,
        }

    if not isinstance(entry, dict):
        return {
            "tick": None,
            "fallback_reason": "ws_missing",
            **ws_state,
        }

    price, last_ws_ts, ws_age = _bybit_entry_ws_fields(entry, now)
    ws_active = bool(ws_state.get("websocket_active"))

    if price is None or ws_age is None:
        return {
            "tick": None,
            "fallback_reason": "ws_missing",
            **ws_state,
        }

    if ws_age > stale_sec and not ws_active:
        return {
            "tick": None,
            "fallback_reason": "ws_stale",
            **ws_state,
        }

    ts = _epoch_seconds(last_ws_ts) or now
    tick = {
        "symbol": _normalize_bybit_symbol(bybit_symbol),
        "price": float(price),
        "bid": entry.get("bid"),
        "ask": entry.get("ask"),
        "timestamp": ts,
        "ts": ts,
        "provider": "bybit_ws",
        "source": "bybit_ws",
        "category": str(entry.get("bybit_category") or category).lower(),
        "age_seconds": round(ws_age, 3),
        "bybit_symbol": _normalize_bybit_symbol(bybit_symbol),
        "bybit_category": str(entry.get("bybit_category") or category).lower(),
    }
    return {
        "tick": tick,
        "fallback_reason": None,
        "fallback_used": False,
        **ws_state,
    }


def _parse_bybit_ticker_payload(data: dict, recv_ts: float) -> tuple[str, float, float | None, float | None] | None:
    if not isinstance(data, dict):
        return None
    symbol = _normalize_bybit_symbol(data.get("symbol") or "")
    if not symbol:
        return None
    price_raw = data.get("lastPrice") or data.get("markPrice") or data.get("indexPrice")
    try:
        price = float(price_raw)
    except (TypeError, ValueError):
        return None
    if price <= 0:
        return None
    bid = ask = None
    bid_raw = data.get("bid1Price") or data.get("bid")
    ask_raw = data.get("ask1Price") or data.get("ask")
    try:
        if bid_raw not in (None, ""):
            bid = float(bid_raw)
    except (TypeError, ValueError):
        bid = None
    try:
        if ask_raw not in (None, ""):
            ask = float(ask_raw)
    except (TypeError, ValueError):
        ask = None
    return symbol, price, bid, ask


def _run_bybit_price_poller(poll_interval: float = 3.0):
    """REST poll Bybit tickers for bybit-execution crypto pairs (WS fallback only)."""
    from data_feeds import _fetch_bybit_ticker

    log.info("[BYBIT-PRICE-POLL] Started — interval=%.1fs", poll_interval)
    while True:
        try:
            if not _bybit_rest_fallback_enabled():
                time.sleep(poll_interval)
                continue
            try:
                _cp = list(rt().CRYPTO_PAIRS)
            except RuntimeError:
                _cp = []
            symbol_map = _bybit_crypto_symbol_map(_cp)
            if not symbol_map:
                time.sleep(poll_interval)
                continue

            cfg = _bybit_config()
            now_ts = time.time()
            updated = 0
            for bybit_symbol in sorted(symbol_map):
                pair = next(
                    (
                        p
                        for p in _cp
                        if _normalize_bybit_symbol(
                            p.get("bybit_symbol") or p.get("symbol") or p.get("display")
                        )
                        == bybit_symbol
                    ),
                    {},
                )
                category = _bybit_category_for_pair(pair, cfg)
                raw = _fetch_bybit_ticker(bybit_symbol, category=category)
                if not raw or not raw.get("price"):
                    continue
                if _record_bybit_rest_price(
                    bybit_symbol,
                    raw.get("price"),
                    now_ts=now_ts,
                    pairs=_cp,
                    category=category,
                    ws_stale_sec=max(_bybit_ws_price_stale_sec(cfg), poll_interval * 3.0),
                ):
                    updated += 1
            if updated == 0:
                log.debug("[BYBIT-PRICE-POLL] no bybit-execution matches updated")
        except Exception as e:
            log.warning("[BYBIT-PRICE-POLL] cycle error: %s", e)
        time.sleep(poll_interval)


class BybitLivePriceWS:
    """Bybit public linear/spot/inverse ticker WebSocket for live crypto execution prices."""

    def __init__(self):
        self._running = True
        self._thread = None
        self._reconnect_attempt = 0
        self.last_connect_ts: float | None = None
        self.last_recv_ts: float | None = None
        self.last_error: str | None = None
        self.match_symbols: list[str] = []

    def _next_reconnect_delay(self) -> float:
        base = 2.0
        cap = 60.0
        exp = min(cap, base * (2 ** min(self._reconnect_attempt, 6)))
        jitter = random.uniform(0.0, min(3.0, exp * 0.2))
        return exp + jitter

    def _subscriptions_by_category(self, pairs: list) -> dict[str, list[str]]:
        cfg = _bybit_config()
        if _crypto_execution_provider(cfg) != "bybit" or not _bybit_ws_enabled(cfg):
            return {}
        out: dict[str, list[str]] = {}
        for pair in pairs or []:
            if not isinstance(pair, dict) or not pair.get("enabled", True):
                continue
            if str(pair.get("type") or "").lower() != "crypto":
                continue
            sym = _normalize_bybit_symbol(
                pair.get("bybit_symbol") or pair.get("symbol") or pair.get("display")
            )
            if not sym:
                continue
            stream_sym = _bybit_stream_symbol(sym)
            cat = _bybit_category_for_pair(pair, cfg)
            out.setdefault(cat, [])
            if stream_sym not in out[cat]:
                out[cat].append(stream_sym)
        return out

    def _connect_and_stream_category(self, category: str, stream_symbols: list[str]):
        import websockets

        url = _bybit_ws_public_url(category)
        topics = [f"tickers.{s}" for s in stream_symbols]

        while self._running:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            try:
                loop.run_until_complete(
                    self._stream_category(url, category, topics, stream_symbols)
                )
            except Exception as e:
                self.last_error = f"connection_error:{e}"
                log.error("[BYBIT-WS] %s connection error: %s", category, e)
                if self._running:
                    self._reconnect_attempt += 1
                    delay = self._next_reconnect_delay()
                    log.warning(
                        "[BYBIT-WS] %s reconnect attempt=%s delay=%.1fs",
                        category,
                        self._reconnect_attempt,
                        delay,
                    )
                    time.sleep(delay)
            finally:
                try:
                    loop.close()
                except Exception:
                    pass

    async def _stream_category(
        self,
        url: str,
        category: str,
        topics: list[str],
        stream_symbols: list[str],
    ):
        import websockets

        try:
            _cp = list(rt().CRYPTO_PAIRS)
        except RuntimeError:
            _cp = []

        async with websockets.connect(
            url,
            ping_interval=None,
            ping_timeout=None,
            open_timeout=45,
            close_timeout=10,
            ssl=default_ssl_context(),
        ) as ws:
            self._reconnect_attempt = 0
            self.last_connect_ts = time.time()
            self.last_error = None
            subscribe_msg = {
                "req_id": str(int(time.time() * 1000)),
                "op": "subscribe",
                "args": topics,
            }
            await ws.send(json.dumps(subscribe_msg))
            log.info(
                "[BYBIT-WS] Connected %s — subscribed %d tickers (%s)",
                url,
                len(topics),
                category,
            )
            _last_ping = time.time()
            reverse_map = {
                _bybit_stream_symbol(s): _normalize_bybit_symbol(s) for s in stream_symbols
            }
            while self._running:
                try:
                    raw = await asyncio.wait_for(ws.recv(), timeout=60)
                except asyncio.TimeoutError:
                    if time.time() - _last_ping > 40:
                        try:
                            await ws.send(json.dumps({"op": "ping"}))
                            _last_ping = time.time()
                            continue
                        except Exception:
                            pass
                    self.last_error = "receive_timeout"
                    log.warning("[BYBIT-WS] %s receive timeout; reconnecting", category)
                    break
                if not raw:
                    continue
                if isinstance(raw, (bytes, bytearray)):
                    raw = raw.decode("utf-8", errors="replace")
                try:
                    msg = json.loads(raw)
                except json.JSONDecodeError:
                    self.last_error = "json_decode"
                    break
                if isinstance(msg, dict) and msg.get("op") == "ping":
                    await ws.send(json.dumps({"op": "pong"}))
                    _last_ping = time.time()
                    continue
                recv_ts = time.time()
                global _bybit_ws_last_recv_ts
                _bybit_ws_last_recv_ts = recv_ts
                self.last_recv_ts = recv_ts

                topic = str(msg.get("topic") or "")
                if not topic.startswith("tickers."):
                    continue
                data = msg.get("data")
                if isinstance(data, list) and data:
                    data = data[0]
                parsed = _parse_bybit_ticker_payload(data if isinstance(data, dict) else {}, recv_ts)
                if not parsed:
                    continue
                symbol, price, bid, ask = parsed
                cache_symbol = reverse_map.get(symbol, symbol)
                _record_bybit_ws_price(
                    cache_symbol,
                    price,
                    bid=bid,
                    ask=ask,
                    now_ts=recv_ts,
                    pairs=_cp,
                    category=category,
                )

    def _connect_and_stream(self):
        try:
            _cp = list(rt().CRYPTO_PAIRS)
        except RuntimeError:
            _cp = []
        subs = self._subscriptions_by_category(_cp)
        self.match_symbols = sorted(
            {_bybit_stream_symbol(s) for syms in subs.values() for s in syms}
        )
        if not subs:
            log.info("[BYBIT-WS] No bybit-execution crypto pairs — feed idle")
            while self._running:
                time.sleep(5.0)
            return

        threads: list[threading.Thread] = []
        for category, stream_symbols in subs.items():
            if not stream_symbols:
                continue
            t = threading.Thread(
                target=self._connect_and_stream_category,
                args=(category, stream_symbols),
                daemon=True,
                name=f"BybitLivePriceWS-{category}",
            )
            t.start()
            threads.append(t)
        for t in threads:
            while self._running and t.is_alive():
                t.join(timeout=1.0)

    def start(self):
        global _bybit_live_price_ws
        if not _bybit_ws_enabled():
            log.info("[BYBIT-WS] Disabled via BYBIT_WS_ENABLED")
            return
        _bybit_live_price_ws = self
        if self._thread is None or not self._thread.is_alive():
            self._running = True
            self._thread = threading.Thread(
                target=self._connect_and_stream,
                daemon=True,
                name="BybitLivePriceWS",
            )
            self._thread.start()
            log.info("[BYBIT-WS] Started Bybit live price feed thread")

    def stop(self):
        self._running = False
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=5)
        log.info("[BYBIT-WS] Stopped Bybit live price feed thread")


def _run_mt5_tick_poller(poll_interval: float = 1.5):
    """Background daemon: poll MT5 symbol_info_tick for every enabled MT5 pair
    and write bid/ask/mid into _live_prices. Forex/commodity/index/stock prices
    only landed in _live_prices as a side effect of fetch_mt5() before this; this
    keeps them continuously fresh independent of /api/candles traffic.
    """
    log.info("[MT5-PRICE-POLL] Started — interval=%.1fs", poll_interval)
    while True:
        try:
            try:
                pairs = list(rt().ALL_PAIRS)
            except RuntimeError:
                pairs = []

            mt5_pairs = [
                p for p in pairs
                if p.get("source") == "mt5" and p.get("enabled", True)
            ]
            if not mt5_pairs:
                time.sleep(poll_interval)
                continue

            try:
                import mt5_executor
            except Exception as exc:
                log.debug("[MT5-PRICE-POLL] mt5_executor import failed: %s", exc)
                time.sleep(poll_interval)
                continue

            if not mt5_executor.mt5_connect():
                time.sleep(poll_interval)
                continue

            mt5 = mt5_executor._get_mt5()
            if mt5 is None:
                time.sleep(poll_interval)
                continue

            updated = 0
            for p in mt5_pairs:
                try:
                    display = p.get("display") or p.get("symbol") or ""
                    if not display:
                        continue
                    mt5_symbol = mt5_executor.mt5_map_symbol(display)
                    if not mt5_symbol:
                        continue
                    try:
                        mt5.symbol_select(mt5_symbol, True)
                    except Exception:
                        pass
                    tick = mt5.symbol_info_tick(mt5_symbol)
                    if not tick:
                        continue
                    bid = float(tick.bid) if getattr(tick, "bid", 0) > 0 else None
                    ask = float(tick.ask) if getattr(tick, "ask", 0) > 0 else None
                    if bid is not None and ask is not None:
                        price = (bid + ask) / 2.0
                    elif getattr(tick, "last", 0) > 0:
                        price = float(tick.last)
                    elif bid is not None:
                        price = bid
                    elif ask is not None:
                        price = ask
                    else:
                        continue
                    with _live_prices_lock:
                        _live_prices[display] = {
                            "price": price,
                            "bid": bid,
                            "ask": ask,
                            "ts": time.time(),
                            "source": "mt5",
                        }
                    updated += 1
                except Exception as e:
                    log.debug("[MT5-PRICE-POLL] %s tick error: %s", p.get("display"), e)
            if updated == 0:
                log.debug("[MT5-PRICE-POLL] no ticks updated this cycle (%d pairs)", len(mt5_pairs))
        except Exception as e:
            log.warning("[MT5-PRICE-POLL] cycle error: %s", e)
        time.sleep(poll_interval)


def _run_binance_price_poller(poll_interval: float = 3.0):
    """Background daemon: REST poll Binance Futures all-tickers price endpoint
    and write crypto pair prices into _live_prices. Acts as a fallback when
    BinanceLivePriceWS (!ticker@arr) is silently failing — the WS still runs
    in parallel. Fresh WS bid/ask/source remains authoritative over REST last
    prices; REST is preferred only when WS is missing or stale.
    """
    url = "https://fapi.binance.com/fapi/v1/ticker/price"
    log.info("[BINANCE-PRICE-POLL] Started — interval=%.1fs", poll_interval)
    while True:
        try:
            try:
                _cp = list(rt().CRYPTO_PAIRS)
            except RuntimeError:
                _cp = []
            crypto_symbols = _binance_crypto_symbol_map(_cp)
            if not crypto_symbols:
                time.sleep(poll_interval)
                continue

            try:
                resp = http_requests.get(url, timeout=8)
            except Exception as e:
                log.debug("[BINANCE-PRICE-POLL] HTTP error: %s", e)
                time.sleep(poll_interval)
                continue
            if resp.status_code != 200:
                log.debug("[BINANCE-PRICE-POLL] HTTP %s", resp.status_code)
                time.sleep(poll_interval)
                continue
            try:
                items = resp.json()
            except ValueError:
                time.sleep(poll_interval)
                continue
            if not isinstance(items, list):
                time.sleep(poll_interval)
                continue

            now_ts = time.time()
            updated = 0
            for item in items:
                sym = _normalize_binance_symbol(item.get("symbol", ""))
                if sym not in crypto_symbols:
                    continue
                try:
                    price = float(item.get("price", 0))
                except (TypeError, ValueError):
                    continue
                if price <= 0:
                    continue
                if _record_binance_rest_price(
                    sym,
                    price,
                    now_ts=now_ts,
                    pairs=_cp,
                    ws_stale_sec=max(_binance_ws_price_stale_sec(), poll_interval * 3.0),
                ):
                    updated += 1
            if updated == 0:
                log.debug("[BINANCE-PRICE-POLL] no crypto matches in response")
        except Exception as e:
            log.warning("[BINANCE-PRICE-POLL] cycle error: %s", e)
        time.sleep(poll_interval)


def _run_eodhd_price_poller():
    """Background daemon thread: poll EODHD REST prices for non-WS pairs every 21min.
    Optimized for delayed stock data (15-20min delay) and API efficiency."""
    try:
        _nw = rt().NON_WS_EODHD
    except RuntimeError:
        _nw = []
    log.info(
        f"[PRICE-POLL] Started — {len(_nw)} EODHD non-WS pairs (21min interval for delayed stocks)"
    )
    while True:
        try:
            _nw = rt().NON_WS_EODHD
        except RuntimeError:
            _nw = []
        _fetch_eodhd_live_prices(_nw)
        # Reset first-run flag after first cycle
        global _PRICE_POLL_FIRST_RUN
        _PRICE_POLL_FIRST_RUN = False
        time.sleep(21 * 60)  # 21 minutes for delayed stock data optimization


class BinanceLivePriceWS:
    """Binance Futures WebSocket manager for live crypto prices using !ticker@arr stream."""

    def __init__(self):
        self._running = True
        self._thread = None
        self._reconnect_attempt = 0
        self.last_connect_ts: float | None = None
        self.last_recv_ts: float | None = None
        self.last_error: str | None = None
        self.match_symbols: list[str] = []

    def _next_reconnect_delay(self) -> float:
        base = 2.0
        cap = 60.0
        exp = min(cap, base * (2 ** min(self._reconnect_attempt, 6)))
        jitter = random.uniform(0.0, min(3.0, exp * 0.2))
        return exp + jitter

    def _connect_and_stream(self):
        """Connect to Binance Futures WebSocket and stream all market tickers."""
        import websockets
        import json
        import asyncio

        url = "wss://fstream.binance.com/ws/!ticker@arr"

        # Build symbol lookup for faster matching
        try:
            _cp = rt().CRYPTO_PAIRS
        except RuntimeError:
            _cp = []
        crypto_symbols = _binance_crypto_symbol_map(_cp)
        self.match_symbols = sorted(crypto_symbols.keys())

        while self._running:
            try:
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)

                async def _stream():
                    # Disable client PING frames — slow/VPN links often miss PONG in time (1011).
                    # !ticker@arr pushes frequently; recv timeout is the liveness check.
                    async with websockets.connect(
                        url,
                        ping_interval=None,
                        ping_timeout=None,
                        open_timeout=45,
                        close_timeout=10,
                        ssl=default_ssl_context(),
                    ) as ws:
                        self._reconnect_attempt = 0
                        self.last_connect_ts = time.time()
                        self.last_error = None
                        log.info(
                            "[BINANCE-WS] Connected to fstream.binance.com !ticker@arr"
                        )
                        _last_ping = time.time()
                        while self._running:
                            try:
                                data = await asyncio.wait_for(ws.recv(), timeout=45)
                                recv_ts = time.time()
                                global _binance_ws_last_recv_ts
                                _binance_ws_last_recv_ts = recv_ts
                                self.last_recv_ts = recv_ts
                                
                                # Proactive heartbeat
                                if time.time() - _last_ping > 20:
                                    try:
                                        await ws.ping()
                                        _last_ping = time.time()
                                    except Exception:
                                        pass

                                tickers = json.loads(data)

                                for ticker in tickers:
                                    symbol = _normalize_binance_symbol(ticker.get("s", ""))
                                    if symbol in crypto_symbols:
                                        try:
                                            price = float(ticker.get("c", 0) or 0)
                                            bid = float(ticker.get("b", 0) or 0)
                                            ask = float(ticker.get("a", 0) or 0)
                                        except (TypeError, ValueError):
                                            continue

                                        if price > 0:
                                            _record_binance_ws_price(
                                                symbol,
                                                price,
                                                bid=bid,
                                                ask=ask,
                                                now_ts=recv_ts,
                                                pairs=_cp,
                                            )

                            except asyncio.TimeoutError:
                                try:
                                    await asyncio.wait_for(ws.ping(), timeout=10)
                                    _last_ping = time.time()
                                    continue
                                except Exception:
                                    self.last_error = "receive_timeout_ping_failed"
                                    log.warning(
                                        "[BINANCE-WS] Receive timeout + ping failed, reconnecting..."
                                    )
                                    break
                            except json.JSONDecodeError as e:
                                self.last_error = f"json_decode:{e}"
                                log.warning(
                                    f"[BINANCE-WS] Non-JSON payload, reconnecting: {e}"
                                )
                                break
                            except websockets.exceptions.ConnectionClosed as e:
                                self.last_error = f"connection_closed:{e}"
                                log.warning(
                                    f"[BINANCE-WS] Connection closed ({e}), reconnecting..."
                                )
                                break
                            except Exception as e:
                                self.last_error = f"process_error:{e}"
                                log.error(f"[BINANCE-WS] Process error: {e}")
                                break

                loop.run_until_complete(_stream())

            except Exception as e:
                self.last_error = f"connection_error:{e}"
                log.error(f"[BINANCE-WS] Connection error: {e}")
                if self._running:
                    self._reconnect_attempt += 1
                    delay = self._next_reconnect_delay()
                    log.warning(
                        "[BINANCE-WS] Reconnect backoff attempt=%s delay=%.1fs",
                        self._reconnect_attempt,
                        delay,
                    )
                    time.sleep(delay)
            finally:
                try:
                    loop.close()
                except Exception:
                    pass

    def start(self):
        """Start the WebSocket thread."""
        if self._thread is None or not self._thread.is_alive():
            self._running = True
            self._thread = threading.Thread(
                target=self._connect_and_stream, daemon=True, name="BinanceLivePriceWS"
            )
            self._thread.start()
            log.info("[BINANCE-WS] Started Binance Futures price feed thread")

    def stop(self):
        """Stop the WebSocket thread."""
        self._running = False
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=5)
        log.info("[BINANCE-WS] Stopped Binance Futures price feed thread")


class BinanceCandleWS:
    """Binance Futures kline WebSocket for live crypto M1/M5/M15/H1/H4/D1 candles."""

    _STREAM_TFS = {"1m": "M1", "5m": "M5", "15m": "M15", "1h": "H1", "4h": "H4", "1d": "D1"}

    def __init__(self, symbols: list[str] | None = None, intervals: list[str] | None = None):
        self._running = True
        self._thread = None
        self._symbols = [str(s).lower() for s in (symbols or []) if s]
        self._intervals = [str(i).lower() for i in (intervals or []) if str(i).lower() in self._STREAM_TFS]
        self._reconnect_attempt = 0

    def _next_reconnect_delay(self) -> float:
        base = 2.0
        cap = 60.0
        exp = min(cap, base * (2 ** min(self._reconnect_attempt, 6)))
        jitter = random.uniform(0.0, min(3.0, exp * 0.2))
        return exp + jitter

    def _connect_and_stream(self):
        import websockets
        import json
        import asyncio

        try:
            _cp = rt().CRYPTO_PAIRS
        except RuntimeError:
            _cp = []
        enabled = [p for p in _cp if p.get("enabled", True)]
        if not enabled:
            log.info("[BN-KLINE-WS] No enabled crypto pairs, exiting")
            return

        enabled_symbol_map = {
            p["symbol"].replace("/", "").lower(): p["display"]
            for p in enabled
        }
        if self._symbols:
            symbol_map = {
                s: enabled_symbol_map[s]
                for s in self._symbols
                if s in enabled_symbol_map
            }
        else:
            symbol_map = dict(enabled_symbol_map)
        if not symbol_map:
            log.info("[BN-KLINE-WS] No selected crypto symbols matched enabled runtime pairs")
            return

        intervals = self._intervals or list(self._STREAM_TFS.keys())
        if not intervals:
            log.info("[BN-KLINE-WS] No selected intervals; exiting")
            return

        streams = "/".join(
            f"{sym}@kline_{interval}"
            for sym in symbol_map
            for interval in intervals
        )
        url = f"wss://fstream.binance.com/stream?streams={streams}"

        while self._running:
            try:
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)

                async def _stream():
                    async with websockets.connect(
                        url,
                        ping_interval=None,
                        ping_timeout=None,
                        open_timeout=45,
                        close_timeout=10,
                        ssl=default_ssl_context(),
                    ) as ws:
                        self._reconnect_attempt = 0
                        log.info(
                            "[BN-KLINE-WS] Connected — %s crypto pairs on %s",
                            len(symbol_map),
                            ", ".join(f"@kline_{interval}" for interval in intervals),
                        )
                        _last_ping = time.time()
                        while self._running:
                            try:
                                raw = await asyncio.wait_for(ws.recv(), timeout=90)
                                
                                # Proactive heartbeat
                                if time.time() - _last_ping > 20:
                                    try:
                                        await ws.ping()
                                        _last_ping = time.time()
                                    except Exception:
                                        pass

                                msg = json.loads(raw)
                                data = msg.get("data", {})
                                k = data.get("k")
                                if not k:
                                    continue
                                sym_lower = data.get("s", "").lower()
                                display = symbol_map.get(sym_lower)
                                if not display:
                                    continue
                                tf = self._STREAM_TFS.get(str(k.get("i", "")).lower())
                                if not tf:
                                    continue
                                _cb = get_candle_builder()
                                if _cb:
                                    _cb.on_kline(
                                        display,
                                        tf,
                                        float(k["o"]),
                                        float(k["h"]),
                                        float(k["l"]),
                                        float(k["c"]),
                                        float(k.get("v", 0)),
                                        int(k.get("t", 0)),
                                        bool(k.get("x", False)),
                                    )
                            except asyncio.TimeoutError:
                                try:
                                    await asyncio.wait_for(ws.ping(), timeout=10)
                                    _last_ping = time.time()
                                    continue
                                except Exception:
                                    log.warning("[BN-KLINE-WS] Receive timeout + ping failed, reconnecting...")
                                    break
                            except json.JSONDecodeError as e:
                                log.warning(f"[BN-KLINE-WS] JSON error: {e}")
                                break
                            except websockets.exceptions.ConnectionClosed as e:
                                log.warning(f"[BN-KLINE-WS] Connection closed ({e}), reconnecting...")
                                break
                            except Exception as e:
                                log.error(f"[BN-KLINE-WS] Process error: {e}")
                                break

                loop.run_until_complete(_stream())

            except Exception as e:
                log.error(f"[BN-KLINE-WS] Connection error: {e}")
                if self._running:
                    self._reconnect_attempt += 1
                    delay = self._next_reconnect_delay()
                    log.warning(
                        "[BN-KLINE-WS] Reconnect backoff attempt=%s delay=%.1fs",
                        self._reconnect_attempt,
                        delay,
                    )
                    time.sleep(delay)
            finally:
                try:
                    loop.close()
                except Exception:
                    pass

    def start(self):
        if self._thread is None or not self._thread.is_alive():
            self._running = True
            self._thread = threading.Thread(
                target=self._connect_and_stream, daemon=True, name="BinanceCandleWS"
            )
            self._thread.start()
            log.info("[BN-KLINE-WS] Started Binance Futures kline feed thread")

    def stop(self):
        self._running = False
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=5)
        log.info("[BN-KLINE-WS] Stopped")


DEFAULT_BINANCE_KLINE_WS_INTERVALS = ["1m", "5m", "15m", "1h", "4h", "1d"]


def resolve_binance_kline_ws_intervals(config: dict | None = None) -> list[str]:
    """Return configured Binance kline intervals, limited to supported streams."""
    cfg = config if isinstance(config, dict) else {}
    raw = cfg.get("BINANCE_KLINE_WS_INTERVALS", DEFAULT_BINANCE_KLINE_WS_INTERVALS)
    if not isinstance(raw, (list, tuple)):
        raw = DEFAULT_BINANCE_KLINE_WS_INTERVALS

    supported = set(BinanceCandleWS._STREAM_TFS)
    out = []
    for item in raw:
        interval = str(item or "").strip().lower()
        if interval in supported and interval not in out:
            out.append(interval)
    return out or list(DEFAULT_BINANCE_KLINE_WS_INTERVALS)


def _format_eodhd_ws_disconnect(exc: BaseException) -> str:
    """Readable disconnect detail for logs (websockets 16 uses rcvd/sent, not exc.code)."""
    name = type(exc).__name__
    text = str(exc).strip()
    parts = [name]
    rcvd = getattr(exc, "rcvd", None)
    if rcvd is not None:
        c = getattr(rcvd, "code", None)
        r = getattr(rcvd, "reason", None) or ""
        r = r.strip() if isinstance(r, str) else ""
        if c is not None:
            parts.append(f"rcv_code={c}")
        if r:
            parts.append(f"rcv_reason={r!r}")
    # Fallback for exceptions that expose code/reason on self (non-websockets)
    if len(parts) == 1:
        c2 = getattr(exc, "code", None)
        if c2 is not None:
            parts.append(f"code={c2}")
        r2 = getattr(exc, "reason", None)
        if isinstance(r2, str) and r2.strip():
            parts.append(f"reason={r2.strip()!r}")
    if text and text not in parts:
        parts.append(text)
    out = " ".join(parts)
    return out if len(out) > len(name) else (text or out or repr(exc))


class EODHDWebSocketManager:
    """Manages 3 persistent WebSocket connections (US, Forex, Crypto) for real-time prices."""

    WS_BASE = "wss://ws.eodhistoricaldata.com/ws"

    def __init__(self, api_key):

        self._key = api_key

        self._loop = None

        self._thread = None

    def _build_ticker_map(self, pairs):
        """Map Athena display names â†’ {ws_endpoint: [ws_tickers], display_map: {ws_ticker: display}}"""

        us_tickers, fx_tickers, cr_tickers = [], [], []

        display_map = {}  # ws_ticker â†’ athena display name

        for p in pairs:
            if not p.get("ws", True):  # default True = backward-compatible
                continue

            disp, ptype = p["display"], p.get("type", "")

            sym = p.get("symbol", "")

            if ptype == "crypto":
                # Crypto pairs are now handled by BinanceLivePriceWS - skip EODHD mapping
                continue

            elif ptype in ("stock",) and ".US" in sym:
                ws_t = sym.replace(".US", "")

                us_tickers.append(ws_t)

                display_map[ws_t] = disp

            elif ptype in ("forex", "commodity") or ("/" in disp and ptype != "crypto"):
                ws_t = disp.replace("/", "")

                fx_tickers.append(ws_t)

                display_map[ws_t] = disp

            elif ptype == "index":
                # Indices use EODHD .INDX suffix (same as REST API).
                # US indices → us endpoint; international indices → forex endpoint.
                ws_t = sym.lstrip("^") + ".INDX"

                if sym in ("^GSPC", "^IXIC", "^DJI"):
                    us_tickers.append(ws_t)
                else:
                    fx_tickers.append(ws_t)

                display_map[ws_t] = disp

        return {
            "us": us_tickers,
            "forex": fx_tickers,
            "crypto": cr_tickers,
            "map": display_map,
        }

    async def _connect(self, endpoint, tickers, display_map):
        """Connect to one WS endpoint, subscribe, and stream prices forever."""

        import websockets

        url = f"{self.WS_BASE}/{endpoint}?api_token={self._key}"
        delay = 5.0
        max_delay = 60.0

        while True:
            try:
                async with websockets.connect(
                    url,
                    ping_interval=None,
                    ping_timeout=None,
                    open_timeout=45,
                    close_timeout=10,
                    ssl=default_ssl_context(),
                ) as ws:
                    delay = 5.0  # reset backoff after a successful handshake
                    # EODHD manages keepalive at application layer (heartbeat messages).

                    # WebSocket-level pings cause 1011 errors because EODHD doesn't respond

                    # to protocol-level PINGs — disable them and rely on app heartbeats.

                    sub = json.dumps(
                        {"action": "subscribe", "symbols": ",".join(tickers)}
                    )

                    await ws.send(sub)

                    log.info(f"[WS] {endpoint}: subscribed to {len(tickers)} tickers")

                    async for raw in ws:
                        try:
                            msg = json.loads(raw)

                            s = msg.get("s", "")

                            if not s or s == "heartbeat":
                                continue

                            disp = display_map.get(s, s)

                            def _f(k):

                                v = msg.get(k)

                                if v is None:
                                    return None

                                try:
                                    return float(v)

                                except (ValueError, TypeError):
                                    return None

                            # EODHD WS reports `t` in milliseconds; normalize to
                            # seconds so every _live_prices producer uses the
                            # same epoch unit. Magnitude check tolerates an
                            # already-seconds value if the upstream schema ever
                            # changes.
                            _t_raw = msg.get("t", 0)
                            try:
                                _t_val = float(_t_raw)
                                _ts_sec = _t_val / 1000.0 if _t_val > 1e12 else _t_val
                            except (TypeError, ValueError):
                                _ts_sec = 0

                            entry = {"ts": _ts_sec, "source": f"eodhd_{endpoint}"}

                            if endpoint == "forex":
                                bid, ask = _f("b"), _f("a")

                                entry["bid"] = bid

                                entry["ask"] = ask

                                entry["price"] = (
                                    round((bid + ask) / 2, 6) if bid and ask else None
                                )

                                if bid and ask:
                                    raw_spread = ask - bid

                                    if "XAU" in s or "XAG" in s:
                                        entry["spread"] = round(raw_spread, 2)

                                        entry["spreadUnit"] = "$"

                                    elif "JPY" in s:
                                        entry["spread"] = round(raw_spread * 100, 1)

                                        entry["spreadUnit"] = "p"

                                    else:
                                        entry["spread"] = round(raw_spread * 10000, 1)

                                        entry["spreadUnit"] = "p"

                                entry["changePct"] = _f("dc")

                                entry["changeDiff"] = _f("dd")

                            elif endpoint == "crypto":
                                entry["price"] = _f("p")

                                entry["volume"] = _f("q") or 0

                                entry["changePct"] = _f("dc")

                                entry["changeDiff"] = _f("dd")

                            elif endpoint == "us":
                                entry["price"] = _f("p")

                                entry["volume"] = msg.get("v")

                                entry["marketStatus"] = msg.get("ms", "unknown")

                            if entry.get("price"):
                                with _live_prices_lock:
                                    _live_prices[disp] = entry

                                # Crypto candles come from Binance — only build WS candles for forex/US

                                _cb = get_candle_builder()
                                if _cb and endpoint != "crypto":
                                    _cb.on_tick(
                                        disp,
                                        entry["price"],
                                        entry.get("volume", 0),
                                        entry.get("ts", 0),
                                    )

                        except Exception as _e:
                            log.debug(f"[WS] msg parse error: {_e}")

            except Exception as e:
                detail = _format_eodhd_ws_disconnect(e)
                log.warning(
                    f"[WS] {endpoint} disconnected: {detail} — reconnecting in {delay:.0f}s"
                )
                await asyncio.sleep(delay)
                delay = min(max_delay, max(5.0, delay * 1.5))

    async def _run_all(self, pairs):

        ticker_info = self._build_ticker_map(pairs)

        tasks = []

        for ep, tickers in [("us", ticker_info["us"]), ("forex", ticker_info["forex"])]:
            if tickers:
                if len(tickers) > 50:
                    log.warning(
                        f"[WS] {ep}: {len(tickers)} symbols (EODHD plans typically cap ~50 "
                        f"per connection; frequent drops often mean over limit or upstream reset)"
                    )
                tasks.append(self._connect(ep, tickers, ticker_info["map"]))

        if tasks:
            await asyncio.gather(*tasks)

    def start(self, pairs):
        """Start the WS manager in a background daemon thread."""

        global _ws_manager_started

        if _ws_manager_started:
            return

        _ws_manager_started = True

        def _run():

            self._loop = asyncio.new_event_loop()

            asyncio.set_event_loop(self._loop)

            # Subscribe WS-capable pairs only (ws:True, default) — capped at 50 tickers per endpoint (EODHD plan limit)
            # us (~23):  AAPL,TSLA,NVDA,MSFT,META,GOOG,NFLX,AMD,COIN,PYPL,INTC,UBER,PLTR
            #            + SPY,IWM,EEM,DIA,GDX,SOXX,SLV,USO + GSPC.INDX,DJI.INDX
            # forex (~49): 21 forex pairs + XAU/USD,XAG/USD,XPT/USD,XPD/USD,WTI Oil,Brent Oil,Nat Gas,Copper,
            #              Aluminium,Lead,Nickel,Zinc,Gasoline,Cattle,Cocoa,Coffee,Corn,Cotton,Soybeans,Sugar,Wheat
            #              + UK100,ASX 200,Nikkei 225,Hang Seng,EURX,JPYX,USDX
            # crypto: handled by BinanceLivePriceWS/BinanceCandleWS — not counted here
            # ws:False pairs use REST cache (H1:55m, H4:3h55m, D1:23h TTL) — scan/backtest/execute unaffected
            # US stock ticks (us endpoint) carry real exchange volume → CandleBuilder builds M1/M5/M15 bars live

            try:
                ws_pairs = list(rt().ALL_PAIRS)
            except RuntimeError:
                ws_pairs = []

            self._loop.run_until_complete(self._run_all(ws_pairs))

        self._thread = threading.Thread(target=_run, daemon=True, name="eodhd-ws")

        self._thread.start()

        # Start REST price poller for non-WS pairs (runs every 60s, daemon)
        threading.Thread(
            target=_run_eodhd_price_poller, daemon=True, name="eodhd-price-poll"
        ).start()

        log.info("[WS] WebSocket manager started")


# ── Candle Builder: WS ticks → H1/H4/D1 OHLCV → SQLite ──────────────

_candle_builder = None


def get_candle_builder():
    return _candle_builder


def set_candle_builder(builder) -> None:
    global _candle_builder
    _candle_builder = builder


class CandleBuilder:
    """EODHD/US tick stream → OHLCV bars; Binance futures kline WS → M1/M5/M15/H1/H4/D1 for crypto.

    Tick path is non-crypto only (see EODHD handler). Kline path uses Binance `t` (UTC open ms)
    with the same bucket alignment as H1/H4/D1.

    US stock ticks carry real exchange volume (msg["v"]) so we accumulate M1/M5/M15 in addition
    to H1/H4/D1. Forex/commodity/index ticks have no central-exchange volume (OTC) so those
    remain H1/H4/D1 only to avoid misleading zero-volume intraday bars."""

    # Forex/commodity/index on_tick: H1/H4/D1 only — no real volume in OTC ticks.
    _TFS_TICK = {"H1": 3600, "H4": 14400, "D1": 86400}
    # US stock on_tick: add M1/M5/M15 since EODHD US WS carries real exchange volume per tick.
    _TFS_TICK_US_STOCK = {"M1": 60, "M5": 300, "M15": 900, "H1": 3600, "H4": 14400, "D1": 86400}
    # Binance on_kline: all futures kline intervals we subscribe to.
    _TFS_KLINE = {"M1": 60, "M5": 300, "M15": 900, "H1": 3600, "H4": 14400, "D1": 86400}

    def __init__(self):

        self._bars = {}  # (display, tf) → {start, o, h, l, c, vol, ticks}

        self._lock = threading.Lock()

        # Keep the high-churn candle cache off the OneDrive-synced repo on Windows.
        self._db = str(ensure_candle_cache_db_ready())

        # Set of display names that are US stocks (populated lazily from ALL_PAIRS).
        self._us_stock_displays: set[str] = set()
        self._us_stock_displays_loaded = False

        self._init_db()

    def _init_db(self):

        with sqlite3.connect(self._db, timeout=15.0) as con:
            con.execute("PRAGMA journal_mode=WAL")

            con.execute("""CREATE TABLE IF NOT EXISTS candle_cache (

                pair TEXT NOT NULL, timeframe TEXT NOT NULL, bar_time TEXT NOT NULL,

                open REAL NOT NULL, high REAL NOT NULL, low REAL NOT NULL, close REAL NOT NULL,

                volume REAL DEFAULT 0, tick_count INTEGER DEFAULT 0,

                PRIMARY KEY (pair, timeframe, bar_time))""")

            con.execute(
                "CREATE INDEX IF NOT EXISTS idx_cc_lookup ON candle_cache(pair, timeframe)"
            )

    @staticmethod
    def _bar_start(ts_s, tf_sec):
        """Align timestamp to bar boundary (UTC)."""

        return datetime.fromtimestamp(int(ts_s // tf_sec) * tf_sec, tz=timezone.utc)

    def _get_us_stock_displays(self) -> set[str]:
        """Lazily load the set of US stock display names from ALL_PAIRS."""
        if self._us_stock_displays_loaded:
            return self._us_stock_displays
        try:
            from athena_runtime import rt
            pairs = rt().ALL_PAIRS
            self._us_stock_displays = {
                p["display"] for p in pairs
                if p.get("type") == "stock" and str(p.get("symbol", "")).endswith(".US")
            }
            self._us_stock_displays_loaded = True
        except Exception:
            pass
        return self._us_stock_displays

    def on_tick(self, display, price, volume, ts_ms):
        """Process a tick: update in-progress bars, flush completed ones to DB.

        US stock ticks use _TFS_TICK_US_STOCK (M1/M5/M15/H1/H4/D1) because EODHD
        US endpoint carries real exchange volume per tick.
        Forex/commodity/index ticks use _TFS_TICK (H1/H4/D1 only) — no OTC volume.
        """

        if not price or price <= 0:
            return

        ts_s = (
            ts_ms / 1000.0
            if ts_ms > 1e12
            else float(ts_ms)
            if ts_ms > 0
            else time.time()
        )

        vol = volume or 0

        tfs = self._TFS_TICK_US_STOCK if display in self._get_us_stock_displays() else self._TFS_TICK

        with self._lock:
            for tf, tf_sec in tfs.items():
                key = (display, tf)

                start = self._bar_start(ts_s, tf_sec)

                bar = self._bars.get(key)

                if bar and bar["start"] != start:
                    self._flush(display, tf, bar)

                    bar = None

                if bar is None:
                    self._bars[key] = {
                        "start": start,
                        "o": price,
                        "h": price,
                        "l": price,
                        "c": price,
                        "vol": vol,
                        "ticks": 1,
                    }

                else:
                    bar["h"] = max(bar["h"], price)

                    bar["l"] = min(bar["l"], price)

                    bar["c"] = price

                    bar["vol"] += vol

                    bar["ticks"] += 1

    def on_kline(self, display, tf, o, h, l, c, vol, ts_ms, is_closed):
        """Apply Binance futures kline snapshot for crypto M1/M5/M15/H1/H4/D1.

        Kline fields are the running bar OHLCV; overwrite each update. Flush when is_closed.
        tick_count=0 marks DB rows as kline-sourced (vs on_tick accumulation).
        """

        if not c or c <= 0 or not o or o <= 0:
            return

        tf = (tf or "").upper()
        tf_sec = self._TFS_KLINE.get(tf)
        if not tf_sec:
            return

        ts_s = (
            ts_ms / 1000.0
            if ts_ms > 1e12
            else float(ts_ms)
            if ts_ms > 0
            else time.time()
        )

        key = (display, tf)

        with self._lock:
            start = self._bar_start(ts_s, tf_sec)

            bar = self._bars.get(key)

            if bar and bar["start"] != start:
                self._flush(display, tf, bar)

            self._bars[key] = {
                "start": start,
                "o": float(o),
                "h": float(h),
                "l": float(l),
                "c": float(c),
                "vol": float(vol or 0),
                "ticks": 0,
            }

            if is_closed:
                self._flush(display, tf, self._bars[key])
                del self._bars[key]

    def _flush(self, pair, tf, bar):
        """Write a completed bar to SQLite."""

        try:
            t = bar["start"].strftime("%Y-%m-%d %H:%M:%S")

            with sqlite3.connect(self._db, timeout=15.0) as con:
                con.execute(
                    "INSERT OR REPLACE INTO candle_cache "
                    "(pair,timeframe,bar_time,open,high,low,close,volume,tick_count) "
                    "VALUES(?,?,?,?,?,?,?,?,?)",
                    (
                        pair,
                        tf,
                        t,
                        bar["o"],
                        bar["h"],
                        bar["l"],
                        bar["c"],
                        bar["vol"],
                        bar["ticks"],
                    ),
                )

        except Exception as e:
            log.error(f"[CB] flush {pair} {tf}: {e}")

    def get_candles(self, display, tf, limit=500):
        """Return completed candles from DB + current in-progress bar."""

        try:
            with sqlite3.connect(self._db, timeout=15.0) as con:
                con.row_factory = sqlite3.Row

                rows = con.execute(
                    "SELECT bar_time,open,high,low,close,volume FROM candle_cache "
                    "WHERE pair=? AND timeframe=? ORDER BY bar_time DESC LIMIT ?",
                    (display, tf, limit),
                ).fetchall()

            candles = [
                {
                    "time": r["bar_time"],
                    "open": r["open"],
                    "high": r["high"],
                    "low": r["low"],
                    "close": r["close"],
                    "vol": r["volume"],
                }
                for r in reversed(rows)
            ]

            with self._lock:
                bar = self._bars.get((display, tf))

                if bar:
                    candles.append(
                        {
                            "time": bar["start"].strftime("%Y-%m-%d %H:%M:%S"),
                            "open": bar["o"],
                            "high": bar["h"],
                            "low": bar["l"],
                            "close": bar["c"],
                            "vol": bar["vol"],
                        }
                    )

            return candles if candles else None

        except Exception as e:
            log.error(f"[CB] get {display} {tf}: {e}")

            return None

    def seed(self, pairs):
        """Seed candle_cache with EODHD historical data (D1 EOD + H1 intraday → H4 resample)."""

        time.sleep(3)

        _key = os.environ.get("EODHD_KEY", "")

        if not _key:
            log.warning("[CB] No EODHD_KEY, skip seed")

            return

        log.info(f"[CB] Seeding candle cache for {len(pairs)} pairs...")

        seeded = 0

        for p in pairs:
            try:
                # Skip disabled pairs — no point seeding data we won't scan

                if not p.get("enabled", True):
                    continue

                # Crypto: seed from Binance REST klines instead of EODHD
                if p.get("type") == "crypto":
                    self._seed_crypto(p)
                    seeded += 1
                    continue

                ticker = rt().eodhd_ticker_for_pair(p)

                if not ticker:
                    continue

                disp = p["display"]

                with sqlite3.connect(self._db, timeout=15.0) as con:
                    cnt = con.execute(
                        "SELECT COUNT(*) FROM candle_cache WHERE pair=? AND timeframe='H1'",
                        (disp,),
                    ).fetchone()[0]

                if cnt >= 100:
                    continue

                d1_n = 0

                # D1 from EOD historical (365 days)

                api = _get_eodhd_client()

                if api:
                    d1_start = (
                        datetime.now(timezone.utc) - timedelta(days=365)
                    ).strftime("%Y-%m-%d")

                    try:
                        d1_bars = api.get_eod_historical_stock_market_data(
                            ticker, period="d", from_date=d1_start, order="a"
                        )

                    except Exception:
                        d1_bars = None

                    if d1_bars and isinstance(d1_bars, list):
                        d1_rows = [
                            (
                                disp,
                                "D1",
                                b["date"],
                                float(b["open"]),
                                float(b["high"]),
                                float(b["low"]),
                                float(b["close"]),
                                float(b.get("volume") or 0),
                                0,
                            )
                            for b in d1_bars
                            if b.get("open") is not None
                        ]

                        if d1_rows:
                            with sqlite3.connect(self._db, timeout=15.0) as con:
                                con.executemany(
                                    "INSERT OR IGNORE INTO candle_cache "
                                    "(pair,timeframe,bar_time,open,high,low,close,volume,tick_count) "
                                    "VALUES(?,?,?,?,?,?,?,?,?)",
                                    d1_rows,
                                )

                            d1_n = len(d1_rows)

                # H1 from EODHD library intraday historical (180 days / 6 months)

                from_ts = int(
                    (datetime.now(timezone.utc) - timedelta(days=180)).timestamp()
                )

                bars = None

                if api:
                    try:
                        bars = api.get_intraday_historical_data(
                            ticker, interval="1h", from_unix_time=from_ts
                        )

                    except Exception:
                        bars = None

                if not bars or not isinstance(bars, list):
                    if d1_n:
                        seeded += 1

                        log.info(f"[CB] {disp}: {d1_n} D1 (no intraday)")

                    continue

                h1_rows = [
                    (
                        disp,
                        "H1",
                        b["datetime"],
                        float(b["open"]),
                        float(b["high"]),
                        float(b["low"]),
                        float(b["close"]),
                        float(b.get("volume") or 0),
                        0,
                    )
                    for b in bars
                    if b.get("open") is not None and b.get("close") is not None
                ]

                # Resample H1 → H4

                import pandas as pd

                df = pd.DataFrame(
                    [
                        {
                            "time": b["datetime"],
                            "open": float(b["open"]),
                            "high": float(b["high"]),
                            "low": float(b["low"]),
                            "close": float(b["close"]),
                            "vol": float(b.get("volume") or 0),
                        }
                        for b in bars
                        if b.get("open") is not None and b.get("close") is not None
                    ]
                )

                h4_rows = []

                if len(df) >= 4:
                    df["time"] = pd.to_datetime(df["time"], utc=True)

                    df = df.set_index("time")

                    offset_hours = (
                        forex_h4_resample_offset_hours()
                        if p.get("type") == "forex"
                        else 0.0
                    )
                    if abs(offset_hours) > 1e-9:
                        offset = pd.to_timedelta(offset_hours, unit="h")
                        df.index = df.index - offset
                    else:
                        offset = None

                    h4 = (
                        df.resample("4h", origin="epoch", label="left", closed="left")
                        .agg(
                            {
                                "open": "first",
                                "high": "max",
                                "low": "min",
                                "close": "last",
                                "vol": "sum",
                            }
                        )
                        .dropna(subset=["open", "close"])
                    )

                    if offset is not None:
                        h4.index = h4.index + offset

                    h4_rows = [
                        (
                            disp,
                            "H4",
                            str(idx),
                            float(row["open"]),
                            float(row["high"]),
                            float(row["low"]),
                            float(row["close"]),
                            float(row["vol"]),
                            0,
                        )
                        for idx, row in h4.iterrows()
                    ]

                all_rows = h1_rows + h4_rows

                if all_rows:
                    with sqlite3.connect(self._db, timeout=15.0) as con:
                        con.executemany(
                            "INSERT OR IGNORE INTO candle_cache "
                            "(pair,timeframe,bar_time,open,high,low,close,volume,tick_count) "
                            "VALUES(?,?,?,?,?,?,?,?,?)",
                            all_rows,
                        )

                seeded += 1

                log.info(
                    f"[CB] {disp}: {d1_n} D1, {len(h1_rows)} H1, {len(h4_rows)} H4"
                )

                time.sleep(0.5)

            except Exception as e:
                log.warning(f"[CB] Seed {p['display']}: {e}")

        log.info(f"[CB] Seed complete: {seeded}/{len(pairs)} pairs")

    def _seed_crypto(self, pair: dict):
        """Seed candle_cache M5/M15/H1/H4/D1 for one crypto pair from Binance futures REST."""
        disp = pair["display"]
        bn_sym = pair["symbol"].replace("/", "")

        with sqlite3.connect(self._db, timeout=15.0) as con:
            deleted = con.execute(
                "DELETE FROM candle_cache "
                "WHERE pair=? AND timeframe IN ('M5','M15','H1','H4','D1') AND tick_count > 0",
                (disp,),
            ).rowcount
            if deleted:
                log.info(
                    f"[CB] {disp}: purged {deleted} WS-corrupted rows flagged with tick_count>0 before re-seed"
                )
            counts = {
                tf: con.execute(
                    "SELECT COUNT(*) FROM candle_cache WHERE pair=? AND timeframe=?",
                    (disp, tf),
                ).fetchone()[0]
                for tf in ("M5", "M15", "H1", "H4", "D1")
            }
            if (
                counts.get("H1", 0) >= 100
                and (counts.get("H4", 0) < 100 or counts.get("D1", 0) < 100)
            ):
                migrated = con.execute(
                    "DELETE FROM candle_cache "
                    "WHERE pair=? AND timeframe IN ('M5','M15','H1','H4','D1')",
                    (disp,),
                ).rowcount
                if migrated:
                    log.info(
                        f"[CB] {disp}: cleared {migrated} crypto seed rows for futures M5/M15/H1/H4/D1 migration"
                    )
                counts = {"M5": 0, "M15": 0, "H1": 0, "H4": 0, "D1": 0}
        if all(counts.get(tf, 0) >= 100 for tf in ("M5", "M15", "H1", "H4", "D1")):
            return

        for tf_label, interval, limit in [
            ("M5", "5m", 1000),
            ("M15", "15m", 1000),
            ("H1", "1h", 1000),
            ("H4", "4h", 1000),
            ("D1", "1d", 1000),
        ]:
            try:
                if counts.get(tf_label, 0) >= 100:
                    continue
                resp = http_requests.get(
                    "https://fapi.binance.com/fapi/v1/klines",
                    params={"symbol": bn_sym, "interval": interval, "limit": limit},
                    timeout=15,
                )
                if resp.status_code != 200:
                    log.warning(
                        f"[CB] Crypto seed {disp} {tf_label}: HTTP {resp.status_code}"
                    )
                    continue
                data = resp.json()
                rows = [
                    (
                        disp,
                        tf_label,
                        datetime.fromtimestamp(k[0] / 1000, tz=timezone.utc).strftime(
                            "%Y-%m-%d %H:%M:%S"
                        ),
                        float(k[1]),
                        float(k[2]),
                        float(k[3]),
                        float(k[4]),
                        float(k[5]),
                        0,
                    )
                    for k in data
                ]
                if rows:
                    with sqlite3.connect(self._db, timeout=15.0) as con:
                        con.executemany(
                            "INSERT OR IGNORE INTO candle_cache "
                            "(pair,timeframe,bar_time,open,high,low,close,volume,tick_count) "
                            "VALUES(?,?,?,?,?,?,?,?,?)",
                            rows,
                        )
                    log.info(f"[CB] {disp}: seeded {len(rows)} {tf_label} from Binance REST")
            except Exception as e:
                log.warning(f"[CB] Crypto seed {disp} {tf_label}: {e}")
            time.sleep(0.3)

    def bulk_update_d1(self):
        """Use EODHD Bulk EOD API to update D1 candles for all active pairs in ~5 API calls.

        One call per exchange (US, FOREX, CC, JSE, INDX) instead of individual calls."""

        _key = os.environ.get("EODHD_KEY", "")

        if not _key:
            return

        # Build mapping: exchange → {bulk_code → display_name} (skip crypto — Binance owns that)

        exchange_map = {}

        try:
            _all_pairs_bulk = rt().ALL_PAIRS
        except RuntimeError:
            _all_pairs_bulk = []
        for p in _all_pairs_bulk:
            if not p.get("enabled", True):
                continue

            if p.get("type") == "crypto":
                continue

            ticker = rt().eodhd_ticker_for_pair(p)

            if not ticker or "." not in ticker:
                continue

            code, exch = ticker.rsplit(".", 1)

            if exch not in exchange_map:
                exchange_map[exch] = {}

            exchange_map[exch][code] = p["display"]

        updated = 0
        _skip_bulk_exch = bulk_d1_skip_exchanges()

        for exch, code_map in exchange_map.items():
            if str(exch).strip().upper() in _skip_bulk_exch:
                log.debug(
                    "[CB] Bulk D1 %s: skipped (not supported by EODHD bulk last-day; D1 via per-symbol EOD)",
                    exch,
                )
                continue
            try:
                symbols_csv = ",".join(code_map.keys())

                r = http_requests.get(
                    f"https://eodhd.com/api/eod-bulk-last-day/{exch}",
                    params={"api_token": _key, "fmt": "json", "symbols": symbols_csv},
                    timeout=30,
                )

                if r.status_code != 200:
                    log.warning(f"[CB] Bulk D1 {exch}: HTTP {r.status_code}")

                    continue

                bars = r.json()

                if not bars or not isinstance(bars, list):
                    continue

                rows = []

                def _sf(v):
                    """Safe float — handles 'NA' and None from Bulk API."""

                    if v is None or v == "NA":
                        return None

                    try:
                        return float(v)

                    except (ValueError, TypeError):
                        return None

                for b in bars:
                    code = b.get("code", "")

                    disp = code_map.get(code)

                    if not disp or not b.get("date"):
                        continue

                    o, h, lo, c = (
                        _sf(b.get("open")),
                        _sf(b.get("high")),
                        _sf(b.get("low")),
                        _sf(b.get("close")),
                    )

                    if o is None or c is None:
                        continue

                    rows.append(
                        (
                            disp,
                            "D1",
                            b["date"],
                            o,
                            h,
                            lo,
                            c,
                            _sf(b.get("volume")) or 0,
                            0,
                        )
                    )

                if rows:
                    with sqlite3.connect(self._db, timeout=15.0) as con:
                        con.executemany(
                            "INSERT OR REPLACE INTO candle_cache "
                            "(pair,timeframe,bar_time,open,high,low,close,volume,tick_count) "
                            "VALUES(?,?,?,?,?,?,?,?,?)",
                            rows,
                        )

                    updated += len(rows)

                log.info(f"[CB] Bulk D1 {exch}: {len(rows)} bars")

            except Exception as e:
                log.warning(f"[CB] Bulk D1 {exch}: {e}")

        log.info(
            f"[CB] Bulk D1 update: {updated} total bars across {len(exchange_map)} exchanges"
        )

    def start_refresh_loop(self):
        """Background loop: bulk D1 update every 4 hours to catch all market closes."""

        def _loop():

            while True:
                time.sleep(4 * 3600)

                try:
                    self.bulk_update_d1()

                except Exception as e:
                    log.warning(f"[CB] Refresh loop error: {e}")

        threading.Thread(target=_loop, daemon=True, name="candle-refresh").start()

        log.info("[CB] D1 refresh loop started (Bulk API every 4h)")

    def stats(self):
        """Summary of candle cache contents."""

        try:
            with sqlite3.connect(self._db, timeout=15.0) as con:
                rows = con.execute(
                    "SELECT pair, timeframe, COUNT(*), MIN(bar_time), MAX(bar_time) "
                    "FROM candle_cache GROUP BY pair, timeframe "
                    "ORDER BY pair, timeframe"
                ).fetchall()

            return [
                {"pair": r[0], "tf": r[1], "bars": r[2], "oldest": r[3], "newest": r[4]}
                for r in rows
            ]

        except Exception as e:
            log.error(f"[CB] stats: {e}")

            return []


def fetch_candles_live(display, tf, limit=500):
    """Fetch candles from the candle_cache (WS-built + historical seed).

    Returns dict with standardized error format."""

    _cb = get_candle_builder()
    if _cb:
        candles = _cb.get_candles(display, tf, limit)

        if candles:
            return {"error": False, "symbol": display, "detail": "", "candles": candles}

        else:
            return {"error": True, "symbol": display, "detail": "No candles available"}

    return {
        "error": True,
        "symbol": display,
        "detail": "Candle builder not initialized",
    }
