#!/usr/bin/env python3

"""Sentinel Pro v4.0 - Trading Intelligence Engine (Python Edition)"""

# Windows CMD: force unbuffered output so all prints show immediately

import sys

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(line_buffering=True)

if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(line_buffering=True)

import bisect
import os
import sys
import json
import math
import time
import threading
import webbrowser
import logging
import sqlite3
import signal as _signal

# Load .env BEFORE importing any module that reads env vars at import time
# (telegram_notify starts a background thread on import that calls _load_config())
try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:
    pass  # dotenv optional — falls back to os.environ

import telegram_notify

from datetime import datetime, timezone, timedelta

from flask import Flask, jsonify, request, send_from_directory

import requests as _requests_mod

# C2: Use requests.Session for connection pooling across all HTTP calls

http_requests = _requests_mod.Session()

from eodhd import APIClient as _EODHDClient  # noqa: E402

_eodhd_client = None


def _get_eodhd_client():

    global _eodhd_client

    if _eodhd_client is None:
        _key = os.environ.get("EODHD_KEY", "")

        if _key:
            _eodhd_client = _EODHDClient(_key)

    return _eodhd_client


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)

log = logging.getLogger("sentinel")


# â"€â"€ Binance Funding Rate Cache (Task 3) â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€

_funding_rate_cache: dict = {}  # symbol -> (rate: float, fetched_at: float)

_FUNDING_CACHE_TTL = 300  # 5 minutes


def _fetch_funding_rate(binance_symbol: str) -> dict:
    """Fetch current perpetual funding rate from Binance public API. Cached 5 min.

    Returns dict with standardized error format."""

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


# â"€â"€ Open Interest Cache (crypto) â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€

_oi_cache: dict = {}  # symbol -> {"oi": float, "price": float, "ts": float}

_OI_CACHE_TTL = 300  # 5 minutes


def _fetch_open_interest(binance_symbol: str) -> dict:
    """Fetch open interest + price from Binance Futures public API. Cached 5 min.

    Returns dict with standardized error format."""

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

        # Get current price for divergence calc

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


# â"€â"€ EODHD WebSocket Real-Time Price Manager â"€â"€

import asyncio  # noqa: E402

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
            items = resp.json()
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


def _run_eodhd_price_poller():
    """Background daemon thread: poll EODHD REST prices for non-WS pairs every 21min.
    Optimized for delayed stock data (15-20min delay) and API efficiency."""
    log.info(
        f"[PRICE-POLL] Started — {len(_NON_WS_EODHD)} EODHD non-WS pairs (21min interval for delayed stocks)"
    )
    while True:
        _fetch_eodhd_live_prices(_NON_WS_EODHD)
        # Reset first-run flag after first cycle
        global _PRICE_POLL_FIRST_RUN
        _PRICE_POLL_FIRST_RUN = False
        time.sleep(21 * 60)  # 21 minutes for delayed stock data optimization


class BinanceLivePriceWS:
    """Binance Futures WebSocket manager for live crypto prices using !ticker@arr stream."""

    def __init__(self):
        self._running = True
        self._thread = None

    def _connect_and_stream(self):
        """Connect to Binance Futures WebSocket and stream all market tickers."""
        import websockets
        import json
        import asyncio

        url = "wss://fstream.binance.com/ws/!ticker@arr"

        # Build symbol lookup for faster matching
        crypto_symbols = {
            pair["symbol"].replace("/", ""): pair["display"]
            for pair in CRYPTO_PAIRS
            if pair.get("enabled", True)
        }

        while self._running:
            try:
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)

                async def _stream():
                    async with websockets.connect(url, ping_interval=20) as ws:
                        log.info(
                            "[BINANCE-WS] Connected to fstream.binance.com !ticker@arr"
                        )
                        while self._running:
                            try:
                                data = await asyncio.wait_for(ws.recv(), timeout=30)
                                tickers = json.loads(data)

                                for ticker in tickers:
                                    symbol = ticker.get("s", "")
                                    if symbol in crypto_symbols:
                                        display_name = crypto_symbols[symbol]
                                        price = float(ticker.get("c", 0))

                                        if price > 0:
                                            with _live_prices_lock:
                                                _live_prices[display_name] = {
                                                    "price": price,
                                                    "ts": time.time(),
                                                }

                            except asyncio.TimeoutError:
                                log.warning(
                                    "[BINANCE-WS] Receive timeout, reconnecting..."
                                )
                                break
                            except Exception as e:
                                log.error(f"[BINANCE-WS] Process error: {e}")
                                break

                loop.run_until_complete(_stream())

            except Exception as e:
                log.error(f"[BINANCE-WS] Connection error: {e}")
                if self._running:
                    time.sleep(5)  # Wait before reconnect
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

        while True:
            try:
                async with websockets.connect(url, ping_interval=None) as ws:
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

                            entry = {"ts": msg.get("t", 0)}

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

                                if _candle_builder and endpoint != "crypto":
                                    _candle_builder.on_tick(
                                        disp,
                                        entry["price"],
                                        entry.get("volume", 0),
                                        entry.get("ts", 0),
                                    )

                        except Exception as _e:
                            log.debug(f"[WS] msg parse error: {_e}")

            except Exception as e:
                log.warning(f"[WS] {endpoint} disconnected: {e} â€” reconnecting in 5s")

                await asyncio.sleep(5)

    async def _run_all(self, pairs):

        ticker_info = self._build_ticker_map(pairs)

        tasks = []

        for ep, tickers in [("us", ticker_info["us"]), ("forex", ticker_info["forex"])]:
            if tickers:
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

            # Subscribe WS-capable pairs only (ws:True, default) — capped at 50 tickers (EODHD plan limit)
            # us=17:  COIN,AAPL,PLTR,GOOG,MSFT,NFLX,PYPL,UBER,INTC,AMD + SLV,SPY,EEM,IWM,USO + GSPC.INDX,DJI.INDX
            # forex=19: EURUSD,GBPJPY,AUDUSD,USDJPY,GBPAUD,USDCHF,EURGBP,USDCAD + XAU,XPT,NatGas,WTI,XAG,Brent,XPD
            #           + FTSE.INDX,AXJO.INDX,HSI.INDX,N225.INDX
            # crypto=14: ETH,LINK,XRP,APT,NEAR,DOGE,ADA,SOL,FET,DOT,INJ,BNB,MATIC,SUI
            # Pairs with ws:False use REST cache (H1:55m, H4:3h55m, D1:23h TTL) — scan/backtest/execute unaffected

            ws_pairs = list(ALL_PAIRS)

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


class CandleBuilder:
    """Accumulates WebSocket ticks into H1/H4/D1 candles, persists completed bars to SQLite.

    On startup, seeds the cache with EODHD historical so scans have instant history."""

    _TFS = {"H1": 3600, "H4": 14400, "D1": 86400}

    def __init__(self):

        self._bars = {}  # (display, tf) → {start, o, h, l, c, vol, ticks}

        self._lock = threading.Lock()

        self._db = os.path.join(
            os.path.dirname(os.path.abspath(__file__)), "candle_cache.db"
        )

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

    def on_tick(self, display, price, volume, ts_ms):
        """Process a tick: update in-progress bars, flush completed ones to DB."""

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

        with self._lock:
            for tf, tf_sec in self._TFS.items():
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

                # Crypto candles come from Binance — skip EODHD seed for crypto

                if p.get("type") == "crypto":
                    continue

                ticker = _eodhd_ticker_for_pair(p)

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
                    df["time"] = pd.to_datetime(df["time"])

                    df = df.set_index("time")

                    h4 = pd.DataFrame(
                        {
                            "open": df["open"].resample("4h").first(),
                            "high": df["high"].resample("4h").max(),
                            "low": df["low"].resample("4h").min(),
                            "close": df["close"].resample("4h").last(),
                            "vol": df["vol"].resample("4h").sum(),
                        }
                    ).dropna(subset=["open", "close"])

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

    def bulk_update_d1(self):
        """Use EODHD Bulk EOD API to update D1 candles for all active pairs in ~5 API calls.

        One call per exchange (US, FOREX, CC, JSE, INDX) instead of individual calls."""

        _key = os.environ.get("EODHD_KEY", "")

        if not _key:
            return

        # Build mapping: exchange → {bulk_code → display_name} (skip crypto — Binance owns that)

        exchange_map = {}

        for p in ALL_PAIRS:
            if not p.get("enabled", True):
                continue

            if p.get("type") == "crypto":
                continue

            ticker = _eodhd_ticker_for_pair(p)

            if not ticker or "." not in ticker:
                continue

            code, exch = ticker.rsplit(".", 1)

            if exch not in exchange_map:
                exchange_map[exch] = {}

            exchange_map[exch][code] = p["display"]

        updated = 0

        for exch, code_map in exchange_map.items():
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

    if _candle_builder:
        candles = _candle_builder.get_candles(display, tf, limit)

        if candles:
            return {"error": False, "symbol": display, "detail": "", "candles": candles}

        else:
            return {"error": True, "symbol": display, "detail": "No candles available"}

    return {
        "error": True,
        "symbol": display,
        "detail": "Candle builder not initialized",
    }


# N1: CONFIG loaded from config.py (YAML overrides + validation happen there)

from config import CONFIG  # noqa: E402

# twelvedata_feed imported lazily inside backtest block to avoid startup cost
# (The lazy import inside the try block handles this — no top-level import needed)


# enabled=True  = included in live scan; confluence score is the execution gate
# enabled=False = JSE-only pairs (no trading platform) or instruments with no viable data source
# BT_AUTO_TOGGLE=False ensures backtest NEVER modifies this flag

FOREX_PAIRS = [
    {
        "symbol": "EURUSD=X",
        "type": "forex",
        "display": "EUR/USD",
        "source": "eodhd",
        "enabled": True,
    },  # SQN +0.16 — enabled; score gate filters low-conviction setups
    {
        "symbol": "GBPUSD=X",
        "type": "forex",
        "display": "GBP/USD",
        "source": "eodhd",
        "enabled": True,
    },  # SQN -1.62 (post-fix BT 2026-03-13): 7 trades/730d, WR 14%, OOS:-10 — no edge confirmed # re-enabled for ATR-fix retest
    {
        "symbol": "USDJPY=X",
        "type": "forex",
        "display": "USD/JPY",
        "source": "eodhd",
        "enabled": True,
    },  # SQN -2.33 — enabled; re-evaluate post-formula fix
    {
        "symbol": "AUDUSD=X",
        "type": "forex",
        "display": "AUD/USD",
        "source": "eodhd",
        "enabled": True,
    },  # SQN +1.00, WR 53.3%, IS:+0.45/OOS:+0.88 (2026-03-15 confirmed)
    {
        "symbol": "NZDUSD=X",
        "type": "forex",
        "display": "NZD/USD",
        "source": "eodhd",
        "enabled": True,
    },  # SQN 0.00 — enabled; zero trades was data gap not signal failure
    {
        "symbol": "EURGBP=X",
        "type": "forex",
        "display": "EUR/GBP",
        "source": "eodhd",
        "enabled": True,
    },  # SQN +1.54, WR 59.1%, IS:+1.48/OOS:+1.61 (2026-03-15 confirmed)
    {
        "symbol": "USDCAD=X",
        "type": "forex",
        "display": "USD/CAD",
        "source": "eodhd",
        "enabled": True,
    },  # SQN +0.27
    {
        "symbol": "USDCHF=X",
        "type": "forex",
        "display": "USD/CHF",
        "source": "eodhd",
        "enabled": True,
    },  # v3.1 SQN +0.61, OOS +1.22 ✓
    {
        "symbol": "EURJPY=X",
        "type": "forex",
        "display": "EUR/JPY",
        "source": "eodhd",
        "enabled": True,
    },  # SQN +1.06
    {
        "symbol": "GBPJPY=X",
        "type": "forex",
        "display": "GBP/JPY",
        "source": "eodhd",
        "enabled": True,
    },  # SQN -0.72
    {
        "symbol": "AUDJPY=X",
        "type": "forex",
        "display": "AUD/JPY",
        "source": "eodhd",
        "enabled": True,
    },  # SQN +0.22
    {
        "symbol": "EURAUD=X",
        "type": "forex",
        "display": "EUR/AUD",
        "source": "eodhd",
        "enabled": True,
    },  # SQN -1.43 (old look-ahead bias) — re-evaluate post-formula fix
    {
        "symbol": "GBPAUD=X",
        "type": "forex",
        "display": "GBP/AUD",
        "source": "eodhd",
        "enabled": True,
    },  # SQN +0.91
    {
        "symbol": "USDZAR=X",
        "type": "forex",
        "display": "USD/ZAR",
        "source": "eodhd",
        "enabled": True,
    },  # SQN -0.32
    {
        "symbol": "EURCHF=X",
        "type": "forex",
        "display": "EUR/CHF",
        "source": "eodhd",
        "enabled": True,
    },  # SQN -0.83
    {
        "symbol": "USDMXN=X",
        "type": "forex",
        "display": "USD/MXN",
        "source": "eodhd",
        "enabled": True,
    },  # SQN +0.85, OOS +0.60 ✓
    {
        "symbol": "USDSGD=X",
        "type": "forex",
        "display": "USD/SGD",
        "source": "eodhd",
        "enabled": True,
    },  # SQN +0.43
]

COMMODITY_PAIRS = [
    {
        "symbol": "GC=F",
        "type": "commodity",
        "display": "XAU/USD",
        "source": "eodhd",
        "enabled": True,
    },  # SQN +1.92, WR 64.7%, 17 trades (2026-03-15 ATR-fix confirmed)
    {
        "symbol": "SI=F",
        "type": "commodity",
        "display": "XAG/USD",
        "source": "eodhd",
        "enabled": True,
    },  # SQN -0.07 — DISABLE (borderline, no edge confirmed)
    {
        "symbol": "CL=F",
        "type": "commodity",
        "display": "WTI Oil",
        "source": "eodhd",
        "enabled": True,
    },  # SQN not retested with correct ATR — monitor only
    {
        "symbol": "BZ=F",
        "type": "commodity",
        "display": "Brent Oil",
        "source": "eodhd",
        "enabled": True,
    },  # Pepperstone: SPOTBRENT
    {
        "symbol": "NG.US",
        "type": "commodity",
        "display": "Nat Gas",
        "source": "eodhd",
        "enabled": True,
    },  # EODHD: NG.US; Pepperstone: NATGAS
    {
        "symbol": "HG=F",
        "type": "commodity",
        "display": "Copper",
        "source": "eodhd",
        "enabled": True,
    },  # Pepperstone: COPPER - WebSocket enabled
    {
        "symbol": "PL=F",
        "type": "commodity",
        "display": "XPT/USD",
        "source": "eodhd",
        "enabled": True,
    },  # Pepperstone: XPTUSD
    {
        "symbol": "PA=F",
        "type": "commodity",
        "display": "XPD/USD",
        "source": "eodhd",
        "enabled": True,
    },  # Pepperstone: XPDUSD
]

INDEX_PAIRS = [
    {
        "symbol": "^IXIC",
        "type": "index",
        "display": "NASDAQ-100",
        "source": "eodhd",
        "enabled": True,
        "ws": False,
    },  # NAS100 index via EODHD REST
    {
        "symbol": "^GSPC",
        "type": "index",
        "display": "S&P 500",
        "source": "eodhd",
        "enabled": True,
    },  # SQN +1.23 WR 60.0% (10T) ← WS: us endpoint GSPC.INDX
    {
        "symbol": "^DJI",
        "type": "index",
        "display": "Dow Jones",
        "source": "eodhd",
        "enabled": True,
    },  # SQN +0.61 WR 55.6% (9T) ← WS: us endpoint DJI.INDX
    {
        "symbol": "^GDAXI",
        "type": "index",
        "display": "DAX 40",
        "source": "eodhd",
        "enabled": True,
        "ws": False,
    },  # SQN -1.89 WR 23.1% (13T) Pepperstone: GER40
    {
        "symbol": "^FTSE",
        "type": "index",
        "display": "UK100",
        "source": "eodhd",
        "enabled": True,
    },  # SQN +0.67 WR 50.0% (12T) Pepperstone: UK100 ← WS: forex ep
    {
        "symbol": "^AXJO",
        "type": "index",
        "display": "ASX 200",
        "source": "eodhd",
        "enabled": True,
    },  # SQN +0.12 WR 40.0% (10T) Pepperstone: AUS200 ← WS: forex ep
    {
        "symbol": "^N225",
        "type": "index",
        "display": "Nikkei 225",
        "source": "eodhd",
        "enabled": True,
    },  # SQN -0.39 WR 33.3% (9T) Pepperstone: JPN225 ← WS: forex ep
    {
        "symbol": "^HSI",
        "type": "index",
        "display": "Hang Seng",
        "source": "eodhd",
        "enabled": True,
    },  # SQN -0.38 WR 33.3% (9T) Pepperstone: HK50 ← WS: forex ep
]

US_STOCK_PAIRS = [
    {
        "symbol": "AAPL.US",
        "type": "stock",
        "display": "AAPL",
        "source": "eodhd",
        "enabled": True,
    },  # SQN -0.30 — score gate filters
    {
        "symbol": "TSLA.US",
        "type": "stock",
        "display": "TSLA",
        "source": "eodhd",
        "enabled": True,
    },  # SQN +0.10
    {
        "symbol": "NVDA.US",
        "type": "stock",
        "display": "NVDA",
        "source": "eodhd",
        "enabled": True,
    },  # SQN +1.44 ✓
    {
        "symbol": "MSFT.US",
        "type": "stock",
        "display": "MSFT",
        "source": "eodhd",
        "enabled": True,
    },  # SQN +0.49
    {
        "symbol": "AMZN.US",
        "type": "stock",
        "display": "AMZN",
        "source": "eodhd",
        "enabled": True,
        "ws": False,
    },  # SQN +0.27
    {
        "symbol": "META.US",
        "type": "stock",
        "display": "META",
        "source": "eodhd",
        "enabled": True,
    },  # SQN -0.29
    {
        "symbol": "GOOG.US",
        "type": "stock",
        "display": "GOOG",
        "source": "eodhd",
        "enabled": True,
    },  # SQN +1.61, OOS:+1.01 ✓
    {
        "symbol": "JPM.US",
        "type": "stock",
        "display": "JPM",
        "source": "eodhd",
        "enabled": True,
        "ws": False,
    },  # SQN +0.26
    {
        "symbol": "V.US",
        "type": "stock",
        "display": "V",
        "source": "eodhd",
        "enabled": True,
        "ws": False,
    },  # SQN -1.39
    {
        "symbol": "XOM.US",
        "type": "stock",
        "display": "XOM",
        "source": "eodhd",
        "enabled": True,
        "ws": False,
    },  # SQN -0.03
    {
        "symbol": "NFLX.US",
        "type": "stock",
        "display": "NFLX",
        "source": "eodhd",
        "enabled": True,
    },  # Pepperstone CFD
    {
        "symbol": "AMD.US",
        "type": "stock",
        "display": "AMD",
        "source": "eodhd",
        "enabled": True,
    },  # Pepperstone CFD
    {
        "symbol": "CRM.US",
        "type": "stock",
        "display": "CRM",
        "source": "eodhd",
        "enabled": True,
        "ws": False,
    },  # Pepperstone CFD
    {
        "symbol": "DIS.US",
        "type": "stock",
        "display": "DIS",
        "source": "eodhd",
        "enabled": True,
        "ws": False,
    },  # Pepperstone CFD
    {
        "symbol": "BA.US",
        "type": "stock",
        "display": "BA",
        "source": "eodhd",
        "enabled": True,
        "ws": False,
    },  # Pepperstone CFD
    {
        "symbol": "COIN.US",
        "type": "stock",
        "display": "COIN",
        "source": "eodhd",
        "enabled": True,
    },  # Pepperstone CFD
    {
        "symbol": "PYPL.US",
        "type": "stock",
        "display": "PYPL",
        "source": "eodhd",
        "enabled": True,
    },  # Pepperstone CFD
    {
        "symbol": "INTC.US",
        "type": "stock",
        "display": "INTC",
        "source": "eodhd",
        "enabled": True,
    },  # Pepperstone CFD
    {
        "symbol": "UBER.US",
        "type": "stock",
        "display": "UBER",
        "source": "eodhd",
        "enabled": True,
    },  # Pepperstone CFD
    {
        "symbol": "PLTR.US",
        "type": "stock",
        "display": "PLTR",
        "source": "eodhd",
        "enabled": True,
    },  # Pepperstone CFD
]

ETF_PAIRS = [
    {
        "symbol": "SPY.US",
        "type": "stock",
        "display": "SPY",
        "source": "eodhd",
        "enabled": True,
    },  # SQN +1.03 ✓
    {
        "symbol": "QQQ.US",
        "type": "stock",
        "display": "QQQ",
        "source": "eodhd",
        "enabled": True,
        "ws": False,
    },  # SQN +0.38
    {
        "symbol": "GLD.US",
        "type": "stock",
        "display": "GLD",
        "source": "eodhd",
        "enabled": True,
        "ws": False,
    },  # SQN +2.08, OOS:+2.98 ✓
    {
        "symbol": "TLT.US",
        "type": "stock",
        "display": "TLT",
        "source": "eodhd",
        "enabled": True,
        "ws": False,
    },  # Treasury ETF
    {
        "symbol": "IWM.US",
        "type": "stock",
        "display": "IWM",
        "source": "eodhd",
        "enabled": True,
    },  # Russell 2000 ETF
    {
        "symbol": "EEM.US",
        "type": "stock",
        "display": "EEM",
        "source": "eodhd",
        "enabled": True,
    },  # Emerging Markets ETF
    {
        "symbol": "XLE.US",
        "type": "stock",
        "display": "XLE",
        "source": "eodhd",
        "enabled": True,
        "ws": False,
    },  # Energy Sector ETF
    {
        "symbol": "SLV.US",
        "type": "stock",
        "display": "SLV",
        "source": "eodhd",
        "enabled": True,
    },  # Silver ETF
    {
        "symbol": "USO.US",
        "type": "stock",
        "display": "USO",
        "source": "eodhd",
        "enabled": True,
    },  # Oil ETF
]

JSE_PAIRS = [
    {
        "symbol": "NPN.JO",
        "type": "stock",
        "display": "Naspers",
        "source": "eodhd",
        "enabled": False,
    },  # DISABLED: No JSE trading platform
    {
        "symbol": "SOL.JO",
        "type": "stock",
        "display": "Sasol",
        "source": "eodhd",
        "enabled": False,
    },  # DISABLED: No JSE trading platform
    {
        "symbol": "SBK.JO",
        "type": "stock",
        "display": "Std Bank",
        "source": "eodhd",
        "enabled": False,
    },  # DISABLED: No JSE trading platform
    {
        "symbol": "AGL.JO",
        "type": "stock",
        "display": "Anglo Am",
        "source": "eodhd",
        "enabled": False,
    },  # DISABLED: No JSE trading platform
    {
        "symbol": "MTN.JO",
        "type": "stock",
        "display": "MTN Group",
        "source": "eodhd",
        "enabled": False,
    },  # DISABLED: No JSE trading platform
    {
        "symbol": "SHP.JO",
        "type": "stock",
        "display": "Shoprite",
        "source": "eodhd",
        "enabled": False,
    },  # DISABLED: No JSE trading platform
    {
        "symbol": "CFR.JO",
        "type": "stock",
        "display": "Richemont",
        "source": "eodhd",
        "enabled": False,
    },  # DISABLED: No JSE trading platform
    {
        "symbol": "FSR.JO",
        "type": "stock",
        "display": "FirstRand",
        "source": "eodhd",
        "enabled": False,
    },  # DISABLED: No JSE trading platform
    {
        "symbol": "ABG.JO",
        "type": "stock",
        "display": "Absa",
        "source": "eodhd",
        "enabled": False,
    },  # DISABLED: No JSE trading platform
    {
        "symbol": "CPI.JO",
        "type": "stock",
        "display": "Capitec",
        "source": "eodhd",
        "enabled": False,
    },  # DISABLED: No JSE trading platform
    {
        "symbol": "PRX.JO",
        "type": "stock",
        "display": "Prosus",
        "source": "eodhd",
        "enabled": False,
    },  # DISABLED: No JSE trading platform
    {
        "symbol": "GFI.JO",
        "type": "stock",
        "display": "Gold Fields",
        "source": "eodhd",
        "enabled": False,
    },  # DISABLED: No JSE trading platform
    {
        "symbol": "ANG.JO",
        "type": "stock",
        "display": "AngloGold",
        "source": "eodhd",
        "enabled": False,
    },  # DISABLED: No JSE trading platform
    {
        "symbol": "SSW.JO",
        "type": "stock",
        "display": "Sibanye",
        "source": "eodhd",
        "enabled": False,
    },  # DISABLED: No JSE trading platform
]

CRYPTO_PAIRS = [
    {
        "symbol": "BTCUSDT",
        "type": "crypto",
        "display": "BTC/USDT",
        "source": "binance",
        "enabled": True,
        "ws": False,
    },  # SQN -0.81 Phase A / pre-PhaseA: +0.18 (borderline, re-test) # re-enabled for ATR-fix retest
    {
        "symbol": "ETHUSDT",
        "type": "crypto",
        "display": "ETH/USDT",
        "source": "binance",
        "enabled": True,
    },  # SQN +2.97, WR 64.3%, 28 trades (2026-03-15 ATR-fix confirmed)
    {
        "symbol": "XRPUSDT",
        "type": "crypto",
        "display": "XRP/USDT",
        "source": "binance",
        "enabled": True,
    },  # SQN -0.80 Phase A # re-enabled for ATR-fix retest
    {
        "symbol": "SOLUSDT",
        "type": "crypto",
        "display": "SOL/USDT",
        "source": "binance",
        "enabled": True,
    },  # v3.1 SQN +0.02 (improved from -0.64 but still weak) # re-enabled for ATR-fix retest
    {
        "symbol": "ADAUSDT",
        "type": "crypto",
        "display": "ADA/USDT",
        "source": "binance",
        "enabled": True,
    },  # SQN -0.80 Phase A # re-enabled for ATR-fix retest
    {
        "symbol": "DOGEUSDT",
        "type": "crypto",
        "display": "DOGE/USDT",
        "source": "binance",
        "enabled": True,
    },  # SQN -0.64 Phase A # re-enabled for ATR-fix retest
    {
        "symbol": "AVAXUSDT",
        "type": "crypto",
        "display": "AVAX/USDT",
        "source": "binance",
        "enabled": True,
        "ws": False,
    },  # v3.1 SQN +0.44, OOS +0.02 (overfit) # re-enabled for ATR-fix retest
    {
        "symbol": "LINKUSDT",
        "type": "crypto",
        "display": "LINK/USDT",
        "source": "binance",
        "enabled": True,
    },  # SQN +1.68, WR 69.2%, 13 trades (2026-03-15 confirmed)
    {
        "symbol": "MATICUSDT",
        "type": "crypto",
        "display": "MATIC/USDT",
        "source": "binance",
        "enabled": True,
    },  # SQN +0.26 Phase A (noise) # re-enabled for ATR-fix retest
    {
        "symbol": "BNBUSDT",
        "type": "crypto",
        "display": "BNB/USDT",
        "source": "binance",
        "enabled": True,
    },  # v3.1 SQN -0.55 (negative edge) # re-enabled for ATR-fix retest
    {
        "symbol": "DOTUSDT",
        "type": "crypto",
        "display": "DOT/USDT",
        "source": "binance",
        "enabled": True,
    },  # SQN -0.36 Phase A # re-enabled for ATR-fix retest
    {
        "symbol": "LTCUSDT",
        "type": "crypto",
        "display": "LTC/USDT",
        "source": "binance",
        "enabled": True,
        "ws": False,
    },  # v3.1 SQN +0.97 (improved from -0.16, near threshold)
    {
        "symbol": "SUIUSDT",
        "type": "crypto",
        "display": "SUI/USDT",
        "source": "binance",
        "enabled": True,
    },  # SQN +0.76, IS:+0.43/OOS:+0.82 âœ"
    {
        "symbol": "NEARUSDT",
        "type": "crypto",
        "display": "NEAR/USDT",
        "source": "binance",
        "enabled": True,
    },  # SQN -1.27 # re-enabled for ATR-fix retest
    {
        "symbol": "APTUSDT",
        "type": "crypto",
        "display": "APT/USDT",
        "source": "binance",
        "enabled": True,
    },  # SQN +0.67, IS:+0.56/OOS:+0.37 âœ"
    {
        "symbol": "INJUSDT",
        "type": "crypto",
        "display": "INJ/USDT",
        "source": "binance",
        "enabled": True,
    },  # SQN -2.01 # re-enabled for ATR-fix retest
    {
        "symbol": "FETUSDT",
        "type": "crypto",
        "display": "FET/USDT",
        "source": "binance",
        "enabled": True,
    },  # SQN +0.14 # re-enabled for ATR-fix retest
    {
        "symbol": "RENDERUSDT",
        "type": "crypto",
        "display": "RENDER/USDT",
        "source": "binance",
        "enabled": True,
        "ws": False,
    },  # SQN -2.45 # re-enabled for ATR-fix retest
]

ALL_PAIRS = (
    FOREX_PAIRS
    + COMMODITY_PAIRS
    + INDEX_PAIRS
    + US_STOCK_PAIRS
    + ETF_PAIRS
    + JSE_PAIRS
    + CRYPTO_PAIRS
)

# Pairs that opted out of WS — polled via REST every 60s
_NON_WS_EODHD = [
    p for p in ALL_PAIRS if not p.get("ws", True) and p.get("source") == "eodhd"
]
_NON_WS_CRYPTO = [
    p for p in ALL_PAIRS if not p.get("ws", True) and p.get("source") == "binance"
]


_TOGGLE_STATE_FILE = os.path.join(os.path.dirname(__file__), "toggle_state.json")


def _persist_toggle_state():
    """Save current enabled/disabled state to JSON so it survives restarts."""

    state = {p["display"]: p.get("enabled", True) for p in ALL_PAIRS}

    try:
        with open(_TOGGLE_STATE_FILE, "w") as f:
            json.dump(state, f, indent=2)

        log.info(f"[TOGGLE] Persisted toggle state for {len(state)} pairs")

    except Exception as e:
        log.warning(f"[TOGGLE] Failed to persist: {e}")


def _load_toggle_state():
    """Restore enabled/disabled state from JSON sidecar on startup."""

    global ACTIVE_PAIRS

    if not os.path.exists(_TOGGLE_STATE_FILE):
        return

    try:
        with open(_TOGGLE_STATE_FILE) as f:
            state = json.load(f)

        applied = 0

        for p in ALL_PAIRS:
            if p["display"] in state:
                p["enabled"] = state[p["display"]]

                applied += 1

        ACTIVE_PAIRS = [p for p in ALL_PAIRS if p.get("enabled", True)]

        log.info(
            f"[TOGGLE] Restored toggle state: {applied} pairs, {len(ACTIVE_PAIRS)} active"
        )

    except Exception as e:
        log.warning(f"[TOGGLE] Failed to load: {e}")


ACTIVE_PAIRS = [p for p in ALL_PAIRS if p.get("enabled", True)]

_load_toggle_state()

TF_B = {"D1": "1d", "H4": "4h", "H1": "1h"}

_YF_INTRADAY_PERIOD = "180d"

_VENDOR_SYMBOL_OVERRIDES = {
    # Precious metals: EODHD D1 via .FOREX suffix (confirmed 12,350 bars); Polygon C: for H4/H1
    # EODHD has NO intraday for XAU/XAG — Polygon handles H4/H1 backtest fallback
    "XAU/USD": {"eodhd": "XAUUSD.FOREX", "polygon": "C:XAUUSD"},
    "XAG/USD": {"eodhd": "XAGUSD.FOREX", "polygon": "C:XAGUSD"},
    "XPT/USD": {"polygon": "C:XPTUSD", "eodhd": "XPTUSD.FOREX", "yfinance": "PL=F"},
    "XPD/USD": {"polygon": "C:XPDUSD", "fallback": "yfinance"},
    # Energy / base metals: EODHD D1 plain symbol; yfinance fallback for intraday
    "WTI Oil": {"yfinance": "CL=F", "eodhd": "CL", "fallback": "yfinance"},
    "Brent Oil": {"yfinance": "BZ=F", "eodhd": "BZ", "fallback": "yfinance"},
    "Nat Gas": {"yfinance": "NG=F", "eodhd": "NG", "fallback": "yfinance"},
    "Copper": {"yfinance": "HG=F", "eodhd": "HG", "fallback": "yfinance"},
    # Indices: EODHD plain symbol for D1; eodhd_intraday uses .INDX suffix (confirmed working)
    "UK100": {
        "yfinance": "^FTSE",
        "eodhd": "FTSE",
        "eodhd_intraday": "FTSE.INDX",
        "polygon": "C:UK100",
        "fallback": "yfinance",
    },
    "S&P 500": {
        "yfinance": "^GSPC",
        "eodhd": "GSPC",
        "eodhd_intraday": "GSPC.INDX",
        "fallback": "yfinance",
    },
    "Nasdaq": {
        "yfinance": "^IXIC",
        "eodhd": "IXIC",
        "eodhd_intraday": "IXIC.INDX",
        "fallback": "yfinance",
    },
    "NASDAQ-100": {
        "yfinance": "^IXIC",
        "eodhd": "IXIC",
        "eodhd_intraday": "IXIC.INDX",
        "fallback": "yfinance",
    },
    "Dow Jones": {
        "yfinance": "^DJI",
        "eodhd": "DJI",
        "eodhd_intraday": "DJI.INDX",
        "fallback": "yfinance",
    },
    "DAX 40": {
        "yfinance": "^GDAXI",
        "eodhd": "GDAXI",
        "eodhd_intraday": "GDAXI.INDX",
        "fallback": "yfinance",
    },
    "ASX 200": {
        "yfinance": "^AXJO",
        "eodhd": "AXJO",
        "eodhd_intraday": "AXJO.INDX",
        "fallback": "yfinance",
    },
    "Nikkei 225": {
        "yfinance": "^N225",
        "eodhd": "N225",
        "eodhd_intraday": "N225.INDX",
        "fallback": "yfinance",
    },
    "Hang Seng": {
        "yfinance": "^HSI",
        "eodhd": "HSI",
        "eodhd_intraday": "HSI.INDX",
        "fallback": "yfinance",
    },
}


def _vendor_overrides(pair: dict) -> dict:

    return _VENDOR_SYMBOL_OVERRIDES.get(pair.get("display", ""), {})


def _yfinance_symbol_for_pair(pair: dict) -> str | None:

    override = pair.get("yfinanceSymbol") or _vendor_overrides(pair).get("yfinance")

    return override or pair.get("symbol")


def _eodhd_ticker_for_pair(pair: dict) -> str | None:
    # Check vendor override first — highest priority
    override = _vendor_overrides(pair).get("eodhd")
    if override:
        return override
    # ... rest of existing function unchanged

    disp = pair.get("display", "")

    ptype = pair.get("type", "")

    sym = pair.get("symbol", "")

    if ptype == "crypto":
        base = disp.split("/")[0] if "/" in disp else disp.replace("USDT", "")

        return f"{base}-USD.CC"

    if ptype == "forex":
        return disp.replace("/", "") + ".FOREX"

    if ptype == "commodity":
        if "/" in disp:
            return disp.replace("/", "") + ".FOREX"

        if sym.endswith((".FOREX", ".COMM")):
            return sym

        return None

    if ptype == "stock" and ".JO" in sym:
        return sym.replace(".JO", ".JSE")

    if ptype == "index":
        return sym if ".INDX" in sym else sym.lstrip("^") + ".INDX"

    return sym or None


def _polygon_ticker_for_pair(pair: dict) -> str | None:

    override = pair.get("polygonTicker") or _vendor_overrides(pair).get("polygon")

    if override:
        return override

    disp = pair.get("display", "")

    ptype = pair.get("type", "")

    if ptype in ("forex", "commodity") and "/" in disp:
        return "C:" + disp.replace("/", "")

    sym = pair.get("symbol", "")

    if sym.startswith(("C:", "X:", "I:")):
        return sym

    return None


def _fallback_source_for_pair(pair: dict) -> str | None:

    override = pair.get("fallbackSource") or _vendor_overrides(pair).get("fallback")

    if override:
        return override

    ptype = pair.get("type", "")

    if ptype == "stock":
        return "yfinance"

    if ptype == "index":
        return "yfinance" if _yfinance_symbol_for_pair(pair) else None

    if ptype == "commodity":
        return (
            "polygon"
            if _polygon_ticker_for_pair(pair)
            else ("yfinance" if _yfinance_symbol_for_pair(pair) else None)
        )

    if ptype == "forex":
        return "polygon" if _polygon_ticker_for_pair(pair) else None

    return None


def _fetch_fallback_candles(pair: dict, tf: str, limit: int, reason: str = ""):
    """Try fallback sources in order: Polygon → yfinance.
    Twelvedata re-enabled once upgraded to Grow/Venture plan.
    Returns candle list or None if all sources fail."""

    tag = f" ({reason})" if reason else ""
    disp = pair["display"]

    # 1. Polygon — good for forex/metals, rate-limited to 5 req/min on free tier
    if _polygon_ticker_for_pair(pair):
        resp = fetch_polygon(pair, tf, limit)
        candles = _extract_candles(resp)
        if candles:
            log.info(f"[FALLBACK] {disp} {tf}: using Polygon{tag}")
            return candles

    # 2. yfinance — last resort, broad coverage but lower reliability
    yf_symbol = _yfinance_symbol_for_pair(pair)
    if yf_symbol:
        log.info(f"[FALLBACK] {disp} {tf}: using yfinance{tag}")
        return fetch_yfinance(yf_symbol, tf, limit)

    return None


def _atr_for_levels(
    d1i: dict, h4i: dict, h1i: dict, pair: dict = None, style: str | None = None
):
    """
    Returns ATR value for SL/TP calculation — correct timeframe per asset class.

    CRYPTO:              H4 ATR first — entries are H4-based, H1 too tight for
                         overnight crypto gaps and volatile moves
    FOREX:               D1 ATR first — D1 swing trades, normal pullback 40-80
                         pips, H1 ATR (25-30 pips) causes premature stop-outs
    STOCKS/ETFs:         D1 ATR first — stocks gap at open daily, H1 too tight
                         for 5-20 day swing holds
    COMMODITIES:         D1 ATR first — macro-driven, daily gaps common on news
    INDICES:             D1 ATR first — daily range 0.5-1.5%, H1 only 0.1-0.3%
    """
    ptype = (pair or {}).get("type", "")
    resolved_style = _normalize_style(style or "swing")
    if resolved_style == "auto":
        resolved_style = "swing"

    priority_cfg = CONFIG.get("LEVEL_ATR_PRIORITY", {}) or {}
    style_map = priority_cfg.get(ptype, {}) or priority_cfg.get("default", {})
    order = style_map.get(resolved_style)

    if not order:
        if resolved_style == "scalp":
            order = ["H1", "H4", "D1"]
        elif resolved_style == "intraday":
            order = ["H4", "H1", "D1"]
        elif ptype == "crypto":
            order = ["H4", "D1", "H1"]
        else:
            order = ["D1", "H4", "H1"]

    snaps = {
        "D1": (d1i or {}).get("snap", {}),
        "H4": (h4i or {}).get("snap", {}),
        "H1": (h1i or {}).get("snap", {}),
    }
    for tf in order:
        atr_val = (snaps.get(tf) or {}).get("atr")
        if atr_val:
            return atr_val
    return None


