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

# N1: Load external config.yaml overrides (tunable thresholds without code deploy)
_yaml_overrides = {}
try:
    import yaml as _yaml
    _cfg_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.yaml")
    if os.path.exists(_cfg_path):
        with open(_cfg_path, "r") as _f:
            _yaml_overrides = _yaml.safe_load(_f) or {}
        log.info(f"Loaded config.yaml ({len(_yaml_overrides)} keys)")
except ImportError:
    pass  # pyyaml optional
except Exception as _e:
    log.warning(f"config.yaml load failed: {_e}")

CONFIG = {
    # "TWELVE_DATA_KEY": os.environ.get("TWELVE_DATA_KEY", ""),  # kept for revert reference
    "ANTHROPIC_KEY": os.environ.get("ANTHROPIC_KEY", "YOUR_ANTHROPIC_API_KEY"),
    "ANTHROPIC_MODEL": "claude-sonnet-4-6",
    "CRYPTOPANIC_KEY": os.environ.get("CRYPTOPANIC_KEY", ""),
    "FINNHUB_KEY": os.environ.get("FINNHUB_KEY", ""),
    "RISK_PCT": 0.01, "SL_ATR_MULT": 1.5, "TP1_ATR_MULT": 2.0, "TP2_ATR_MULT": 3.5,
    "VOLUME_THRESHOLD": 1.5, "ADX_TREND_MIN": 25,
    "D1_CANDLES": 250, "H4_CANDLES": 120, "H1_CANDLES": 120, "MIN_CONFLUENCE": 7.0,
    # Asset-class risk multipliers — applied to RISK_PCT in backtest and live scan
    # Metals = alpha driver (1.2x), Crypto = high-conviction burst (0.8x), Forex/Index/Stock = stabiliser (0.6x)
    "RISK_MULT": {"commodity":1.2, "crypto":0.8, "forex":0.6, "index":0.6, "stock":0.6},
    # Asset-class ranging suppression thresholds — crypto is more tolerant (trends hard, consolidates often)
    # (dead_thresh, dead_penalty, choppy_thresh, choppy_penalty)
    "RANGING": {
        "crypto":    {"dead":14, "dead_pen":3.0, "choppy":18, "choppy_pen":1.5},
        "commodity": {"dead":18, "dead_pen":3.0, "choppy":23, "choppy_pen":1.5},
        "forex":     {"dead":16, "dead_pen":3.0, "choppy":20, "choppy_pen":1.5},
        "stock":     {"dead":16, "dead_pen":3.0, "choppy":21, "choppy_pen":1.5},
        "index":     {"dead":16, "dead_pen":3.0, "choppy":21, "choppy_pen":1.5},
    },
    "ATR_CLASS": {
        "forex":     {"sl":1.2, "tp1":2.0, "tp2":3.0},
        "commodity": {"sl":1.5, "tp1":2.5, "tp2":4.0},
        "index":     {"sl":1.5, "tp1":2.5, "tp2":4.0},
        "stock":     {"sl":1.5, "tp1":2.5, "tp2":4.0},
        "crypto":    {"sl":2.0, "tp1":3.5, "tp2":5.0}
    },
    # F1: Per-class D1 ADX trend minimum — crypto trends emerge at ADX 18-22
    "ADX_TREND_MIN_CLASS": {"crypto":20, "forex":22, "commodity":25, "stock":25, "index":25},
    # F3: Per-class counter-trend penalty — crypto breaks D1 trends routinely from H4
    "COUNTER_TREND_PEN": {"crypto":-1.5, "forex":-2.0, "commodity":-3.0, "stock":-3.0, "index":-3.0},
    # F4: Per-class RSI bounds — crypto stays overbought for weeks in bull runs
    "RSI_BOUNDS": {
        "crypto":    {"ob":88, "os":15},
        "forex":     {"ob":80, "os":20},
        "commodity": {"ob":78, "os":22},
        "stock":     {"ob":78, "os":22},
        "index":     {"ob":78, "os":22},
    },
    # F6: Per-class backtest macro lookback — crypto is fast-cycling
    "MACRO_LOOKBACK": {"crypto":15, "forex":15, "commodity":50, "stock":50, "index":50},
    # F7: Per-class Weinstein lookback (D1 bars) — crypto cycles are 60-90 bars vs 150 for equities
    "WEINSTEIN_LOOKBACK": {"crypto":60, "forex":100, "commodity":150, "stock":150, "index":150},
    # V2: Per-class bt_min + live MIN_CONFLUENCE — crypto requires higher conviction
    "BT_MIN": {"crypto":8.0, "commodity":6.0, "forex":5.5, "stock":6.0, "index":6.0},
    "MIN_CONFLUENCE_CLASS": {"crypto":8.0, "commodity":6.0, "forex":5.5, "stock":6.0, "index":6.0},
}
# N1: Apply YAML overrides — deep-merge dicts, overwrite scalars
for _k, _v in _yaml_overrides.items():
    if _k in CONFIG and isinstance(CONFIG[_k], dict) and isinstance(_v, dict):
        CONFIG[_k].update(_v)
    else:
        CONFIG[_k] = _v

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

def fetch_candles(pair, tf, limit):
    """Route candle fetch to correct source (binance, yfinance, or polygon) based on pair config."""
    if pair["source"]=="binance": return fetch_binance(pair["symbol"], TF_B[tf], limit)
    if pair["source"]=="eodhd": return fetch_eodhd(pair, tf, limit)
    if pair["source"]=="polygon": return fetch_polygon(pair, tf, limit)
    if pair["source"]=="yfinance": return fetch_yfinance(pair["symbol"], tf, limit)
    return None

def calc_ema(c, p):
    """Exponential Moving Average. Returns list aligned with input, None-padded."""
    k=2/(p+1); e=[None]*len(c)
    if len(c)<p: return e
    e[p-1]=sum(c[:p])/p
    for i in range(p,len(c)): e[i]=c[i]*k+e[i-1]*(1-k)
    return e

def calc_sma(a, p):
    """Simple Moving Average. Returns list aligned with input, None-padded."""
    r=[None]*len(a)
    for i in range(p-1,len(a)): r[i]=sum(a[i-p+1:i+1])/p
    return r

def calc_rsi(c, p):
    """Wilder RSI (smoothed). Returns list aligned with input, None-padded."""
    r=[None]*len(c)
    if len(c)<p+1: return r
    g=l=0
    for i in range(1,p+1):
        d=c[i]-c[i-1]
        if d>0: g+=d
        else: l-=d
    ag,al=g/p,l/p
    r[p]=100 if al==0 else 100-100/(1+ag/al)
    for i in range(p+1,len(c)):
        d=c[i]-c[i-1]; ag=(ag*(p-1)+max(d,0))/p; al=(al*(p-1)+max(-d,0))/p
        r[i]=100 if al==0 else 100-100/(1+ag/al)
    return r

