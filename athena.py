#!/usr/bin/env python3
"""ATHENA PRO v3.1 - Trading Intelligence Engine (Python Edition)"""
# Windows CMD: force unbuffered output so all prints show immediately
import sys
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(line_buffering=True)
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(line_buffering=True)
import os, sys, json, math, time, threading, webbrowser, logging, sqlite3
from datetime import datetime, timezone
from flask import Flask, jsonify, request, send_from_directory
import requests as _requests_mod
# C2: Use requests.Session for connection pooling across all HTTP calls
http_requests = _requests_mod.Session()
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass  # dotenv optional — falls back to os.environ
from eodhd import APIClient as _EODHDClient
_eodhd_client = None
def _get_eodhd_client():
    global _eodhd_client
    if _eodhd_client is None:
        _key = os.environ.get("EODHD_KEY", "")
        if _key: _eodhd_client = _EODHDClient(_key)
    return _eodhd_client

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s", datefmt="%H:%M:%S")
log = logging.getLogger("athena")

# ── EODHD WebSocket Real-Time Price Manager ──
import asyncio
_live_prices = {}  # thread-safe via GIL for simple dict reads/writes
_ws_manager_started = False

class EODHDWebSocketManager:
    """Manages 3 persistent WebSocket connections (US, Forex, Crypto) for real-time prices."""
    WS_BASE = "wss://ws.eodhistoricaldata.com/ws"

    def __init__(self, api_key):
        self._key = api_key
        self._loop = None
        self._thread = None

    def _build_ticker_map(self, pairs):
        """Map Athena display names → {ws_endpoint: [ws_tickers], display_map: {ws_ticker: display}}"""
        us_tickers, fx_tickers, cr_tickers = [], [], []
        display_map = {}  # ws_ticker → athena display name
        for p in pairs:
            disp, ptype = p["display"], p.get("type", "")
            sym = p.get("symbol", "")
            if ptype == "crypto":
                base = disp.split("/")[0] if "/" in disp else disp.replace("USDT", "")
                ws_t = f"{base}-USD"
                cr_tickers.append(ws_t)
                display_map[ws_t] = disp
            elif ptype in ("stock",) and ".US" in sym:
                ws_t = sym.replace(".US", "")
                us_tickers.append(ws_t)
                display_map[ws_t] = disp
            elif ptype in ("forex", "commodity") or ("/" in disp and ptype != "crypto"):
                ws_t = disp.replace("/", "")
                fx_tickers.append(ws_t)
                display_map[ws_t] = disp
        return {"us": us_tickers, "forex": fx_tickers, "crypto": cr_tickers, "map": display_map}

    async def _connect(self, endpoint, tickers, display_map):
        """Connect to one WS endpoint, subscribe, and stream prices forever."""
        import websockets
        url = f"{self.WS_BASE}/{endpoint}?api_token={self._key}"
        while True:
            try:
                async with websockets.connect(url, ping_interval=30, ping_timeout=10) as ws:
                    sub = json.dumps({"action": "subscribe", "symbols": ",".join(tickers)})
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
                                if v is None: return None
                                try: return float(v)
                                except (ValueError, TypeError): return None
                            entry = {"ts": msg.get("t", 0)}
                            if endpoint == "forex":
                                bid, ask = _f("b"), _f("a")
                                entry["bid"] = bid
                                entry["ask"] = ask
                                entry["price"] = round((bid + ask) / 2, 6) if bid and ask else None
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
                                entry["changePct"] = _f("dc")
                                entry["changeDiff"] = _f("dd")
                            elif endpoint == "us":
                                entry["price"] = _f("p")
                                entry["volume"] = msg.get("v")
                                entry["marketStatus"] = msg.get("ms", "unknown")
                            if entry.get("price"):
                                _live_prices[disp] = entry
                        except Exception as _e:
                            log.debug(f"[WS] msg parse error: {_e}")
            except Exception as e:
                log.warning(f"[WS] {endpoint} disconnected: {e} — reconnecting in 5s")
                await asyncio.sleep(5)

    async def _run_all(self, pairs):
        ticker_info = self._build_ticker_map(pairs)
        tasks = []
        for ep, tickers in [("us", ticker_info["us"]), ("forex", ticker_info["forex"]), ("crypto", ticker_info["crypto"])]:
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
            self._loop.run_until_complete(self._run_all(pairs))
        self._thread = threading.Thread(target=_run, daemon=True, name="eodhd-ws")
        self._thread.start()
        log.info("[WS] WebSocket manager started")

# N1: CONFIG loaded from config.py (YAML overrides + validation happen there)
from config import CONFIG

# enabled=False = negative backtest SQN, excluded from live scan (still available in backtest)
FOREX_PAIRS = [
    {"symbol":"EURUSD=X","type":"forex","display":"EUR/USD","source":"eodhd","enabled":False},  # SQN +0.16 (Phase A, weak — cut)
    {"symbol":"GBPUSD=X","type":"forex","display":"GBP/USD","source":"eodhd","enabled":False},  # SQN -0.53 (Phase A)
    {"symbol":"USDJPY=X","type":"forex","display":"USD/JPY","source":"eodhd","enabled":False},  # SQN -2.33 (Phase A)
    {"symbol":"AUDUSD=X","type":"forex","display":"AUD/USD","source":"eodhd","enabled":False},  # SQN +0.46 (Phase A, weak — cut)
    {"symbol":"NZDUSD=X","type":"forex","display":"NZD/USD","source":"eodhd","enabled":False},  # SQN  0.00 (Phase A, zero trades)
    {"symbol":"EURGBP=X","type":"forex","display":"EUR/GBP","source":"eodhd","enabled":False},  # SQN +0.65 (Phase A, watchlist)
    {"symbol":"USDCAD=X","type":"forex","display":"USD/CAD","source":"eodhd","enabled":False},  # SQN +0.27 (Phase A, weak — cut)
    {"symbol":"USDCHF=X","type":"forex","display":"USD/CHF","source":"eodhd","enabled":True},   # v3.1 SQN +0.61, OOS +1.22 ✔ (Polygon data)
    {"symbol":"EURJPY=X","type":"forex","display":"EUR/JPY","source":"eodhd","enabled":False},  # SQN +1.06, IS:-0.23 (IS neg, watchlist)
    {"symbol":"GBPJPY=X","type":"forex","display":"GBP/JPY","source":"eodhd","enabled":False},  # SQN -0.72
    {"symbol":"AUDJPY=X","type":"forex","display":"AUD/JPY","source":"eodhd","enabled":False},  # SQN +0.22
    {"symbol":"EURAUD=X","type":"forex","display":"EUR/AUD","source":"eodhd","enabled":False},  # SQN +0.15
    {"symbol":"GBPAUD=X","type":"forex","display":"GBP/AUD","source":"eodhd","enabled":False},  # SQN +0.91, IS:-1.38 (IS neg, watchlist)
    {"symbol":"USDZAR=X","type":"forex","display":"USD/ZAR","source":"eodhd","enabled":False},  # SQN -0.32
    {"symbol":"EURCHF=X","type":"forex","display":"EUR/CHF","source":"eodhd","enabled":False},  # SQN -0.83
    {"symbol":"USDMXN=X","type":"forex","display":"USD/MXN","source":"eodhd","enabled":True},   # SQN +0.85, IS:+0.61/OOS:+0.60 ✔
    {"symbol":"USDSGD=X","type":"forex","display":"USD/SGD","source":"eodhd","enabled":False},  # SQN +0.43
]
COMMODITY_PAIRS = [
    {"symbol":"GC=F","type":"commodity","display":"XAU/USD","source":"eodhd","enabled":True},     # SQN +3.53 (v3.1) ✓ → EODHD D1, Polygon H4/H1 fallback
    {"symbol":"SI=F","type":"commodity","display":"XAG/USD","source":"eodhd","enabled":True},     # SQN +3.08 (v3.1) ✓ → EODHD D1, Polygon H4/H1 fallback
    {"symbol":"CL=F","type":"commodity","display":"WTI Oil","source":"eodhd","enabled":False},     # SQN -1.36 (Phase A, cut)
]
INDEX_PAIRS = [
    {"symbol":"^GSPC","type":"index","display":"S&P 500","source":"eodhd","enabled":False},       # SQN +0.09 (Phase A, weak — cut)
    {"symbol":"^IXIC","type":"index","display":"Nasdaq","source":"eodhd","enabled":False},         # SQN -0.20 (Phase A)
    {"symbol":"^DJI","type":"index","display":"Dow Jones","source":"eodhd","enabled":False},      # SQN +0.80 (Phase A, watchlist)
]
US_STOCK_PAIRS = [
    {"symbol":"AAPL.US","type":"stock","display":"AAPL","source":"eodhd","enabled":False},        # SQN -0.30
    {"symbol":"TSLA.US","type":"stock","display":"TSLA","source":"eodhd","enabled":False},        # SQN +0.10
    {"symbol":"NVDA.US","type":"stock","display":"NVDA","source":"eodhd","enabled":False},        # SQN +1.44, OOS:0 (no OOS, watchlist)
    {"symbol":"MSFT.US","type":"stock","display":"MSFT","source":"eodhd","enabled":False},        # SQN +0.49, OOS:-2.12
    {"symbol":"AMZN.US","type":"stock","display":"AMZN","source":"eodhd","enabled":False},        # SQN +0.27
    {"symbol":"META.US","type":"stock","display":"META","source":"eodhd","enabled":False},        # SQN -0.29
    {"symbol":"GOOG.US","type":"stock","display":"GOOG","source":"eodhd","enabled":True},         # SQN +1.61, IS:+1.28/OOS:+1.01 ✔
    {"symbol":"JPM.US","type":"stock","display":"JPM","source":"eodhd","enabled":False},          # SQN +0.26
    {"symbol":"V.US","type":"stock","display":"V","source":"eodhd","enabled":False},              # SQN -1.39
    {"symbol":"XOM.US","type":"stock","display":"XOM","source":"eodhd","enabled":False},          # SQN -0.03
]
ETF_PAIRS = [
    {"symbol":"SPY.US","type":"stock","display":"SPY","source":"eodhd","enabled":False},          # SQN +1.03, OOS:0 (no OOS, watchlist)
    {"symbol":"QQQ.US","type":"stock","display":"QQQ","source":"eodhd","enabled":False},          # SQN +0.38
    {"symbol":"GLD.US","type":"stock","display":"GLD","source":"eodhd","enabled":True},           # SQN +2.08, IS:+0.95/OOS:+2.98 ✔ Gold ETF
    {"symbol":"TLT.US","type":"stock","display":"TLT","source":"eodhd","enabled":False},          # SQN 0 — Treasury ETF
    {"symbol":"FTSE.INDX","type":"index","display":"FTSE 100","source":"eodhd","enabled":False},  # no data from EODHD
]
JSE_PAIRS = [
    {"symbol":"NPN.JO","type":"stock","display":"Naspers","source":"eodhd","enabled":True},       # SQN +1.43 (Phase A) ✓ → EODHD D1, yfinance H4/H1 fallback
    {"symbol":"SOL.JO","type":"stock","display":"Sasol","source":"eodhd","enabled":False},        # SQN -1.11 (Phase A)
    {"symbol":"SBK.JO","type":"stock","display":"Std Bank","source":"eodhd","enabled":False},     # SQN +0.14 (Phase A, weak — cut)
    {"symbol":"AGL.JO","type":"stock","display":"Anglo Am","source":"eodhd","enabled":True},      # v3.1 SQN +0.67, OOS +1.01 ✔ → EODHD D1, yfinance H4/H1 fallback
    {"symbol":"MTN.JO","type":"stock","display":"MTN Group","source":"eodhd","enabled":False},    # SQN ? (missing intraday)
    {"symbol":"SHP.JO","type":"stock","display":"Shoprite","source":"eodhd","enabled":False},     # SQN -0.83
    {"symbol":"CFR.JO","type":"stock","display":"Richemont","source":"eodhd","enabled":False},    # SQN -2.35
    {"symbol":"FSR.JO","type":"stock","display":"FirstRand","source":"eodhd","enabled":False},    # SQN -2.40
    {"symbol":"ABG.JO","type":"stock","display":"Absa","source":"eodhd","enabled":False},         # SQN +1.41, IS:-1.15 (IS neg, watchlist)
    {"symbol":"CPI.JO","type":"stock","display":"Capitec","source":"eodhd","enabled":False},      # SQN -0.78
    {"symbol":"PRX.JO","type":"stock","display":"Prosus","source":"eodhd","enabled":True},        # SQN +1.54, IS:+1.52/OOS:+0.34 ✔
    {"symbol":"GFI.JO","type":"stock","display":"Gold Fields","source":"eodhd","enabled":False},  # SQN +1.43, OOS:-0.16 (watchlist)
    {"symbol":"ANG.JO","type":"stock","display":"AngloGold","source":"eodhd","enabled":False},    # SQN +0.39
    {"symbol":"SSW.JO","type":"stock","display":"Sibanye","source":"eodhd","enabled":False},      # SQN +0.39
]
CRYPTO_PAIRS = [
    {"symbol":"BTCUSDT","type":"crypto","display":"BTC/USDT","source":"binance","enabled":False},  # SQN -0.81 Phase A / pre-PhaseA: +0.18 (borderline, re-test)
    {"symbol":"ETHUSDT","type":"crypto","display":"ETH/USDT","source":"binance","enabled":False},  # SQN +0.50 Phase A (weak)
    {"symbol":"XRPUSDT","type":"crypto","display":"XRP/USDT","source":"binance","enabled":False},  # SQN -0.80 Phase A
    {"symbol":"SOLUSDT","type":"crypto","display":"SOL/USDT","source":"binance","enabled":False},  # v3.1 SQN +0.02 (improved from -0.64 but still weak)
    {"symbol":"ADAUSDT","type":"crypto","display":"ADA/USDT","source":"binance","enabled":False},  # SQN -0.80 Phase A
    {"symbol":"DOGEUSDT","type":"crypto","display":"DOGE/USDT","source":"binance","enabled":False}, # SQN -0.64 Phase A
    {"symbol":"AVAXUSDT","type":"crypto","display":"AVAX/USDT","source":"binance","enabled":False}, # v3.1 SQN +0.44, OOS +0.02 (overfit)
    {"symbol":"LINKUSDT","type":"crypto","display":"LINK/USDT","source":"binance","enabled":True},  # v3.1 SQN +0.93, OOS +1.00 ✔ (regime-adaptive)
    {"symbol":"MATICUSDT","type":"crypto","display":"MATIC/USDT","source":"binance","enabled":False},# SQN +0.26 Phase A (noise)
    {"symbol":"BNBUSDT","type":"crypto","display":"BNB/USDT","source":"binance","enabled":False},  # v3.1 SQN -0.55 (negative edge)
    {"symbol":"DOTUSDT","type":"crypto","display":"DOT/USDT","source":"binance","enabled":False},  # SQN -0.36 Phase A
    {"symbol":"LTCUSDT","type":"crypto","display":"LTC/USDT","source":"binance","enabled":True},   # v3.1 SQN +0.97 (improved from -0.16, near threshold)
    {"symbol":"SUIUSDT","type":"crypto","display":"SUI/USDT","source":"binance","enabled":True},   # SQN +0.76, IS:+0.43/OOS:+0.82 ✔
    {"symbol":"NEARUSDT","type":"crypto","display":"NEAR/USDT","source":"binance","enabled":False},# SQN -1.27
    {"symbol":"APTUSDT","type":"crypto","display":"APT/USDT","source":"binance","enabled":True},   # SQN +0.67, IS:+0.56/OOS:+0.37 ✔
    {"symbol":"INJUSDT","type":"crypto","display":"INJ/USDT","source":"binance","enabled":False},  # SQN -2.01
    {"symbol":"FETUSDT","type":"crypto","display":"FET/USDT","source":"binance","enabled":False},  # SQN +0.14
    {"symbol":"RENDERUSDT","type":"crypto","display":"RENDER/USDT","source":"binance","enabled":False}, # SQN -2.45
]
ALL_PAIRS = FOREX_PAIRS + COMMODITY_PAIRS + INDEX_PAIRS + US_STOCK_PAIRS + ETF_PAIRS + JSE_PAIRS + CRYPTO_PAIRS
ACTIVE_PAIRS = [p for p in ALL_PAIRS if p.get("enabled", True)]
TF_B = {"D1":"1d","H4":"4h","H1":"1h"}