def _max_score_for_pair(pair: dict) -> float:
    """Theoretical max score for z-score factor engine.

    final_score is a weighted average of z-scores clamped to [-3, +3], so max is 3.0."""

    return 3.0


def fetch_yfinance(sym, tf, limit):
    """Download OHLCV candles from Yahoo Finance. Returns list of candle dicts or None."""

    try:
        import yfinance as yf

        import pandas as pd

        period = "2y" if tf == "D1" else _YF_INTRADAY_PERIOD

        interval = "1d" if tf == "D1" else "1h"

        df = yf.download(
            sym, period=period, interval=interval, progress=False, auto_adjust=True
        )

        if df is None or df.empty:
            log.warning(f"[YF] {sym}: no data")
            return None

        if isinstance(df.columns, pd.MultiIndex):
            # Determine which level holds OHLCV names (level 0 or level 1)

            _ohlcv = {"Open", "High", "Low", "Close", "Volume"}

            _l0 = set(df.columns.get_level_values(0))

            df.columns = (
                df.columns.get_level_values(0)
                if _ohlcv.intersection(_l0)
                else df.columns.get_level_values(1)
            )

        # Drop duplicate columns (some JSE tickers produce duplicates after MultiIndex flatten)

        df = df.loc[:, ~df.columns.duplicated(keep="first")]

        if tf == "H4":
            # Column-by-column resample â€” avoids pandas version issues with .agg(dict)

            vol_col = (
                df["Volume"]
                if "Volume" in df.columns
                else pd.Series(0.0, index=df.index)
            )

            df = pd.DataFrame(
                {
                    "Open": df["Open"].resample("4h").first(),
                    "High": df["High"].resample("4h").max(),
                    "Low": df["Low"].resample("4h").min(),
                    "Close": df["Close"].resample("4h").last(),
                    "Volume": vol_col.resample("4h").sum(),
                }
            ).dropna(subset=["Open", "Close"])

        df = df.tail(limit)

        return [
            {
                "time": str(idx.date() if tf == "D1" else idx),
                "open": float(r["Open"]),
                "high": float(r["High"]),
                "low": float(r["Low"]),
                "close": float(r["Close"]),
                "vol": float(r.get("Volume", 0)),
            }
            for idx, r in df.iterrows()
        ]

    except Exception as e:
        log.error(f"[YF] {sym}: {e}")
        return None


def fetch_binance(sym, interval, limit):
    """Download OHLCV candles from Binance REST API with failover endpoint."""

    try:
        for base in ["https://api.binance.com", "https://api1.binance.com"]:
            r = http_requests.get(
                f"{base}/api/v3/klines",
                params={"symbol": sym, "interval": interval, "limit": limit},
                timeout=15,
            )

            if r.status_code == 200:
                return [
                    {
                        "time": datetime.fromtimestamp(
                            k[0] / 1000, tz=timezone.utc
                        ).isoformat(),
                        "open": float(k[1]),
                        "high": float(k[2]),
                        "low": float(k[3]),
                        "close": float(k[4]),
                        "vol": float(k[5]),
                    }
                    for k in r.json()
                ]

            log.warning(f"[BN] {sym} HTTP {r.status_code}: {r.text[:120]}")

        return None

    except Exception as e:
        log.error(f"[BN] {sym}: {e}")
        return None


def fetch_binance_paginated(sym, interval, total_bars):
    """Paginate Binance klines to fetch more than 1000 bars. Used by backtest only."""

    all_candles = []

    end_time = None  # None = latest

    pages = (total_bars + 999) // 1000  # ceil division

    for page in range(pages):
        try:
            params = {"symbol": sym, "interval": interval, "limit": 1000}

            if end_time:
                params["endTime"] = end_time

            for base in ["https://api.binance.com", "https://api1.binance.com"]:
                r = http_requests.get(
                    f"{base}/api/v3/klines", params=params, timeout=15
                )

                if r.status_code == 200:
                    data = r.json()

                    if not data:
                        break

                    batch = [
                        {
                            "time": datetime.fromtimestamp(
                                k[0] / 1000, tz=timezone.utc
                            ).isoformat(),
                            "open": float(k[1]),
                            "high": float(k[2]),
                            "low": float(k[3]),
                            "close": float(k[4]),
                            "vol": float(k[5]),
                        }
                        for k in data
                    ]

                    all_candles = batch + all_candles  # prepend older data

                    end_time = data[0][0] - 1  # 1ms before earliest bar

                    break

                log.warning(f"[BN-PAG] {sym} HTTP {r.status_code}: {r.text[:120]}")

            else:
                break  # both endpoints failed

            if page < pages - 1:
                time.sleep(1)

        except Exception as e:
            log.error(f"[BN-PAG] {sym} page {page}: {e}")

            break

    log.info(f"[BN-PAG] {sym} {interval}: {len(all_candles)} bars ({pages} pages)")

    return all_candles if all_candles else None


def _fetch_bt_yfinance(sym, period="730d"):
    """Fetch extended H1 data from yfinance for backtest, return (h4_candles, h1_candles) tuple."""

    try:
        import yfinance as yf

        import pandas as pd

        h1_df = yf.download(
            sym, period=period, interval="1h", progress=False, auto_adjust=True
        )

        if h1_df is None or h1_df.empty:
            log.warning(f"[BT-YF] {sym}: no H1 data")

            return None, None

        if isinstance(h1_df.columns, pd.MultiIndex):
            _ohlcv = {"Open", "High", "Low", "Close", "Volume"}

            _l0 = set(h1_df.columns.get_level_values(0))

            h1_df.columns = (
                h1_df.columns.get_level_values(0)
                if _ohlcv.intersection(_l0)
                else h1_df.columns.get_level_values(1)
            )

        h1_df = h1_df.loc[:, ~h1_df.columns.duplicated(keep="first")]

        vol_col = (
            h1_df["Volume"]
            if "Volume" in h1_df.columns
            else pd.Series(0.0, index=h1_df.index)
        )

        h4_df = pd.DataFrame(
            {
                "Open": h1_df["Open"].resample("4h").first(),
                "High": h1_df["High"].resample("4h").max(),
                "Low": h1_df["Low"].resample("4h").min(),
                "Close": h1_df["Close"].resample("4h").last(),
                "Volume": vol_col.resample("4h").sum(),
            }
        ).dropna(subset=["Open", "Close"])

        h4_candles = [
            {
                "time": str(idx),
                "open": float(r["Open"]),
                "high": float(r["High"]),
                "low": float(r["Low"]),
                "close": float(r["Close"]),
                "vol": float(r["Volume"]),
            }
            for idx, r in h4_df.iterrows()
        ]

        h1_candles = [
            {
                "time": str(idx),
                "open": float(r["Open"]),
                "high": float(r["High"]),
                "low": float(r["Low"]),
                "close": float(r["Close"]),
                "vol": float(r.get("Volume", 0)),
            }
            for idx, r in h1_df.iterrows()
        ]

        log.info(f"[BT-YF] {sym}: {len(h4_candles)} H4 bars, {len(h1_candles)} H1 bars")

        return h4_candles, h1_candles

    except Exception as e:
        log.error(f"[BT-YF] {sym}: {e}")

        return None, None


def _resample_to_h4(h1_candles):
    """Resample H1 candles to H4 format."""
    if not h1_candles:
        return []

    import pandas as pd

    df = pd.DataFrame(h1_candles)
    df["time"] = pd.to_datetime(df["time"])
    df = df.set_index("time")

    h4_df = pd.DataFrame(
        {
            "open": df["open"].resample("4h").first(),
            "high": df["high"].resample("4h").max(),
            "low": df["low"].resample("4h").min(),
            "close": df["close"].resample("4h").last(),
            "vol": df["vol"].resample("4h").sum(),
        }
    ).dropna(subset=["open", "close"])

    h4_candles = [
        {
            "time": str(idx),
            "open": float(r["open"]),
            "high": float(r["high"]),
            "low": float(r["low"]),
            "close": float(r["close"]),
            "vol": float(r["vol"]),
        }
        for idx, r in h4_df.iterrows()
    ]

    return h4_candles


def _eodhd_intraday_ticker_for_pair(pair: dict) -> str | None:
    """Get EODHD intraday ticker for pair, checking eodhd_intraday override first."""
    override = pair.get("eodhdIntradayTicker") or _vendor_overrides(pair).get(
        "eodhd_intraday"
    )
    if override:
        return override
    # For commodities without intraday override, return None to trigger yfinance fallback
    if pair.get("type") == "commodity":
        return None
    # Fall back to regular ticker for other types
    return _eodhd_ticker_for_pair(pair)


