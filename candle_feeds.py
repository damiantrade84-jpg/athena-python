"""Live prices, EODHD/Binance WebSockets, and CandleBuilder (WS → SQLite).

Uses `athena_runtime.rt()` for pair lists and `eodhd_ticker_for_pair` so this module
imports before `ALL_PAIRS` / `_NON_WS_EODHD` exist in the monolith.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import sqlite3
import threading
import time
from datetime import datetime, timedelta, timezone

from athena_runtime import rt
from data_feeds import _get_eodhd_client, http_requests

log = logging.getLogger("sentinel")

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
        crypto_symbols = {
            pair["symbol"].replace("/", ""): pair["display"]
            for pair in _cp
            if pair.get("enabled", True)
        }

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
                    ) as ws:
                        log.info(
                            "[BINANCE-WS] Connected to fstream.binance.com !ticker@arr"
                        )
                        while self._running:
                            try:
                                data = await asyncio.wait_for(ws.recv(), timeout=45)
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
                            except json.JSONDecodeError as e:
                                log.warning(
                                    f"[BINANCE-WS] Non-JSON payload, reconnecting: {e}"
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


class BinanceCandleWS:
    """Binance Futures kline WebSocket — feeds H1 OHLCV into CandleBuilder for crypto.

    Subscribes to @kline_1h for each enabled crypto pair via the combined stream
    endpoint so a single WS connection covers all pairs.  CandleBuilder accumulates
    H1 ticks and rolls them up to H4/D1 automatically.
    """

    def __init__(self):
        self._running = True
        self._thread = None

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

        symbol_map = {
            p["symbol"].replace("/", "").lower(): p["display"]
            for p in enabled
        }

        streams = "/".join(f"{sym}@kline_1h" for sym in symbol_map)
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
                    ) as ws:
                        log.info(
                            f"[BN-KLINE-WS] Connected — {len(symbol_map)} crypto pairs on @kline_1h"
                        )
                        while self._running:
                            try:
                                raw = await asyncio.wait_for(ws.recv(), timeout=90)
                                msg = json.loads(raw)
                                data = msg.get("data", {})
                                k = data.get("k")
                                if not k:
                                    continue
                                sym_lower = data.get("s", "").lower()
                                display = symbol_map.get(sym_lower)
                                if not display:
                                    continue
                                _cb = get_candle_builder()
                                if _cb:
                                    _cb.on_tick(
                                        display,
                                        float(k["c"]),
                                        float(k.get("v", 0)),
                                        int(k.get("t", 0)),
                                    )
                            except asyncio.TimeoutError:
                                log.warning("[BN-KLINE-WS] Receive timeout, reconnecting...")
                                break
                            except json.JSONDecodeError as e:
                                log.warning(f"[BN-KLINE-WS] JSON error: {e}")
                                break
                            except Exception as e:
                                log.error(f"[BN-KLINE-WS] Process error: {e}")
                                break

                loop.run_until_complete(_stream())

            except Exception as e:
                log.error(f"[BN-KLINE-WS] Connection error: {e}")
                if self._running:
                    time.sleep(5)
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
                async with websockets.connect(
                    url,
                    ping_interval=None,
                    ping_timeout=None,
                    open_timeout=45,
                    close_timeout=10,
                ) as ws:
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
                log.warning(f"[WS] {endpoint} disconnected: {e} — reconnecting in 5s")

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

    def _seed_crypto(self, pair: dict):
        """Seed candle_cache for a single crypto pair from Binance REST klines."""
        disp = pair["display"]
        bn_sym = pair["symbol"].replace("/", "")

        with sqlite3.connect(self._db, timeout=15.0) as con:
            cnt = con.execute(
                "SELECT COUNT(*) FROM candle_cache WHERE pair=? AND timeframe='H1'",
                (disp,),
            ).fetchone()[0]
        if cnt >= 100:
            return

        for tf_label, interval, limit in [("D1", "1d", 365), ("H4", "4h", 500), ("H1", "1h", 500)]:
            try:
                resp = http_requests.get(
                    "https://api.binance.com/api/v3/klines",
                    params={"symbol": bn_sym, "interval": interval, "limit": limit},
                    timeout=15,
                )
                if resp.status_code != 200:
                    continue
                data = resp.json()
                rows = [
                    (
                        disp, tf_label,
                        datetime.fromtimestamp(k[0] / 1000, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
                        float(k[1]), float(k[2]), float(k[3]), float(k[4]),
                        float(k[5]), 0,
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