def calc_macd(c, f=12, s=26, sig=9):
    ef,es=calc_ema(c,f),calc_ema(c,s)
    ml=[ef[i]-es[i] if ef[i] is not None and es[i] is not None else None for i in range(len(c))]
    valid=[v for v in ml if v is not None]; se=calc_ema(valid,sig)
    sl2=[None]*len(c); vf=next((i for i,v in enumerate(ml) if v is not None),len(c)); si=0
    for i in range(vf,len(c)): sl2[i]=se[si] if si<len(se) else None; si+=1
    hist=[ml[i]-sl2[i] if ml[i] is not None and sl2[i] is not None else None for i in range(len(c))]
    return {"macd":ml,"signal":sl2,"hist":hist}

def calc_atr(h, l, c, p):
    tr=[0]+[max(h[i]-l[i],abs(h[i]-c[i-1]),abs(l[i]-c[i-1])) for i in range(1,len(c))]
    a=[None]*len(c)
    if len(tr)<=p: return a
    a[p]=sum(tr[1:p+1])/p
    for i in range(p+1,len(tr)): a[i]=(a[i-1]*(p-1)+tr[i])/p
    return a

def calc_adx(hi, lo, c, p):
    """Wilder ADX with +DI/-DI. Returns dict with aligned arrays, None-padded."""
    n=len(c); adx=[None]*n; plus_di=[None]*n; minus_di=[None]*n
    if n<p*2: return {"adx":adx,"plusDI":plus_di,"minusDI":minus_di}
    true_range,dm_plus,dm_minus=[],[],[]
    for i in range(1,n):
        up_move,down_move=hi[i]-hi[i-1],lo[i-1]-lo[i]
        dm_plus.append(up_move if up_move>down_move and up_move>0 else 0)
        dm_minus.append(down_move if down_move>up_move and down_move>0 else 0)
        true_range.append(max(hi[i]-lo[i],abs(hi[i]-c[i-1]),abs(lo[i]-c[i-1])))
    smooth_tr,smooth_dp,smooth_dm=sum(true_range[:p]),sum(dm_plus[:p]),sum(dm_minus[:p]); dx_values=[]
    for i in range(p,len(true_range)):
        smooth_tr=smooth_tr-smooth_tr/p+true_range[i]
        smooth_dp=smooth_dp-smooth_dp/p+dm_plus[i]
        smooth_dm=smooth_dm-smooth_dm/p+dm_minus[i]
        pdi_val=(smooth_dp/smooth_tr)*100 if smooth_tr else 0
        mdi_val=(smooth_dm/smooth_tr)*100 if smooth_tr else 0
        di_sum=pdi_val+mdi_val
        plus_di[i+1]=pdi_val; minus_di[i+1]=mdi_val
        dx_values.append(abs(pdi_val-mdi_val)/di_sum*100 if di_sum else 0)
    if len(dx_values)>=p:
        adx_avg=sum(dx_values[:p])/p
        if p*2<n: adx[p*2]=adx_avg
        for i in range(p,len(dx_values)):
            adx_avg=(adx_avg*(p-1)+dx_values[i])/p; idx=i+p+1
            if idx<n: adx[idx]=adx_avg
    return {"adx":adx,"plusDI":plus_di,"minusDI":minus_di}

def calc_bb(c, p, m):
    u,mid,l=[],[],[]
    for i in range(len(c)):
        if i<p-1: u.append(None); mid.append(None); l.append(None); continue
        sl=c[i-p+1:i+1]; mn=sum(sl)/p; sd=math.sqrt(sum((x-mn)**2 for x in sl)/p)
        mid.append(mn); u.append(mn+m*sd); l.append(mn-m*sd)
    return {"upper":u,"mid":mid,"lower":l}

def calc_rsi_divergence(candles, lookback=30):
    """Wilder: RSI divergence using proper 3-bar pivot detection.
    Returns: 'bullish', 'bearish', or None"""
    try:
        c=candles[-lookback:]; cl=[x["close"] for x in c]; hi=[x["high"] for x in c]; lo=[x["low"] for x in c]
        rsi=calc_rsi(cl,14); n=len(cl)
        if n<15 or rsi[-1] is None: return None
        # Find pivot highs: hi[i] > hi[i-1] and hi[i] > hi[i+1] (local maxima)
        phigh_idx=[i for i in range(1,n-1) if hi[i]>hi[i-1] and hi[i]>hi[i+1] and rsi[i] is not None]
        # Find pivot lows: lo[i] < lo[i-1] and lo[i] < lo[i+1] (local minima)
        plow_idx=[i for i in range(1,n-1) if lo[i]<lo[i-1] and lo[i]<lo[i+1] and rsi[i] is not None]
        # Bearish divergence: need 2 pivot highs, price higher high, RSI lower high
        if len(phigh_idx)>=2:
            i1,i2=phigh_idx[-2],phigh_idx[-1]
            if hi[i2]>hi[i1]*1.001 and rsi[i2]<rsi[i1]*0.99: return "bearish"
        # Bullish divergence: need 2 pivot lows, price lower low, RSI higher low
        if len(plow_idx)>=2:
            i1,i2=plow_idx[-2],plow_idx[-1]
            if lo[i2]<lo[i1]*0.999 and rsi[i2]>rsi[i1]*1.01: return "bullish"
    except Exception as e: log.warning(f"calc_rsi_divergence: {e}")
    return None

def calc_weinstein_stage(candles, lookback=150):
    """Stan Weinstein stage analysis using configurable MA lookback.
    F7: Per-class lookback — crypto=60, forex=100, equities=150 D1 bars.
    Stage 1=Basing, Stage 2=Advancing, Stage 3=Topping, Stage 4=Declining"""
    try:
        cl=[c["close"] for c in candles]
        if len(cl)<lookback: return None,None
        ma30w=calc_sma(cl,lookback); L=len(cl)-1
        if ma30w[L] is None or ma30w[L-5] is None: return None,None
        price=cl[L]; ma=ma30w[L]; ma_prev=ma30w[L-5]
        ma_rising=ma>ma_prev
        if price>ma and ma_rising: return 2,"Stage 2 — Advancing"
        if price>ma and not ma_rising: return 3,"Stage 3 — Topping"
        if price<ma and not ma_rising: return 4,"Stage 4 — Declining"
        if price<ma and ma_rising: return 1,"Stage 1 — Basing"
    except Exception as e: log.warning(f"calc_weinstein_stage: {e}")
    return None,None