def _fetch_eodhd_intraday_bt(pair, days=730):
    """Fetch extended intraday H1 data from EODHD REST API with from/to params for backtest.

    Returns (h4_candles, h1_candles) tuple. Uses raw HTTP, not SDK, to leverage from/to range."""

    try:
        _key = os.environ.get("EODHD_KEY", "")

        if not _key:
            log.warning("[EODHD-BT] No EODHD_KEY set")

            return None, None

        ticker = _eodhd_intraday_ticker_for_pair(pair)

        if not ticker:
            log.warning(f"[EODHD-BT] {pair['display']}: no valid intraday ticker")

            return None, None

        now_ts = int(datetime.now(timezone.utc).timestamp())

        from_ts = int((datetime.now(timezone.utc) - timedelta(days=days)).timestamp())

        url = f"https://eodhd.com/api/intraday/{ticker}"

        params = {
            "api_token": _key,
            "fmt": "json",
            "from": from_ts,
            "to": now_ts,
            "interval": "1h",
        }

        r = http_requests.get(url, params=params, timeout=30)

        if r.status_code != 200:
            log.warning(f"[EODHD-BT] {ticker} HTTP {r.status_code}: {r.text[:120]}")

            return None, None

        bars = r.json()

        if not bars or not isinstance(bars, list):
            log.warning(f"[EODHD-BT] {ticker}: no intraday data")

            return None, None

        h1_candles = [
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

        if len(h1_candles) < 100:
            log.warning(f"[EODHD-BT] {ticker}: only {len(h1_candles)} H1 bars")

            return None, None

        import pandas as pd

        df = pd.DataFrame(h1_candles)

        df["time"] = pd.to_datetime(df["time"])

        df = df.set_index("time")

        h4_df = pd.DataFrame(
            {
                "open": df["open"].resample("4h").first(),
                "high": df["high"].resample("4h").max(),
                "low": df["low"].resample("4h").min(),
                "close": df["close"].resample("4h").last(),
                "vol": df["vol"].resample("4h").sum(),
            }
        ).dropna(subset=["open", "close"])

        h4_candles = [
            {
                "time": str(idx),
                "open": float(r["open"]),
                "high": float(r["high"]),
                "low": float(r["low"]),
                "close": float(r["close"]),
                "vol": float(r["vol"]),
            }
            for idx, r in h4_df.iterrows()
        ]

        log.info(
            f"[EODHD-BT] {ticker}: {len(h4_candles)} H4 bars, {len(h1_candles)} H1 bars ({days}d)"
        )

        if len(h4_candles) < 120:
            log.warning(
                f"[EODHD-BT] {ticker}: only {len(h4_candles)} H4 bars after resample (need 120+), falling back"
            )

            return None, None

        return h4_candles, h1_candles

    except Exception as e:
        log.error(f"[EODHD-BT] {pair['display']}: {e}")

        return None, None


def fetch_eodhd(pair, tf, limit):
    """Download OHLCV candles via EODHD SDK (APIClient). Covers forex, stocks, indices â€” 1000 req/min.

    Returns dict with standardized error format."""

    symbol = pair.get("display", pair.get("symbol", "unknown"))

    try:
        api = _get_eodhd_client()

        if not api:
            log.warning("[EODHD] No EODHD_KEY set")

            return {"error": True, "symbol": symbol, "detail": "No EODHD_KEY set"}

        ticker = _eodhd_ticker_for_pair(pair)

        if not ticker:
            fallback = _fetch_fallback_candles(
                pair, tf, limit, reason="no valid EODHD ticker"
            )

            if fallback:
                return {
                    "error": True,
                    "symbol": symbol,
                    "detail": "no valid EODHD ticker (using fallback)",
                    "candles": fallback,
                }

            return {"error": True, "symbol": symbol, "detail": "no valid EODHD ticker"}

        if tf == "D1":
            start = (datetime.now(timezone.utc) - timedelta(days=730)).strftime(
                "%Y-%m-%d"
            )

            bars = api.get_eod_historical_stock_market_data(
                ticker, period="d", from_date=start, order="a"
            )

            if not bars:
                log.warning(f"[EODHD] {ticker} D1: no data")

                fallback = _fetch_fallback_candles(
                    pair, tf, limit, reason="EODHD daily unavailable"
                )

                if fallback:
                    return {
                        "error": True,
                        "symbol": symbol,
                        "detail": "EODHD daily unavailable (using fallback)",
                        "candles": fallback,
                    }

                return {
                    "error": True,
                    "symbol": symbol,
                    "detail": "EODHD daily unavailable",
                }

            candles = [
                {
                    "time": b["date"],
                    "open": float(b["open"]),
                    "high": float(b["high"]),
                    "low": float(b["low"]),
                    "close": float(b["close"]),
                    "vol": float(b.get("volume") or 0),
                }
                for b in bars
            ]

        else:
            start_ts = int(
                (datetime.now(timezone.utc) - timedelta(days=365)).timestamp()
            )

            bars = api.get_intraday_historical_data(
                ticker, interval="1h", from_unix_time=start_ts
            )

            if not bars:
                fallback = _fetch_fallback_candles(
                    pair, tf, limit, reason="EODHD intraday unavailable"
                )

                if fallback:
                    return {
                        "error": True,
                        "symbol": symbol,
                        "detail": "EODHD intraday unavailable (using fallback)",
                        "candles": fallback,
                    }

                log.warning(f"[EODHD] {ticker} {tf}: no data")

                return {
                    "error": True,
                    "symbol": symbol,
                    "detail": "EODHD intraday unavailable",
                }

            candles = [
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

            if tf == "H4" and len(candles) >= 4:
                import pandas as pd

                df = pd.DataFrame(candles)

                df["time"] = pd.to_datetime(df["time"])

                df = df.set_index("time")

                # Column-by-column resample â€” avoids pandas version issues with .agg(dict)

                df = pd.DataFrame(
                    {
                        "open": df["open"].resample("4h").first(),
                        "high": df["high"].resample("4h").max(),
                        "low": df["low"].resample("4h").min(),
                        "close": df["close"].resample("4h").last(),
                        "vol": df["vol"].resample("4h").sum(),
                    }
                ).dropna()

                candles = [
                    {
                        "time": k.isoformat(),
                        "open": float(v["open"]),
                        "high": float(v["high"]),
                        "low": float(v["low"]),
                        "close": float(v["close"]),
                        "vol": float(v["vol"]),
                    }
                    for k, v in df.iterrows()
                ]

        final_candles = candles[-limit:] if len(candles) > limit else candles

        return {
            "error": False,
            "symbol": symbol,
            "detail": "",
            "candles": final_candles,
        }

    except Exception as e:
        log.error(f"[EODHD] {pair['display']}: {e}")

        return {"error": True, "symbol": symbol, "detail": str(e)}


_polygon_lock = threading.Lock()
_polygon_last_request: float = 0.0  # epoch seconds of last Polygon HTTP request
_POLYGON_MIN_INTERVAL = 12.0  # 5 req/min free tier → 1 request per 12 s


def fetch_polygon(pair, tf, limit):
    """Download OHLCV candles from Polygon.io REST API. Best forex data quality.

    Returns dict with standardized error format."""

    symbol = pair.get("display", pair.get("symbol", "unknown"))

    with _polygon_lock:
        global _polygon_last_request

        # Pre-request throttle: enforce minimum interval regardless of success or error.
        # Old approach (sleep after success only) left 429/error paths unthrottled,
        # causing immediate follow-up calls that triggered further 429s on best pairs (XAU, XAG).
        elapsed = time.time() - _polygon_last_request
        if elapsed < _POLYGON_MIN_INTERVAL:
            time.sleep(_POLYGON_MIN_INTERVAL - elapsed)
        _polygon_last_request = time.time()

        try:
            key = os.environ.get("POLYGON_KEY", CONFIG.get("POLYGON_KEY", ""))

            if not key:
                log.warning("[PG] No POLYGON_KEY set")

                return {"error": True, "symbol": symbol, "detail": "No POLYGON_KEY set"}

            ticker = _polygon_ticker_for_pair(pair)

            if not ticker:
                log.info(f"[PG] {pair['display']}: no Polygon ticker mapping")

                return {
                    "error": True,
                    "symbol": symbol,
                    "detail": "no Polygon ticker mapping",
                }

            mult, span = {"D1": (1, "day"), "H4": (4, "hour"), "H1": (1, "hour")}.get(
                tf, (1, "day")
            )

            end = datetime.now(timezone.utc)

            start = end - timedelta(days=730)

            url = f"https://api.polygon.io/v2/aggs/ticker/{ticker}/range/{mult}/{span}/{start.strftime('%Y-%m-%d')}/{end.strftime('%Y-%m-%d')}"

            r = http_requests.get(
                url, params={"apiKey": key, "limit": 50000, "sort": "asc"}, timeout=20
            )

            if r.status_code == 403:
                log.warning(f"[PG] {ticker}: 403 Forbidden — check API key or plan")

                return {
                    "error": True,
                    "symbol": symbol,
                    "detail": "403 Forbidden - check API key or plan",
                }

            if r.status_code == 429:
                log.warning(
                    f"[PG] {ticker}: 429 rate limited — throttle will apply before next request"
                )

                # Send Telegram notification for Polygon rate limit
                try:
                    telegram_notify.notify_polygon_rate_limit()
                except Exception as _tn_e:
                    log.debug(
                        f"[TELEGRAM] Polygon rate limit notification failed: {_tn_e}"
                    )

                return {
                    "error": True,
                    "symbol": symbol,
                    "detail": "HTTP 429 rate limited",
                }

            if r.status_code != 200:
                log.warning(f"[PG] {ticker} HTTP {r.status_code}: {r.text[:120]}")

                return {
                    "error": True,
                    "symbol": symbol,
                    "detail": f"HTTP {r.status_code}",
                }

            data = r.json()

            results = data.get("results", [])

            if not results:
                log.warning(f"[PG] {ticker}: no results")

                return {"error": True, "symbol": symbol, "detail": "no results"}

            candles = [
                {
                    "time": datetime.fromtimestamp(
                        bar["t"] / 1000, tz=timezone.utc
                    ).isoformat(),
                    "open": float(bar["o"]),
                    "high": float(bar["h"]),
                    "low": float(bar["l"]),
                    "close": float(bar["c"]),
                    "vol": float(bar.get("v", 0)),
                }
                for bar in results
            ]

            final_candles = candles[-limit:] if len(candles) > limit else candles

            return {
                "error": False,
                "symbol": symbol,
                "detail": "",
                "candles": final_candles,
            }

        except Exception as e:
            log.error(f"[PG] {pair['display']}: {e}")

            return {"error": True, "symbol": symbol, "detail": str(e)}


_candle_cache: dict = {}  # (symbol, tf) → (candles, expiry_epoch)

_candle_cache_lock = threading.Lock()

# TTL per timeframe: cache holds until ~1 bar before the next bar is due

_CANDLE_CACHE_TTL = {"H1": 55 * 60, "H4": 235 * 60, "D1": 23 * 3600}


def _extract_candles(resp) -> list | None:
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


def fetch_candles(pair: dict, tf: str, limit: int) -> list | None:
    """Route candle fetch to correct source with in-memory TTL cache.



    Caches candle lists per (symbol, tf) so repeated scans within the same bar

    window reuse data instead of hammering the REST API on every scan.

    TTL: H1=55 min, H4=3h55m, D1=23h — expires just before the next bar closes.

    """

    if pair.get("type") != "crypto":
        live_resp = fetch_candles_live(pair.get("display", ""), tf, limit)
        live_candles = _extract_candles(live_resp)

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

    # Cache miss or expired — fetch fresh

    if pair["source"] == "binance":
        candles = fetch_binance(pair["symbol"], TF_B[tf], limit)

    elif pair["source"] == "eodhd":
        candles = fetch_eodhd(pair, tf, limit)

    elif pair["source"] == "polygon":
        candles = fetch_polygon(pair, tf, limit)

    elif pair["source"] == "yfinance":
        candles = fetch_yfinance(_yfinance_symbol_for_pair(pair), tf, limit)

    else:
        candles = None

    # Fallback chain for non-crypto: eodhd → yfinance (twelvedata removed - rate limited)
    if not candles and pair.get("type") != "crypto" and pair.get("source") == "eodhd":
        _yf_sym = _yfinance_symbol_for_pair(pair)
        if _yf_sym:
            log.info(
                f"[CANDLE] {pair.get('display')} EODHD failed, trying yfinance ({_yf_sym})"
            )
            candles = fetch_yfinance(_yf_sym, tf, limit)

    candles = _extract_candles(candles)

    # Data quality guard: reject candles with zero/negative close prices
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


from indicators import (  # noqa: E402
    calc_ema,
    calc_sma,
    calc_atr,
    calc_fib_proximity,
    calc_stochastic,
    calc_levels,
    calc_indicators,
    calc_fib,
    calc_indicators_with_normalized,
)


from scoring import (  # noqa: E402
    get_session,
    calc_confluence,
    detect_div,
    apply_correlation_cap,
    get_pair_profile,
    pair_filter_enabled,
    _pair_exchange_closed,
    _build_event_risk,
    _classify_signal,
)

from config import _json_safe  # noqa: E402


_scan_lock = threading.Lock()  # thread-safe scan guard (replaces bare boolean)

_kill_switch = False  # N4: Kill-switch — blocks new scans/analyses when True

_test_mode = (
    False  # Test mode: drops score thresholds, enables force-execute on all signals
)

_disabled_pairs: set = set()  # per-pair kill-switch — display names of pairs to exclude


def _normalize_style(style: str | None) -> str:
    """Normalize style strings used by scan/backtest endpoints."""

    s = (style or "auto").lower()

    return s if s in ("auto", "swing", "intraday", "scalp") else "auto"


def _resolve_scan_style(requested_style: str, pair: dict) -> str:
    """Resolve scan style per pair. Auto favors intraday for fast-moving markets."""

    if requested_style == "auto":
        return "intraday" if pair.get("type") == "crypto" else "swing"

    return requested_style


def _effective_backtest_style(pair: dict, requested_style: str) -> str:
    """Resolve backtest iteration style per pair."""

    if requested_style == "auto":
        return "intraday" if pair.get("type") == "crypto" else "swing"

    return requested_style  # scalp, intraday, swing all have dedicated loops


def run_full_scan(style: str = "auto", asset_class: str | None = None) -> dict:
    """Parallel scan of tracked pairs. Optional asset_class filter (crypto/forex/stock/commodity/index)."""

    _requested_style = _normalize_style(style)

    _valid_classes = {"crypto", "forex", "stock", "commodity", "index"}

    _ac = asset_class.lower().strip() if asset_class else None

    if _ac and _ac not in _valid_classes:
        return {
            "success": False,
            "error": f"Invalid asset_class '{asset_class}'. Valid: {sorted(_valid_classes)}",
            "signals": [],
            "tradeSignals": [],
            "watchlist": [],
            "errors": [],
            "skipped": [],
            "btcBias": "neutral",
            "totalPairs": 0,
            "activePairs": 0,
            "scannedAt": datetime.now(timezone.utc).isoformat(),
        }

    if _kill_switch:
        return {
            "success": False,
            "error": "Kill-switch active â€” system paused",
            "signals": [],
            "tradeSignals": [],
            "watchlist": [],
            "errors": [],
            "skipped": [],
            "btcBias": "neutral",
            "totalPairs": 0,
            "activePairs": 0,
            "scannedAt": datetime.now(timezone.utc).isoformat(),
        }

    if not _scan_lock.acquire(blocking=False):
        return {
            "success": False,
            "error": "Scan already in progress",
            "signals": [],
            "tradeSignals": [],
            "watchlist": [],
            "errors": [],
            "skipped": [],
            "btcBias": "neutral",
            "totalPairs": 0,
            "activePairs": 0,
            "scannedAt": datetime.now(timezone.utc).isoformat(),
        }

    try:
        candidate_pairs = [p for p in ALL_PAIRS if p["display"] not in _disabled_pairs]

        if _ac:
            candidate_pairs = [p for p in candidate_pairs if p.get("type") == _ac]

        active_pairs = [p for p in candidate_pairs if p.get("enabled", True)]

        results, watchlist, errors, skipped = [], [], [], []

        scan_funnel = {
            "total": len(candidate_pairs),
            "active": len(active_pairs),
            "no_data": 0,
            "low_score": 0,
            "passed": 0,
            "watchlist": 0,
            "errors": 0,
            "closed_exchange": 0,
            "event_block": 0,
            "inactive_pair": 0,
            "counter_trend": 0,
            "dead_ranging": 0,
        }

        btc_bias = "neutral"

        _closed_exchanges = set()

        ds_ctx = {}

        earnings_ctx = {}

        try:
            _eodhd_key = os.environ.get("EODHD_KEY", "")

            if _eodhd_key:
                for exch_code in ["JSE", "US"]:
                    try:
                        r = http_requests.get(
                            f"https://eodhd.com/api/exchange-details/{exch_code}?api_token={_eodhd_key}&fmt=json",
                            timeout=8,
                        )

                        if r.status_code == 200:
                            edata = r.json()

                            if not edata.get("isOpen", True):
                                _closed_exchanges.add(exch_code)

                                log.info(f"[EXCH] {exch_code}: CLOSED")

                            else:
                                log.info(f"[EXCH] {exch_code}: OPEN")

                    except Exception as _e:
                        log.debug(f"[EXCH] {exch_code} check error: {_e}")

        except Exception as e:
            log.warning(f"[EXCH] Exchange check failed: {e}")

        log.info("Fetching market context...")

        news_ctx = fetch_news_context(candidate_pairs)

        try:
            yield_ctx = fetch_yield_curve()

            if yield_ctx:
                news_ctx["yieldCurve"] = yield_ctx

        except Exception as e:
            log.warning(f"[YIELD] scan fetch err: {e}")

        try:
            ds_ctx = fetch_div_split_context()

            if ds_ctx:
                news_ctx["divSplit"] = ds_ctx

        except Exception as e:
            log.warning(f"[DIVS] scan fetch err: {e}")

        try:
            earnings_ctx = fetch_upcoming_earnings_context(candidate_pairs)

            if earnings_ctx:
                news_ctx["upcomingEarnings"] = earnings_ctx

        except Exception as e:
            log.warning(f"[EARN] scan fetch err: {e}")

        try:
            btc = fetch_candles(
                {"symbol": "BTCUSDT", "source": "binance"}, "D1", CONFIG["D1_CANDLES"]
            )

            if btc and len(btc) >= 200:
                s = calc_indicators(btc)["snap"]

                if s["ema21"] and s["ema50"] and s["ema200"]:
                    if s["ema21"] > s["ema50"] > s["ema200"]:
                        btc_bias = "bullish"

                    elif s["ema21"] < s["ema50"] < s["ema200"]:
                        btc_bias = "bearish"

            log.info(f"BTC bias: {btc_bias}")

        except Exception as e:
            log.error(f"BTC err: {e}")

        from concurrent.futures import ThreadPoolExecutor, as_completed

        def _analyse(pair):

            try:
                _pair_style = _resolve_scan_style(_requested_style, pair)

                return pair, analyze_pair(pair, btc_bias, style=_pair_style), None

            except Exception as e:
                return pair, None, str(e)

        with ThreadPoolExecutor(max_workers=3) as pool:
            futures = {pool.submit(_analyse, pair): pair for pair in candidate_pairs}

            for fut in as_completed(futures):
                pair, sig, err = fut.result()

                if err:
                    errors.append({"pair": pair["display"], "error": err})

                    scan_funnel["errors"] += 1

                    log.error(f"{pair['display']:12s} ERR: {err}")

                    continue

                if not sig:
                    skipped.append(
                        {
                            "pair": pair["display"],
                            "reason": "No data",
                            "tier": "skip",
                            "diagnostics": [{"code": "no_data", "detail": "No data"}],
                        }
                    )

                    scan_funnel["no_data"] += 1

                    log.info(f"{pair['display']:12s} SKIP")

                    continue

                threshold = get_pair_profile(pair).get(
                    "min_confluence",
                    CONFIG["MIN_CONFLUENCE_CLASS"].get(
                        pair["type"], CONFIG["MIN_CONFLUENCE"]
                    ),
                )

                if _test_mode:
                    threshold = max(
                        0.1, threshold * 0.5
                    )  # halve threshold in test mode

                sig = _annotate_signal_for_scan(
                    sig,
                    pair,
                    threshold,
                    ds_ctx,
                    earnings_ctx,
                    _closed_exchanges,
                    news_ctx,
                )

                tier, tier_reason = _classify_signal(sig, pair)

                sig["signalTier"] = tier

                sig["watchlistReason"] = tier_reason if tier == "watchlist" else None

                codes = {d.get("code") for d in sig.get("scanDiagnostics", [])}

                if "low_confluence" in codes:
                    scan_funnel["low_score"] += 1

                if "closed_exchange" in codes:
                    scan_funnel["closed_exchange"] += 1

                if "event_risk" in codes:
                    scan_funnel["event_block"] += 1

                if "inactive_pair" in codes:
                    scan_funnel["inactive_pair"] += 1

                if "counter_trend" in codes:
                    scan_funnel["counter_trend"] += 1

                if "dead_ranging" in codes:
                    scan_funnel["dead_ranging"] += 1

                if tier == "trade":
                    # Run AI analysis in background thread — only notify if grade A or B
                    # Respect AI_ON_DEMAND_ONLY: skip AI on automatic scans
                    if telegram_notify._is_enabled() and not CONFIG.get("AI_ON_DEMAND_ONLY", False):
                        _sig_snap = {
                            k: v
                            for k, v in sig.items()
                            if k not in ("d1", "h4", "h1", "h4Candles")
                        }

                        def _ai_notify(_s=_sig_snap):
                            try:
                                ai = run_ai(_s)
                                telegram_notify.notify_signal_with_ai(
                                    pair=_s["pair"],
                                    direction=_s["direction"],
                                    score=_s.get("confluenceScore", 0),
                                    max_score=_s.get("maxScore", 3.0),
                                    entry=_s.get("price", 0),
                                    sl=_s.get("sl", 0),
                                    tp1=_s.get("tp1", 0),
                                    rr1=_s.get("rr1", 0),
                                    regime=_s.get("trendState", ""),
                                    signal_class=_s.get("signalClass", ""),
                                    ai_grade=ai.get("grade", ""),
                                    ai_verdict=ai.get("verdict", ""),
                                    ai_edge_prob=ai.get("edgeProbability", 0),
                                    ai_risk=ai.get("riskLevel", ""),
                                    ai_warnings=ai.get("warnings", []),
                                )
                            except Exception as _e:
                                log.debug(f"[TELEGRAM] AI notify failed: {_e}")

                        threading.Thread(target=_ai_notify, daemon=True).start()

                    try:
                        sig["serverIndicators"] = fetch_eodhd_indicators(pair)

                    except Exception as _e:
                        log.debug(
                            f"[IND] {pair['display']} server indicators skipped: {_e}"
                        )

                    results.append(sig)

                    scan_funnel["passed"] += 1

                    log.info(
                        f"{pair['display']:12s} {sig['direction']:5s} {sig['confluenceScore']}/{sig.get('maxScore', 3)} [{sig.get('trendState', '?')}]"
                    )

                elif tier == "watchlist":
                    watchlist.append(sig)

                    scan_funnel["watchlist"] += 1

                    log.info(
                        f"{pair['display']:12s} WATCH {sig['confluenceScore']}/{sig.get('maxScore', 3)} :: {tier_reason}"
                    )

                else:
                    skipped.append(
                        {
                            "pair": pair["display"],
                            "reason": tier_reason,
                            "tier": "skip",
                            "diagnostics": sig.get("scanDiagnostics", []),
                        }
                    )

                    log.info(
                        f"{pair['display']:12s} SKIP  {sig['confluenceScore']}/{sig.get('maxScore', 3)} :: {tier_reason}"
                    )

        log.info(f"Scan funnel: {scan_funnel}")

        results.sort(key=lambda x: x["confluenceScore"], reverse=True)

        watchlist.sort(key=lambda x: x["confluenceScore"], reverse=True)

        results = apply_correlation_cap(results)

        watchlist = apply_correlation_cap(watchlist)

        return {
            "success": True,
            "signals": results,
            "tradeSignals": results,
            "watchlist": watchlist,
            "errors": errors,
            "skipped": skipped,
            "scanFunnel": scan_funnel,
            "btcBias": btc_bias,
            "totalPairs": len(candidate_pairs),
            "activePairs": len(active_pairs),
            "scannedAt": datetime.now(timezone.utc).isoformat(),
            "styleRequested": _requested_style,
            "style": _requested_style,
            "testMode": _test_mode,
        }

    finally:
        _scan_lock.release()


EXPERT_PROMPT = """You are Marcus Reid — 18-year prop-desk veteran turned trading mentor. You've seen it all and you're not easily impressed, but when a setup is clean you get genuinely excited. You speak like a sharp friend who happens to be a market wizard — concise, opinionated, occasionally witty. No corporate-speak. No filler.



Framework: Elder Triple Screen, Wilder rules, Weinstein stages, Murphy intermarket, Minervini templates, Van Tharp R-multiples, Douglas probability.



STRICT RULES:

- Output ONLY valid JSON. No markdown, no explanations outside JSON values.

- Use probability language: "edge suggests", "probability favors", "setup indicates". NEVER "will", "guaranteed", "should hit".

- Reference specific input data (score%, TrendState, vote names, warnings).

- Counter-trend = automatic grade drop of 1 full level + explicit warning.



INPUT FORMAT — the signal comes in labeled sections:

=== SIGNAL === (pair, direction, score as percentage of class max, conviction, regime)

=== ENGINE B (NAKED MARKET STRUCTURE) === (present if naked structure analysis was run — overrides traditional technicals)

=== TECHNICALS === (individual scored votes with their weights, plus context indicators)

=== LEVELS === (entry, SL, TP1, TP2 with R-multiples)

=== WARNINGS === (penalties already applied to score — these are facts, not opinions)

=== CONTEXT === (NOT scored — news, DXY, yield curve, backtest stats — use for narrative color)

=== PORTFOLIO === (current heat and drawdown if any)



GRADING (use the Score % from SIGNAL section):

A+ (85-100%): Elite — full size, everything aligned.

A  (70-84%): Strong — normal size, minor gaps only.

B  (55-69%): Valid — half size, needs monitoring.

C  (40-54%): Watchlist only — interesting but not ready.

F  (0-39%): Avoid — insufficient edge or DEAD RANGING.



REGIME (read TrendState first):

- TRENDING (ADX>=35): Full rules, pullbacks to EMA21/50 are entries, extend TP.

- DEVELOPING (25-34): Confirm with volume.

- RANGING: Downgrade B->C, C->F. Only fade BB extremes + stoch reversal.

- DEAD RANGING: F-grade instantly. Do not trade.



ELDER TRIPLE SCREEN:

D1 tide must lead. Any H4/H1 conflict = WAIT. Counter-trend = -1 grade.



WILDER + MURPHY + WEINSTEIN:

- RSI divergence = HIGH priority. RSI 40-80 bullish range (Cardwell). ADX<25 = no trend signals.

- Fib proximity active -> name exact level in entryZone. Fib + stoch + EMA cluster = A-grade.

- Weinstein Stage 1/3 = no new trend trades. Stage 2 = ideal LONG. Stage 4 = ideal SHORT.

- DXY rising = headwind for EUR,GBP,AUD,NZD,XAU,XAG LONGS. BTC bearish = alt LONG risk.



VAN THARP SIZING:

Express in R. SL >2% price = quarter size. Min 2R reward. SQN: <1.6=Poor, 2.5+=Good, 3+=Excellent, 5+=Superb.



NEWS & EVENTS:

High-impact (FOMC,NFP,CPI) within 24h = reduce 50% or WAIT. Conflicting news = downgrade 1 level.



STYLE RULES:

- SCALP: ADX>30, H1 exhaustion, 1.5-2R.

- INTRADAY: H4+H1 aligned, same session, 2-3R.

- SWING: D1 EMA stack dominant, EMA200 slope, 4-6R.

If incompatible with requested style, say so and recommend correct style.



ENGINE B (NAKED) RULES:

- If the "ENGINE B (NAKED MARKET STRUCTURE)" section is fully present, the setup is a PURE price-action trade.

- You MUST prioritize the structural verdict (Swing Sequence, Nearest Support/Resistance, Break of Structure) and base the narrative entirely on market structure and liquidity. Disregard the absence of traditional indicators.



edgeProbability: Base = Score% × 0.95. +10 if conviction HIGH and TRENDING. -15 if counter-trend or DEAD RANGING. -10 if high-impact news. Cap 20-95.



riskLevel: "Low" if edgeProb>=70 and TRENDING. "High" if edgeProb<40 or DEAD RANGING or counter-trend. "Medium" otherwise.



OUTPUT — EXACT JSON ONLY:

{"grade":"A","verdict":"One punchy sentence — be yourself, not a robot","narrative":"2-3 sentences referencing specific votes and data. Show personality.","entryZone":"exact price/fib","invalidation":"exact price","keyLevels":"S1/R1","positionSizing":"Full/Half/Quarter + R explanation","tradeStyle":"SWING|INTRADAY|SCALP","tradeStyleReason":"why","warnings":["specific risks"],"edgeProbability":68,"riskLevel":"Medium"}



Now analyse the following signal data and reply with JSON only:"""


def fetch_dxy_context():
    """Fetch DXY (US Dollar Index) 5-day trend for Murphy intermarket context."""

    try:
        d = fetch_yfinance("DX-Y.NYB", "D1", 10)

        if not d or len(d) < 5:
            return None

        cl = [c["close"] for c in d]

        chg = round((cl[-1] - cl[-5]) / cl[-5] * 100, 2)

        trend = "rising" if chg > 0.3 else "falling" if chg < -0.3 else "flat"

        return f"trend={trend} 5d_chg={chg}% price={round(cl[-1], 2)}"

    except Exception:
        return None


# Phase A: UST Yield Curve cache (1hr TTL â€” rates change slowly)

_yield_cache = {"data": None, "ts": 0}

_YIELD_TTL = 3600


def fetch_yield_curve():
    """Fetch UST yield rates from EODHD. Returns shape, 2Y-10Y spread, and 3M rate for AI risk context."""

    global _yield_cache

    now = time.time()

    if _yield_cache["data"] and (now - _yield_cache["ts"]) < _YIELD_TTL:
        return _yield_cache["data"]

    try:
        _key = os.environ.get("EODHD_KEY", "")

        if not _key:
            return None

        r = http_requests.get(
            f"https://eodhd.com/api/ust/yield-rates?api_token={_key}&fmt=json",
            timeout=10,
        )

        if r.status_code != 200:
            return None

        data = r.json()

        # API returns {"meta":{...},"data":[{"date":"...","tenor":"2Y","rate":3.47},...]}

        rows = data.get("data", data) if isinstance(data, dict) else data

        if not rows or not isinstance(rows, list):
            return None

        # Build tenor map from latest date

        latest_date = max(_row["date"] for _row in rows if _row.get("date"))

        tenor_map = {
            _row["tenor"]: _row["rate"]
            for _row in rows
            if _row.get("date") == latest_date
            and _row.get("tenor")
            and _row.get("rate") is not None
        }

        y3m = tenor_map.get("3M") or tenor_map.get("1.5M")

        y2y = tenor_map.get("2Y")

        y10y = tenor_map.get("10Y")

        y30y = tenor_map.get("30Y")

        if y2y is None or y10y is None:
            return None

        spread_2_10 = round(float(y10y) - float(y2y), 3)

        if spread_2_10 < -0.1:
            shape = "inverted"

        elif spread_2_10 < 0.1:
            shape = "flat"

        else:
            shape = "normal"

        result = {
            "shape": shape,
            "spread_2_10": spread_2_10,
            "y3m": round(float(y3m), 3) if y3m else None,
            "y2y": round(float(y2y), 3),
            "y10y": round(float(y10y), 3),
            "y30y": round(float(y30y), 3) if y30y else None,
            "riskContext": "risk-off (recession warning)"
            if shape == "inverted"
            else "neutral"
            if shape == "flat"
            else "risk-on",
            "date": latest_date,
        }

        _yield_cache = {"data": result, "ts": now}

        log.info(
            f"[YIELD] Curve: {shape} | 2Y:{y2y}% 10Y:{y10y}% spread:{spread_2_10}%"
        )

        return result

    except Exception as e:
        log.warning(f"[YIELD] fetch failed: {e}")

        return None


# Phase B: Dividend/Split awareness cache (24hr TTL)

_divsplit_cache = {"data": {}, "ts": 0}

_DIVSPLIT_TTL = 86400  # 24 hours


# Stock pairs that can have dividends/splits

_DIV_SPLIT_PAIRS = [p["symbol"] for p in ALL_PAIRS if p.get("type") == "stock"]


def fetch_div_split_context():
    """Fetch upcoming dividends and splits for stock pairs. Warns AI if ex-div within 7 days."""

    global _divsplit_cache

    now = time.time()

    if _divsplit_cache["ts"] > 0 and (now - _divsplit_cache["ts"]) < _DIVSPLIT_TTL:
        log.debug(f"[DIVS] Cache hit — age {int(now - _divsplit_cache['ts'])}s")
        return _divsplit_cache["data"]

    _key = os.environ.get("EODHD_KEY", "")

    if not _key:
        return {}

    today = datetime.now(timezone.utc).date()

    result = {}

    for sym in _DIV_SPLIT_PAIRS:
        entry = {}

        # Dividends

        try:
            r = http_requests.get(
                f"https://eodhd.com/api/div/{sym}?api_token={_key}&fmt=json", timeout=8
            )

            if r.status_code == 200:
                divs = r.json()

                if divs and isinstance(divs, list):
                    upcoming = []

                    for d in divs:
                        ex = d.get("date") or d.get("exDividendDate")

                        if not ex:
                            continue

                        try:
                            ex_date = datetime.strptime(str(ex)[:10], "%Y-%m-%d").date()

                            days_to = (ex_date - today).days

                            if 0 <= days_to <= 14:
                                upcoming.append(
                                    {
                                        "exDate": str(ex_date),
                                        "daysTo": days_to,
                                        "amount": d.get("value", d.get("dividend")),
                                    }
                                )

                        except Exception as _e:
                            log.debug(f"[DIVS] date parse error: {_e}")

                    if upcoming:
                        entry["upcomingDiv"] = upcoming

                        log.info(
                            f"[DIVS] {sym}: ex-div in {upcoming[0]['daysTo']} days"
                        )

        except Exception as e:
            log.warning(f"[DIVS] {sym}: {e}")

        # Splits

        try:
            r = http_requests.get(
                f"https://eodhd.com/api/splits/{sym}?api_token={_key}&fmt=json",
                timeout=8,
            )

            if r.status_code == 200:
                splits = r.json()

                if splits and isinstance(splits, list):
                    upcoming = []

                    for s in splits:
                        sd = s.get("date")

                        if not sd:
                            continue

                        try:
                            s_date = datetime.strptime(str(sd)[:10], "%Y-%m-%d").date()

                            days_to = (s_date - today).days

                            if 0 <= days_to <= 30:
                                upcoming.append(
                                    {
                                        "splitDate": str(s_date),
                                        "daysTo": days_to,
                                        "ratio": s.get("split"),
                                    }
                                )

                        except Exception as _e:
                            log.debug(f"[SPLITS] date parse error: {_e}")

                    if upcoming:
                        entry["upcomingSplit"] = upcoming

                        log.warning(
                            f"[SPLITS] {sym}: split in {upcoming[0]['daysTo']} days"
                        )

        except Exception as e:
            log.warning(f"[SPLITS] {sym}: {e}")

        if entry:
            result[sym] = entry

    _divsplit_cache = {"data": result, "ts": now}

    log.info(
        f"[DIVS] Checked {len(_DIV_SPLIT_PAIRS)} pairs — {len(result)} with upcoming events"
    )

    log.info(
        f"[DIVS] Cache populated — {len(_DIV_SPLIT_PAIRS)} pairs, {len(result)} events found. Next refresh in 24h."
    )

    return result


# Phase B2: Upcoming earnings awareness cache (6hr TTL)

_earnings_cache = {"data": {}, "ts": 0}

_EARNINGS_TTL = 21600

_EARNINGS_AVAILABLE = None  # None = untested, True = confirmed working


def fetch_upcoming_earnings_context(pairs: list | None = None) -> dict:
    """Fetch upcoming earnings for tracked stock pairs via EODHD SDK."""

    global _earnings_cache, _EARNINGS_AVAILABLE

    now = time.time()

    if _earnings_cache["data"] and (now - _earnings_cache["ts"]) < _EARNINGS_TTL:
        return _earnings_cache["data"]

    if _EARNINGS_AVAILABLE is False:
        return {}  # Already confirmed unavailable on this EODHD plan — skip API call

    api = _get_eodhd_client()

    if not api:
        return {}

    stock_pairs = [p for p in (pairs or ALL_PAIRS) if p.get("type") == "stock"]

    if not stock_pairs:
        return {}

    symbols = [p["symbol"] for p in stock_pairs]

    today = datetime.now(timezone.utc).date()

    end_date = today + timedelta(days=14)

    try:
        rows = (
            api.get_upcoming_earnings_data(
                from_date=today.strftime("%Y-%m-%d"),
                to_date=end_date.strftime("%Y-%m-%d"),
                symbols=",".join(symbols),
            )
            or []
        )

        _EARNINGS_AVAILABLE = True

    except Exception as e:
        if "403" in str(e) or "Forbidden" in str(e):
            _EARNINGS_AVAILABLE = (
                False  # Disable for this session — plan doesn't include it
            )

            log.warning(
                "[EARN] 403 Forbidden — earnings calendar not available on this EODHD plan. Disabling for session."
            )

        else:
            log.warning(f"[EARN] fetch failed: {e}")

        return {}

    result = {}

    for row in rows:
        sym = (row.get("code") or row.get("symbol") or row.get("ticker") or "").upper()

        if not sym or sym not in symbols:
            continue

        dt_raw = (
            row.get("report_date")
            or row.get("date")
            or row.get("earnings_date")
            or row.get("datetime")
        )

        if not dt_raw:
            continue

        try:
            earn_date = datetime.strptime(str(dt_raw)[:10], "%Y-%m-%d").date()

        except ValueError:
            continue

        days_to = (earn_date - today).days

        if 0 <= days_to <= 14:
            result[sym] = {
                "date": str(earn_date),
                "daysTo": days_to,
                "beforeMarket": row.get("before_market"),
                "afterMarket": row.get("after_market"),
                "currency": row.get("currency"),
            }

    _earnings_cache = {"data": result, "ts": now}

    log.info(
        f"[EARN] Checked {len(symbols)} stock symbols â€” {len(result)} with upcoming earnings"
    )

    return result


# P2: 5-minute TTL cache for news context â€” avoid redundant API calls during rapid scans

_news_cache = {"data": None, "ts": 0}

_NEWS_TTL = 300  # 5 minutes


def fetch_news_context(pairs: list | None = None):

    now = time.time()

    if _news_cache["data"] and (now - _news_cache["ts"]) < _NEWS_TTL:
        log.info("[NEWS] Using cached context")

        return _news_cache["data"]

    ctx = {"forexEvents": [], "cryptoNews": [], "marketNews": []}

    try:
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")

        r = http_requests.get(
            f"https://finnhub.io/api/v1/calendar/economic?from={today}&to={today}&token={CONFIG.get('FINNHUB_KEY', '')}",
            timeout=8,
        )

        if r.status_code == 200:
            events = [
                e
                for e in r.json().get("economicCalendar", [])
                if e.get("impact", "").lower() in ["high", "3"]
            ]

            ctx["forexEvents"] = [
                {
                    "time": e.get("time", ""),
                    "currency": e.get("country", ""),
                    "event": e.get("event", ""),
                }
                for e in events[:5]
            ]

            log.info(
                f"[NEWS] Economic calendar: {len(ctx['forexEvents'])} high-impact events today"
            )

    except Exception as e:
        log.warning(f"[NEWS] Economic calendar failed: {e}")

    if CONFIG.get("CRYPTOPANIC_KEY"):
        try:
            r = http_requests.get(
                f"https://cryptopanic.com/api/v1/posts/?auth_token={CONFIG['CRYPTOPANIC_KEY']}&public=true&filter=hot",
                timeout=8,
            )

            if r.status_code == 200:
                posts = r.json().get("results", [])

                ctx["cryptoNews"] = [
                    {
                        "title": p.get("title", ""),
                        "sentiment": "bullish"
                        if p.get("votes", {}).get("positive", 0)
                        > p.get("votes", {}).get("negative", 0)
                        else "bearish"
                        if p.get("votes", {}).get("negative", 0) > 0
                        else "neutral",
                        "currencies": [c["code"] for c in p.get("currencies", [])],
                    }
                    for p in posts[:8]
                ]

                log.info(f"[NEWS] CryptoPanic: {len(ctx['cryptoNews'])} headlines")

        except Exception as e:
            log.warning(f"[NEWS] CryptoPanic failed: {e}")

    if CONFIG.get("FINNHUB_KEY"):
        try:
            r = http_requests.get(
                f"https://finnhub.io/api/v1/news?category=general&token={CONFIG['FINNHUB_KEY']}",
                timeout=8,
            )

            if r.status_code == 200:
                ctx["marketNews"] = [
                    {
                        "headline": a.get("headline", ""),
                        "summary": a.get("summary", "")[:100],
                    }
                    for a in r.json()[:5]
                ]

                log.info(f"[NEWS] Finnhub: {len(ctx['marketNews'])} market headlines")

        except Exception as e:
            log.warning(f"[NEWS] Finnhub failed: {e}")

    try:
        _eodhd_key = os.environ.get("EODHD_KEY", "")

        if _eodhd_key:
            ticker_map = {
                t: p["display"]
                for p in (pairs or ACTIVE_PAIRS)
                if (t := _eodhd_ticker_for_pair(p))
            }

            sentiments = {}

            if ticker_map:
                tickers_csv = ",".join(ticker_map.keys())

                sdata = http_requests.get(
                    f"https://eodhd.com/api/sentiments?s={tickers_csv}&api_token={_eodhd_key}&fmt=json",
                    timeout=12,
                ).json()

                for sticker, display in ticker_map.items():
                    scores = sdata.get(sticker, [])

                    if scores and scores[0].get("normalized") is not None:
                        sc = scores[0]["normalized"]

                        label = (
                            "bullish"
                            if sc > 0.6
                            else "bearish"
                            if sc < 0.4
                            else "neutral"
                        )

                        sentiments[display] = round(sc, 3)

                        log.info(f"[SENT] {display:12s} {sc:.2f} {label}")

                if sentiments:
                    ctx["pairSentiment"] = sentiments

            pair_news = {}

            for sticker, display in list(ticker_map.items())[:10]:
                try:
                    ndata = http_requests.get(
                        f"https://eodhd.com/api/news?s={sticker}&limit=3&api_token={_eodhd_key}&fmt=json",
                        timeout=8,
                    ).json()

                    if ndata and isinstance(ndata, list):
                        pair_news[display] = [
                            {
                                "t": a.get("title", "")[:80],
                                "s": round(
                                    a.get("sentiment", {}).get("polarity", 0.5), 2
                                ),
                            }
                            for a in ndata[:3]
                        ]

                except Exception as _e:
                    log.debug(f"[NEWS] {display} news fetch error: {_e}")

            if pair_news:
                ctx["pairNews"] = pair_news

                log.info(
                    f"[NEWS] EODHD per-pair news: {len(pair_news)} pairs, {sum(len(v) for v in pair_news.values())} articles"
                )

            word_weights = {}

            for sticker, display in list(ticker_map.items())[:10]:
                try:
                    wdata = http_requests.get(
                        f"https://eodhd.com/api/news-word-weights?s={sticker}&page[limit]=5&api_token={_eodhd_key}&fmt=json",
                        timeout=8,
                    ).json()

                    if wdata and isinstance(wdata, dict) and wdata.get("data"):
                        word_weights[display] = list(wdata["data"].keys())[:5]

                except Exception as _e:
                    log.debug(f"[NEWS] {display} word weights error: {_e}")

            if word_weights:
                ctx["wordWeights"] = word_weights

                log.info(f"[NEWS] Word weights: {len(word_weights)} pairs")

    except Exception as e:
        log.warning(f"[NEWS] EODHD sentiment/news failed: {e}")

    _news_cache["data"] = ctx

    _news_cache["ts"] = time.time()

    return ctx


def _build_signal_message(
    signal: dict,
    news_ctx: dict | None,
    style_pref: str,
    style_labels: dict,
    portfolio_heat: float = 0.0,
    drawdown_pct: float = 0.0,
    learning_ctx: dict | None = None,
) -> str:
    """Build sectioned signal string sent to Marcus Reid (Claude) for analysis.



    Sections: SIGNAL, TECHNICALS, LEVELS, WARNINGS, CONTEXT, PORTFOLIO.

    Removes redundant ServerIndicators (votes already contain the verdict).

    """

    max_score = signal.get("maxScore", 3.0)

    score = signal.get("confluenceScore", 0)

    score_pct = round(score / max_score * 100) if max_score else 0

    spread = signal.get("spread", 0)

    conviction = "HIGH" if spread >= 0.6 else "MEDIUM" if spread >= 0.3 else "LOW"

    ptype = signal.get("type", "stock")

    # === SIGNAL ===

    lines = [
        "=== SIGNAL ===",
        f"Pair: {signal['pair']} | Direction: {signal['direction']} | Score: {score}/{max_score} ({score_pct}%)",
        f"Conviction: {conviction} (spread {spread}) | Entry Mode: {signal.get('entryMode', 'trend')} | "
        f"Class: {signal.get('signalClass', 'trend_continuation')}",
        f"Style: {style_pref.upper()} ({style_labels.get(style_pref.lower(), '')}) | "
        f"Regime: {signal.get('trendState', '?')} "
        f"(ADX {signal.get('h4', {}).get('snap', {}).get('adxPct', '?')}th pct, "
        f"{signal.get('h4', {}).get('snap', {}).get('adxLabel', '?')})",
    ]
    # === ENGINE B (NAKED MARKET STRUCTURE) ===
    eng_b = signal.get("engine_b")
    if eng_b:
        lines.append("")
        lines.append("=== ENGINE B (NAKED MARKET STRUCTURE) ===")
        lines.append(f"  Structural Verdict: {eng_b.get('structural_verdict')}")
        lines.append(f"  Current Swing Seq: {eng_b.get('current_swing_sequence')}")
        lines.append(f"  Macro Swing Seq: {eng_b.get('macro_swing_sequence')}")

        rz = eng_b.get("nearest_resistance_zone")
        if rz:
            lines.append(
                f"  Nearest Res Zone: {rz.get('lower', 0):.4f} - {rz.get('upper', 0):.4f}"
            )

        sz = eng_b.get("nearest_support_zone")
        if sz:
            lines.append(
                f"  Nearest Sup Zone: {sz.get('lower', 0):.4f} - {sz.get('upper', 0):.4f}"
            )

        lines.append(f"  Distance to Res: {eng_b.get('distance_to_res', 0):.2f}%")
        lines.append(f"  Distance to Sup: {eng_b.get('distance_to_sup', 0):.2f}%")
        lines.append(f"  Room to Move Bonus: {eng_b.get('room_to_move_bonus', 0)}")
        lines.append(f"  Catalyst Bonus: {eng_b.get('catalyst_bonus', 0)}")
        lines.append(f"  AI Stats Adjustment: {eng_b.get('ai_adjustment', 0):.2f}")
        lines.append(
            f"  Engine B Final Score: {eng_b.get('score', 0):.2f} / {eng_b.get('max_possible', 3.0):.2f} ({eng_b.get('score_pct', 0):.1f}%)"
        )
        lines.append(
            f"  Engine B Actionable: {'YES' if eng_b.get('is_actionable') else 'NO'}"
        )
        lines.append(f"  Engine B Rec SL: {eng_b.get('recommended_stop_loss')}")
        lines.append(f"  Engine B Rec TP: {eng_b.get('recommended_take_profit')}")

    # === TECHNICALS ===
    votes = signal.get("votes", {})
    vote_lines = []

    # Flatten nested votes dynamically (e.g. from old frontend cache or UI-structured payloads)
    flat_votes = {}
    for k, v in votes.items():
        if isinstance(v, dict):
            for sub_k, sub_v in v.items():
                flat_votes[sub_k] = sub_v
        else:
            flat_votes[k] = v

    for vname, vval in flat_votes.items():
        try:
            sign = f"+{vval}" if float(vval) > 0 else str(vval)
            vote_lines.append(f"  {vname}: {sign}")
        except (ValueError, TypeError):
            vote_lines.append(f"  {vname}: {vval}")

    lines.append("")

    lines.append("=== TECHNICALS (scored votes) ===")

    lines.extend(vote_lines)

    lines.append(f"  Stoch K/D: {signal.get('stochK')}/{signal.get('stochD')}")

    lines.append(f"  Volume ratio: {signal.get('volRatio', 1.0)}x avg")

    lines.append(f"  EMA200 slope: {signal.get('ema200Slope', 0)}%")

    lines.append(f"  Weinstein: {signal.get('weinsteinLabel', 'n/a')}")

    lines.append(
        f"  ATR percentile: {signal.get('h4', {}).get('snap', {}).get('atrPct', '?')} "
        f"({signal.get('h4', {}).get('snap', {}).get('atrLabel', '?')})"
    )

    # === LEVELS ===

    lines.append("")

    lines.append("=== LEVELS ===")
    lines.append(
        f"Entry: {signal['price']} | SL: {signal['sl']} ({signal.get('slPct', '?')}%) | "
        f"TP1: {signal['tp1']} ({signal['rr1']}R) | TP2: {signal['tp2']} ({signal['rr2']}R)"
    )
    lines.append(
        f"ATR: {signal.get('atr', signal.get('naked_data', {}).get('atr', 'N/A'))}"
    )

    fib = signal.get("fib")

    if fib:
        # Only send key fib levels — full dump is ~100 extra tokens
        _key_fibs = {
            k: v
            for k, v in fib.items()
            if k in ("0", "236", "382", "500", "618", "786", "1000")
        }
        lines.append(f"Fib levels: {json.dumps(_key_fibs)}")

    # === WARNINGS (scoring penalties applied) ===

    warnings = signal.get("warnings", [])

    if warnings:
        lines.append("")

        lines.append("=== WARNINGS (scoring penalties applied) ===")

        for w in warnings:
            lines.append(f"- {w}")

    # === CONTEXT (not scored, for AI analysis) ===

    lines.append("")

    lines.append("=== CONTEXT (for your analysis, NOT scored) ===")

    _sname = signal.get("session", {}).get("name", "Global")
    _squal = signal.get("session", {}).get("quality", "N/A")
    lines.append(
        f"BTC bias: {signal.get('btcBias', 'n/a')} | Session: {_sname} ({_squal})"
    )

    dxy_ctx = fetch_dxy_context()

    if dxy_ctx:
        lines.append(f"DXY: {dxy_ctx}")

    _yc = fetch_yield_curve()

    if _yc:
        lines.append(
            f"Yield curve: {_yc['shape']} (2Y-10Y spread: {_yc['spread_2_10']}%, "
            f"3M: {_yc.get('y3m')}%, 10Y: {_yc['y10y']}%) — {_yc['riskContext']}"
        )

    _ds = fetch_div_split_context()

    _pair_sym = signal.get("symbol", "")

    if _ds and _pair_sym in _ds:
        _ev = _ds[_pair_sym]

        if _ev.get("upcomingDiv"):
            _d = _ev["upcomingDiv"][0]

            lines.append(
                f"Ex-div in {_d['daysTo']} days ({_d['exDate']}, amount: {_d.get('amount', '?')}) — gap-down risk"
            )

        if _ev.get("upcomingSplit"):
            _s = _ev["upcomingSplit"][0]

            lines.append(
                f"Split in {_s['daysTo']} days ({_s['splitDate']}, ratio: {_s.get('ratio', '?')}) — price distortion risk"
            )

    _oi_div = signal.get("oiDivergence")

    if _oi_div:
        lines.append(
            f"Open Interest: {_oi_div.get('oiChange', 0)}% chg | Price: {_oi_div.get('priceChange', 0)}% chg | Signal: {_oi_div.get('signal', 'neutral')}"
        )

    if "_fundamentals" in signal:
        _f = signal["_fundamentals"]

        lines.append(
            f"Fundamentals: P/E {_f.get('pe_ratio', '?')} | Fwd P/E {_f.get('forward_pe', '?')} | Margin {_f.get('profit_margin', '?')} | Beta {_f.get('beta', '?')}"
        )

    if "_insider" in signal:
        _ins = signal["_insider"]

        lines.append(
            f"Insider Trading (90d): {_ins.get('net_sentiment')} | {_ins.get('buys')} buys (${_ins.get('buy_value')}), {_ins.get('sells')} sells (${_ins.get('sell_value')})"
        )

    _bt = signal.get("backtestStats")

    if _bt:
        lines.append(
            f"Backtest: SQN {_bt.get('sqn', '?')} | WR {_bt.get('winRate', '?')}% | "
            f"Expectancy {_bt.get('expectancy', '?')}R | Max DD {_bt.get('maxDrawdownPct', '?')}%"
        )

        _rs = _bt.get("regimeStats", {})

        if _rs:
            lines.append(
                f"Regime WR: {json.dumps({k: v.get('wr') for k, v in _rs.items()})}"
            )

    pair_sqn = signal.get("pairSQN")

    if pair_sqn:
        lines.append(f"Pair SQN: {pair_sqn}")

    if news_ctx:
        _ctx_parts = []

        # Forex events — keep only event name + date (strip full object bloat)
        if news_ctx.get("forexEvents"):
            _fe = [
                {
                    "e": e.get("event", e.get("title", "")),
                    "d": e.get("date", e.get("time", "")),
                }
                for e in news_ctx["forexEvents"][:4]
            ]

            _ctx_parts.append(f"High-impact events: {json.dumps(_fe)}")

        _sent = news_ctx.get("pairSentiment", {})

        if _sent.get(signal.get("pair", "")):
            _sc = _sent[signal["pair"]]

            _sl = "bullish" if _sc > 0.6 else "bearish" if _sc < 0.4 else "neutral"

            _ctx_parts.append(f"News sentiment: {_sc} ({_sl})")

        if news_ctx.get("cryptoNews") and ptype == "crypto":
            pair_coins = [signal["symbol"].replace("USDT", "").replace("USDC", "")]

            relevant = [
                n
                for n in news_ctx["cryptoNews"]
                if not n["currencies"] or any(c in pair_coins for c in n["currencies"])
            ]

            if relevant:
                # Title only — strip full article body
                _ctx_parts.append(
                    f"Crypto news: {', '.join(n.get('title', '')[:80] for n in relevant[:2])}"
                )

        if news_ctx.get("marketNews"):
            # Title only
            _ctx_parts.append(
                f"Market news: {', '.join(n.get('title', n.get('headline', ''))[:80] for n in news_ctx['marketNews'][:2])}"
            )

        _pnews = news_ctx.get("pairNews", {}).get(signal.get("pair", ""), [])

        if _pnews:
            _ctx_parts.append(
                f"Pair news: {', '.join(n.get('title', n.get('headline', ''))[:80] for n in _pnews[:2])}"
            )

        _ww = news_ctx.get("wordWeights", {}).get(signal.get("pair", ""), [])

        if _ww:
            _ctx_parts.append(f"News drivers: {json.dumps(_ww[:5])}")

        for cp in _ctx_parts:
            lines.append(cp)

    # === PORTFOLIO ===

    if portfolio_heat > 0 or drawdown_pct > 0:
        lines.append("")

        lines.append("=== PORTFOLIO ===")

        if portfolio_heat > 0:
            lines.append(f"Heat: {portfolio_heat:.1%}")

        if drawdown_pct > 0:
            lines.append(f"Drawdown: {drawdown_pct:.1%}")

    # === LEARNING CONTEXT (live outcome feedback) ===

    if learning_ctx and learning_ctx.get("sample_size", 0) >= CONFIG.get(
        "LEARNING_MIN_TRADES", 5
    ):
        lines.append("")

        lines.append("=== LEARNING CONTEXT (from live outcomes) ===")

        pair_s = learning_ctx.get("pair_stats")

        if pair_s:
            lines.append(
                f"This pair history: {pair_s['win_rate'] * 100:.0f}% WR over "
                f"{pair_s['total_trades']} trades (avg {pair_s['avg_r']:+.2f}R)"
                + (
                    f", best in {pair_s['best_regime']}"
                    if pair_s.get("best_regime")
                    else ""
                )
            )

        at_s = learning_ctx.get("asset_type_stats")

        if at_s:
            lines.append(
                f"{signal.get('type', '').upper()} class: {at_s['win_rate'] * 100:.0f}% WR "
                f"avg {at_s['avg_r']:+.2f}R ({at_s['total_trades']} trades)"
            )

        grade_acc = learning_ctx.get("grade_accuracy", {})

        if grade_acc:
            grade_parts = []

            for g in ["A+", "A", "B", "C"]:
                if g in grade_acc and grade_acc[g]["trades"] >= 2:
                    s = grade_acc[g]

                    grade_parts.append(
                        f"{g}:{s['win_rate'] * 100:.0f}%WR/{s['avg_r']:+.1f}R"
                    )

            if grade_parts:
                lines.append(f"Grade calibration: {' | '.join(grade_parts)}")

        failures = learning_ctx.get("recent_failures", [])

        if failures:
            fail_summary = "; ".join(
                f"{f['pair']} {f['grade']} {f['regime']} R={f['r']:.1f}"
                for f in failures[:3]
                if f.get("pair")
            )

            if fail_summary:
                lines.append(f"Recent losses: {fail_summary}")

        factors = learning_ctx.get("factor_reliability", [])
        if factors:
            factor_summary = " | ".join(
                f"{f['factor']} ({f['count']} trades, avg W:{f['avg_win']} L:{f['avg_loss']})"
                for f in factors[:6]
            )
            if factor_summary:
                lines.append(f"Top Pattern Reliability: {factor_summary}")

        top_votes = learning_ctx.get("top_votes", [])
        if top_votes:
            vote_summary = " | ".join(
                f"{v['vote']} ({v['count']} trades, WR:{int(v['win_rate'] * 100)}%)"
                for v in top_votes[:4]
            )
            if vote_summary:
                lines.append(f"Best Indicator Combos: {vote_summary}")

    return "\n".join(lines)


def _parse_ai_json(text: str, pair: str = "?") -> dict | None:
    """Parse JSON from AI response using multiple fallback strategies."""
    import re as _re

    _parsed = None

    # Attempt 1: code-fence extraction
    if "```" in text:
        for p in text.split("```"):
            p = p.strip()
            if p.startswith("json"):
                p = p[4:].strip()
            if p.startswith("{"):
                try:
                    _parsed = json.loads(p[: p.rfind("}") + 1])
                    break
                except json.JSONDecodeError:
                    pass

    # Attempt 2: regex for outermost JSON object
    if _parsed is None:
        _m = _re.search(r"\{[^{}]*(?:\{[^{}]*\}[^{}]*)*\}", text)
        if _m:
            try:
                _parsed = json.loads(_m.group())
            except json.JSONDecodeError:
                pass

    # Attempt 3: first { to last }
    if _parsed is None:
        start = text.find("{")
        end = text.rfind("}") + 1
        if start >= 0 and end > start:
            try:
                _parsed = json.loads(text[start:end])
            except json.JSONDecodeError:
                pass

    return _parsed


def run_ai(
    signal: dict,
    news_ctx: dict | None = None,
    style_pref: str = "auto",
    portfolio_heat: float = 0.0,
    drawdown_pct: float = 0.0,
    learning_ctx: dict | None = None,
) -> dict:
    """Send signal data to xAI Grok for Marcus Reid AI analysis. Returns parsed JSON dict."""

    if not CONFIG.get("XAI_API_KEY") or CONFIG["XAI_API_KEY"] == "YOUR_XAI_API_KEY":
        log.error("[AI] xAI API key is None or not configured!")

        return {"error": "xAI API key not configured"}

    try:
        log.info(f"[AI] Analyzing {signal['pair']}...")

        import openai

        c = openai.OpenAI(api_key=CONFIG["XAI_API_KEY"], base_url="https://api.x.ai/v1")

        style_labels = {
            "scalp": "SCALP â€” focus on H1 exhaustion, tight 1.5R, quick execution",
            "intraday": "INTRADAY â€” H4+H1 alignment, same-session execution, 2-3R",
            "swing": "SWING â€” D1 trend dominance, EMA200 slope, 4-6R multi-day hold",
        }

        if style_pref == "auto":
            _sc = signal.get("confluenceScore", 0)
            _max = signal.get("maxScore", 3.0) or 3.0
            _pct = (_sc / _max * 100) if _max > 0 else 0

            style_pref = (
                "swing" if _pct >= 75 else "intraday" if _pct >= 50 else "scalp"
            )
        msg = _build_signal_message(
            signal,
            news_ctx,
            style_pref,
            style_labels,
            portfolio_heat=portfolio_heat,
            drawdown_pct=drawdown_pct,
            learning_ctx=learning_ctx,
        )

        result = None

        # Try structured outputs first (guaranteed valid JSON)
        if CONFIG.get("AI_STRUCTURED_OUTPUTS", True):
            try:
                from ai_schemas import EngineAResponse

                completion = c.beta.chat.completions.parse(
                    model=CONFIG["XAI_MODEL"],
                    max_tokens=900,
                    messages=[
                        {"role": "system", "content": EXPERT_PROMPT},
                        {"role": "user", "content": msg},
                    ],
                    response_format=EngineAResponse,
                )
                if completion.choices[0].message.parsed:
                    result = completion.choices[0].message.parsed.model_dump()
                    log.debug(f"[AI] {signal['pair']}: structured output success")
            except Exception as _so_err:
                log.debug(
                    f"[AI] {signal['pair']}: structured output failed ({_so_err}), using fallback"
                )

        # Fallback to Responses API + manual parsing
        if result is None:
            r = c.responses.create(
                model=CONFIG["XAI_MODEL"],
                max_output_tokens=900,
                input=[
                    {"role": "system", "content": EXPERT_PROMPT},
                    {"role": "user", "content": msg},
                ],
            )
            t = r.output_text.strip()
            result = _parse_ai_json(t, signal["pair"])

            if result is None:
                log.error(
                    f"[AI] {signal['pair']}: could not parse JSON from response: {t[:200]}"
                )
                return {"error": "AI response was not valid JSON"}

        # Validate required keys

        _required = {"grade", "edgeProbability", "riskLevel"}

        _missing = _required - set(result.keys())

        if _missing:
            log.warning(f"[AI] {signal['pair']}: parsed JSON missing keys {_missing}")

        log.info(
            f"[AI] {signal['pair']} => Grade:{result.get('grade', '?')} Prob:{result.get('edgeProbability', '?')}% Risk:{result.get('riskLevel', '?')} | {str(result.get('verdict', ''))[:60]}"
        )

        return result

    except Exception as e:
        log.error(f"[AI] ERROR for {signal.get('pair', '?')}: {e}")

        return {"error": str(e)}


def backtest_pair(pair, style="auto"):
    """Walk-forward backtest on D1/H4 bars with slippage, regime tagging, and Monte Carlo DD simulation."""

    requested_style = _normalize_style(style)

    effective_style = _effective_backtest_style(pair, requested_style)

    if effective_style not in ("swing", "intraday", "scalp"):
        return {"error": f"Unsupported backtest style: {requested_style}"}

    log.info(
        f"[BT] {pair['display']} fetching data... style={requested_style}->{effective_style}"
    )

    try:
        import pandas as pd

        sym = pair["symbol"]

        def df_to_candles(df):

            return [
                {
                    "time": str(idx),
                    "open": float(r["Open"]),
                    "high": float(r["High"]),
                    "low": float(r["Low"]),
                    "close": float(r["Close"]),
                    "vol": float(r.get("Volume", 0)),
                }
                for idx, r in df.iterrows()
            ]

        _ptype = pair.get("type", "")

        if pair["source"] == "binance":
            # Crypto: paginated H4/H1 for 2+ years of data

            d1_raw = fetch_binance(sym, "1d", 1000)

            h4_raw = fetch_binance_paginated(sym, "4h", 5000)

            h1_raw = fetch_binance_paginated(sym, "1h", 2000)

        elif _ptype == "forex":
            # Forex: EODHD D1 + EODHD intraday H1 (from/to 730d) → use D1 directly, yfinance fallback

            d1_raw = _extract_candles(fetch_eodhd(pair, "D1", 600)) or fetch_candles(
                pair, "D1", 600
            )

            h4_raw, h1_raw = _fetch_eodhd_intraday_bt(pair, days=730)

            if not h4_raw or not h1_raw:
                _pg_ticker = _polygon_ticker_for_pair(pair)
                if _pg_ticker:
                    log.info(
                        f"[BT] {pair['display']}: EODHD intraday failed, trying Polygon"
                    )
                    _pg_h4 = _extract_candles(fetch_polygon(pair, "H4", 5000))
                    _pg_h1 = _extract_candles(fetch_polygon(pair, "H1", 5000))
                    h4_raw = h4_raw or _pg_h4
                    h1_raw = h1_raw or _pg_h1
                if not h4_raw or not h1_raw:
                    _yf_sym = _yfinance_symbol_for_pair(pair)
                    if _yf_sym:
                        log.info(f"[BT] {pair['display']}: trying yfinance fallback")
                        _yf_h4, _yf_h1 = _fetch_bt_yfinance(_yf_sym)
                        h4_raw = h4_raw or _yf_h4
                        h1_raw = h1_raw or _yf_h1

        elif _ptype in ("stock", "commodity", "index"):
            # Stocks/Commodities/Indices: EODHD D1 + EODHD intraday (730d)
            # Fallback chain: EODHD → Polygon (commodities) → yfinance
            d1_raw = _extract_candles(fetch_eodhd(pair, "D1", 600)) or fetch_candles(
                pair, "D1", 600
            )

            h4_raw, h1_raw = _fetch_eodhd_intraday_bt(pair, days=730)

            if not h4_raw or not h1_raw:
                # Try Polygon first for commodities (better data quality than yfinance)
                _pg_ticker = _polygon_ticker_for_pair(pair)
                if _pg_ticker and _ptype == "commodity":
                    log.info(
                        f"[BT] {pair['display']}: EODHD intraday failed, trying Polygon"
                    )
                    _pg_h4 = _extract_candles(fetch_polygon(pair, "H4", 5000))
                    _pg_h1 = _extract_candles(fetch_polygon(pair, "H1", 5000))
                    h4_raw = h4_raw or _pg_h4
                    h1_raw = h1_raw or _pg_h1
                # Twelvedata — better commodity/metal history than yfinance (free, reliable)
                # Only fires for commodity pairs that have a Twelvedata symbol mapping
                _h4_thin = not h4_raw or len(h4_raw or []) < 500
                _h1_thin = not h1_raw or len(h1_raw or []) < 500
                if (_h4_thin or _h1_thin) and _ptype == "commodity":
                    try:
                        from twelvedata_feed import fetch_twelvedata_bt

                        _td_h4, _td_h1 = fetch_twelvedata_bt(pair["display"], days=730)
                        if _td_h4 and len(_td_h4) > len(h4_raw or []):
                            h4_raw = _td_h4
                            log.info(
                                f"[BT] {pair['display']}: Twelvedata H4 {len(h4_raw)} bars"
                            )
                        elif not h4_raw:
                            h4_raw = _td_h4
                        if _td_h1 and len(_td_h1) > len(h1_raw or []):
                            h1_raw = _td_h1
                            log.info(
                                f"[BT] {pair['display']}: Twelvedata H1 {len(h1_raw)} bars"
                            )
                        elif not h1_raw:
                            h1_raw = _td_h1
                    except Exception as _td_err:
                        log.debug(
                            f"[BT] Twelvedata failed for {pair['display']}: {_td_err}"
                        )

                # yfinance as final fallback — also triggers when Polygon returns thin data (<500 H4 bars)
                # Polygon free plan caps H4 history for C:XAUUSD etc. at ~38 days, which is
                # unusable: all 228 bars postdate most D1 entries, indicators compute out-of-context.
                _h4_thin = not h4_raw or len(h4_raw or []) < 500
                _h1_thin = not h1_raw or len(h1_raw or []) < 500
                if _h4_thin or _h1_thin:
                    _yf_sym = _yfinance_symbol_for_pair(pair)
                    if _yf_sym:
                        log.info(
                            f"[BT] {pair['display']}: H4={len(h4_raw or [])} H1={len(h1_raw or [])} bars — trying yfinance for better coverage"
                        )
                        _yf_h4, _yf_h1 = _fetch_bt_yfinance(_yf_sym)
                        if _yf_h4 and len(_yf_h4) > len(h4_raw or []):
                            h4_raw = _yf_h4
                        elif not h4_raw:
                            h4_raw = _yf_h4
                        if _yf_h1 and len(_yf_h1) > len(h1_raw or []):
                            h1_raw = _yf_h1
                        elif not h1_raw:
                            h1_raw = _yf_h1

        else:
            d1_raw = fetch_candles(pair, "D1", 600)

            h4_raw = fetch_candles(pair, "H4", 1000)

            h1_raw = fetch_candles(pair, "H1", 1000)

        if not d1_raw:
            return {"error": f"No D1 data for {pair['display']}"}

        if not h4_raw or not h1_raw:
            return {"error": f"No H4/H1 data for {pair['display']}"}

        if len(d1_raw) < 230:
            return {
                "error": f"Insufficient D1 history for {pair['display']} ({len(d1_raw)} bars)"
            }

        if effective_style == "swing" and len(h4_raw) < 250:
            return {
                "error": f"Insufficient H4 history for swing backtest ({len(h4_raw)} bars, need 250+) — Polygon free plan may cap intraday history. Try adding a yfinanceSymbol override or EODHD intraday key."
            }

        if effective_style == "intraday" and len(h4_raw) < 260:
            return {
                "error": f"Insufficient H4 history for {pair['display']} ({len(h4_raw)} bars, need 260+)"
            }

        if effective_style == "scalp" and len(h1_raw) < 260:
            return {
                "error": f"Insufficient H1 history for {pair['display']} ({len(h1_raw)} bars)"
            }

        h4_times = pd.to_datetime(
            [c["time"] for c in h4_raw], utc=True, errors="coerce"
        )

        h1_times = pd.to_datetime(
            [c["time"] for c in h1_raw], utc=True, errors="coerce"
        )

    except Exception as e:
        return {"error": f"Data fetch failed: {e}"}

    # N8: Session-variable slippage â€” forex widens during Asian/off-hours

    _BASE_SLIP = {
        "forex": 0.0001,
        "crypto": 0.002,
        "commodity": 0.001,
        "stock": 0.001,
        "index": 0.001,
    }

    def _get_slippage(bar, ptype):

        base = _BASE_SLIP.get(ptype, 0.001)

        if ptype == "forex":
            # Estimate session from bar time string (D1 bars don't have hour, use base)

            t = bar.get("time", "")

            try:
                h = int(t[11:13]) if len(t) > 13 else -1

            except (ValueError, IndexError):
                h = -1

            if 0 <= h < 7 or h >= 22:  # Asian / off-hours
                return base * 1.8  # wider spread

            elif 13 <= h < 16:  # London/NY overlap
                return base * 0.7  # tightest spread

        return base

    # Shared init

    trades = []
    equity = 1.0
    equity_curve = [1.0]

    _ptype = pair["type"]

    _h4_need = max(50, CONFIG["H4_CANDLES"])

    _h1_need = max(50, CONFIG["H1_CANDLES"])

    funnel = {
        "total_setups": 0,
        "fail_score": 0,
        "fail_macro": 0,
        "fail_regime": 0,
        "taken": 0,
        "skip_window": 0,
    }

    _recent_scores = []  # CR3: rolling score history for adaptive percentile threshold

    _pair_max_score = _max_score_for_pair(pair)  # Pre-compute for score-based sizing

    if effective_style == "swing":
        # --- SWING D1 LOOP â€” UNCHANGED ---

        MIN_BARS = 220
        COOLDOWN = 3
        MAX_OPEN = 3  # R5: max concurrent positions

        total_bars = len(d1_raw)

        i = MIN_BARS
        last_exit_bar = 0
        open_positions = 0

        # R4: Walk-forward split â€” 70% in-sample, 30% out-of-sample

        _oos_start = MIN_BARS + int((total_bars - MIN_BARS) * 0.7)

        while i < total_bars - 1:
            if i - last_exit_bar < COOLDOWN:
                i += 1
                continue

            # R5: Max concurrent positions cap

            if open_positions >= MAX_OPEN:
                i += 1
                continue

            d1_window = d1_raw[i - MIN_BARS : i]

            entry_ts = pd.to_datetime(d1_raw[i]["time"], utc=True, errors="coerce")

            if pd.isna(entry_ts):
                i += 1
                continue

            intraday_cutoff = entry_ts + pd.Timedelta(days=1)

            h4_window = [
                bar
                for bar, ts in zip(h4_raw, h4_times)
                if not pd.isna(ts) and ts < intraday_cutoff
            ][-_h4_need:]

            h1_window = [
                bar
                for bar, ts in zip(h1_raw, h1_times)
                if not pd.isna(ts) and ts < intraday_cutoff
            ][-_h1_need:]

            if len(h4_window) < 50 or len(h1_window) < 50:
                funnel["skip_window"] += 1
                i += 1
                continue

            try:
                d1i = calc_indicators_with_normalized(
                    d1_window, pair.get("type", "stock")
                )

                h4i = calc_indicators_with_normalized(
                    h4_window, pair.get("type", "stock")
                )

                # Inject fib_proximity so structure factor is non-None during backtest
                try:
                    _bt_fib = calc_fib(h4_window)
                    _bt_h4_close = h4_window[-1]["close"] if h4_window else None
                    if _bt_fib and _bt_h4_close:
                        h4i["snap"]["fib_proximity"] = calc_fib_proximity(
                            float(_bt_h4_close), _bt_fib
                        )
                except Exception:
                    pass

                h1i = calc_indicators_with_normalized(
                    h1_window, pair.get("type", "stock")
                )

                vols = [c["vol"] for c in h1_window]
                vsma = calc_sma(vols, 20)

                vr = vols[-1] / vsma[-1] if vsma and vsma[-1] and vsma[-1] > 0 else 1.0

                stoch = calc_stochastic(
                    h4_window, 5, 3, 3
                )  # TA-Lib STOCH standard: fastK=5, slowK=3, slowD=3

                # Route forex pairs to dedicated forex scoring engine in backtest
                if pair.get("type") == "forex":
                    from forex_scoring import compute_forex_score

                    _min_forex = CONFIG.get("MIN_FOREX_CONFLUENCE", 0.40)
                    _fx = compute_forex_score(
                        d1_snap=d1i["snap"],
                        h4_snap=h4i["snap"],
                        h1_snap=h1i["snap"],
                        h1_candles=h1_window,
                        pair=pair,
                        bar_time=d1_raw[i][
                            "time"
                        ],  # use actual current D1 bar datetime
                        backtest_mode=True,  # bypass session filter in backtest
                        h4_candles=h4_window,
                    )
                    res = {
                        "final_score": _fx.final_score,
                        "direction": _fx.direction,
                        "factor_scores": _fx.components,
                        "regime": {
                            "state": 1,
                            "label": _fx.signal_type,
                        },  # Match calc_confluence format
                        "signal_type": _fx.signal_type,
                        "score": _fx.final_score,  # Add compatibility field for backtest
                        "trendState": _fx.signal_type,  # Add compatibility field
                    }
                    direction = _fx.direction
                    bt_min = _min_forex
                else:
                    res = calc_confluence(
                        d1i,
                        h4i,
                        h1i,
                        vr,
                        stoch,
                        pair,
                        "neutral",
                        d1_candles=d1_window,
                        h4_candles=h4_window,
                        h1_candles=h1_window,
                        volume_threshold=get_pair_profile(pair).get(
                            "volume_threshold",
                            CONFIG.get(
                                "VOLUME_THRESHOLD_BACKTEST", CONFIG["VOLUME_THRESHOLD"]
                            ),
                        ),
                        bar_time=h4_window[-1].get("time") if h4_window else None,
                    )

            except Exception:
                i += 1
                continue

            funnel["total_setups"] += 1

            _ts = res.get("trendState", "UNKNOWN")

            # F2: Use BT_MIN as BT threshold natively.
            # Forex bt_min already set by routing block (MIN_FOREX_CONFLUENCE) — do not overwrite.
            if pair.get("type") != "forex":
                bt_min = get_pair_profile(pair).get(
                    "bt_min", CONFIG["BT_MIN"].get(_ptype, 0.4)
                )

            _recent_scores.append(res["score"])

            if res["score"] < bt_min:
                funnel["fail_score"] += 1
                i += 1
                continue

            direction = res["direction"]

            # Realistic entry: signal on bar i close, enter at bar i+1 open (no lookahead)

            if i + 1 >= total_bars:
                i += 1
                continue

            entry_bar = d1_raw[i + 1]

            raw_entry = entry_bar.get("open", entry_bar["close"])

            slip = raw_entry * _get_slippage(entry_bar, _ptype)

            entry = raw_entry + slip if direction == "LONG" else raw_entry - slip

            atr = _atr_for_levels(d1i, h4i, h1i, pair=pair, style=effective_style)

            if not atr or atr == 0:
                i += 1
                continue

            # C4: Use shared calc_levels (deduplicates with analyze_pair)

            _bt_regime_state = (
                res.get("regime", {}).get("state") if res.get("regime") else None
            )

            lvl = calc_levels(
                entry, atr, direction, _ptype, regime_state=_bt_regime_state
            )

            sl = lvl["sl"]
            tp1 = lvl["tp1"]
            tp2 = lvl["tp2"]

            sl_mult = lvl["mults"]["sl"]
            lvl["mults"]["tp1"]
            tp2_mult = lvl["mults"]["tp2"]

            rr1 = lvl["rr1"]

            # Validate levels are finite and meaningful — NaN/0 levels cause ghost OPEN trades

            if not all(math.isfinite(v) and v > 0 for v in (sl, tp1, tp2)):
                i += 1
                continue

            if (
                abs(entry - sl) < entry * 0.0001
            ):  # SL less than 0.01% from entry — invalid
                i += 1
                continue

            # V3: Structure-based stops for crypto — use wider of ATR-stop vs swing-stop

            if _ptype == "crypto":
                _recent = d1_window[-10:]

                if direction == "LONG":
                    swing_sl = min(c["low"] for c in _recent)

                    if swing_sl < sl:
                        sl = swing_sl  # wider stop lets noise breathe

                else:
                    swing_sl = max(c["high"] for c in _recent)

                    if swing_sl > sl:
                        sl = swing_sl

                rr1 = abs(tp1 - entry) / abs(sl - entry) if abs(sl - entry) > 0 else 0

            # T1: Volatility-adjusted sizing â€” if ATR > 1.5x its 20-bar SMA, reduce size 30%

            _atr_series = calc_atr(
                [c["high"] for c in d1_window],
                [c["low"] for c in d1_window],
                [c["close"] for c in d1_window],
                14,
            )

            _atr_sma = calc_sma([v for v in _atr_series if v is not None], 20)

            _vol_adj = 1.0

            _valid_atr_sma = [v for v in _atr_sma if v is not None]

            if _valid_atr_sma and _valid_atr_sma[-1] and _valid_atr_sma[-1] > 0:
                if atr > _valid_atr_sma[-1] * 1.5:
                    _vol_adj = 0.7

            _score_factor = (
                max(0.25, min(1.0, res["score"] / _pair_max_score))
                if _pair_max_score > 0
                else 1.0
            )

            outcome = "OPEN"
            result_r = 0.0
            exit_bar = i

            for j in range(i + 1, min(i + 21, total_bars)):
                bar = d1_raw[j]

                if direction == "LONG":
                    # A2: worst-case intrabar â€” SL checked before TP (conservative)

                    if bar["low"] <= sl:
                        _sl_slip_r = (
                            _get_slippage(bar, _ptype) * sl / (atr * sl_mult)
                            if atr and sl_mult
                            else 0
                        )

                        outcome = "SL"
                        result_r = round(-1.0 - _sl_slip_r, 4)
                        exit_bar = j
                        break

                    if bar["high"] >= tp2:
                        outcome = "TP2"
                        result_r = (tp2_mult / sl_mult) - (slip / (atr * sl_mult))
                        exit_bar = j
                        break

                    if bar["high"] >= tp1:
                        outcome = "TP1"
                        result_r = rr1 - (slip / (atr * sl_mult))
                        exit_bar = j
                        break

                else:
                    # A2: worst-case intrabar â€” SL checked before TP (conservative)

                    if bar["high"] >= sl:
                        _sl_slip_r = (
                            _get_slippage(bar, _ptype) * sl / (atr * sl_mult)
                            if atr and sl_mult
                            else 0
                        )

                        outcome = "SL"
                        result_r = round(-1.0 - _sl_slip_r, 4)
                        exit_bar = j
                        break

                    if bar["low"] <= tp2:
                        outcome = "TP2"
                        result_r = (tp2_mult / sl_mult) - (slip / (atr * sl_mult))
                        exit_bar = j
                        break

                    if bar["low"] <= tp1:
                        outcome = "TP1"
                        result_r = rr1 - (slip / (atr * sl_mult))
                        exit_bar = j
                        break

            if outcome == "OPEN":
                # Force-close at last forward bar â€” record actual P&L vs recording a ghost 0R

                _last_fwd = d1_raw[min(i + 20, total_bars - 1)]

                _exit_px = _last_fwd["close"]

                _sl_dist = abs(entry - sl)

                if _sl_dist > 0 and math.isfinite(_exit_px):
                    result_r = (
                        (_exit_px - entry) / _sl_dist
                        if direction == "LONG"
                        else (entry - _exit_px) / _sl_dist
                    )

                    result_r = round(max(-5.0, min(5.0, result_r)), 2)  # cap outliers

                else:
                    result_r = 0.0

                outcome = "TIMEOUT"

            risk_mult = CONFIG["RISK_MULT"].get(_ptype, 1.0)

            # F3: Deduct round-trip exchange fee (entry + exit commission) from result_r

            _sl_dist_sw = abs(entry - sl)

            if _sl_dist_sw > 0:
                _fee_r_sw = CONFIG["FEE_PCT"].get(_ptype, 0.0004) * entry / _sl_dist_sw

                result_r = round(result_r - _fee_r_sw, 4)

            # T1: Apply volatility adjustment and score-based sizing to position size

            equity_change = (
                result_r * CONFIG["RISK_PCT"] * risk_mult * _vol_adj * _score_factor
            )

            equity = round(equity * (1 + equity_change), 6)

            equity_curve.append(round(equity, 4))

            # R2: Tag trade with regime for segmentation

            _regime = (
                _ts
                if _ts in ("TRENDING", "DEVELOPING", "RANGING", "DEAD RANGING")
                else "UNKNOWN"
            )

            open_positions += 1

            funnel["taken"] += 1

            trades.append(
                {
                    "date": entry_bar["time"][:10],
                    "pair": pair["display"],
                    "direction": direction,
                    "score": res["score"],
                    "entry": round(entry, 6),
                    "sl": round(sl, 6),
                    "tp1": round(tp1, 6),
                    "tp2": round(tp2, 6),
                    "outcome": outcome,
                    "resultR": round(result_r, 2),
                    "regime": _regime,
                    "oos": i >= _oos_start,
                    "volAdj": _vol_adj,
                }
            )

            if outcome not in ("OPEN",):
                last_exit_bar = exit_bar

                open_positions -= 1

            i = exit_bar + 1 if outcome != "OPEN" else i + 1

    elif effective_style == "intraday":  # walk H4 bars
        MIN_H4 = 250
        COOLDOWN = 2
        MAX_HOLD = 12
        MAX_OPEN = 3

        total_h4 = len(h4_raw)

        i = MIN_H4
        last_exit_bar = 0
        open_positions = 0

        _oos_start = MIN_H4 + int((total_h4 - MIN_H4) * 0.7)

        # Pre-parse timestamps once

        h4_ts = pd.to_datetime([c["time"] for c in h4_raw], utc=True, errors="coerce")

        d1_ts = pd.to_datetime([c["time"] for c in d1_raw], utc=True, errors="coerce")

        while i < total_h4 - 1:
            if i - last_exit_bar < COOLDOWN:
                i += 1
                continue

            if open_positions >= MAX_OPEN:
                i += 1
                continue

            h4_window = h4_raw[i - MIN_H4 : i]

            entry_ts = h4_ts[i]

            if pd.isna(entry_ts):
                i += 1
                continue

            # H1 alignment: all H1 bars before this H4 bar's timestamp + 4h

            h1_cutoff = entry_ts + pd.Timedelta(hours=4)

            h1_window = [
                bar
                for bar, ts in zip(h1_raw, h1_times)
                if not pd.isna(ts) and ts < h1_cutoff
            ][-_h1_need:]

            # D1 context: real D1 data up to this point for Weinstein/regime

            d1_ctx = [
                bar
                for bar, ts in zip(d1_raw, d1_ts)
                if not pd.isna(ts) and ts <= entry_ts
            ][-220:]

            if len(h1_window) < 50 or len(d1_ctx) < 50:
                i += 1
                continue

            try:
                h4i = calc_indicators_with_normalized(
                    h4_window, pair.get("type", "stock")
                )

                # Inject fib_proximity so structure factor is non-None during backtest
                try:
                    _bt_fib = calc_fib(h4_window)
                    _bt_h4_close = h4_window[-1]["close"] if h4_window else None
                    if _bt_fib and _bt_h4_close:
                        h4i["snap"]["fib_proximity"] = calc_fib_proximity(
                            float(_bt_h4_close), _bt_fib
                        )
                except Exception:
                    pass

                h1i = calc_indicators_with_normalized(
                    h1_window, pair.get("type", "stock")
                )

                d1i_ctx = calc_indicators_with_normalized(
                    d1_ctx, pair.get("type", "stock")
                )

                vols = [c["vol"] for c in h1_window]
                vsma = calc_sma(vols, 20)

                vr = vols[-1] / vsma[-1] if vsma and vsma[-1] and vsma[-1] > 0 else 1.0

                stoch = calc_stochastic(
                    h1_window, 5, 3, 3
                )  # TA-Lib STOCH standard: fastK=5, slowK=3, slowD=3

                res = calc_confluence(
                    d1i_ctx,
                    h4i,
                    h1i,
                    vr,
                    stoch,
                    pair,
                    "neutral",
                    d1_candles=d1_ctx,
                    h4_candles=h4_window,
                    h1_candles=h1_window,
                    volume_threshold=get_pair_profile(pair).get(
                        "volume_threshold",
                        CONFIG.get(
                            "VOLUME_THRESHOLD_BACKTEST", CONFIG["VOLUME_THRESHOLD"]
                        ),
                    ),
                    bar_time=h4_window[-1].get("time") if h4_window else None,
                )

            except Exception:
                i += 1
                continue

            funnel["total_setups"] += 1

            _ts = res.get("trendState", "UNKNOWN")

            # F2: Use BT_MIN as BT threshold natively
            bt_min = get_pair_profile(pair).get(
                "bt_min", CONFIG["BT_MIN"].get(_ptype, 0.4)
            )

            _recent_scores.append(res["score"])

            if res["score"] < bt_min:
                funnel["fail_score"] += 1
                i += 1
                continue

            direction = res["direction"]

            # Realistic entry: signal on bar i close, enter at bar i+1 open (no lookahead)

            if i + 1 >= total_h4:
                i += 1
                continue

            entry_bar = h4_raw[i + 1]

            raw_entry = entry_bar.get("open", entry_bar["close"])

            slip = raw_entry * _get_slippage(entry_bar, _ptype)

            entry = raw_entry + slip if direction == "LONG" else raw_entry - slip

            atr = _atr_for_levels(
                d1i_ctx, h4i, h1i, pair=pair, style=effective_style
            )

            if not atr or atr == 0:
                i += 1
                continue

            _bt_regime_state2 = (
                res.get("regime", {}).get("state") if res.get("regime") else None
            )

            lvl = calc_levels(
                entry, atr, direction, _ptype, regime_state=_bt_regime_state2
            )

            sl = lvl["sl"]
            tp1 = lvl["tp1"]
            tp2 = lvl["tp2"]

            sl_mult = lvl["mults"]["sl"]
            lvl["mults"]["tp1"]
            tp2_mult = lvl["mults"]["tp2"]

            rr1 = lvl["rr1"]

            if not all(math.isfinite(v) and v > 0 for v in (sl, tp1, tp2)):
                i += 1
                continue

            if abs(entry - sl) < entry * 0.0001:
                i += 1
                continue

            if _ptype == "crypto":
                _recent = h4_window[-10:]

                if direction == "LONG":
                    swing_sl = min(c["low"] for c in _recent)

                    if swing_sl < sl:
                        sl = swing_sl

                else:
                    swing_sl = max(c["high"] for c in _recent)

                    if swing_sl > sl:
                        sl = swing_sl

                rr1 = abs(tp1 - entry) / abs(sl - entry) if abs(sl - entry) > 0 else 0

            _atr_series = calc_atr(
                [c["high"] for c in h4_window],
                [c["low"] for c in h4_window],
                [c["close"] for c in h4_window],
                14,
            )

            _atr_sma = calc_sma([v for v in _atr_series if v is not None], 20)

            _vol_adj = 1.0

            _valid_atr_sma = [v for v in _atr_sma if v is not None]

            if _valid_atr_sma and _valid_atr_sma[-1] and _valid_atr_sma[-1] > 0:
                if atr > _valid_atr_sma[-1] * 1.5:
                    _vol_adj = 0.7

            _score_factor = (
                max(0.25, min(1.0, res["score"] / _pair_max_score))
                if _pair_max_score > 0
                else 1.0
            )

            outcome = "OPEN"
            result_r = 0.0
            exit_bar = i

            for j in range(i + 1, min(i + MAX_HOLD + 1, total_h4)):
                bar = h4_raw[j]

                if direction == "LONG":
                    if bar["low"] <= sl:
                        _sl_slip_r = (
                            _get_slippage(bar, _ptype) * sl / (atr * sl_mult)
                            if atr and sl_mult
                            else 0
                        )

                        outcome = "SL"
                        result_r = round(-1.0 - _sl_slip_r, 4)
                        exit_bar = j
                        break

                    if bar["high"] >= tp2:
                        outcome = "TP2"
                        result_r = (tp2_mult / sl_mult) - (slip / (atr * sl_mult))
                        exit_bar = j
                        break

                    if bar["high"] >= tp1:
                        outcome = "TP1"
                        result_r = rr1 - (slip / (atr * sl_mult))
                        exit_bar = j
                        break

                else:
                    if bar["high"] >= sl:
                        _sl_slip_r = (
                            _get_slippage(bar, _ptype) * sl / (atr * sl_mult)
                            if atr and sl_mult
                            else 0
                        )

                        outcome = "SL"
                        result_r = round(-1.0 - _sl_slip_r, 4)
                        exit_bar = j
                        break

                    if bar["low"] <= tp2:
                        outcome = "TP2"
                        result_r = (tp2_mult / sl_mult) - (slip / (atr * sl_mult))
                        exit_bar = j
                        break

                    if bar["low"] <= tp1:
                        outcome = "TP1"
                        result_r = rr1 - (slip / (atr * sl_mult))
                        exit_bar = j
                        break

            if outcome == "OPEN":
                _last_fwd = h4_raw[min(i + MAX_HOLD, total_h4 - 1)]

                _exit_px = _last_fwd["close"]

                _sl_dist = abs(entry - sl)

                if _sl_dist > 0 and math.isfinite(_exit_px):
                    result_r = (
                        (_exit_px - entry) / _sl_dist
                        if direction == "LONG"
                        else (entry - _exit_px) / _sl_dist
                    )

                    result_r = round(max(-5.0, min(5.0, result_r)), 2)

                else:
                    result_r = 0.0

                outcome = "TIMEOUT"

            risk_mult = CONFIG["RISK_MULT"].get(_ptype, 1.0)

            # F3: Deduct round-trip exchange fee from result_r

            _sl_dist_id = abs(entry - sl)

            if _sl_dist_id > 0:
                _fee_r_id = CONFIG["FEE_PCT"].get(_ptype, 0.0004) * entry / _sl_dist_id

                result_r = round(result_r - _fee_r_id, 4)

            equity_change = (
                result_r * CONFIG["RISK_PCT"] * risk_mult * _vol_adj * _score_factor
            )

            equity = round(equity * (1 + equity_change), 6)

            equity_curve.append(round(equity, 4))

            _regime = (
                _ts
                if _ts in ("TRENDING", "DEVELOPING", "RANGING", "DEAD RANGING")
                else "UNKNOWN"
            )

            open_positions += 1

            funnel["taken"] += 1

            trades.append(
                {
                    "date": entry_bar["time"][:10],
                    "pair": pair["display"],
                    "direction": direction,
                    "score": res["score"],
                    "entry": round(entry, 6),
                    "sl": round(sl, 6),
                    "tp1": round(tp1, 6),
                    "tp2": round(tp2, 6),
                    "outcome": outcome,
                    "resultR": round(result_r, 2),
                    "regime": _regime,
                    "oos": i >= _oos_start,
                    "volAdj": _vol_adj,
                }
            )

            if outcome not in ("OPEN",):
                last_exit_bar = exit_bar

                open_positions -= 1

            i = exit_bar + 1 if outcome != "OPEN" else i + 1

    elif effective_style == "scalp":  # H1 bar walk-forward
        MIN_H1 = 250
        COOLDOWN = 1
        MAX_HOLD = 6
        MAX_OPEN = 3

        total_h1 = len(h1_raw)

        i = MIN_H1
        last_exit_bar = 0
        open_positions = 0

        _oos_start = MIN_H1 + int((total_h1 - MIN_H1) * 0.7)

        h1_ts_sc = pd.to_datetime(
            [c["time"] for c in h1_raw], utc=True, errors="coerce"
        )

        d1_ts_sc = pd.to_datetime(
            [c["time"] for c in d1_raw], utc=True, errors="coerce"
        )

        h4_ts_sc = pd.to_datetime(
            [c["time"] for c in h4_raw], utc=True, errors="coerce"
        )

        while i < total_h1 - 1:
            if i - last_exit_bar < COOLDOWN:
                i += 1
                continue

            if open_positions >= MAX_OPEN:
                i += 1
                continue

            h1_window = h1_raw[i - MIN_H1 : i]

            entry_ts = h1_ts_sc[i]

            if pd.isna(entry_ts):
                i += 1
                continue

            # H4 context: all H4 bars before this H1 bar

            h4_ctx = [
                bar
                for bar, ts in zip(h4_raw, h4_ts_sc)
                if not pd.isna(ts) and ts <= entry_ts
            ][-250:]

            # D1 context: real D1 data up to this point for Weinstein/regime

            d1_ctx = [
                bar
                for bar, ts in zip(d1_raw, d1_ts_sc)
                if not pd.isna(ts) and ts <= entry_ts
            ][-220:]

            if len(h4_ctx) < 50 or len(d1_ctx) < 50:
                i += 1
                continue

            try:
                h1i = calc_indicators_with_normalized(
                    h1_window, pair.get("type", "stock")
                )

                h4i_ctx = calc_indicators_with_normalized(
                    h4_ctx, pair.get("type", "stock")
                )

                d1i_ctx = calc_indicators_with_normalized(
                    d1_ctx, pair.get("type", "stock")
                )

                vols = [c["vol"] for c in h1_window]
                vsma = calc_sma(vols, 20)

                vr = vols[-1] / vsma[-1] if vsma and vsma[-1] and vsma[-1] > 0 else 1.0

                stoch = calc_stochastic(
                    h1_window, 5, 3, 3
                )  # TA-Lib STOCH standard: fastK=5, slowK=3, slowD=3

                res = calc_confluence(
                    d1i_ctx,
                    h4i_ctx,
                    h1i,
                    vr,
                    stoch,
                    pair,
                    "neutral",
                    d1_candles=d1_ctx,
                    h4_candles=h4_ctx,
                    h1_candles=h1_window,
                    volume_threshold=get_pair_profile(pair).get(
                        "volume_threshold",
                        CONFIG.get(
                            "VOLUME_THRESHOLD_BACKTEST", CONFIG["VOLUME_THRESHOLD"]
                        ),
                    ),
                    bar_time=h1_window[-1].get("time") if h1_window else None,
                )

            except Exception:
                i += 1
                continue

            funnel["total_setups"] += 1

            _ts = res.get("trendState", "UNKNOWN")

            # F2: Use BT_MIN as BT threshold natively (must equal MIN_CONFLUENCE_CLASS)

            bt_min = get_pair_profile(pair).get(
                "bt_min", CONFIG["BT_MIN"].get(_ptype, 0.70)
            )

            _recent_scores.append(res["score"])

            if res["score"] < bt_min:
                funnel["fail_score"] += 1
                i += 1
                continue

            direction = res["direction"]

            if i + 1 >= total_h1:
                i += 1
                continue

            entry_bar = h1_raw[i + 1]

            raw_entry = entry_bar.get("open", entry_bar["close"])

            slip = raw_entry * _get_slippage(entry_bar, _ptype)

            entry = raw_entry + slip if direction == "LONG" else raw_entry - slip

            atr = _atr_for_levels(
                d1i_ctx, h4i_ctx, h1i, pair=pair, style=effective_style
            )

            if not atr or atr == 0:
                i += 1
                continue

            _bt_regime_state3 = (
                res.get("regime", {}).get("state") if res.get("regime") else None
            )

            lvl = calc_levels(
                entry, atr, direction, _ptype, regime_state=_bt_regime_state3
            )

            sl = lvl["sl"]
            tp1 = lvl["tp1"]
            tp2 = lvl["tp2"]

            sl_mult = lvl["mults"]["sl"]
            lvl["mults"]["tp1"]
            tp2_mult = lvl["mults"]["tp2"]

            rr1 = lvl["rr1"]

            if not all(math.isfinite(v) and v > 0 for v in (sl, tp1, tp2)):
                i += 1
                continue

            if abs(entry - sl) < entry * 0.0001:
                i += 1
                continue

            if _ptype == "crypto":
                _recent = h1_window[-10:]

                if direction == "LONG":
                    swing_sl = min(c["low"] for c in _recent)

                    if swing_sl < sl:
                        sl = swing_sl

                else:
                    swing_sl = max(c["high"] for c in _recent)

                    if swing_sl > sl:
                        sl = swing_sl

                rr1 = abs(tp1 - entry) / abs(sl - entry) if abs(sl - entry) > 0 else 0

            _atr_series = calc_atr(
                [c["high"] for c in h1_window],
                [c["low"] for c in h1_window],
                [c["close"] for c in h1_window],
                14,
            )

            _atr_sma = calc_sma([v for v in _atr_series if v is not None], 20)

            _vol_adj = 1.0

            _valid_atr_sma = [v for v in _atr_sma if v is not None]

            if _valid_atr_sma and _valid_atr_sma[-1] and _valid_atr_sma[-1] > 0:
                if atr > _valid_atr_sma[-1] * 1.5:
                    _vol_adj = 0.7

            _score_factor = (
                max(0.25, min(1.0, res["score"] / _pair_max_score))
                if _pair_max_score > 0
                else 1.0
            )

            outcome = "OPEN"
            result_r = 0.0
            exit_bar = i

            for j in range(i + 1, min(i + MAX_HOLD + 1, total_h1)):
                bar = h1_raw[j]

                if direction == "LONG":
                    if bar["low"] <= sl:
                        _sl_slip_r = (
                            _get_slippage(bar, _ptype) * sl / (atr * sl_mult)
                            if atr and sl_mult
                            else 0
                        )

                        outcome = "SL"
                        result_r = round(-1.0 - _sl_slip_r, 4)
                        exit_bar = j
                        break

                    if bar["high"] >= tp2:
                        outcome = "TP2"
                        result_r = (tp2_mult / sl_mult) - (slip / (atr * sl_mult))
                        exit_bar = j
                        break

                    if bar["high"] >= tp1:
                        outcome = "TP1"
                        result_r = rr1 - (slip / (atr * sl_mult))
                        exit_bar = j
                        break

                else:
                    if bar["high"] >= sl:
                        _sl_slip_r = (
                            _get_slippage(bar, _ptype) * sl / (atr * sl_mult)
                            if atr and sl_mult
                            else 0
                        )

                        outcome = "SL"
                        result_r = round(-1.0 - _sl_slip_r, 4)
                        exit_bar = j
                        break

                    if bar["low"] <= tp2:
                        outcome = "TP2"
                        result_r = (tp2_mult / sl_mult) - (slip / (atr * sl_mult))
                        exit_bar = j
                        break

                    if bar["low"] <= tp1:
                        outcome = "TP1"
                        result_r = rr1 - (slip / (atr * sl_mult))
                        exit_bar = j
                        break

            if outcome == "OPEN":
                _last_fwd = h1_raw[min(i + MAX_HOLD, total_h1 - 1)]

                _exit_px = _last_fwd["close"]

                _sl_dist = abs(entry - sl)

                if _sl_dist > 0 and math.isfinite(_exit_px):
                    result_r = (
                        (_exit_px - entry) / _sl_dist
                        if direction == "LONG"
                        else (entry - _exit_px) / _sl_dist
                    )

                    result_r = round(max(-5.0, min(5.0, result_r)), 2)

                else:
                    result_r = 0.0

                outcome = "TIMEOUT"

            risk_mult = CONFIG["RISK_MULT"].get(_ptype, 1.0)

            # F3: Deduct round-trip exchange fee from result_r

            _sl_dist_sc = abs(entry - sl)

            if _sl_dist_sc > 0:
                _fee_r_sc = CONFIG["FEE_PCT"].get(_ptype, 0.0004) * entry / _sl_dist_sc

                result_r = round(result_r - _fee_r_sc, 4)

            equity_change = (
                result_r * CONFIG["RISK_PCT"] * risk_mult * _vol_adj * _score_factor
            )

            equity = round(equity * (1 + equity_change), 6)

            equity_curve.append(round(equity, 4))

            _regime = (
                _ts
                if _ts in ("TRENDING", "DEVELOPING", "RANGING", "DEAD RANGING")
                else "UNKNOWN"
            )

            open_positions += 1

            funnel["taken"] += 1

            trades.append(
                {
                    "date": entry_bar["time"][:10],
                    "pair": pair["display"],
                    "direction": direction,
                    "score": res["score"],
                    "entry": round(entry, 6),
                    "sl": round(sl, 6),
                    "tp1": round(tp1, 6),
                    "tp2": round(tp2, 6),
                    "outcome": outcome,
                    "resultR": round(result_r, 2),
                    "regime": _regime,
                    "oos": i >= _oos_start,
                    "volAdj": _vol_adj,
                }
            )

            if outcome not in ("OPEN",):
                last_exit_bar = exit_bar

                open_positions -= 1

            i = exit_bar + 1 if outcome != "OPEN" else i + 1

    if not trades:
        _max_score_seen = round(max(_recent_scores), 3) if _recent_scores else 0
        _bt_min_used = get_pair_profile(pair).get(
            "bt_min", CONFIG["BT_MIN"].get(_ptype, 0.4)
        )
        _h4_bars = len(h4_raw) if h4_raw else 0
        _h1_bars = len(h1_raw) if h1_raw else 0
        return {
            "error": (
                f"No signals generated for {pair['display']} — "
                f"setups={funnel['total_setups']} skip_window={funnel['skip_window']} "
                f"fail_score={funnel['fail_score']} bt_min={_bt_min_used} "
                f"max_score_seen={_max_score_seen} "
                f"d1={len(d1_raw)} h4={_h4_bars} h1={_h1_bars}"
            )
        }

    wins = [
        t
        for t in trades
        if t["outcome"] in ("TP1", "TP2")
        or (t["outcome"] == "TIMEOUT" and t["resultR"] > 0)
    ]

    losses = [
        t
        for t in trades
        if t["outcome"] == "SL" or (t["outcome"] == "TIMEOUT" and t["resultR"] <= 0)
    ]

    gross_profit = sum(t["resultR"] for t in wins)

    gross_loss = abs(sum(t["resultR"] for t in losses))

    win_rate = round(len(wins) / len(trades) * 100, 1) if trades else 0

    profit_factor = round(gross_profit / gross_loss, 2) if gross_loss > 0 else None

    total_r = round(sum(t["resultR"] for t in trades), 2)

    r_values = [t["resultR"] for t in trades]

    avg_r = round(total_r / len(trades), 3) if trades else 0

    _var = (
        sum((r - avg_r) ** 2 for r in r_values) / (len(r_values) - 1)
        if len(r_values) > 1
        else 0
    )

    sqn = (
        round(max(-10, min(10, (avg_r / _var**0.5) * (len(trades) ** 0.5))), 2)
        if len(trades) > 1 and avg_r != 0 and _var > 0
        else 0
    )

    peak = 1.0
    max_dd = 0.0

    for e in equity_curve:
        if e > peak:
            peak = e

        dd = (peak - e) / peak

        if dd > max_dd:
            max_dd = dd

    max_dd_pct = round(max_dd * 100, 1)

    # B2a: Recovery time — max bars from drawdown start to equity peak recovery

    _peak_eq = 1.0
    _in_dd_since = 0
    max_recovery_bars = 0

    for _idx, _eq in enumerate(equity_curve):
        if _eq >= _peak_eq:
            if _in_dd_since:
                max_recovery_bars = max(max_recovery_bars, _idx - _in_dd_since)

                _in_dd_since = 0

            _peak_eq = _eq

        elif not _in_dd_since:
            _in_dd_since = _idx

    # A4: Expectancy decomposition

    avg_win = round(sum(t["resultR"] for t in wins) / len(wins), 3) if wins else 0

    avg_loss = (
        round(sum(t["resultR"] for t in losses) / len(losses), 3) if losses else 0
    )

    r_skew = round(avg_win / abs(avg_loss), 2) if avg_loss != 0 else None

    # Sharpe ratio (annualized): mean(R) / std(R) * sqrt(trades_per_year)

    # Sortino ratio: mean(R) / downside_std(R) * sqrt(trades_per_year)

    if len(trades) >= 2:
        try:
            _dur_days = (
                pd.to_datetime(trades[-1]["date"]) - pd.to_datetime(trades[0]["date"])
            ).days

            _trades_per_year = len(trades) / max(1, _dur_days) * 365

        except Exception:
            _trades_per_year = float(len(trades))

    else:
        _trades_per_year = 1.0

    _std_r = _var**0.5 if _var > 0 else 0

    sharpe = round(avg_r / _std_r * (_trades_per_year**0.5), 2) if _std_r > 0 else 0

    _downside = [r for r in r_values if r < 0]

    _down_var = (
        sum(r**2 for r in _downside) / (len(_downside) - 1) if len(_downside) > 1 else 0
    )

    _down_std = _down_var**0.5

    sortino = (
        round(avg_r / _down_std * (_trades_per_year**0.5), 2) if _down_std > 0 else 0
    )

    # B2: Monte Carlo drawdown simulation â€” 500 random shuffles of trade sequence

    # Transforms single-path DD into distribution: P5=best case, P95=worst case

    import random as _rnd

    _risk_mult = CONFIG["RISK_MULT"].get(pair["type"], 1.0)

    _mc_dds = []

    for _ in range(500):
        _shuffled = r_values[:]
        _rnd.shuffle(_shuffled)

        _eq = 1.0
        _pk = 1.0
        _mdd = 0.0

        for _r in _shuffled:
            _eq *= 1 + _r * CONFIG["RISK_PCT"] * _risk_mult

            if _eq > _pk:
                _pk = _eq

            _d = (_pk - _eq) / _pk

            if _d > _mdd:
                _mdd = _d

        _mc_dds.append(_mdd)

    _mc_dds.sort()
    _nc = len(_mc_dds)

    mc_dd = {
        "p5": round(_mc_dds[int(_nc * 0.05)] * 100, 1),
        "p50": round(_mc_dds[int(_nc * 0.50)] * 100, 1),
        "p95": round(_mc_dds[int(_nc * 0.95)] * 100, 1),
    }

    # B3: Score band win rate tracking â€” which confluence scores actually deliver edge?

    score_bands = {}

    for band_label, lo_b, hi_b in [
        ("6-7", 6, 7),
        ("7-8", 7, 8),
        ("8-9", 8, 9),
        ("9+", 9, 99),
    ]:
        band_trades = [t for t in trades if lo_b <= t["score"] < hi_b]

        if band_trades:
            bw = sum(1 for t in band_trades if t["outcome"] in ("TP1", "TP2"))

            score_bands[band_label] = {
                "trades": len(band_trades),
                "wr": round(bw / len(band_trades) * 100, 1),
            }

    # R2: Regime segmentation stats â€” track performance by market regime

    regime_stats = {}

    for regime in ["TRENDING", "DEVELOPING", "RANGING", "DEAD RANGING"]:
        rt = [t for t in trades if t.get("regime") == regime]

        if rt:
            rw = sum(1 for t in rt if t["outcome"] in ("TP1", "TP2"))

            regime_stats[regime] = {
                "trades": len(rt),
                "wr": round(rw / len(rt) * 100, 1),
                "expectancy": round(sum(t["resultR"] for t in rt) / len(rt), 3),
            }

    # R4: Walk-forward split â€” in-sample vs out-of-sample SQN comparison

    is_trades = [t for t in trades if not t.get("oos", False)]

    oos_trades = [t for t in trades if t.get("oos", False)]

    def _calc_sqn(tlist):

        if len(tlist) < 2:
            return 0

        rv = [t["resultR"] for t in tlist]

        _a = sum(rv) / len(rv)

        _v = sum((r - _a) ** 2 for r in rv) / (len(rv) - 1) if len(rv) > 1 else 0

        return (
            round(max(-10, min(10, (_a / _v**0.5) * (len(rv) ** 0.5))), 2)
            if _a != 0 and _v > 0
            else 0
        )

    is_sqn = _calc_sqn(is_trades)

    oos_sqn = _calc_sqn(oos_trades)

    _is_insufficient = len(is_trades) < 5

    wf_split = {
        "is_trades": len(is_trades),
        "oos_trades": len(oos_trades),
        "is_sqn": None if _is_insufficient else is_sqn,
        "oos_sqn": oos_sqn,
        "overfit_flag": oos_sqn < is_sqn * 0.5
        if is_sqn > 0 and len(oos_trades) >= 3
        else False,
        "wf_note": "IS period had insufficient setups (<5 trades) — OOS result is fully out-of-sample"
        if _is_insufficient
        else None,
    }

    # F5: Buy-and-hold benchmark — passive return over full D1 data period

    try:
        bh_return = (
            round((d1_raw[-1]["close"] / d1_raw[0]["close"] - 1) * 100, 2)
            if d1_raw and d1_raw[0]["close"]
            else None
        )

    except Exception:
        bh_return = None

    log.info(
        f"[BT] {pair['display']} done: {len(trades)} trades, WR {win_rate}%, PF {profit_factor}, Expect {avg_r}R, SQN {sqn}, Sharpe {sharpe}, Sortino {sortino}, IS:{is_sqn}/OOS:{oos_sqn}, MC-P95 DD {mc_dd['p95']}%, MaxRec {max_recovery_bars} bars"
    )

    # Save backtest result to DB
    try:
        import sqlite3 as _sq

        with _sq.connect(_AUDIT_DB) as _con:
            _con.execute(
                "INSERT INTO backtest_results "
                "(run_date,pair,asset_type,engine,trades,win_rate,profit_factor,"
                "expectancy,sqn,sharpe,sortino,is_score,oos_score,max_dd_pct,bt_min,atr_source) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    __import__("datetime").datetime.utcnow().isoformat(),
                    pair["display"],
                    pair.get("type", ""),
                    "forex_scoring"
                    if pair.get("type") == "forex"
                    else "factor_scoring",
                    len(trades),
                    round(win_rate, 4),
                    round(profit_factor, 4),
                    round(avg_r, 4),
                    round(sqn, 4),
                    round(sharpe, 4),
                    round(sortino, 4),
                    round(is_sqn, 4),
                    round(oos_sqn, 4),
                    round(max_dd_pct, 4),
                    float(bt_min),
                    "D1_ATR" if pair.get("type") != "crypto" else "H4_ATR",
                ),
            )
        log.info(
            f"[BT-DB] Saved: {pair['display']} SQN={sqn:.2f} ({len(trades)} trades) → audit.db"
        )
    except Exception as _dbe:
        log.debug(f"[BT-DB] Failed to save result: {_dbe}")

    return {
        "pair": pair["display"],
        "symbol": pair["symbol"],
        "type": pair["type"],
        "totalTrades": len(trades),
        "wins": len(wins),
        "losses": len(losses),
        "winRate": win_rate,
        "profitFactor": profit_factor,
        "totalR": total_r,
        "expectancy": avg_r,
        "sqn": sqn,
        "sharpe": sharpe,
        "sortino": sortino,
        "avgWin": avg_win,
        "avgLoss": avg_loss,
        "rSkew": r_skew,
        "maxDrawdownPct": max_dd_pct,
        "maxRecoveryBars": max_recovery_bars,
        "mcDD": mc_dd,
        "scoreBands": score_bands,
        "regimeStats": regime_stats,
        "wfSplit": wf_split,
        "funnel": funnel,
        "btStyle": effective_style,
        "btStyleRequested": requested_style,
        "bhReturn": bh_return,
        "volumeThreshold": {
            "bt": CONFIG.get("VOLUME_THRESHOLD_BACKTEST", 1.2),
            "live": CONFIG.get("VOLUME_THRESHOLD", 1.5),
        },
        "pairMaxScore": _pair_max_score,
        "equityCurve": equity_curve,
        "trades": trades[-50:],
    }


def _format_backtest_results(trades, pair, engine_type="NAKED"):
    """Format Engine B backtest results to match Engine A's response schema exactly."""
    if not trades:
        return {"error": f"No signals generated for {pair['display']} in NAKED mode"}

    # Use resultR as primary (Engine A schema); fall back to r_multiple for older records
    def _r(t):
        return t.get("resultR", t.get("r_multiple", 0.0))

    def _sqn_for(_trades):
        if len(_trades) <= 1:
            return 0
        _values = [_r(t) for t in _trades]
        _avg = sum(_values) / len(_values)
        _var_local = (
            sum((r - _avg) ** 2 for r in _values) / (len(_values) - 1)
            if len(_values) > 1
            else 0
        )
        return (
            round(max(-10, min(10, (_avg / _var_local**0.5) * (len(_trades) ** 0.5))), 2)
            if len(_trades) > 1 and _avg != 0 and _var_local > 0
            else 0
        )

    wins = [t for t in trades if _r(t) > 0]
    losses = [t for t in trades if _r(t) <= 0]
    gross_profit = sum(_r(t) for t in wins)
    gross_loss = abs(sum(_r(t) for t in losses))
    win_rate = round(len(wins) / len(trades) * 100, 1)
    profit_factor = round(gross_profit / gross_loss, 2) if gross_loss > 0 else None
    total_r = round(sum(_r(t) for t in trades), 2)
    r_values = [_r(t) for t in trades]
    avg_r = round(total_r / len(trades), 3) if trades else 0
    avg_win = round(sum(_r(t) for t in wins) / len(wins), 3) if wins else 0
    avg_loss = round(sum(_r(t) for t in losses) / len(losses), 3) if losses else 0

    # SQN
    _var = (
        sum((r - avg_r) ** 2 for r in r_values) / (len(r_values) - 1)
        if len(r_values) > 1
        else 0
    )
    sqn = (
        round(max(-10, min(10, (avg_r / _var**0.5) * (len(trades) ** 0.5))), 2)
        if len(trades) > 1 and avg_r != 0 and _var > 0
        else 0
    )

    # R-skew (avg_win / |avg_loss|)
    r_skew = round(avg_win / abs(avg_loss), 2) if avg_loss != 0 else None

    # Sharpe / Sortino (daily-ish approximation)
    _std = _var**0.5
    sharpe = round(avg_r / _std, 3) if _std > 0 else 0
    neg_dev = sum((r - avg_r) ** 2 for r in r_values if r < 0)
    sortino_denom = (neg_dev / len(r_values)) ** 0.5 if neg_dev > 0 else 0
    sortino = round(avg_r / sortino_denom, 3) if sortino_denom > 0 else 0

    # Simple equity curve (cumulative R)
    equity_curve = []
    eq = 1.0
    for rv in r_values:
        eq = round(eq * (1 + rv * 0.01), 4)
        equity_curve.append(eq)

    # Max drawdown
    peak = 1.0
    max_dd_pct = 0.0
    for eq_val in equity_curve:
        if eq_val > peak:
            peak = eq_val
        dd = (peak - eq_val) / peak * 100
        if dd > max_dd_pct:
            max_dd_pct = dd
    max_dd_pct = round(max_dd_pct, 2)

    _peak_eq = 1.0
    _recovery_start = None
    max_recovery_bars = 0
    for _idx, eq_val in enumerate(equity_curve):
        if eq_val >= _peak_eq:
            if _recovery_start is not None:
                max_recovery_bars = max(max_recovery_bars, _idx - _recovery_start)
                _recovery_start = None
            _peak_eq = eq_val
        elif _recovery_start is None:
            _recovery_start = _idx

    is_trades = [t for t in trades if not t.get("oos")]
    oos_trades = [t for t in trades if t.get("oos")]
    wf_split = {
        "is_trades": len(is_trades),
        "oos_trades": len(oos_trades),
        "is_sqn": _sqn_for(is_trades),
        "oos_sqn": _sqn_for(oos_trades) if oos_trades else None,
        "overfit_flag": False,
        "wf_note": "Engine B walk-forward split uses trade oos flags",
    }

    # Monte Carlo DD stub
    mc_dd = {"p50": max_dd_pct * 1.0, "p95": max_dd_pct * 1.5, "p99": max_dd_pct * 2.0}

    result = {
        # ── Core identity (matches Engine A) ──────────────────────────────────
        "pair": pair["display"],
        "symbol": pair["symbol"],
        "type": pair["type"],
        # ── Trade stats ───────────────────────────────────────────────────────
        "totalTrades": len(trades),
        "wins": len(wins),
        "losses": len(losses),
        "winRate": win_rate,
        "profitFactor": profit_factor,
        "totalR": total_r,
        "expectancy": avg_r,
        "sqn": sqn,
        "sharpe": sharpe,
        "sortino": sortino,
        "avgWin": avg_win,
        "avgLoss": avg_loss,
        "rSkew": r_skew,
        # ── Risk/DD ───────────────────────────────────────────────────────────
        "maxDrawdownPct": max_dd_pct,
        "maxRecoveryBars": max_recovery_bars,
        "mcDD": mc_dd,
        # ── Analysis ──────────────────────────────────────────────────────────
        "scoreBands": {},
        "regimeStats": {},
        "funnel": {},
        "wfSplit": wf_split,
        # ── Style / engine ────────────────────────────────────────────────────
        "btStyle": "naked",
        "btStyleRequested": "naked",
        "engine": engine_type,
        # ── Benchmarks ────────────────────────────────────────────────────────
        "bhReturn": None,
        "pairMaxScore": None,
        "volumeThreshold": {
            "bt": CONFIG.get("VOLUME_THRESHOLD_BACKTEST", 1.2),
            "live": CONFIG.get("VOLUME_THRESHOLD", 1.5),
        },
        # ── Curves & trades (CRITICAL — JS crashes without these) ─────────────
        "equityCurve": equity_curve,
        "trades": trades[-50:],
    }

    liquidity_pct = sum(1 for t in trades if t.get("liquidity_sweep")) / len(trades) * 100
    zone_touch_pct = sum(1 for t in trades if t.get("zone_touched")) / len(trades) * 100
    breakout_pct = (
        sum(
            1
            for t in trades
            if t.get("trigger_pattern") in ("INSIDE_BREAK", "STRONG_CLOSE", "ENGULFING")
        )
        / len(trades)
        * 100
    )
    rejection_pct = (
        sum(1 for t in trades if t.get("trigger_pattern") == "REJECTION")
        / len(trades)
        * 100
    )
    trigger_counts = {}
    for t in trades:
        _pattern = str(t.get("trigger_pattern") or "NONE")
        trigger_counts[_pattern] = trigger_counts.get(_pattern, 0) + 1
    dominant_trigger = (
        max(trigger_counts.items(), key=lambda kv: kv[1])[0] if trigger_counts else "NONE"
    )

    result.update(
        {
            "liquidity_sweep_pct": round(liquidity_pct, 1),
            "zone_touch_pct": round(zone_touch_pct, 1),
            "breakout_entry_pct": round(breakout_pct, 1),
            "rejection_entry_pct": round(rejection_pct, 1),
            "dominant_trigger_pattern": dominant_trigger,
        }
    )

    if pair.get("type") == "forex":
        result["engine"] = "ENGINE_B_FOREX"

    return result


# ── NEW: Engine B (Naked) Backtest Function ───────────────────────────────
def backtest_pair_naked(pair: dict, style: str = "naked"):
    """Separate backtest loop for NakedEngine (Engine B).
    Completely isolated from Engine A backtest_pair()."""
    from market_structure import engine as naked_engine
    from indicators import calc_atr

    candles_d1 = fetch_candles(pair, "D1", CONFIG.get("D1_CANDLES", 600))
    candles_h4 = fetch_candles(pair, "H4", CONFIG.get("H4_CANDLES", 1000))
    candles_h1 = fetch_candles(pair, "H1", CONFIG.get("H1_CANDLES", 2000))

    if not candles_d1 or not candles_h4 or not candles_h1:
        return {
            "success": False,
            "error": "Insufficient candle data (D1, H4, or H1 missing).",
            "trades": [],
            "totalTrades": 0,
        }

    candles_d1 = candles_d1[:-1] if len(candles_d1) > 1 else candles_d1
    candles_h4 = candles_h4[:-1] if len(candles_h4) > 1 else candles_h4
    candles_h1 = candles_h1[:-1] if len(candles_h1) > 1 else candles_h1

    requested_style = "auto" if style == "naked" else style
    resolved_style, style_profile = _naked_scan_style_profile(requested_style)
    d1_times = [c.get("time", c.get("datetime", "")) for c in candles_d1]
    h1_times = [c.get("time", c.get("datetime", "")) for c in candles_h1]
    oos_start = 50 + int(max(0, len(candles_h4) - 50) * 0.7)

    trades = []
    for i in range(50, len(candles_h4) - 5):
        h4_time = candles_h4[i].get("time") or candles_h4[i].get("datetime")
        if not h4_time:
            continue

        h4_ctx = candles_h4[: i + 1]
        d1_slice_end = bisect.bisect_right(d1_times, h4_time)
        h1_slice_end = bisect.bisect_right(h1_times, h4_time)
        if d1_slice_end < 20 or h1_slice_end < 20:
            continue

        d1_ctx = candles_d1[:d1_slice_end]
        h1_ctx = candles_h1[:h1_slice_end]
        current_price = float(candles_h4[i]["close"])

        atr_list = calc_atr(
            [c["high"] for c in h4_ctx],
            [c["low"] for c in h4_ctx],
            [c["close"] for c in h4_ctx],
            14,
        )
        atr = atr_list[-1] if atr_list and atr_list[-1] else (current_price * 0.01)

        candidates = []
        for direction in ["LONG", "SHORT"]:
            res = naked_engine.analyze_structure(
                d1_ctx,
                h4_ctx,
                h1_ctx,
                current_price,
                direction,
                atr,
                "RANGING",
            )
            conf_data = naked_engine.calculate_confidence(
                res,
                current_price,
                direction,
                entry_candles=h1_ctx,
                style_profile=style_profile,
            )
            if conf_data["score"] < style_profile["min_score"] or not conf_data.get("passed"):
                continue

            entry = current_price
            sl = res.get("recommended_stop_loss")
            tp = res.get("recommended_take_profit")
            if sl is None or tp is None:
                continue

            rr = conf_data.get("rr", 0.0)
            if rr <= 0:
                sl_dist = abs(entry - sl)
                tp_dist = abs(tp - entry)
                rr = (tp_dist / sl_dist) if sl_dist > 0 else 0.0
            if rr <= 0:
                continue

            candidates.append(
                {
                    "direction": direction,
                    "score": conf_data["score"],
                    "pct": conf_data["pct"],
                    "entry": entry,
                    "sl": sl,
                    "tp": tp,
                    "rr": rr,
                    "res": res,
                    "conf": conf_data,
                }
            )

        if not candidates:
            continue

        best = max(candidates, key=lambda x: (x["score"], x["rr"], x["pct"]))
        direction = best["direction"]
        entry = best["entry"]
        sl = best["sl"]
        tp = best["tp"]
        target_rr = best["rr"]

        outcome = "TIMEOUT"
        r_multiple = 0.0
        future_window = candles_h4[i + 1 : min(i + 13, len(candles_h4))]
        for future in future_window:
            if direction == "LONG":
                if float(future["low"]) <= sl:
                    outcome = "SL"
                    r_multiple = -1.0
                    break
                if float(future["high"]) >= tp:
                    outcome = "TP1"
                    r_multiple = round(target_rr, 2)
                    break
            else:
                if float(future["high"]) >= sl:
                    outcome = "SL"
                    r_multiple = -1.0
                    break
                if float(future["low"]) <= tp:
                    outcome = "TP1"
                    r_multiple = round(target_rr, 2)
                    break

        if outcome == "TIMEOUT" and future_window:
            last_close = float(future_window[-1]["close"])
            risk = abs(entry - sl)
            if risk > 0:
                open_r = ((last_close - entry) / risk) if direction == "LONG" else ((entry - last_close) / risk)
                r_multiple = round(max(-1.0, min(target_rr, open_r)), 2)

        bar_date = candles_h4[i].get("time", "")[:10] if candles_h4[i].get("time") else ""
        trades.append(
            {
                "date": bar_date,
                "pair": pair["display"],
                "direction": direction,
                "score": best["score"],
                "entry": round(float(entry), 6),
                "sl": round(float(sl), 6),
                "tp1": round(float(tp), 6),
                "tp2": round(float(tp), 6),
                "outcome": outcome,
                "resultR": round(r_multiple, 2),
                "regime": best["res"].get("macro_swing_sequence", "RANGING"),
                "oos": i >= oos_start,
                "volAdj": 1.0,
                "r_multiple": r_multiple,
                "liquidity_sweep": best["res"].get("liquidity_sweep", False),
                "fvg_overlap": best["res"].get("fvg_overlap", False),
                "trigger_pattern": best["conf"].get("trigger_pattern", "NONE"),
                "zone_touched": best["res"].get("zone_touched", False),
                "rr_target": round(target_rr, 2),
            }
        )

    result = _format_backtest_results(trades, pair, engine_type="NAKED")
    if "error" not in result:
        result["btStyle"] = resolved_style
        result["btStyleRequested"] = requested_style
        try:
            import sqlite3 as _sq

            _wf = result.get("wfSplit", {}) or {}
            _is_sqn = _wf.get("is_sqn")
            _oos_sqn = _wf.get("oos_sqn")
            with _sq.connect(_AUDIT_DB, timeout=15.0) as _con:
                _con.execute(
                    "INSERT INTO backtest_results "
                    "(run_date,pair,asset_type,engine,trades,win_rate,profit_factor,"
                    "expectancy,sqn,sharpe,sortino,is_score,oos_score,max_dd_pct,bt_min,atr_source,notes) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        datetime.utcnow().isoformat(),
                        pair["display"],
                        pair.get("type", ""),
                        "naked_engine",
                        result.get("totalTrades", 0),
                        result.get("winRate"),
                        result.get("profitFactor"),
                        result.get("expectancy"),
                        result.get("sqn"),
                        result.get("sharpe"),
                        result.get("sortino"),
                        round(_is_sqn, 4) if _is_sqn is not None else None,
                        round(_oos_sqn, 4) if _oos_sqn is not None else None,
                        result.get("maxDrawdownPct"),
                        style_profile.get("min_score"),
                        f"{style_profile.get('atr_tf', 'H4')}_ATR",
                        f"style={resolved_style};requested={requested_style}",
                    ),
                )
                _con.commit()
        except Exception as _e:
            log.warning(f"[ENGINE B BT] backtest_results write failed: {_e}")
    return result