def fetch_yfinance(sym, tf, limit):
    """Download OHLCV candles from Yahoo Finance. Returns list of candle dicts or None."""
    try:
        import yfinance as yf
        import pandas as pd
        period = "2y" if tf == "D1" else "730d"
        interval = "1d" if tf == "D1" else "1h"
        df = yf.download(sym, period=period, interval=interval, progress=False, auto_adjust=True)
        if df is None or df.empty: log.warning(f"[YF] {sym}: no data"); return None
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = [col[0] for col in df.columns]
        if tf == "H4":
            df = df.resample("4h").agg({"Open":"first","High":"max","Low":"min","Close":"last","Volume":"sum"}).dropna()
        df = df.tail(limit)
        return [{"time":str(idx.date() if tf=="D1" else idx),"open":float(r["Open"]),"high":float(r["High"]),"low":float(r["Low"]),"close":float(r["Close"]),"vol":float(r.get("Volume",0))} for idx,r in df.iterrows()]
    except Exception as e: log.error(f"[YF] {sym}: {e}"); return None

def fetch_binance(sym, interval, limit):
    """Download OHLCV candles from Binance REST API with failover endpoint."""
    try:
        for base in ["https://api.binance.com","https://api1.binance.com"]:
            r = http_requests.get(f"{base}/api/v3/klines", params={"symbol":sym,"interval":interval,"limit":limit}, timeout=15)
            if r.status_code == 200:
                return [{"time":datetime.fromtimestamp(k[0]/1000,tz=timezone.utc).isoformat(),"open":float(k[1]),"high":float(k[2]),"low":float(k[3]),"close":float(k[4]),"vol":float(k[5])} for k in r.json()]
            log.warning(f"[BN] {sym} HTTP {r.status_code}: {r.text[:120]}")
        return None
    except Exception as e: log.error(f"[BN] {sym}: {e}"); return None

def fetch_eodhd(pair, tf, limit):
    """Download OHLCV candles via EODHD SDK (APIClient). Covers forex, stocks, indices — 1000 req/min."""
    try:
        api = _get_eodhd_client()
        if not api: log.warning("[EODHD] No EODHD_KEY set"); return None
        # Derive EODHD ticker from pair config
        _disp = pair["display"]
        _ptype = pair.get("type", "")
        if _ptype in ("forex", "commodity") or "/" in _disp:
            ticker = _disp.replace("/", "") + ".FOREX"
        elif _ptype == "stock" and ".JO" in pair["symbol"]:
            ticker = pair["symbol"].replace(".JO", ".JSE")
        elif _ptype == "index":
            ticker = pair["symbol"] if ".INDX" in pair["symbol"] else pair["symbol"].lstrip("^") + ".INDX"
        else:
            ticker = pair["symbol"]
        if tf == "D1":
            from datetime import timedelta
            start = (datetime.now(timezone.utc) - timedelta(days=730)).strftime("%Y-%m-%d")
            bars = api.get_eod_historical_stock_market_data(ticker, period="d", from_date=start, order="a")
            if not bars: log.warning(f"[EODHD] {ticker} D1: no data"); return None
            candles = [{"time": b["date"], "open": float(b["open"]), "high": float(b["high"]),
                        "low": float(b["low"]), "close": float(b["close"]),
                        "vol": float(b.get("volume") or 0)} for b in bars]
        else:
            # H1 or H4 — SDK intraday (H4 = fetch H1 then resample)
            from datetime import timedelta
            start_ts = int((datetime.now(timezone.utc) - timedelta(days=120)).timestamp())
            bars = api.get_intraday_historical_data(ticker, interval="1h", from_unix_time=start_ts)
            if not bars:
                # Auto-fallback: EODHD has no intraday for some assets (commodities, JSE)
                if _ptype == "commodity" or "/" in _disp:
                    log.info(f"[EODHD] {ticker} {tf}: no intraday — fallback to Polygon")
                    return fetch_polygon(pair, tf, limit)
                elif _ptype == "stock":
                    log.info(f"[EODHD] {ticker} {tf}: no intraday — fallback to yfinance")
                    return fetch_yfinance(pair["symbol"], tf, limit)
                log.warning(f"[EODHD] {ticker} {tf}: no data"); return None
            candles = [{"time": b["datetime"], "open": float(b["open"]), "high": float(b["high"]),
                        "low": float(b["low"]), "close": float(b["close"]),
                        "vol": float(b.get("volume") or 0)} for b in bars
                       if b.get("open") is not None and b.get("close") is not None]
            if tf == "H4" and len(candles) >= 4:
                import pandas as pd
                df = pd.DataFrame(candles)
                df["time"] = pd.to_datetime(df["time"])
                df = df.set_index("time").resample("4h").agg({"open":"first","high":"max","low":"min","close":"last","vol":"sum"}).dropna()
                candles = [{"time": str(idx), "open": float(r["open"]), "high": float(r["high"]),
                            "low": float(r["low"]), "close": float(r["close"]), "vol": float(r["vol"])}
                           for idx, r in df.iterrows()]
        return candles[-limit:] if len(candles) > limit else candles
    except Exception as e: log.error(f"[EODHD] {pair['display']}: {e}"); return None