def calc_fib_proximity(price, fib):
    """Check if price is near a key Fibonacci level (within 1.5% range band).
    Direction-agnostic: price BELOW a fib level = support = bullish vote (+1).
    Price ABOVE a fib level = resistance = bearish vote (-1). 0 = not near any level.
    This avoids circular bias where direction determines fib vote before all votes are in."""
    try:
        levels=[fib["fib382"],fib["fib500"],fib["fib618"]]
        rng=fib["highest"]-fib["lowest"]
        if rng<=0: return 0
        band=rng*0.015
        for lvl in levels:
            if abs(price-lvl)<=band:
                if price<=lvl: return 1   # price at or below fib = support zone = bullish
                else:          return -1  # price at or above fib = resistance zone = bearish
    except Exception as e: log.warning(f"calc_fib_proximity: {e}")
    return 0

def calc_stochastic(candles, kp, ks, ds):
    hi=[c["high"] for c in candles]; lo=[c["low"] for c in candles]; cl=[c["close"] for c in candles]
    n=len(cl); rawK=[None]*n
    for i in range(kp-1,n):
        hh,ll=max(hi[i-kp+1:i+1]),min(lo[i-kp+1:i+1])
        rawK[i]=((cl[i]-ll)/(hh-ll))*100 if hh!=ll else 50
    mapped=[v if v is not None else 0 for v in rawK]; kL=calc_sma(mapped,ks)
    for i in range(kp-1+ks-1):
        if i<len(kL): kL[i]=None
    mapped2=[v if v is not None else 0 for v in kL]; dL=calc_sma(mapped2,ds)
    for i in range(kp-1+ks-1+ds-1):
        if i<len(dL): dL[i]=None
    return {"k":kL,"d":dL}

def calc_adx_momentum(adx_series, window=5):
    """CR1/CR2: Detect ADX momentum — is the trend strengthening or exhausting?
    Returns (slope, state) where state is one of:
      'strengthening' — ADX rising, trend getting stronger
      'exhausting'    — ADX was >30 and is now falling (trend losing steam)
      'collapsing'    — ADX falling fast from >25 (regime transition imminent)
      'stable'        — ADX roughly flat
    This is critical for crypto where regimes shift in days not weeks."""
    valid = [v for v in adx_series if v is not None]
    if len(valid) < window + 2:
        return 0, "stable"
    recent = valid[-(window+1):]
    slope = (recent[-1] - recent[0]) / window
    peak = max(valid[-20:]) if len(valid) >= 20 else max(valid)
    cur = valid[-1]
    if slope > 0.5:
        return round(slope, 2), "strengthening"
    elif peak > 30 and cur < peak * 0.8 and slope < -0.3:
        return round(slope, 2), "collapsing"
    elif peak > 25 and slope < -0.3:
        return round(slope, 2), "exhausting"
    return round(slope, 2), "stable"

def calc_adx_percentile(adx_series, lookback=252):
    """Rank current ADX vs its own history — tells if momentum is expanding or contracting.
    Returns (percentile 0-100, label). E.g. 78th pct = ADX higher than 78% of last 252 bars."""
    valid=[v for v in adx_series if v is not None]
    if len(valid)<20: return None,"insufficient data"
    cur=valid[-1]; window=valid[-lookback:]
    pct=round(sum(1 for v in window if v<=cur)/len(window)*100,1)
    label="expanding" if (len(valid)>=3 and valid[-1]>valid[-2]) else "contracting"
    return pct,label

def calc_atr_percentile(atr_series, lookback=100):
    """V1: Rank current ATR vs its own history — detects volatility compression/expansion.
    Returns (percentile 0-100, label). Crypto trends explode from compression (low ATR pct)."""
    valid=[v for v in atr_series if v is not None]
    if len(valid)<20: return None,"insufficient data"
    cur=valid[-1]; window=valid[-lookback:]
    pct=round(sum(1 for v in window if v<=cur)/len(window)*100,1)
    expanding = len(valid)>=3 and valid[-1]>valid[-2]
    label="expanding" if expanding else "contracting"
    return pct,label

def calc_levels(price, atr, direction, pair_type):
    """C4: Shared SL/TP calculation — used by both analyze_pair and backtest_pair."""
    m=CONFIG["ATR_CLASS"].get(pair_type,{"sl":CONFIG["SL_ATR_MULT"],"tp1":CONFIG["TP1_ATR_MULT"],"tp2":CONFIG["TP2_ATR_MULT"]})
    sl =price-atr*m["sl"]  if direction=="LONG" else price+atr*m["sl"]
    tp1=price+atr*m["tp1"] if direction=="LONG" else price-atr*m["tp1"]
    tp2=price+atr*m["tp2"] if direction=="LONG" else price-atr*m["tp2"]
    rr1=abs(tp1-price)/abs(sl-price) if abs(sl-price)>0 else 0
    rr2=abs(tp2-price)/abs(sl-price) if abs(sl-price)>0 else 0
    return {"sl":sl,"tp1":tp1,"tp2":tp2,"rr1":rr1,"rr2":rr2,"mults":m}

def calc_indicators(candles):
    """Compute all indicators for a candle series. Returns dict with 'snap' of latest values."""
    cl=[c["close"] for c in candles]; hi=[c["high"] for c in candles]; lo=[c["low"] for c in candles]
    e21,e50,e200=calc_ema(cl,21),calc_ema(cl,50),calc_ema(cl,200)
    rsi=calc_rsi(cl,14); macd=calc_macd(cl); atr=calc_atr(hi,lo,cl,14)
    adx=calc_adx(hi,lo,cl,14); bb=calc_bb(cl,20,2); L=len(cl)-1
    adx_now=adx["adx"][L]; adx_prev=next((adx["adx"][j] for j in range(L-1,-1,-1) if adx["adx"][j] is not None),None)
    rsi_prev=next((rsi[j] for j in range(L-1,-1,-1) if rsi[j] is not None),None)
    e200_slope=round((e200[L]-e200[L-10])/e200[L-10]*100,3) if e200[L] and L>=10 and e200[L-10] else 0
    adx_pct,adx_lbl=calc_adx_percentile(adx["adx"])
    atr_pct,atr_lbl=calc_atr_percentile(atr)
    adx_slope,adx_momentum=calc_adx_momentum(adx["adx"])
    return {"snap":{"ema21":e21[L],"ema50":e50[L],"ema200":e200[L],"close":cl[L],"rsi":rsi[L],"rsiPrev":rsi_prev,
        "macdLine":macd["macd"][L],"macdSignal":macd["signal"][L],"macdHist":macd["hist"][L],
        "macdHistPrev":macd["hist"][L-1] if L>0 else None,
        "atr":atr[L],"adx":adx_now,"adxPrev":adx_prev,"adxPct":adx_pct,"adxLabel":adx_lbl,
        "atrPct":atr_pct,"atrLabel":atr_lbl,
        "adxSlope":adx_slope,"adxMomentum":adx_momentum,
        "plusDI":adx["plusDI"][L],"minusDI":adx["minusDI"][L],
        "bbUpper":bb["upper"][L],"bbMid":bb["mid"][L],"bbLower":bb["lower"][L],"ema200Slope10":e200_slope}}

