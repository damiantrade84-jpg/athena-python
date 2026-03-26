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
import re
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

try:
    import anthropic as _anthropic_mod
except ImportError:
    _anthropic_mod = None

import telegram_notify

from datetime import datetime, timezone, timedelta

from flask import Flask, jsonify, request, send_from_directory
from athena_app.api.routes_scan import api_scan_impl
from athena_app.api.routes_backtest import api_backtest_impl
from athena_app.api.routes_execution import normalize_pip_mode
from athena_app.services.scan_backtest_service import (
    handle_scan_request,
    handle_backtest_request,
)
from athena_app.services.candle_service import recompute_levels_for_style
from athena_app.repositories.audit_repo import insert_manual_error

from data_feeds import (  # noqa: E402
    http_requests,
    _get_eodhd_client,
    _fetch_funding_rate,
    _fetch_open_interest,
    _calc_oi_divergence,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)

log = logging.getLogger("sentinel")

# Silence noisy HTTP and library loggers — reduces console flood
import logging as _logging
_logging.getLogger("werkzeug").setLevel(_logging.WARNING)
_logging.getLogger("urllib3").setLevel(_logging.WARNING)
_logging.getLogger("requests").setLevel(_logging.WARNING)
_logging.getLogger("httpx").setLevel(_logging.WARNING)

log.setLevel(logging.WARNING)

from candles_cache import (  # noqa: E402
    _candle_cache,
    _candle_cache_lock,
    extract_candles as _extract_candles,
    fetch_candles as _fetch_candles_routed,
    forex_h4_resample_offset_hours as _forex_h4_resample_offset_hours,
    merge_forex_forming_ws as _merge_forex_forming_ws_core,
    resample_from_h1 as _resample_from_h1,
)


_last_scan_results: dict = {"signals": []}  # latest run_full_scan output for chart-analysis context
_engine_b_cache: dict = {}  # sid/symbol -> naked analysis result dict


from candle_feeds import (  # noqa: E402
    BinanceCandleWS,
    BinanceLivePriceWS,
    CandleBuilder,
    EODHDWebSocketManager,
    _live_prices,
    _live_prices_lock,
    fetch_candles_live,
    get_candle_builder,
    set_candle_builder,
)


def _merge_forex_forming_ws(candles: list, display: str, tf: str, limit: int):
    return _merge_forex_forming_ws_core(
        candles,
        display,
        tf,
        limit,
        get_candle_builder=get_candle_builder,
    )



# N1: CONFIG loaded from config.py (YAML overrides + validation happen there)

from config import CONFIG, scan_candle_limits  # noqa: E402

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
        "source": "mt5",
        "enabled": True,
    },  # SQN +0.16 — enabled; score gate filters low-conviction setups
    {
        "symbol": "GBPUSD=X",
        "type": "forex",
        "display": "GBP/USD",
        "source": "polygon",  # TEST: Using Polygon native H4/D1 for Engine C comparison
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
        "source": "mt5",
        "enabled": True,
    },  # SQN 0.00 — enabled; zero trades was data gap not signal failure
    {
        "symbol": "EURGBP=X",
        "type": "forex",
        "display": "EUR/GBP",
        "source": "mt5",
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
        "source": "mt5",
        "enabled": True,
    },  # SQN +1.06
    {
        "symbol": "GBPJPY=X",
        "type": "forex",
        "display": "GBP/JPY",
        "source": "mt5",
        "enabled": True,
    },  # SQN -0.72
    {
        "symbol": "AUDJPY=X",
        "type": "forex",
        "display": "AUD/JPY",
        "source": "mt5",
        "enabled": True,
    },  # SQN +0.22
    {
        "symbol": "EURAUD=X",
        "type": "forex",
        "display": "EUR/AUD",
        "source": "mt5",
        "enabled": True,
    },  # SQN -1.43 (old look-ahead bias) — re-evaluate post-formula fix
    {
        "symbol": "GBPAUD=X",
        "type": "forex",
        "display": "GBP/AUD",
        "source": "mt5",
        "enabled": True,
    },  # SQN +0.91
    {
        "symbol": "USDZAR=X",
        "type": "forex",
        "display": "USD/ZAR",
        "source": "mt5",
        "enabled": True,
    },  # SQN -0.32
    {
        "symbol": "EURCHF=X",
        "type": "forex",
        "display": "EUR/CHF",
        "source": "mt5",
        "enabled": True,
    },  # SQN -0.83
    {
        "symbol": "USDMXN=X",
        "type": "forex",
        "display": "USD/MXN",
        "source": "mt5",
        "enabled": True,
    },  # SQN +0.85, OOS +0.60 ✓
    {
        "symbol": "USDSGD=X",
        "type": "forex",
        "display": "USD/SGD",
        "source": "mt5",
        "enabled": True,
    },  # SQN +0.43
]

COMMODITY_PAIRS = [
    {
        "symbol": "GC=F",
        "type": "commodity",
        "display": "XAU/USD",
        "source": "mt5",
        "enabled": True,
    },  # SQN +1.92, WR 64.7%, 17 trades (2026-03-15 ATR-fix confirmed)
    {
        "symbol": "SI=F",
        "type": "commodity",
        "display": "XAG/USD",
        "source": "mt5",
        "enabled": True,
    },  # SQN -0.07 — DISABLE (borderline, no edge confirmed)
    {
        "symbol": "CL=F",
        "type": "commodity",
        "display": "WTI Oil",
        "source": "mt5",
        "enabled": True,
    },  # SQN not retested with correct ATR — monitor only
    {
        "symbol": "BZ=F",
        "type": "commodity",
        "display": "Brent Oil",
        "source": "mt5",
        "enabled": True,
    },  # Pepperstone: SPOTBRENT
    {
        "symbol": "NG.US",
        "type": "commodity",
        "display": "Nat Gas",
        "source": "mt5",
        "enabled": True,
    },  # EODHD: NG.US; Pepperstone: NATGAS
    {
        "symbol": "HG=F",
        "type": "commodity",
        "display": "Copper",
        "source": "mt5",
        "enabled": True,
    },  # Pepperstone: COPPER - WebSocket enabled
    {
        "symbol": "PL=F",
        "type": "commodity",
        "display": "XPT/USD",
        "source": "mt5",
        "enabled": True,
    },  # Pepperstone: XPTUSD
    {
        "symbol": "PA=F",
        "type": "commodity",
        "display": "XPD/USD",
        "source": "mt5",
        "enabled": True,
    },  # Pepperstone: XPDUSD
]

INDEX_PAIRS = [
    {
        "symbol": "^IXIC",
        "type": "index",
        "display": "NASDAQ-100",
        "source": "mt5",
        "enabled": True,
        "ws": False,
    },  # NAS100 index via EODHD REST
    {
        "symbol": "^GSPC",
        "type": "index",
        "display": "S&P 500",
        "source": "mt5",
        "enabled": True,
    },  # SQN +1.23 WR 60.0% (10T) ← WS: us endpoint GSPC.INDX
    {
        "symbol": "^DJI",
        "type": "index",
        "display": "Dow Jones",
        "source": "mt5",
        "enabled": True,
    },  # SQN +0.61 WR 55.6% (9T) ← WS: us endpoint DJI.INDX
    {
        "symbol": "^GDAXI",
        "type": "index",
        "display": "DAX 40",
        "source": "mt5",
        "enabled": True,
        "ws": False,
    },  # SQN -1.89 WR 23.1% (13T) Pepperstone: GER40
    {
        "symbol": "^FTSE",
        "type": "index",
        "display": "UK100",
        "source": "mt5",
        "enabled": True,
    },  # SQN +0.67 WR 50.0% (12T) Pepperstone: UK100 ← WS: forex ep
    {
        "symbol": "^AXJO",
        "type": "index",
        "display": "ASX 200",
        "source": "mt5",
        "enabled": True,
    },  # SQN +0.12 WR 40.0% (10T) Pepperstone: AUS200 ← WS: forex ep
    {
        "symbol": "^N225",
        "type": "index",
        "display": "Nikkei 225",
        "source": "mt5",
        "enabled": True,
    },  # SQN -0.39 WR 33.3% (9T) Pepperstone: JPN225 ← WS: forex ep
    {
        "symbol": "^HSI",
        "type": "index",
        "display": "Hang Seng",
        "source": "mt5",
        "enabled": True,
    },  # SQN -0.38 WR 33.3% (9T) Pepperstone: HK50 ← WS: forex ep
]

US_STOCK_PAIRS = [
    {
        "symbol": "AAPL.US",
        "type": "stock",
        "display": "AAPL",
        "source": "mt5",
        "enabled": True,
    },  # SQN -0.30 — score gate filters
    {
        "symbol": "TSLA.US",
        "type": "stock",
        "display": "TSLA",
        "source": "mt5",
        "enabled": True,
    },  # SQN +0.10
    {
        "symbol": "NVDA.US",
        "type": "stock",
        "display": "NVDA",
        "source": "mt5",
        "enabled": True,
    },  # SQN +1.44 ✓
    {
        "symbol": "MSFT.US",
        "type": "stock",
        "display": "MSFT",
        "source": "mt5",
        "enabled": True,
    },  # SQN +0.49
    {
        "symbol": "AMZN.US",
        "type": "stock",
        "display": "AMZN",
        "source": "mt5",
        "enabled": True,
        "ws": False,
    },  # SQN +0.27
    {
        "symbol": "META.US",
        "type": "stock",
        "display": "META",
        "source": "mt5",
        "enabled": True,
    },  # SQN -0.29
    {
        "symbol": "GOOG.US",
        "type": "stock",
        "display": "GOOG",
        "source": "mt5",
        "enabled": True,
    },  # SQN +1.61, OOS:+1.01 ✓
    {
        "symbol": "JPM.US",
        "type": "stock",
        "display": "JPM",
        "source": "mt5",
        "enabled": True,
        "ws": False,
    },  # SQN +0.26
    {
        "symbol": "V.US",
        "type": "stock",
        "display": "V",
        "source": "mt5",
        "enabled": True,
        "ws": False,
    },  # SQN -1.39
    {
        "symbol": "XOM.US",
        "type": "stock",
        "display": "XOM",
        "source": "mt5",
        "enabled": True,
        "ws": False,
    },  # SQN -0.03
    {
        "symbol": "NFLX.US",
        "type": "stock",
        "display": "NFLX",
        "source": "mt5",
        "enabled": True,
    },  # Pepperstone CFD
    {
        "symbol": "AMD.US",
        "type": "stock",
        "display": "AMD",
        "source": "mt5",
        "enabled": True,
    },  # Pepperstone CFD
    {
        "symbol": "CRM.US",
        "type": "stock",
        "display": "CRM",
        "source": "mt5",
        "enabled": True,
        "ws": False,
    },  # Pepperstone CFD
    {
        "symbol": "DIS.US",
        "type": "stock",
        "display": "DIS",
        "source": "mt5",
        "enabled": True,
        "ws": False,
    },  # Pepperstone CFD
    {
        "symbol": "BA.US",
        "type": "stock",
        "display": "BA",
        "source": "mt5",
        "enabled": True,
        "ws": False,
    },  # Pepperstone CFD
    {
        "symbol": "COIN.US",
        "type": "stock",
        "display": "COIN",
        "source": "mt5",
        "enabled": True,
    },  # Pepperstone CFD
    {
        "symbol": "PYPL.US",
        "type": "stock",
        "display": "PYPL",
        "source": "mt5",
        "enabled": True,
    },  # Pepperstone CFD
    {
        "symbol": "INTC.US",
        "type": "stock",
        "display": "INTC",
        "source": "mt5",
        "enabled": True,
    },  # Pepperstone CFD
    {
        "symbol": "UBER.US",
        "type": "stock",
        "display": "UBER",
        "source": "mt5",
        "enabled": True,
    },  # Pepperstone CFD
    {
        "symbol": "PLTR.US",
        "type": "stock",
        "display": "PLTR",
        "source": "mt5",
        "enabled": True,
    },  # Pepperstone CFD
]

