"""In-memory candle TTL cache, normalization helpers, and fetch routing.

Extracted from athena.py so scan/backtest paths share one implementation without
pulling in WebSocket / CandleBuilder classes (those stay on the monolith).
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Any, Callable

log = logging.getLogger("sentinel")

_candle_cache: dict = {}
_candle_cache_lock = threading.Lock()

_CANDLE_CACHE_TTL = {"H1": 55 * 60, "H4": 235 * 60, "D1": 23 * 3600}


def extract_candles(resp) -> list | None:
    """Normalize candle fetch results.

    Some fetchers return a raw list[dict]. Others return a dict with `candles`
    plus standardized error metadata.
    """
    if resp is None:
        return None
    if isinstance(resp, list):
        return resp if resp else None
    if isinstance(resp, dict):
        candles = resp.get("candles")
        return candles if isinstance(candles, list) and candles else None
    return None


def candle_time_epoch_utc(val) -> int | None:
    """Normalize candle timestamp to UTC unix seconds (for bar alignment compare)."""
    from datetime import datetime, timezone

    if val is None or val == "":
        return None
    if isinstance(val, (int, float)):
        x = float(val)
        if x > 1e12:
            return int(x / 1000.0)
        return int(x)
    s = str(val).strip()
    if " " in s and "T" not in s:
        s = s.replace(" ", "T", 1)
    try:
        if s.endswith("Z"):
            dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
        else:
            dt = datetime.fromisoformat(s)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
        return int(dt.timestamp())
    except ValueError:
        return None


def merge_forex_forming_ws(
    candles: list,
    display: str,
    tf: str,
    limit: int,
    *,
    get_candle_builder: Callable[[], Any | None],
) -> tuple[list, str | None]:
    """Blend CandleBuilder WS OHLC into last EODHD bar or append newer WS bar.

    Returns (candles, note) where note is 'replaced', 'appended', or None.
    """
    _candle_builder = get_candle_builder()

    if not _candle_builder or not display or not candles:
        return candles, None

    try:
        live = _candle_builder.get_candles(display, tf, min(8, max(3, len(candles))))
    except Exception:
        return candles, None

    if not live:
        return candles, None

    ws_last = live[-1]
    rest_last = candles[-1]

    k_rest = candle_time_epoch_utc(
        rest_last.get("time")
        if rest_last.get("time") is not None
        else rest_last.get("datetime")
    )
    k_ws = candle_time_epoch_utc(ws_last.get("time"))
    if k_rest is None or k_ws is None:
        return candles, None

    def _flt(d: dict, *keys: str, default: float = 0.0) -> float:
        for k in keys:
            if k in d and d[k] is not None:
                try:
                    return float(d[k])
                except (TypeError, ValueError):
                    continue
        return default

    out = list(candles)

    if k_ws == k_rest:
        ro = _flt(rest_last, "open")
        rh, rl = _flt(rest_last, "high"), _flt(rest_last, "low")
        wh, wl, wc = _flt(ws_last, "high"), _flt(ws_last, "low"), _flt(ws_last, "close")
        o = ro
        c = wc
        h = max(o, c, rh, wh)
        l = min(o, c, rl, wl)
        nl = dict(rest_last)
        nl["open"] = o
        nl["high"] = h
        nl["low"] = l
        nl["close"] = c
        wv = _flt(ws_last, "vol", "volume")
        rv = _flt(rest_last, "vol", "volume")
        nl["vol"] = wv if wv else rv
        out[-1] = nl
        return out, "replaced"

    if k_ws > k_rest:
        out.append(
            {
                "time": ws_last.get("time"),
                "open": _flt(ws_last, "open"),
                "high": _flt(ws_last, "high"),
                "low": _flt(ws_last, "low"),
                "close": _flt(ws_last, "close"),
                "vol": _flt(ws_last, "vol", "volume"),
            }
        )
        if len(out) > limit:
            out = out[-limit:]
        return out, "appended"

    return candles, None


def fetch_candles(
    pair: dict,
    tf: str,
    limit: int,
    *,
    fetch_candles_live: Callable[..., Any],
    fetch_binance: Callable[..., Any],
    fetch_eodhd: Callable[..., Any],
    fetch_polygon: Callable[..., Any],
    fetch_yfinance: Callable[..., Any],
    yfinance_symbol_for_pair: Callable[[dict], str | None],
    tf_b: dict[str, str],
) -> list | None:
    """Route candle fetch to correct source with in-memory TTL cache.

    Caches candle lists per (symbol, tf) so repeated scans within the same bar
    window reuse data instead of hammering the REST API on every scan.

    TTL: H1=55 min, H4=3h55m, D1=23h — expires just before the next bar closes.
    """
    if pair.get("type") != "crypto":
        live_resp = fetch_candles_live(pair.get("display", ""), tf, limit)
        live_candles = extract_candles(live_resp)

        _min_live_bars = {"D1": 220, "H4": 50, "H1": 50}.get(tf, limit)

        if live_candles and len(live_candles) >= min(limit, _min_live_bars):
            return live_candles[-limit:] if len(live_candles) > limit else live_candles

    key = (pair.get("symbol", pair.get("display")), tf)

    now = time.time()

    with _candle_cache_lock:
        entry = _candle_cache.get(key)

        if entry is not None:
            candles, expiry = entry

            if now < expiry:
                return candles

    if pair["source"] == "binance":
        candles = fetch_binance(pair["symbol"], tf_b[tf], limit)

    elif pair["source"] == "eodhd":
        candles = fetch_eodhd(pair, tf, limit)

    elif pair["source"] == "polygon":
        candles = fetch_polygon(pair, tf, limit)

    elif pair["source"] == "yfinance":
        candles = fetch_yfinance(yfinance_symbol_for_pair(pair), tf, limit)

    else:
        candles = None

    if not candles and pair.get("type") != "crypto" and pair.get("source") == "eodhd":
        _yf_sym = yfinance_symbol_for_pair(pair)
        if _yf_sym:
            log.info(
                f"[CANDLE] {pair.get('display')} EODHD failed, trying yfinance ({_yf_sym})"
            )
            candles = fetch_yfinance(_yf_sym, tf, limit)

    candles = extract_candles(candles)

    if candles:
        _bad = sum(1 for c in candles[-10:] if c.get("close", 0) <= 0)
        if _bad > 0:
            log.warning(
                f"[CANDLE] {pair.get('display')} {tf}: {_bad}/10 recent bars have close <= 0 — discarding"
            )
            candles = None

    if candles:
        ttl = _CANDLE_CACHE_TTL.get(tf, 55 * 60)

        with _candle_cache_lock:
            _candle_cache[key] = (candles, now + ttl)

    return candles