def calc_fib(candles):
    """Calculate Fibonacci retracement levels from last 50 candles' high/low range."""
    r=candles[-50:]; high=max(c["high"] for c in r); low=min(c["low"] for c in r); rng=high-low
    return {"highest":round(high,6),"lowest":round(low,6),"fib236":round(high-rng*0.236,6),
            "fib382":round(high-rng*0.382,6),"fib500":round(high-rng*0.5,6),"fib618":round(high-rng*0.618,6),
            "fib786":round(high-rng*0.786,6),"ext1618":round(high+rng*0.618,6)}

def get_session():
    """Determine current forex session (Asian/London/NY/Overlap) from UTC hour."""
    h=datetime.now(timezone.utc).hour
    if 7<=h<9: return {"name":"London Open","quality":"high","color":"#22c55e"}
    if 13<=h<16: return {"name":"London/NY Overlap","quality":"high","color":"#22c55e"}
    if 9<=h<13: return {"name":"London","quality":"medium","color":"#3b82f6"}
    if 16<=h<22: return {"name":"New York","quality":"medium","color":"#3b82f6"}
    return {"name":"Asian / Off-Hours","quality":"low","color":"#f59e0b"}

def calc_confluence(d1, h4, h1, vr, stoch, e200s, pair, btc_bias, d1_candles=None, h4_candles=None, h1_candles=None):
    """Weighted confluence system — higher timeframes score more than lower timeframes.
    Weights reflect statistical importance: D1 > H4 > H1 (Elder/Wilder/Cardwell/Murphy/Weinstein).
    Max possible score = 13.0. Counter-trend penalty = -3.0.
    Ranging suppression: ADX<20 on H4 = brutal score reduction (markets bleed in chop).

    Vote weights:
      D1 Trend Gate     = 2.0  (highest — primary trend filter)
      D1 ADX Trend      = 1.5  (confirms D1 is trending not drifting)
      D1 Weinstein Stage= 1.5  (independent cycle stage)
      H4 MACD Momentum  = 1.5  (Elder Screen 2 momentum wave)
      H4 RSI Zone       = 1.0  (Cardwell regime health)
      H4 Stochastic     = 1.0  (entry timing)
      H1 EMA Entry      = 1.0  (Elder Screen 3 trigger)
      H1 BB Pullback    = 0.5  (weakest — noise-prone)
      H1 RSI Divergence = 1.0  (strong reversal signal when present)
      H4 Fib Level      = 1.0  (Murphy price-at-fib confluence)
    """
    v={}; w=[]; bull=bear=0.0; s=d1["snap"]; s4=h4["snap"]; s1=h1["snap"]

    # ── VOTE 1: D1 Trend Gate — weight 2.0 (Elder Screen 1) ────────────────
    # F2: Crypto gets partial credit (1.0) for ema21>ema50 even without ema200 alignment
    # Forex: reduced to 1.0 — forex is more mean-reverting than trending
    W1=1.0 if pair["type"]=="forex" else 2.0; d1_trend=0; _ptype=pair["type"]
    if s["ema21"] and s["ema50"] and s["ema200"]:
        if s["ema21"]>s["ema50"]>s["ema200"]:   v["D1 Trend Gate"]=1;  bull+=W1; d1_trend=1
        elif s["ema21"]<s["ema50"]<s["ema200"]: v["D1 Trend Gate"]=-1; bear+=W1; d1_trend=-1
        elif _ptype=="crypto" and s["ema21"]>s["ema50"]: v["D1 Trend Gate"]=1; bull+=1.0; d1_trend=1; w.append("D1 EMA partial — ema21>ema50 but ema200 not aligned (crypto partial credit 1.0)")
        elif _ptype=="crypto" and s["ema21"]<s["ema50"]: v["D1 Trend Gate"]=-1; bear+=1.0; d1_trend=-1; w.append("D1 EMA partial — ema21<ema50 but ema200 not aligned (crypto partial credit 1.0)")
        else: v["D1 Trend Gate"]=0; w.append("D1 EMA stack mixed — no clear trend")
    else: v["D1 Trend Gate"]=0

    # ── VOTE 2: D1 ADX Trend Strength — weight 1.5 (Wilder) ────────────────
    # F1: Per-class ADX threshold. R1: Percentile gradient (75th+=full, 50th+=1.0, 25th+=0.5)
    W2=1.5; d1_adx=s.get("adx"); d1_pdi=s.get("plusDI"); d1_mdi=s.get("minusDI")
    _adx_min=CONFIG["ADX_TREND_MIN_CLASS"].get(_ptype, 25)
    _d1_adx_pct=s.get("adxPct")
    if d1_adx is not None and d1_pdi is not None and d1_mdi is not None:
        # R1: Percentile-based gradient scoring
        if _d1_adx_pct is not None and _d1_adx_pct>=75:
            _w2=W2  # full 1.5
        elif _d1_adx_pct is not None and _d1_adx_pct>=50:
            _w2=1.0  # partial
        elif d1_adx>=_adx_min:
            _w2=0.5  # floor credit if above class threshold
        else:
            _w2=0
        if _w2>0:
            if d1_pdi>d1_mdi: v["D1 ADX Trend"]=1;  bull+=_w2
            else:              v["D1 ADX Trend"]=-1; bear+=_w2
        else:
            v["D1 ADX Trend"]=0
            adx_str=f"{d1_adx:.1f}" if d1_adx is not None else "n/a"
            w.append(f"D1 ADX weak ({adx_str}, pct:{_d1_adx_pct}) — below {_ptype} threshold ({_adx_min})")
    else:
        v["D1 ADX Trend"]=0

    # ── VOTE 3: D1 Weinstein Stage — weight 1.5 ─────────────────────────────
    # F7: Per-class Weinstein lookback — crypto=60, forex=100, equities=150
    W3=1.5; weinstein_stage=None; weinstein_label=None
    _wein_lb=CONFIG["WEINSTEIN_LOOKBACK"].get(_ptype, 150)
    if d1_candles:
        weinstein_stage, weinstein_label = calc_weinstein_stage(d1_candles, lookback=_wein_lb)
        if weinstein_stage==2:   v["D1 Weinstein Stage"]=1;  bull+=W3
        elif weinstein_stage==4: v["D1 Weinstein Stage"]=-1; bear+=W3
        elif weinstein_stage==3: v["D1 Weinstein Stage"]=0; w.append(f"Weinstein {weinstein_label} — potential distribution, avoid new longs")
        elif weinstein_stage==1: v["D1 Weinstein Stage"]=0; w.append(f"Weinstein {weinstein_label} — basing, wait for Stage 2 breakout")
        else: v["D1 Weinstein Stage"]=0
    else: v["D1 Weinstein Stage"]=0

    # Hard D1 gate for counter-trend penalty
    hard_long_block  = (v["D1 Trend Gate"]==-1)
    hard_short_block = (v["D1 Trend Gate"]==1)

    # ── FOREX SESSION FILTER — only fire during London/NY expansion windows ──
    # F5: AUD/NZD pairs are ACTIVE during Asian session — exempt from penalty
    _forex_session_pen = 0.0
    if _ptype == "forex":
        _sess = get_session()
        _is_asia_active = any(x in pair["display"] for x in ["AUD","NZD"])
        if _sess["quality"] == "low" and not _is_asia_active:
            _forex_session_pen = 1.5
            w.append(f"FOREX SESSION: {_sess['name']} — off-hours, low-expansion window, score penalised -1.5")
        elif _sess["quality"] == "low" and _is_asia_active:
            w.append(f"FOREX SESSION: {_sess['name']} — but {pair['display']} is active during Asian hours (no penalty)")

    # ── RANGING SUPPRESSION — asset-class aware ADX thresholds ─────────────
    # Crypto: tolerant (ADX<14 dead, <18 choppy) — trends hard, consolidates between legs
    # Commodity: moderate (ADX<18 dead, <23 choppy) — sustained multi-week trends
    # Forex/Stock/Index: stricter (ADX<16 dead, <20-21 choppy) — more mean-reverting
    adx_val=s4["adx"]; adx_prev=s4.get("adxPrev")
    _adx_mom=s4.get("adxMomentum","stable"); _adx_slope=s4.get("adxSlope",0)
    ranging_penalty=0.0
    _rng=CONFIG["RANGING"].get(pair["type"],CONFIG["RANGING"]["commodity"])
    if adx_val is not None and adx_val < _rng["dead"]:
        ranging_penalty = _rng["dead_pen"]
        w.append(f"DEAD RANGING: H4 ADX={adx_val:.1f} (<{_rng['dead']}) — score penalised -{_rng['dead_pen']}, avoid entirely")
    elif adx_val is not None and adx_val < _rng["choppy"]:
        ranging_penalty = _rng["choppy_pen"]
        w.append(f"CHOPPY MARKET: H4 ADX={adx_val:.1f} (<{_rng['choppy']}) — score penalised -{_rng['choppy_pen']}")

    # CR1/CR2: ADX momentum — detect regime TRANSITIONS for crypto
    # When ADX was strong but is now collapsing, trend-follow signals are stale
    if _ptype == "crypto" and _adx_mom in ("collapsing", "exhausting"):
        _trans_pen = 1.5 if _adx_mom == "collapsing" else 0.8
        ranging_penalty += _trans_pen
        w.append(f"REGIME TRANSITION: H4 ADX {_adx_mom} (slope={_adx_slope}) — trend fading, -{_trans_pen} penalty")

    # ── VOTE 4: H4 MACD Momentum — weight 1.5 (Elder Screen 2) ─────────────
    W4=1.5
    if s4["macdLine"] is not None and s4["macdSignal"] is not None and s4["macdHist"] is not None:
        hist_now=s4["macdHist"]; hist_prev=s4.get("macdHistPrev")
        if s4["macdLine"]>s4["macdSignal"] and hist_now>0:
            v["H4 MACD Momentum"]=1; bull+=W4
            if hist_prev is not None and hist_now<hist_prev: w.append("H4 MACD histogram decelerating — momentum fading")
        elif s4["macdLine"]<s4["macdSignal"] and hist_now<0:
            v["H4 MACD Momentum"]=-1; bear+=W4
            if hist_prev is not None and hist_now>hist_prev: w.append("H4 MACD histogram decelerating — momentum fading")
        else: v["H4 MACD Momentum"]=0
    else: v["H4 MACD Momentum"]=0

    # ── VOTE 5: H4 RSI Zone — weight 1.0 (Cardwell regime) ─────────────────
    # F4: Per-class RSI bounds — crypto stays overbought for weeks in bull runs
    W5=1.0; r4=s4["rsi"]
    _rsi_b=CONFIG["RSI_BOUNDS"].get(_ptype, {"ob":78, "os":22})
    if r4 is not None:
        if 45<r4<_rsi_b["ob"]:   v["H4 RSI Zone"]=1;  bull+=W5
        elif _rsi_b["os"]<r4<55: v["H4 RSI Zone"]=-1; bear+=W5
        elif r4>=_rsi_b["ob"]: v["H4 RSI Zone"]=0; w.append(f"H4 RSI overbought ({r4:.0f} >= {_rsi_b['ob']}) — wait for pullback")
        elif r4<=_rsi_b["os"]: v["H4 RSI Zone"]=0; w.append(f"H4 RSI oversold ({r4:.0f} <= {_rsi_b['os']}) — wait for bounce")
        else: v["H4 RSI Zone"]=0
    else: v["H4 RSI Zone"]=0

    # ── VOTE 6: H4 Stochastic Entry Timing — weight 1.0 ─────────────────────
    W6=1.0
    lK=stoch["k"][-1] if stoch["k"] and stoch["k"][-1] is not None else None
    lD=stoch["d"][-1] if stoch["d"] and stoch["d"][-1] is not None else None
    if lK is not None and lD is not None:
        if   lK>lD and lK<35:       v["H4 Stochastic"]=1;  bull+=W6
        elif lK<lD and lK>65:       v["H4 Stochastic"]=-1; bear+=W6
        elif lK>lD and 35<=lK<=55:  v["H4 Stochastic"]=1;  bull+=W6
        elif lK<lD and 45<=lK<=65:  v["H4 Stochastic"]=-1; bear+=W6
        else: v["H4 Stochastic"]=0
    else: v["H4 Stochastic"]=0

    # ── VOTE 7: H1 EMA Entry — weight 1.0 (Elder Screen 3) ──────────────────
    W7=1.0
    if s1["ema21"] and s1["ema50"]:
        if s1["ema21"]>s1["ema50"]: v["H1 EMA Entry"]=1;  bull+=W7
        else:                        v["H1 EMA Entry"]=-1; bear+=W7
    else: v["H1 EMA Entry"]=0

    # ── VOTE 8: H1 BB Pullback Zone — weight 0.5 (weakest signal) ───────────
    W8=0.5
    if s1["bbUpper"] is not None and s1["bbLower"] is not None:
        bbr=s1["bbUpper"]-s1["bbLower"]; cl1_p=s1.get("close")
        if bbr>0 and cl1_p is not None:
            bbp=(cl1_p-s1["bbLower"])/bbr
            if bbp<0.25:   v["H1 BB Pullback"]=1;  bull+=W8
            elif bbp>0.75: v["H1 BB Pullback"]=-1; bear+=W8
            else: v["H1 BB Pullback"]=0
        else: v["H1 BB Pullback"]=0
    else: v["H1 BB Pullback"]=0

    # ── VOTE 9: H1 RSI Divergence — weight 1.0 (Wilder reversal) ────────────
    W9=1.0
    if h1_candles:
        h1_div=calc_rsi_divergence(h1_candles)
        if h1_div=="bullish":   v["H1 RSI Divergence"]=1;  bull+=W9; w.append("H1 RSI Bullish Divergence — reversal signal (Wilder)")
        elif h1_div=="bearish": v["H1 RSI Divergence"]=-1; bear+=W9; w.append("H1 RSI Bearish Divergence — reversal signal (Wilder)")
        else: v["H1 RSI Divergence"]=0
    else: v["H1 RSI Divergence"]=0

    # ── VOTE 10: H4 Fib Confluence — weight 1.0 (Murphy) ────────────────────
    # Direction is NOT pre-decided here — Fib votes independently based on price vs level.
    # This prevents circular bias where direction determined at vote 9 always gets Fib confirmation.
    W10=1.0
    if h4_candles:
        h4_fib=calc_fib(h4_candles)
        fib_vote=calc_fib_proximity(s4.get("close",0) or 0, h4_fib)
        v["H4 Fib Level"]=fib_vote
        if fib_vote==1:    bull+=W10
        elif fib_vote==-1: bear+=W10
    else: v["H4 Fib Level"]=0

    # ── DIRECTION decided after ALL 10 votes are tallied ─────────────────────
    direction="LONG" if bull>=bear else "SHORT"

    # V1: ATR compression bonus for crypto — trends explode from low-volatility compression
    _atr_pct=s4.get("atrPct"); _atr_lbl=s4.get("atrLabel","")
    if _ptype=="crypto" and _atr_pct is not None:
        if _atr_pct<=25 and _atr_lbl=="expanding":
            bull+=0.5 if direction=="LONG" else 0; bear+=0.5 if direction=="SHORT" else 0
            w.append(f"ATR COMPRESSION BREAKOUT: ATR pct={_atr_pct} (25th) expanding — crypto volatility expanding from compression (+0.5)")
        elif _atr_pct>=75:
            w.append(f"ATR EXTENDED: ATR pct={_atr_pct} (75th+) — already extended, late entry risk")

    # V4: Range detection — mean-reversion at BB extremes when ranging (forex + crypto regime transitions)
    _entry_mode="trend"
    if (_ptype=="forex" and ranging_penalty>0) or (_ptype=="crypto" and _adx_mom in ("collapsing","exhausting")):
        _h4_bbp=None
        if s4["bbUpper"] is not None and s4["bbLower"] is not None:
            _bbr4=s4["bbUpper"]-s4["bbLower"]
            if _bbr4>0: _h4_bbp=(s4.get("close",0)-s4["bbLower"])/_bbr4
        if _h4_bbp is not None and r4 is not None:
            if (_h4_bbp<0.20 and r4<40) or (_h4_bbp>0.80 and r4>60):
                _entry_mode="mean_revert"
                ranging_penalty=max(0, ranging_penalty-1.0)  # reduce penalty for mean-reversion setups
                if _h4_bbp<0.20: direction="LONG"
                elif _h4_bbp>0.80: direction="SHORT"
                w.append(f"MEAN-REVERT: BB%={_h4_bbp:.2f}, ranging penalty reduced — fade to BB mid")

    # Volume context (non-forex only)
    if _ptype != "forex":
        if vr>=CONFIG["VOLUME_THRESHOLD"]: w.append(f"High volume ({vr:.1f}x) confirms move")
        elif max(bull,bear)>=5: w.append(f"Low volume ({vr:.1f}x avg) — confirm before entry")

    # ── INTERMARKET CONTEXT (BTC bias for alts) ──────────────────────────────
    if pair["type"]=="crypto" and pair["symbol"]!="BTCUSDT":
        if direction=="LONG"  and btc_bias=="bearish": w.append("BTC bearish — alt LONG is counter-trend risk")
        elif direction=="SHORT" and btc_bias=="bullish": w.append("BTC bullish — alt SHORT is counter-trend risk")

    # ── FINAL SCORE: weighted sum, apply ranging + session + counter-trend penalties
    raw_score = max(bull, bear)
    score = max(0.0, raw_score - ranging_penalty - _forex_session_pen)

    # F3: Per-class counter-trend penalty — crypto breaks D1 trends routinely from H4
    _ct_pen=abs(CONFIG["COUNTER_TREND_PEN"].get(_ptype, -3.0))
    if direction=="LONG" and hard_long_block:
        w.append(f"COUNTER-TREND: D1 bearish — Elder Triple Screen violation, -{_ct_pen} score")
        score = max(0.0, score - _ct_pen)
    if direction=="SHORT" and hard_short_block:
        w.append(f"COUNTER-TREND: D1 bullish — Elder Triple Screen violation, -{_ct_pen} score")
        score = max(0.0, score - _ct_pen)

    score = round(score, 1)

    # Trend state for Marcus Reid AI context
    if adx_val is not None:
        if adx_val>=35:   trend_state="TRENDING"
        elif adx_val>=25: trend_state="DEVELOPING"
        elif adx_val>=_rng["dead"]: trend_state="RANGING"
        else:             trend_state="DEAD RANGING"
    else: trend_state="UNKNOWN"

    atr_mults=CONFIG["ATR_CLASS"].get(pair["type"],{"sl":CONFIG["SL_ATR_MULT"]})
    if s1["atr"] and s1.get("close") and s1["atr"]*atr_mults["sl"]>s1["close"]*0.03:
        w.append("Wide SL > 3% of price — size down")

    spread = round(abs(bull - bear), 1)
    return {"score":score,"votes":v,"direction":direction,"bull":round(bull,1),"bear":round(bear,1),
            "spread":spread,"warnings":w,"trendState":trend_state,"weinsteinStage":weinstein_stage,"weinsteinLabel":weinstein_label,
            "entryMode":_entry_mode,"adxMomentum":_adx_mom,"adxSlope":_adx_slope}