def run_full_backtest(style="auto", asset_class: str | None = None):
    """Run backtest_pair in parallel. Optional asset_class filter (crypto/forex/stock/commodity/index)."""

    from concurrent.futures import ThreadPoolExecutor, as_completed

    _valid_classes = {"crypto", "forex", "stock", "commodity", "index"}

    _ac = asset_class.lower().strip() if asset_class else None

    if _ac and _ac not in _valid_classes:
        return {
            "success": False,
            "error": f"Invalid asset_class '{asset_class}'. Valid: {sorted(_valid_classes)}",
            "results": [],
            "errors": [],
            "totalPairs": 0,
        }

    _jse_syms = {p["symbol"] for p in JSE_PAIRS}

    pairs_to_test = [p for p in ALL_PAIRS if p["symbol"] not in _jse_syms]

    if _ac:
        pairs_to_test = [p for p in pairs_to_test if p.get("type") == _ac]

    results = []
    errors = []

    def _bt(pair):

        try:
            return pair, backtest_pair(pair, style=style)

        except Exception as e:
            return pair, {"error": str(e)}

    with ThreadPoolExecutor(max_workers=4) as pool:
        futures = {pool.submit(_bt, p): p for p in pairs_to_test}

        for fut in as_completed(futures):
            pair, r = fut.result()

            if "error" in r:
                errors.append({"pair": pair["display"], "error": r["error"]})

            else:
                results.append(r)

    results.sort(key=lambda x: x["sqn"] if x["sqn"] is not None else -999, reverse=True)

    return {
        "success": True,
        "results": results,
        "errors": errors,
        "totalPairs": len(pairs_to_test),
        "assetClass": _ac or "all",
    }