def fetch_eodhd_indicators(pair):
    """Phase 5: Fetch server-side indicator snapshots via EODHD filter=last_X. Returns dict or None."""
    try:
        _key = os.environ.get("EODHD_KEY", "")
        if not _key: return None
        _disp, _ptype = pair["display"], pair.get("type", "")
        if _ptype == "crypto":
            base_sym = _disp.split("/")[0] if "/" in _disp else _disp.replace("USDT","")
            ticker = f"{base_sym}-USD.CC"
        elif _ptype in ("forex", "commodity") or ("/" in _disp and _ptype != "crypto"):
            ticker = _disp.replace("/", "") + ".FOREX"
        elif _ptype == "stock" and ".JO" in pair["symbol"]:
            ticker = pair["symbol"].replace(".JO", ".JSE")
        elif _ptype == "index":
            ticker = pair["symbol"] if ".INDX" in pair["symbol"] else pair["symbol"].lstrip("^") + ".INDX"
        else:
            ticker = pair["symbol"]
        base = f"https://eodhd.com/api/technical/{ticker}?api_token={_key}&fmt=json"
        indicators = [
            ("EMA21", "function=ema&period=21&filter=last_ema"),
            ("EMA50", "function=ema&period=50&filter=last_ema"),
            ("RSI",   "function=rsi&period=14&filter=last_rsi"),
            ("ADX",   "function=adx&period=14&filter=last_adx"),
            ("ATR",   "function=atr&period=14&filter=last_atr"),
            ("MACD",  "function=macd&filter=last_macd"),
            ("SAR",   "function=sar&filter=last_sar"),
        ]
        result = {}
        for name, params in indicators:
            try:
                r = http_requests.get(f"{base}&{params}", timeout=6)
                if r.status_code == 200:
                    val = r.json()
                    if isinstance(val, (int, float)): result[name] = round(float(val), 4)
            except Exception as _e: log.debug(f"[IND] {name} fetch error: {_e}")
        if result:
            log.info(f"[IND] {_disp:12s} {' '.join(f'{k}={v}' for k,v in result.items())}")
        return result if result else None
    except Exception as e:
        log.warning(f"[IND] {pair['display']}: {e}")
        return None

def fetch_polygon(pair, tf, limit):
    """Download OHLCV candles from Polygon.io REST API. Best forex data quality."""
    try:
        key = os.environ.get("POLYGON_KEY", CONFIG.get("POLYGON_KEY", ""))
        if not key: log.warning("[PG] No POLYGON_KEY set"); return None
        # Derive Polygon ticker from display: "EUR/USD" → "C:EURUSD"
        ticker = "C:" + pair["display"].replace("/", "")
        mult, span = {"D1": (1, "day"), "H4": (4, "hour"), "H1": (1, "hour")}.get(tf, (1, "day"))
        # Date range — 2 years back
        from datetime import timedelta
        end = datetime.now(timezone.utc)
        start = end - timedelta(days=730)
        url = f"https://api.polygon.io/v2/aggs/ticker/{ticker}/range/{mult}/{span}/{start.strftime('%Y-%m-%d')}/{end.strftime('%Y-%m-%d')}"
        r = http_requests.get(url, params={"apiKey": key, "limit": 50000, "sort": "asc"}, timeout=20)
        if r.status_code == 403:
            log.warning(f"[PG] {ticker}: 403 Forbidden — check API key or plan"); return None
        if r.status_code != 200:
            log.warning(f"[PG] {ticker} HTTP {r.status_code}: {r.text[:120]}"); return None
        data = r.json()
        results = data.get("results", [])
        if not results: log.warning(f"[PG] {ticker}: no results"); return None
        candles = [{"time": datetime.fromtimestamp(bar["t"] / 1000, tz=timezone.utc).isoformat(),
                     "open": float(bar["o"]), "high": float(bar["h"]), "low": float(bar["l"]),
                     "close": float(bar["c"]), "vol": float(bar.get("v", 0))}
                    for bar in results]
        # Rate limit: Polygon free = 5 calls/min
        time.sleep(12)
        return candles[-limit:] if len(candles) > limit else candles
    except Exception as e: log.error(f"[PG] {pair['display']}: {e}"); return None

def fetch_candles(pair: dict, tf: str, limit: int) -> list | None:
    """Route candle fetch to correct source (binance, yfinance, or polygon) based on pair config."""
    if pair["source"]=="binance": return fetch_binance(pair["symbol"], TF_B[tf], limit)
    if pair["source"]=="eodhd": return fetch_eodhd(pair, tf, limit)
    if pair["source"]=="polygon": return fetch_polygon(pair, tf, limit)
    if pair["source"]=="yfinance": return fetch_yfinance(pair["symbol"], tf, limit)
    return None

# ── Indicator functions (extracted to indicators.py) ──
from indicators import (
    calc_ema, calc_sma, calc_rsi, calc_macd, calc_atr, calc_adx, calc_bb,
    calc_rsi_divergence, calc_weinstein_stage, calc_fib_proximity,
    calc_stochastic, calc_adx_momentum, calc_adx_percentile,
    calc_atr_percentile, calc_levels, calc_indicators, calc_fib,
)

# ── Scoring engine (extracted to scoring.py) ──
from scoring import (
    get_session, calc_confluence, detect_div,
    CORR_CLUSTERS, apply_correlation_cap,
)

_scan_in_progress = False
_kill_switch = False      # N4: Kill-switch — blocks new scans/analyses when True
_disabled_pairs: set = set()  # per-pair kill-switch — display names of pairs to exclude

def run_full_scan() -> dict:
    """Parallel scan of all ACTIVE_PAIRS. Returns signals, errors, skipped lists."""
    global _scan_in_progress
    if _kill_switch:
        return {"success":False,"error":"Kill-switch active — system paused","signals":[],"errors":[],"skipped":[],"btcBias":"neutral","totalPairs":0,"scannedAt":datetime.now(timezone.utc).isoformat()}
    if _scan_in_progress:
        return {"success":False,"error":"Scan already in progress","signals":[],"errors":[],"skipped":[],"btcBias":"neutral","totalPairs":0,"scannedAt":datetime.now(timezone.utc).isoformat()}
    _scan_in_progress = True
    results,errors,skipped=[],[],[]
    # N3: Live scan funnel counters
    scan_funnel = {"total":len(ACTIVE_PAIRS), "no_data":0, "low_score":0, "passed":0, "errors":0}
    btc_bias="neutral"
    # Phase 6: Exchange open/close detection — skip closed markets for stock/ETF pairs
    _closed_exchanges = set()
    try:
        _eodhd_key = os.environ.get("EODHD_KEY", "")
        if _eodhd_key:
            for exch_code in ["JSE", "US"]:
                try:
                    r = http_requests.get(f"https://eodhd.com/api/exchange-details/{exch_code}?api_token={_eodhd_key}&fmt=json", timeout=8)
                    if r.status_code == 200:
                        edata = r.json()
                        if not edata.get("isOpen", True):
                            _closed_exchanges.add(exch_code)
                            log.info(f"[EXCH] {exch_code}: CLOSED")
                        else:
                            log.info(f"[EXCH] {exch_code}: OPEN")
                except Exception as _e: log.debug(f"[EXCH] {exch_code} check error: {_e}")
    except Exception as e: log.warning(f"[EXCH] Exchange check failed: {e}")
    # Pre-fetch: news context + BTC bias + yield curve + div/split (serial, before parallel pair analysis)
    log.info("Fetching market context...")
    news_ctx = fetch_news_context()
    # Phase A: Yield curve context
    yield_ctx = None
    try:
        yield_ctx = fetch_yield_curve()
        if yield_ctx: news_ctx["yieldCurve"] = yield_ctx
    except Exception as e: log.warning(f"[YIELD] scan fetch err: {e}")
    # Phase B: Dividend/split context
    try:
        ds_ctx = fetch_div_split_context()
        if ds_ctx: news_ctx["divSplit"] = ds_ctx
    except Exception as e: log.warning(f"[DIVS] scan fetch err: {e}")
    try:
        btc=fetch_candles({"symbol":"BTCUSDT","source":"binance"},"D1",CONFIG["D1_CANDLES"])
        if btc and len(btc)>=200:
            s=calc_indicators(btc)["snap"]
            if s["ema21"] and s["ema50"] and s["ema200"]:
                if s["ema21"]>s["ema50"]>s["ema200"]: btc_bias="bullish"
                elif s["ema21"]<s["ema50"]<s["ema200"]: btc_bias="bearish"
        log.info(f"BTC bias: {btc_bias}")
    except Exception as e: log.error(f"BTC err: {e}")
    # B4: Parallel pair analysis — ThreadPoolExecutor(max_workers=4)
    from concurrent.futures import ThreadPoolExecutor, as_completed
    def _analyse(pair):
        # Phase 6: Skip pairs on closed exchanges
        if _closed_exchanges:
            sym = pair.get("symbol", "")
            if ".JO" in sym and "JSE" in _closed_exchanges:
                log.info(f"{pair['display']:12s} SKIP (JSE closed)")
                return pair, None, None
            if (".US" in sym or ".INDX" in sym) and pair["type"] in ("stock","index") and "US" in _closed_exchanges:
                log.info(f"{pair['display']:12s} SKIP (US closed)")
                return pair, None, None
        try:
            return pair, analyze_pair(pair, btc_bias), None
        except Exception as e:
            return pair, None, str(e)
    with ThreadPoolExecutor(max_workers=6) as pool:
        futures = {pool.submit(_analyse, pair): pair for pair in ACTIVE_PAIRS}
        for fut in as_completed(futures):
            pair, sig, err = fut.result()
            if err:
                errors.append({"pair":pair["display"],"error":err})
                scan_funnel["errors"] += 1
                log.error(f"{pair['display']:12s} ERR: {err}")
            elif sig and sig["confluenceScore"]>=CONFIG["MIN_CONFLUENCE_CLASS"].get(pair["type"], CONFIG["MIN_CONFLUENCE"]):
                # Phase 5: Attach server-side indicators (EODHD covers all asset classes incl. crypto via .CC)
                try: sig["serverIndicators"] = fetch_eodhd_indicators(pair)
                except Exception as _e: log.debug(f"[IND] {pair['display']} server indicators skipped: {_e}")
                sig["newsCtx"]=news_ctx; results.append(sig)
                scan_funnel["passed"] += 1
                log.info(f"{pair['display']:12s} {sig['direction']:5s} {sig['confluenceScore']}/13 [{sig.get('trendState','?')}]")
            elif sig:
                skipped.append({"pair":pair["display"],"reason":f"Low confluence ({sig['confluenceScore']}/13)"})
                scan_funnel["low_score"] += 1
                log.info(f"{pair['display']:12s} WEAK  {sig['confluenceScore']}/13")
            else:
                skipped.append({"pair":pair["display"],"reason":"No data"})
                scan_funnel["no_data"] += 1
                log.info(f"{pair['display']:12s} SKIP")
    log.info(f"Scan funnel: {scan_funnel}")
    results.sort(key=lambda x:x["confluenceScore"],reverse=True)
    # B5: Apply correlation cap warnings after sorting (highest score first = priority preserved)
    results = apply_correlation_cap(results)
    _scan_in_progress = False
    return {"success":True,"signals":results,"errors":errors,"skipped":skipped,"scanFunnel":scan_funnel,
            "btcBias":btc_bias,"totalPairs":len(ACTIVE_PAIRS),"scannedAt":datetime.now(timezone.utc).isoformat()}

