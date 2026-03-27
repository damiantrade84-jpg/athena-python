"""In-memory candle TTL cache, normalization helpers, and fetch routing.

Extracted from athena.py so scan/backtest paths share one implementation without
pulling in WebSocket / CandleBuilder classes (those stay on the monolith).
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Any, Callable

from config import CONFIG

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


def forex_h4_resample_offset_hours() -> float:
    """Project-configured H4 bucket offset for forex feeds."""
    try:
        return float(CONFIG.get("FOREX_H4_RESAMPLE_OFFSET_HOURS", 0.0) or 0.0)
    except (TypeError, ValueError):
        return 0.0


def resample_from_h1(
    h1_candles: list[dict] | None,
    target_tf: str,
    limit: int,
    *,
    alignment_offset_hours: float = 0.0,
) -> list[dict] | None:
    """Build H4/D1 candles from a canonical H1 series.

    This is used for forex chart consistency (Vision/screenshots) so H1/H4/D1
    come from one timeline instead of mixed provider paths.
    """
    if not h1_candles:
        return None

    tf = (target_tf or "").upper()
    if tf == "H1":
        out = list(h1_candles)
        return out[-limit:] if len(out) > limit else out

    freq = {"H4": "4h", "D1": "1D"}.get(tf)
    if not freq:
        return None

    try:
        import pandas as pd
    except Exception:
        return None

    rows = []
    for c in h1_candles:
        ts = c.get("time", c.get("datetime"))
        if ts is None:
            continue
        try:
            o = float(c.get("open"))
            h = float(c.get("high"))
            l = float(c.get("low"))
            cl = float(c.get("close"))
        except (TypeError, ValueError):
            continue
        rows.append(
            {
                "time": ts,
                "open": o,
                "high": h,
                "low": l,
                "close": cl,
                "vol": float(c.get("vol", c.get("volume", 0)) or 0),
            }
        )

    if not rows:
        return None

    df = pd.DataFrame(rows)
    df["time"] = pd.to_datetime(df["time"], utc=True, errors="coerce")
    df = df.dropna(subset=["time"]).sort_values("time").drop_duplicates(subset=["time"])
    if df.empty:
        return None
    df = df.set_index("time")

    resample_df = df
    offset_hours = 0.0
    try:
        offset_hours = float(alignment_offset_hours or 0.0)
    except (TypeError, ValueError):
        offset_hours = 0.0

    if tf == "H4" and abs(offset_hours) > 1e-9:
        offset = pd.to_timedelta(offset_hours, unit="h")
        resample_df = df.copy()
        # Shift to epoch boundaries, resample, then shift labels back to the broker/session grid.
        resample_df.index = resample_df.index - offset
    else:
        offset = None

    # Epoch-anchored left-closed buckets matching open-time bar labelling.
    # H4 can optionally be shifted to match broker/session-aligned forex grids (e.g. 01/05/09... UTC).
    agg = (
        resample_df.resample(freq, origin="epoch", label="left", closed="left")
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
        agg.index = agg.index + offset

    if agg.empty:
        return None

    out = [
        {
            "time": idx.isoformat(),
            "open": float(r["open"]),
            "high": float(r["high"]),
            "low": float(r["low"]),
            "close": float(r["close"]),
            "vol": float(r["vol"]),
        }
        for idx, r in agg.iterrows()
    ]
    return out[-limit:] if len(out) > limit else out


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
    fetch_mt5: Callable[..., Any] = None,
    yfinance_symbol_for_pair: Callable[[dict], str | None] = None,
    tf_b: dict[str, str],
) -> list | None:
    tf = tf.upper()
    """Route candle fetch to correct source with in-memory TTL cache.

    Caches candle lists per (symbol, tf) so repeated scans within the same bar
    window reuse data instead of hammering the REST API on every scan.

    TTL: H1=55 min, H4=3h55m, D1=23h — expires just before the next bar closes.
    """
    # Try CandleBuilder first only for crypto H1 live candles.
    # Non-crypto pairs should route directly to their configured source (MT5, etc.).
    use_candle_builder = tf == "H1" and pair.get("type") == "crypto"

    if use_candle_builder:
        live_resp = fetch_candles_live(pair.get("display", ""), tf, limit)
        live_candles = extract_candles(live_resp)

        _min_live_bars = {"H1": 20, "H4": 50, "D1": 50}.get(tf, limit)

        if live_candles and len(live_candles) >= min(limit, _min_live_bars):
            return live_candles[-limit:] if len(live_candles) > limit else live_candles

    # Include limit in key so chart (e.g. 1000 bars for EMA200) does not share TTL
    # entry with scans using a smaller limit.
    key = (pair.get("symbol", pair.get("display")), tf, int(limit))

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

    elif pair["source"] == "mt5" and fetch_mt5:
        candles = fetch_mt5(pair, tf, limit)

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