def detect_div(d1c, h4c, h1c):
    """Detect H4 RSI divergence and H1 volume divergence. Returns list of warning strings."""
    w=[]
    try:
        h4=h4c[-20:]; cl=[c["close"] for c in h4]; rsi=calc_rsi(cl,14); pr=[c["high"] for c in h4]; n=len(pr)
        if n>=10 and rsi[-1] is not None:
            t=n//3; pm=max(pr[t:2*t]); rm=[x for x in rsi[t:2*t] if x is not None]
            if rm:
                if pr[-1]>pm and rsi[-1]<max(rm): w.append("H4 RSI Bearish Divergence")
                if pr[-1]<pm and rsi[-1]>max(rm): w.append("H4 RSI Bullish Divergence")
    except Exception as e: log.warning(f"detect_div H4: {e}")
    try:
        h1=h1c[-20:]; vols=[c["vol"] for c in h1]; pr=[c["close"] for c in h1]; n=len(pr)
        if n>=10:
            m=n//2
            if pr[-1]>pr[0] and vols[-1]<vols[m] and vols[-1]>0: w.append("H1 Vol Div - rising price, falling vol")
            if pr[-1]<pr[0] and vols[-1]<vols[m] and vols[-1]>0: w.append("H1 Vol Div - falling price, falling vol")
    except Exception as e: log.warning(f"detect_div H1: {e}")
    return w