EXPERT_PROMPT="""You are Marcus Reid — 18-year prop-desk veteran. Framework: Elder Triple Screen, Wilder rules, Weinstein stages, Murphy intermarket, Minervini templates, Van Tharp R-multiples, Douglas probability. Be direct, blunt, zero sugar-coating. Never guarantee outcomes.

STRICT RULES — BREAK THESE AND YOU FAIL:
- Output ONLY valid JSON. No markdown, no explanations, no ```json.
- Use probability language ONLY: "edge suggests", "probability favors", "setup indicates". NEVER use "will", "guaranteed", "definitely", "should hit".
- Every sentence must reference at least one exact input field (confluenceScore, TrendState, WeinsteinLabel, votes, warnings, etc.).
- Counter-trend = automatic grade drop of 1 full level + explicit warning.
- Score <6.0 or DEAD RANGING = grade F, no entry.

GRADING (max 13.0):
A+ (10-13): Elite — full size. A (8-9.9): Strong — normal size. B (6-7.9): Valid — half size. C (4-5.9): Watchlist only. F (0-3.9): Avoid.

REGIME (read TrendState first):
- TRENDING (ADX≥35): Full rules, pullbacks to EMA21/50 are entries, extend TP.
- DEVELOPING (25-34): Confirm with volume.
- RANGING (per asset class): Downgrade B→C, C→F. Only fade BB extremes + stoch reversal.
- DEAD RANGING: F-grade instantly. Do not trade.

ELDER TRIPLE SCREEN (mandatory):
D1 tide must lead. Any H4/H1 conflict = WAIT. Counter-trend = -1 grade.

WILDER + MURPHY + WEINSTEIN:
- RSI divergence = HIGH priority warning. RSI 40-80 bullish range (Cardwell). ADX<25 = no trend signals.
- Fib proximity vote active → name exact level in entryZone. Price AT fib + stoch + EMA = A-grade cluster.
- Weinstein Stage 1/3 = no new trend trades. Stage 2 = ideal LONG. Stage 4 = ideal SHORT.
- DXY rising = headwind for EUR,GBP,AUD,NZD,XAU,XAG LONGS. BTC bearish = alt LONG risk.

VAN THARP SIZING:
Express everything in R. SL >2% price = quarter size. Min 2R reward. SQN: <1.6=Poor, 2.5+=Good, 3+=Excellent, 5+=Superb.

NEWS & EVENTS:
High-impact (FOMC,NFP,CPI) within 24h = reduce 50% or WAIT. Conflicting news = downgrade 1 level.

STYLE RULES:
- SCALP: ADX>30, H1 exhaustion, 1.5-2R. Flag if D1 too strong.
- INTRADAY: H4+H1 aligned, same session, 2-3R. Session quality medium+ required.
- SWING: D1 EMA stack dominant, EMA200 slope, 4-6R. Warn if H1 extended.
If incompatible with requested style, say so and recommend correct style.

edgeProbability calculation:
Base = confluenceScore × 7.7. +15 if spread≥3 and TRENDING. -15 if counter-trend or DEAD RANGING. -10 if high-impact news. Cap at 95.

riskLevel calculation:
"Low" if edgeProbability≥70 and TRENDING and no counter-trend. "High" if edgeProbability<40 or DEAD RANGING or counter-trend. "Medium" otherwise.

OUTPUT — EXACT JSON ONLY:
{"grade":"A","verdict":"ONE sharp sentence","narrative":"2-3 sentences — reference specific votes, TrendState, WeinsteinLabel","entryZone":"exact price/fib","invalidation":"exact price","keyLevels":"S1/R1","positionSizing":"Full/Half/Quarter + R explanation","tradeStyle":"SWING|INTRADAY|SCALP","tradeStyleReason":"why","warnings":["specific risks"],"edgeProbability":68,"riskLevel":"Medium"}

Now analyse the following signal data and reply with JSON only:"""

def fetch_dxy_context():
    """Fetch DXY (US Dollar Index) 5-day trend for Murphy intermarket context."""
    try:
        d=fetch_yfinance("DX-Y.NYB","D1",10)
        if not d or len(d)<5: return None
        cl=[c["close"] for c in d]
        chg=round((cl[-1]-cl[-5])/cl[-5]*100,2)
        trend="rising" if chg>0.3 else "falling" if chg<-0.3 else "flat"
        return f"trend={trend} 5d_chg={chg}% price={round(cl[-1],2)}"
    except: return None

# Phase A: UST Yield Curve cache (1hr TTL — rates change slowly)
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
        if not _key: return None
        r = http_requests.get(f"https://eodhd.com/api/ust/yield-rates?api_token={_key}&fmt=json", timeout=10)
        if r.status_code != 200: return None
        data = r.json()
        # API returns {"meta":{...},"data":[{"date":"...","tenor":"2Y","rate":3.47},...]}
        rows = data.get("data", data) if isinstance(data, dict) else data
        if not rows or not isinstance(rows, list): return None
        # Build tenor map from latest date
        latest_date = max(_row["date"] for _row in rows if _row.get("date"))
        tenor_map = {_row["tenor"]: _row["rate"] for _row in rows if _row.get("date") == latest_date and _row.get("tenor") and _row.get("rate") is not None}
        y3m  = tenor_map.get("3M")  or tenor_map.get("1.5M")
        y2y  = tenor_map.get("2Y")
        y10y = tenor_map.get("10Y")
        y30y = tenor_map.get("30Y")
        if y2y is None or y10y is None: return None
        spread_2_10 = round(float(y10y) - float(y2y), 3)
        if spread_2_10 < -0.1:   shape = "inverted"
        elif spread_2_10 < 0.1:  shape = "flat"
        else:                     shape = "normal"
        result = {
            "shape": shape,
            "spread_2_10": spread_2_10,
            "y3m":  round(float(y3m), 3)  if y3m  else None,
            "y2y":  round(float(y2y), 3),
            "y10y": round(float(y10y), 3),
            "y30y": round(float(y30y), 3) if y30y else None,
            "riskContext": "risk-off (recession warning)" if shape=="inverted" else "neutral" if shape=="flat" else "risk-on",
            "date": latest_date
        }
        _yield_cache = {"data": result, "ts": now}
        log.info(f"[YIELD] Curve: {shape} | 2Y:{y2y}% 10Y:{y10y}% spread:{spread_2_10}%")
        return result
    except Exception as e:
        log.warning(f"[YIELD] fetch failed: {e}")
        return None

# Phase B: Dividend/Split awareness cache (24hr TTL)
_divsplit_cache = {"data": {}, "ts": 0}
_DIVSPLIT_TTL = 86400  # 24 hours

# Stock pairs that can have dividends/splits
_DIV_SPLIT_PAIRS = ["GOOG.US", "GLD.US", "NPN.JO", "AGL.JO", "PRX.JO", "SPY.US", "QQQ.US", "TLT.US"]

def fetch_div_split_context():
    """Fetch upcoming dividends and splits for stock pairs. Warns AI if ex-div within 7 days."""
    global _divsplit_cache
    now = time.time()
    if _divsplit_cache["data"] and (now - _divsplit_cache["ts"]) < _DIVSPLIT_TTL:
        return _divsplit_cache["data"]
    _key = os.environ.get("EODHD_KEY", "")
    if not _key: return {}
    today = datetime.now(timezone.utc).date()
    result = {}
    for sym in _DIV_SPLIT_PAIRS:
        entry = {}
        # Dividends
        try:
            r = http_requests.get(f"https://eodhd.com/api/div/{sym}?api_token={_key}&fmt=json", timeout=8)
            if r.status_code == 200:
                divs = r.json()
                if divs and isinstance(divs, list):
                    upcoming = []
                    for d in divs:
                        ex = d.get("date") or d.get("exDividendDate")
                        if not ex: continue
                        try:
                            ex_date = datetime.strptime(str(ex)[:10], "%Y-%m-%d").date()
                            days_to = (ex_date - today).days
                            if 0 <= days_to <= 14:
                                upcoming.append({"exDate": str(ex_date), "daysTo": days_to, "amount": d.get("value", d.get("dividend"))})
                        except Exception as _e: log.debug(f"[DIVS] date parse error: {_e}")
                    if upcoming:
                        entry["upcomingDiv"] = upcoming
                        log.info(f"[DIVS] {sym}: ex-div in {upcoming[0]['daysTo']} days")
        except Exception as e: log.warning(f"[DIVS] {sym}: {e}")
        # Splits
        try:
            r = http_requests.get(f"https://eodhd.com/api/splits/{sym}?api_token={_key}&fmt=json", timeout=8)
            if r.status_code == 200:
                splits = r.json()
                if splits and isinstance(splits, list):
                    upcoming = []
                    for s in splits:
                        sd = s.get("date")
                        if not sd: continue
                        try:
                            s_date = datetime.strptime(str(sd)[:10], "%Y-%m-%d").date()
                            days_to = (s_date - today).days
                            if 0 <= days_to <= 30:
                                upcoming.append({"splitDate": str(s_date), "daysTo": days_to, "ratio": s.get("split")})
                        except Exception as _e: log.debug(f"[SPLITS] date parse error: {_e}")
                    if upcoming:
                        entry["upcomingSplit"] = upcoming
                        log.warning(f"[SPLITS] {sym}: split in {upcoming[0]['daysTo']} days")
        except Exception as e: log.warning(f"[SPLITS] {sym}: {e}")
        if entry:
            result[sym] = entry
    _divsplit_cache = {"data": result, "ts": now}
    log.info(f"[DIVS] Checked {len(_DIV_SPLIT_PAIRS)} pairs — {len(result)} with upcoming events")
    return result

# P2: 5-minute TTL cache for news context — avoid redundant API calls during rapid scans
_news_cache = {"data": None, "ts": 0}
_NEWS_TTL = 300  # 5 minutes