def _init_audit_db(db_path: str) -> None:
    """Create audit table if it doesn't exist, and migrate legacy schemas."""

    con = sqlite3.connect(db_path, timeout=15.0)
    con.execute("PRAGMA journal_mode=WAL")

    con.execute("""

        CREATE TABLE IF NOT EXISTS audit_log (

            id            INTEGER PRIMARY KEY AUTOINCREMENT,

            ts            TEXT NOT NULL,

            pair          TEXT,

            score         REAL,

            direction     TEXT,

            trend         TEXT,

            grade         TEXT,

            edge_prob     REAL,

            risk          TEXT,

            style         TEXT,

            entry_price   REAL,

            sl            REAL,

            tp            REAL,

            volume        REAL,

            regime        TEXT,

            risk_amount   REAL,

            risk_pct      REAL,

            ticket        TEXT

        )

    """)

    con.execute("""
        CREATE TABLE IF NOT EXISTS backtest_results (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            run_date        TEXT    NOT NULL,
            pair            TEXT    NOT NULL,
            asset_type      TEXT,
            engine          TEXT,
            trades          INTEGER,
            win_rate        REAL,
            profit_factor   REAL,
            expectancy      REAL,
            sqn             REAL,
            sharpe          REAL,
            sortino         REAL,
            is_score        REAL,
            oos_score       REAL,
            max_dd_pct      REAL,
            bt_min          REAL,
            atr_source      TEXT,
            notes           TEXT
        )
    """)
    con.execute(
        "CREATE INDEX IF NOT EXISTS idx_bt_pair ON backtest_results (pair, run_date)"
    )

    # Migrate: add new columns to existing tables that lack them

    existing = {row[1] for row in con.execute("PRAGMA table_info(audit_log)")}

    for col, defn in [
        ("entry_price", "REAL"),
        ("sl", "REAL"),
        ("tp", "REAL"),
        ("volume", "REAL"),
        ("regime", "TEXT"),
        ("risk_amount", "REAL"),
        ("risk_pct", "REAL"),
        ("ticket", "TEXT"),
        # Task 1 â€” outcome tracking
        ("exit_price", "REAL"),
        ("exit_time", "TEXT"),
        ("pnl", "REAL"),
        ("r_multiple", "REAL"),
        ("exit_reason", "TEXT"),
        ("holding_period_hours", "REAL"),
        # D1 self-improvement context
        ("asset_class", "TEXT"),
        ("score_pct", "REAL"),
        ("max_score", "REAL"),
        ("votes_json", "TEXT"),
        ("warnings_json", "TEXT"),
        ("weinstein", "TEXT"),
        ("trend_state", "TEXT"),
        ("adx_pct", "REAL"),
        ("btc_bias", "TEXT"),
        ("session_name", "TEXT"),
        ("error_tag", "TEXT"),  # AUTO-ERR: reason — set on failed auto-trade attempts
        ("fee_cost", "REAL"),  # Actual paid commission captured from exchange order
        (
            "factors_json",
            "TEXT",
        ),  # Factor scores + key indicators (COT, carry, microstructure, etc.)
    ]:
        if col not in existing:
            con.execute(f"ALTER TABLE audit_log ADD COLUMN {col} {defn}")

    con.commit()

    con.close()


_AUDIT_DB = os.path.join(os.path.dirname(os.path.abspath(__file__)), "audit.db")

_init_audit_db(_AUDIT_DB)


# ── AI Learning + Auto-Trader ─────────────────────────────────────────────

from ai_learning import init_learning_db  # noqa: E402

init_learning_db(_AUDIT_DB)


from auto_trader import auto_trader as _auto_trader  # noqa: E402

_auto_trader.configure(
    run_scan_fn=lambda style="auto", asset_class=None: run_full_scan(
        style, asset_class
    ),
    kill_switch_fn=lambda: _kill_switch,
    test_mode_fn=lambda: _test_mode,
    audit_db=_AUDIT_DB,
    config_fn=lambda: CONFIG,
)


app = Flask(__name__, static_folder="static")

app.config["PERMANENT_SESSION_LIFETIME"] = 86400  # 24 hours


# ── API authentication (shared-secret header) ────────────────────────────

_ATHENA_API_KEY = os.environ.get("ATHENA_API_KEY", "")  # set in .env to enable auth

_AUTH_EXEMPT = {"/", "/favicon.ico"}  # paths that don't require auth


# ── Lightweight rate limiter (no external dependency) ─────────────────────

_rate_limits: dict = {}  # ip -> [timestamps]

_RATE_WINDOW = 60  # seconds

_RATE_MAX_REQUESTS = 120  # max requests per window (2/sec average)

_RATE_EXECUTE_MAX = 5  # stricter limit for execution endpoints


# _json_safe imported from config.py — see that module for implementation


@app.before_request
def _auth_and_rate_limit():

    from flask import request as _req

    path = _req.path or ""

    ip = _req.remote_addr or "unknown"

    # Auth check — only enforced when ATHENA_API_KEY is set in .env

    if _ATHENA_API_KEY and path not in _AUTH_EXEMPT:
        provided = _req.headers.get("X-Sentinel-Key", "")

        if provided != _ATHENA_API_KEY:
            log.warning(
                f"[AUTH] {ip} rejected on {path} — invalid/missing X-Sentinel-Key"
            )

            return jsonify({"error": "Unauthorized — set X-Sentinel-Key header"}), 401

    # Rate limiting

    now = time.time()

    is_sensitive = (
        path.startswith("/api/execute")
        or path.startswith("/api/killswitch")
        or path.startswith("/api/webhook")
    )

    max_req = (
        (_RATE_EXECUTE_MAX * 4 if _test_mode else _RATE_EXECUTE_MAX)
        if is_sensitive
        else _RATE_MAX_REQUESTS
    )

    key = f"{ip}:{path}" if is_sensitive else ip

    if key not in _rate_limits:
        _rate_limits[key] = []

    _rate_limits[key] = [t for t in _rate_limits[key] if now - t < _RATE_WINDOW]

    if len(_rate_limits[key]) >= max_req:
        log.warning(f"[RATE] {ip} exceeded {max_req} req/{_RATE_WINDOW}s on {path}")

        return jsonify({"error": "Rate limit exceeded. Try again shortly."}), 429

    _rate_limits[key].append(now)


@app.route("/")
def index():
    resp = send_from_directory("static", "index.html")
    resp.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    resp.headers["Pragma"] = "no-cache"
    resp.headers["Expires"] = "0"
    return resp


@app.route("/api/scan", methods=["POST"])
def api_scan():

    d = request.get_json(force=True, silent=True) or {}

    return jsonify(
        _json_safe(
            run_full_scan(
                style=d.get("style", "auto"), asset_class=d.get("asset_class")
            )
        )
    )


@app.route("/api/analyze", methods=["POST"])
def api_analyze():

    # S1: Validate Flask JSON payload

    d = request.json

    if not d or not isinstance(d, dict) or "signal" not in d:
        return jsonify({"error": "Invalid payload: expected {signal: {...}}"}), 400

    sig = d["signal"]

    if not isinstance(sig, dict) or "pair" not in sig:
        return jsonify({"error": "Invalid signal object"}), 400

    if _kill_switch:
        return jsonify({"error": "Kill-switch active â€” system paused"}), 503

    try:
        news_ctx = sig.get("newsCtx") or fetch_news_context()

        style_pref = d.get("stylePreference", "auto")

        # Fetch live portfolio context so Claude knows current risk exposure

        _p_heat = 0.0

        _dd_pct = 0.0

        try:
            from risk_engine import _calc_portfolio_heat, _current_drawdown

            _sig_type = sig.get("type", "")

            if _sig_type == "crypto":
                from bybit_executor import bybit_get_account, bybit_get_positions

                _acct = bybit_get_account()

                _pos_resp = bybit_get_positions()

                _pos = (
                    _pos_resp.get("positions", [])
                    if isinstance(_pos_resp, dict)
                    else (_pos_resp or [])
                )

            else:
                from mt5_executor import mt5_get_account, mt5_get_positions

                _acct = mt5_get_account()

                _pos_resp = mt5_get_positions()

                _pos = (
                    _pos_resp.get("positions", [])
                    if isinstance(_pos_resp, dict)
                    else (_pos_resp or [])
                )

            if _acct and not _acct.get("error"):
                _p_heat = _calc_portfolio_heat(_pos, _acct["balance"])

                _dd_pct = _current_drawdown(_acct["equity"])

        except Exception as _ph_err:
            log.debug(f"[AI] portfolio heat/drawdown fetch failed: {_ph_err}")

        # Fetch AI learning context for this pair

        _learning_ctx = None

        if CONFIG.get("LEARNING_ENABLED", True):
            try:
                from ai_learning import get_ai_learning_context

                _learning_ctx = get_ai_learning_context(
                    pair=sig.get("pair", ""),
                    asset_type=sig.get("type", ""),
                    db_path=_AUDIT_DB,
                    lookback_days=CONFIG.get("LEARNING_LOOKBACK_DAYS", 90),
                )

            except Exception as _lce:
                log.debug(f"[LEARN] context fetch failed: {_lce}")

        if sig.get("type") in ("stock", "index"):
            try:
                from eodhd_enrichment import enrich_signal

                sig = enrich_signal(sig)

            except Exception as enc_err:
                log.debug(f"[ENRICH] failed: {enc_err}")

        result = run_ai(
            sig,
            news_ctx,
            style_pref,
            portfolio_heat=_p_heat,
            drawdown_pct=_dd_pct,
            learning_ctx=_learning_ctx,
        )

        # N9: Audit log â€” persist every AI analysis to SQLite

        try:
            _max_s = sig.get("maxScore", 3.0)

            _score_pct = (
                round(sig.get("confluenceScore", 0) / _max_s * 100, 1) if _max_s else 0
            )

            with sqlite3.connect(_AUDIT_DB, timeout=15.0) as _con:
                _factors = {
                    "scores": sig.get("factorScores"),
                    "weights": sig.get("factorWeights"),
                    "disabled": sig.get("disabledFactors"),
                    "regime": sig.get("regimeName"),
                }
                _con.execute(
                    "INSERT INTO audit_log(ts,pair,score,direction,trend,grade,edge_prob,risk,style,"
                    "asset_class,score_pct,max_score,votes_json,warnings_json,"
                    "weinstein,trend_state,adx_pct,btc_bias,session_name,regime,factors_json) "
                    "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        datetime.now(timezone.utc).isoformat(),
                        sig.get("pair"),
                        sig.get("confluenceScore"),
                        sig.get("direction"),
                        sig.get("trendState"),
                        result.get("grade"),
                        result.get("edgeProbability"),
                        result.get("riskLevel"),
                        style_pref,
                        sig.get("type"),
                        _score_pct,
                        _max_s,
                        json.dumps(sig.get("votes", {})),
                        json.dumps(sig.get("warnings", [])),
                        sig.get("weinsteinLabel"),
                        sig.get("trendState"),
                        sig.get("h4", {}).get("snap", {}).get("adxPct"),
                        sig.get("btcBias"),
                        sig.get("session", {}).get("name"),
                        sig.get("trendState"),
                        json.dumps(_factors),
                    ),
                )

                _con.commit()

        except Exception as _ae:
            log.warning(f"Audit DB write failed: {_ae}")

        return jsonify(result)

    except Exception as e:
        # S2: Sanitise exception — don't leak internal paths

        log.error(f"api_analyze error: {e}")

        return jsonify({"error": "Analysis failed"}), 500


def _current_btc_bias() -> str:
    btc_bias = "neutral"
    try:
        btc = fetch_candles(
            {"symbol": "BTCUSDT", "source": "binance"}, "D1", CONFIG["D1_CANDLES"]
        )
        if btc and len(btc) >= 200:
            s = calc_indicators(btc)["snap"]
            if s["ema21"] and s["ema50"] and s["ema200"]:
                if s["ema21"] > s["ema50"] > s["ema200"]:
                    btc_bias = "bullish"
                elif s["ema21"] < s["ema50"] < s["ema200"]:
                    btc_bias = "bearish"
    except Exception as e:
        log.debug(f"[BTC-BIAS] single-pair calc failed: {e}")
    return btc_bias


def _resolve_pair_from_signal(sig: dict) -> dict | None:
    symbol = sig.get("symbol") or sig.get("pair") or sig.get("display")
    if not symbol:
        return None
    pair_obj = next(
        (
            p
            for p in ALL_PAIRS
            if p.get("symbol") == symbol or p.get("display") == symbol
        ),
        None,
    )
    if pair_obj:
        return pair_obj
    return {
        "symbol": sig.get("symbol") or sig.get("pair"),
        "type": sig.get("type", "crypto"),
        "display": sig.get("display") or sig.get("pair") or symbol,
        "source": CONFIG.get("EXCHANGE_SOURCE", "binance"),
    }


def _naked_scan_style_profile(style: str | None) -> tuple[str, dict]:
    resolved = _normalize_style(style)
    if resolved == "auto":
        resolved = "scalp"
    profiles = {
        "scalp": {
            "min_score": 3.0,
            "min_room_atr": 0.35,
            "min_rr": 1.0,
            "fallback_rr": 1.4,
            "require_macro_align": False,
            "zone_tf": "H4",
            "entry_tf": "H1",
            "atr_tf": "H4",
        },
        "intraday": {
            "min_score": 4.0,
            "min_room_atr": 0.7,
            "min_rr": 1.2,
            "fallback_rr": 1.8,
            "require_macro_align": False,
            "zone_tf": "H4",
            "entry_tf": "H1",
            "atr_tf": "H4",
        },
        "swing": {
            "min_score": 5.0,
            "min_room_atr": 1.0,
            "min_rr": 1.6,
            "fallback_rr": 2.2,
            "require_macro_align": True,
            "zone_tf": "D1",
            "entry_tf": "H4",
            "atr_tf": "D1",
        },
    }
    cfg_profiles = (CONFIG.get("NAKED_ENGINE", {}) or {}).get("style_profiles", {}) or {}
    merged_profiles = {}
    for profile_name, defaults in profiles.items():
        merged_profiles[profile_name] = dict(defaults)
        cfg_override = cfg_profiles.get(profile_name, {})
        if isinstance(cfg_override, dict):
            merged_profiles[profile_name].update(cfg_override)
    return resolved, merged_profiles.get(resolved, merged_profiles["scalp"])