def analyze_pair(pair, btc_bias):
    """Full analysis pipeline for one pair: fetch data, calc indicators, score confluence, compute levels."""
    d1=fetch_candles(pair,"D1",CONFIG["D1_CANDLES"])
    h4=fetch_candles(pair,"H4",CONFIG["H4_CANDLES"])
    h1=fetch_candles(pair,"H1",CONFIG["H1_CANDLES"])
    if not d1 or not h4 or not h1: return None
    if len(d1)<200 or len(h4)<50 or len(h1)<50: return None
    d1i,h4i,h1i=calc_indicators(d1),calc_indicators(h4),calc_indicators(h1)
    vols=[c["vol"] for c in h1]; vsma=calc_sma(vols,20)
    vr=vols[-1]/vsma[-1] if vsma[-1] and vsma[-1]>0 else 0
    stoch=calc_stochastic(h4,14,3,3)
    e200=calc_ema([c["close"] for c in d1],200)
    e200s=(e200[-1]-e200[-21])/e200[-21] if e200[-1] and len(e200)>=21 and e200[-21] else 0
    res=calc_confluence(d1i,h4i,h1i,vr,stoch,e200s,pair,btc_bias,d1_candles=d1,h4_candles=h4,h1_candles=h1)
    allw=res["warnings"]+detect_div(d1,h4,h1)
    price=h1[-1]["close"]; atr=h1i["snap"]["atr"]
    if not atr or atr==0: return None
    d=res["direction"]
    # C4: Use shared calc_levels helper (deduplicates SL/TP logic with backtest)
    lvl=calc_levels(price, atr, d, pair["type"])
    sl=lvl["sl"]; tp1=lvl["tp1"]; tp2=lvl["tp2"]; rr1=lvl["rr1"]; rr2=lvl["rr2"]
    sk=stoch["k"][-1] if stoch["k"] and stoch["k"][-1] is not None else None
    sd=stoch["d"][-1] if stoch["d"] and stoch["d"][-1] is not None else None
    risk_dollar=round(abs(price-sl)/price*100,2)
    max_score=13.0  # weighted system max: D1(2)+D1ADX(1.5)+Weinstein(1.5)+H4MACD(1.5)+H4RSI(1)+Stoch(1)+H1EMA(1)+BB(0.5)+Div(1)+Fib(1)

    return {"pair":pair["display"],"display":pair["display"],"symbol":pair["symbol"],"type":pair["type"],
        "direction":d,"confluenceScore":res["score"],"confluencePct":round(res["score"]/max_score*100),
        "spread":res["spread"],"entryMode":res.get("entryMode","trend"),
        "votes":res["votes"],"maxScore":max_score,"price":price,"sl":round(sl,6),"tp1":round(tp1,6),
        "tp2":round(tp2,6),"rr1":round(rr1,2),"rr2":round(rr2,2),"atr":round(atr,6),
        "slPips":round(abs(price-sl),6),"slPct":risk_dollar,"fib":calc_fib(h4),"d1":d1i,"h4":h4i,"h1":h1i,
        "volRatio":round(vr,2),"ema200Slope":round(e200s*100,3),
        "stochK":round(sk,1) if sk else None,"stochD":round(sd,1) if sd else None,
        "btcBias":btc_bias if pair["type"]=="crypto" else "n/a",
        "trendState":res["trendState"],"weinsteinStage":res["weinsteinStage"],"weinsteinLabel":res["weinsteinLabel"],
        "warnings":allw,"session":get_session(),
        "timestamp":datetime.now(timezone.utc).isoformat(),"aiAnalysis":None}