def fetch_news_context():
    now = time.time()
    if _news_cache["data"] and (now - _news_cache["ts"]) < _NEWS_TTL:
        log.info("[NEWS] Using cached context")
        return _news_cache["data"]
    ctx = {"forexEvents":[], "cryptoNews":[], "marketNews":[]}
    try:
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        r = http_requests.get(f"https://finnhub.io/api/v1/calendar/economic?from={today}&to={today}&token={CONFIG.get('FINNHUB_KEY','')}", timeout=8)
        if r.status_code == 200:
            events = [e for e in r.json().get("economicCalendar",[]) if e.get("impact","").lower() in ["high","3"]]
            ctx["forexEvents"] = [{"time":e.get("time",""),"currency":e.get("country",""),"event":e.get("event","")} for e in events[:5]]
            log.info(f"[NEWS] Economic calendar: {len(ctx['forexEvents'])} high-impact events today")
    except Exception as e: log.warning(f"[NEWS] Economic calendar failed: {e}")
    if CONFIG.get("CRYPTOPANIC_KEY"):
        try:
            r = http_requests.get(f"https://cryptopanic.com/api/v1/posts/?auth_token={CONFIG['CRYPTOPANIC_KEY']}&public=true&filter=hot", timeout=8)
            if r.status_code == 200:
                posts = r.json().get("results", [])
                ctx["cryptoNews"] = [{"title":p.get("title",""),"sentiment":"bullish" if p.get("votes",{}).get("positive",0)>p.get("votes",{}).get("negative",0) else "bearish" if p.get("votes",{}).get("negative",0)>0 else "neutral","currencies":[c["code"] for c in p.get("currencies",[])]} for p in posts[:8]]
                log.info(f"[NEWS] CryptoPanic: {len(ctx['cryptoNews'])} headlines")
        except Exception as e: log.warning(f"[NEWS] CryptoPanic failed: {e}")
    if CONFIG.get("FINNHUB_KEY"):
        try:
            r = http_requests.get(f"https://finnhub.io/api/v1/news?category=general&token={CONFIG['FINNHUB_KEY']}", timeout=8)
            if r.status_code == 200:
                ctx["marketNews"] = [{"headline":a.get("headline",""),"summary":a.get("summary","")[:100]} for a in r.json()[:5]]
                log.info(f"[NEWS] Finnhub: {len(ctx['marketNews'])} market headlines")
        except Exception as e: log.warning(f"[NEWS] Finnhub failed: {e}")
    # EODHD per-pair sentiment — batch call: normalized 0.0–1.0 (>0.6 bullish, <0.4 bearish)
    try:
        _eodhd_key = os.environ.get("EODHD_KEY", "")
        if _eodhd_key:
            def _eodhd_ticker(p):
                _d, _t = p["display"], p.get("type","")
                if _t == "crypto":
                    # BTC/USDT → BTC-USD.CC
                    base = _d.split("/")[0] if "/" in _d else _d.replace("USDT","")
                    return f"{base}-USD.CC"
                if _t in ("forex","commodity") or ("/" in _d and _t != "crypto"): return _d.replace("/","") + ".FOREX"
                elif _t == "stock" and ".JO" in p["symbol"]: return p["symbol"].replace(".JO",".JSE")
                elif _t == "index": return p["symbol"] if ".INDX" in p["symbol"] else p["symbol"].lstrip("^") + ".INDX"
                else: return p["symbol"]
            ticker_map = {_eodhd_ticker(p): p["display"] for p in ACTIVE_PAIRS}
            # Phase 2: Single batch sentiment call instead of N sequential calls
            sentiments = {}
            if ticker_map:
                tickers_csv = ",".join(ticker_map.keys())
                sdata = http_requests.get(f"https://eodhd.com/api/sentiments?s={tickers_csv}&api_token={_eodhd_key}&fmt=json", timeout=12).json()
                for sticker, display in ticker_map.items():
                    scores = sdata.get(sticker, [])
                    if scores and scores[0].get("normalized") is not None:
                        sc = scores[0]["normalized"]
                        label = "bullish" if sc > 0.6 else "bearish" if sc < 0.4 else "neutral"
                        sentiments[display] = round(sc, 3)
                        log.info(f"[SENT] {display:12s} {sc:.2f} {label}")
                if sentiments: ctx["pairSentiment"] = sentiments
            # Phase 3: EODHD per-pair financial news (replaces Finnhub general news)
            pair_news = {}
            for sticker, display in list(ticker_map.items())[:10]:
                try:
                    ndata = http_requests.get(f"https://eodhd.com/api/news?s={sticker}&limit=3&api_token={_eodhd_key}&fmt=json", timeout=8).json()
                    if ndata and isinstance(ndata, list):
                        pair_news[display] = [{"t": a.get("title","")[:80], "s": round(a.get("sentiment",{}).get("polarity",0.5),2)} for a in ndata[:3]]
                except Exception as _e: log.debug(f"[NEWS] {display} news fetch error: {_e}")
            if pair_news:
                ctx["pairNews"] = pair_news
                log.info(f"[NEWS] EODHD per-pair news: {len(pair_news)} pairs, {sum(len(v) for v in pair_news.values())} articles")
            # Phase 4: News word weights — top keywords driving each pair's news
            word_weights = {}
            for sticker, display in list(ticker_map.items())[:10]:
                try:
                    wdata = http_requests.get(f"https://eodhd.com/api/news-word-weights?s={sticker}&page[limit]=5&api_token={_eodhd_key}&fmt=json", timeout=8).json()
                    if wdata and isinstance(wdata, dict) and wdata.get("data"):
                        word_weights[display] = list(wdata["data"].keys())[:5]
                except Exception as _e: log.debug(f"[NEWS] {display} word weights error: {_e}")
            if word_weights:
                ctx["wordWeights"] = word_weights
                log.info(f"[NEWS] Word weights: {len(word_weights)} pairs")
    except Exception as e: log.warning(f"[NEWS] EODHD sentiment/news failed: {e}")
    _news_cache["data"] = ctx
    _news_cache["ts"] = time.time()
    return ctx

def _build_signal_message(signal: dict, news_ctx: dict | None,
                          style_pref: str, style_labels: dict) -> str:
    """Build the structured signal string sent to Marcus Reid (Claude) for analysis."""
    max_score = signal.get("maxScore", 13.0)
    spread = signal.get("spread", 0)
    conviction = "HIGH" if spread >= 3 else "MEDIUM" if spread >= 1.5 else "LOW"
    pair_sqn = signal.get("pairSQN")

    parts = [
        f"Pair:{signal['pair']} Dir:{signal['direction']} Score:{signal['confluenceScore']}/{max_score}",
        f"Spread:{spread}({conviction} conviction)",
        f"PairSQN:{pair_sqn}" if pair_sqn else "",
        f"TrendState:{signal.get('trendState', '?')}",
        f"ADXPct:{signal.get('h4', {}).get('snap', {}).get('adxPct', '?')}th-pct"
        f"({signal.get('h4', {}).get('snap', {}).get('adxLabel', '?')})",
        f"Weinstein:{signal.get('weinsteinLabel', 'n/a')}",
        f"Price:{signal['price']} SL:{signal['sl']}(SL%:{signal.get('slPct', '?')}%)",
        f"TP1:{signal['tp1']}(R:{signal['rr1']}) TP2:{signal['tp2']}(R:{signal['rr2']})",
        f"ATR:{signal['atr']}",
        f"Votes:{json.dumps(signal['votes'])}",
        f"Vol:{signal['volRatio']}x Stoch:{signal.get('stochK')}/{signal.get('stochD')}",
        f"EMA200slope:{signal['ema200Slope']}%",
        f"BTC:{signal.get('btcBias', 'n/a')}",
        f"Session:{signal['session']['name']}({signal['session']['quality']})",
        f"Warnings:{json.dumps(signal['warnings'])}",
        f"Fib:{json.dumps(signal['fib'])}",
        f"ATRPct:{signal.get('h4', {}).get('snap', {}).get('atrPct', '?')}"
        f"({signal.get('h4', {}).get('snap', {}).get('atrLabel', '?')})",
        f"EntryMode:{signal.get('entryMode', 'trend')}",
        f"StylePref:{style_pref.upper()}",
    ]
    msg = " ".join(p for p in parts if p)

    dxy_ctx = fetch_dxy_context()
    if dxy_ctx:
        msg += f" DXY:{dxy_ctx}"

    _yc = fetch_yield_curve()
    if _yc:
        msg += (f" YieldCurve:{{shape:{_yc['shape']},2y10y_spread:{_yc['spread_2_10']}%,"
                f"3m:{_yc.get('y3m')}%,10y:{_yc['y10y']}%,context:{_yc['riskContext']}}}")

    _ds = fetch_div_split_context()
    _pair_sym = signal.get("symbol", "")
    if _ds and _pair_sym in _ds:
        _ev = _ds[_pair_sym]
        if _ev.get("upcomingDiv"):
            _d = _ev["upcomingDiv"][0]
            msg += (f" ExDivWarning:ex-div in {_d['daysTo']} days"
                    f" ({_d['exDate']}, amount:{_d.get('amount', '?')}) — gap-down risk, reduce size")
        if _ev.get("upcomingSplit"):
            _s = _ev["upcomingSplit"][0]
            msg += (f" SplitWarning:split in {_s['daysTo']} days"
                    f" ({_s['splitDate']}, ratio:{_s.get('ratio', '?')}) — price distortion risk")

    _bt = signal.get("backtestStats")
    if _bt:
        msg += (f" BT_SQN:{_bt.get('sqn', '?')} BT_WR:{_bt.get('winRate', '?')}%"
                f" BT_Expect:{_bt.get('expectancy', '?')}R BT_MaxDD:{_bt.get('maxDrawdownPct', '?')}%")
        _rs = _bt.get("regimeStats", {})
        if _rs:
            msg += f" BT_RegimeWR:{json.dumps({k: v.get('wr') for k, v in _rs.items()})}"

    msg += f" StyleDetail:{style_labels.get(style_pref.lower(), style_pref.upper())}"

    if news_ctx:
        if news_ctx.get("forexEvents"):
            msg += f" HighImpactEvents:{json.dumps(news_ctx['forexEvents'])}"
        if news_ctx.get("marketNews"):
            msg += f" MarketNews:{json.dumps(news_ctx['marketNews'])}"
        if news_ctx.get("cryptoNews") and signal.get("type") == "crypto":
            pair_coins = [signal["symbol"].replace("USDT", "").replace("USDC", "")]
            relevant = [n for n in news_ctx["cryptoNews"]
                        if not n["currencies"] or any(c in pair_coins for c in n["currencies"])]
            if relevant:
                msg += f" CryptoNews:{json.dumps(relevant[:3])}"
        _sent = news_ctx.get("pairSentiment", {})
        if _sent.get(signal.get("pair", "")):
            _sc = _sent[signal["pair"]]
            _sl = "bullish" if _sc > 0.6 else "bearish" if _sc < 0.4 else "neutral"
            msg += f" NewsSentiment:{_sc}({_sl})"
        _pnews = news_ctx.get("pairNews", {}).get(signal.get("pair", ""), [])
        if _pnews:
            msg += f" PairNews:{json.dumps(_pnews)}"
        _ww = news_ctx.get("wordWeights", {}).get(signal.get("pair", ""), [])
        if _ww:
            msg += f" NewsDrivers:{json.dumps(_ww)}"

    _server_ind = signal.get("serverIndicators")
    if _server_ind:
        msg += f" ServerIndicators:{json.dumps(_server_ind)}"

    return msg