def _compute_naked_analysis(sig: dict, engine_a_ctx: dict = None):
    if not isinstance(sig, dict):
        return None, None, "Invalid signal"

    pair_obj = _resolve_pair_from_signal(sig)
    if not pair_obj:
        return None, None, "Invalid signal"

    direction = str(sig.get("direction", "LONG")).upper()
    if direction not in ("LONG", "SHORT"):
        direction = "LONG"

    def _enrich_engine_b_ai_payload(payload: dict) -> dict:
        enriched = dict(payload or {})

        def _fmt_level(value):
            try:
                return f"{float(value):,.4f}"
            except Exception:
                return "-"

        risk_level = str(enriched.get("riskLevel") or "").upper()
        if risk_level == "LOW":
            enriched["riskLevel"] = "Low"
        elif risk_level == "HIGH":
            enriched["riskLevel"] = "High"
        elif risk_level == "MEDIUM":
            enriched["riskLevel"] = "Medium"

        verdict = str(enriched.get("verdict") or "").strip()
        struct_verdict = str(res.get("structural_verdict") or "UNCLEAR")
        score = res.get("score")
        max_possible = res.get("max_possible")
        support_zone = res.get("nearest_support_zone") or {}
        resistance_zone = res.get("nearest_resistance_zone") or {}
        active_zone = support_zone if direction == "LONG" else resistance_zone
        rec_sl = res.get("recommended_stop_loss")
        rec_tp = res.get("recommended_take_profit")
        zone_lower = active_zone.get("lower")
        zone_upper = active_zone.get("upper")
        zone_center = active_zone.get("center")
        if zone_lower is not None and zone_upper is not None:
            entry_zone = f"{_fmt_level(zone_lower)} - {_fmt_level(zone_upper)}"
        elif zone_center is not None:
            entry_zone = _fmt_level(zone_center)
        else:
            entry_zone = _fmt_level(current_price)

        level_bits = [f"Entry {_fmt_level(current_price)}"]
        if rec_sl is not None:
            level_bits.append(f"SL {_fmt_level(rec_sl)}")
        if rec_tp is not None:
            level_bits.append(f"TP {_fmt_level(rec_tp)}")
        opp_zone = resistance_zone if direction == "LONG" else support_zone
        if opp_zone.get("center") is not None:
            level_bits.append(f"Opp zone {_fmt_level(opp_zone.get('center'))}")

        warnings = list(enriched.get("warnings") or [])
        if not res.get("bos_confirmed"):
            warnings.append("BOS not confirmed")
        if res.get("liquidity_sweep"):
            warnings.append("Liquidity sweep in play")
        if res.get("fvg_overlap"):
            warnings.append("FVG overlap present")

        enriched["narrative"] = enriched.get("narrative") or verdict or "No AI narrative returned."
        enriched["tradeStyleReason"] = enriched.get("tradeStyleReason") or (
            f"{struct_verdict} structure | Score {score:.2f}/{max_possible:.1f}"
            if score is not None and max_possible is not None
            else f"{struct_verdict} structure"
        )
        enriched["riskNote"] = enriched.get("riskNote") or (
            f"Micro {res.get('current_swing_sequence', '-')} | Macro {res.get('macro_swing_sequence', '-')}"
        )
        enriched["entryZone"] = enriched.get("entryZone") or entry_zone
        enriched["invalidation"] = enriched.get("invalidation") or _fmt_level(rec_sl)
        enriched["keyLevels"] = enriched.get("keyLevels") or " | ".join(level_bits)
        enriched["positionSizing"] = enriched.get("positionSizing") or "Size off Engine B stop distance at fixed account risk."
        enriched["warnings"] = list(dict.fromkeys(warnings))
        return enriched

    try:
        d1 = fetch_candles(pair_obj, "D1", CONFIG.get("D1_CANDLES", 220))
        h4 = fetch_candles(pair_obj, "H4", CONFIG.get("H4_CANDLES", 220))
        h1 = fetch_candles(pair_obj, "H1", CONFIG.get("H1_CANDLES", 220))

        if not d1 or not h4 or not h1:
            return None, pair_obj, "Failed to fetch D1/H4/H1 candles"

        d1 = d1[:-1] if len(d1) > 1 else d1
        h4 = h4[:-1] if len(h4) > 1 else h4
        h1 = h1[:-1] if len(h1) > 1 else h1

        h4_highs = [float(c["high"]) for c in h4]
        h4_lows = [float(c["low"]) for c in h4]
        h4_closes = [float(c["close"]) for c in h4]

        log.info(
            f"[NAKED-AI] {pair_obj.get('display')}: H4 candles={len(h4)}, sample_high={h4_highs[-1] if h4_highs else 'N/A'}"
        )

        atr_series = calc_atr(h4_highs, h4_lows, h4_closes, 14)
        atr = (
            float(atr_series[-1]) if atr_series and atr_series[-1] is not None else 0.0
        )

        log.info(
            f"[NAKED-AI] {pair_obj.get('display')}: ATR series length={len(atr_series) if atr_series else 0}, final_ATR={atr}"
        )

        if not atr or atr <= 0:
            log.warning(
                f"[NAKED-AI] {pair_obj.get('display')}: Failed ATR calc - series={atr_series}, using fallback ATR"
            )
            current_price = float(sig.get("price") or h1[-1]["close"])
            _atr_pct = {
                "forex": 0.002,
                "crypto": 0.02,
                "commodity": 0.008,
                "stock": 0.008,
                "index": 0.006,
            }
            atr = current_price * _atr_pct.get(pair_obj.get("type", ""), 0.01)
            log.info(
                f"[NAKED-AI] {pair_obj.get('display')}: Using fallback ATR={atr} (type={pair_obj.get('type')})"
            )

        current_price = float(sig.get("price") or h1[-1]["close"])

        from market_structure import NakedEngine

        engine = NakedEngine()
        res = engine.analyze_structure(d1, h4, h1, current_price, direction, atr)

        try:
            from ai_learning import get_ai_learning_context

            learning_ctx = get_ai_learning_context(
                pair_obj.get("display"), pair_obj.get("type", "crypto"), _AUDIT_DB
            )
        except Exception as e:
            log.warning(f"Failed to fetch AI learning context for Naked Analysis: {e}")
            learning_ctx = None

        resolved_style, style_profile = _naked_scan_style_profile(sig.get("style", "auto"))
        conf = engine.calculate_confidence(
            res,
            current_price,
            direction,
            learning_ctx,
            entry_candles=h1,
            style_profile=style_profile,
        )
        res.update(conf)
        res["current_price"] = current_price
        res["direction"] = direction
        res["style"] = resolved_style

        # Fetch news context for Engine B AI advisory (if enabled, non-blocking)
        _news_ctx = None
        if CONFIG.get("ENGINE_B_NEWS_CONTEXT_ENABLED", True):
            try:
                _news_ctx = fetch_news_context([pair_obj])
            except Exception as _ne:
                log.debug(f"[NAKED-AI] News context fetch failed (non-blocking): {_ne}")
                _news_ctx = None

        try:
            from engine_b_ai import get_engine_b_ai_verdict

            ai_verdict = get_engine_b_ai_verdict(
                pair=pair_obj.get("display"),
                direction=direction,
                current_price=current_price,
                structure_result=res,
                confidence_result=conf,
                learning_ctx=learning_ctx,
                xai_api_key=CONFIG.get("XAI_API_KEY"),
                xai_model=CONFIG.get("XAI_MODEL", "grok-beta"),
                engine_a_ctx=engine_a_ctx,
                news_ctx=_news_ctx,
            )
            if "error" not in ai_verdict:
                res["ai_analysis"] = _enrich_engine_b_ai_payload(ai_verdict)
                log.info(
                    f"[NAKED-AI] {pair_obj.get('display')}: AI grade={ai_verdict.get('grade')}"
                )
            else:
                err_msg = ai_verdict.get("error", "AI unavailable")
                log.warning(
                    f"[NAKED-AI] {pair_obj.get('display')}: AI analysis failed - {err_msg}"
                )
                res["ai_analysis"] = {
                    "grade": "N/A",
                    "edgeProbability": None,
                    "riskLevel": "UNKNOWN",
                    "verdict": f"AI review unavailable: {err_msg}",
                }
                res["ai_analysis"] = _enrich_engine_b_ai_payload(res["ai_analysis"])
        except Exception as e:
            log.warning(f"[NAKED-AI] Failed to get AI verdict: {e}")
            res["ai_analysis"] = {
                "grade": "N/A",
                "edgeProbability": None,
                "riskLevel": "UNKNOWN",
                "verdict": f"AI review unavailable: {e}",
            }
            res["ai_analysis"] = _enrich_engine_b_ai_payload(res["ai_analysis"])

        return res, pair_obj, None
    except Exception as e:
        log.error(f"naked_analysis error: {e}")
        return None, pair_obj, "Naked Structure Analysis failed"


@app.route("/api/naked-analysis", methods=["POST"])
def api_naked_analysis():
    d = request.json
    if not d or "signal" not in d:
        return jsonify({"error": "Invalid payload"}), 400

    res, _pair_obj, err = _compute_naked_analysis(d["signal"])
    if err:
        return jsonify({"error": err}), 500
    return jsonify(_json_safe(res))


@app.route("/api/compare-engines", methods=["POST"])
def api_compare_engines():
    d = request.json or {}
    sig = d.get("signal")
    if not isinstance(sig, dict):
        return jsonify({"error": "Invalid payload"}), 400

    pair_obj = _resolve_pair_from_signal(sig)
    if not pair_obj:
        return jsonify({"error": "Invalid signal"}), 400

    requested_style = d.get("style") or sig.get("style") or "auto"
    engine_a_style = _resolve_scan_style(_normalize_style(requested_style), pair_obj)
    engine_b_style, engine_b_profile = _naked_scan_style_profile(requested_style)

    try:
        btc_bias = (
            _current_btc_bias() if pair_obj.get("type") == "crypto" else "neutral"
        )
        engine_a = analyze_pair(pair_obj, btc_bias, style=engine_a_style)
        if not engine_a:
            return jsonify(
                {"error": "Engine A analysis unavailable for this pair"}
            ), 422

        compare_direction = (
            sig.get("direction") if sig.get("is_naked") else engine_a.get("direction")
        )
        engine_b_seed = dict(sig)
        engine_b_seed.update(
            {
                "symbol": pair_obj.get("symbol"),
                "pair": pair_obj.get("display"),
                "display": pair_obj.get("display"),
                "type": pair_obj.get("type"),
                "direction": compare_direction,
                "price": engine_a.get("price", sig.get("price")),
            }
        )
        engine_b, _pair_obj, err = _compute_naked_analysis(
            engine_b_seed, engine_a_ctx=engine_a
        )
        if err:
            return jsonify({"error": err}), 422

        engine_b["style"] = engine_b_style

        b_seq = engine_b.get("current_swing_sequence", "")
        b_macro = engine_b.get("macro_swing_sequence", "")
        a_dir = engine_a.get("direction")
        b_dir = engine_b.get("direction")
        structure_aligned = (a_dir == "LONG" and b_seq == "HH_HL") or (
            a_dir == "SHORT" and b_seq == "LH_LL"
        )
        macro_aligned = (a_dir == "LONG" and b_macro == "HH_HL") or (
            a_dir == "SHORT" and b_macro == "LH_LL"
        )
        summary = {
            "sameDirection": a_dir == b_dir,
            "structureAligned": structure_aligned,
            "macroAligned": macro_aligned,
            "aiReviewIncluded": bool(engine_b.get("ai_analysis")),
            "engineAStyle": engine_a_style,
            "engineBStyle": engine_b_style,
            "engineBMinScore": engine_b_profile["min_score"],
            "verdict": "ALIGNED"
            if a_dir == b_dir and structure_aligned
            else "CONFLICT",
        }
        return jsonify(
            _json_safe({"engineA": engine_a, "engineB": engine_b, "summary": summary})
        )
    except Exception as e:
        log.error(f"compare_engines error: {e}")
        return jsonify({"error": "Engine comparison failed"}), 500


@app.route("/api/quick-execute", methods=["POST"])
def api_quick_execute():
    d = request.json
    if not d or "signal" not in d or "engine_b" not in d:
        return jsonify({"error": "Invalid payload"}), 400

    sig = d["signal"]
    engine_b = d["engine_b"]

    # Override Engine A SL/TP with Engine B structure
    if "recommended_stop_loss" in engine_b and engine_b["recommended_stop_loss"]:
        sig["sl"] = engine_b["recommended_stop_loss"]
    if "recommended_take_profit" in engine_b and engine_b["recommended_take_profit"]:
        sig["tp1"] = engine_b["recommended_take_profit"]
        sig["tp2"] = engine_b[
            "recommended_take_profit"
        ]  # For scalping, tp2 defaults to tp1

    is_crypto = sig.get("type") == "crypto"

    try:
        from risk_engine import risk_check

        if is_crypto:
            from bybit_executor import (
                bybit_get_account,
                bybit_get_positions,
                bybit_get_symbol_info,
                bybit_execute,
            )

            account = bybit_get_account()
            if not account:
                return jsonify({"error": "Bybit not connected"}), 400
            pos_result = bybit_get_positions()
            positions = (
                pos_result.get("positions", [])
                if isinstance(pos_result, dict)
                else (pos_result or [])
            )
            symbol_info = bybit_get_symbol_info(sig.get("pair") or sig.get("symbol"))
            executor = bybit_execute
        else:
            from mt5_executor import (
                mt5_get_account,
                mt5_get_positions,
                mt5_get_symbol_info,
                mt5_execute,
            )

            account = mt5_get_account()
            if not account:
                return jsonify({"error": "MT5 not connected"}), 400
            pos_result = mt5_get_positions()
            positions = (
                pos_result.get("positions", [])
                if isinstance(pos_result, dict)
                else (pos_result or [])
            )
            # mt5_get_symbol_info takes the Athena display name to map to the internal MT5 dictionary
            symbol_info = mt5_get_symbol_info(sig.get("display") or sig.get("pair"))
            if not symbol_info or symbol_info.get("error"):
                return jsonify({"error": "Symbol not on broker"}), 400
            executor = mt5_execute

        approval = risk_check(
            signal=sig,
            account_balance=account["balance"],
            account_equity=account["equity"],
            open_positions=positions,
            symbol_info=symbol_info,
            kill_switch=False,
            sizing_override=CONFIG.get("AUTO_TRADE_SIZING_OVERRIDE", 1.0),
            is_manual_override=True,
        )

        pair_name = sig.get("pair", sig.get("symbol", "N/A"))
        if not approval.approved:
            log.warning(f"[QUICK EXEC] {pair_name} REJECTED: {approval.reason}")
            try:
                with sqlite3.connect(_AUDIT_DB, timeout=15.0) as _con:
                    _con.execute(
                        "INSERT INTO audit_log(ts,pair,score,direction,style,grade,error_tag) VALUES(?,?,?,?,?,?,?)",
                        (
                            datetime.now(timezone.utc).isoformat(),
                            pair_name,
                            sig.get("confluenceScore", 0),
                            sig.get("direction"),
                            "scalp",
                            "MANUAL-ERR",
                            approval.reason,
                        ),
                    )
            except Exception as _e:
                log.warning(f"[QUICK EXEC] Failed to log rejection to audit_db: {_e}")
            return jsonify({"error": f"Risk Blocked: {approval.reason}"}), 400

        result = executor(sig, approval)
        if result.get("success"):
            # Build Engine B factor JSON for AI learning
            _b_factors = {}
            if "structural_verdict" in engine_b:
                bos = engine_b.get("bos_data", {})
                sweep = engine_b.get("sweep_data", {})
                seq = engine_b.get("current_swing_sequence", "RANGING")
                engine_b.get("macro_swing_sequence", "RANGING")

                _b_factors["Naked_BOS_Bull"] = 1.0 if bos.get("bos_bull") else 0.0
                _b_factors["Naked_BOS_Bear"] = 1.0 if bos.get("bos_bear") else 0.0
                _b_factors["Naked_Sweep_Bull"] = 1.0 if sweep.get("bull_sweep") else 0.0
                _b_factors["Naked_Sweep_Bear"] = 1.0 if sweep.get("bear_sweep") else 0.0
                _b_factors["Naked_Seq_Bull"] = 1.0 if seq == "HH_HL" else 0.0
                _b_factors["Naked_Seq_Bear"] = 1.0 if seq == "LH_LL" else 0.0

            _factors = {
                "scores": _b_factors,
                "weights": {},
                "disabled": [],
                "regime": engine_b.get("regime", "RANGING"),
            }
            try:
                with sqlite3.connect(_AUDIT_DB, timeout=15.0) as con:
                    con.execute(
                        "INSERT INTO audit_log(ts,pair,score,direction,trend,grade,edge_prob,risk,style,"
                        "entry_price,sl,tp,volume,regime,risk_amount,risk_pct,ticket,fee_cost,factors_json) "
                        "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                        (
                            datetime.now(timezone.utc).isoformat(),
                            pair_name,
                            sig.get("confluenceScore", 0),
                            sig.get("direction"),
                            engine_b.get("current_swing_sequence", "RANGING"),
                            "EXECUTED",
                            None,
                            f"${approval.risk_amount}",
                            "scalp",
                            result.get("entryPrice"),
                            sig.get("sl"),
                            sig.get("tp1"),
                            result.get("volume"),
                            engine_b.get("regime", "RANGING"),
                            approval.risk_amount,
                            approval.risk_pct,
                            str(result.get("ticket", "")),
                            result.get("feeCost"),
                            json.dumps(_factors),
                        ),
                    )
                    con.commit()
            except Exception as ae:
                log.warning(f"[QUICK EXEC] Audit DB write failed: {ae}")

            return jsonify(
                {
                    "success": True,
                    "ticket": result.get("ticket"),
                    "message": "Executed instantly!",
                }
            )
        else:
            return jsonify({"error": result.get("error", "Execution failed")}), 400

    except Exception as e:
        log.error(f"quick_execute error: {e}")
        return jsonify({"error": str(e)}), 500


@app.route("/api/scan-naked", methods=["POST"])
def api_scan_naked():
    d = request.json or {}
    asset_class = d.get("assetClass", "crypto").lower()
    resolved_style, style_profile = _naked_scan_style_profile(d.get("style", "auto"))

    candidate_pairs = [
        p
        for p in ALL_PAIRS
        if p.get("type", "").lower() == asset_class
        and p.get("enabled", True)
        and p["display"] not in _disabled_pairs
    ]

    results = []

    from market_structure import NakedEngine

    engine = NakedEngine()

    import time

    def _fetch_cached_only(pair, tf, limit):
        """For naked scan: check in-memory TTL cache first, only call live if truly expired."""
        key = (pair.get("symbol", pair.get("display")), tf)
        now = time.time()
        with _candle_cache_lock:
            entry = _candle_cache.get(key)
            if entry is not None:
                candles, expiry = entry
                if now < expiry:
                    return candles
        # Cache miss — call normal fetch_candles (will populate cache for next time)
        return fetch_candles(pair, tf, limit)

    # Determine which timeframes this style needs
    _zone_tf = style_profile.get("zone_tf", "H4")
    _entry_tf = style_profile.get("entry_tf", "H1")
    _atr_tf = style_profile.get("atr_tf", "H4")
    _needed_tfs = list(
        {_zone_tf, _entry_tf, _atr_tf, "D1"}
    )  # always fetch D1 as fallback

    for pair in candidate_pairs:
        try:
            # Yield CPU to prevent Flask thread locking during synchronous scan
            time.sleep(0.1)

            # Fetch all needed timeframes
            _tf_map = {}
            for tf in _needed_tfs:
                cfg_key = f"{tf.upper().replace('H', 'H').replace('D', 'D')}_CANDLES"
                limit = CONFIG.get(cfg_key, 220)
                raw = _fetch_cached_only(pair, tf, limit)
                if raw and len(raw) > 1:
                    _tf_map[tf] = raw[:-1]  # drop incomplete current bar
                elif raw:
                    _tf_map[tf] = raw
                else:
                    _tf_map[tf] = []

            zone_candles = _tf_map.get(_zone_tf, [])
            entry_candles = _tf_map.get(_entry_tf, [])
            d1_candles = _tf_map.get("D1", [])
            atr_candles = _tf_map.get(_atr_tf, zone_candles)

            if len(zone_candles) < 10 or len(entry_candles) < 10:
                continue

            # ATR from the style's designated timeframe
            _atr_highs = [float(c["high"]) for c in atr_candles]
            _atr_lows = [float(c["low"]) for c in atr_candles]
            _atr_closes = [float(c["close"]) for c in atr_candles]
            atr_series = calc_atr(_atr_highs, _atr_lows, _atr_closes, 14)
            atr = float(atr_series[-1]) if atr_series else 0.0

            if not atr or atr <= 0:
                continue

            current_price = float(entry_candles[-1]["close"])

            # Test both directions
            # analyze_structure uses: arg2 (h4 slot) for zones/macro, arg3 (h1 slot) for micro/BOS/sweep
            for direction in ["LONG", "SHORT"]:
                res = engine.analyze_structure(
                    d1_candles,
                    zone_candles,
                    entry_candles,
                    current_price,
                    direction,
                    atr,
                )

                verdict = res.get("structural_verdict", "NONE")
                seq = res.get("current_swing_sequence", "")
                if verdict == "CLEAR":
                    conf_data = engine.calculate_confidence(
                        res,
                        current_price,
                        direction,
                        entry_candles=entry_candles,
                        style_profile=style_profile,
                    )
                    if conf_data["score"] < style_profile["min_score"] or not conf_data.get("passed"):
                        continue

                    res.update(conf_data)
                    res["current_price"] = current_price
                    res["symbol"] = pair.get("symbol")
                    res["display"] = pair.get("display")
                    res["type"] = pair.get("type")
                    res["direction"] = direction
                    res["style"] = resolved_style
                    res["zone_tf"] = _zone_tf
                    res["entry_tf"] = _entry_tf
                    res["atr_tf"] = _atr_tf

                    sl = res.get("recommended_stop_loss")
                    tp = res.get("recommended_take_profit")
                    rr = conf_data.get("rr", 0.0)

                    if not tp and sl:
                        sl_dist = abs(current_price - sl)
                        if direction == "LONG":
                            tp = current_price + (sl_dist * style_profile["fallback_rr"])
                        else:
                            tp = current_price - (sl_dist * style_profile["fallback_rr"])
                        res["recommended_take_profit"] = tp
                        res["fallback_tp_applied"] = True

                    if sl and tp and rr <= 0:
                        sl_dist = abs(current_price - sl)
                        tp_dist = abs(tp - current_price)
                        rr = (tp_dist / sl_dist) if sl_dist > 0 else 0.0
                    if rr < style_profile["min_rr"]:
                        continue

                    signal = {
                        "id": f"NKD_{pair['display']}_{direction}_{int(time.time())}",
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                        "pair": pair.get("display"),
                        "display": pair.get("display"),
                        "symbol": pair.get("symbol"),
                        "type": pair.get("type"),
                        "direction": direction,
                        "price": current_price,
                        "confluenceScore": conf_data["score"],
                        "confluencePct": conf_data["pct"],
                        "volRatio": 1.0,
                        "stochK": None,
                        "stochD": None,
                        "ema200Slope": 0.0,
                        "session": {"name": "Global"},
                        "grade": "Naked Structure",
                        "trendState": seq,
                        "edgeProbability": conf_data["pct"],
                        "riskLevel": "Low",
                        "score_pct": conf_data["pct"],
                        "is_naked": True,
                        "style": resolved_style,
                        "sl": sl,
                        "slPct": round(
                            abs(current_price - sl) / current_price * 100, 2
                        )
                        if sl
                        else 0.0,
                        "tp1": tp,
                        "tp2": tp,
                        "rr1": round(rr, 2) if rr else style_profile["fallback_rr"],
                        "rr2": round(rr, 2) if rr else style_profile["fallback_rr"],
                        "fib": {"fib618": 0.0, "fib500": 0.0},
                        "naked_data": res,
                    }
                    results.append(signal)
        except Exception as e:
            log.warning(f"[NAKED-SCAN] Error on {pair['display']}: {e}", exc_info=True)
            continue

    return jsonify(_json_safe({"success": True, "signals": results}))


# â"€â"€ Execution Engine Endpoints â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€

_executed_signals: set = set()  # Track executed signal IDs to prevent duplicates


@app.route("/api/execute", methods=["POST"])
def api_execute():
    """Execute a trade signal via MT5 (forex/stocks) or Bybit (crypto). Every order passes through risk_engine first."""

    if not CONFIG.get("EXECUTION_ENABLED", False):
        return jsonify(
            {"error": "Execution disabled. Set EXECUTION_ENABLED: true in config.yaml"}
        ), 403

    if _kill_switch:
        return jsonify({"error": "Kill-switch active â€” execution blocked"}), 503

    d = request.json

    if not d or "signal" not in d:
        return jsonify({"error": "Invalid payload: expected {signal: {...}}"}), 400

    sig = d["signal"]

    pair = sig.get("pair", "")

    force = (
        d.get("force", False) and _test_mode
    )  # force only works when test mode is active

    # Duplicate guard (skipped in force mode)

    sig_id = f"{pair}_{sig.get('direction')}_{sig.get('timestamp', '')}"

    if sig_id in _executed_signals and not force:
        return jsonify(
            {"error": "DUPLICATE: This signal has already been executed"}
        ), 409

    if force:
        log.warning(
            f"[EXEC] FORCE EXECUTE: {pair} {sig.get('direction')} (test mode, score {sig.get('confluenceScore', '?')})"
        )

    # ── Auto-refresh stale signals using live price feed ─────────────────────

    # If the signal is older than SIGNAL_MAX_AGE_SEC/2, re-run analyze_pair now.

    # _live_prices is updated every tick from the webhook/WebSocket feed, so this

    # gives execution at the actual current market price, not a stale scan price.

    _sig_age = 9999

    _ts_str = sig.get("timestamp", "")

    if _ts_str:
        try:
            _sig_age = (
                datetime.now(timezone.utc)
                - datetime.fromisoformat(_ts_str.replace("Z", "+00:00"))
            ).total_seconds()

        except Exception:
            _sig_age = 9999

    _max_age = CONFIG.get("SIGNAL_MAX_AGE_SEC", 300)

    if _sig_age > _max_age / 2:  # stale by half the window — refresh proactively
        _pair_obj = next((p for p in ALL_PAIRS if p["display"] == pair), None)

        if _pair_obj:
            try:
                _btc_bias = "neutral"

                _fresh = analyze_pair(
                    _pair_obj, _btc_bias, style=sig.get("style", "swing")
                )

                if _fresh:
                    _orig_dir = sig.get("direction", "")

                    if _fresh["direction"] != _orig_dir:
                        return jsonify(
                            {
                                "error": f"SIGNAL_FLIPPED: {pair} is now {_fresh['direction']} (was {_orig_dir})",
                                "newDirection": _fresh["direction"],
                                "refreshedAt": _fresh["timestamp"],
                            }
                        ), 409

                    # Merge refreshed levels/price/timestamp once the direction still matches.

                    sig["price"] = _fresh["price"]

                    sig["sl"] = _fresh["sl"]

                    sig["tp1"] = _fresh["tp1"]

                    sig["tp2"] = _fresh["tp2"]

                    sig["atr"] = _fresh.get("atr", sig.get("atr", 0))

                    sig["confluenceScore"] = _fresh["confluenceScore"]

                    sig["maxScore"] = _fresh.get("maxScore", sig.get("maxScore", 3))

                    sig["trendState"] = _fresh.get("trendState", sig.get("trendState"))

                    sig["timestamp"] = _fresh["timestamp"]

                    log.info(
                        f"[EXEC] {pair}: signal refreshed @ {_fresh['price']} (was {_sig_age:.0f}s old)"
                    )

            except Exception as _re:
                log.warning(
                    f"[EXEC] {pair}: live refresh failed ({_re}) — using original signal, refreshing timestamp"
                )

                sig["timestamp"] = datetime.now(timezone.utc).isoformat()

        else:
            # Pair not in ALL_PAIRS (e.g. webhook signal) — just stamp it fresh

            sig["timestamp"] = datetime.now(timezone.utc).isoformat()

    try:
        from risk_engine import risk_check

        sig_type = sig.get("type", "")

        is_crypto = sig_type == "crypto"

        if is_crypto:
            from bybit_executor import (
                bybit_get_account,
                bybit_get_positions,
                bybit_get_symbol_info,
                bybit_execute,
            )

            account = bybit_get_account()

            if not account or account.get("error"):
                return jsonify(
                    {
                        "error": "Bybit not connected. Set BYBIT_API_KEY and BYBIT_API_SECRET in .env"
                    }
                ), 503

            _pos_resp = bybit_get_positions()

            positions = (
                _pos_resp.get("positions", [])
                if isinstance(_pos_resp, dict)
                else (_pos_resp or [])
            )

            symbol_info = bybit_get_symbol_info(pair)

            if symbol_info and symbol_info.get("error"):
                symbol_info = None

        else:
            from mt5_executor import (
                mt5_get_account,
                mt5_get_positions,
                mt5_get_symbol_info,
                mt5_execute,
            )

            account = mt5_get_account()

            if not account or account.get("error"):
                return jsonify(
                    {
                        "error": "MT5 not connected. Start MT5 terminal and check credentials."
                    }
                ), 503

            _pos_resp = mt5_get_positions()

            positions = (
                _pos_resp.get("positions", [])
                if isinstance(_pos_resp, dict)
                else (_pos_resp or [])
            )

            symbol_info = mt5_get_symbol_info(pair)

            if not symbol_info or symbol_info.get("error"):
                return jsonify(
                    {
                        "error": f"Symbol '{pair}' not available on your MT5 broker. "
                        f"Check Market Watch or use a broker that offers this instrument."
                    }
                ), 200

        # Map AI grade/positionSizing to a sizing multiplier

        _grade = d.get("grade", "")

        _pos_size_str = d.get("positionSizing", "").lower()

        if "quarter" in _pos_size_str:
            _sizing_override = 0.25

        elif "half" in _pos_size_str:
            _sizing_override = 0.5

        elif "normal" in _pos_size_str or _grade == "A":
            _sizing_override = 0.75

        else:
            _sizing_override = 1.0  # Full / A+ / unspecified

        # MANDATORY RISK CHECK â€” no bypass

        approval = risk_check(
            signal=sig,
            account_balance=account["balance"],
            account_equity=account["equity"],
            open_positions=positions,
            symbol_info=symbol_info,
            kill_switch=_kill_switch,
            sizing_override=_sizing_override,
        )

        if not approval.approved:
            log.warning(f"[EXEC] {pair} REJECTED by risk engine: {approval.reason}")

            try:
                with sqlite3.connect(_AUDIT_DB, timeout=15.0) as _con:
                    _con.execute(
                        "INSERT INTO audit_log(ts,pair,score,direction,style,grade,error_tag) VALUES(?,?,?,?,?,?,?)",
                        (
                            datetime.utcnow().isoformat(),
                            pair,
                            sig.get("score"),
                            sig.get("direction"),
                            sig.get("style", "manual"),
                            "MANUAL-ERR",
                            approval.reason,
                        ),
                    )
            except Exception as _e:
                log.warning(f"[EXEC] Failed to log rejection to audit_db: {_e}")

            return jsonify(
                {
                    "error": f"Risk engine rejected: {approval.reason}",
                    "approval": approval.to_dict(),
                }
            ), 200

        # Execute via appropriate executor

        if is_crypto:
            result = bybit_execute(sig, approval)

        else:
            result = mt5_execute(sig, approval)

        if result.get("success"):
            _executed_signals.add(sig_id)

            # Log to audit DB â€” full trade lifecycle entry

            try:
                with sqlite3.connect(_AUDIT_DB, timeout=15.0) as con:
                    _factors = {
                        "scores": sig.get("factorScores"),
                        "weights": sig.get("factorWeights"),
                        "disabled": sig.get("disabledFactors"),
                        "regime": sig.get("regimeName"),
                    }
                    con.execute(
                        "INSERT INTO audit_log(ts,pair,score,direction,trend,grade,edge_prob,risk,style,"
                        "entry_price,sl,tp,volume,regime,risk_amount,risk_pct,ticket,fee_cost,factors_json) "
                        "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                        (
                            datetime.now(timezone.utc).isoformat(),
                            pair,
                            sig.get("confluenceScore"),
                            sig.get("direction"),
                            sig.get("trendState"),
                            "EXECUTED",
                            None,
                            f"${approval.risk_amount}",
                            "execution",
                            result.get("entryPrice"),
                            sig.get("sl"),
                            sig.get("tp1"),
                            result.get("volume"),
                            sig.get("trendState"),
                            approval.risk_amount,
                            approval.risk_pct,
                            str(result.get("ticket", "")),
                            result.get("feeCost"),
                            json.dumps(_factors),
                        ),
                    )

                    con.commit()

            except Exception as ae:
                log.warning(f"Audit DB write failed: {ae}")

            log.info(
                f"[EXEC] {pair} EXECUTED: ticket={result.get('ticket')}, volume={result.get('volume')}"
            )

        return jsonify(result)

    except Exception as e:
        log.error(f"[EXEC] execution error: {e}")

        return jsonify({"error": "Execution failed â€” check logs"}), 500


@app.route("/api/webhook", methods=["POST"])
def api_webhook():
    """Ingest external signals (e.g. TradingView alerts) and execute via risk engine.



    Payload (all fields required):

      {

        "secret":    "WEBHOOK_SECRET from .env (optional but recommended)",

        "pair":      "BTC/USDT",

        "type":      "crypto",          # crypto | forex | stock | commodity | index

        "direction": "LONG",            # LONG | SHORT

        "price":     65000.0,           # entry price

        "sl":        63000.0,           # stop-loss

        "tp1":       68000.0,           # take-profit 1

        "tp2":       71000.0,           # take-profit 2 (optional)

        "score":     7.5,               # confluence score (optional, for sizing)

        "maxScore":  10.0               # max possible score (optional)

      }



    Returns: same shape as /api/execute (success, ticket, volume, entryPrice, etc.)

    """

    if not CONFIG.get("EXECUTION_ENABLED", False):
        return jsonify({"error": "Execution disabled in config.yaml"}), 403

    if _kill_switch:
        return jsonify({"error": "Kill-switch active — webhook blocked"}), 503

    d = request.get_json(force=True, silent=True) or {}

    # Optional shared-secret guard (separate from ATHENA_API_KEY header auth)

    _wh_secret = os.environ.get("WEBHOOK_SECRET", "")

    if _wh_secret and d.get("secret", "") != _wh_secret:
        log.warning(f"[WEBHOOK] {request.remote_addr} rejected — invalid secret")

        return jsonify({"error": "Unauthorized — invalid webhook secret"}), 401

    # Build minimal signal dict from webhook payload

    pair_name = d.get("pair", "")

    direction = (d.get("direction") or d.get("side") or "").upper()

    sig_type = d.get("type", "")

    price = d.get("price") or d.get("entry") or 0

    sl = d.get("sl") or d.get("stop") or 0

    tp1 = d.get("tp1") or d.get("tp") or 0

    tp2 = d.get("tp2", 0)

    if (
        not pair_name
        or direction not in ("LONG", "SHORT")
        or not price
        or not sl
        or not tp1
    ):
        return jsonify(
            {"error": "Missing required fields: pair, direction, price, sl, tp1"}
        ), 400

    sig = {
        "pair": pair_name,
        "display": pair_name,
        "symbol": d.get("symbol", pair_name.replace("/", "")),
        "type": sig_type,
        "direction": direction,
        "price": float(price),
        "sl": float(sl),
        "tp1": float(tp1),
        "tp2": float(tp2) if tp2 else float(tp1),
        "confluenceScore": float(d.get("score", 0.5)),
        "maxScore": float(d.get("maxScore", 3.0)),
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "trendState": d.get("trendState", "DEVELOPING"),
    }

    # Duplicate guard — webhook signals use pair+direction+minute-bucket as key

    _minute = datetime.now(timezone.utc).strftime("%Y%m%d%H%M")

    sig_id = f"wh_{pair_name}_{direction}_{_minute}"

    if sig_id in _executed_signals:
        log.info(f"[WEBHOOK] Duplicate suppressed: {sig_id}")

        return jsonify(
            {"error": "DUPLICATE: webhook signal already fired this minute"}
        ), 409

    try:
        from risk_engine import risk_check

        is_crypto = sig_type == "crypto"

        if is_crypto:
            from bybit_executor import (
                bybit_get_account,
                bybit_get_positions,
                bybit_get_symbol_info,
                bybit_execute,
            )

            account = bybit_get_account()

            if not account or account.get("error"):
                return jsonify({"error": "Bybit not connected"}), 503

            _pos_resp = bybit_get_positions()

            positions = (
                _pos_resp.get("positions", [])
                if isinstance(_pos_resp, dict)
                else (_pos_resp or [])
            )

            symbol_info = bybit_get_symbol_info(pair_name)

            if symbol_info and symbol_info.get("error"):
                symbol_info = None

        else:
            from mt5_executor import (
                mt5_get_account,
                mt5_get_positions,
                mt5_get_symbol_info,
                mt5_execute,
            )

            account = mt5_get_account()

            if not account or account.get("error"):
                return jsonify({"error": "MT5 not connected"}), 503

            _pos_resp = mt5_get_positions()

            positions = (
                _pos_resp.get("positions", [])
                if isinstance(_pos_resp, dict)
                else (_pos_resp or [])
            )

            symbol_info = mt5_get_symbol_info(pair_name)

            if not symbol_info or symbol_info.get("error"):
                return jsonify(
                    {
                        "error": f"Symbol '{pair_name}' not available on your MT5 broker. "
                        f"Check Market Watch or use a broker that offers this instrument."
                    }
                ), 200

        approval = risk_check(
            signal=sig,
            account_balance=account["balance"],
            account_equity=account["equity"],
            open_positions=positions,
            symbol_info=symbol_info,
            kill_switch=_kill_switch,
            sizing_override=float(d.get("sizingOverride", 1.0)),
        )

        if not approval.approved:
            log.warning(f"[WEBHOOK] {pair_name} REJECTED: {approval.reason}")

            return jsonify(
                {
                    "error": f"Risk engine rejected: {approval.reason}",
                    "approval": approval.to_dict(),
                }
            ), 200

        if is_crypto:
            result = bybit_execute(sig, approval)

        else:
            result = mt5_execute(sig, approval)

        if result.get("success"):
            _executed_signals.add(sig_id)

            try:
                with sqlite3.connect(_AUDIT_DB, timeout=15.0) as con:
                    _factors = {
                        "scores": sig.get("factorScores"),
                        "weights": sig.get("factorWeights"),
                        "disabled": sig.get("disabledFactors"),
                        "regime": sig.get("regimeName"),
                    }
                    con.execute(
                        "INSERT INTO audit_log(ts,pair,score,direction,trend,grade,edge_prob,risk,style,"
                        "entry_price,sl,tp,volume,regime,risk_amount,risk_pct,ticket,factors_json) "
                        "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                        (
                            datetime.now(timezone.utc).isoformat(),
                            pair_name,
                            sig.get("confluenceScore"),
                            direction,
                            sig.get("trendState"),
                            "WEBHOOK",
                            None,
                            f"${approval.risk_amount}",
                            "webhook",
                            result.get("entryPrice"),
                            sig.get("sl"),
                            sig.get("tp1"),
                            result.get("volume"),
                            sig.get("trendState"),
                            approval.risk_amount,
                            approval.risk_pct,
                            str(result.get("ticket", "")),
                            json.dumps(_factors),
                        ),
                    )

                    con.commit()

            except Exception as ae:
                log.warning(f"[WEBHOOK] Audit DB write failed: {ae}")

            log.info(
                f"[WEBHOOK] {pair_name} {direction} EXECUTED: ticket={result.get('ticket')} vol={result.get('volume')}"
            )

        return jsonify(result)

    except Exception as e:
        log.error(f"[WEBHOOK] execution error: {e}")

        return jsonify({"error": "Webhook execution failed — check logs"}), 500


@app.route("/api/mt5-status")
def api_mt5_status():
    """Get MT5 connection status and account info."""

    try:
        from mt5_executor import mt5_get_account, mt5_get_positions

        account = mt5_get_account()

        if not account or account.get("error"):
            return jsonify(
                {
                    "connected": False,
                    "error": account.get("detail", "MT5 not connected")
                    if isinstance(account, dict)
                    else "MT5 not connected",
                }
            )

        _pos_resp = mt5_get_positions()

        positions = (
            _pos_resp.get("positions", [])
            if isinstance(_pos_resp, dict)
            else (_pos_resp or [])
        )

        return jsonify(
            {
                "connected": True,
                "account": account,
                "openPositions": len(positions),
                "positions": positions,
                "executionEnabled": CONFIG.get("EXECUTION_ENABLED", False),
            }
        )

    except Exception as e:
        return jsonify({"connected": False, "error": str(e)})


@app.route("/api/mt5-positions")
def api_mt5_positions():
    """Get open MT5 positions."""

    try:
        from mt5_executor import mt5_get_positions

        _pos_resp = mt5_get_positions()

        return jsonify(
            {
                "positions": _pos_resp.get("positions", [])
                if isinstance(_pos_resp, dict)
                else (_pos_resp or [])
            }
        )

    except Exception as e:
        return jsonify({"positions": [], "error": str(e)})


@app.route("/api/bybit-status")
def api_bybit_status():
    """Get Bybit Futures connection status and account info."""

    try:
        from bybit_executor import bybit_get_account, bybit_get_positions

        account = bybit_get_account()

        if not account or account.get("error"):
            return jsonify(
                {
                    "connected": False,
                    "error": account.get("detail", "Bybit not connected")
                    if isinstance(account, dict)
                    else "Bybit not connected",
                }
            )

        _pos_resp = bybit_get_positions()

        positions = (
            _pos_resp.get("positions", [])
            if isinstance(_pos_resp, dict)
            else (_pos_resp or [])
        )

        return jsonify(
            {
                "connected": True,
                "account": account,
                "openPositions": len(positions),
                "positions": positions,
            }
        )

    except Exception as e:
        return jsonify({"connected": False, "error": str(e)})


@app.route("/api/close-position", methods=["POST"])
def api_close_position():
    """Manually close an open position on MT5 or Bybit."""

    data = request.get_json(force=True)

    exch = data.get("exchange", "")

    pair = data.get("pair", "")

    direction = data.get("direction", "")

    volume = float(data.get("volume", 0))

    ticket = data.get("ticket")

    if exch == "bybit":
        from bybit_executor import bybit_close_position

        result = bybit_close_position(pair, direction, volume)

    elif exch == "mt5":
        if not ticket:
            return jsonify(
                {"success": False, "error": "MT5 close requires ticket"}
            ), 400

        from mt5_executor import mt5_close_position

        result = mt5_close_position(int(ticket))

    else:
        return jsonify({"success": False, "error": f"Unknown exchange: {exch}"}), 400

    status = 200 if result.get("success") else 500

    return jsonify(result), status