ETF_PAIRS = [
    {
        "symbol": "SPY.US",
        "type": "stock",
        "display": "SPY",
        "source": "mt5",
        "enabled": True,
    },  # SQN +1.03 ✓
    {
        "symbol": "QQQ.US",
        "type": "stock",
        "display": "QQQ",
        "source": "mt5",
        "enabled": True,
        "ws": False,
    },  # SQN +0.38
    {
        "symbol": "GLD.US",
        "type": "stock",
        "display": "GLD",
        "source": "mt5",
        "enabled": True,
        "ws": False,
    },  # SQN +2.08, OOS:+2.98 ✓
    {
        "symbol": "TLT.US",
        "type": "stock",
        "display": "TLT",
        "source": "mt5",
        "enabled": True,
        "ws": False,
    },  # Treasury ETF
    {
        "symbol": "IWM.US",
        "type": "stock",
        "display": "IWM",
        "source": "mt5",
        "enabled": True,
    },  # Russell 2000 ETF
    {
        "symbol": "EEM.US",
        "type": "stock",
        "display": "EEM",
        "source": "mt5",
        "enabled": True,
    },  # Emerging Markets ETF
    {
        "symbol": "XLE.US",
        "type": "stock",
        "display": "XLE",
        "source": "mt5",
        "enabled": True,
        "ws": False,
    },  # Energy Sector ETF
    {
        "symbol": "SLV.US",
        "type": "stock",
        "display": "SLV",
        "source": "mt5",
        "enabled": True,
    },  # Silver ETF
    {
        "symbol": "USO.US",
        "type": "stock",
        "display": "USO",
        "source": "mt5",
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

        r = http_requests.get(url, params=params, timeout=60)

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


_eodhd_cooldown_until = 0.0  # global cooldown timestamp for 402 errors

def fetch_eodhd(pair, tf, limit):
    """Download OHLCV candles via EODHD SDK (APIClient). Covers forex, stocks, indices â€“ 1000 req/min.

    Returns dict with standardized error format."""
    global _eodhd_cooldown_until

    symbol = pair.get("display", pair.get("symbol", "unknown"))

    # Fast-fail if EODHD recently returned 402 (Payment Required / rate limit)
    if time.time() < _eodhd_cooldown_until:
        fallback = _fetch_fallback_candles(pair, tf, limit, reason="EODHD cooldown (402)")
        if fallback:
            return {"error": True, "symbol": symbol, "detail": "EODHD cooldown", "candles": fallback}
        return {"error": True, "symbol": symbol, "detail": "EODHD cooldown"}

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

            # Retry D1 SDK call with backoff (skip retries on 402/429)
            bars = None
            for attempt in range(1, 4):
                try:
                    bars = api.get_eod_historical_stock_market_data(
                        ticker, period="d", from_date=start, order="a"
                    )
                    break
                except Exception as e:
                    if "402" in str(e) or "429" in str(e) or "Payment Required" in str(e):
                        log.warning(f"[EODHD] {ticker} D1: 402/429 — cooldown 10 min")
                        _eodhd_cooldown_until = time.time() + 600
                        raise
                    if attempt == 3:
                        log.warning(f"[EODHD] {ticker} D1 failed after 3 attempts: {e}")
                        raise
                    backoff = 1.5 * attempt
                    log.warning(f"[EODHD] {ticker} D1 attempt {attempt} failed, retry in {backoff}s: {e}")
                    time.sleep(backoff)

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

            # Retry intraday SDK call with backoff (skip retries on 402/429)
            bars = None
            for attempt in range(1, 4):
                try:
                    bars = api.get_intraday_historical_data(
                        ticker, interval="1h", from_unix_time=start_ts
                    )
                    break
                except Exception as e:
                    if "402" in str(e) or "429" in str(e) or "Payment Required" in str(e):
                        log.warning(f"[EODHD] {ticker} intraday: 402/429 — cooldown 10 min")
                        _eodhd_cooldown_until = time.time() + 600
                        raise
                    if attempt == 3:
                        log.warning(f"[EODHD] {ticker} intraday failed after 3 attempts: {e}")
                        raise
                    backoff = 1.5 * attempt
                    log.warning(f"[EODHD] {ticker} intraday attempt {attempt} failed, retry in {backoff}s: {e}")
                    time.sleep(backoff)

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
                _resampled = _resample_from_h1(
                    candles,
                    "H4",
                    len(candles),
                    alignment_offset_hours=(
                        _forex_h4_resample_offset_hours()
                        if pair.get("type") == "forex"
                        else 0.0
                    ),
                )
                if _resampled:
                    candles = _resampled
                else:
                    log.warning(f"[EODHD] {ticker} _resample_from_h1 failed, returning raw H1 candles")

        final_candles = candles[-limit:] if len(candles) > limit else candles

        return {
            "error": False,
            "symbol": symbol,
            "detail": "",
            "candles": final_candles,
        }

    except Exception as e:
        log.error(f"[EODHD] {pair['display']}: {e}")
        fallback = _fetch_fallback_candles(pair, tf, limit, reason=f"EODHD error: {e}")
        if fallback:
            return {"error": True, "symbol": symbol, "detail": str(e), "candles": fallback}
        return {"error": True, "symbol": symbol, "detail": str(e)}


_polygon_lock = threading.Lock()
_polygon_last_request: float = 0.0  # epoch seconds of last Polygon HTTP request
_POLYGON_MIN_INTERVAL = 12.0  # 5 req/min free tier → 1 request per 12 s


def fetch_polygon(pair, tf, limit):
    """Download OHLCV candles from Polygon.io REST API. Best forex data quality.

    Returns dict with standardized error format."""

    symbol = pair.get("display", pair.get("symbol", "unknown"))
    
    # DEBUG: Log Polygon data fetch for comparison test
    log.info(f"[PG] FETCH {symbol} {tf} limit={limit}")

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

            # Reduce start range for intraday to prevent pagination from truncating recent data
            if tf == "H1":
                start = end - timedelta(days=60)
            elif tf == "H4":
                start = end - timedelta(days=120)
            else:
                start = end - timedelta(days=730)

            url = f"https://api.polygon.io/v2/aggs/ticker/{ticker}/range/{mult}/{span}/{start.strftime('%Y-%m-%d')}/{end.strftime('%Y-%m-%d')}"

            # Fetch descending to ensure we get the most recent bars if constrained, then reverse later
            r = http_requests.get(
                url, params={"apiKey": key, "limit": 50000, "sort": "desc"}, timeout=20
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

                # Log Polygon rate limit error (no Telegram notification)
                log.warning(
                    f"[POLYGON] Rate limited (429) - backing off requests"
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

            # Reverse the results because we fetched with sort=desc to guarantee the latest data
            results = list(reversed(results))
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
            
            # DEBUG: Log Polygon data quality for comparison
            if candles:
                last_bar = candles[-1]
                first_bar = candles[0]
                log.info(f"[PG] {symbol} {tf}: {len(candles)} bars, first={first_bar['time']}, last={last_bar['time']}")
                if tf in ("H4", "D1"):
                    # Check if timestamps align with proper boundaries
                    last_time = datetime.fromisoformat(last_bar['time'].replace('Z', '+00:00'))
                    if tf == "H4":
                        hour_ok = last_time.hour % 4 == 0 and last_time.minute == 0
                        log.info(f"[PG] {symbol} {tf}: H4 boundary check - hour={last_time.hour}, aligned={hour_ok}")
                    elif tf == "D1":
                        day_ok = last_time.hour == 0 and last_time.minute == 0
                        log.info(f"[PG] {symbol} {tf}: D1 boundary check - hour={last_time.hour}, aligned={day_ok}")

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

def fetch_mt5(pair: dict, tf: str, limit: int):
    """Download OHLCV candles directly from the live MT5 broker terminal. Fast, accurate, real-time."""
    import mt5_executor
    import time
    from datetime import datetime, timezone
    
    symbol = pair.get("display", pair.get("symbol", ""))
    
    if not mt5_executor.mt5_connect():
        return {"error": True, "symbol": symbol, "detail": "MT5 not connected"}
        
    mt5 = mt5_executor._get_mt5()
    mt5_symbol = mt5_executor.mt5_map_symbol(symbol)
    
    if not mt5_symbol:
        return {"error": True, "symbol": symbol, "detail": "no MT5 symbol mapping"}
        
    if not mt5.symbol_select(mt5_symbol, True):
        return {"error": True, "symbol": symbol, "detail": "symbol not found in MT5"}
        
    # Map timeframe
    tf_map = {
        "M1": mt5.TIMEFRAME_M1,
        "M5": mt5.TIMEFRAME_M5,
        "M15": mt5.TIMEFRAME_M15,
        "M30": mt5.TIMEFRAME_M30,
        "H1": mt5.TIMEFRAME_H1,
        "H4": mt5.TIMEFRAME_H4,
        "D1": mt5.TIMEFRAME_D1,
    }
    mt5_tf = tf_map.get(tf, mt5.TIMEFRAME_H1)
    
    request_limit = limit + 100
    
    bars = mt5.copy_rates_from_pos(mt5_symbol, mt5_tf, 0, request_limit)
    
    if bars is None or len(bars) == 0:
        err = mt5.last_error()
        return {"error": True, "symbol": symbol, "detail": f"MT5 failed: {err}"}
        
    # Dynamic timezone detection to align broker integers to perfect UTC strings
    tick = mt5.symbol_info_tick(mt5_symbol)
    offset_seconds = 0
    if tick and tick.time > 0:
        utc_now = time.time()
        diff_sec = tick.time - utc_now
        offset_hours = round(diff_sec / 3600.0)
        offset_seconds = int(offset_hours * 3600)
    
    candles = []
    for b in bars:
        shifted_ts = b['time'] - offset_seconds
        dt = datetime.fromtimestamp(shifted_ts, tz=timezone.utc)
        candles.append({
            "time": dt.isoformat(),
            "open": float(b['open']),
            "high": float(b['high']),
            "low": float(b['low']),
            "close": float(b['close']),
            "vol": float(b['tick_volume'])
        })
    # Bridge to global _live_prices cache for execution levels
    if candles:
        last = candles[-1]
        with _live_prices_lock:
            _live_prices[symbol] = {
                "price": float(last["close"]),
                "ts": time.time(),
                "source": "mt5"
            }
        
    return {
        "error": False,
        "symbol": symbol,
        "detail": "",
        "candles": candles[-limit:] if len(candles) > limit else candles,
    }

def fetch_candles(pair: dict, tf: str, limit: int) -> list | None:
    """Route candle fetch to correct source with in-memory TTL cache (see candles_cache)."""
    return _fetch_candles_routed(
        pair,
        tf,
        limit,
        fetch_candles_live=fetch_candles_live,
        fetch_binance=fetch_binance,
        fetch_eodhd=fetch_eodhd,
        fetch_polygon=fetch_polygon,
        fetch_yfinance=fetch_yfinance,
        fetch_mt5=fetch_mt5,
        yfinance_symbol_for_pair=_yfinance_symbol_for_pair,
        tf_b=TF_B,
    )


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
    get_pair_score_group,
    get_min_confluence_threshold,
    pair_filter_enabled,
    _pair_exchange_closed,
    _build_event_risk,
    _classify_signal,
)

from config import _json_safe  # noqa: E402
from engine_c import compute_consensus, apply_vision  # noqa: E402
from athena_runtime import executed_signals  # noqa: E402


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

    if requested_style != "auto":
        return requested_style
    ptype = pair.get("type", "")
    if ptype == "crypto":
        return "intraday"
    elif ptype == "forex":
        return "intraday"
    else:
        return "swing"  # stocks, commodities, indices, ETFs, JSE


def _effective_backtest_style(pair: dict, requested_style: str) -> str:
    """Resolve backtest iteration style per pair."""

    if requested_style != "auto":
        return requested_style
    ptype = pair.get("type", "")
    if ptype == "crypto":
        return "intraday"
    elif ptype == "forex":
        return "swing"
    else:
        return "swing"


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



edgeProbability: Estimate a realistic win probability from 20-95 based on score%, regime quality, structural alignment, and risk factors. Use score% as an anchor, not a fixed formula.



riskLevel: "Low" if edgeProb>=70 and TRENDING. "High" if edgeProb<40 or DEAD RANGING or counter-trend. "Medium" otherwise.



PER-STYLE RATINGS:

Rate this signal for ALL THREE trade styles independently based on the data provided:
- SCALP (hold minutes, H1 ATR, tight SL/TP, 1.5-2R)
- INTRADAY (hold hours, H4 ATR, moderate SL/TP, 2-3R)
- SWING (hold days, D1 ATR, wide SL/TP, 3-6R)
Include in "style_ratings". The top-level grade/edgeProbability/riskLevel should reflect whichever style you rate highest. Set "tradeStyle" to that best style.



OUTPUT — EXACT JSON ONLY:

{"grade":"A","verdict":"One punchy sentence — be yourself, not a robot","narrative":"2-3 sentences referencing specific votes and data. Show personality.","entryZone":"exact price/fib","invalidation":"exact price","keyLevels":"S1/R1","positionSizing":"Full/Half/Quarter + R explanation","tradeStyle":"SWING|INTRADAY|SCALP","tradeStyleReason":"why","warnings":["specific risks"],"edgeProbability":68,"riskLevel":"Medium","style_ratings":{"scalp":{"grade":"B","edgeProbability":52,"riskLevel":"High"},"intraday":{"grade":"A","edgeProbability":68,"riskLevel":"Medium"},"swing":{"grade":"A+","edgeProbability":78,"riskLevel":"Low"}}}



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
    """Build sectioned signal string sent to Marcus Reid (xAI Grok) for analysis.



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
    from ai_utils import parse_json_object

    _ = pair  # kept for backward-compatible signature and logging call sites
    return parse_json_object(text)


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
        _temp = float(CONFIG.get("AI_TEMPERATURE", 0.3))

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
                    max_tokens=1100,
                    temperature=_temp,
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
                max_output_tokens=1100,
                temperature=_temp,
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

        log.warning(
            f"[AI] {signal['pair']} => Grade:{result.get('grade', '?')} Prob:{result.get('edgeProbability', '?')}% Risk:{result.get('riskLevel', '?')} | {str(result.get('verdict', ''))[:60]}"
        )

        return result

    except Exception as e:
        log.error(f"[AI] ERROR for {signal.get('pair', '?')}: {e}")

        return {"error": str(e)}



from backtest_runner import backtest_pair, backtest_pair_naked, run_full_backtest  # noqa: E402


def _init_audit_db(db_path: str) -> None:
    """Create audit table if it doesn't exist, and migrate legacy schemas."""

    con = sqlite3.connect(db_path, timeout=15.0)
    con.execute("PRAGMA journal_mode=WAL")

    con.execute("""

        CREATE TABLE IF NOT EXISTS audit_log (

            id                    INTEGER PRIMARY KEY AUTOINCREMENT,

            ts                    TEXT NOT NULL,

            pair                  TEXT,

            score                 REAL,

            direction             TEXT,

            trend                 TEXT,

            grade                 TEXT,

            edge_prob             REAL,

            risk                  TEXT,

            style                 TEXT,

            entry_price           REAL,

            sl                    REAL,

            tp                    REAL,

            volume                REAL,

            regime                TEXT,

            risk_amount           REAL,

            risk_pct              REAL,

            ticket                TEXT,

            exit_price            REAL,

            exit_time             TEXT,

            pnl                   REAL,

            r_multiple            REAL,

            exit_reason           TEXT,

            holding_period_hours  REAL,

            asset_class           TEXT,

            score_pct             REAL,

            max_score             REAL,

            votes_json            TEXT,

            warnings_json         TEXT,

            weinstein             TEXT,

            trend_state           TEXT,

            adx_pct               REAL,

            btc_bias              TEXT,

            session_name          TEXT,

            error_tag             TEXT,

            fee_cost              REAL,

            factors_json          TEXT

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
        ("signal_price_ref", "REAL"),  # Scan/signal price at order time (vs entry_price fill)
        ("slippage_bps", "REAL"),  # Adverse slippage in basis points (sign = bad for trader)
    ]:
        if col not in existing:
            con.execute(f"ALTER TABLE audit_log ADD COLUMN {col} {defn}")

    con.execute(
        """
        CREATE TABLE IF NOT EXISTS shadow_signals (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            ts            TEXT NOT NULL,
            pair          TEXT,
            asset_type    TEXT,
            direction     TEXT,
            signal_price  REAL,
            sl            REAL,
            tp            REAL,
            rr            REAL,
            conviction    REAL,
            verdict       TEXT,
            tier          TEXT,
            source        TEXT,
            payload_json  TEXT
        )
        """
    )
    con.execute("CREATE INDEX IF NOT EXISTS idx_shadow_ts ON shadow_signals (ts)")

    con.commit()

    con.close()


_AUDIT_DB = os.path.join(os.path.dirname(os.path.abspath(__file__)), "audit.db")

_init_audit_db(_AUDIT_DB)


def _insert_shadow_from_engine_c(consensus: dict) -> None:
    """Append-only log of Engine C ALIGNED+tradeable rows (no broker)."""
    if not isinstance(consensus, dict) or not CONFIG.get("SHADOW_LEDGER_ENABLED"):
        return
    if consensus.get("verdict") != "ALIGNED" or not consensus.get("trade"):
        return
    pair = consensus.get("display") or consensus.get("symbol") or ""
    try:
        slim = {
            "engine_weights": consensus.get("engine_weights"),
            "components": consensus.get("components"),
            "sl_method": consensus.get("sl_method"),
            "tp_method": consensus.get("tp_method"),
        }
        with sqlite3.connect(_AUDIT_DB, timeout=15.0) as con:
            con.execute(
                """INSERT INTO shadow_signals
                   (ts,pair,asset_type,direction,signal_price,sl,tp,rr,conviction,verdict,tier,source,payload_json)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    datetime.now(timezone.utc).isoformat(),
                    pair,
                    consensus.get("type"),
                    consensus.get("direction"),
                    consensus.get("entry"),
                    consensus.get("sl"),
                    consensus.get("tp"),
                    consensus.get("rr"),
                    consensus.get("conviction"),
                    consensus.get("verdict"),
                    consensus.get("tier"),
                    "ENGINE_C_SCAN",
                    json.dumps(_json_safe(slim)),
                ),
            )
            con.commit()
    except Exception as exc:
        log.debug(f"[SHADOW] insert failed: {exc}")


# ── AI Learning + Auto-Trader ─────────────────────────────────────────────

from ai_learning import init_learning_db  # noqa: E402

init_learning_db(_AUDIT_DB)


from scanner import run_full_scan  # noqa: E402

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
_ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")  # set in .env for Claude Vision

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
    result = api_scan_impl(
        d,
        scan_service_handle=lambda payload: handle_scan_request(
            payload, run_full_scan=run_full_scan
        ),
    )
    global _last_scan_results
    _last_scan_results = result
    return jsonify(_json_safe(result))


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
                    "scores": sig.get("factor_scores"),
                    "weights": sig.get("factor_weights"),
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


def _naked_scan_style_profile(
    style: str | None, score_group: str | None = None
) -> tuple[str, dict]:
    resolved = _normalize_style(style)
    if resolved == "auto":
        resolved = "intraday"  # Engine B walks H4 bars — intraday is the natural default
    profiles = {
        "scalp": {
            "min_score": 0.9,
            "min_room_atr": 0.35,
            "min_rr": 1.0,
            "fallback_rr": 1.4,
            "require_macro_align": False,
            "zone_tf": "H4",
            "entry_tf": "H1",
            "atr_tf": "H4",
        },
        "intraday": {
            "min_score": 1.5,
            "min_room_atr": 0.7,
            "min_rr": 1.2,
            "fallback_rr": 1.8,
            "require_macro_align": False,
            "zone_tf": "H4",
            "entry_tf": "H1",
            "atr_tf": "H4",
        },
        "swing": {
            "min_score": 1.8,
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
    resolved_profile = merged_profiles.get(resolved, merged_profiles["scalp"])

    # Optional subgroup-level profile overrides for Engine B strictness.
    if score_group:
        group_overrides = (
            ((CONFIG.get("NAKED_ENGINE", {}) or {}).get("score_group_overrides", {}) or {})
            .get(score_group, {})
        )
        if isinstance(group_overrides, dict):
            style_override = group_overrides.get(resolved, {})
            if isinstance(style_override, dict):
                resolved_profile = {**resolved_profile, **style_override}

    return resolved, resolved_profile


def _engine_b_regime_label(
    h4_candles: list,
    pair_type: str = "stock",
    regime_hint: dict | str | None = None,
) -> str:
    """Resolve a shared Engine B regime label across live, analysis, and backtest.

    Forex Engine A uses ``signal_type`` as ``regime.label`` (e.g. TREND_PULLBACK), which is not a
    key in ``NAKED_ENGINE.zone_multipliers``. Map those to TRENDING/RANGING-style labels; NONE/UNKNOWN
    falls through to H4 ``detect_regime``.
    """
    _std_regimes = frozenset(
        {"TRENDING", "RANGING", "HIGH_VOLATILITY", "LOW_VOLATILITY"}
    )
    _forex_signal_to_zone_regime = {
        "TREND_PULLBACK": "TRENDING",
        "LONDON_BREAKOUT": "TRENDING",
    }

    raw = None
    if isinstance(regime_hint, dict):
        raw = regime_hint.get("label") or regime_hint.get("regime")
    elif regime_hint:
        raw = regime_hint

    if raw:
        h = str(raw).upper()
        if h in ("NONE", "UNKNOWN", ""):
            pass
        elif (pair_type or "").lower() == "forex":
            if h in _forex_signal_to_zone_regime:
                return _forex_signal_to_zone_regime[h]
            if h in _std_regimes:
                return h
        else:
            return h

    if not h4_candles or len(h4_candles) < 20:
        return "RANGING"

    try:
        from regime import detect_regime

        h4i = calc_indicators(h4_candles)
        h4_snap = h4i.get("snap", {}) if isinstance(h4i, dict) else {}
        regime = detect_regime(h4_snap, pair_type or "stock", bb_width_pct=None)
        return str(regime.get("label", "RANGING")).upper()
    except Exception:
        return "RANGING"


def _compute_naked_analysis(sig: dict, engine_a_ctx: dict = None, force_ai: bool = False):
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
        _clim = scan_candle_limits()
        d1 = fetch_candles(pair_obj, "D1", _clim["D1"])
        h4 = fetch_candles(pair_obj, "H4", _clim["H4"])
        h1 = fetch_candles(pair_obj, "H1", _clim["H1"])

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
        regime_label = _engine_b_regime_label(
            h4,
            pair_obj.get("type", "stock"),
            engine_a_ctx.get("regime") if isinstance(engine_a_ctx, dict) else None,
        )
        res = engine.analyze_structure(
            d1, h4, h1, current_price, direction, atr, regime_label, asset_type=pair_obj.get("type", "")
        )

        try:
            from ai_learning import get_ai_learning_context

            learning_ctx = get_ai_learning_context(
                pair_obj.get("display"), pair_obj.get("type", "crypto"), _AUDIT_DB
            )
        except Exception as e:
            log.warning(f"Failed to fetch AI learning context for Naked Analysis: {e}")
            learning_ctx = None

        _pair_score_group = get_pair_score_group(pair_obj)
        resolved_style, style_profile = _naked_scan_style_profile(
            sig.get("style", "auto"), score_group=_pair_score_group
        )
        _pair_type = pair_obj.get("type", "")
        _forex_struct_tf = CONFIG.get("ENGINE_B_FOREX_STRUCTURE_TF", "D1").upper()
        if _pair_type == "forex" and _forex_struct_tf == "D1" and resolved_style == "intraday":
            resolved_style, style_profile = _naked_scan_style_profile(
                "swing", score_group=_pair_score_group
            )
        # Determine correct entry candles based on forex structure timeframe
        _entry_candles = h4 if (_pair_type == "forex" and _forex_struct_tf == "D1") else h1
        conf = engine.calculate_confidence(
            res,
            current_price,
            direction,
            learning_ctx,
            entry_candles=_entry_candles,
            style_profile=style_profile,
        )
        _regime_gate = {
            "TRENDING": 0.85,
            "RANGING": 1.2,
            "HIGH_VOLATILITY": 1.3,
            "LOW_VOLATILITY": 1.0,
        }
        _min_score_scaled = style_profile["min_score"] * _regime_gate.get(regime_label, 1.0)
        res["min_score_used"] = round(_min_score_scaled, 2)
        res["regime_gate"] = _regime_gate.get(regime_label, 1.0)
        res.update(conf)
        res["current_price"] = current_price
        res["direction"] = direction
        res["style"] = resolved_style
        res["regime"] = regime_label

        # AI execution control
        _run_ai = force_ai or not CONFIG.get("AI_ON_DEMAND_ONLY", True)
        if _run_ai:
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
                    xai_model=CONFIG.get("XAI_MODEL", "grok-4.20-0309-reasoning"),
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
        else:
            log.debug(f"[NAKED] {pair_obj.get('display')}: AI skipped (AI_ON_DEMAND_ONLY=true, force_ai=false)")
            res["ai_analysis"] = None

        return res, pair_obj, None
    except Exception as e:
        log.error(f"naked_analysis error: {e}")
        return None, pair_obj, "Naked Structure Analysis failed"


@app.route("/api/naked-analysis", methods=["POST"])
def api_naked_analysis():
    d = request.json
    if not d or "signal" not in d:
        return jsonify({"error": "Invalid payload"}), 400

    res, _pair_obj, err = _compute_naked_analysis(d["signal"], force_ai=True)
    if err:
        return jsonify({"error": err}), 500
    # Cache for chart-analysis context lookup
    sig = d["signal"]
    _sym = sig.get("symbol") or sig.get("display") or ""
    if _sym and res:
        _sid = _sym.replace("/", "_").replace("=", "_").replace("^", "_").replace(".", "_")
        _engine_b_cache[_sid] = res
        _engine_b_cache[_sym] = res
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
    _pair_score_group = get_pair_score_group(pair_obj)
    engine_b_style, engine_b_profile = _naked_scan_style_profile(
        requested_style, score_group=_pair_score_group
    )

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
            engine_b_seed, engine_a_ctx=engine_a, force_ai=True
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


@app.route("/api/scan-naked", methods=["POST"])
def api_scan_naked():
    d = request.json or {}
    asset_class = d.get("assetClass", "crypto").lower()
    requested_style = d.get("style", "auto")
    _forex_struct_tf = CONFIG.get("ENGINE_B_FOREX_STRUCTURE_TF", "D1").upper()

    candidate_pairs = [
        p
        for p in ALL_PAIRS
        if p.get("type", "").lower() == asset_class
        and p.get("enabled", True)
        and p["display"] not in _disabled_pairs
    ]

    results = []
    _best_per_pair = {}

    from market_structure import NakedEngine

    engine = NakedEngine()

    import time

    def _fetch_cached_only(pair, tf, limit):
        """For naked scan: check in-memory TTL cache first, only call live if truly expired."""
        key = (pair.get("symbol", pair.get("display")), tf, int(limit))
        now = time.time()
        with _candle_cache_lock:
            entry = _candle_cache.get(key)
            if entry is not None:
                candles, expiry = entry
                if now < expiry:
                    return candles
        # Cache miss — call normal fetch_candles (will populate cache for next time)
        return fetch_candles(pair, tf, limit)

    for pair in candidate_pairs:
        try:
            # Yield CPU to prevent Flask thread locking during synchronous scan
            time.sleep(0.1)

            _pair_score_group = get_pair_score_group(pair)
            resolved_style, style_profile = _naked_scan_style_profile(
                requested_style, score_group=_pair_score_group
            )
            if (
                pair.get("type", "") == "forex"
                and _forex_struct_tf == "D1"
                and resolved_style == "intraday"
            ):
                resolved_style, style_profile = _naked_scan_style_profile(
                    "swing", score_group=_pair_score_group
                )

            # Determine which timeframes this pair/style needs
            _zone_tf = style_profile.get("zone_tf", "H4")
            _entry_tf = style_profile.get("entry_tf", "H1")
            _atr_tf = style_profile.get("atr_tf", "H4")
            _needed_tfs = list({_zone_tf, _entry_tf, _atr_tf, "D1"})

            # Fetch all needed timeframes
            _tf_map = {}
            for tf in _needed_tfs:
                cfg_key = f"{tf.upper()}_CANDLES"
                if cfg_key not in CONFIG:
                    cfg_key = "H4_CANDLES"
                limit = int(CONFIG[cfg_key])
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

            # Volatility gate
            if len(atr_series) >= 50:
                _valid_atrs = [a for a in atr_series[-50:] if a and a > 0]
                _atr_avg = sum(_valid_atrs) / len(_valid_atrs) if _valid_atrs else 0
                if _atr_avg > 0 and atr < _atr_avg * 0.6:
                    log.warning(f"[NAKED-DBG] {pair['display']}: ATR={atr:.6f} < 60% avg={_atr_avg:.6f} — VOL GATE")
                    continue

            current_price = float(entry_candles[-1]["close"])

            # Test both directions
            # analyze_structure uses: arg2 (h4 slot) for zones/macro, arg3 (h1 slot) for micro/BOS/sweep
            regime_label = _engine_b_regime_label(zone_candles, pair.get("type", "stock"))
            for direction in ["LONG", "SHORT"]:
                res = engine.analyze_structure(
                    d1_candles,
                    zone_candles,
                    entry_candles,
                    current_price,
                    direction,
                    atr,
                    regime_label,
                    fallback_rr=style_profile.get("fallback_rr", 2.0),
                    asset_type=pair.get("type", ""),
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
                    # Regime-scale the min_score: tighter gate in ranging/choppy, looser in trending
                    _regime_gate = {
                        "TRENDING": 0.85,        # trend does the work — slightly easier entry
                        "RANGING": 1.2,          # need more conviction in chop
                        "HIGH_VOLATILITY": 1.3,  # noise kills — require strong structure
                        "LOW_VOLATILITY": 1.0,   # default — calm market, standard gate
                    }
                    _min_score_scaled = style_profile["min_score"] * _regime_gate.get(regime_label, 1.0)

                    if conf_data["score"] < _min_score_scaled:
                        log.warning(
                            f"[NAKED-DBG] {pair['display']} {direction}: "
                            f"score={conf_data['score']:.1f} vs min={_min_score_scaled:.1f}, "
                            f"passed={conf_data.get('passed')}, regime={regime_label} — REJECTED"
                        )
                        continue

                    res.update(conf_data)
                    res["current_price"] = current_price
                    res["symbol"] = pair.get("symbol")
                    res["display"] = pair.get("display")
                    res["type"] = pair.get("type")
                    res["scoreGroup"] = _pair_score_group
                    res["direction"] = direction
                    res["style"] = resolved_style
                    res["regime"] = regime_label
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
                        log.warning(
                            f"[NAKED-DBG] {pair['display']} {direction}: "
                            f"rr={rr:.2f} < min_rr={style_profile['min_rr']} — REJECTED"
                        )
                        continue

                    signal = {
                        "id": f"NKD_{pair['display']}_{direction}_{int(time.time())}",
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                        "pair": pair.get("display"),
                        "display": pair.get("display"),
                        "symbol": pair.get("symbol"),
                        "type": pair.get("type"),
                        "scoreGroup": _pair_score_group,
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
                    
                    # Calculate style-specific SL/TP for display
                    try:
                        from indicators import calc_levels
                        _lvl_scalp = calc_levels(current_price, atr, direction, 
                                                  pair.get("type", "stock"), style="scalp")
                        _lvl_intra = calc_levels(current_price, atr, direction, 
                                                  pair.get("type", "stock"), style="intraday")
                        signal["scalp_sl"] = _lvl_scalp["sl"]
                        signal["scalp_tp"] = _lvl_scalp["tp1"]
                        signal["intraday_sl"] = _lvl_intra["sl"]
                        signal["intraday_tp"] = _lvl_intra["tp1"]
                    except Exception:
                        pass
                    
                    _pair_key = pair.get("display", "")
                    _existing = _best_per_pair.get(_pair_key)
                    if _existing is None or signal["confluenceScore"] > _existing["confluenceScore"]:
                        _best_per_pair[_pair_key] = signal
                else:
                    log.warning(f"[NAKED-DBG] {pair['display']} {direction}: verdict={verdict} seq={seq} — SKIPPED")
        except Exception as e:
            log.warning(f"[NAKED-SCAN] Error on {pair['display']}: {e}", exc_info=True)
            continue

    results = sorted(
        _best_per_pair.values(),
        key=lambda x: x.get("confluenceScore", 0),
        reverse=True,
    )

    return jsonify(_json_safe({"success": True, "signals": results}))


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

    if sig_id in executed_signals:
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
            executed_signals.add(sig_id)

            try:
                with sqlite3.connect(_AUDIT_DB, timeout=15.0) as con:
                    _factors = {
                        "scores": sig.get("factor_scores"),
                        "weights": sig.get("factor_weights"),
                        "disabled": sig.get("disabledFactors"),
                        "regime": sig.get("regimeName"),
                    }
                    con.execute(
                        "INSERT INTO audit_log(ts,pair,score,direction,trend,grade,edge_prob,risk,style,"
                        "entry_price,sl,tp,volume,regime,risk_amount,risk_pct,ticket,factors_json,"
                        "signal_price_ref,slippage_bps) "
                        "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
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
                            result.get("signalPriceRef"),
                            result.get("slippageBps"),
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


@app.route("/api/market-hours")
def api_market_hours():
    """Return open/closed status for all major markets in GMT+2 (SAST/EET).

    Sessions defined in UTC, converted to GMT+2 for display.
    Crypto is always open (24/7). Forex closes Fri 22:00 UTC, reopens Sun 22:00 UTC.
    """
    from datetime import datetime, timezone, timedelta

    now_utc = datetime.now(timezone.utc)
    now_gmt2 = now_utc + timedelta(hours=2)

    utc_h = now_utc.hour
    utc_m = now_utc.minute
    utc_total = utc_h * 60 + utc_m
    utc_weekday = now_utc.weekday()  # 0=Mon, 6=Sun

    def mins_until(target_total_utc):
        """Minutes until a UTC HH:MM time (expressed as total minutes)."""
        diff = target_total_utc - utc_total
        if diff <= 0:
            diff += 24 * 60
        return diff

    def fmt_opens_in(minutes):
        if minutes < 60:
            return f"Opens in {minutes}m"
        h = minutes // 60
        m = minutes % 60
        return f"Opens in {h}h {m}m" if m else f"Opens in {h}h"

    # ── Forex (Sun 22:00 UTC – Fri 22:00 UTC) ────────────────────────────────
    if utc_weekday == 5:  # Saturday — closed all day
        forex_open = False
        forex_status = "Closed (Weekend)"
        # Opens Sun 22:00 UTC = (6-5)*24*60 - utc_total + 22*60
        mins_to_sun22 = ((6 - utc_weekday) * 1440) - utc_total + 22 * 60
        forex_opens_in = fmt_opens_in(mins_to_sun22 % (7 * 1440))
    elif utc_weekday == 6 and utc_total < 22 * 60:  # Sunday before 22:00 UTC
        forex_open = False
        forex_status = "Closed (Weekend)"
        forex_opens_in = fmt_opens_in(22 * 60 - utc_total)
    elif utc_weekday == 4 and utc_total >= 22 * 60:  # Friday after 22:00 UTC
        forex_open = False
        forex_status = "Closed (Weekend)"
        forex_opens_in = "Opens Sun 22:00 UTC"
    else:
        forex_open = True
        forex_status = "Open"
        forex_opens_in = None

    # ── Sessions (UTC) ─────────────────────────────────────────────────────────
    # Sydney:    21:00–06:00 UTC  (23:00–08:00 GMT+2)
    # Tokyo:     00:00–09:00 UTC  (02:00–11:00 GMT+2)
    # London:    07:00–16:00 UTC  (09:00–18:00 GMT+2)
    # New York:  13:00–21:00 UTC  (15:00–23:00 GMT+2)

    def session_state(start_utc, end_utc):
        """Returns (is_open, mins_to_open_or_close)."""
        s = start_utc * 60
        e = end_utc * 60
        if s <= e:
            is_open = s <= utc_total < e
            if is_open:
                return True, e - utc_total
            else:
                return False, mins_until(s)
        else:  # wraps midnight
            is_open = utc_total >= s or utc_total < e
            if is_open:
                if utc_total >= s:
                    return True, (24 * 60 - utc_total) + e
                else:
                    return True, e - utc_total
            else:
                return False, mins_until(s)

    syd_open, syd_mins = session_state(21, 6)
    tok_open, tok_mins = session_state(0, 9)
    lon_open, lon_mins = session_state(7, 16)
    ny_open, ny_mins = session_state(13, 21)

    # London/NY overlap
    overlap_open = lon_open and ny_open

    # ── Equity markets (GMT+2 local times) ────────────────────────────────────
    # JSE:       09:00–17:00 GMT+2 (Mon-Fri)
    # LSE/UK100: 09:00–17:30 GMT+2 (Mon-Fri) = 07:00–15:30 UTC
    # NYSE/DAX:  NYSE 15:30–22:00 GMT+2 | DAX 09:00–17:30 GMT+2
    gmt2_h = now_gmt2.hour
    gmt2_m = now_gmt2.minute
    gmt2_total = gmt2_h * 60 + gmt2_m
    is_weekday = utc_weekday <= 4  # Mon–Fri

    def equity_state(open_gmt2, close_gmt2):
        s = open_gmt2[0] * 60 + open_gmt2[1]
        e = close_gmt2[0] * 60 + close_gmt2[1]
        if not is_weekday:
            diff = ((7 - utc_weekday) % 7) * 1440 + s - gmt2_total
            return False, "Closed (Weekend)", f"Opens Mon {open_gmt2[0]:02d}:{open_gmt2[1]:02d}"
        is_open = s <= gmt2_total < e
        if is_open:
            diff = e - gmt2_total
            return True, "Open", f"Closes in {diff//60}h {diff%60}m" if diff >= 60 else f"Closes in {diff}m"
        elif gmt2_total < s:
            diff = s - gmt2_total
            return False, "Pre-Market", fmt_opens_in(diff)
        else:
            return False, "Closed", f"Opens tomorrow {open_gmt2[0]:02d}:{open_gmt2[1]:02d}"

    jse_open, jse_status, jse_note = equity_state((9, 0), (17, 0))
    lse_open, lse_status, lse_note = equity_state((9, 0), (17, 30))
    dax_open, dax_status, dax_note = equity_state((9, 0), (17, 30))
    nyse_open, nyse_status, nyse_note = equity_state((15, 30), (22, 0))

    # ── Build response ─────────────────────────────────────────────────────────
    def session_detail(is_open, mins):
        if not forex_open:
            return {"open": False, "status": "Forex Closed", "note": forex_opens_in}
        if is_open:
            h, m = divmod(mins, 60)
            note = f"Closes in {h}h {m}m" if h else f"Closes in {m}m"
            return {"open": True, "status": "Active", "note": note}
        else:
            return {"open": False, "status": "Closed", "note": fmt_opens_in(mins)}

    return jsonify({
        "serverTime": now_utc.strftime("%Y-%m-%d %H:%M:%S UTC"),
        "localTime": now_gmt2.strftime("%Y-%m-%d %H:%M GMT+2"),
        "sessions": {
            "sydney":    session_detail(syd_open, syd_mins),
            "tokyo":     session_detail(tok_open, tok_mins),
            "london":    session_detail(lon_open, lon_mins),
            "new_york":  session_detail(ny_open, ny_mins),
            "overlap":   {"open": overlap_open and forex_open, "status": "London/NY Overlap" if (overlap_open and forex_open) else "Inactive", "note": "Highest liquidity"},
        },
        "markets": {
            "forex":  {"open": forex_open, "status": forex_status, "note": forex_opens_in or "Closes Fri 22:00 UTC", "hours": "Sun 22:00 – Fri 22:00 UTC"},
            "crypto": {"open": True, "status": "Open 24/7", "note": "Always open", "hours": "24/7"},
            "jse":    {"open": jse_open, "status": jse_status, "note": jse_note, "hours": "09:00–17:00 GMT+2"},
            "lse":    {"open": lse_open, "status": lse_status, "note": lse_note, "hours": "09:00–17:30 GMT+2"},
            "dax":    {"open": dax_open, "status": dax_status, "note": dax_note, "hours": "09:00–17:30 GMT+2"},
            "nyse":   {"open": nyse_open, "status": nyse_status, "note": nyse_note, "hours": "15:30–22:00 GMT+2"},
        },
    })


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
        out = api_backtest_impl(
            d,
            service_handle=lambda payload: handle_backtest_request(
                payload,
                normalize_style=_normalize_style,
                all_pairs=ALL_PAIRS,
                backtest_pair=backtest_pair,
                run_full_backtest=run_full_backtest,
                auto_toggle_pair=_auto_toggle_pair,
                active_pairs=ACTIVE_PAIRS,
                allow_auto_toggle=bool(CONFIG.get("BT_AUTO_TOGGLE", False)),
            ),
        )
        if out.get("error"):
            return jsonify({"error": out["error"]}), out.get("status", 400)
        return jsonify(_json_safe(out.get("data", {}))), out.get("status", 200)

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


def _scalp_ui_signal(raw_signal: dict) -> dict:
    """Normalize Engine D output to the shape expected by the scalp tab."""
    ai_grade = str(raw_signal.get("ai_grade", "C") or "C").upper()
    risk_level = (
        "LOW"
        if ai_grade == "A"
        else "MEDIUM"
        if ai_grade == "B"
        else "HIGH"
    )
    zone_conditions = raw_signal.get("zone_conditions", []) or []
    zone_desc = (
        f"{raw_signal.get('zone_type', 'zone').upper()} near {raw_signal.get('zone_level')} "
        f"with {len(zone_conditions)} condition(s): {', '.join(zone_conditions) or 'none'}"
    )
    trigger_desc = (
        f"{raw_signal.get('trigger_type', 'trigger')} + "
        f"{raw_signal.get('momentum_method', 'momentum confirmation')}"
    )
    return {
        "symbol": raw_signal.get("pair", ""),
        "pair": raw_signal.get("pair", ""),
        "direction": raw_signal.get("direction", ""),
        "entry": raw_signal.get("price"),
        "price": raw_signal.get("price"),
        "sl": raw_signal.get("sl"),
        "tp1": raw_signal.get("tp1"),
        "tp2": raw_signal.get("tp2"),
        "rr": float(raw_signal.get("rr1", 0.0) or 0.0),
        "ai_grade": ai_grade,
        "ai_score": raw_signal.get("ai_score", 0),
        "risk_level": risk_level,
        "zone_desc": zone_desc,
        "trigger_desc": trigger_desc,
        "engine": raw_signal.get("engine", "SCALP"),
        "session": raw_signal.get("session"),
        "spread_pips": raw_signal.get("spread_pips"),
        "timestamp": raw_signal.get("timestamp"),
    }


@app.route("/api/scalp-scan", methods=["POST"])
def api_scalp_scan():
    """Run Engine D scalp scan and return frontend-friendly JSON."""
    try:
        from scalp_engine import get_scalp_pairs, run_scalp_scan

        payload = request.get_json(silent=True) or {}
        pairs = payload.get("pairs")
        if not isinstance(pairs, list) or not pairs:
            pairs = get_scalp_pairs()

        result = run_scalp_scan(pairs)
        signals = [_scalp_ui_signal(s) for s in (result.get("signals", []) or [])]
        return jsonify(
            {
                "signals": signals,
                "skipped": result.get("skipped", []),
                "scanned": result.get("scanned", len(pairs)),
                "session": result.get("session"),
                "sessions_active": result.get("sessions_active", []),
                "reason": result.get("reason"),
            }
        )
    except Exception as e:
        log.error(f"api_scalp_scan error: {e}")
        return jsonify({"error": str(e)}), 500


@app.route("/api/scalp-execute", methods=["POST"])
def api_scalp_execute():
    """Execute a single Engine D scalp setup through the normal MT5 risk path."""
    try:
        from scalp_engine import run_scalp_scan
        from risk_engine import risk_check
        from mt5_executor import (
            mt5_execute,
            mt5_get_account,
            mt5_get_positions,
            mt5_get_symbol_info,
        )

        payload = request.get_json(silent=True) or {}
        symbol = (payload.get("symbol") or "").strip()
        if not symbol:
            return jsonify({"error": "Missing symbol"}), 400

        scan = run_scalp_scan([symbol])
        raw_signals = scan.get("signals", []) or []
        if not raw_signals:
            reason = scan.get("reason") or "No valid scalp setup found"
            skipped = scan.get("skipped", []) or []
            if skipped:
                reason = skipped[0].get("reason", reason)
            return jsonify({"success": False, "error": reason}), 200

        signal = raw_signals[0]
        account = mt5_get_account()
        if not account or account.get("error"):
            return jsonify({"error": "MT5 not connected"}), 503

        positions_resp = mt5_get_positions()
        positions = (
            positions_resp.get("positions", [])
            if isinstance(positions_resp, dict)
            else (positions_resp or [])
        )

        symbol_info = mt5_get_symbol_info(symbol)
        if not symbol_info or symbol_info.get("error"):
            return jsonify({"error": f"Symbol '{symbol}' not available on MT5"}), 200

        approval = risk_check(
            signal=signal,
            account_balance=account["balance"],
            account_equity=account["equity"],
            open_positions=positions,
            symbol_info=symbol_info,
            kill_switch=_kill_switch,
        )
        if not approval.approved:
            return jsonify(
                {
                    "success": False,
                    "error": f"Risk engine rejected: {approval.reason}",
                    "approval": approval.to_dict(),
                }
            ), 200

        result = mt5_execute(signal, approval)
        if not result.get("success"):
            return jsonify(
                {"success": False, "error": result.get("error", "Execution failed")}
            ), 200

        return jsonify(
            {
                "success": True,
                "ticket": result.get("ticket"),
                "volume": result.get("volume"),
                "entry_price": result.get("entry_price"),
                "approval": approval.to_dict(),
            }
        )
    except Exception as e:
        log.error(f"api_scalp_execute error: {e}")
        return jsonify({"error": str(e)}), 500


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
            CONFIG.get("XAI_MODEL", "grok-4.20-0309-reasoning"),
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


@app.route("/api/chart-analysis", methods=["POST"])
def api_chart_analysis():
    """Send chart screenshot to Claude Vision for professional TA reading."""
    if _anthropic_mod is None:
        return jsonify({"error": "anthropic library not installed"}), 500

    data = request.get_json()
    if not data or not data.get("image"):
        return jsonify({"error": "Missing image data"}), 400

    symbol = data.get("symbol", "")
    tf = data.get("tf", "H4")

    # Find pair context
    pair = next(
        (p for p in ALL_PAIRS if p.get("symbol") == symbol or p.get("display") == symbol),
        None,
    )

    context_parts = []

    # Engine A signal context — prefer POST body (fresh frontend state), fall back to last scan cache
    sig = data.get("signal")
    if not sig:
        for s in _last_scan_results.get("signals", []):
            if s.get("symbol") == symbol or s.get("display") == symbol:
                sig = s
                break

    if sig:
        # regime can be a dict {"label": "..."} (Engine A) or a plain string (Engine C)
        _regime_raw = sig.get('regime', {})
        _regime_label = (
            _regime_raw.get('label', 'UNKNOWN')
            if isinstance(_regime_raw, dict)
            else str(_regime_raw or 'UNKNOWN')
        )
        context_parts.append(
            f"ENGINE A: direction={sig.get('direction')}, "
            f"score={sig.get('confluenceScore')}/{sig.get('maxScore', 3.0)}, "
            f"regime={_regime_label}, "
            f"style={sig.get('tradeStyle', 'swing')}"
        )
        context_parts.append(
            f"LEVELS: entry={sig.get('price')}, sl={sig.get('sl')}, "
            f"tp1={sig.get('tp1')}, tp2={sig.get('tp2')}, "
            f"rr1={sig.get('rr1')}, rr2={sig.get('rr2')}"
        )
        factors = sig.get("factors_json") or sig.get("votes", {})
        if factors:
            context_parts.append(f"FACTOR VOTES: {factors}")

    # Engine B context — prefer POST body (frontend state), fall back to server-side cache
    sid = symbol.replace("/", "_").replace("=", "_").replace("^", "_").replace(".", "_")
    eb = data.get("engineB") or _engine_b_cache.get(sid) or _engine_b_cache.get(symbol)
    if eb:
        context_parts.append(
            f"ENGINE B: swing_sequence={eb.get('current_swing_sequence')}, "
            f"bos_bull={eb.get('bos_data', {}).get('bos_bull')}, "
            f"bos_bear={eb.get('bos_data', {}).get('bos_bear')}, "
            f"choch_bull={eb.get('choch_data', {}).get('choch_bull')}, "
            f"choch_bear={eb.get('choch_data', {}).get('choch_bear')}, "
            f"bos_mtf_confirmed={eb.get('bos_mtf_confirmed')}"
        )
        if eb.get("order_blocks"):
            obs_str = ", ".join(
                [f"{ob['type']} str={ob.get('strength', 0)}%" for ob in eb["order_blocks"]]
            )
            context_parts.append(f"ORDER BLOCKS: {obs_str}")
        if eb.get("nearest_support_zone"):
            z = eb["nearest_support_zone"]
            context_parts.append(f"SUPPORT ZONE: {z.get('lower')}-{z.get('upper')}")
        if eb.get("nearest_resistance_zone"):
            z = eb["nearest_resistance_zone"]
            context_parts.append(f"RESISTANCE ZONE: {z.get('lower')}-{z.get('upper')}")
        if eb.get("breaker_block"):
            context_parts.append(
                f"BREAKER: {eb['breaker_block'].get('type')} at {eb['breaker_block'].get('level')}"
            )
        if eb.get("confidence"):
            conf = eb["confidence"]
            context_parts.append(
                f"CONFIDENCE: score={conf.get('score')}, "
                f"passed={conf.get('passed')}, rr={conf.get('rr')}"
            )

    asset_type = pair.get("type", "unknown") if pair else "unknown"
    algo_context = "\n".join(context_parts) if context_parts else "No algorithmic data available."

    system_prompt = (
        "You are a senior technical analyst reviewing a chart with full algorithmic context. "
        "You speak directly — no hedging, no disclaimers. You are reviewing this chart for "
        "a professional algorithmic trader who needs confirmation or contradiction of the "
        "system's signals. Be specific about price levels and patterns you observe."
    )

    direction_str = sig.get("direction", "UNKNOWN") if sig else "UNKNOWN"

    def _extract_vision_structured(analysis_text: str, direction_hint: str) -> dict:
        """Extract structured Vision fields from free-text analysis."""
        import re as _re

        txt = analysis_text or ""
        up = txt.upper()
        out = {
            "rating": "",
            "confirms_direction": True,
            "sl_flag": "ok",
            "tp_flag": "ok",
            "style_ratings": {},
        }
        for style in ("SCALP", "INTRADAY", "SWING"):
            m = _re.search(
                rf"{style}\s+RATING\s*:\s*(STRONG|MODERATE|WEAK|AVOID|CONTRADICTS?)",
                up,
            )
            if m:
                val = m.group(1).upper()
                if val == "CONTRADICT":
                    val = "CONTRADICTS"
                out["style_ratings"][style.lower()] = val

        if out["style_ratings"]:
            for label in ("STRONG", "MODERATE", "WEAK", "AVOID", "CONTRADICTS"):
                if label in out["style_ratings"].values():
                    out["rating"] = label
                    break
        if not out["rating"]:
            for label in ("CONTRADICTS", "AVOID", "STRONG", "MODERATE", "WEAK"):
                if label in up:
                    out["rating"] = label
                    break

        if "CONFLICTED" in up or "CONTRADICT" in up:
            out["confirms_direction"] = False
            out["rating"] = "CONTRADICTS"
        elif direction_hint and direction_hint.upper() in ("LONG", "SHORT"):
            _oppose = _re.search(r"(OPPOSES|AGAINST)\s+THE\s+ALGORITHMIC", up)
            if _oppose:
                out["confirms_direction"] = False
                out["rating"] = "CONTRADICTS"

        if _re.search(r"\bSL\b.*(TOO\s+TIGHT|TIGHT)", up):
            out["sl_flag"] = "too_tight"
        if _re.search(r"\bTP\b.*(TOO\s+FAR|UNREALISTIC)", up):
            out["tp_flag"] = "too_far"

        if not out["rating"]:
            out["rating"] = "MODERATE"
        return out

    user_prompt = (
        f"Analyse this {asset_type.upper()} chart ({tf} timeframe).\n\n"
        f"ALGORITHMIC CONTEXT:\n{algo_context}\n\n"
        "CHART ANNOTATIONS VISIBLE:\n"
        "- Green/red candles with EMA 21 (cyan), EMA 50 (purple), EMA 200 (gold dashed)\n"
        "- Entry (grey dashed), SL (red solid), TP1/TP2 (green solid) horizontal lines\n"
        "- Engine B zones may be visible (support green, resistance red, BOS amber, CHoCH purple, OB labelled)\n\n"
        "ANSWER THESE 5 QUESTIONS:\n"
        "1. PATTERN: What price action pattern is forming? (flag, wedge, channel, H&S, double top/bottom, breakout, pullback, range, etc.)\n"
        f"2. STRUCTURE: Does the visible price structure CONFIRM or CONTRADICT the algorithmic {direction_str} bias? Why?\n"
        "3. MISSED: Are there any patterns or levels the algorithm may have missed? (unfilled gaps, hidden divergence, liquidity pools above/below current price, trendline breaks)\n"
        "4. SL/TP ASSESSMENT: Based on what you see, are the SL and TP levels well-placed? Would you adjust either? Be specific with price levels.\n"
        "5. PER-STYLE RATINGS: Rate this chart setup for EACH trade style independently. "
        "Consider whether the visible structure, momentum, and levels suit that holding period.\n"
        "SCALP RATING: STRONG / MODERATE / WEAK / AVOID\n"
        "INTRADAY RATING: STRONG / MODERATE / WEAK / AVOID\n"
        "SWING RATING: STRONG / MODERATE / WEAK / AVOID\n"
        "(use CONTRADICTS instead if the chart clearly opposes the algorithmic direction for that style) "
        "— one sentence justification per style.\n\n"
        "You MUST end with exactly these 4 lines:\n"
        "TF ALIGNMENT: ALIGNED or CONFLICTED\n"
        "SCALP RATING: <STRONG|MODERATE|WEAK|AVOID|CONTRADICTS>\n"
        "INTRADAY RATING: <STRONG|MODERATE|WEAK|AVOID|CONTRADICTS>\n"
        "SWING RATING: <STRONG|MODERATE|WEAK|AVOID|CONTRADICTS>\n\n"
        "Keep total response under 350 words. Be direct."
    )

    try:
        api_key = os.environ.get("ANTHROPIC_API_KEY", "")
        if not api_key:
            return jsonify({"error": "ANTHROPIC_API_KEY not set"}), 500

        client = _anthropic_mod.Anthropic(api_key=api_key)

        # Strip data URL prefix if present (H4 primary image)
        img_h4 = data["image"]
        if img_h4.startswith("data:"):
            img_h4 = img_h4.split(",", 1)[1]

        # Optional D1 bias image
        img_d1 = data.get("image_d1")
        if img_d1 and img_d1.startswith("data:"):
            img_d1 = img_d1.split(",", 1)[1]

        # Optional H1 entry image (Engine C triple-screen)
        img_h1 = data.get("image_h1")
        if img_h1 and img_h1.startswith("data:"):
            img_h1 = img_h1.split(",", 1)[1]

        dual_mode = bool(img_d1)
        triple_mode = bool(img_d1 and img_h1)

        if triple_mode:
            # ── Triple-screen (Elder): D1 bias, H4 tactical, H1 entry quality ──
            triple_prompt = (
                f"You are reviewing THREE charts for {asset_type.upper()} — {symbol}.\n"
                "IMAGE 1 is D1 (daily) — strategic TREND / BIAS filter.\n"
                "IMAGE 2 is H4 (4-hour) — intermediate structure, momentum, EMA stack.\n"
                "IMAGE 3 is H1 (1-hour) — entry timing: RSI zone, EMA21 reclaim, trigger candle.\n\n"
                f"ALGORITHMIC CONTEXT:\n{algo_context}\n\n"
                "CHART ANNOTATIONS (same in all three images):\n"
                "- Green/red candles · EMA 21 (cyan) · EMA 50 (purple) · EMA 200 (gold dashed)\n"
                "- Entry (grey dashed) · SL (red solid) · TP (green solid) lines\n"
                "- Engine B zones may be visible: support (green), resistance (red), "
                "BOS (amber), CHoCH (purple), OB (labelled)\n\n"
                "ANSWER THESE 5 QUESTIONS:\n"
                f"1. D1 BIAS: What is the clear trend on D1? Does it CONFIRM or CONTRADICT "
                f"the algorithmic {direction_str} signal? One sentence.\n"
                f"2. H4 STRUCTURE: Does H4 show valid intermediate {direction_str} alignment? "
                "Momentum, swing structure, EMA positioning.\n"
                f"3. H1 ENTRY: Is this a clean {direction_str} entry on H1 now? "
                "Comment on pullback depth vs EMA21, candle quality, and whether to wait.\n"
                "4. TF ALIGNMENT: Do D1, H4, and H1 ALL support the same direction? "
                "Answer ALIGNED or CONFLICTED with one-line justification. "
                "CONFLICTED means the trade should be skipped. "
                "Also: are SL and TP well-placed vs H1/H4 structure? Name price levels.\n"
                "5. PER-STYLE RATINGS (H1 primary for SCALP, H4 for INTRADAY, D1 for SWING):\n"
                "SCALP RATING: STRONG / MODERATE / WEAK / AVOID\n"
                "INTRADAY RATING: STRONG / MODERATE / WEAK / AVOID\n"
                "SWING RATING: STRONG / MODERATE / WEAK / AVOID\n"
                "(use CONTRADICTS if that timeframe clearly opposes the algorithmic direction)\n\n"
                "You MUST end with exactly these 4 lines:\n"
                "TF ALIGNMENT: ALIGNED or CONFLICTED\n"
                "SCALP RATING: <STRONG|MODERATE|WEAK|AVOID|CONTRADICTS>\n"
                "INTRADAY RATING: <STRONG|MODERATE|WEAK|AVOID|CONTRADICTS>\n"
                "SWING RATING: <STRONG|MODERATE|WEAK|AVOID|CONTRADICTS>\n\n"
                "Keep total response under 480 words. Be direct."
            )
            content = [
                {"type": "text", "text": "IMAGE 1 — D1 DAILY BIAS CHART:"},
                {
                    "type": "image",
                    "source": {"type": "base64", "media_type": "image/png", "data": img_d1},
                },
                {"type": "text", "text": "IMAGE 2 — H4 INTERMEDIATE CHART:"},
                {
                    "type": "image",
                    "source": {"type": "base64", "media_type": "image/png", "data": img_h4},
                },
                {"type": "text", "text": "IMAGE 3 — H1 ENTRY TIMING CHART:"},
                {
                    "type": "image",
                    "source": {"type": "base64", "media_type": "image/png", "data": img_h1},
                },
                {"type": "text", "text": triple_prompt},
            ]
            log.info(f"[AI CHART] Triple-screen D1+H4+H1 analysis for {symbol}")
        elif dual_mode:
            # ── Dual-TF prompt: D1 for bias, H4 for entry ──────────────────
            dual_prompt = (
                f"You are reviewing TWO charts for {asset_type.upper()} — {symbol}.\n"
                "IMAGE 1 is the D1 (daily) chart — tells you the macro TREND and BIAS.\n"
                "IMAGE 2 is the H4 (4-hour) chart — tells you the ENTRY TIMING and structure.\n\n"
                f"ALGORITHMIC CONTEXT:\n{algo_context}\n\n"
                "CHART ANNOTATIONS (same in both images):\n"
                "- Green/red candles · EMA 21 (cyan) · EMA 50 (purple) · EMA 200 (gold dashed)\n"
                "- Entry (grey dashed) · SL (red solid) · TP (green solid) lines\n"
                "- Engine B zones may be visible: support (green), resistance (red), "
                "BOS (amber), CHoCH (purple), OB (labelled)\n\n"
                "ANSWER THESE 5 QUESTIONS:\n"
                f"1. D1 BIAS: What is the clear trend on D1? Does it CONFIRM or CONTRADICT "
                f"the algorithmic {direction_str} signal? One sentence.\n"
                f"2. H4 ENTRY: Does H4 show a valid {direction_str} entry setup right now? "
                "Describe structure, momentum, and EMA positioning.\n"
                "3. TF ALIGNMENT: Do D1 and H4 BOTH support the same direction? "
                "Answer ALIGNED or CONFLICTED with one-line justification. "
                "This is the most important question — a CONFLICTED answer means the trade should be skipped.\n"
                "4. SL/TP ASSESSMENT: Based on H4 structure, are SL and TP well-placed? "
                "Would you adjust either? Be specific with price levels.\n"
                "5. PER-STYLE RATINGS (weight both timeframes — D1 for swing, H4 for scalp/intraday):\n"
                "SCALP RATING: STRONG / MODERATE / WEAK / AVOID\n"
                "INTRADAY RATING: STRONG / MODERATE / WEAK / AVOID\n"
                "SWING RATING: STRONG / MODERATE / WEAK / AVOID\n"
                "(use CONTRADICTS if the combined picture clearly opposes the algorithmic direction)\n\n"
                "You MUST end with exactly these 4 lines:\n"
                "TF ALIGNMENT: ALIGNED or CONFLICTED\n"
                "SCALP RATING: <STRONG|MODERATE|WEAK|AVOID|CONTRADICTS>\n"
                "INTRADAY RATING: <STRONG|MODERATE|WEAK|AVOID|CONTRADICTS>\n"
                "SWING RATING: <STRONG|MODERATE|WEAK|AVOID|CONTRADICTS>\n\n"
                "Keep total response under 420 words. Be direct."
            )
            content = [
                {"type": "text", "text": "IMAGE 1 — D1 DAILY BIAS CHART:"},
                {
                    "type": "image",
                    "source": {"type": "base64", "media_type": "image/png", "data": img_d1},
                },
                {"type": "text", "text": "IMAGE 2 — H4 ENTRY CHART:"},
                {
                    "type": "image",
                    "source": {"type": "base64", "media_type": "image/png", "data": img_h4},
                },
                {"type": "text", "text": dual_prompt},
            ]
            log.info(f"[AI CHART] Dual-TF D1+H4 analysis for {symbol}")
        else:
            # ── Single-TF fallback (H4 only) ───────────────────────────────
            content = [
                {
                    "type": "image",
                    "source": {"type": "base64", "media_type": "image/png", "data": img_h4},
                },
                {"type": "text", "text": user_prompt},
            ]
            log.info(f"[AI CHART] Single-TF {tf} analysis for {symbol}")

        _max_tokens = 1100 if triple_mode else 800
        _vision_model = CONFIG.get("VISION_MODEL", "claude-opus-4-6")
        _vision_temp = float(CONFIG.get("AI_VISION_TEMPERATURE", 0.6))
        message = client.messages.create(
            model=_vision_model,
            max_tokens=_max_tokens,
            temperature=_vision_temp,
            system=system_prompt,
            messages=[{"role": "user", "content": content}],
        )

        analysis = message.content[0].text if message.content else "No analysis returned."
        structured = _extract_vision_structured(analysis, direction_str)

        _tf_label = "D1+H4+H1" if triple_mode else ("D1+H4" if dual_mode else tf)
        return jsonify({
            "analysis": analysis,
            "structured": structured,
            "model": _vision_model,
            "symbol": symbol,
            "tf": _tf_label,
            "dual_tf": dual_mode,
            "triple_tf": triple_mode,
        })

    except Exception as e:
        log.error(f"[AI CHART] Error: {e}")
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

    _cb = get_candle_builder()
    if not _cb:
        return jsonify({"error": "Candle builder not running"}), 503

    if pair:
        candles = _cb.get_candles(pair, tf, limit)

        in_progress = {}

        with _cb._lock:
            bar = _cb._bars.get((pair, tf))

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

    stats = _cb.stats()

    active = {}

    with _cb._lock:
        for (disp, tf_k), bar in _cb._bars.items():
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


@app.route("/api/candles", methods=["GET"])
def api_candles():
    """Return OHLCV candles for the chart widget."""
    symbol = request.args.get("symbol")
    tf = request.args.get("tf", "H4").upper()
    # Binance klines allow up to 1000; extra history lets H4/D1 EMA200 start further left vs TradingView.
    try:
        limit = min(int(request.args.get("limit", 300)), 1000)
    except (TypeError, ValueError):
        limit = 300

    if not symbol:
        return jsonify({"error": "Missing symbol parameter"}), 400

    pair = next(
        (p for p in ALL_PAIRS if p.get("symbol") == symbol or p.get("display") == symbol),
        None,
    )
    if not pair:
        return jsonify({"error": f"Unknown symbol: {symbol}"}), 404

    # Dashboard chart: use a forex-only aligned candle path so H1/H4/D1 for Vision
    # come from one canonical H1 timeline (EODHD + optional forming WS merge).
    # ?source=live keeps the generic shared fetch path for debugging.
    ptype = pair.get("type") or ""
    source_q = (request.args.get("source") or "").strip().lower()
    chart_source = "live" if source_q == "live" else "shared"
    candles = None

    if ptype == "forex" and pair.get("source") == "eodhd" and source_q != "live":
        # Fetch one canonical H1 stream and resample deterministically for H4/D1.
        base_limit = {
            "H1": limit,
            "H4": min(max(limit * 4 + 8, 80), 9000),
            "D1": min(max(limit * 24 + 24, 240), 9000),
        }.get(tf, limit)
        h1_series = _extract_candles(fetch_eodhd(pair, "H1", base_limit))
        if h1_series:
            h1_series, ws_note = _merge_forex_forming_ws(
                h1_series, pair.get("display", ""), "H1", base_limit
            )
            candles = _resample_from_h1(
                h1_series,
                tf,
                limit,
                alignment_offset_hours=(
                    _forex_h4_resample_offset_hours() if ptype == "forex" else 0.0
                ),
            )
            if candles:
                chart_source = "eodhd_h1_resampled"
                if ws_note:
                    chart_source += "+ws"

    if not candles:
        candles = fetch_candles(pair, tf, limit)
        # Optional forming-bar merge for forex on generic/shared path.
        if candles and ptype == "forex" and pair.get("source") != "polygon":
            candles, ws_note = _merge_forex_forming_ws(
                candles, pair.get("display", ""), tf, limit
            )
            if ws_note and chart_source == "shared":
                chart_source = "shared+ws"

    if not candles:
        return jsonify({"error": f"No candle data for {symbol} {tf}"}), 404

    _naive_iso_utc = re.compile(
        r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d+)?$"
    )
    result = []
    for c in candles:
        _t = c.get("time", c.get("datetime", ""))
        if isinstance(_t, str):
            _ts = _t.strip().replace(" ", "T")
            if _naive_iso_utc.match(_ts):
                _ts += "Z"
            _t = _ts
        result.append(
            {
                "t": _t,
                "o": float(c.get("open", 0)),
                "h": float(c.get("high", 0)),
                "l": float(c.get("low", 0)),
                "c": float(c.get("close", 0)),
                "v": float(c.get("vol", c.get("volume", 0))),
            }
        )

    return jsonify(
        {
            "candles": result,
            "symbol": symbol,
            "display": pair.get("display", symbol),
            "tf": tf,
            "pairType": ptype,
            "candlesSource": chart_source,
        }
    )


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


def analyze_pair(
    pair, btc_bias, style="swing", use_naked_engine=False, regime_context=None
):

    pair_profile = get_pair_profile(pair)
    _score_group = get_pair_score_group(pair)
    _pair_ctx = dict(pair or {})
    _pair_ctx["score_group"] = _score_group

    _lim = scan_candle_limits()
    d1 = fetch_candles(pair, "D1", _lim["D1"])

    h4 = fetch_candles(pair, "H4", _lim["H4"])

    h1 = fetch_candles(pair, "H1", _lim["H1"])

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
    _msig_age = time.time() - _msig.get("_updated_ts", 0) if _msig else 999.0
    if _msig and _msig_age < 45.0:
        h4i["snap"].update(
            {
                k: v
                for k, v in _msig.items()
                if v is not None and k not in {"_updated_ts", "_exchange"}
            }
        )
        h4i["snap"]["microstructure_exchange"] = _msig.get("_exchange")
        h4i["snap"]["microstructure_age_sec"] = round(_msig_age, 3)

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
            from regime import detect_regime

            _forex_result = compute_forex_score(
                d1_snap=_cf_d1i["snap"],
                h4_snap=_cf_h4i["snap"],
                h1_snap=_cf_h1i["snap"],
                h1_candles=_cf_h1c,
                pair=_pair_ctx,
                bar_time=str(d1[-1].get("time", "") or d1[-1].get("datetime", "")),
                h4_candles=_cf_h4c,
                score_group=_score_group,
            )
            _fx_regime = detect_regime(
                _cf_h4i["snap"],
                pair.get("type", "forex"),
                bb_width_pct=_cf_h4i["snap"].get("bbWidth_pct")
                or _cf_h4i["snap"].get("bb_width_pct"),
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
                "regime": {
                    "state": _fx_regime.get("state", 1),
                    "label": _fx_regime.get("label", "RANGING"),
                },
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
                f"[FOREX] {pair.get('display')} forex_scoring FAILED: {_fx_err} — skipping pair to avoid factor-engine fallback"
            )
            return None

    if res is None or pair.get("type") != "forex":
        res = calc_confluence(
            _cf_d1i,
            _cf_h4i,
            _cf_h1i,
            vr,
            stoch,
            _pair_ctx,
            btc_bias,
            d1_candles=_cf_d1c,
            h4_candles=_cf_h4c,
            h1_candles=_cf_h1c,
            funding_rate=_funding_rate,
            volume_threshold=pair_profile.get(
                "volume_threshold", CONFIG["VOLUME_THRESHOLD"]
            ),
            regime_context=regime_context,
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
        float(price), float(atr), direction, pair["type"],
        regime_state=_regime_state, style=_style,
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

    # Max possible final_score depends on scoring engine:
    # - Forex: 0-1 scale (forex_scoring.py caps at 1.0)
    # - Crypto/Stock: 0-3 scale (z-score factor engine, capped at 3.0)
    # Only set maxScoreOverride if not already set (forex sets it to 1.0)
    if res.get("maxScoreOverride") is None:
        res["maxScoreOverride"] = 3.0
    
    max_score = res.get("maxScoreOverride") or _max_score_for_pair(pair)

    # --- ENGINE B: NAKED MARKET STRUCTURE OVERLAY ---
    structure_data = None
    if use_naked_engine and res["score"] >= get_pair_profile(pair).get(
        "min_score",
        CONFIG["MIN_CONFLUENCE_CLASS"].get(
            pair.get("type", ""), CONFIG.get("MIN_CONFLUENCE", 0.6)
        ),
    ):
        try:
            from market_structure import engine as naked_engine
            _overlay_style, _overlay_profile = _naked_scan_style_profile(
                _style, score_group=_score_group
            )

            _regime_label = _engine_b_regime_label(
                h4,
                pair.get("type", "stock"),
                res.get("regime"),
            )
            structure_data = naked_engine.analyze_structure(
                d1, h4, h1, float(price), direction, float(atr), _regime_label, asset_type=pair.get("type", "")
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

    # Dynamic Confluence Scaling: Anchor the UI 67% mark to the actual pair threshold.
    # This prevents strong Crypto signals (e.g. 1.88) from looking 'WEAK' just because 3.0 is impossible.
    _threshold = get_min_confluence_threshold(pair)
    _raw_pct = (res["score"] / _threshold) * 67 if _threshold > 0 else 0
    _confluence_pct = min(100, max(0, round(_raw_pct)))

    return {
        "pair": pair["display"],
        "display": pair["display"],
        "symbol": pair["symbol"],
        "type": pair["type"],
        "scoreGroup": _score_group,
        "direction": direction,
        "confluenceScore": round(res["score"], 4),
        "confluencePct": _confluence_pct,
        "votes": res["votes"],
        "maxScore": max_score,
        "price": round(float(price), 6),
        "sl": round(float(lvl["sl"]), 6),
        "tp1": round(float(lvl["tp1"]), 6),
        "tp2": round(float(lvl["tp2"]), 6),
        "rr1": round(float(lvl["rr1"]), 2),
        "rr2": round(float(lvl["rr2"]), 2),
        "atr": round(float(atr), 6),
        "slDistance": round(abs(float(price) - float(lvl["sl"])), 6),
        "slPips": round(abs(float(price) - float(lvl["sl"])) * (100 if "JPY" in pair.get("display", "") else 10000) if pair.get("type") == "forex" else 1, 1),
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
        # Session badge = current FX liquidity window (UTC now). Do not use h4[-1]
        # time — vendor bar timestamps can lag or parse oddly vs wall clock at scan time.
        "session": get_session(),
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "aiAnalysis": None,
        "oiDivergence": _oi_divergence,
        "fundingRate": res.get("fundingRate"),
        "regime": res.get("regime"),
        "factorScores": res.get("factor_scores"),
        "factorWeights": res.get("factor_weights"),
        "regimeName": res.get("regimeName"),
        "correlationAdjustments": res.get("correlationAdjustments", {}),
        "disabledFactors": res.get("disabledFactors", []),
        "factorDiagnostics": res.get("factorDiagnostics", {}),
        "pairProfile": pair_profile,
        "style": _style,
        "h1Candles": [
            {
                "t": c.get("time", ""),
                "o": round(c["open"], 6),
                "h": round(c["high"], 6),
                "l": round(c["low"], 6),
                "c": round(c["close"], 6),
                "v": round(float(c.get("vol", c.get("volume", 0)) or 0), 2),
            }
            for c in h1[-120:]
        ],
        "h4Candles": [
            {
                "t": c.get("time", ""),
                "o": round(c["open"], 6),
                "h": round(c["high"], 6),
                "l": round(c["low"], 6),
                "c": round(c["close"], 6),
                "v": round(float(c.get("vol", c.get("volume", 0)) or 0), 2),
            }
            for c in h4[-80:]
        ],
        "d1Candles": [
            {
                "t": c.get("time", ""),
                "o": round(c["open"], 6),
                "h": round(c["high"], 6),
                "l": round(c["low"], 6),
                "c": round(c["close"], 6),
                "v": round(float(c.get("vol", c.get("volume", 0)) or 0), 2),
            }
            for c in d1[-120:]
        ],
        "style_levels": _build_style_levels(
            price=float(price),
            atr=float(atr),
            direction=direction,
            pair_type=pair.get("type", "stock"),
        ),
    }


# _build_event_risk imported from scoring.py


# _classify_signal imported from scoring.py — see that module for implementation


def _build_style_levels(price: float, atr: float, direction: str, pair_type: str) -> dict:
    """Compute SL/TP1/TP2/RR for scalp, intraday, and swing styles from ATR multipliers.
    Returns a dict keyed by style for frontend display on signal cards.
    """
    from indicators import calc_levels as _calc_levels
    result = {}
    if not price or not atr or price <= 0 or atr <= 0:
        return result
    for _style in ("scalp", "intraday", "swing"):
        try:
            lvl = _calc_levels(price, atr, direction, pair_type, style=_style)
            result[_style] = {
                "sl":  round(float(lvl["sl"]),  6),
                "tp1": round(float(lvl["tp1"]), 6),
                "tp2": round(float(lvl["tp2"]), 6),
                "rr1": round(float(lvl["rr1"]), 2),
                "rr2": round(float(lvl["rr2"]), 2),
            }
        except Exception:
            pass
    return result


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


def _mt5_deals_for_audit_ticket(_mt5_lib, ticket_int: int):
    """All history deals for a closed row. Prefer position= (stable across partials/closes).

    Stored ticket may be position id (preferred) or legacy opening *order* id — both are handled.
    """

    deals = _mt5_lib.history_deals_get(position=ticket_int)
    if not deals:
        by_order = _mt5_lib.history_deals_get(ticket=ticket_int)
        if by_order:
            pids = {
                int(d.position_id)
                for d in by_order
                if getattr(d, "position_id", 0) not in (None, 0)
            }
            for pid in pids:
                expanded = _mt5_lib.history_deals_get(position=pid)
                if expanded:
                    deals = expanded
                    break
            if not deals and pids:
                pid = max(pids)
                deals = _mt5_lib.history_deals_get(position=pid)
    if not deals:
        from_dt = datetime.now(timezone.utc) - timedelta(days=90)
        to_dt = datetime.now(timezone.utc)
        raw = _mt5_lib.history_deals_get(
            int(from_dt.timestamp()),
            int(to_dt.timestamp()),
        )
        if raw:
            related = [
                d
                for d in raw
                if int(getattr(d, "position_id", 0) or 0) == ticket_int
                or int(getattr(d, "order", 0) or 0) == ticket_int
            ]
            if related:
                pids = {
                    int(d.position_id)
                    for d in related
                    if getattr(d, "position_id", 0) not in (None, 0)
                }
                if len(pids) == 1:
                    pid = next(iter(pids))
                    deals = _mt5_lib.history_deals_get(position=pid) or related
                else:
                    deals = related
    return list(deals) if deals else []


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

            # Position closed — look up deal history

            try:
                ticket_int = int(ticket_str)

                deals = _mt5_deals_for_audit_ticket(_mt5_lib, ticket_int)

                if deals:
                    deals_sorted = sorted(deals, key=lambda d: d.time)
                    close_deal = deals_sorted[-1]
                    total_profit = sum(float(d.profit) for d in deals_sorted)

                    _update_trade_outcome(
                        ticket=ticket_str,
                        exit_price=float(close_deal.price),
                        exit_time=datetime.fromtimestamp(
                            close_deal.time, tz=timezone.utc
                        ).isoformat(),
                        pnl=total_profit,
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

        from collections import defaultdict

        with sqlite3.connect(_AUDIT_DB, timeout=15.0) as con:
            con.row_factory = sqlite3.Row

            pending = con.execute(
                "SELECT id, ticket, pair, direction, entry_price, sl, tp, volume, ts, risk_amount FROM audit_log "
                "WHERE ticket IS NOT NULL AND ticket != '' AND exit_price IS NULL "
                "AND pair LIKE '%USDT%'"
            ).fetchall()

        by_sym: dict = defaultdict(list)
        for row in pending:
            pair_raw = row["pair"] or ""
            ccxt_sym = _bybit_mod.bybit_map_symbol(
                pair_raw
            ) or _bybit_mod.bybit_map_symbol(pair_raw.replace("/", ""))
            if not ccxt_sym:
                continue
            by_sym[ccxt_sym].append({k: row[k] for k in row.keys()})

        until_ms = int(datetime.now(timezone.utc).timestamp() * 1000)

        for ccxt_sym, rows in by_sym.items():
            sym_open = [p for p in positions if p.get("symbol") == ccxt_sym]
            rows_to_close = []
            for row in rows:
                if _bybit_mod.bybit_audit_row_still_open(row, sym_open):
                    continue
                rows_to_close.append(row)
            if not rows_to_close:
                continue

            min_ts = min(
                _bybit_mod.bybit_audit_ts_to_ms(r.get("ts")) or 0
                for r in rows_to_close
            )
            since_ms = max(0, min_ts - 120_000)
            history = _bybit_mod.bybit_fetch_closed_positions_history(
                exchange, ccxt_sym, since_ms, until_ms, limit=100
            )
            used_keys: set = set()

            for row in sorted(rows_to_close, key=lambda r: r.get("ts") or ""):
                try:
                    matched = _bybit_mod.bybit_match_closed_history_row(
                        row, history, used_keys
                    )
                    if not matched:
                        matched = _bybit_mod.bybit_match_closed_history_row(
                            row,
                            history,
                            used_keys,
                            entry_tol=0.035,
                            vol_tol=0.09,
                        )
                    exit_px = 0.0
                    exit_iso = ""
                    pnl_est = 0.0
                    if matched:
                        exit_px, exit_iso, pnl_est = (
                            _bybit_mod.bybit_closed_row_to_outcome(matched)
                        )
                    if (not matched) or exit_px <= 0:
                        fb = _bybit_mod.bybit_fallback_outcome_from_trades(
                            exchange, ccxt_sym, row
                        )
                        if fb:
                            exit_px, exit_iso, pnl_est = fb
                    if exit_px > 0 and exit_iso:
                        _update_trade_outcome(
                            ticket=str(row["ticket"]),
                            exit_price=exit_px,
                            exit_time=exit_iso,
                            pnl=round(float(pnl_est), 4),
                            entry_price=row["entry_price"],
                            sl=row["sl"],
                            tp=row["tp"],
                            volume=row["volume"],
                            entry_ts=row["ts"],
                            risk_amount=float(row["risk_amount"])
                            if row["risk_amount"]
                            else None,
                        )
                        log.info(
                            "[MONITOR] Bybit outcome: %s ticket=%s pnl=%s",
                            row.get("pair"),
                            row.get("ticket"),
                            round(float(pnl_est), 4),
                        )
                except Exception as e:
                    log.debug(
                        f"[MONITOR] Bybit outcome failed for {row.get('pair')}: {e}"
                    )

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
            return jsonify(
                {
                    "total_trades": 0,
                    "message": "No completed trades yet",
                    "execution_quality": {
                        "trades_with_slippage": 0,
                        "median_abs_slippage_bps": None,
                        "mean_abs_slippage_bps": None,
                    },
                    "attribution_by_source": {},
                }
            )

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

        # Execution quality — adverse slippage magnitude at entry (Bybit/MT5 when captured)
        slip_vals = [
            abs(float(t["slippage_bps"]))
            for t in trades
            if t.get("slippage_bps") is not None
        ]
        execution_quality = {
            "trades_with_slippage": len(slip_vals),
            "median_abs_slippage_bps": None,
            "mean_abs_slippage_bps": None,
        }
        if slip_vals:
            slip_sorted = sorted(slip_vals)
            mid = len(slip_sorted) // 2
            if len(slip_sorted) % 2:
                med_slip = slip_sorted[mid]
            else:
                med_slip = (slip_sorted[mid - 1] + slip_sorted[mid]) / 2.0
            execution_quality["median_abs_slippage_bps"] = round(med_slip, 2)
            execution_quality["mean_abs_slippage_bps"] = round(
                sum(slip_vals) / len(slip_vals), 2
            )

        # Attribution by coarse execution / grade bucket (closed trades only)
        att_raw: dict = defaultdict(lambda: {"n": 0, "w": 0, "r_sum": 0.0})
        for t in trades:
            g = str(t.get("grade") or "UNKNOWN").upper()
            if g.startswith("AUTO"):
                bucket = "AUTO_ERR" if "ERR" in g else "AUTO"
            elif "MANUAL" in g and "ERR" in g:
                bucket = "MANUAL_ERR"
            elif g == "WEBHOOK":
                bucket = "WEBHOOK"
            elif g == "EXECUTED":
                bucket = "MANUAL_EXEC"
            else:
                bucket = "OTHER"
            att_raw[bucket]["n"] += 1
            if (t.get("pnl") or 0) > 0:
                att_raw[bucket]["w"] += 1
            att_raw[bucket]["r_sum"] += float(t.get("r_multiple") or 0)
        attribution_by_source = {}
        for b, v in att_raw.items():
            if v["n"] > 0:
                attribution_by_source[b] = {
                    "trades": v["n"],
                    "win_rate_pct": round(v["w"] / v["n"] * 100, 1),
                    "total_r": round(v["r_sum"], 2),
                }

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
                "execution_quality": execution_quality,
                "attribution_by_source": attribution_by_source,
            }
        )

    except Exception as e:
        log.error(f"api_performance error: {e}")

        return jsonify({"error": str(e)}), 500


# Microstructure live cache — populated by WS feed callbacks, keyed by symbol (e.g. "BTCUSDT")
_micro_cache: dict = {}
_ws_clients: list = []  # WS client instances for graceful shutdown


@app.route("/api/microstructure-health")
def api_microstructure_health():
    """Feed freshness for crypto microstructure WS (operational dashboard)."""
    now = time.time()
    enabled = bool(CONFIG.get("MICROSTRUCTURE_FEEDS_ENABLED"))
    rows = []
    for sym, data in _micro_cache.items():
        if not isinstance(data, dict):
            continue
        ts = data.get("_updated_ts")
        age = round(now - ts, 1) if ts is not None else None
        rows.append(
            {
                "symbol": sym,
                "age_sec": age,
                "stale": age is None or age > 45.0,
                "order_book_imbalance": data.get("order_book_imbalance"),
                "liquidity_pressure": data.get("liquidity_pressure"),
            }
        )
    rows.sort(key=lambda r: r["symbol"])
    return jsonify(
        _json_safe({"feeds_enabled": enabled, "symbol_count": len(rows), "symbols": rows})
    )


@app.route("/api/shadow-signals")
def api_shadow_signals():
    """Recent Engine C shadow ledger rows (requires SHADOW_LEDGER_ENABLED + scans)."""
    try:
        lim = int(request.args.get("limit", 50))
    except (TypeError, ValueError):
        lim = 50
    lim = max(1, min(lim, 200))
    try:
        with sqlite3.connect(_AUDIT_DB, timeout=15.0) as con:
            con.row_factory = sqlite3.Row
            q = con.execute(
                "SELECT * FROM shadow_signals ORDER BY id DESC LIMIT ?",
                (lim,),
            ).fetchall()
        return jsonify(_json_safe({"signals": [dict(r) for r in q]}))
    except Exception as e:
        log.error(f"api_shadow_signals: {e}")
        return jsonify({"error": str(e)}), 500


from types import SimpleNamespace  # noqa: E402

from athena_runtime import set_runtime  # noqa: E402
from execution import register_execution_routes  # noqa: E402

set_runtime(
    SimpleNamespace(
        CONFIG=CONFIG,
        AUDIT_DB=_AUDIT_DB,
        log=log,
        ALL_PAIRS=ALL_PAIRS,
        disabled_pairs=_disabled_pairs,
        scan_lock=_scan_lock,
        kill_switch=lambda: _kill_switch,
        test_mode=lambda: _test_mode,
        analyze_pair=analyze_pair,
        fetch_candles=fetch_candles,
        fetch_eodhd=fetch_eodhd,
        extract_candles=_extract_candles,
        merge_forex_forming_ws=_merge_forex_forming_ws,
        current_btc_bias=_current_btc_bias,
        resolve_scan_style=_resolve_scan_style,
        normalize_style=_normalize_style,
        naked_scan_style_profile=_naked_scan_style_profile,
        engine_b_regime_label=_engine_b_regime_label,
        insert_shadow_from_engine_c=_insert_shadow_from_engine_c,
        resolve_pair_from_signal=_resolve_pair_from_signal,
        calc_indicators_with_normalized=calc_indicators_with_normalized,
        calc_indicators=calc_indicators,
        atr_for_levels=_atr_for_levels,
        calc_levels=calc_levels,
        fetch_news_context=fetch_news_context,
        fetch_yield_curve=fetch_yield_curve,
        fetch_div_split_context=fetch_div_split_context,
        fetch_upcoming_earnings_context=fetch_upcoming_earnings_context,
        fetch_eodhd_indicators=fetch_eodhd_indicators,
        JSE_PAIRS=JSE_PAIRS,
        fetch_binance=fetch_binance,
        fetch_binance_paginated=fetch_binance_paginated,
        fetch_eodhd_intraday_bt=_fetch_eodhd_intraday_bt,
        fetch_bt_yfinance=_fetch_bt_yfinance,
        polygon_ticker_for_pair=_polygon_ticker_for_pair,
        yfinance_symbol_for_pair=_yfinance_symbol_for_pair,
        max_score_for_pair=_max_score_for_pair,
        NON_WS_EODHD=_NON_WS_EODHD,
        CRYPTO_PAIRS=CRYPTO_PAIRS,
        eodhd_ticker_for_pair=_eodhd_ticker_for_pair,
        get_eodhd_client=_get_eodhd_client,
    )
)
register_execution_routes(app)


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

        log.warning(
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

        _eodhd_pairs = [p for p in ACTIVE_PAIRS if p.get("source") == "eodhd"]
        _ws_mgr.start(_eodhd_pairs)

        set_candle_builder(CandleBuilder())

        def _cb_startup():

            cb = get_candle_builder()

            _seed_pairs = [p for p in ALL_PAIRS if p.get("source") == "eodhd"]
            cb.seed(_seed_pairs)  # seed 6mo H1/H4/D1

            cb.bulk_update_d1()  # fresh D1 from Bulk API

            cb.start_refresh_loop()  # bulk D1 every 4h

        threading.Thread(target=_cb_startup, daemon=True, name="candle-seed").start()

        log.info("[CB] Candle builder started (WS ticks + 6mo seed + Bulk D1 for EODHD sources)")

    else:
        log.warning("[WS] No EODHD_KEY â€” WebSocket prices disabled")

    # Start Binance Futures WebSocket for crypto live prices + kline candles
    crypto_enabled = [p for p in CRYPTO_PAIRS if p.get("enabled", True)]
    if crypto_enabled:
        _binance_ws = BinanceLivePriceWS()
        _binance_ws.start()
        _binance_candle_ws = BinanceCandleWS()
        _binance_candle_ws.start()
        log.info(f"[BINANCE-WS] Started price + kline feeds for {len(crypto_enabled)} enabled crypto pairs")
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
                    incoming_exchange = str(metrics.get("exchange", "")).lower()
                    now_ts = time.time()
                    existing = _micro_cache.get(sym, {})
                    existing_exchange = str(existing.get("_exchange", "")).lower()
                    existing_age = now_ts - float(existing.get("_updated_ts", 0) or 0)

                    # Prefer Binance for crypto microstructure because the candle/live-price
                    # path also uses Binance Futures. Fall back to Bybit only when the Binance
                    # slot is absent or stale.
                    use_update = False
                    if incoming_exchange == "binance":
                        use_update = True
                    elif not existing:
                        use_update = True
                    elif existing_exchange == "binance" and existing_age > 5.0:
                        use_update = True
                    elif existing_exchange != "binance":
                        use_update = True

                    if not use_update:
                        return

                    _micro_cache[sym] = {
                        k: metrics.get(k)
                        for k in (
                            "order_book_imbalance",
                            "orderflow_delta",
                            "liquidity_wall_detection",
                            "liquidity_pressure",
                        )
                    }
                    _micro_cache[sym]["_updated_ts"] = now_ts
                    _micro_cache[sym]["_exchange"] = incoming_exchange

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

        # Stop Binance candle WS
        try:
            _binance_candle_ws.stop()
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

    # Start Telegram bot (two-way command centre)
    try:
        from telegram_bot import start_telegram_bot
        start_telegram_bot()
    except Exception as e:
        log.warning(f"[TELEGRAM] Bot startup failed: {e}")

    # Clean Ctrl-C shutdown on Windows — daemon threads stop automatically
    import signal as _signal
    def _shutdown_handler(sig, frame):
        log.info("[SHUTDOWN] Ctrl-C received — stopping Sentinel Pro...")
        import os as _os
        _os._exit(0)
    _signal.signal(_signal.SIGINT, _shutdown_handler)
    _signal.signal(_signal.SIGTERM, _shutdown_handler)

    app.run(host=_host, port=5000, debug=False, use_reloader=False)