# B5: Correlation clusters — pairs in the same cluster share USD or sector exposure
# If 2+ signals fire from same cluster, 3rd gets a correlation warning (half-size)
CORR_CLUSTERS = {
    "metals":    ["XAU/USD","XAG/USD","GLD"],
    "defi":      ["SOL/USDT","AVAX/USDT","LINK/USDT","BNB/USDT","ETH/USDT","INJ/USDT","NEAR/USDT"],
    "ai_crypto": ["FET/USDT","RENDER/USDT","NEAR/USDT"],
    "forex_usd": ["EUR/USD","GBP/USD","AUD/USD","NZD/USD","USD/CHF","USD/CAD","USD/ZAR","USD/MXN","USD/SGD"],
    "forex_jpy": ["EUR/JPY","GBP/JPY","AUD/JPY"],
    "jse":       ["Naspers","Sasol","Std Bank","Anglo Am","MTN Group","Shoprite","Richemont","FirstRand","Absa","Capitec","Prosus","Gold Fields","AngloGold","Sibanye"],
    "us_tech":   ["AAPL","TSLA","NVDA","MSFT","AMZN","META","GOOG"],
    "us_sp500":  ["SPY","QQQ","S&P 500","Nasdaq"],
}

def _apply_correlation_cap(signals):
    """Tag signals with correlationWarning if cluster already has 2+ active signals."""
    cluster_counts = {}
    for sig in signals:
        pair_name = sig["pair"]
        for cluster, members in CORR_CLUSTERS.items():
            if pair_name in members:
                cluster_counts[cluster] = cluster_counts.get(cluster, 0) + 1
                if cluster_counts[cluster] >= 2:
                    sig.setdefault("warnings", [])
                    sig["warnings"].append(f"CORR CAP: {cluster} cluster already has {cluster_counts[cluster]-1} signal(s) — halve size to cap USD exposure")
                    sig["correlationWarning"] = cluster
    return signals

_scan_in_progress = False
_kill_switch = False  # N4: Kill-switch — blocks new scans/analyses when True

def run_full_scan():
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
    results = _apply_correlation_cap(results)
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