@app.route("/api/binance-status")
def api_binance_status():
    """Legacy endpoint — redirects to Bybit status."""

    return api_bybit_status()


@app.route("/api/execution-config", methods=["GET", "POST"])
def api_execution_config():
    """Get or update execution config (EXECUTION_ENABLED, AUTO_EXECUTE, etc.)."""

    if request.method == "GET":
        return jsonify(
            {
                "executionEnabled": CONFIG.get("EXECUTION_ENABLED", False),
                "autoExecute": CONFIG.get("AUTO_EXECUTE", False),
                "autoExecuteMinScore": CONFIG.get("AUTO_EXECUTE_MIN_SCORE", 8.0),
                "autoExecuteMinGrade": CONFIG.get("AUTO_EXECUTE_MIN_GRADE", "B"),
                "maxPortfolioHeat": CONFIG.get("MAX_PORTFOLIO_HEAT", 0.06),
                "maxOpenPositions": CONFIG.get("MAX_OPEN_POSITIONS", 5),
                "riskPct": CONFIG.get("RISK_PCT", 0.01),
            }
        )

    d = request.json or {}

    if "executionEnabled" in d:
        CONFIG["EXECUTION_ENABLED"] = bool(d["executionEnabled"])

        log.info(
            f"[EXEC] Execution {'ENABLED' if CONFIG['EXECUTION_ENABLED'] else 'DISABLED'}"
        )

    if "autoExecute" in d:
        CONFIG["AUTO_EXECUTE"] = bool(d["autoExecute"])

    return jsonify({"success": True})


@app.route("/api/screener-scan", methods=["POST"])
def api_screener_scan():
    """Phase C: Discover new high-cap momentum stocks via EODHD screener. Finds candidates not yet in our tracked pairs."""

    try:
        _key = os.environ.get("EODHD_KEY", "")

        if not _key:
            return jsonify({"error": "EODHD_KEY not set"}), 500

        d = request.json or {}

        min_cap = d.get("minMarketCap", 50000000000)  # $50B default

        limit = min(d.get("limit", 50), 100)

        # Fetch top momentum stocks: sorted by 200d_new_hi (price near 52-week high)

        params = {
            "api_token": _key,
            "fmt": "json",
            "limit": limit,
            "offset": 0,
            "filters": json.dumps([["market_capitalization", ">", min_cap]]),
            # EODHD screener rejects sort arrays on current plan; we'll sort client-side instead
        }

        r = http_requests.get(
            "https://eodhd.com/api/screener", params=params, timeout=15
        )

        if r.status_code == 403:
            return jsonify({"error": "Screener requires All-In-One plan (403)"}), 403

        if r.status_code != 200:
            err_detail = None

            try:
                err_json = r.json()

                if isinstance(err_json, dict):
                    if err_json.get("errors"):
                        # Flatten first error message for readability

                        first_key = next(iter(err_json["errors"].keys()), None)

                        if first_key:
                            err_vals = err_json["errors"].get(first_key)

                            if isinstance(err_vals, list) and err_vals:
                                err_detail = f"{first_key}: {err_vals[0]}"

                if not err_detail:
                    err_detail = str(err_json)

            except Exception:
                err_detail = r.text[:200]

            return jsonify(
                {
                    "error": f"EODHD screener error: HTTP {r.status_code}",
                    "details": err_detail,
                }
            ), 502

        data = r.json()

        rows = data.get("data", data) if isinstance(data, dict) else data

        if not rows or not isinstance(rows, list):
            return jsonify({"error": "No screener results"}), 404

        # Sort by 200d_new_hi locally (descending) now that API sort is unavailable on this plan

        try:
            rows.sort(key=lambda r: r.get("200d_new_hi", 0) or 0, reverse=True)

        except Exception:
            pass

        # Cross-reference against our existing pairs

        existing_syms = {p["symbol"].upper() for p in ALL_PAIRS}

        candidates = []

        already_tracked = []

        for row in rows:
            sym = (row.get("code") or row.get("symbol") or "").upper()

            name = row.get("name") or row.get("description") or sym

            exchange = row.get("exchange") or ""

            full_sym = f"{sym}.{exchange}" if exchange and "." not in sym else sym

            entry = {
                "symbol": full_sym,
                "name": name,
                "exchange": exchange,
                "marketCap": row.get("market_capitalization"),
                "52wHigh": row.get("52w_high"),
                "52wLow": row.get("52w_low"),
                "price": row.get("close") or row.get("last_close"),
                "200dNewHi": row.get("200d_new_hi"),
            }

            if full_sym in existing_syms or sym in existing_syms:
                already_tracked.append(entry)

            else:
                candidates.append(entry)

        log.info(
            f"[SCREENER] Found {len(candidates)} new candidates, {len(already_tracked)} already tracked"
        )

        return jsonify(
            {
                "success": True,
                "newCandidates": candidates[:20],
                "alreadyTracked": already_tracked[:10],
                "totalScanned": len(rows),
                "scannedAt": datetime.now(timezone.utc).isoformat(),
            }
        )

    except Exception as e:
        log.error(f"api_screener_scan error: {e}")

        return jsonify({"error": "Screener scan failed"}), 500


def _auto_toggle_pair(pair, result):
    """Auto-enable/disable a pair based on backtest SQN criteria.

    Enable: SQN > +0.5, IS_SQN > 0, OOS_SQN >= 0 (or < 3 OOS trades).

    Disable: SQN <= 0 or IS_SQN < 0.

    Returns: 'enabled', 'disabled', or None (no change)."""

    global ACTIVE_PAIRS

    if "error" in result:
        return None

    sqn = result.get("sqn", 0)

    wf = result.get("wfSplit", {})

    is_sqn = wf.get("is_sqn", 0)

    oos_sqn = wf.get("oos_sqn", 0)

    oos_trades = wf.get("oos_trades", 0)

    total_trades = int(result.get("totalTrades", 0) or 0)

    min_trades = 10 if pair.get("type") in ("crypto", "forex") else 8

    was_enabled = pair.get("enabled", True)

    if total_trades < min_trades:
        log.info(
            f"[BT-AUTO] {pair['display']} unchanged: only {total_trades} trades (<{min_trades})"
        )

        return None

    # Enable criteria: SQN > 0.5, IS positive, OOS non-negative (or too few OOS trades to judge)

    should_enable = sqn > 0.5 and is_sqn > 0 and (oos_sqn >= 0 or oos_trades < 3)

    # Disable criteria: overall SQN negative or IS clearly negative

    should_disable = sqn <= 0 or is_sqn < -0.5

    action = None

    if should_enable and not was_enabled:
        pair["enabled"] = True

        action = "enabled"

        log.info(
            f"[BT-AUTO] {pair['display']} AUTO-ENABLED (SQN:{sqn}, IS:{is_sqn}, OOS:{oos_sqn})"
        )

    elif should_disable and was_enabled:
        pair["enabled"] = False

        action = "disabled"

        log.warning(
            f"[BT-AUTO] {pair['display']} AUTO-DISABLED (SQN:{sqn}, IS:{is_sqn}, OOS:{oos_sqn})"
        )

    if action:
        ACTIVE_PAIRS = [p for p in ALL_PAIRS if p.get("enabled", True)]

        _persist_toggle_state()

    return action


@app.route("/api/backtest-naked", methods=["POST"])
def api_backtest_naked():
    """Separate endpoint for Engine B (Naked Market Structure) backtesting."""
    try:
        data = request.get_json(force=True, silent=True) or {}
        pair_symbol = data.get("pair") or data.get("symbol")
        requested_style = data.get("style", "auto")

        if not pair_symbol:
            return jsonify(
                {
                    "success": False,
                    "error": "No pair selected. Engine B backtest requires a specific pair — select one from the dropdown.",
                }
            ), 400

        pair = next(
            (
                p
                for p in ALL_PAIRS
                if p.get("display") == pair_symbol or p.get("symbol") == pair_symbol
            ),
            None,
        )
        if not pair:
            return jsonify(
                {
                    "success": False,
                    "error": f"Pair '{pair_symbol}' not found in pair list.",
                }
            ), 404

        result = backtest_pair_naked(pair, style=requested_style)

        if result is None:
            return jsonify(
                {
                    "success": False,
                    "error": "Insufficient candle data to run Engine B backtest for this pair.",
                }
            ), 422

        safe_result = (
            _json_safe(result) if callable(globals().get("_json_safe")) else result
        )
        return jsonify(safe_result)

    except Exception as exc:
        log.exception("[ENGINE B BT] Unhandled error in api_backtest_naked")
        return jsonify(
            {"success": False, "error": f"Engine B backtest failed: {str(exc)}"}
        ), 500


@app.route("/api/backtest", methods=["POST"])
def api_backtest():

    try:
        d = request.get_json(force=True, silent=True) or {}

        # S1: Validate symbol if provided

        sym = d.get("symbol")

        _bt_style = _normalize_style(d.get("style", "auto"))

        _allow_bt_toggle = bool(CONFIG.get("BT_AUTO_TOGGLE", False))

        if sym:
            if not isinstance(sym, str):
                return jsonify({"error": "Invalid symbol"}), 400

            pair = next((p for p in ALL_PAIRS if p["symbol"] == sym), None)

            if not pair:
                return jsonify({"error": "Unknown symbol"}), 404

            result = backtest_pair(pair, style=_bt_style)

            if _allow_bt_toggle:
                toggle = _auto_toggle_pair(pair, result)

                if toggle:
                    result["autoToggle"] = toggle

            result["activePairs"] = len(ACTIVE_PAIRS)

            return jsonify(_json_safe(result))

        # Full backtest â€” optional asset_class filter (crypto/forex/stock/commodity/index)

        full = run_full_backtest(style=_bt_style, asset_class=d.get("asset_class"))

        toggles = []

        if _allow_bt_toggle:
            for r in full.get("results", []):
                p = next((p for p in ALL_PAIRS if p["symbol"] == r.get("symbol")), None)

                if p:
                    t = _auto_toggle_pair(p, r)

                    if t:
                        toggles.append(
                            {"pair": r["pair"], "action": t, "sqn": r["sqn"]}
                        )

        full["autoToggles"] = toggles

        full["activePairs"] = len(ACTIVE_PAIRS)

        return jsonify(_json_safe(full))

    except Exception as e:
        # S2: Sanitise exception

        log.error(f"api_backtest error: {e}")

        return jsonify({"error": "Backtest failed"}), 500


@app.route("/api/backtest-history")
def api_backtest_history():
    """Return all stored backtest results, newest first."""
    try:
        with sqlite3.connect(_AUDIT_DB, timeout=15.0) as con:
            con.row_factory = sqlite3.Row
            rows = con.execute("""
                SELECT * FROM backtest_results
                ORDER BY run_date DESC
                LIMIT 500
            """).fetchall()
            return jsonify([dict(r) for r in rows])
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/backtest-history/<pair_name>")
def api_backtest_history_pair(pair_name):
    """Return backtest history for a specific pair."""
    try:
        with sqlite3.connect(_AUDIT_DB, timeout=15.0) as con:
            con.row_factory = sqlite3.Row
            rows = con.execute(
                """
                SELECT * FROM backtest_results
                WHERE pair = ?
                ORDER BY run_date DESC
                LIMIT 50
            """,
                (pair_name,),
            ).fetchall()
            return jsonify([dict(r) for r in rows])
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/backtest-best")
def api_backtest_best():
    """Return best result per pair (highest SQN from most recent run)."""
    try:
        with sqlite3.connect(_AUDIT_DB, timeout=15.0) as con:
            con.row_factory = sqlite3.Row
            rows = con.execute("""
                SELECT b.*
                FROM backtest_results b
                INNER JOIN (
                    SELECT pair, MAX(run_date) as latest
                    FROM backtest_results
                    GROUP BY pair
                ) latest ON b.pair = latest.pair
                AND b.run_date = latest.latest
                ORDER BY b.sqn DESC
            """).fetchall()
            return jsonify([dict(r) for r in rows])
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# N4: Kill-switch API — immediately blocks new scans/analyses


@app.route("/api/killswitch", methods=["POST"])
def api_killswitch():

    global _kill_switch

    d = request.json or {}

    action = d.get("action", "toggle")

    if action == "on":
        _kill_switch = True

    elif action == "off":
        _kill_switch = False

    else:
        _kill_switch = not _kill_switch

    log.warning(f"KILL-SWITCH {'ACTIVATED' if _kill_switch else 'DEACTIVATED'}")

    return jsonify({"killSwitch": _kill_switch})


@app.route("/api/killswitch/pair/<path:display>", methods=["POST"])
def api_killswitch_pair(display: str):
    """Enable or disable a specific pair by display name. Body: {"enabled": false}"""

    global _disabled_pairs, ACTIVE_PAIRS

    d = request.json or {}

    enabled = d.get("enabled", True)

    if enabled:
        _disabled_pairs.discard(display)

    else:
        _disabled_pairs.add(display)

    ACTIVE_PAIRS = [
        p
        for p in ALL_PAIRS
        if p.get("enabled", True) and p["display"] not in _disabled_pairs
    ]

    log.warning(
        f"[KILL] Pair {display!r}: {'ENABLED' if enabled else 'DISABLED'} ({len(ACTIVE_PAIRS)} active)"
    )

    return jsonify(
        {
            "pair": display,
            "enabled": enabled,
            "activePairs": len(ACTIVE_PAIRS),
            "disabledPairs": sorted(_disabled_pairs),
        }
    )


@app.route("/api/killswitch/pair")
def api_killswitch_pair_list():
    """List all disabled pairs."""
    return jsonify(
        {"disabledPairs": sorted(_disabled_pairs), "activePairs": len(ACTIVE_PAIRS)}
    )


def _update_yaml_toggle(state: bool):
    try:
        import os
        import re

        cfg_path = os.path.join(os.path.dirname(__file__), "config.yaml")
        if os.path.exists(cfg_path):
            with open(cfg_path, "r", encoding="utf-8") as f:
                content = f.read()
            new_val = "true" if state else "false"
            content = re.sub(
                r"^(AUTO_TRADE_ENABLED\s*:\s*)(true|false)(.*)$",
                rf"\g<1>{new_val}\3",
                content,
                flags=re.IGNORECASE | re.MULTILINE,
            )
            with open(cfg_path, "w", encoding="utf-8") as f:
                f.write(content)
    except Exception as e:
        log.error(f"Failed to update config.yaml: {e}")


@app.route("/api/test-mode", methods=["POST"])
def api_test_mode():
    """Toggle test mode: drops score thresholds, enables force-execute on all signals. For demo accounts only."""
    global _test_mode
    d = request.json or {}
    action = d.get("action", "toggle")
    if action == "on":
        _test_mode = True

    elif action == "off":
        _test_mode = False

    else:
        _test_mode = not _test_mode

    log.warning(
        f"[TEST MODE] {'ACTIVATED' if _test_mode else 'DEACTIVATED'} — score thresholds {'lowered' if _test_mode else 'restored'}"
    )

    return jsonify({"testMode": _test_mode})


@app.route("/api/test-mode")
def api_test_mode_status():
    """Check current test mode status."""

    return jsonify({"testMode": _test_mode})


# ── Auto-Trade Bot endpoints ──────────────────────────────────────────────────


@app.route("/api/auto-trade", methods=["GET"])
def api_auto_trade_status():

    return jsonify(_auto_trader.get_status())


@app.route("/api/auto-trade", methods=["POST"])
def api_auto_trade_toggle():

    d = request.json or {}

    action = d.get("action", "toggle")

    if action == "on":
        _auto_trader.enable()

    elif action == "off":
        _auto_trader.disable()

    else:
        _auto_trader.toggle()

    return jsonify(_auto_trader.get_status())


# ── AI Learning endpoints ─────────────────────────────────────────────────────


@app.route("/api/learning/stats")
def api_learning_stats():

    try:
        from ai_learning import get_ai_learning_context

        pair = request.args.get("pair", "")

        asset_type = request.args.get("type", "")

        ctx = get_ai_learning_context(
            pair,
            asset_type,
            _AUDIT_DB,
            lookback_days=CONFIG.get("LEARNING_LOOKBACK_DAYS", 90),
        )

        return jsonify(ctx)

    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/learning/meta-analysis", methods=["POST"])
def api_meta_analysis():

    try:
        from ai_learning import run_meta_analysis

        result = run_meta_analysis(
            _AUDIT_DB,
            CONFIG.get("XAI_API_KEY", ""),
            CONFIG.get("XAI_MODEL", "grok-4-1-fast-reasoning"),
        )

        return jsonify(result)

    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/sentiment/inject", methods=["POST"])
def api_inject_sentiment():
    """Inject external sentiment score (from LunarCrush, Crypto.com, etc.)"""

    data = request.get_json()

    pair = data.get("pair", "")

    score = data.get("score", 0)

    source = data.get("source", "external")

    if not pair:
        return jsonify({"error": "pair required"}), 400

    try:
        from sentiment_gate import inject_external_sentiment

        inject_external_sentiment(pair, float(score), source)

        return jsonify(
            {"success": True, "pair": pair, "score": score, "source": source}
        )

    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/auto-trade/log")
def api_auto_trade_log():
    """Last 30 auto-trade attempts — both successful and failed — for dashboard diagnosis."""

    try:
        with sqlite3.connect(_AUDIT_DB, timeout=15.0) as con:
            con.row_factory = sqlite3.Row

            rows = con.execute(
                """SELECT ts, pair, direction, score, asset_class, grade, error_tag, ticket, volume, entry_price

                   FROM audit_log

                   WHERE grade LIKE 'AUTO%'

                   ORDER BY id DESC LIMIT 30"""
            ).fetchall()

        return jsonify([dict(r) for r in rows])

    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/failed-executions")
def api_failed_executions():
    """Return recent failed/rejected trade attempts (manual and auto) with rejection reason."""

    try:
        with sqlite3.connect(_AUDIT_DB, timeout=15.0) as con:
            con.row_factory = sqlite3.Row

            rows = con.execute(
                """SELECT ts, pair, direction, score, grade, error_tag
                   FROM audit_log
                   WHERE grade LIKE '%-ERR%'
                   ORDER BY id DESC LIMIT 50"""
            ).fetchall()

        return jsonify([dict(r) for r in rows])

    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/learning/last-meta")
def api_last_meta():

    try:
        from ai_learning import get_last_meta_analysis

        result = get_last_meta_analysis(_AUDIT_DB)

        return jsonify(result or {})

    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/candle-cache")
def api_candle_cache():
    """View candle builder status: stats or per-pair candles.

    GET /api/candle-cache              → summary stats

    GET /api/candle-cache?pair=EUR/USD&tf=H1&limit=20  → candles for pair"""

    pair = request.args.get("pair")

    tf = request.args.get("tf", "H1")

    limit = int(request.args.get("limit", 50))

    if not _candle_builder:
        return jsonify({"error": "Candle builder not running"}), 503

    if pair:
        candles = _candle_builder.get_candles(pair, tf, limit)

        in_progress = {}

        with _candle_builder._lock:
            bar = _candle_builder._bars.get((pair, tf))

            if bar:
                in_progress = {
                    "start": str(bar["start"]),
                    "o": bar["o"],
                    "h": bar["h"],
                    "l": bar["l"],
                    "c": bar["c"],
                    "vol": bar["vol"],
                    "ticks": bar["ticks"],
                }

        return jsonify(
            {
                "pair": pair,
                "tf": tf,
                "bars": len(candles) if candles else 0,
                "candles": (candles or [])[-limit:],
                "inProgress": in_progress,
            }
        )

    # Summary: stats + active bar count

    stats = _candle_builder.stats()

    active = {}

    with _candle_builder._lock:
        for (disp, tf_k), bar in _candle_builder._bars.items():
            if disp not in active:
                active[disp] = {}

            active[disp][tf_k] = {"ticks": bar["ticks"], "price": bar["c"]}

    return jsonify(
        {
            "stats": stats,
            "activeBars": active,
            "activePairCount": len(active),
            "ts": datetime.now(timezone.utc).isoformat(),
        }
    )


@app.route("/api/flush-candle-cache", methods=["POST"])
def api_flush_candle_cache():
    """Clear the in-memory REST candle cache so the next scan fetches fresh data from all sources."""

    with _candle_cache_lock:
        count = len(_candle_cache)

        _candle_cache.clear()

    log.info(f"[CACHE] Flushed {count} cached candle entries")

    return jsonify({"flushed": count, "ts": datetime.now(timezone.utc).isoformat()})


@app.route("/api/backup-db", methods=["POST"])
def api_backup_db():
    """Manually trigger database backup."""
    try:
        from backup_db import backup_now

        backed_up = backup_now(reason="manual")
        return jsonify(
            {
                "success": True,
                "backed_up": [os.path.basename(p) for p in backed_up],
                "message": f"Backed up {len(backed_up)} databases",
            }
        )
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@app.route("/api/prices")
def api_prices():

    with _live_prices_lock:
        snapshot = dict(_live_prices)

    return jsonify(
        {
            "prices": snapshot,
            "count": len(snapshot),
            "ts": datetime.now(timezone.utc).isoformat(),
        }
    )


@app.route("/api/yield-curve")
def api_yield_curve():
    """Phase E: Yield curve data for dashboard widget."""

    try:
        yc = fetch_yield_curve()

        if not yc:
            return jsonify({"error": "Yield curve unavailable"}), 503

        return jsonify(yc)

    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/bulk-prices")
def api_bulk_prices():
    """Phase D: Bulk live OHLCV for US stocks via EODHD real-time endpoint (1 call vs multiple WS connections)."""

    try:
        _key = os.environ.get("EODHD_KEY", "")

        if not _key:
            return jsonify({"error": "EODHD_KEY not set"}), 500

        syms = request.args.get("symbols", "GOOG.US,GLD.US,SPY.US,QQQ.US")

        r = http_requests.get(
            f"https://eodhd.com/api/real-time/{syms.split(',')[0]}?s={','.join(syms.split(',')[1:])}&api_token={_key}&fmt=json",
            timeout=8,
        )

        if r.status_code != 200:
            return jsonify({"error": f"HTTP {r.status_code}"}), 502

        data = r.json()

        results = {}

        rows = data if isinstance(data, list) else [data]

        for row in rows:
            sym = row.get("code", "")

            results[sym] = {
                "price": row.get("close") or row.get("last_trade"),
                "open": row.get("open"),
                "high": row.get("high"),
                "low": row.get("low"),
                "volume": row.get("volume"),
                "changePct": row.get("change_p"),
                "changeDiff": row.get("change"),
                "timestamp": row.get("timestamp"),
            }

        return jsonify(
            {"prices": results, "ts": datetime.now(timezone.utc).isoformat()}
        )

    except Exception as e:
        log.error(f"api_bulk_prices error: {e}")

        return jsonify({"error": "Bulk prices failed"}), 500


@app.route("/api/pairs")
def api_pairs():
    """Return ALL_PAIRS grouped by asset type for frontend selectors (excludes JSE)."""

    type_labels = {
        "forex": "Forex",
        "commodity": "Commodities",
        "index": "Indices",
        "stock": "US Stocks",
        "etf": "ETFs",
        "crypto": "Crypto",
    }

    _etf_syms = {p["symbol"] for p in ETF_PAIRS}

    _jse_syms = {p["symbol"] for p in JSE_PAIRS}

    groups = {}

    for p in ALL_PAIRS:
        sym = p["symbol"]

        if sym in _jse_syms:
            continue  # JSE excluded from backtest selector (data quality)

        t = p.get("type", "other")

        label = "ETFs" if sym in _etf_syms else type_labels.get(t, t.title())

        if label not in groups:
            groups[label] = []

        groups[label].append(
            {"sym": sym, "label": p["display"], "enabled": p.get("enabled", True)}
        )

    bt_total = len(ALL_PAIRS) - len(JSE_PAIRS)

    return jsonify({"groups": groups, "total": bt_total, "active": len(ACTIVE_PAIRS)})


@app.route("/api/health")
def health():

    return jsonify(
        {
            "status": "paused" if _kill_switch else "ok",
            "killSwitch": _kill_switch,
            "pairs": len(ALL_PAIRS),
            "activePairs": len(ACTIVE_PAIRS),
            "dataSource": "yfinance+binance",
            "xaiKey": CONFIG["XAI_API_KEY"] != "YOUR_XAI_API_KEY",
        }
    )