def run_ai(signal: dict, news_ctx: dict | None = None, style_pref: str = "auto") -> dict:
    """Send signal data to Anthropic Claude for Marcus Reid AI analysis. Returns parsed JSON dict."""
    if not CONFIG.get("ANTHROPIC_KEY") or CONFIG["ANTHROPIC_KEY"] == "YOUR_ANTHROPIC_API_KEY":
        log.error("[AI] Anthropic API key is None or not configured!")
        return {"error": "Anthropic API key not configured"}
    try:
        log.info(f"[AI] Analyzing {signal['pair']}...")
        import anthropic
        c = anthropic.Anthropic(api_key=CONFIG["ANTHROPIC_KEY"])
        style_labels = {
            "scalp":    "SCALP — focus on H1 exhaustion, tight 1.5R, quick execution",
            "intraday": "INTRADAY — H4+H1 alignment, same-session execution, 2-3R",
            "swing":    "SWING — D1 trend dominance, EMA200 slope, 4-6R multi-day hold",
        }
        if style_pref == "auto":
            _sc = signal.get("confluenceScore", 0)
            style_pref = "swing" if _sc >= 9 else "intraday" if _sc >= 7 else "scalp"
        msg = _build_signal_message(signal, news_ctx, style_pref, style_labels)
        r = c.messages.create(
            model=CONFIG["ANTHROPIC_MODEL"], max_tokens=1500,
            system=EXPERT_PROMPT, messages=[{"role": "user", "content": msg}]
        )
        t=r.content[0].text.strip()
        if "```" in t:
            parts = t.split("```")
            for p in parts:
                p = p.strip()
                if p.startswith("json"): p = p[4:].strip()
                if p.startswith("{"): t = p; break
        start = t.find("{"); end = t.rfind("}") + 1
        if start >= 0 and end > start: t = t[start:end]
        result = json.loads(t)
        log.info(f"[AI] {signal['pair']} => Grade:{result.get('grade','?')} Prob:{result.get('edgeProbability','?')}% Risk:{result.get('riskLevel','?')} | {str(result.get('verdict',''))[:60]}")
        return result
    except Exception as e:
        log.error(f"[AI] ERROR for {signal.get('pair','?')}: {e}")
        return {"error":str(e)}

def backtest_pair(pair):
    """Walk-forward backtest on D1 bars with slippage, regime tagging, and Monte Carlo DD simulation."""
    log.info(f"[BT] {pair['display']} fetching data...")
    try:
        import yfinance as yf, pandas as pd
        sym = pair["symbol"]
        def df_to_candles(df):
            return [{"time":str(idx)[:10],"open":float(r["Open"]),"high":float(r["High"]),"low":float(r["Low"]),"close":float(r["Close"]),"vol":float(r.get("Volume",0))} for idx,r in df.iterrows()]
        if pair["source"] == "binance":
            d1_raw = fetch_binance(sym, "1d", 1000)
            h4_raw = fetch_binance(sym, "4h", 1000)
            h1_raw = fetch_binance(sym, "1h", 1000)
        elif pair["source"] == "polygon":
            d1_raw = fetch_polygon(pair, "D1", 600)
            h4_raw = fetch_polygon(pair, "H4", 1000)
            h1_raw = fetch_polygon(pair, "H1", 1000)
        elif pair["source"] == "eodhd":
            d1_raw = fetch_eodhd(pair, "D1", 600)
            h4_raw = fetch_eodhd(pair, "H4", 1000)
            h1_raw = fetch_eodhd(pair, "H1", 1000)
        else:
            d1_df = yf.download(sym, period="2y", interval="1d", progress=False, auto_adjust=True)
            if d1_df is None or d1_df.empty: return {"error": f"No D1 data for {pair['display']}"}
            if isinstance(d1_df.columns, pd.MultiIndex): d1_df.columns = [col[0] for col in d1_df.columns]
            h1_df = yf.download(sym, period="730d", interval="1h", progress=False, auto_adjust=True)
            if h1_df is None or h1_df.empty: h1_df = None
            elif isinstance(h1_df.columns, pd.MultiIndex): h1_df.columns = [col[0] for col in h1_df.columns]
            d1_raw = df_to_candles(d1_df)
            h4_raw = df_to_candles(h1_df.resample("4h").agg({"Open":"first","High":"max","Low":"min","Close":"last","Volume":"sum"}).dropna()) if h1_df is not None else None
            h1_raw = df_to_candles(h1_df) if h1_df is not None else None
        if not d1_raw: return {"error": f"No D1 data for {pair['display']}"}
        if len(d1_raw) < 230: return {"error": f"Insufficient D1 history for {pair['display']} ({len(d1_raw)} bars)"}
    except Exception as e:
        return {"error": f"Data fetch failed: {e}"}

    # N8: Session-variable slippage — forex widens during Asian/off-hours
    _BASE_SLIP = {"forex":0.0001,"crypto":0.002,"commodity":0.001,"stock":0.001,"index":0.001}
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

    # Walk forward on D1 bars — reliable 2yr history for all pair types
    trades = []; equity = 1.0; equity_curve = [1.0]
    MIN_BARS = 220; COOLDOWN = 5; MAX_OPEN = 3  # R5: max concurrent positions
    total_bars = len(d1_raw)
    _ptype = pair["type"]
    i = MIN_BARS; last_exit_bar = 0; open_positions = 0
    # F8: Trade funnel diagnostic counters
    funnel = {"total_setups":0, "fail_score":0, "fail_macro":0, "fail_regime":0, "taken":0}
    _recent_scores = []  # CR3: rolling score history for adaptive percentile threshold
    # R4: Walk-forward split — 70% in-sample, 30% out-of-sample
    _oos_start = MIN_BARS + int((total_bars - MIN_BARS) * 0.7)
    while i < total_bars - 1:
        if i - last_exit_bar < COOLDOWN:
            i += 1; continue
        # R5: Max concurrent positions cap
        if open_positions >= MAX_OPEN:
            i += 1; continue
        d1_window = d1_raw[i - MIN_BARS : i]
        h4_window = d1_window[-80:]
        h1_window = d1_window[-60:]
        if len(h4_window) < 50 or len(h1_window) < 50:
            i += 1; continue
        try:
            d1i = calc_indicators(d1_window)
            h4i = calc_indicators(h4_window)
            h1i = calc_indicators(h1_window)
            vols = [c["vol"] for c in h1_window]; vsma = calc_sma(vols, 20)
            vr = vols[-1] / vsma[-1] if vsma[-1] and vsma[-1] > 0 else 1.0
            stoch = calc_stochastic(h4_window, 14, 3, 3)
            e200 = calc_ema([c["close"] for c in d1_window], 200)
            e200s = (e200[-1] - e200[-21]) / e200[-21] if e200[-1] and len(e200) >= 21 and e200[-21] else 0
            res = calc_confluence(d1i, h4i, h1i, vr, stoch, e200s, pair, "neutral",
                                   d1_candles=d1_window, h4_candles=h4_window, h1_candles=h1_window)
        except Exception:
            i += 1; continue
        funnel["total_setups"] += 1
        # V2/R3: Per-class bt_min from CONFIG + dynamic threshold by regime
        bt_min = CONFIG["BT_MIN"].get(_ptype, 6.0)
        _ts = res.get("trendState", "UNKNOWN")
        if _ts == "TRENDING": bt_min = max(bt_min - 1.0, 5.0)  # R3: relax in strong trend
        elif _ts == "DEAD RANGING": bt_min = bt_min + 2.0       # R3: demand more in chop
        elif _ts == "RANGING": bt_min = bt_min + 1.0
        # CR2: Extra penalty for regime transitions (ADX collapsing/exhausting)
        _h4_adx_mom = h4i["snap"].get("adxMomentum", "stable")
        if _ptype == "crypto" and _h4_adx_mom == "collapsing":
            bt_min += 1.5  # demand much higher score during regime collapse
        elif _ptype == "crypto" and _h4_adx_mom == "exhausting":
            bt_min += 0.8  # moderate increase during trend exhaustion
        # CR3: Rolling score percentile — use 60th pct of recent scores as adaptive floor
        if _ptype == "crypto" and len(_recent_scores) >= 10:
            _sorted = sorted(_recent_scores[-30:])
            _p60 = _sorted[int(len(_sorted) * 0.6)]
            bt_min = max(bt_min, _p60)  # never go below percentile floor
        _recent_scores.append(res["score"])
        if res["score"] < bt_min:
            funnel["fail_score"] += 1; i += 1; continue
        # F6: Per-class macro lookback — crypto uses 15 bars, forex 30, others 50
        _macro_lb = CONFIG["MACRO_LOOKBACK"].get(_ptype, 50)
        d1_closes = [c["close"] for c in d1_window[-_macro_lb:]]
        d1_macro_mean = sum(d1_closes) / len(d1_closes)
        d1_current = d1_closes[-1]
        macro_long  = d1_current >= d1_macro_mean
        macro_short = d1_current <= d1_macro_mean
        direction = res["direction"]
        if direction == "LONG"  and not macro_long:  funnel["fail_macro"] += 1; i += 1; continue
        if direction == "SHORT" and not macro_short: funnel["fail_macro"] += 1; i += 1; continue
        entry_bar = d1_raw[i]
        raw_entry = entry_bar["close"]
        slip = raw_entry * _get_slippage(entry_bar, _ptype)
        entry = raw_entry + slip if direction == "LONG" else raw_entry - slip
        atr = d1i["snap"]["atr"]
        if not atr or atr == 0: i += 1; continue
        # C4: Use shared calc_levels (deduplicates with analyze_pair)
        lvl = calc_levels(entry, atr, direction, _ptype)
        sl = lvl["sl"]; tp1 = lvl["tp1"]; tp2 = lvl["tp2"]
        sl_mult = lvl["mults"]["sl"]; tp1_mult = lvl["mults"]["tp1"]; tp2_mult = lvl["mults"]["tp2"]
        rr1 = lvl["rr1"]
        # V3: Structure-based stops for crypto — use wider of ATR-stop vs swing-stop
        if _ptype == "crypto":
            _recent = d1_window[-10:]
            if direction == "LONG":
                swing_sl = min(c["low"] for c in _recent)
                if swing_sl < sl: sl = swing_sl  # wider stop lets noise breathe
            else:
                swing_sl = max(c["high"] for c in _recent)
                if swing_sl > sl: sl = swing_sl
            rr1 = abs(tp1 - entry) / abs(sl - entry) if abs(sl - entry) > 0 else 0
        # T1: Volatility-adjusted sizing — if ATR > 1.5x its 20-bar SMA, reduce size 30%
        _atr_series = calc_atr([c["high"] for c in d1_window], [c["low"] for c in d1_window], [c["close"] for c in d1_window], 14)
        _atr_sma = calc_sma([v for v in _atr_series if v is not None], 20)
        _vol_adj = 1.0
        _valid_atr_sma = [v for v in _atr_sma if v is not None]
        if _valid_atr_sma and _valid_atr_sma[-1] and _valid_atr_sma[-1] > 0:
            if atr > _valid_atr_sma[-1] * 1.5: _vol_adj = 0.7
        outcome = "OPEN"; result_r = 0.0; exit_bar = i
        for j in range(i + 1, min(i + 21, total_bars)):
            bar = d1_raw[j]
            if direction == "LONG":
                # A2: worst-case intrabar — SL checked before TP (conservative)
                if bar["low"] <= sl:   outcome = "SL";  result_r = -1.0; exit_bar = j; break
                if bar["high"] >= tp2: outcome = "TP2"; result_r = (tp2_mult / sl_mult) - (slip / (atr * sl_mult)); exit_bar = j; break
                if bar["high"] >= tp1: outcome = "TP1"; result_r = rr1 - (slip / (atr * sl_mult)); exit_bar = j; break
            else:
                # A2: worst-case intrabar — SL checked before TP (conservative)
                if bar["high"] >= sl:  outcome = "SL";  result_r = -1.0; exit_bar = j; break
                if bar["low"] <= tp2:  outcome = "TP2"; result_r = (tp2_mult / sl_mult) - (slip / (atr * sl_mult)); exit_bar = j; break
                if bar["low"] <= tp1:  outcome = "TP1"; result_r = rr1 - (slip / (atr * sl_mult)); exit_bar = j; break
        if outcome == "OPEN": result_r = 0.0
        risk_mult = CONFIG["RISK_MULT"].get(_ptype, 1.0)
        # T1: Apply volatility adjustment to position size
        equity_change = result_r * CONFIG["RISK_PCT"] * risk_mult * _vol_adj
        equity = round(equity * (1 + equity_change), 6)
        equity_curve.append(round(equity, 4))
        # R2: Tag trade with regime for segmentation
        _regime = _ts if _ts in ("TRENDING","DEVELOPING","RANGING","DEAD RANGING") else "UNKNOWN"
        funnel["taken"] += 1
        trades.append({
            "date": entry_bar["time"][:10],
            "pair": pair["display"], "direction": direction,
            "score": res["score"], "entry": round(entry, 6),
            "sl": round(sl, 6), "tp1": round(tp1, 6), "tp2": round(tp2, 6),
            "outcome": outcome, "resultR": round(result_r, 2),
            "regime": _regime, "oos": i >= _oos_start, "volAdj": _vol_adj
        })
        if outcome != "OPEN": last_exit_bar = exit_bar
        i = exit_bar + 1 if outcome != "OPEN" else i + 1

    if not trades: return {"error": f"No signals generated for {pair['display']} (threshold too high or insufficient data)"}
    wins = [t for t in trades if t["outcome"] in ("TP1","TP2")]
    losses = [t for t in trades if t["outcome"] == "SL"]
    gross_profit = sum(t["resultR"] for t in wins)
    gross_loss   = abs(sum(t["resultR"] for t in losses))
    win_rate     = round(len(wins) / len(trades) * 100, 1) if trades else 0
    profit_factor = round(gross_profit / gross_loss, 2) if gross_loss > 0 else None
    total_r      = round(sum(t["resultR"] for t in trades), 2)
    r_values     = [t["resultR"] for t in trades]
    avg_r        = round(total_r / len(trades), 3) if trades else 0
    _var = sum((r - avg_r)**2 for r in r_values) / len(r_values) if r_values else 0
    sqn  = round((avg_r / _var**0.5) * (len(trades)**0.5), 2) if len(trades) > 1 and avg_r != 0 and _var > 0 else 0
    peak = 1.0; max_dd = 0.0
    for e in equity_curve:
        if e > peak: peak = e
        dd = (peak - e) / peak
        if dd > max_dd: max_dd = dd
    max_dd_pct = round(max_dd * 100, 1)
    # A4: Expectancy decomposition
    avg_win  = round(sum(t["resultR"] for t in wins) / len(wins), 3) if wins else 0
    avg_loss = round(sum(t["resultR"] for t in losses) / len(losses), 3) if losses else 0
    r_skew   = round(avg_win / abs(avg_loss), 2) if avg_loss != 0 else None

    # B2: Monte Carlo drawdown simulation — 500 random shuffles of trade sequence
    # Transforms single-path DD into distribution: P5=best case, P95=worst case
    import random as _rnd
    _risk_mult = CONFIG["RISK_MULT"].get(pair["type"], 1.0)
    _mc_dds = []
    for _ in range(500):
        _shuffled = r_values[:]; _rnd.shuffle(_shuffled)
        _eq = 1.0; _pk = 1.0; _mdd = 0.0
        for _r in _shuffled:
            _eq *= (1 + _r * CONFIG["RISK_PCT"] * _risk_mult)
            if _eq > _pk: _pk = _eq
            _d = (_pk - _eq) / _pk
            if _d > _mdd: _mdd = _d
        _mc_dds.append(_mdd)
    _mc_dds.sort(); _nc = len(_mc_dds)
    mc_dd = {"p5":round(_mc_dds[int(_nc*0.05)]*100,1),"p50":round(_mc_dds[int(_nc*0.50)]*100,1),"p95":round(_mc_dds[int(_nc*0.95)]*100,1)}

    # B3: Score band win rate tracking — which confluence scores actually deliver edge?
    score_bands = {}
    for band_label, lo_b, hi_b in [("6-7",6,7),("7-8",7,8),("8-9",8,9),("9+",9,99)]:
        band_trades = [t for t in trades if lo_b <= t["score"] < hi_b]
        if band_trades:
            bw = sum(1 for t in band_trades if t["outcome"] in ("TP1","TP2"))
            score_bands[band_label] = {"trades": len(band_trades), "wr": round(bw/len(band_trades)*100,1)}

    # R2: Regime segmentation stats — track performance by market regime
    regime_stats = {}
    for regime in ["TRENDING","DEVELOPING","RANGING","DEAD RANGING"]:
        rt = [t for t in trades if t.get("regime") == regime]
        if rt:
            rw = sum(1 for t in rt if t["outcome"] in ("TP1","TP2"))
            regime_stats[regime] = {"trades":len(rt), "wr":round(rw/len(rt)*100,1),
                "expectancy":round(sum(t["resultR"] for t in rt)/len(rt),3)}

    # R4: Walk-forward split — in-sample vs out-of-sample SQN comparison
    is_trades = [t for t in trades if not t.get("oos", False)]
    oos_trades = [t for t in trades if t.get("oos", False)]
    def _calc_sqn(tlist):
        if len(tlist) < 2: return 0
        rv = [t["resultR"] for t in tlist]
        _a = sum(rv)/len(rv)
        _v = sum((r-_a)**2 for r in rv)/len(rv)
        return round((_a / _v**0.5) * (len(rv)**0.5), 2) if _a != 0 and _v > 0 else 0
    is_sqn = _calc_sqn(is_trades)
    oos_sqn = _calc_sqn(oos_trades)
    wf_split = {"is_trades":len(is_trades), "oos_trades":len(oos_trades),
                "is_sqn":is_sqn, "oos_sqn":oos_sqn,
                "overfit_flag": oos_sqn < is_sqn * 0.5 if is_sqn > 0 and len(oos_trades) >= 3 else False}

    log.info(f"[BT] {pair['display']} done: {len(trades)} trades, WR {win_rate}%, PF {profit_factor}, Expect {avg_r}R, SQN {sqn}, IS:{is_sqn}/OOS:{oos_sqn}, MC-P95 DD {mc_dd['p95']}%")
    return {
        "pair": pair["display"], "symbol": pair["symbol"], "type": pair["type"],
        "totalTrades": len(trades), "wins": len(wins), "losses": len(losses),
        "winRate": win_rate, "profitFactor": profit_factor,
        "totalR": total_r, "expectancy": avg_r, "sqn": sqn,
        "avgWin": avg_win, "avgLoss": avg_loss, "rSkew": r_skew,
        "maxDrawdownPct": max_dd_pct, "mcDD": mc_dd, "scoreBands": score_bands,
        "regimeStats": regime_stats, "wfSplit": wf_split, "funnel": funnel,
        "equityCurve": equity_curve, "trades": trades[-50:]
    }