def run_ai(signal, news_ctx=None, style_pref="auto"):
    """Send signal data to Anthropic Claude for Marcus Reid AI analysis. Returns parsed JSON dict."""
    if not CONFIG.get("ANTHROPIC_KEY") or CONFIG["ANTHROPIC_KEY"]=="YOUR_ANTHROPIC_API_KEY":
        log.error("[AI] Anthropic API key is None or not configured!")
        return {"error":"Anthropic API key not configured"}
    try:
        log.info(f"[AI] Analyzing {signal['pair']}...")
        import anthropic
        c=anthropic.Anthropic(api_key=CONFIG["ANTHROPIC_KEY"])
        style_labels = {"scalp":"SCALP — focus on H1 exhaustion, tight 1.5R, quick execution","intraday":"INTRADAY — H4+H1 alignment, same-session execution, 2-3R","swing":"SWING — D1 trend dominance, EMA200 slope, 4-6R multi-day hold"}
        # Auto-style detection if not specified
        if style_pref == "auto":
            _sc = signal.get("confluenceScore", 0)
            style_pref = "swing" if _sc >= 9 else "intraday" if _sc >= 7 else "scalp"
        dxy_ctx=fetch_dxy_context()
        max_score=signal.get("maxScore",13.0)
        spread=signal.get("spread",0)
        conviction="HIGH" if spread>=3 else "MEDIUM" if spread>=1.5 else "LOW"
        pair_sqn=signal.get("pairSQN")
        msg=(f"Pair:{signal['pair']} Dir:{signal['direction']} Score:{signal['confluenceScore']}/{max_score} "
             f"Spread:{spread}({conviction} conviction) "
             f"{'PairSQN:'+str(pair_sqn)+' ' if pair_sqn else ''}"
             f"TrendState:{signal.get('trendState','?')} "
             f"ADXPct:{signal.get('h4',{}).get('snap',{}).get('adxPct','?')}th-pct({signal.get('h4',{}).get('snap',{}).get('adxLabel','?')}) "
             f"Weinstein:{signal.get('weinsteinLabel','n/a')} "
             f"Price:{signal['price']} SL:{signal['sl']}(SL%:{signal.get('slPct','?')}%) "
             f"TP1:{signal['tp1']}(R:{signal['rr1']}) TP2:{signal['tp2']}(R:{signal['rr2']}) "
             f"ATR:{signal['atr']} "
             f"Votes:{json.dumps(signal['votes'])} "
             f"Vol:{signal['volRatio']}x Stoch:{signal.get('stochK')}/{signal.get('stochD')} "
             f"EMA200slope:{signal['ema200Slope']}% "
             f"BTC:{signal.get('btcBias','n/a')} "
             f"Session:{signal['session']['name']}({signal['session']['quality']}) "
             f"Warnings:{json.dumps(signal['warnings'])} "
             f"Fib:{json.dumps(signal['fib'])} "
             f"ATRPct:{signal.get('h4',{}).get('snap',{}).get('atrPct','?')}({signal.get('h4',{}).get('snap',{}).get('atrLabel','?')}) "
             f"EntryMode:{signal.get('entryMode','trend')} "
             f"StylePref:{style_pref.upper()}")
        if dxy_ctx: msg += f" DXY:{dxy_ctx}"
        # Phase A: Yield curve context
        _yc = fetch_yield_curve()
        if _yc:
            msg += (f" YieldCurve:{{shape:{_yc['shape']},2y10y_spread:{_yc['spread_2_10']}%,"
                    f"3m:{_yc.get('y3m')}%,10y:{_yc['y10y']}%,context:{_yc['riskContext']}}}")
        # Phase B: Div/split warnings for stock pairs
        _ds = fetch_div_split_context()
        _pair_sym = signal.get("symbol", "")
        if _ds and _pair_sym in _ds:
            _ev = _ds[_pair_sym]
            if _ev.get("upcomingDiv"):
                _d = _ev["upcomingDiv"][0]
                msg += f" ExDivWarning:ex-div in {_d['daysTo']} days ({_d['exDate']}, amount:{_d.get('amount','?')}) — gap-down risk, reduce size"
            if _ev.get("upcomingSplit"):
                _s = _ev["upcomingSplit"][0]
                msg += f" SplitWarning:split in {_s['daysTo']} days ({_s['splitDate']}, ratio:{_s.get('ratio','?')}) — price distortion risk"
        # R6: Feed AI backtest performance context if available
        _bt = signal.get("backtestStats")
        if _bt:
            msg += (f" BT_SQN:{_bt.get('sqn','?')} BT_WR:{_bt.get('winRate','?')}%"
                    f" BT_Expect:{_bt.get('expectancy','?')}R BT_MaxDD:{_bt.get('maxDrawdownPct','?')}%")
            _rs = _bt.get("regimeStats", {})
            if _rs:
                msg += f" BT_RegimeWR:{json.dumps({k:v.get('wr') for k,v in _rs.items()})}"
        if style_pref:
            msg += f" StyleDetail:{style_labels.get(style_pref.lower(), style_pref.upper())}"
        if news_ctx:
            if news_ctx.get("forexEvents"): msg += f" HighImpactEvents:{json.dumps(news_ctx['forexEvents'])}"
            if news_ctx.get("marketNews"): msg += f" MarketNews:{json.dumps(news_ctx['marketNews'])}"
            if news_ctx.get("cryptoNews") and signal.get("type")=="crypto":
                pair_coins = [signal["symbol"].replace("USDT","").replace("USDC","")]
                relevant = [n for n in news_ctx["cryptoNews"] if not n["currencies"] or any(c in pair_coins for c in n["currencies"])]
                if relevant: msg += f" CryptoNews:{json.dumps(relevant[:3])}"
            _sent = news_ctx.get("pairSentiment", {})
            if _sent.get(signal.get("pair","")):
                _sc = _sent[signal["pair"]]
                _sl = "bullish" if _sc > 0.6 else "bearish" if _sc < 0.4 else "neutral"
                msg += f" NewsSentiment:{_sc}({_sl})"
            # Phase 3: Per-pair news headlines with article sentiment
            _pnews = news_ctx.get("pairNews", {}).get(signal.get("pair",""), [])
            if _pnews: msg += f" PairNews:{json.dumps(_pnews)}"
            # Phase 4: News word weights — top keywords driving this pair's news
            _ww = news_ctx.get("wordWeights", {}).get(signal.get("pair",""), [])
            if _ww: msg += f" NewsDrivers:{json.dumps(_ww)}"
        # Phase 5: Server-side indicators via EODHD filter=last_X
        _server_ind = signal.get("serverIndicators")
        if _server_ind: msg += f" ServerIndicators:{json.dumps(_server_ind)}"
        r=c.messages.create(model=CONFIG["ANTHROPIC_MODEL"],max_tokens=1500,system=EXPERT_PROMPT,messages=[{"role":"user","content":msg}])
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

if __name__=="__main__":
    log.info("="*60)
    log.info("ATHENA PRO v3.1 - Python Edition")
    log.info("="*60)
    active_fx=sum(1 for p in FOREX_PAIRS if p.get("enabled",True))
    active_cr=sum(1 for p in CRYPTO_PAIRS if p.get("enabled",True))
    log.info(f"Pairs: {len(ACTIVE_PAIRS)} active / {len(ALL_PAIRS)} total ({active_fx}fx {len(COMMODITY_PAIRS)}cmd {sum(1 for p in INDEX_PAIRS if p.get('enabled',True))}idx {sum(1 for p in JSE_PAIRS if p.get('enabled',True))}jse {active_cr}crypto)")
    log.info(f"Data: yfinance (free) + Binance (free)")
    log.info(f"Anthropic: {'SET' if CONFIG['ANTHROPIC_KEY']!='YOUR_ANTHROPIC_API_KEY' else 'NOT SET'}")
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