@app.route("/api/audit")
def api_audit():
    """Return last N audit log entries from SQLite."""

    limit = min(int(request.args.get("limit", 50)), 500)

    try:
        con = sqlite3.connect(_AUDIT_DB, timeout=15.0)

        con.row_factory = sqlite3.Row

        rows = con.execute(
            "SELECT * FROM audit_log ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()

        con.close()

        return jsonify([dict(r) for r in rows])

    except Exception as e:
        return jsonify({"error": str(e)}), 500


def _check_api_keys() -> None:
    """Log startup warnings for missing API keys so the operator knows what's degraded."""

    missing = []

    if not os.environ.get("EODHD_KEY"):
        missing.append(
            "EODHD_KEY â€” real-time WebSocket prices, indicators, screener, and news all disabled"
        )

    _ak = os.environ.get("XAI_API_KEY", CONFIG.get("XAI_API_KEY", ""))

    if not _ak or _ak == "YOUR_XAI_API_KEY":
        missing.append("XAI_API_KEY â€” AI trade grading disabled")

    if not os.environ.get("CRYPTOPANIC_KEY"):
        missing.append("CRYPTOPANIC_KEY (optional) â€” crypto news sentiment reduced")

    if not os.environ.get("FINNHUB_KEY"):
        missing.append("FINNHUB_KEY (optional) â€” Polygon news fallback disabled")

    if missing:
        log.warning("[KEYS] Running in degraded mode â€” set missing keys in .env:")

        for m in missing:
            log.warning(f"  • {m}")

    else:
        log.info("[KEYS] All API keys configured")


# â"€â"€ Injected runtime definitions (scan helpers, analyze_pair, event risk) â"€â"€


def fetch_eodhd_indicators(pair):

    try:
        _key = os.environ.get("EODHD_KEY", "")

        if not _key:
            return None

        _disp = pair["display"]

        ticker = _eodhd_ticker_for_pair(pair)

        if not ticker:
            return None

        base = f"https://eodhd.com/api/technical/{ticker}?api_token={_key}&fmt=json"

        indicators = [
            ("EMA21", "function=ema&period=21&filter=last_ema"),
            ("EMA50", "function=ema&period=50&filter=last_ema"),
            ("RSI", "function=rsi&period=14&filter=last_rsi"),
            ("ADX", "function=adx&period=14&filter=last_adx"),
            ("ATR", "function=atr&period=14&filter=last_atr"),
            ("MACD", "function=macd&filter=last_macd"),
            ("SAR", "function=sar&filter=last_sar"),
        ]

        result = {}

        for name, params in indicators:
            try:
                r = http_requests.get(f"{base}&{params}", timeout=6)

                if r.status_code == 200:
                    val = r.json()

                    if isinstance(val, (int, float)):
                        result[name] = round(float(val), 4)

            except Exception as _e:
                log.debug(f"[IND] {name} fetch error: {_e}")

        if result:
            log.info(
                f"[IND] {_disp:12s} {' '.join(f'{k}={v}' for k, v in result.items())}"
            )

        return result if result else None

    except Exception as e:
        log.warning(f"[IND] {pair['display']}: {e}")

        return None


def analyze_pair(pair, btc_bias, style="swing", use_naked_engine=False):

    pair_profile = get_pair_profile(pair)

    d1 = fetch_candles(pair, "D1", CONFIG["D1_CANDLES"])

    h4 = fetch_candles(pair, "H4", CONFIG["H4_CANDLES"])

    h1 = fetch_candles(pair, "H1", CONFIG["H1_CANDLES"])

    if not d1 or not h4 or not h1:
        return None

    # F8: Drop the last (potentially still-forming) bar from each timeframe.

    # EODHD REST returns the current open bar — using it inflates indicators with

    # partial candle data. Matches freqtrade's process_only_new_candles=True.

    d1 = d1[:-1] if len(d1) > 1 else d1

    h4 = h4[:-1] if len(h4) > 1 else h4

    h1 = h1[:-1] if len(h1) > 1 else h1

    if len(d1) < 220 or len(h4) < 50 or len(h1) < 50:
        return None

    d1i = calc_indicators_with_normalized(d1, pair.get("type", "stock"))

    h4i = calc_indicators_with_normalized(h4, pair.get("type", "stock"))

    # Inject fib proximity into H4 snap so structure factor in compute_factor_scores() is non-None
    try:
        _h4_fib = calc_fib(h4)
        _h4_close = h4[-1]["close"] if h4 else None
        if _h4_fib and _h4_close:
            h4i["snap"]["fib_proximity"] = calc_fib_proximity(float(_h4_close), _h4_fib)
    except Exception:
        pass  # gracefully degrade — structure factor stays None

    # Inject live microstructure signals if WS feed has data for this symbol
    _msig = _micro_cache.get(pair.get("symbol", ""), {})
    if _msig:
        h4i["snap"].update({k: v for k, v in _msig.items() if v is not None})

    h1i = calc_indicators_with_normalized(h1, pair.get("type", "stock"))

    vols = [c["vol"] for c in h1]
    _ptype = pair.get("type", "stock")

    if _ptype in ("stock", "index"):
        # TOD Z-Scoring for U-Shaped Volume (compare current hour to same-hour history)
        try:
            current_dt = datetime.fromisoformat(
                h1[-1].get("time", "").replace("Z", "+00:00")
            )
            current_hour = current_dt.hour
            tod_vols = [
                c["vol"]
                for c in h1
                if datetime.fromisoformat(c.get("time", "").replace("Z", "+00:00")).hour
                == current_hour
            ]
            if len(tod_vols) >= 5:
                vsma_tod = calc_sma(tod_vols, min(len(tod_vols) - 1, 20))
                vr = (
                    tod_vols[-1] / vsma_tod[-1]
                    if vsma_tod and vsma_tod[-1] and vsma_tod[-1] > 0
                    else 1.0
                )
            else:
                vsma = calc_sma(vols, 20)
                vr = vols[-1] / vsma[-1] if vsma and vsma[-1] and vsma[-1] > 0 else 1.0
        except Exception:
            vsma = calc_sma(vols, 20)
            vr = vols[-1] / vsma[-1] if vsma and vsma[-1] and vsma[-1] > 0 else 1.0
    else:
        vsma = calc_sma(vols, 20)
        vr = vols[-1] / vsma[-1] if vsma and vsma[-1] and vsma[-1] > 0 else 1.0

    # Forex: override candle volume (zero/unreliable on EODHD) with real Dukascopy tick volume
    if _ptype == "forex":
        try:
            from duka_volume import get_forex_vr as _get_forex_vr

            vr = _get_forex_vr(pair.get("display", ""), tf="H1", lookback=20)
        except Exception:
            pass  # gracefully degrade to EODHD-derived vr if duka unavailable

    # Style-based timeframe routing (Elder Triple Screen: D1 tide, H4 momentum, H1 entry)

    # SWING  (default): D1 trend gate, H4 momentum, H1 entry

    # INTRADAY:         D1 trend gate, H4 momentum, H1 entry + H1 stochastic

    # SCALP:            D1 trend gate, H4 momentum, H1 entry + H1 stochastic

    _style = (style or "swing").lower()

    if _style == "intraday":
        _cf_d1i, _cf_h4i, _cf_h1i = d1i, h4i, h1i

        _cf_d1c, _cf_h4c, _cf_h1c = d1, h4, h1

        stoch = calc_stochastic(
            h1, 5, 3, 3
        )  # TA-Lib STOCH standard: fastK=5, slowK=3, slowD=3

    elif _style == "scalp":
        _cf_d1i, _cf_h4i, _cf_h1i = d1i, h4i, h1i

        _cf_d1c, _cf_h4c, _cf_h1c = d1, h4, h1

        stoch = calc_stochastic(
            h1, 5, 3, 3
        )  # TA-Lib STOCH standard: fastK=5, slowK=3, slowD=3

    else:  # swing
        _cf_d1i, _cf_h4i, _cf_h1i = d1i, h4i, h1i

        _cf_d1c, _cf_h4c, _cf_h1c = d1, h4, h1

        stoch = calc_stochastic(
            h4, 5, 3, 3
        )  # TA-Lib STOCH standard: fastK=5, slowK=3, slowD=3

    # F11: EMA200 slope computed once here (UI display only — not fed to calc_confluence)

    _e200 = calc_ema([c["close"] for c in d1], 200)

    e200s = (
        (_e200[-1] - _e200[-21]) / _e200[-21]
        if _e200 and len(_e200) >= 21 and _e200[-1] and _e200[-21]
        else 0
    )

    # Fetch funding rate + open interest for crypto pairs

    _funding_rate = None

    _oi_divergence = None

    if pair.get("type") == "crypto":
        _bn_sym = pair.get("symbol", "").replace("/", "")  # e.g. BTCUSDT

        _fr_resp = _fetch_funding_rate(_bn_sym)
        _funding_rate = (
            _fr_resp.get("rate")
            if isinstance(_fr_resp, dict) and not _fr_resp.get("error")
            else None
        )

        _oi_data = _fetch_open_interest(_bn_sym)

        _prev_close = d1[-2]["close"] if d1 and len(d1) >= 2 else None

        _cur_close = h1i["snap"].get("close") or (d1[-1]["close"] if d1 else None)

        if _cur_close and _prev_close:
            _oi_divergence = _calc_oi_divergence(_oi_data, _cur_close, _prev_close)

    res = None
    # Route forex pairs to dedicated forex scoring engine
    if pair.get("type") == "forex":
        try:
            from forex_scoring import compute_forex_score

            _forex_result = compute_forex_score(
                d1_snap=_cf_d1i["snap"],
                h4_snap=_cf_h4i["snap"],
                h1_snap=_cf_h1i["snap"],
                h1_candles=_cf_h1c,
                pair=pair,
                bar_time=str(d1[-1].get("time", "") or d1[-1].get("datetime", "")),
                h4_candles=_cf_h4c,
            )
            # Map forex factor_scores to UI-compatible votes format (MUST be flat for AI consumption)
            fx_votes = {}

            # Map forex components to screen structure for UI display and AI logging
            components = _forex_result.components
            if components:
                # Screen 1 (D1) - Trend gate and session
                fx_votes["D1 Trend"] = 1.0 if components.get("trend_gate") else 0.0
                fx_votes["Session"] = 1.0 if components.get("session_active") else 0.0

                # Screen 2 (H4) - Momentum and ADX
                fx_votes["H4 Momentum"] = components.get("momentum_confirm", 0.0)
                fx_votes["H4 ADX"] = components.get("adx_filter", 0.0)

                # Screen 3 (H1) - Entry quality and COT
                fx_votes["H1 Entry"] = components.get("entry_quality", 0.0)
                fx_votes["COT Boost"] = components.get("cot_boost", 0.0)

            res = {
                "final_score": _forex_result.final_score,
                "direction": _forex_result.direction,
                "factor_scores": _forex_result.components,
                "regime": {"state": 1, "label": _forex_result.signal_type},
                "signal_type": _forex_result.signal_type,
                "score": _forex_result.final_score,
                "trendState": _forex_result.signal_type,
                # Keys required by signal dict construction below
                "votes": fx_votes,
                "warnings": [],
                "weinsteinStage": None,
                "weinsteinLabel": "N/A",
                "maxScoreOverride": 1.0,
            }
        except Exception as _fx_err:
            log.error(
                f"[FOREX] {pair.get('display')} forex_scoring FAILED: {_fx_err} — falling back to calc_confluence"
            )
            res = None  # force fallback

    if res is None or pair.get("type") != "forex":
        res = calc_confluence(
            _cf_d1i,
            _cf_h4i,
            _cf_h1i,
            vr,
            stoch,
            pair,
            btc_bias,
            d1_candles=_cf_d1c,
            h4_candles=_cf_h4c,
            h1_candles=_cf_h1c,
            funding_rate=_funding_rate,
            volume_threshold=pair_profile.get(
                "volume_threshold", CONFIG["VOLUME_THRESHOLD"]
            ),
        )

    # For SCALP: warn if D1 trend disagrees with signal direction

    if _style == "scalp":
        _ds = d1i["snap"]

        if _ds.get("ema21") and _ds.get("ema50") and _ds.get("ema200"):
            if _ds["ema21"] > _ds["ema50"] > _ds["ema200"]:
                _d1_dir = "LONG"

            elif _ds["ema21"] < _ds["ema50"] < _ds["ema200"]:
                _d1_dir = "SHORT"

            else:
                _d1_dir = None

            if _d1_dir and _d1_dir != res["direction"]:
                res["warnings"].append(
                    f"SCALP COUNTER-TREND: D1 EMA stack is {_d1_dir} â€” trading against higher-TF trend, reduce size"
                )

    direction = res["direction"]

    live_px = (_live_prices.get(pair["display"], {}) or {}).get("price")

    price = (
        live_px
        or h1i["snap"].get("close")
        or h4i["snap"].get("close")
        or d1i["snap"].get("close")
    )

    atr = _atr_for_levels(d1i, h4i, h1i, pair=pair, style=_style)

    if price is None or not atr:
        return None

    fib = calc_fib(h4)

    _regime_state = res.get("regime", {}).get("state") if res.get("regime") else None

    lvl = calc_levels(
        float(price), float(atr), direction, pair["type"], regime_state=_regime_state
    )

    sk = stoch["k"][-1] if stoch["k"] and stoch["k"][-1] is not None else None

    sd = stoch["d"][-1] if stoch["d"] and stoch["d"][-1] is not None else None

    risk_pct = (
        round(abs(float(price) - float(lvl["sl"])) / float(price) * 100, 2)
        if price and float(price) != 0
        else None
    )

    warn_list = list(res.get("warnings", []))

    if pair_filter_enabled(pair, "divergence_warning"):
        for w in detect_div(d1, h4, h1):
            if w not in warn_list:
                warn_list.append(w)

    if _oi_divergence and _oi_divergence.get("warning"):
        warn_list.append(_oi_divergence["warning"])

    max_score = res.get("maxScoreOverride") or _max_score_for_pair(pair)

    # --- ENGINE B: NAKED MARKET STRUCTURE OVERLAY ---
    structure_data = None
    if use_naked_engine and res["score"] >= get_pair_profile(pair).get(
        "min_score", CONFIG.get("MIN_CONFLUENCE", 0.6)
    ):
        try:
            from market_structure import engine as naked_engine
            _overlay_style, _overlay_profile = _naked_scan_style_profile(_style)

            _reg = res.get("regime", {})
            _regime_label = (
                _reg.get("label", "RANGING")
                if isinstance(_reg, dict)
                else (str(_reg) if _reg else "RANGING")
            )
            structure_data = naked_engine.analyze_structure(
                d1, h4, h1, float(price), direction, float(atr), _regime_label
            )

            if structure_data and structure_data.get("structural_verdict") == "CLEAR":
                _min_room = float(atr) * float(_overlay_profile.get("min_room_atr", 1.0))
                if (
                    direction == "LONG"
                    and structure_data.get("distance_to_res", float("inf")) < _min_room
                ):
                    log.warning(
                        f"[ENGINE-B] {pair['display']} LONG blocked: nearest resistance too close"
                    )
                    return None
                if (
                    direction == "SHORT"
                    and structure_data.get("distance_to_sup", float("inf")) < _min_room
                ):
                    log.warning(
                        f"[ENGINE-B] {pair['display']} SHORT blocked: nearest support too close"
                    )
                    return None

                if pair.get("type") in ("crypto", "forex", "commodity"):
                    # Light fetch for DXY correlation
                    _dxy = fetch_candles(
                        {
                            "symbol": "USDollar",
                            "display": "DXY",
                            "source": "twelvedata",
                            "type": "index",
                        },
                        "H4",
                        100,
                    )
                    if _dxy:
                        _dxy_c = [float(c["close"]) for c in _dxy]
                        _asset_c = [float(c["close"]) for c in h4]
                        if not naked_engine.check_macro_correlation(
                            _asset_c, _dxy_c, direction
                        ):
                            log.warning(
                                f"[ENGINE-B] {pair['display']} {direction} blocked: adverse DXY correlation"
                            )
                            return None

                # Safest Stop Loss Override (Combined Risk Management)
                if structure_data.get("recommended_stop_loss"):
                    _struct_sl = float(structure_data["recommended_stop_loss"])
                    _math_sl = float(lvl["sl"])
                    lvl["sl"] = (
                        min(_math_sl, _struct_sl)
                        if direction == "LONG"
                        else max(_math_sl, _struct_sl)
                    )

                # Take Profit Override to sit safely inside structural walls
                if structure_data.get("recommended_take_profit"):
                    _struct_tp = float(structure_data["recommended_take_profit"])
                    _math_tp1 = float(lvl["tp1"])
                    lvl["tp1"] = (
                        min(_math_tp1, _struct_tp)
                        if direction == "LONG"
                        else max(_math_tp1, _struct_tp)
                    )

                risk_pct = round(
                    abs(float(price) - float(lvl["sl"])) / float(price) * 100, 2
                )
        except Exception as e:
            log.error(f"[ENGINE-B] Error on {pair['display']}: {e}")
    # ------------------------------------------------

    return {
        "pair": pair["display"],
        "display": pair["display"],
        "symbol": pair["symbol"],
        "type": pair["type"],
        "direction": direction,
        "confluenceScore": round(res["score"], 4),
        "confluencePct": min(100, round(res["score"] / max_score * 100)),
        "votes": res["votes"],
        "maxScore": max_score,
        "price": round(float(price), 6),
        "sl": round(float(lvl["sl"]), 6),
        "tp1": round(float(lvl["tp1"]), 6),
        "tp2": round(float(lvl["tp2"]), 6),
        "rr1": round(float(lvl["rr1"]), 2),
        "rr2": round(float(lvl["rr2"]), 2),
        "atr": round(float(atr), 6),
        "slPips": round(abs(float(price) - float(lvl["sl"])), 6),
        "slPct": risk_pct,
        "fib": fib,
        "d1": d1i,
        "h4": h4i,
        "h1": h1i,
        "volRatio": round(vr, 2),
        "ema200Slope": round(e200s * 100, 3),
        "stochK": round(sk, 1) if sk is not None else None,
        "stochD": round(sd, 1) if sd is not None else None,
        "btcBias": btc_bias if pair["type"] == "crypto" else "n/a",
        "trendState": res["trendState"],
        "weinsteinStage": res["weinsteinStage"],
        "weinsteinLabel": res["weinsteinLabel"],
        "entryMode": res.get("entryMode", "trend"),
        "signalClass": res.get("signalClass", "trend_continuation"),
        "adxMomentum": res.get("adxMomentum"),
        "adxSlope": res.get("adxSlope"),
        "spread": res.get("spread", 0),
        "warnings": warn_list,
        "session": get_session(h4[-1].get("time") if h4 else None),
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "aiAnalysis": None,
        "oiDivergence": _oi_divergence,
        "fundingRate": res.get("fundingRate"),
        "regime": res.get("regime"),
        "pairProfile": pair_profile,
        "style": _style,
        "h4Candles": [
            {
                "t": c.get("time", ""),
                "o": round(c["open"], 6),
                "h": round(c["high"], 6),
                "l": round(c["low"], 6),
                "c": round(c["close"], 6),
            }
            for c in h4[-80:]
        ],
    }


# _build_event_risk imported from scoring.py


def _annotate_signal_for_scan(
    signal: dict,
    pair: dict,
    threshold: float,
    ds_ctx: dict,
    earnings_ctx: dict,
    closed_exchanges: set,
    news_ctx: dict,
) -> dict:

    signal["scanThreshold"] = threshold

    signal["isEnabled"] = pair.get("enabled", True)

    signal["exchangeClosed"] = _pair_exchange_closed(pair, closed_exchanges)

    signal["eventRisk"] = _build_event_risk(
        pair, ds_ctx, earnings_ctx, closed_exchanges
    )

    signal["newsCtx"] = news_ctx

    diagnostics = []

    if signal["confluenceScore"] < threshold:
        diagnostics.append(
            {
                "code": "low_confluence",
                "detail": f"Score {signal['confluenceScore']}/{threshold}",
            }
        )

    if signal.get("trendState") == "RANGING":
        diagnostics.append({"code": "ranging", "detail": "Ranging regime"})

    if signal.get("trendState") == "DEAD RANGING":
        diagnostics.append({"code": "dead_ranging", "detail": "Dead ranging regime"})

    if any("COUNTER-TREND" in w for w in signal.get("warnings", [])):
        diagnostics.append(
            {"code": "counter_trend", "detail": "Counter-trend warning active"}
        )

    if signal.get("exchangeClosed"):
        diagnostics.append({"code": "closed_exchange", "detail": "Exchange closed"})

    if signal["eventRisk"].get("hardBlock"):
        diagnostics.append(
            {
                "code": "event_risk",
                "detail": ", ".join(signal["eventRisk"].get("reasons", [])),
            }
        )

    if not pair.get("enabled", True):
        diagnostics.append(
            {
                "code": "inactive_pair",
                "detail": "Pair not auto-enabled for live trading",
            }
        )

    signal["scanDiagnostics"] = diagnostics

    for reason in signal["eventRisk"].get("reasons", []):
        warn = f"EVENT RISK: {reason}"

        if warn not in signal["warnings"]:
            signal["warnings"].append(warn)

    return signal


# _classify_signal imported from scoring.py — see that module for implementation


# â"€â"€ Task 1: Trade Outcome Monitor â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€


def _resolve_exit_reason(exit_price: float, sl: float | None, tp: float | None) -> str:
    """Classify exit as SL_HIT, TP_HIT, or MANUAL_CLOSE based on price proximity."""

    if exit_price is None:
        return "MANUAL_CLOSE"

    tol_pct = 0.002  # 0.2% tolerance

    if sl and abs(exit_price - sl) / max(abs(sl), 1e-9) <= tol_pct:
        return "SL_HIT"

    if tp and abs(exit_price - tp) / max(abs(tp), 1e-9) <= tol_pct:
        return "TP_HIT"

    return "MANUAL_CLOSE"


def _update_trade_outcome(
    ticket: str,
    exit_price: float,
    exit_time: str,
    pnl: float,
    entry_price: float | None,
    sl: float | None,
    tp: float | None,
    volume: float | None,
    entry_ts: str | None,
    risk_amount: float | None = None,
) -> None:
    """Write outcome columns for a closed trade in audit_log."""

    exit_reason = _resolve_exit_reason(exit_price, sl, tp)

    # R-multiple: pnl / dollar_risk.

    # Use risk_amount from audit_log (pre-calculated by risk_engine, correct for all

    # asset classes including forex lots and commodity contracts).

    # Fallback to price-distance × volume only for crypto/stocks where volume IS

    # in base units and risk_amount may not be stored on legacy rows.

    r_multiple = None

    if pnl is not None:
        if risk_amount and risk_amount > 0:
            r_multiple = round(pnl / risk_amount, 2)

        elif entry_price and sl and volume and abs(entry_price - sl) > 0:
            # Fallback: only valid when volume is in base units (crypto, stocks)

            risk_dist = abs(entry_price - sl)

            r_multiple = round(pnl / (risk_dist * volume), 2)

    # Holding period

    holding_hours = None

    if entry_ts:
        try:
            t_entry = datetime.fromisoformat(entry_ts.replace("Z", "+00:00"))

            t_exit = datetime.fromisoformat(exit_time.replace("Z", "+00:00"))

            holding_hours = round((t_exit - t_entry).total_seconds() / 3600, 2)

        except Exception as _hp_err:
            log.debug(f"[AUDIT] holding period calc failed: {_hp_err}")

    try:
        with sqlite3.connect(_AUDIT_DB, timeout=15.0) as con:
            con.execute(
                "UPDATE audit_log SET exit_price=?, exit_time=?, pnl=?, r_multiple=?, "
                "exit_reason=?, holding_period_hours=? WHERE ticket=? AND exit_price IS NULL",
                (
                    exit_price,
                    exit_time,
                    pnl,
                    r_multiple,
                    exit_reason,
                    holding_hours,
                    ticket,
                ),
            )

            con.commit()

        log.info(
            f"[MONITOR] Outcome logged: ticket={ticket} exit={exit_price} pnl={pnl} R={r_multiple} reason={exit_reason}"
        )

        # AI self-learning: extract outcome into learning_log

        if CONFIG.get("LEARNING_ENABLED", True):
            try:
                from ai_learning import extract_learning_from_trade

                extract_learning_from_trade(_AUDIT_DB, ticket, is_demo=_test_mode)

            except Exception as _le:
                log.debug(f"[LEARN] extraction failed for {ticket}: {_le}")

        # Feed realized P&L to daily loss tracker

        if pnl is not None:
            try:
                from risk_engine import record_daily_pnl

                # Get current balance for reference

                _bal = 0.0

                try:
                    from mt5_executor import mt5_get_account

                    _acc = mt5_get_account()

                    if _acc:
                        _bal = _acc.get("balance", 0)

                except Exception as _mt5e:
                    log.debug(f"[DAILY-PNL] MT5 balance fetch: {_mt5e}")

                if _bal <= 0:
                    try:
                        import bybit_executor as _bm

                        _bex = _bm._get_exchange()

                        if _bex:
                            _bb = _bex.fetch_balance()

                            _bal = float(_bb.get("total", {}).get("USDT", 0))

                    except Exception as _bbe:
                        log.debug(f"[DAILY-PNL] Bybit balance fetch: {_bbe}")

                if _bal > 0:
                    record_daily_pnl(pnl, _bal)

            except Exception as _dpnl_err:
                log.debug(f"[DAILY-PNL] record failed: {_dpnl_err}")

    except Exception as e:
        log.warning(f"[MONITOR] DB write failed for ticket {ticket}: {e}")


_score_decay_counter = 0


def _outcome_monitor_loop() -> None:
    """Background loop: every 60s reconcile closed MT5/CCXT trades against audit_log."""

    global _score_decay_counter

    while True:
        try:
            time.sleep(60)

            _check_mt5_outcomes()

            _check_ccxt_outcomes()

            # Score decay check every 5 minutes (not every 60s — too expensive)

            _score_decay_counter += 1

            if _score_decay_counter >= 5:
                _score_decay_counter = 0

                _check_score_decay()

        except Exception as e:
            log.debug(f"[MONITOR] loop error: {e}")


def _check_mt5_outcomes() -> None:
    """Check MT5 for positions that have closed since last check."""

    try:
        from mt5_executor import mt5_get_positions, mt5_connect

        import MetaTrader5 as _mt5_lib

        if not mt5_connect():
            return

        _pos_resp = mt5_get_positions()

        _pos_list = (
            _pos_resp.get("positions", [])
            if isinstance(_pos_resp, dict)
            else (_pos_resp or [])
        )

        open_tickets = {str(p["ticket"]) for p in _pos_list}

        # Find audit rows with a ticket but no exit_price

        with sqlite3.connect(_AUDIT_DB, timeout=15.0) as con:
            con.row_factory = sqlite3.Row

            pending = con.execute(
                "SELECT id, ticket, entry_price, sl, tp, volume, ts, risk_amount FROM audit_log "
                "WHERE ticket IS NOT NULL AND ticket != '' AND exit_price IS NULL"
            ).fetchall()

        for row in pending:
            ticket_str = str(row["ticket"])

            if ticket_str in open_tickets:
                continue  # still open

            # Position closed â€” look up deal history

            try:
                ticket_int = int(ticket_str)

                deals = _mt5_lib.history_deals_get(ticket=ticket_int)

                if not deals:
                    # Broader search: last 30 days

                    from_dt = datetime.now(timezone.utc) - timedelta(days=30)

                    deals = _mt5_lib.history_deals_get(
                        int(from_dt.timestamp()),
                        int(datetime.now(timezone.utc).timestamp()),
                    )

                    deals = [d for d in (deals or []) if d.position_id == ticket_int]

                if deals:
                    close_deal = sorted(deals, key=lambda d: d.time)[-1]

                    _update_trade_outcome(
                        ticket=ticket_str,
                        exit_price=close_deal.price,
                        exit_time=datetime.fromtimestamp(
                            close_deal.time, tz=timezone.utc
                        ).isoformat(),
                        pnl=close_deal.profit,
                        entry_price=row["entry_price"],
                        sl=row["sl"],
                        tp=row["tp"],
                        volume=row["volume"],
                        entry_ts=row["ts"],
                        risk_amount=float(row["risk_amount"])
                        if row["risk_amount"]
                        else None,
                    )

            except Exception as e:
                log.debug(
                    f"[MONITOR] MT5 deal lookup failed for ticket {ticket_str}: {e}"
                )

    except Exception as e:
        log.debug(f"[MONITOR] MT5 outcome check failed: {e}")


_breakeven_applied: set = set()  # tickets already moved to breakeven


def _check_ccxt_outcomes() -> None:
    """Check Bybit futures for crypto positions that have been fully exited.

    Also checks open positions for 1R profit to move SL to breakeven."""

    try:
        import bybit_executor as _bybit_mod

        exchange = _bybit_mod._get_exchange()

        if not exchange:
            return

        _pos_resp = _bybit_mod.bybit_get_positions()

        positions = (
            _pos_resp.get("positions", [])
            if isinstance(_pos_resp, dict)
            else (_pos_resp or [])
        )

        open_symbols = {p["symbol"] for p in positions}

        # ── Breakeven trailing stop at 1R ──────────────────────────────────

        for pos in positions:
            try:
                ccxt_sym = pos.get("symbol", "")

                entry_px = float(pos.get("entryPrice", 0) or 0)

                cur_px = float(pos.get("markPrice", 0) or pos.get("lastPrice", 0) or 0)

                side = (pos.get("side") or "").lower()

                contracts = abs(float(pos.get("contracts", 0) or 0))

                if not entry_px or not cur_px or not contracts or not side:
                    continue

                # Find matching audit row for SL distance

                with sqlite3.connect(_AUDIT_DB, timeout=15.0) as con:
                    con.row_factory = sqlite3.Row

                    match = con.execute(
                        "SELECT ticket, sl, entry_price FROM audit_log "
                        "WHERE exit_price IS NULL AND ticket IS NOT NULL AND ticket != '' "
                        "AND (pair LIKE '%USDT%') ORDER BY ts DESC LIMIT 10"
                    ).fetchall()

                for row in match:
                    audit_entry = float(row["entry_price"] or 0)

                    audit_sl = float(row["sl"] or 0)

                    ticket = str(row["ticket"])

                    if not audit_entry or not audit_sl or ticket in _breakeven_applied:
                        continue

                    sl_dist = abs(audit_entry - audit_sl)

                    if sl_dist == 0:
                        continue

                    # Check if entry matches this position (within 1%)

                    if abs(audit_entry - entry_px) / entry_px > 0.01:
                        continue

                    # Calculate current R

                    if side == "long":
                        current_r = (cur_px - entry_px) / sl_dist

                    else:
                        current_r = (entry_px - cur_px) / sl_dist

                    if current_r >= 1.0:
                        result = _bybit_mod.bybit_move_sl_to_breakeven(
                            ccxt_sym,
                            "LONG" if side == "long" else "SHORT",
                            entry_px,
                            contracts,
                        )

                        if result.get("success"):
                            _breakeven_applied.add(ticket)

                            log.info(
                                f"[MONITOR] {ccxt_sym}: reached {current_r:.1f}R — SL moved to breakeven @ {entry_px}"
                            )

                    break  # only match one audit row per position

            except Exception as e:
                log.debug(f"[MONITOR] breakeven check error: {e}")

        with sqlite3.connect(_AUDIT_DB, timeout=15.0) as con:
            con.row_factory = sqlite3.Row

            pending = con.execute(
                "SELECT id, ticket, pair, direction, entry_price, sl, tp, volume, ts, risk_amount FROM audit_log "
                "WHERE ticket IS NOT NULL AND ticket != '' AND exit_price IS NULL "
                "AND pair LIKE '%USDT%'"
            ).fetchall()

        for row in pending:
            # pair is stored as "BTC/USDT"; map to ccxt format via internal map

            pair_raw = row["pair"] or ""

            ccxt_sym = _bybit_mod.bybit_map_symbol(
                pair_raw
            ) or _bybit_mod.bybit_map_symbol(pair_raw.replace("/", ""))

            if not ccxt_sym:
                continue

            if ccxt_sym in open_symbols:
                continue  # still holding

            try:
                trades = exchange.fetch_my_trades(ccxt_sym, limit=10)

                if trades:
                    last_trade = sorted(trades, key=lambda t: t["timestamp"])[-1]

                    # P&L: (exit - entry) * size for LONG, (entry - exit) * size for SHORT

                    exit_px = float(last_trade.get("price") or 0)

                    entry_px = float(row["entry_price"] or 0)

                    vol = float(row["volume"] or last_trade.get("amount") or 0)

                    direction = (row["direction"] or "").upper()

                    if exit_px and entry_px and vol:
                        if direction == "SHORT":
                            pnl_est = (entry_px - exit_px) * vol

                        else:  # LONG or unknown
                            pnl_est = (exit_px - entry_px) * vol

                        pnl_est = round(pnl_est, 4)

                    else:
                        pnl_est = round(
                            float(last_trade.get("info", {}).get("realizedPnl") or 0), 4
                        )

                    _update_trade_outcome(
                        ticket=str(row["ticket"]),
                        exit_price=exit_px,
                        exit_time=last_trade.get(
                            "datetime", datetime.now(timezone.utc).isoformat()
                        ),
                        pnl=pnl_est,
                        entry_price=row["entry_price"],
                        sl=row["sl"],
                        tp=row["tp"],
                        volume=row["volume"],
                        entry_ts=row["ts"],
                        risk_amount=float(row["risk_amount"])
                        if row["risk_amount"]
                        else None,
                    )

            except Exception as e:
                log.debug(f"[MONITOR] Bybit trade lookup failed for {row['pair']}: {e}")

    except Exception as e:
        log.debug(f"[MONITOR] Bybit outcome check failed: {e}")


def _start_outcome_monitor() -> None:
    """Start the trade outcome monitor as a background daemon thread."""

    t = threading.Thread(
        target=_outcome_monitor_loop, name="OutcomeMonitor", daemon=True
    )

    t.start()

    log.info("[MONITOR] Trade outcome monitor started (60s interval)")


# ── Score Decay Monitor — recalculate confluence for open positions ────────

_score_decay_results: dict = {}  # pair -> {"score": float, "entryScore": float, "ts": str}


def _check_score_decay() -> None:
    """Re-evaluate confluence for open positions; log warnings if score has decayed."""

    try:
        with sqlite3.connect(_AUDIT_DB, timeout=15.0) as con:
            con.row_factory = sqlite3.Row
            open_trades = con.execute(
                "SELECT pair, score, direction FROM audit_log WHERE exit_price IS NULL"
            ).fetchall()

        if not open_trades:
            return

        all_pairs = {p["display"]: p for p in ALL_PAIRS}

        for row in open_trades:
            pair_name = row["pair"]
            entry_score = row["score"] or 0

            pair = all_pairs.get(pair_name)

            if not pair:
                continue

            try:
                result = analyze_pair(pair, "neutral", style="swing")

                if not result:
                    continue

                cur_score = result.get("confluenceScore", 0)

                decay = entry_score - cur_score

                _score_decay_results[pair_name] = {
                    "currentScore": cur_score,
                    "entryScore": entry_score,
                    "decay": round(decay, 2),
                    "direction": row["direction"],
                    "currentDirection": result.get("direction"),
                    "ts": datetime.now(timezone.utc).isoformat(),
                }

                if decay >= 3:
                    log.warning(
                        f"[DECAY] {pair_name}: score dropped {entry_score:.1f} → {cur_score:.1f} (Δ{decay:.1f}) — consider exit"
                    )
                elif decay >= 1.5:
                    log.info(
                        f"[DECAY] {pair_name}: score softened {entry_score:.1f} → {cur_score:.1f} (Δ{decay:.1f})"
                    )

            except Exception as e:
                log.debug(f"[DECAY] {pair_name} re-eval failed: {e}")

    except Exception as e:
        log.debug(f"[DECAY] score decay check failed: {e}")


@app.route("/api/score-decay")
def api_score_decay():
    """Return score decay status for open positions."""

    return jsonify(_score_decay_results)


# â"€â"€ Task 2: Performance Dashboard Endpoint â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€


@app.route("/api/performance")
def api_performance():
    """Return performance statistics for all completed trades."""

    try:
        with sqlite3.connect(_AUDIT_DB, timeout=15.0) as con:
            con.row_factory = sqlite3.Row

            rows = con.execute(
                "SELECT * FROM audit_log WHERE exit_price IS NOT NULL ORDER BY ts ASC"
            ).fetchall()

        trades = [dict(r) for r in rows]

        if not trades:
            return jsonify({"total_trades": 0, "message": "No completed trades yet"})

        wins = [t for t in trades if (t.get("pnl") or 0) > 0]

        losses = [t for t in trades if (t.get("pnl") or 0) <= 0]

        total = len(trades)

        win_count = len(wins)

        loss_count = len(losses)

        win_rate = round(win_count / total * 100, 1) if total else 0

        r_vals = [
            t.get("r_multiple") for t in trades if t.get("r_multiple") is not None
        ]

        avg_r = round(sum(r_vals) / len(r_vals), 2) if r_vals else 0

        total_r = round(sum(r_vals), 2) if r_vals else 0

        gross_wins = sum(t.get("pnl") or 0 for t in wins)

        gross_losses = abs(sum(t.get("pnl") or 0 for t in losses))

        profit_factor = (
            round(gross_wins / gross_losses, 2) if gross_losses > 0 else None
        )

        # Max drawdown from cumulative R equity curve

        cum_r = 0
        peak = 0
        max_dd = 0

        for r in r_vals:
            cum_r += r

            if cum_r > peak:
                peak = cum_r

            dd = peak - cum_r

            if dd > max_dd:
                max_dd = dd

        max_dd_pct = round(max_dd / peak * 100, 1) if peak > 0 else 0

        # Sharpe + Sortino ratios (from R-multiples)

        _perf_avg_r = sum(r_vals) / len(r_vals) if r_vals else 0

        _perf_var = (
            sum((r - _perf_avg_r) ** 2 for r in r_vals) / (len(r_vals) - 1)
            if len(r_vals) > 1
            else 0
        )

        _perf_std = _perf_var**0.5

        perf_sharpe = (
            round(_perf_avg_r / _perf_std * (len(r_vals) ** 0.5), 2)
            if _perf_std > 0
            else 0
        )

        _perf_down = [r for r in r_vals if r < 0]

        _perf_down_var = (
            sum(r**2 for r in _perf_down) / (len(_perf_down) - 1)
            if len(_perf_down) > 1
            else 0
        )

        _perf_down_std = _perf_down_var**0.5

        perf_sortino = (
            round(_perf_avg_r / _perf_down_std * (len(r_vals) ** 0.5), 2)
            if _perf_down_std > 0
            else 0
        )

        # Win rate by regime (trendState)

        from collections import defaultdict

        regime_stats: dict = defaultdict(lambda: {"w": 0, "l": 0})

        for t in trades:
            k = t.get("trend") or t.get("regime") or "UNKNOWN"

            if (t.get("pnl") or 0) > 0:
                regime_stats[k]["w"] += 1

            else:
                regime_stats[k]["l"] += 1

        win_rate_by_regime = {
            k: round(v["w"] / (v["w"] + v["l"]) * 100, 1)
            for k, v in regime_stats.items()
            if v["w"] + v["l"] > 0
        }

        # Win rate by score band

        bands = [
            (5, 6, "5-6"),
            (6, 7, "6-7"),
            (7, 8, "7-8"),
            (8, 9, "8-9"),
            (9, 99, "9+"),
        ]

        win_rate_by_score_band: dict = {}

        for lo, hi, label in bands:
            bt = [t for t in trades if lo <= (t.get("score") or 0) < hi]

            if bt:
                bw = sum(1 for t in bt if (t.get("pnl") or 0) > 0)

                win_rate_by_score_band[label] = round(bw / len(bt) * 100, 1)

        # Win rate by asset type (inferred from pair name)

        def _pair_type(pair: str) -> str:

            if not pair:
                return "unknown"

            p = (pair or "").upper()

            if "USDT" in p:
                return "crypto"

            if any(
                c in p
                for c in [
                    "EUR",
                    "GBP",
                    "USD",
                    "JPY",
                    "AUD",
                    "NZD",
                    "CHF",
                    "CAD",
                    "ZAR",
                    "MXN",
                    "SGD",
                ]
            ):
                return "forex"

            if any(c in p for c in ["XAU", "XAG", "OIL", "GLD"]):
                return "commodity"

            if any(c in p for c in ["SPY", "QQQ", "S&P", "NASDAQ", "NAS"]):
                return "index"

            return "stock"

        asset_stats: dict = defaultdict(lambda: {"w": 0, "l": 0})

        for t in trades:
            k = _pair_type(t.get("pair", ""))

            if (t.get("pnl") or 0) > 0:
                asset_stats[k]["w"] += 1

            else:
                asset_stats[k]["l"] += 1

        win_rate_by_asset_type = {
            k: round(v["w"] / (v["w"] + v["l"]) * 100, 1)
            for k, v in asset_stats.items()
            if v["w"] + v["l"] > 0
        }

        # Best/worst pair by total R

        pair_r: dict = defaultdict(float)

        for t in trades:
            pair_r[t.get("pair", "?")] += t.get("r_multiple") or 0

        best_pair = max(pair_r, key=pair_r.get) if pair_r else None

        worst_pair = min(pair_r, key=pair_r.get) if pair_r else None

        hp_vals = [
            t.get("holding_period_hours")
            for t in trades
            if t.get("holding_period_hours") is not None
        ]

        avg_holding = round(sum(hp_vals) / len(hp_vals), 1) if hp_vals else None

        # Last 20 completed trades

        last_20 = sorted(trades, key=lambda t: t.get("ts") or "", reverse=True)[:20]

        # Equity curve: cumulative R list for charting

        equity_curve = []

        cum = 0

        for t in sorted(trades, key=lambda t: t.get("ts") or ""):
            cum += t.get("r_multiple") or 0

            equity_curve.append(round(cum, 2))

        return jsonify(
            {
                "total_trades": total,
                "win_count": win_count,
                "loss_count": loss_count,
                "win_rate": win_rate,
                "average_r_multiple": avg_r,
                "total_r": total_r,
                "profit_factor": profit_factor,
                "sharpe": perf_sharpe,
                "sortino": perf_sortino,
                "max_drawdown_pct": max_dd_pct,
                "win_rate_by_regime": win_rate_by_regime,
                "win_rate_by_score_band": win_rate_by_score_band,
                "win_rate_by_asset_type": win_rate_by_asset_type,
                "best_pair": best_pair,
                "worst_pair": worst_pair,
                "average_holding_period_hours": avg_holding,
                "equity_curve": equity_curve,
                "last_20_trades": last_20,
            }
        )

    except Exception as e:
        log.error(f"api_performance error: {e}")

        return jsonify({"error": str(e)}), 500


# Microstructure live cache — populated by WS feed callbacks, keyed by symbol (e.g. "BTCUSDT")
_micro_cache: dict = {}
_ws_clients: list = []  # WS client instances for graceful shutdown


if __name__ == "__main__":
    log.info("=" * 60)

    log.info("Sentinel Pro v4.0 - Python Edition")

    log.info("=" * 60)

    _check_api_keys()

    active_fx = sum(1 for p in FOREX_PAIRS if p.get("enabled", True))

    active_cr = sum(1 for p in CRYPTO_PAIRS if p.get("enabled", True))

    active_cmd = sum(1 for p in COMMODITY_PAIRS if p.get("enabled", True))

    active_idx = sum(1 for p in INDEX_PAIRS if p.get("enabled", True))

    active_stock = sum(
        1 for p in (US_STOCK_PAIRS + ETF_PAIRS) if p.get("enabled", True)
    )

    active_jse = sum(1 for p in JSE_PAIRS if p.get("enabled", True))

    log.info(
        f"Pairs: {len(ACTIVE_PAIRS)} active / {len(ALL_PAIRS)} total "
        f"({active_fx}fx {active_cmd}cmd {active_idx}idx {active_stock}us {active_jse}jse {active_cr}crypto)"
    )

    log.info("Data: EODHD + Polygon + yfinance + Binance")

    log.info("Est. scan time: ~30s")

    if "--scan" in sys.argv:
        log.info("[SCAN MODE] Running full scan...")

        scan_result = run_full_scan()

        log.info(
            f"Scan complete: {scan_result['totalPairs']} pairs, {len(scan_result['signals'])} signals"
        )

        if scan_result["errors"]:
            log.warning(
                f"Errors ({len(scan_result['errors'])}): {[e['pair'] + ': ' + e['error'] for e in scan_result['errors']]}"
            )

        if scan_result["skipped"]:
            log.info(
                f"Skipped ({len(scan_result['skipped'])}): {[s['pair'] for s in scan_result['skipped']]}"
            )

        if scan_result["signals"]:
            if CONFIG.get("AI_ON_DEMAND_ONLY", False):
                log.info("[AI TEST] Skipped -- AI_ON_DEMAND_ONLY is enabled")
            else:
                log.info("[AI TEST] Testing AI on top signal...")

                top = scan_result["signals"][0]

                ai_result = run_ai(top)

                if "error" in ai_result:
                    log.error(f"[AI TEST] FAILED: {ai_result['error']}")

                else:
                    log.info(
                        f"[AI TEST] OK => Grade:{ai_result.get('grade', '?')} Prob:{ai_result.get('edgeProbability', '?')}%"
                    )

        else:
            log.info("[AI TEST] Skipped -- no signals to test")

        sys.exit(0)

    # Start EODHD WebSocket real-time price streaming + candle builder

    _ws_key = os.environ.get("EODHD_KEY", "")

    if _ws_key:
        _ws_mgr = EODHDWebSocketManager(_ws_key)

        _ws_mgr.start(ACTIVE_PAIRS)

        _candle_builder = CandleBuilder()

        def _cb_startup():

            _candle_builder.seed(ALL_PAIRS)  # seed 6mo H1/H4/D1

            _candle_builder.bulk_update_d1()  # fresh D1 from Bulk API

            _candle_builder.start_refresh_loop()  # bulk D1 every 4h

        threading.Thread(target=_cb_startup, daemon=True, name="candle-seed").start()

        log.info("[CB] Candle builder started (WS ticks + 6mo seed + Bulk D1 every 4h)")

    else:
        log.warning("[WS] No EODHD_KEY â€” WebSocket prices disabled")

    # Start Binance Futures WebSocket for crypto live prices
    crypto_enabled = [p for p in CRYPTO_PAIRS if p.get("enabled", True)]
    if crypto_enabled:
        _binance_ws = BinanceLivePriceWS()
        _binance_ws.start()
        log.info(f"[BINANCE-WS] Started for {len(crypto_enabled)} enabled crypto pairs")
    else:
        log.info("[BINANCE-WS] No enabled crypto pairs - Binance Futures WS disabled")

    # Microstructure WebSocket feeds (Binance + Bybit orderbook/trade streams)
    if CONFIG.get("MICROSTRUCTURE_FEEDS_ENABLED", False):

        def _start_micro_feeds():
            import asyncio

            try:
                from athena.datafeeds.binance_ws import BinanceWS
                from athena.datafeeds.bybit_ws import BybitWS
            except ImportError as exc:
                log.warning(f"[MICRO] Import failed: {exc}")
                return
            crypto_pairs = [p for p in CRYPTO_PAIRS if p.get("enabled", False)]

            def _make_cb(sym):
                def _cb(metrics):
                    _micro_cache[sym] = {
                        k: metrics.get(k)
                        for k in (
                            "order_book_imbalance",
                            "orderflow_delta",
                            "liquidity_wall_detection",
                            "liquidity_pressure",
                        )
                    }

                return _cb

            def _run(client, cb):
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
                try:
                    loop.run_until_complete(client.start(cb))
                    loop.run_forever()
                except Exception as exc:
                    log.warning(f"[MICRO] WS error: {exc}")
                finally:
                    loop.close()

            for pair in crypto_pairs:
                sym = pair["symbol"]
                cb = _make_cb(sym)
                b = BinanceWS(sym.lower())
                y = BybitWS(sym.upper())
                _ws_clients.extend([b, y])
                threading.Thread(
                    target=_run, args=(b, cb), daemon=True, name=f"BinWS-{sym}"
                ).start()
                threading.Thread(
                    target=_run, args=(y, cb), daemon=True, name=f"BbtWS-{sym}"
                ).start()
            log.info(
                f"[MICRO] Started Binance+Bybit feeds for {len(crypto_pairs)} enabled crypto pairs"
            )

        threading.Thread(
            target=_start_micro_feeds, daemon=True, name="MicroFeeds"
        ).start()
    else:
        log.info(
            "[MICRO] Microstructure feeds disabled (set MICROSTRUCTURE_FEEDS_ENABLED: true to enable)"
        )

    # Startup position reconciliation — check for open positions on restart

    def _startup_reconcile():
        """On startup, check MT5/Bybit for open positions and log them."""

        time.sleep(5)  # wait for connections to establish

        try:
            from mt5_executor import mt5_get_positions, mt5_connect

            if mt5_connect():
                _pos_resp = mt5_get_positions()

                mt5_pos = (
                    _pos_resp.get("positions", [])
                    if isinstance(_pos_resp, dict)
                    else (_pos_resp or [])
                )

                if mt5_pos:
                    log.info(f"[STARTUP] MT5: {len(mt5_pos)} open position(s)")

                    for p in mt5_pos:
                        log.info(
                            f"  - {p.get('symbol')} {p.get('type', '?')} vol={p.get('volume')} entry={p.get('price_open')} P&L={p.get('profit')}"
                        )

        except Exception as e:
            log.debug(f"[STARTUP] MT5 reconcile skipped: {e}")

        try:
            import bybit_executor as _bm

            _pos_resp = _bm.bybit_get_positions()

            bpos = (
                _pos_resp.get("positions", [])
                if isinstance(_pos_resp, dict)
                else (_pos_resp or [])
            )

            if bpos:
                log.info(f"[STARTUP] Bybit: {len(bpos)} open position(s)")

                for p in bpos:
                    log.info(
                        f"  - {p.get('symbol')} {p.get('side')} size={p.get('contracts')} entry={p.get('entryPrice')} uPnL={p.get('unrealizedPnl')}"
                    )

        except Exception as e:
            log.debug(f"[STARTUP] Bybit reconcile skipped: {e}")

        # Check audit DB for unresolved trades

        try:
            with sqlite3.connect(_AUDIT_DB, timeout=15.0) as con:
                con.row_factory = sqlite3.Row

                unresolved = con.execute(
                    "SELECT pair, direction, entry_price, ticket FROM audit_log "
                    "WHERE exit_price IS NULL AND ticket IS NOT NULL AND ticket != ''"
                ).fetchall()

            if unresolved:
                log.warning(
                    f"[STARTUP] {len(unresolved)} unresolved trade(s) in audit DB:"
                )

                for r in unresolved:
                    log.warning(
                        f"  - {r['pair']} {r['direction']} entry={r['entry_price']} ticket={r['ticket']}"
                    )

        except Exception as e:
            log.debug(f"[STARTUP] Audit DB reconcile failed: {e}")

    threading.Thread(
        target=_startup_reconcile, daemon=True, name="StartupReconcile"
    ).start()

    # Seed Dukascopy forex volume cache in background (skips days already cached)
    def _duka_seed():
        try:
            from duka_volume import seed_all_forex

            seed_all_forex(days=90, workers=3)
        except Exception as e:
            log.warning(f"[DUKA] Startup seed failed: {e}")

    threading.Thread(target=_duka_seed, daemon=True, name="DukaSeed").start()

    # Seed COT (CFTC) and carry (FRED) data in background
    try:
        from cot_feed import seed_cot_background

        seed_cot_background()
    except Exception as e:
        log.warning(f"[COT] Startup seed failed: {e}")
    try:
        from carry_feed import seed_carry_background

        seed_carry_background()
    except Exception as e:
        log.warning(f"[CARRY] Startup seed failed: {e}")

    # Graceful shutdown handler — clean up connections on SIGINT/SIGTERM

    def _graceful_shutdown(signum, frame):

        sig_name = "SIGINT" if signum == _signal.SIGINT else "SIGTERM"

        log.warning(f"[SHUTDOWN] {sig_name} received — shutting down gracefully...")

        try:
            from bybit_executor import bybit_disconnect

            bybit_disconnect()

        except Exception:
            pass

        try:
            from mt5_executor import mt5_disconnect

            mt5_disconnect()

        except Exception:
            pass

        # Stop microstructure WS clients
        for _wsc in _ws_clients:
            try:
                _wsc._running = False
            except Exception:
                pass
        log.info("[SHUTDOWN] Connections closed. Exiting.")

        sys.exit(0)

    _signal.signal(_signal.SIGINT, _graceful_shutdown)

    _signal.signal(_signal.SIGTERM, _graceful_shutdown)

    log.info("http://localhost:5000")

    threading.Timer(1.5, lambda: webbrowser.open("http://localhost:5000")).start()

    _start_outcome_monitor()

    if CONFIG.get("AUTO_TRADE_ENABLED", False):
        _auto_trader.enable()

        log.info("[AUTO] Auto-trader ENABLED via config")

    else:
        # Start the scheduler thread in standby — it does nothing until enabled

        _auto_trader._running = True

        import threading as _t

        _t.Thread(
            target=_auto_trader._scheduler_loop, name="AutoTrader", daemon=True
        ).start()

        log.info("[AUTO] Auto-trader standby (toggle via UI)")

    _host = os.environ.get(
        "ATHENA_HOST", "127.0.0.1"
    )  # default localhost; set to 0.0.0.0 in .env for LAN

    # Backup databases at startup — protects against data loss during updates
    try:
        from backup_db import backup_now

        backup_now(reason="startup")
        log.info("[BACKUP] Database backup completed at startup")
    except Exception as _bak_e:
        log.warning(f"[BACKUP] Startup backup failed: {_bak_e}")

    app.run(host=_host, port=5000, debug=False)