def run_full_backtest():
    """Run backtest_pair on ALL_PAIRS and return sorted leaderboard by SQN."""
    results = []; errors = []
    for pair in ALL_PAIRS:
        r = backtest_pair(pair)
        if "error" in r: errors.append({"pair": pair["display"], "error": r["error"]})
        else: results.append(r)
    results.sort(key=lambda x: x["sqn"] if x["sqn"] is not None else -999, reverse=True)
    return {"success": True, "results": results, "errors": errors, "totalPairs": len(ALL_PAIRS)}

def _init_audit_db(db_path: str) -> None:
    """Create audit table if it doesn't exist."""
    con = sqlite3.connect(db_path)
    con.execute("""
        CREATE TABLE IF NOT EXISTS audit_log (
            id        INTEGER PRIMARY KEY AUTOINCREMENT,
            ts        TEXT NOT NULL,
            pair      TEXT,
            score     REAL,
            direction TEXT,
            trend     TEXT,
            grade     TEXT,
            edge_prob REAL,
            risk      TEXT,
            style     TEXT
        )
    """)
    con.commit()
    con.close()

_AUDIT_DB = os.path.join(os.path.dirname(os.path.abspath(__file__)), "audit.db")
_init_audit_db(_AUDIT_DB)

app=Flask(__name__,static_folder="static")

@app.route("/")
def index(): return send_from_directory("static","index.html")

@app.route("/api/scan",methods=["POST"])
def api_scan(): return jsonify(run_full_scan())

@app.route("/api/analyze",methods=["POST"])
def api_analyze():
    # S1: Validate Flask JSON payload
    d=request.json
    if not d or not isinstance(d, dict) or "signal" not in d:
        return jsonify({"error":"Invalid payload: expected {signal: {...}}"}), 400
    sig = d["signal"]
    if not isinstance(sig, dict) or "pair" not in sig:
        return jsonify({"error":"Invalid signal object"}), 400
    if _kill_switch:
        return jsonify({"error":"Kill-switch active — system paused"}), 503
    try:
        news_ctx = sig.get("newsCtx") or fetch_news_context()
        style_pref = d.get("stylePreference", "auto")
        result = run_ai(sig, news_ctx, style_pref)
        # N9: Audit log — persist every AI analysis to SQLite
        try:
            _con = sqlite3.connect(_AUDIT_DB)
            _con.execute(
                "INSERT INTO audit_log(ts,pair,score,direction,trend,grade,edge_prob,risk,style) VALUES(?,?,?,?,?,?,?,?,?)",
                (datetime.now(timezone.utc).isoformat(), sig.get("pair"), sig.get("confluenceScore"),
                 sig.get("direction"), sig.get("trendState"), result.get("grade"),
                 result.get("edgeProbability"), result.get("riskLevel"), style_pref)
            )
            _con.commit()
            _con.close()
        except Exception as _ae:
            log.warning(f"Audit DB write failed: {_ae}")
        return jsonify(result)
    except Exception as e:
        # S2: Sanitise exception — don't leak internal paths
        log.error(f"api_analyze error: {e}")
        return jsonify({"error":"Analysis failed"}), 500

@app.route("/api/screener-scan", methods=["POST"])
def api_screener_scan():
    """Phase C: Discover new high-cap momentum stocks via EODHD screener. Finds candidates not yet in our 70 pairs."""
    try:
        _key = os.environ.get("EODHD_KEY", "")
        if not _key: return jsonify({"error": "EODHD_KEY not set"}), 500
        d = request.json or {}
        min_cap = d.get("minMarketCap", 50000000000)  # $50B default
        limit   = min(d.get("limit", 50), 100)
        # Fetch top momentum stocks: sorted by 200d_new_hi (price near 52-week high)
        url = (f"https://eodhd.com/api/screener?api_token={_key}&sort=200d_new_hi-desc"
               f"&filters=[[\"market_capitalization\",\">\",{min_cap}]]"
               f"&limit={limit}&offset=0&fmt=json")
        r = http_requests.get(url, timeout=15)
        if r.status_code == 403: return jsonify({"error": "Screener requires All-In-One plan (403)"}), 403
        if r.status_code != 200: return jsonify({"error": f"EODHD screener error: HTTP {r.status_code}"}), 502
        data = r.json()
        rows = data.get("data", data) if isinstance(data, dict) else data
        if not rows or not isinstance(rows, list): return jsonify({"error": "No screener results"}), 404
        # Cross-reference against our existing 70 pairs
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
        log.info(f"[SCREENER] Found {len(candidates)} new candidates, {len(already_tracked)} already tracked")
        return jsonify({
            "success": True,
            "newCandidates": candidates[:20],
            "alreadyTracked": already_tracked[:10],
            "totalScanned": len(rows),
            "scannedAt": datetime.now(timezone.utc).isoformat()
        })
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
    was_enabled = pair.get("enabled", True)
    # Enable criteria: SQN > 0.5, IS positive, OOS non-negative (or too few OOS trades to judge)
    should_enable = sqn > 0.5 and is_sqn > 0 and (oos_sqn >= 0 or oos_trades < 3)
    # Disable criteria: overall SQN negative or IS clearly negative
    should_disable = sqn <= 0 or is_sqn < -0.5
    action = None
    if should_enable and not was_enabled:
        pair["enabled"] = True
        action = "enabled"
        log.info(f"[BT-AUTO] {pair['display']} AUTO-ENABLED (SQN:{sqn}, IS:{is_sqn}, OOS:{oos_sqn})")
    elif should_disable and was_enabled:
        pair["enabled"] = False
        action = "disabled"
        log.warning(f"[BT-AUTO] {pair['display']} AUTO-DISABLED (SQN:{sqn}, IS:{is_sqn}, OOS:{oos_sqn})")
    if action:
        ACTIVE_PAIRS = [p for p in ALL_PAIRS if p.get("enabled", True)]
    return action

@app.route("/api/backtest",methods=["POST"])
def api_backtest():
    try:
        d=request.json or {}
        # S1: Validate symbol if provided
        sym=d.get("symbol")
        if sym:
            if not isinstance(sym, str): return jsonify({"error":"Invalid symbol"}), 400
            pair=next((p for p in ALL_PAIRS if p["symbol"]==sym),None)
            if not pair: return jsonify({"error":"Unknown symbol"}), 404
            result = backtest_pair(pair)
            toggle = _auto_toggle_pair(pair, result)
            if toggle: result["autoToggle"] = toggle
            result["activePairs"] = len(ACTIVE_PAIRS)
            return jsonify(result)
        # Full backtest — auto-toggle each pair
        full = run_full_backtest()
        toggles = []
        for r in full.get("results", []):
            p = next((p for p in ALL_PAIRS if p["symbol"] == r.get("symbol")), None)
            if p:
                t = _auto_toggle_pair(p, r)
                if t: toggles.append({"pair": r["pair"], "action": t, "sqn": r["sqn"]})
        full["autoToggles"] = toggles
        full["activePairs"] = len(ACTIVE_PAIRS)
        return jsonify(full)
    except Exception as e:
        # S2: Sanitise exception
        log.error(f"api_backtest error: {e}")
        return jsonify({"error":"Backtest failed"}), 500

# N4: Kill-switch API — immediately blocks new scans/analyses
@app.route("/api/killswitch",methods=["POST"])
def api_killswitch():
    global _kill_switch
    d=request.json or {}
    action=d.get("action","toggle")
    if action=="on": _kill_switch=True
    elif action=="off": _kill_switch=False
    else: _kill_switch=not _kill_switch
    log.warning(f"KILL-SWITCH {'ACTIVATED' if _kill_switch else 'DEACTIVATED'}")
    return jsonify({"killSwitch":_kill_switch})

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
    ACTIVE_PAIRS = [p for p in ALL_PAIRS if p.get("enabled", True) and p["display"] not in _disabled_pairs]
    log.warning(f"[KILL] Pair {display!r}: {'ENABLED' if enabled else 'DISABLED'} ({len(ACTIVE_PAIRS)} active)")
    return jsonify({"pair": display, "enabled": enabled, "activePairs": len(ACTIVE_PAIRS),
                    "disabledPairs": sorted(_disabled_pairs)})

@app.route("/api/killswitch/pair")
def api_killswitch_pair_list():
    """List all disabled pairs."""
    return jsonify({"disabledPairs": sorted(_disabled_pairs), "activePairs": len(ACTIVE_PAIRS)})

@app.route("/api/prices")
def api_prices():
    return jsonify({"prices": _live_prices, "count": len(_live_prices),
                    "ts": datetime.now(timezone.utc).isoformat()})

@app.route("/api/yield-curve")
def api_yield_curve():
    """Phase E: Yield curve data for dashboard widget."""
    try:
        yc = fetch_yield_curve()
        if not yc: return jsonify({"error": "Yield curve unavailable"}), 503
        return jsonify(yc)
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/api/bulk-prices")
def api_bulk_prices():
    """Phase D: Bulk live OHLCV for US stocks via EODHD real-time endpoint (1 call vs multiple WS connections)."""
    try:
        _key = os.environ.get("EODHD_KEY", "")
        if not _key: return jsonify({"error": "EODHD_KEY not set"}), 500
        syms = request.args.get("symbols", "GOOG.US,GLD.US,SPY.US,QQQ.US")
        r = http_requests.get(f"https://eodhd.com/api/real-time/{syms.split(',')[0]}?s={','.join(syms.split(',')[1:])}&api_token={_key}&fmt=json", timeout=8)
        if r.status_code != 200: return jsonify({"error": f"HTTP {r.status_code}"}), 502
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
                "timestamp": row.get("timestamp")
            }
        return jsonify({"prices": results, "ts": datetime.now(timezone.utc).isoformat()})
    except Exception as e:
        log.error(f"api_bulk_prices error: {e}")
        return jsonify({"error": "Bulk prices failed"}), 500

@app.route("/api/health")
def health():
    return jsonify({"status":"paused" if _kill_switch else "ok","killSwitch":_kill_switch,
        "pairs":len(ALL_PAIRS),"activePairs":len(ACTIVE_PAIRS),
        "dataSource":"yfinance+binance",
        "anthropicKey":CONFIG["ANTHROPIC_KEY"]!="YOUR_ANTHROPIC_API_KEY"})

@app.route("/api/audit")
def api_audit():
    """Return last N audit log entries from SQLite."""
    limit = min(int(request.args.get("limit", 50)), 500)
    try:
        con = sqlite3.connect(_AUDIT_DB)
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
        missing.append("EODHD_KEY — real-time WebSocket prices, indicators, screener, and news all disabled")
    _ak = os.environ.get("ANTHROPIC_KEY", CONFIG.get("ANTHROPIC_KEY", ""))
    if not _ak or _ak == "YOUR_ANTHROPIC_API_KEY":
        missing.append("ANTHROPIC_KEY — AI trade grading disabled")
    if not os.environ.get("CRYPTOPANIC_KEY"):
        missing.append("CRYPTOPANIC_KEY (optional) — crypto news sentiment reduced")
    if not os.environ.get("FINNHUB_KEY"):
        missing.append("FINNHUB_KEY (optional) — Polygon news fallback disabled")
    if missing:
        log.warning("[KEYS] Running in degraded mode — set missing keys in .env:")
        for m in missing:
            log.warning(f"  • {m}")
    else:
        log.info("[KEYS] All API keys configured")


if __name__=="__main__":
    log.info("="*60)
    log.info("ATHENA PRO v3.1 - Python Edition")
    log.info("="*60)
    _check_api_keys()
    active_fx=sum(1 for p in FOREX_PAIRS if p.get("enabled",True))
    active_cr=sum(1 for p in CRYPTO_PAIRS if p.get("enabled",True))
    log.info(f"Pairs: {len(ACTIVE_PAIRS)} active / {len(ALL_PAIRS)} total ({active_fx}fx {len(COMMODITY_PAIRS)}cmd {sum(1 for p in INDEX_PAIRS if p.get('enabled',True))}idx {sum(1 for p in JSE_PAIRS if p.get('enabled',True))}jse {active_cr}crypto)")
    log.info(f"Data: yfinance (free) + Binance (free)")
    log.info(f"Est. scan time: ~30s")
    if "--scan" in sys.argv:
        log.info("[SCAN MODE] Running full scan...")
        scan_result = run_full_scan()
        log.info(f"Scan complete: {scan_result['totalPairs']} pairs, {len(scan_result['signals'])} signals")
        if scan_result['errors']: log.warning(f"Errors ({len(scan_result['errors'])}): {[e['pair']+': '+e['error'] for e in scan_result['errors']]}")
        if scan_result['skipped']: log.info(f"Skipped ({len(scan_result['skipped'])}): {[s['pair'] for s in scan_result['skipped']]}")
        if scan_result['signals']:
            log.info("[AI TEST] Testing AI on top signal...")
            top = scan_result['signals'][0]
            ai_result = run_ai(top)
            if "error" in ai_result:
                log.error(f"[AI TEST] FAILED: {ai_result['error']}")
            else:
                log.info(f"[AI TEST] OK => Grade:{ai_result.get('grade','?')} Prob:{ai_result.get('edgeProbability','?')}%")
        else:
            log.info("[AI TEST] Skipped — no signals to test")
        sys.exit(0)
    # Start EODHD WebSocket real-time price streaming
    _ws_key = os.environ.get("EODHD_KEY", "")
    if _ws_key:
        _ws_mgr = EODHDWebSocketManager(_ws_key)
        _ws_mgr.start(ACTIVE_PAIRS)
    else:
        log.warning("[WS] No EODHD_KEY — WebSocket prices disabled")
    log.info(f"http://localhost:5000")
    threading.Timer(1.5,lambda:webbrowser.open("http://localhost:5000")).start()
    app.run(host="0.0.0.0",port=5000,debug=False)
