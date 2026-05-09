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
import copy
import webbrowser
import logging
import logging.handlers
import sqlite3
import signal as _signal

# Load .env BEFORE importing any module that reads env vars at import time
# (telegram_notify starts a background thread on import that calls _load_config())
try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:
    pass  # dotenv optional - falls back to os.environ

import telegram_notify

from datetime import datetime, timezone, timedelta

from flask import Flask, jsonify, request
from athena_app.api.routes_scan import api_scan_impl
from athena_app.api.routes_backtest import api_backtest_impl
from athena_app.api.routes_execution import normalize_pip_mode
from athena_app.services.scan_backtest_service import (
    handle_scan_request,
    handle_backtest_request,
)
from athena_app.services.candle_service import recompute_levels_for_style
from athena_app.repositories.audit_repo import insert_manual_error
from stability_monitor import (
    get_signal_stability_index,
    init_stability_store,
    record_outcome_event,
    record_signal_event,
)
from advisory_thresholds import (
    ensure_advisory_store,
    find_pending_recommendation,
    get_recommendation_snapshot,
    record_action,
)
from lottery_service import ensure_lottery_schema
from lottery_engine import set_lottery_db_path

from data_feeds import (  # noqa: E402
    http_requests,
    _get_eodhd_client,
    _fetch_funding_rate,
    _fetch_bybit_funding_rate,
    _fetch_bybit_klines,
    _fetch_bybit_open_interest,
    _fetch_open_interest,
    _calc_oi_divergence,
)
from eodhd_volume_overlay import (  # noqa: E402
    eodhd_commodity_ticker_for_pair as _eodhd_commodity_ticker_for_pair,
    is_eodhd_volume_whitelisted as _is_eodhd_volume_whitelisted,
    overlay_candle_volumes as _overlay_candle_volumes,
    resample_eodhd_volume_bars as _resample_eodhd_volume_bars,
    supports_eodhd_volume_overlay as _supports_eodhd_volume_overlay,
)
from eodhd_volume_batch import (  # noqa: E402
    build_non_ws_stock_pairs,
    start_live_v2_batcher_for_non_ws_stocks,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)

_log_file_path = os.path.join(os.path.dirname(__file__), "logs", "sentinel.log")
os.makedirs(os.path.dirname(_log_file_path), exist_ok=True)
_file_handler = logging.handlers.RotatingFileHandler(
    _log_file_path, maxBytes=20 * 1024 * 1024, backupCount=5, encoding="utf-8"
)
_file_handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s"))
_file_handler.setLevel(logging.INFO)
logging.getLogger().addHandler(_file_handler)

log = logging.getLogger("sentinel")

# Silence noisy HTTP and library loggers - reduces console flood
import logging as _logging
_logging.getLogger("werkzeug").setLevel(_logging.WARNING)
_logging.getLogger("urllib3").setLevel(_logging.WARNING)
_logging.getLogger("urllib3.connectionpool").setLevel(_logging.ERROR)
_logging.getLogger("requests").setLevel(_logging.WARNING)
_logging.getLogger("httpx").setLevel(_logging.WARNING)

log.setLevel(logging.WARNING)
_scl = os.environ.get("SENTINEL_CONSOLE_LEVEL", "").strip().upper()
if _scl in {"DEBUG", "INFO", "WARNING", "ERROR"}:
    log.setLevel(getattr(logging, _scl))

from candles_cache import (  # noqa: E402
    _candle_cache,
    _candle_cache_lock,
    extract_candles as _extract_candles,
    fetch_candles as _fetch_candles_routed,
    forex_h4_resample_offset_hours as _forex_h4_resample_offset_hours,
    get_candle_fetch_meta as _get_candle_fetch_meta,
    merge_forex_forming_ws as _merge_forex_forming_ws_core,
    resample_from_h1 as _resample_from_h1,
)


_last_scan_results: dict = {"signals": []}  # latest run_full_scan output for chart-analysis context
_engine_b_cache: dict = {}  # sid/symbol -> naked analysis result dict
_ENGINE_B_CACHE_TTL = 300.0
_eodhd_volume_cache: dict = {}
_eodhd_volume_cache_lock = threading.Lock()
_EODHD_VOLUME_TTL = {"H1": 55 * 60, "H4": 235 * 60, "D1": 23 * 3600}


def _engine_b_cache_put(key: str, value: dict) -> None:
    if key and value:
        _engine_b_cache[key] = {"data": value, "ts": time.time()}


def _engine_b_cache_get(key: str) -> dict | None:
    if not key:
        return None
    entry = _engine_b_cache.get(key)
    if not entry:
        return None
    # Backward-compatible fallback if older plain dict entries already exist in memory.
    if not isinstance(entry, dict) or "ts" not in entry or "data" not in entry:
        return entry
    if (time.time() - float(entry["ts"])) > _ENGINE_B_CACHE_TTL:
        _engine_b_cache.pop(key, None)
        return None
    return entry.get("data")


from candle_feeds import (  # noqa: E402
    BinanceCandleWS,
    BinanceLivePriceWS,
    CandleBuilder,
    EODHDWebSocketManager,
    _live_prices,
    _live_prices_lock,
    _run_binance_price_poller,
    _run_mt5_tick_poller,
    fetch_candles_live,
    get_candle_builder,
    resolve_binance_kline_ws_intervals,
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

from config import (
    CONFIG,
    AITemperatureConfig,
    ai_key_configured,
    create_ai_client,
    get_ai_provider_label,
    get_ai_api_key,
    get_ai_base_url,
    get_ai_max_retries,
    get_ai_model,
    get_ai_timeout_sec,
    scan_candle_limits,
)  # noqa: E402
from athena.datafeeds.ws_ssl import configure_process_ca_bundle  # noqa: E402
from ai_safe_wrappers import ai_call_with_safe_default  # noqa: E402
from ai_signal_trace import ensure_trace_id  # noqa: E402
from prompt_versions import get_prompt_version  # noqa: E402
from regime_shift_monitor import classify_position as _regime_classify  # noqa: E402

_ca_bundle = configure_process_ca_bundle()
if _ca_bundle:
    log.info("[WS-SSL] CA bundle configured: %s", _ca_bundle)
os.environ["ATHENA_MARKET_DATA_WS_SSL_VERIFY"] = (
    "true" if CONFIG.get("MARKET_DATA_WS_SSL_VERIFY", True) else "false"
)


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
    },  # SQN +0.16 - enabled; score gate filters low-conviction setups
    {
        "symbol": "GBPUSD=X",
        "type": "forex",
        "display": "GBP/USD",
        "source": "mt5",
        "enabled": True,
    },  # SQN -1.62 (post-fix BT 2026-03-13): 7 trades/730d, WR 14%, OOS:-10 - no edge confirmed # re-enabled for ATR-fix retest
    {
        "symbol": "USDJPY=X",
        "type": "forex",
        "display": "USD/JPY",
        "source": "mt5",
        "enabled": True,
    },  # SQN -2.33 - enabled; re-evaluate post-formula fix
    {
        "symbol": "AUDUSD=X",
        "type": "forex",
        "display": "AUD/USD",
        "source": "mt5",
        "enabled": True,
    },  # SQN +1.00, WR 53.3%, IS:+0.45/OOS:+0.88 (2026-03-15 confirmed)
    {
        "symbol": "AUDCHF=X",
        "type": "forex",
        "display": "AUD/CHF",
        "source": "mt5",
        "enabled": True,
    },  # Pepperstone MT5 verified 2026-04-03; Athena edge not yet audited
    {
        "symbol": "AUDNZD=X",
        "type": "forex",
        "display": "AUD/NZD",
        "source": "mt5",
        "enabled": True,
    },  # Pepperstone MT5 verified 2026-04-03; Athena edge not yet audited
    {
        "symbol": "NZDUSD=X",
        "type": "forex",
        "display": "NZD/USD",
        "source": "mt5",
        "enabled": True,
    },  # SQN 0.00 - enabled; zero trades was data gap not signal failure
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
        "source": "mt5",
        "enabled": True,
    },  # SQN +0.27
    {
        "symbol": "USDCHF=X",
        "type": "forex",
        "display": "USD/CHF",
        "source": "mt5",
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
    },  # SQN -1.43 (old look-ahead bias) - re-evaluate post-formula fix
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
    {
        "symbol": "USDBRL=X",
        "type": "forex",
        "display": "USD/BRL",
        "source": "mt5",
        "enabled": True,
    },  # Pepperstone MT5 verified 2026-04-03; Athena edge not yet audited
    {
        "symbol": "USDINR=X",
        "type": "forex",
        "display": "USD/INR",
        "source": "mt5",
        "enabled": True,
    },  # Pepperstone MT5 verified 2026-04-03; Athena edge not yet audited
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
    },  # SQN -0.07 - DISABLE (borderline, no edge confirmed)
    {
        "symbol": "CL=F",
        "type": "commodity",
        "display": "WTI Oil",
        "source": "mt5",
        "enabled": True,
    },  # SQN not retested with correct ATR - monitor only
    {
        "symbol": "BZ=F",
        "type": "commodity",
        "display": "Brent Oil",
        "source": "mt5",
        "enabled": True,
    },  # Pepperstone: SPOTBRENT
    {
        "symbol": "NatGas",
        "type": "commodity",
        "display": "Nat Gas",
        "source": "mt5",
        "enabled": True,
    },  # MT5: NatGas
    {
        "symbol": "Copper",
        "type": "commodity",
        "display": "Copper",
        "source": "mt5",
        "enabled": True,
    },  # MT5: Copper
    {
        "symbol": "Aluminium",
        "type": "commodity",
        "display": "Aluminium",
        "source": "mt5",
        "enabled": True,
    },  # Pepperstone MT5 verified 2026-04-03; Athena edge not yet audited
    {
        "symbol": "Lead",
        "type": "commodity",
        "display": "Lead",
        "source": "mt5",
        "enabled": True,
    },  # Pepperstone MT5 verified 2026-04-03; Athena edge not yet audited
    {
        "symbol": "Nickel",
        "type": "commodity",
        "display": "Nickel",
        "source": "mt5",
        "enabled": True,
    },  # Pepperstone MT5 verified 2026-04-03; Athena edge not yet audited
    {
        "symbol": "Zinc",
        "type": "commodity",
        "display": "Zinc",
        "source": "mt5",
        "enabled": True,
    },  # Pepperstone MT5 verified 2026-04-03; Athena edge not yet audited
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
    {
        "symbol": "Gasoline",
        "type": "commodity",
        "display": "Gasoline",
        "source": "mt5",
        "enabled": True,
    },  # Pepperstone MT5 verified 2026-04-03; Athena edge not yet audited
    {
        "symbol": "Cattle",
        "type": "commodity",
        "display": "Cattle",
        "source": "mt5",
        "enabled": True,
    },  # Pepperstone MT5 verified 2026-04-03; Athena edge not yet audited
    {
        "symbol": "Cocoa",
        "type": "commodity",
        "display": "Cocoa",
        "source": "mt5",
        "enabled": True,
    },  # Pepperstone MT5 verified 2026-04-03; Athena edge not yet audited
    {
        "symbol": "Coffee",
        "type": "commodity",
        "display": "Coffee",
        "source": "mt5",
        "enabled": True,
    },  # Pepperstone MT5 verified 2026-04-03; Athena edge not yet audited
    {
        "symbol": "Corn",
        "type": "commodity",
        "display": "Corn",
        "source": "mt5",
        "enabled": True,
    },  # Pepperstone MT5 verified 2026-04-03; Athena edge not yet audited
    {
        "symbol": "Cotton",
        "type": "commodity",
        "display": "Cotton",
        "source": "mt5",
        "enabled": True,
    },  # Pepperstone MT5 verified 2026-04-03; Athena edge not yet audited
    {
        "symbol": "Soybeans",
        "type": "commodity",
        "display": "Soybeans",
        "source": "mt5",
        "enabled": True,
    },  # Pepperstone MT5 verified 2026-04-03; Athena edge not yet audited
    {
        "symbol": "Sugar",
        "type": "commodity",
        "display": "Sugar",
        "source": "mt5",
        "enabled": True,
    },  # Pepperstone MT5 verified 2026-04-03; Athena edge not yet audited
    {
        "symbol": "Wheat",
        "type": "commodity",
        "display": "Wheat",
        "source": "mt5",
        "enabled": True,
    },  # Pepperstone MT5 verified 2026-04-03; Athena edge not yet audited
]

INDEX_PAIRS = [
    {
        "symbol": "NAS100",
        "type": "index",
        "display": "NASDAQ-100",
        "source": "mt5",
        "enabled": True,
        "ws": False,
    },  # MT5: NAS100
    {
        "symbol": "^GSPC",
        "type": "index",
        "display": "S&P 500",
        "source": "mt5",
        "enabled": True,
    },  # SQN +1.23 WR 60.0% (10T) <- WS: us endpoint GSPC.INDX
    {
        "symbol": "^DJI",
        "type": "index",
        "display": "Dow Jones",
        "source": "mt5",
        "enabled": True,
    },  # SQN +0.61 WR 55.6% (9T) <- WS: us endpoint DJI.INDX
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
    },  # SQN +0.67 WR 50.0% (12T) Pepperstone: UK100 <- WS: forex ep
    {
        "symbol": "^AXJO",
        "type": "index",
        "display": "ASX 200",
        "source": "mt5",
        "enabled": True,
    },  # SQN +0.12 WR 40.0% (10T) Pepperstone: AUS200 <- WS: forex ep
    {
        "symbol": "^N225",
        "type": "index",
        "display": "Nikkei 225",
        "source": "mt5",
        "enabled": True,
    },  # SQN -0.39 WR 33.3% (9T) Pepperstone: JPN225 <- WS: forex ep
    {
        "symbol": "^HSI",
        "type": "index",
        "display": "Hang Seng",
        "source": "mt5",
        "enabled": True,
    },  # SQN -0.38 WR 33.3% (9T) Pepperstone: HK50 <- WS: forex ep
    {
        "symbol": "EURX",
        "type": "index",
        "display": "EURX",
        "source": "mt5",
        "enabled": True,
    },  # Pepperstone MT5 verified 2026-04-03; Athena edge not yet audited
    {
        "symbol": "JPYX",
        "type": "index",
        "display": "JPYX",
        "source": "mt5",
        "enabled": True,
    },  # Pepperstone MT5 verified 2026-04-03; Athena edge not yet audited
    {
        "symbol": "USDX",
        "type": "index",
        "display": "USDX",
        "source": "mt5",
        "enabled": True,
    },  # Pepperstone MT5 verified 2026-04-03; Athena edge not yet audited
]

US_STOCK_PAIRS = [
    {
        "symbol": "AAPL.US",
        "type": "stock",
        "display": "AAPL",
        "source": "mt5",
        "enabled": True,
    },  # SQN -0.30 - score gate filters
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
        "symbol": "DIA.US",
        "type": "stock",
        "display": "DIA",
        "source": "mt5",
        "enabled": True,
    },  # Pepperstone MT5 verified 2026-04-03
    {
        "symbol": "GDX.US",
        "type": "stock",
        "display": "GDX",
        "source": "mt5",
        "enabled": True,
    },  # Pepperstone MT5 verified 2026-04-03
    {
        "symbol": "SOXX.US",
        "type": "stock",
        "display": "SOXX",
        "source": "mt5",
        "enabled": True,
    },  # Pepperstone MT5 verified 2026-04-03
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
        "symbol": "POLUSDT",
        "type": "crypto",
        "display": "POL/USDT",
        "source": "binance",
        "enabled": True,
    },  # Replaces delisted MATICUSDT on Binance/Bybit (verified 2026-04-03)
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
    },  # SQN +0.76, IS:+0.43/OOS:+0.82 ✓"
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
    },  # SQN +0.67, IS:+0.56/OOS:+0.37 ✓"
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
    {
        "symbol": "AAVEUSDT",
        "type": "crypto",
        "display": "AAVE/USDT",
        "source": "binance",
        "enabled": True,
    },  # Binance + Bybit USDT perpetual verified 2026-04-03; Athena edge not yet audited
    {
        "symbol": "ALGOUSDT",
        "type": "crypto",
        "display": "ALGO/USDT",
        "source": "binance",
        "enabled": True,
    },  # Binance + Bybit USDT perpetual verified 2026-04-03; Athena edge not yet audited
    {
        "symbol": "ATOMUSDT",
        "type": "crypto",
        "display": "ATOM/USDT",
        "source": "binance",
        "enabled": True,
    },  # Binance + Bybit USDT perpetual verified 2026-04-03; Athena edge not yet audited
    {
        "symbol": "BCHUSDT",
        "type": "crypto",
        "display": "BCH/USDT",
        "source": "binance",
        "enabled": True,
    },  # Binance + Bybit USDT perpetual verified 2026-04-03; Athena edge not yet audited
    {
        "symbol": "ETCUSDT",
        "type": "crypto",
        "display": "ETC/USDT",
        "source": "binance",
        "enabled": True,
    },  # Binance + Bybit USDT perpetual verified 2026-04-03; Athena edge not yet audited
    {
        "symbol": "TRXUSDT",
        "type": "crypto",
        "display": "TRX/USDT",
        "source": "binance",
        "enabled": True,
    },  # Binance + Bybit USDT perpetual verified 2026-04-03; Athena edge not yet audited
    {
        "symbol": "XLMUSDT",
        "type": "crypto",
        "display": "XLM/USDT",
        "source": "binance",
        "enabled": True,
    },  # Binance + Bybit USDT perpetual verified 2026-04-03; Athena edge not yet audited
    {
        "symbol": "UNIUSDT",
        "type": "crypto",
        "display": "UNI/USDT",
        "source": "binance",
        "enabled": True,
    },  # Binance + Bybit USDT perpetual verified 2026-04-03; Athena edge not yet audited
    {
        "symbol": "FILUSDT",
        "type": "crypto",
        "display": "FIL/USDT",
        "source": "binance",
        "enabled": True,
    },  # Binance + Bybit USDT perpetual verified 2026-04-03; Athena edge not yet audited
    {
        "symbol": "ICPUSDT",
        "type": "crypto",
        "display": "ICP/USDT",
        "source": "binance",
        "enabled": True,
    },  # Binance + Bybit USDT perpetual verified 2026-04-03; Athena edge not yet audited
    {
        "symbol": "HBARUSDT",
        "type": "crypto",
        "display": "HBAR/USDT",
        "source": "binance",
        "enabled": True,
    },  # Binance + Bybit USDT perpetual verified 2026-04-03; Athena edge not yet audited
    {
        "symbol": "ARBUSDT",
        "type": "crypto",
        "display": "ARB/USDT",
        "source": "binance",
        "enabled": True,
    },  # Binance + Bybit USDT perpetual verified 2026-04-03; Athena edge not yet audited
    {
        "symbol": "OPUSDT",
        "type": "crypto",
        "display": "OP/USDT",
        "source": "binance",
        "enabled": True,
    },  # Binance + Bybit USDT perpetual verified 2026-04-03; Athena edge not yet audited
    {
        "symbol": "SEIUSDT",
        "type": "crypto",
        "display": "SEI/USDT",
        "source": "binance",
        "enabled": True,
    },  # Binance + Bybit USDT perpetual verified 2026-04-03; Athena edge not yet audited
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

# Pairs that opted out of WS - polled via REST every 60s
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

TF_B = {"D1": "1d", "H4": "4h", "H1": "1h", "M15": "15m", "M5": "5m", "M1": "1m"}

_YF_INTRADAY_PERIOD = "180d"

_VENDOR_SYMBOL_OVERRIDES = {
    # Precious metals: all 4 fully supported on EODHD intraday (1m bars since ~2009)
    # OTC spot market volume included in every bar
    "XAU/USD": {"eodhd": "XAUUSD.FOREX", "eodhd_intraday": "XAUUSD.FOREX", "polygon": "C:XAUUSD"},
    "XAG/USD": {"eodhd": "XAGUSD.FOREX", "eodhd_intraday": "XAGUSD.FOREX", "polygon": "C:XAGUSD"},
    "XPT/USD": {"eodhd": "XPTUSD.FOREX", "eodhd_intraday": "XPTUSD.FOREX", "polygon": "C:XPTUSD", "yfinance": "PL=F"},
    "XPD/USD": {"eodhd": "XPDUSD.FOREX", "eodhd_intraday": "XPDUSD.FOREX", "polygon": "C:XPDUSD", "fallback": "yfinance"},
    # Energy / base metals: EODHD intraday for oil/gas; industrial metals D1 only
    "WTI Oil": {"yfinance": "CL=F", "eodhd": "CL", "fallback": "yfinance"},
    "Brent Oil": {"yfinance": "BZ=F", "eodhd": "BZ", "fallback": "yfinance"},
    "Nat Gas": {"yfinance": "NG=F", "eodhd": "NG", "fallback": "yfinance"},
    # COPPER/ALUMINUM: no valid EODHD EOD ticker - falls back to yfinance
    "Copper": {"yfinance": "HG=F", "eodhd": "", "fallback": "yfinance"},
    "Aluminium": {"yfinance": "ALI=F", "eodhd": "", "fallback": "yfinance"},
    # Agricultural commodities: COFFEE/COTTON/SUGAR/WHEAT have no valid EODHD EOD ticker
    "Coffee": {"yfinance": "KC=F", "eodhd": "", "fallback": "yfinance"},
    "Corn": {"yfinance": "ZC=F", "eodhd": "CORN", "fallback": "yfinance"},
    "Cotton": {"yfinance": "CT=F", "eodhd": "", "fallback": "yfinance"},
    "Sugar": {"yfinance": "SB=F", "eodhd": "", "fallback": "yfinance"},
    "Wheat": {"yfinance": "ZW=F", "eodhd": "", "fallback": "yfinance"},
    # Indices: EODHD EOD requires .INDX suffix (plain symbol returns 404)
    "UK100": {
        "yfinance": "^FTSE",
        "eodhd": "FTSE.INDX",
        "eodhd_intraday": "FTSE.INDX",
        "polygon": "C:UK100",
        "fallback": "yfinance",
    },
    "S&P 500": {
        "yfinance": "^GSPC",
        "eodhd": "GSPC.INDX",
        "eodhd_intraday": "GSPC.INDX",
        "fallback": "yfinance",
    },
    "Nasdaq": {
        "yfinance": "^IXIC",
        "eodhd": "IXIC.INDX",
        "eodhd_intraday": "IXIC.INDX",
        "fallback": "yfinance",
    },
    "NASDAQ-100": {
        "yfinance": "^IXIC",
        "eodhd": "IXIC.INDX",
        "eodhd_intraday": "IXIC.INDX",
        "fallback": "yfinance",
    },
    "Dow Jones": {
        "yfinance": "^DJI",
        "eodhd": "DJI.INDX",
        "eodhd_intraday": "DJI.INDX",
        "fallback": "yfinance",
    },
    "DAX 40": {
        "yfinance": "^GDAXI",
        "eodhd": "GDAXI.INDX",
        "eodhd_intraday": "GDAXI.INDX",
        "fallback": "yfinance",
    },
    "ASX 200": {
        "yfinance": "^AXJO",
        "eodhd": "AXJO.INDX",
        "eodhd_intraday": "AXJO.INDX",
        "fallback": "yfinance",
    },
    "Nikkei 225": {
        "yfinance": "^N225",
        "eodhd": "N225.INDX",
        "eodhd_intraday": "N225.INDX",
        "fallback": "yfinance",
    },
    "Hang Seng": {
        "yfinance": "^HSI",
        "eodhd": "HSI.INDX",
        "eodhd_intraday": "HSI.INDX",
        "fallback": "yfinance",
    },
    # Currency basket indices: no valid EODHD EOD ticker - falls back to MT5
    "EURX": {"eodhd": "", "fallback": "yfinance"},
    "JPYX": {"eodhd": "", "fallback": "yfinance"},
    "USDX": {"eodhd": "", "fallback": "yfinance"},
}


def _vendor_overrides(pair: dict) -> dict:

    return _VENDOR_SYMBOL_OVERRIDES.get(pair.get("display", ""), {})


def _yfinance_symbol_for_pair(pair: dict) -> str | None:

    override = pair.get("yfinanceSymbol") or _vendor_overrides(pair).get("yfinance")

    if override:
        return override

    sym = pair.get("symbol")
    if pair.get("type") == "stock" and isinstance(sym, str) and sym.endswith(".US"):
        return sym[:-3]

    return sym


def _eodhd_ticker_for_pair(pair: dict) -> str | None:
    disp = pair.get("display", "")
    ptype = pair.get("type", "")
    sym = pair.get("symbol", "")
    commodity_ticker = _eodhd_commodity_ticker_for_pair(pair)

    # Check vendor override first - highest priority
    # Empty string "" is an explicit block (pair confirmed to have no valid EODHD EOD ticker)
    override = _vendor_overrides(pair).get("eodhd")
    if ptype == "commodity" and commodity_ticker:
        return commodity_ticker
    if override is not None:
        return override if override else None

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

        if commodity_ticker:
            return commodity_ticker

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
    Uses only Polygon and yfinance.
    Returns candle list or None if all sources fail."""

    tag = f" ({reason})" if reason else ""
    disp = pair["display"]

    # 1. Polygon - good for forex/metals, rate-limited to 5 req/min on free tier
    if _polygon_ticker_for_pair(pair):
        resp = fetch_polygon(pair, tf, limit)
        candles = _extract_candles(resp)
        if candles:
            log.info(f"[FALLBACK] {disp} {tf}: using Polygon{tag}")
            return candles

    # 2. yfinance - last resort, broad coverage but lower reliability
    yf_symbol = _yfinance_symbol_for_pair(pair)
    if yf_symbol:
        log.info(f"[FALLBACK] {disp} {tf}: using yfinance{tag}")
        return fetch_yfinance(yf_symbol, tf, limit)

    return None


def _atr_for_levels(
    d1i: dict, h4i: dict, h1i: dict, pair: dict = None, style: str | None = None
):
    """Return ATR for SL/TP from ``CONFIG['LEVEL_ATR_PRIORITY']`` (per ``pair['type']`` and style).

    Order is **not** hardcoded here: ``default`` and ``crypto`` entries define
    which timeframe’s ATR is tried first for scalp (typically H1), intraday (H4),
    and swing (D1 for crypto and other classes — see ``config.py`` / ``config.yaml``).
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


def _bybit_atr_for_levels(pair: dict, style: str | None, as_of=None) -> float | None:
    """Fetch Bybit ATR for crypto execution levels when Bybit is the execution venue."""
    symbol = (pair.get("symbol") or pair.get("display") or "").replace("/", "").upper()
    if not symbol:
        return None
    resolved_style = _normalize_style(style or "swing")
    if resolved_style == "auto":
        resolved_style = "swing"
    tf = {"scalp": "H1", "intraday": "H4", "swing": "D1"}.get(resolved_style, "D1")
    end_ms = None
    if as_of is not None:
        try:
            if hasattr(as_of, "timestamp"):
                end_ms = int(as_of.timestamp() * 1000)
            else:
                end_dt = datetime.fromisoformat(str(as_of).replace("Z", "+00:00"))
                end_ms = int(end_dt.timestamp() * 1000)
        except Exception:
            end_ms = None
    candles = _fetch_bybit_klines(symbol, tf, 120, end_ms=end_ms)
    if not candles or len(candles) < 20:
        return None
    atr_series = calc_atr(
        [c["high"] for c in candles],
        [c["low"] for c in candles],
        [c["close"] for c in candles],
        14,
    )
    for value in reversed(atr_series or []):
        if value:
            return float(value)
    return None


def _max_score_for_pair(pair: dict) -> float:
    """Theoretical max score for Engine A v2.

    All asset classes use factor_scoring.py on a unified 0-3.0 scale.
    """
    return 3.0


def fetch_yfinance(sym, tf, limit):
    """Download OHLCV candles from Yahoo Finance. Returns list of candle dicts or None."""

    try:
        import yfinance as yf

        import pandas as pd

        try:
            _yf_cache_dir = Path(__file__).resolve().parent / ".yfinance-cache"
            _yf_cache_dir.mkdir(parents=True, exist_ok=True)
            if hasattr(yf, "set_tz_cache_location"):
                yf.set_tz_cache_location(str(_yf_cache_dir))
        except Exception as _yf_cache_err:
            log.debug(f"[YF] {sym}: cache setup skipped: {_yf_cache_err}")

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
            # Column-by-column resample - avoids pandas version issues with .agg(dict)

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


def _binance_futures_kline_to_candle(k):
    """Map Binance USD-M futures kline rows without dropping volume provenance."""
    return {
        "open_time": int(k[0]),
        "time": datetime.fromtimestamp(k[0] / 1000, tz=timezone.utc).isoformat(),
        "open": float(k[1]),
        "high": float(k[2]),
        "low": float(k[3]),
        "close": float(k[4]),
        "volume": float(k[5]),
        "vol": float(k[5]),
        "quote_volume": float(k[7]),
        "number_of_trades": int(k[8]),
        "taker_buy_base_volume": float(k[9]),
        "taker_buy_quote_volume": float(k[10]),
    }


def fetch_binance(sym, interval, limit):
    """Download OHLCV candles from Binance USD-M futures REST with failover."""

    try:
        for base in ["https://fapi.binance.com", "https://fapi1.binance.com"]:
            r = http_requests.get(
                f"{base}/fapi/v1/klines",
                params={"symbol": sym, "interval": interval, "limit": limit},
                timeout=15,
            )

            if r.status_code == 200:
                return [_binance_futures_kline_to_candle(k) for k in r.json()]

            log.warning(f"[BN-FUT] {sym} HTTP {r.status_code}: {r.text[:120]}")

        return None

    except Exception as e:
        log.error(f"[BN-FUT] {sym}: {e}")
        return None


def fetch_binance_paginated(sym, interval, total_bars):
    """Paginate Binance USD-M futures klines to fetch more than 1000 bars."""

    all_candles = []

    end_time = None  # None = latest

    pages = (total_bars + 999) // 1000  # ceil division

    for page in range(pages):
        try:
            params = {"symbol": sym, "interval": interval, "limit": 1000}

            if end_time:
                params["endTime"] = end_time

            for base in ["https://fapi.binance.com", "https://fapi1.binance.com"]:
                r = http_requests.get(
                    f"{base}/fapi/v1/klines", params=params, timeout=15
                )

                if r.status_code == 200:
                    data = r.json()

                    if not data:
                        break

                    batch = [_binance_futures_kline_to_candle(k) for k in data]

                    all_candles = batch + all_candles  # prepend older data

                    end_time = data[0][0] - 1  # 1ms before earliest bar

                    break

                log.warning(f"[BN-FUT-PAG] {sym} HTTP {r.status_code}: {r.text[:120]}")

            else:
                break  # both endpoints failed

            if page < pages - 1:
                time.sleep(1)

        except Exception as e:
            log.error(f"[BN-FUT-PAG] {sym} page {page}: {e}")

            break

    log.info(f"[BN-FUT-PAG] {sym} {interval}: {len(all_candles)} bars ({pages} pages)")

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
    if pair.get("type") == "commodity":
        return _eodhd_commodity_ticker_for_pair(pair)
    # Fall back to regular ticker for other types
    return _eodhd_ticker_for_pair(pair)


def _eodhd_volume_cache_key(pair: dict, tf: str, limit: int) -> tuple[str, str, int]:
    return (pair.get("symbol", pair.get("display", "")), str(tf or "").upper(), int(limit))


def _get_cached_eodhd_volume_only(pair: dict, tf: str, limit: int):
    key = _eodhd_volume_cache_key(pair, tf, limit)
    now = time.time()
    with _eodhd_volume_cache_lock:
        entry = _eodhd_volume_cache.get(key)
        if not entry:
            return None
        payload, expiry = entry
        if now >= expiry:
            _eodhd_volume_cache.pop(key, None)
            return None
        return payload


def _store_cached_eodhd_volume_only(pair: dict, tf: str, limit: int, payload: dict) -> None:
    key = _eodhd_volume_cache_key(pair, tf, limit)
    ttl = _EODHD_VOLUME_TTL.get(str(tf or "").upper(), 15 * 60)
    with _eodhd_volume_cache_lock:
        _eodhd_volume_cache[key] = (payload, time.time() + ttl)


def _fetch_eodhd_volume_only(pair: dict, tf: str, limit: int, *, cache_only: bool = False) -> dict:
    """Best-effort EODHD volume fetch for MT5-routed symbols only.

    Priority order for live scans (cache_only=True path):
      1. In-memory TTL cache (always checked first - zero latency)
      2. CandleBuilder WS bars (US stocks M1/M5/M15: near-real-time exchange volume)
      3. EODHD Intraday Historical REST (all TFs: finalized 2-3h after close - fallback)

    Backtest callers pass cache_only=False which skips the WS path and goes straight
    to REST so backtests continue to use the same historical data as before.
    This never changes OHLC routing. It is used only to overlay `vol`.
    """
    tf_key = str(tf or "").upper()
    cached = _get_cached_eodhd_volume_only(pair, tf_key, limit)
    if cached is not None:
        return cached

    display = pair.get("display", pair.get("symbol", "unknown"))
    ptype = str(pair.get("type") or "").lower()
    payload = {
        "error": True,
        "symbol": display,
        "detail": "",
        "candles": None,
        "ticker": None,
        "request_tf": tf_key,
        "source_tf": None,
        "volume_source": "cache_miss",
    }

    # --- CandleBuilder check for US stocks (M1/M5/M15/H1/H4/D1) ---
    #
    # M1-H4: CandleBuilder has data from either:
    #   - ws:True stocks: EODHD US WebSocket on_tick() accumulation (existing path)
    #   - ws:False stocks: Live v2 batch batcher on_tick() injection (eodhd_volume_batch.py)
    #   Accepted when last bar is within 2x bar-period of now (session-fresh data).
    #
    # D1: CandleBuilder has data seeded by bulk_update_d1() which runs at startup and
    #     every 4h via the Bulk EOD API. Accepted when we have >=20 bars with volume > 0.
    #     This eliminates per-pair get_eod_historical_stock_market_data() REST calls for D1.
    #
    # Forex/commodity/index: excluded - no real exchange volume in OTC ticks.
    if ptype == "stock" and tf_key in {"M1", "M5", "M15", "H1", "H4", "D1"}:
        try:
            cb = get_candle_builder()
            if cb:
                cb_candles = cb.get_candles(display, tf_key, limit)

                if tf_key == "D1":
                    # D1 path: use bulk_update_d1 seeded data from CandleBuilder
                    _min_d1_bars = 20
                    if cb_candles and len(cb_candles) >= _min_d1_bars:
                        usable_d1 = [c for c in cb_candles if float(c.get("vol", 0) or 0) > 0]
                        if usable_d1:
                            d1_payload = {
                                "error": False,
                                "symbol": display,
                                "detail": "",
                                "candles": cb_candles,
                                "ticker": display,
                                "request_tf": tf_key,
                                "source_tf": "candle_builder_d1",
                                "volume_source": "candle_builder_d1",
                            }
                            _store_cached_eodhd_volume_only(pair, tf_key, limit, d1_payload)
                            log.debug(
                                "[EODHD-VOL] %s D1: CandleBuilder used bars=%s (bulk_update_d1)",
                                display, len(cb_candles),
                            )
                            return d1_payload
                    # D1 not yet populated in CandleBuilder - fall through to REST

                else:
                    # M1-H4 path: WS ticks or Live v2 injected ticks
                    _min_bars = {"M1": 5, "M5": 5, "M15": 5, "H1": 20, "H4": 10}.get(tf_key, 5)
                    if cb_candles and len(cb_candles) >= _min_bars:
                        last_ts_str = cb_candles[-1].get("time", "")
                        if last_ts_str:
                            last_dt = datetime.fromisoformat(str(last_ts_str).replace("Z", "+00:00"))
                            if last_dt.tzinfo is None:
                                last_dt = last_dt.replace(tzinfo=timezone.utc)
                            age_s = (datetime.now(timezone.utc) - last_dt).total_seconds()
                            _tf_sec = {"M1": 60, "M5": 300, "M15": 900, "H1": 3600, "H4": 14400}.get(tf_key, 3600)
                            if age_s < _tf_sec * 2 + 30:
                                usable = [c for c in cb_candles if float(c.get("vol", 0) or 0) > 0]
                                if usable:
                                    ws_payload = {
                                        "error": False,
                                        "symbol": display,
                                        "detail": "",
                                        "candles": cb_candles,
                                        "ticker": display,
                                        "request_tf": tf_key,
                                        "source_tf": "ws_tick",
                                        "volume_source": "ws_tick",
                                    }
                                    _store_cached_eodhd_volume_only(pair, tf_key, limit, ws_payload)
                                    log.debug(
                                        "[EODHD-VOL] %s %s: CandleBuilder bars used bars=%s age_s=%.0f (Engine A/D)",
                                        display, tf_key, len(cb_candles), age_s,
                                    )
                                    return ws_payload
        except Exception as _ws_err:
            log.debug("[EODHD-VOL] %s %s CandleBuilder check failed: %s", display, tf_key, _ws_err)

    if cache_only:
        # cache_miss for a whitelisted pair - trigger background re-warm so next scan gets real data
        if _supports_eodhd_volume_overlay(pair) and _is_eodhd_volume_whitelisted(pair, tf_key):
            log.warning(
                "[EODHD-VOL] %s %s: cache_miss - volume overlay unavailable, "
                "VP will use MT5 tick-volume this scan; bg re-warm triggered",
                display, tf_key,
            )
            def _bg_rewarm(p=pair, t=tf_key, lim=limit):
                try:
                    _fetch_eodhd_volume_only(p, t, lim, cache_only=False)
                except Exception:
                    pass
            threading.Thread(target=_bg_rewarm, daemon=True, name="eodhd-vol-rewarm").start()
        payload["detail"] = "cache_miss"
        payload["volume_source"] = "cache_miss"
        return payload

    if not _supports_eodhd_volume_overlay(pair):
        payload["detail"] = "unsupported asset type"
        _store_cached_eodhd_volume_only(pair, tf_key, limit, payload)
        return payload

    # Skip EODHD REST for forex: OTC markets have no real exchange volume.
    # Whitelisted commodities/indices use EODHD as a best-effort volume overlay.
    # Return explicit flag so overlay falls back to MT5 tick-volume without wasting API calls.
    if ptype == "forex":
        payload["detail"] = "no_real_volume"
        payload["volume_source"] = "mt5_tick"
        _store_cached_eodhd_volume_only(pair, tf_key, limit, payload)
        return payload

    if tf_key not in {"M1", "M5", "M15", "H1", "H4", "D1"}:
        payload["detail"] = f"unsupported timeframe: {tf_key}"
        _store_cached_eodhd_volume_only(pair, tf_key, limit, payload)
        return payload

    if not _is_eodhd_volume_whitelisted(pair, tf_key):
        payload["detail"] = f"pair/timeframe not whitelisted: {tf_key}"
        _store_cached_eodhd_volume_only(pair, tf_key, limit, payload)
        return payload

    _key = os.environ.get("EODHD_KEY", "").strip()
    if not _key:
        payload["detail"] = "EODHD_KEY not set"
        _store_cached_eodhd_volume_only(pair, tf_key, limit, payload)
        return payload

    try:
        if tf_key == "D1":
            ticker = _eodhd_ticker_for_pair(pair)
            payload["ticker"] = ticker
            payload["source_tf"] = "D1"
            if not ticker:
                payload["detail"] = "no valid EODHD daily ticker"
                _store_cached_eodhd_volume_only(pair, tf_key, limit, payload)
                return payload

            api = _get_eodhd_client()
            if not api:
                payload["detail"] = "EODHD client unavailable"
                _store_cached_eodhd_volume_only(pair, tf_key, limit, payload)
                return payload

            days = max(90, int(limit) + 10)
            start = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%d")
            bars = api.get_eod_historical_stock_market_data(
                ticker, period="d", from_date=start, order="a"
            )
            candles = [
                {
                    "time": b["date"],
                    "open": float(b["open"]),
                    "high": float(b["high"]),
                    "low": float(b["low"]),
                    "close": float(b["close"]),
                    "vol": float(b.get("volume") or 0),
                }
                for b in (bars or [])
                if b.get("open") is not None and b.get("close") is not None
            ]
            candles = candles[-limit:] if len(candles) > limit else candles
        else:
            ticker = _eodhd_intraday_ticker_for_pair(pair)
            payload["ticker"] = ticker
            if not ticker:
                payload["detail"] = "no valid EODHD intraday ticker"
                _store_cached_eodhd_volume_only(pair, tf_key, limit, payload)
                return payload

            now_ts = int(datetime.now(timezone.utc).timestamp())
            ptype = str(pair.get("type") or "").lower()
            display_key = str(pair.get("display") or "")
            # Audit result: EODHD forex/metals had usable volume on 1m, while
            # direct 5m/1h returned zero volume. Fetch 1m and resample locally.
            use_1m_source = (
                ptype == "forex"
                or ticker.endswith(".FOREX")
                or display_key in {"XAU/USD", "XAG/USD"}
            )
            source_interval = "1m" if use_1m_source else {"M1": "1m", "M5": "5m", "M15": "5m", "H1": "1h", "H4": "1h"}.get(tf_key, "1h")
            payload["source_tf"] = source_interval
            minutes_per_bar = {"M1": 1, "M5": 5, "M15": 15, "H1": 60, "H4": 240}.get(tf_key, 60)
            source_minutes = {"1m": 1, "5m": 5, "1h": 60}.get(source_interval, 60)
            source_bars_needed = max(int(limit) * max(1, minutes_per_bar // source_minutes), int(limit))
            days = max(2, min(30 if source_interval == "1m" else 365, math.ceil((source_bars_needed * source_minutes) / 1440.0) + 3))
            from_ts = int((datetime.now(timezone.utc) - timedelta(days=days)).timestamp())
            resp = http_requests.get(
                f"https://eodhd.com/api/intraday/{ticker}",
                params={
                    "api_token": _key,
                    "fmt": "json",
                    "from": from_ts,
                    "to": now_ts,
                    "interval": source_interval,
                },
                timeout=20,
            )
            if resp.status_code != 200:
                payload["detail"] = f"HTTP {resp.status_code}"
                _store_cached_eodhd_volume_only(pair, tf_key, limit, payload)
                return payload

            bars = resp.json()
            if not isinstance(bars, list) or not bars:
                payload["detail"] = "no intraday data"
                _store_cached_eodhd_volume_only(pair, tf_key, limit, payload)
                return payload

            source_candles = [
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
            candles = _resample_eodhd_volume_bars(source_candles, tf_key, int(limit))

        usable = [c for c in (candles or []) if float(c.get("vol", 0) or 0) > 0]
        if usable:
            # Distinguish forex (~1-2 min delayed) from stocks/indices (2-3h stale during session)
            _src = "eodhd_1m" if payload.get("source_tf") == "1m" else f"eodhd_{payload.get('source_tf', 'hist')}"
            payload.update({"error": False, "detail": "", "candles": candles, "volume_source": _src})
        else:
            payload["detail"] = "no usable EODHD volume bars"
            payload["volume_source"] = "eodhd_no_vol"
    except Exception as e:
        payload["detail"] = str(e)

    _store_cached_eodhd_volume_only(pair, tf_key, limit, payload)
    return payload


def _overlay_mt5_candles_with_eodhd_volume(pair: dict, tf: str, mt5_resp: dict) -> dict:
    """Overlay EODHD volume onto MT5 candles without altering OHLC or live price source."""
    if not isinstance(mt5_resp, dict):
        return mt5_resp
    if mt5_resp.get("error"):
        return mt5_resp
    if not _supports_eodhd_volume_overlay(pair):
        return mt5_resp

    candles = _extract_candles(mt5_resp)
    if not candles:
        return mt5_resp

    tf_key = str(tf or "").upper()
    if tf_key not in {"H1", "H4", "D1"}:
        return mt5_resp

    volume_resp = _fetch_eodhd_volume_only(pair, tf_key, len(candles))
    volume_candles = _extract_candles(volume_resp)
    if not volume_candles:
        if volume_resp.get("detail"):
            log.info(
                f"[EODHD-VOL] {pair.get('display')} {tf_key}: overlay skipped - {volume_resp.get('detail')}"
            )
        return mt5_resp

    merged, matched = _overlay_candle_volumes(candles, volume_candles, tf_key)
    if matched <= 0:
        log.info(f"[EODHD-VOL] {pair.get('display')} {tf_key}: overlay found no matching bars")
        return mt5_resp

    out = dict(mt5_resp)
    out["candles"] = merged
    out["volumeOverlay"] = {
        "source": "eodhd",
        "ticker": volume_resp.get("ticker"),
        "source_tf": volume_resp.get("source_tf"),
        "matched": matched,
        "requested": len(candles),
    }
    log.info(
        f"[EODHD-VOL] {pair.get('display')} {tf_key}: applied EODHD volume to {matched}/{len(candles)} candles"
    )
    return out


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
            if pair.get("source") == "mt5" and pair.get("type") == "commodity":
                log.info(
                    f"[EODHD-BT] {pair['display']}: no EODHD intraday override - using MT5/fallback history"
                )
            else:
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
    """Download OHLCV candles via EODHD SDK (APIClient). Covers forex, stocks, indices - 1000 req/min.

    Returns dict with standardized error format."""
    global _eodhd_cooldown_until

    symbol = pair.get("display", pair.get("symbol", "unknown"))

    # Fast-fail if EODHD recently returned 402/429; never sleep in scan worker paths.
    if time.time() < _eodhd_cooldown_until:
        fallback = _fetch_fallback_candles(pair, tf, limit, reason="EODHD cooldown (402)")
        if fallback:
            return {"error": True, "symbol": symbol, "detail": "rate_limited", "candles": fallback}
        return {"error": True, "symbol": symbol, "detail": "rate_limited"}

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

            # Never sleep in scan worker paths; return immediately on transient/rate-limit failures.
            bars = None
            for attempt in range(1, 4):
                try:
                    bars = api.get_eod_historical_stock_market_data(
                        ticker, period="d", from_date=start, order="a"
                    )
                    break
                except Exception as e:
                    if "402" in str(e) or "429" in str(e) or "Payment Required" in str(e):
                        log.warning(f"[EODHD] {ticker} D1: 402/429 - cooldown 10 min")
                        _eodhd_cooldown_until = time.time() + 600
                        return {"error": True, "symbol": symbol, "detail": "rate_limited"}
                    if attempt == 3:
                        log.warning(f"[EODHD] {ticker} D1 failed after 3 attempts: {e}")
                        raise
                    log.warning(
                        f"[EODHD] {ticker} D1 attempt {attempt} failed - fast-fail without retry sleep: {e}"
                    )
                    return {"error": True, "symbol": symbol, "detail": "rate_limited"}

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

            # Never sleep in scan worker paths; return immediately on transient/rate-limit failures.
            bars = None
            for attempt in range(1, 4):
                try:
                    bars = api.get_intraday_historical_data(
                        ticker, interval="1h", from_unix_time=start_ts
                    )
                    break
                except Exception as e:
                    if "402" in str(e) or "429" in str(e) or "Payment Required" in str(e):
                        log.warning(f"[EODHD] {ticker} intraday: 402/429 - cooldown 10 min")
                        _eodhd_cooldown_until = time.time() + 600
                        return {"error": True, "symbol": symbol, "detail": "rate_limited"}
                    if attempt == 3:
                        log.warning(f"[EODHD] {ticker} intraday failed after 3 attempts: {e}")
                        raise
                    log.warning(
                        f"[EODHD] {ticker} intraday attempt {attempt} failed - fast-fail without retry sleep: {e}"
                    )
                    return {"error": True, "symbol": symbol, "detail": "rate_limited"}

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

        # Pre-request throttle: minimum interval between Polygon HTTP calls. Never sleep here
        # (would starve scan thread pool); return 429-style payload for fetch_candles fallback.
        elapsed = time.time() - _polygon_last_request
        if elapsed < _POLYGON_MIN_INTERVAL:
            _polygon_throttle_reject = True
        else:
            _polygon_throttle_reject = False
            _polygon_last_request = time.time()

    if _polygon_throttle_reject:
        log.warning(
            f"[PG] {symbol}: client throttle - min interval {_POLYGON_MIN_INTERVAL}s not elapsed"
        )
        return {
            "error": True,
            "symbol": symbol,
            "detail": "HTTP 429 rate limited",
        }

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
            log.warning(f"[PG] {ticker}: 403 Forbidden - check API key or plan")

            return {
                "error": True,
                "symbol": symbol,
                "detail": "403 Forbidden - check API key or plan",
            }

        if r.status_code == 429:
            log.warning(
                f"[PG] {ticker}: 429 rate limited - throttle will apply before next request"
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

    def _mt5_fallback(detail: str):
        fallback = _fetch_fallback_candles(pair, tf, limit, reason=detail)
        if fallback:
            return {"error": True, "symbol": symbol, "detail": detail, "candles": fallback}
        return {"error": True, "symbol": symbol, "detail": detail}

    if not mt5_executor.mt5_connect():
        return _mt5_fallback("MT5 not connected")

    mt5 = mt5_executor._get_mt5()
    mt5_symbol = mt5_executor.mt5_map_symbol(symbol)

    if not mt5_symbol:
        return _mt5_fallback("no MT5 symbol mapping")

    if not mt5.symbol_select(mt5_symbol, True):
        return _mt5_fallback("symbol not found in MT5")

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
        return _mt5_fallback(f"MT5 failed: {err}")

    # Dynamic timezone detection to align broker integers to perfect UTC strings
    # D1 bars should always be at 00:00 UTC regardless of broker timezone offset
    tick = mt5.symbol_info_tick(mt5_symbol)
    offset_seconds = 0
    if tick and tick.time > 0 and tf != "D1":
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
    # Bridge to global _live_prices using a live MT5 tick (bid/ask mid) so the
    # price shown in the dashboard and used for execution drift checks is the
    # broker's current market price, not a stale bar close.
    tick = mt5.symbol_info_tick(mt5_symbol)
    if tick and tick.bid > 0 and tick.ask > 0:
        live_px = (tick.bid + tick.ask) / 2.0
    elif tick and tick.last > 0:
        live_px = float(tick.last)
    elif candles:
        live_px = float(candles[-1]["close"])
    else:
        live_px = None
    if live_px is not None:
        with _live_prices_lock:
            _live_prices[symbol] = {
                "price": live_px,
                "bid": float(tick.bid) if tick and tick.bid > 0 else None,
                "ask": float(tick.ask) if tick and tick.ask > 0 else None,
                "ts": time.time(),
                "source": "mt5",
            }

    resp = {
        "error": False,
        "symbol": symbol,
        "detail": "",
        "candles": candles[-limit:] if len(candles) > limit else candles,
    }
    return _overlay_mt5_candles_with_eodhd_volume(pair, tf, resp)

def fetch_candles(pair: dict, tf: str, limit: int) -> list | None:
    """Route candle fetch to correct source with in-memory TTL cache (see candles_cache)."""
    return _fetch_candles_routed(
        pair,
        tf,
        limit,
        fetch_candles_live=fetch_candles_live,
        fetch_binance=fetch_binance,
        fetch_binance_paginated=fetch_binance_paginated,
        fetch_eodhd=fetch_eodhd,
        fetch_polygon=fetch_polygon,
        fetch_yfinance=fetch_yfinance,
        fetch_mt5=fetch_mt5,
        yfinance_symbol_for_pair=_yfinance_symbol_for_pair,
        tf_b=TF_B,
    )


def fetch_market_state(pair: dict, tf: str, limit: int) -> dict:
    """Detailed market state for a pair/tf, identifying if the last bar is forming."""
    from candle_manager import fetch_market_state as _fms
    return _fms(pair, tf, limit)


def _market_state_series(state: dict | None, *, include_forming: bool = True) -> list:
    """Flatten a market-state payload into confirmed bars plus optional forming bar."""
    if not isinstance(state, dict):
        return []
    confirmed = list(state.get("confirmed") or [])
    if not include_forming:
        return confirmed
    forming = state.get("forming")
    return confirmed + ([forming] if forming else [])


def _engine_b_live_market_state(pair: dict, tf: str, limit: int) -> dict:
    """Live Engine B market-state fetch.

    MT5 pairs bypass TTL candle cache so naked-chart scans read fresh broker candles,
    including the current forming bar when present. Other sources reuse the shared
    market-state path.
    """
    from athena_app.services.engine_b_market_state import engine_b_live_market_state

    return engine_b_live_market_state(
        pair,
        tf,
        limit,
        fetch_mt5=fetch_mt5,
        fetch_market_state=fetch_market_state,
        extract_candles=_extract_candles,
        log=log,
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
    calc_usd_relative_strength_context,
)


from scoring import (  # noqa: E402
    get_session,
    calc_confluence,
    detect_div,
    apply_correlation_cap,
    get_pair_profile,
    get_pair_score_group,
    get_pair_level_atr_class,
    get_min_confluence_threshold,
    pair_filter_enabled,
    _pair_exchange_closed,
    _build_event_risk,
    _classify_signal,
)

from config import _json_safe  # noqa: E402
from engine_c import compute_consensus, apply_vision  # noqa: E402
from athena_runtime import executed_signals  # noqa: E402
from vision_data import (  # noqa: E402
    asdict_safe as _vision_asdict_safe,
    create_vision_label as _create_vision_label,
    create_vision_prediction as _create_vision_prediction,
    create_vision_sample as _create_vision_sample,
    ensure_vision_schema as _ensure_vision_schema,
    fetch_vision_metrics as _fetch_vision_metrics,
    fetch_vision_sample as _fetch_vision_sample,
)
from vision_hybrid import (  # noqa: E402
    infer_chart_vision_v2 as _infer_chart_vision_v2,
    train_hybrid_model as _train_hybrid_model,
)
from vision_prompts import (  # noqa: E402
    build_dual_prompt,
    build_single_prompt,
    build_system_prompt,
    build_triple_prompt,
)


_scan_lock = threading.Lock()  # thread-safe scan guard (replaces bare boolean)

_kill_switch = False  # N4: Kill-switch - blocks new scans/analyses when True

_test_mode = (
    False  # Test mode: drops score thresholds, enables force-execute on all signals
)

_disabled_pairs: set = set()  # per-pair kill-switch - display names of pairs to exclude

# Per-scan DXY H4 cache to avoid redundant yfinance fetches in Engine B overlay
_dxy_h4_cache: tuple[list[float], float] | None = None  # (closes, timestamp)


def _get_dxy_h4_closes(force_refresh: bool = False) -> list[float] | None:
    """Fetch DXY H4 closes with a 5-minute in-memory cache per scan batch."""
    global _dxy_h4_cache
    import time
    now = time.time()
    if not force_refresh and _dxy_h4_cache is not None:
        closes, ts = _dxy_h4_cache
        if now - ts < 300:  # 5 min cache
            return closes
    _dxy = fetch_candles(
        {
            "symbol": "DX-Y.NYB",
            "display": "DXY",
            "source": "yfinance",
            "type": "index",
        },
        "H4",
        100,
    )
    if _dxy:
        closes = [float(c["close"]) for c in _dxy]
        _dxy_h4_cache = (closes, now)
        return closes
    return None


def _normalize_style(style: str | None) -> str:
    """Normalize style strings used by scan/backtest endpoints."""

    s = (style or "auto").lower()

    return s if s in ("auto", "swing", "intraday", "scalp") else "auto"


def resolve_auto_style(requested_style: str, pair: dict | None) -> str:
    """Resolve auto style by asset class.

    Keeps scan/backtest style routing local to athena.py to avoid runtime
    dependency on optional helper modules.
    """
    requested = str(requested_style or "auto").lower()
    if requested != "auto":
        return requested

    ptype = str((pair or {}).get("type") or "").lower()
    if ptype in ("crypto", "forex"):
        return "intraday"
    return "swing"


def _resolve_scan_style(requested_style: str, pair: dict) -> str:
    """Resolve scan style per pair. Auto favors intraday for fast-moving markets."""

    return resolve_auto_style(requested_style, pair)


def _effective_backtest_style(pair: dict, requested_style: str) -> str:
    """Resolve backtest iteration style per pair."""

    return resolve_auto_style(requested_style, pair)


EXPERT_PROMPT = """You are Marcus Reid - 18-year prop-desk veteran turned trading mentor.
You speak like a sharp friend who happens to be a market wizard - concise, opinionated.
No corporate-speak. No filler. No hedging.

ABSOLUTE RULES - VIOLATION = FAILURE:
1. Output ONLY valid JSON. No markdown, no text outside JSON values.
2. NEVER state anything not directly supported by the input data. If a factor is None/missing, say "data unavailable" - do NOT guess.
3. NEVER use "will", "guaranteed", "definitely". Use "edge suggests", "probability favors", "setup indicates".
4. EVERY claim in your narrative MUST reference a specific data point from the input (factor name, score value, weight, z-score, regime label, level price). If you cannot cite the data, do not make the claim.
5. Counter-trend setups: FLAG explicitly in warnings with risk rationale (cite SIGNAL/FACTOR data). Grade from evidence - do NOT auto-downgrade by a fixed number of levels.
6. If directionalScore and direction disagree, FLAG THIS as a critical issue.
7. DEAD RANGING / low ADX / chop: name the regime, explain follow-through risk using ADX percentile/label from SIGNAL, then decide the grade from evidence - do NOT use a fixed grade ceiling.

STYLE & ASSET AWARENESS RULES:
Evaluate the trade setup based on the 'Resolved AI style' and 'Asset type' provided in the AI CALIBRATION CONTEXT.
- Do NOT judge a Scalp setup by Swing criteria (or vice versa).
- Risk:Reward (RR) thresholds per style:
  * SCALP: RR >= 1.5 is acceptable.
  * INTRADAY: RR >= 2.0 is preferred.
  * SWING: RR >= 3.0 is preferred.
- Stop Loss (SL) bounds per Asset Type & Style:
  * CRYPTO: SL > 2% is normal for alts; do NOT automatically force quarter sizing for wide SL unless it exceeds MAX_SL_PCT.
  * FOREX: SL% is typically tighter. A wide SL can be an elevated risk if ATR confirms it.
  * If SL exceeds configured MAX_SL_PCT, treat as invalid/execution-blocking.

INPUT SECTIONS:
=== AI CALIBRATION CONTEXT === (engine source, asset type, style, raw score %, thresholds, dashboard confluence labels)
=== SIGNAL === (pair, direction, score/maxScore, conviction, regime, style)
=== FACTOR DIAGNOSTICS === (per-factor scores with weights, directional vs nondirectional breakdown, confidence multiplier, trend coherence, optional coverage)
=== CONFIDENCE ENGINE === (confidence value and component breakdown)
=== ENGINE B === (naked market structure - swing sequence, BOS, CHoCH, order blocks, FVGs, zones)
=== TECHNICALS === (individual voted indicators + z-scores)
=== LEVELS === (entry, SL, TP1, TP2 with R-multiples, ATR, fib levels)
=== WARNINGS === (penalties already applied - these are FACTS not opinions)
=== CONTEXT === (NOT scored - news, DXY, yield curve, backtest stats, learning history)
=== PORTFOLIO === (heat, drawdown)

HOW TO ANALYSE - FOLLOW THIS EXACT ORDER:
Step 1: Read AI CALIBRATION CONTEXT first. Identify the Asset Type and Resolved AI style. Note whether the dashboard confluence label is Weak, Medium, or Strong. Do not confuse thresholdProgressPct with rawScorePct.
Step 2: Read FACTOR DIAGNOSTICS. Which directional factors are active? Does direction match? What is the confidence multiplier?
Step 3: Check trendCoherence. How many timeframes agree? If coherence_ratio < 0.5, signal is fragmented; 0.5-0.7 mixed; >0.7 aligned.
Step 4: Read regime. Explain follow-through and chop risk from the data. Do not auto-downgrade purely from regime label.
Step 5: Read LEVELS. Evaluate SL and RR according to the Style & Asset Awareness Rules above. Do not automatically penalize Crypto for >2% SL.
Step 6: If ENGINE B data is present, cross-reference structural verdict with factor direction. Agreement = positive; conflict = major red flag.
Step 7: If CONTEXT data is present, use for narrative color ONLY.

GRADING - derive from data, NOT from rawScorePct buckets:
You must arrive at a letter grade (A+ through F) by weighing evidence in this order. Cite which items drove the grade in narrative/warnings.
1. Factor coherence: how many active directional factors support the call, and do weights justify confidence?
2. trendCoherence ratio: <0.5 fragmented; 0.5-0.7 mixed; >0.7 aligned.
3. directionalConfidenceMultiplier: <0.5 is a structural red flag regardless of headline score.
4. ENGINE B (if present): CLEAR structural_verdict + direction aligned to Engine A is a boost; UNCLEAR/misaligned is a risk.
5. RR vs style minimum from LEVELS.
6. Regime: name it, explain follow-through risk, then integrate (no fixed caps).
7. Counter-trend: flag in warnings with data; grade from full evidence.

A grade must cite specific evidence. "Score is X%" alone is not sufficient rationale.

edgeProbability (0-100) - derive from input with this rubric (do not mirror rawScorePct mechanically):
- Base from trendCoherence: take coherence_ratio from FACTOR DIAGNOSTICS (0-1). Add min(40, coherence_ratio * 40) points.
- directionalConfidenceMultiplier: add min(30, multiplier * 30) where multiplier is 0-1 from diagnostics (if missing, use 0).
- ENGINE B: if structural_verdict is CLEAR and direction matches Engine A, +15; if ENGINE B absent/neutral, +0; if UNCLEAR or direction conflicts, -10.
- Regime label from SIGNAL: TRENDING or strong trend labels +10; RANGING/chop near neutral +0; DEAD RANGING or explicit dead chop + (-10).
- RR: meets style minimum from LEVELS +5; below -5.
Sum, clamp to 5-95. Round to integer for the JSON field.

STRUCTURED SCORE OUTPUT:
- Component max scores: trend 20, structure 20, momentum 15, liquidity 10, risk 15, confirmation 20.
- total_score is the sum of those components, clamped 0-100.
- risk_score means risk quality: higher is cleaner/safer, lower is worse.
- ai_action is advisory only. ATHENA Python hard rules decide advisory_rule_trade_allowed after parsing.
- Use blocking_reasons for data-supported blockers only: NO_STOP_LOSS, RR_BELOW_MIN, DAILY_LOSS_LIMIT_HIT, HIGH_IMPACT_NEWS_NEARBY, or DATA_UNAVAILABLE.

PER-STYLE RATINGS - rate ALL THREE independently using specific data:
- SCALP: Need ADX > 30, clean H1 entry, vol_ratio > 1.5, RR >= 1.5
- INTRADAY: Need H4+H1 aligned, same session, RR >= 2.0, momentum confirming
- SWING: Need D1 EMA stack + trendCoherence > 0.8, RR >= 3.0, no upcoming high-impact events

OUTPUT - EXACT JSON in this precise key order to ensure reasoning happens before scoring (no other text):
{"symbol":"BTCUSDT","timeframe":"H4","bias":"long|short|neutral","setup_type":"breakout_retest","trend_score":18,"structure_score":17,"momentum_score":13,"liquidity_score":8,"risk_score":7,"confirmation_score":14,"total_score":77,"grade":"B","ai_action":"needs_confirmation","blocking_reasons":["RR_BELOW_MIN"],"reason":"Good structure, but risk/reward is below required threshold.","narrative":"2-3 sentences. MUST reference specific factor names, scores, and weights from the input. Name the strongest and weakest factors.","verdict":"One punchy sentence citing specific factor scores","reviewSource":"engine_a_marcus","resolvedStyle":"SWING|INTRADAY|SCALP","scannerReadiness":"Weak|Medium|Strong","factorQuality":85,"structuralRisk":"Low","executionRisk":"Medium","selectedStyleGrade":"A","entryZone":"exact price or fib level from input","invalidation":"exact price from SL or structural level","keyLevels":"S1/R1 from input data only","positionSizing":"Full/Half/Quarter + why (reference confidence_multiplier and nondirectionalScore)","tradeStyle":"SWING|INTRADAY|SCALP","tradeStyleReason":"cite specific data","warnings":["specific risks citing data points"],"edgeProbability":68,"riskLevel":"Medium","style_ratings":{"scalp":{"grade":"B","edgeProbability":52,"riskLevel":"High"},"intraday":{"grade":"A","edgeProbability":68,"riskLevel":"Medium"},"swing":{"grade":"A+","edgeProbability":78,"riskLevel":"Low"}}}
"""


def fetch_dxy_context():
    """Fetch DXY (US Dollar Index) 5-day trend for Murphy intermarket context."""

    try:
        d = fetch_candles(
            {
                "symbol": "DX-Y.NYB",
                "display": "DXY",
                "source": "yfinance",
                "type": "index",
            },
            "D1",
            10,
        )

        if not d or len(d) < 5:
            return None

        cl = [c["close"] for c in d]

        chg = round((cl[-1] - cl[-5]) / cl[-5] * 100, 2)

        trend = "rising" if chg > 0.3 else "falling" if chg < -0.3 else "flat"

        return f"trend={trend} 5d_chg={chg}% price={round(cl[-1], 2)}"

    except Exception:
        return None


def fetch_usd_relative_strength_context(
    pair: dict, asset_candles: list | None, tf: str = "H4"
) -> dict | None:
    """USD macro context for XAU/USD using DXY as the USD proxy."""
    if not pair or pair.get("display") != "XAU/USD" or not asset_candles:
        return None

    try:
        dxy_candles = fetch_candles(
            {
                "symbol": "DX-Y.NYB",
                "display": "DXY",
                "source": "yfinance",
                "type": "index",
            },
            tf,
            100,
        )
        if not dxy_candles:
            return None

        asset_label = (pair.get("display") or "Asset").split("/")[0].strip() or "Asset"
        return calc_usd_relative_strength_context(
            asset_candles,
            dxy_candles,
            asset_label=asset_label,
            proxy_label="DXY",
            tf=tf,
        )
    except Exception as exc:
        log.debug("[MACRO] USD relative strength unavailable for %s: %s", pair.get("display"), exc)
        return None


# Phase A: UST Yield Curve cache (1hr TTL - rates change slowly)

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
    """Fetch upcoming dividends and splits for stock pairs.

    Preserves the existing event-risk semantics, but fetches symbols concurrently so
    a cold scan is not blocked by dozens of sequential EODHD round-trips.
    """

    global _divsplit_cache

    now = time.time()

    if _divsplit_cache["ts"] > 0 and (now - _divsplit_cache["ts"]) < _DIVSPLIT_TTL:
        log.debug(f"[DIVS] Cache hit - age {int(now - _divsplit_cache['ts'])}s")
        return _divsplit_cache["data"]

    _key = os.environ.get("EODHD_KEY", "")

    if not _key:
        return {}

    today = datetime.now(timezone.utc).date()

    result = {}
    symbols = list(dict.fromkeys(_DIV_SPLIT_PAIRS))
    max_workers = min(
        len(symbols),
        max(1, int(CONFIG.get("SCAN_MAX_WORKERS", 3) or 3)),
    )

    def _fetch_symbol_div_split(sym: str) -> tuple[str, dict]:
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

        return sym, entry

    if symbols:
        from concurrent.futures import ThreadPoolExecutor, as_completed

        with ThreadPoolExecutor(max_workers=max_workers) as pool:
            futures = {
                pool.submit(_fetch_symbol_div_split, sym): sym for sym in symbols
            }
            for fut in as_completed(futures):
                sym = futures[fut]
                try:
                    out_sym, entry = fut.result()
                except Exception as e:
                    log.warning(f"[DIVS] {sym}: worker failed: {e}")
                    continue
                if entry:
                    result[out_sym] = entry

    _divsplit_cache = {"data": result, "ts": now}

    log.info(
        f"[DIVS] Checked {len(_DIV_SPLIT_PAIRS)} pairs - {len(result)} with upcoming events"
    )

    log.info(
        f"[DIVS] Cache populated - {len(_DIV_SPLIT_PAIRS)} pairs, {len(result)} events found. Next refresh in 24h."
    )

    return result


# Phase B2: Upcoming earnings awareness cache (6hr TTL)

_earnings_cache = {"data": {}, "ts": 0}

_EARNINGS_TTL = 21600

_EARNINGS_AVAILABLE = None  # None = untested, True = confirmed working


def fetch_upcoming_earnings_context(pairs: list | None = None) -> dict:
    """Fetch upcoming earnings for tracked stock pairs via EODHD SDK."""

    global _earnings_cache, _EARNINGS_AVAILABLE

    if not CONFIG.get("EODHD_EARNINGS_CALENDAR_ENABLED", False):
        _EARNINGS_AVAILABLE = False
        return {}

    now = time.time()

    if _earnings_cache["data"] and (now - _earnings_cache["ts"]) < _EARNINGS_TTL:
        return _earnings_cache["data"]

    if _EARNINGS_AVAILABLE is False:
        return {}  # Already confirmed unavailable on this EODHD plan - skip API call

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
                False  # Disable for this session - plan doesn't include it
            )

            log.warning(
                "[EARN] 403 Forbidden - earnings calendar not available on this EODHD plan. Disabling for session."
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
        f"[EARN] Checked {len(symbols)} stock symbols - {len(result)} with upcoming earnings"
    )

    return result


# P2: Background news refresh (default 60 min); per-pair news/word-weights fetched on-demand (AI only).

_news_cache = {"data": None, "ts": 0.0}
_news_lock = threading.Lock()
_news_thread_started = False
_news_thread_lock = threading.Lock()

_NEWS_BG_INTERVAL_SEC = float(CONFIG.get("NEWS_BG_INTERVAL_SEC", 3600.0) or 3600.0)  # default 60 min
_news_pair_cache: dict = {}  # sticker -> {"news": [...], "weights": [...], "ts": float} - populated on-demand (AI only)

_NEWS_EMPTY: dict = {"forexEvents": [], "cryptoNews": [], "marketNews": []}


def _refresh_news_cache(pairs: list | None = None) -> dict:
    """Fetch Finnhub / CryptoPanic / EODHD and update _news_cache."""
    ctx: dict = {"forexEvents": [], "cryptoNews": [], "marketNews": []}

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
                sentiment_by_ticker = {}
                _tickers = list(ticker_map.keys())
                _batch_size = max(
                    1, int(CONFIG.get("EODHD_SENTIMENT_BATCH_SIZE", 25) or 25)
                )
                _connect_timeout = float(
                    CONFIG.get("EODHD_SENTIMENT_CONNECT_TIMEOUT_SEC", 5) or 5
                )
                _read_timeout = float(
                    CONFIG.get("EODHD_SENTIMENT_READ_TIMEOUT_SEC", 12) or 12
                )
                _total_batches = max(1, math.ceil(len(_tickers) / _batch_size))

                for _idx in range(0, len(_tickers), _batch_size):
                    _batch = _tickers[_idx : _idx + _batch_size]
                    try:
                        _resp = http_requests.get(
                            "https://eodhd.com/api/sentiments",
                            params={
                                "s": ",".join(_batch),
                                "api_token": _eodhd_key,
                                "fmt": "json",
                            },
                            timeout=(_connect_timeout, _read_timeout),
                        )
                        _resp.raise_for_status()
                        sdata = _resp.json()
                    except Exception as _sent_err:
                        _safe_err = str(_sent_err).replace(_eodhd_key, "***")
                        log.warning(
                            "[SENT] EODHD batch failed (%d/%d, size=%d): %s",
                            (_idx // _batch_size) + 1,
                            _total_batches,
                            len(_batch),
                            _safe_err,
                        )
                        continue

                    if isinstance(sdata, dict):
                        for _k, _v in sdata.items():
                            sentiment_by_ticker[str(_k)] = _v
                    elif isinstance(sdata, list):
                        for row in sdata:
                            if not isinstance(row, dict):
                                continue
                            key = (
                                row.get("code")
                                or row.get("symbol")
                                or row.get("ticker")
                                or row.get("s")
                            )
                            if key:
                                sentiment_by_ticker[str(key)] = row

                for sticker, display in ticker_map.items():
                    raw_scores = sentiment_by_ticker.get(sticker, [])
                    if isinstance(raw_scores, dict):
                        scores = [raw_scores]
                    elif isinstance(raw_scores, list):
                        scores = raw_scores
                    else:
                        scores = []

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

            log.info("[NEWS] EODHD per-pair news/word-weights: fetched on-demand during AI analysis only")

    except Exception as e:
        log.warning(f"[NEWS] EODHD sentiment/news failed: {e}")

    with _news_lock:
        _news_cache["data"] = ctx
        _news_cache["ts"] = time.time()

    return ctx


def _news_updater_loop():
    """One-shot refresh for on-demand use."""
    try:
        _refresh_news_cache(None)
    except Exception as e:
        log.warning(f"[NEWS] Background refresh failed: {e}")

    global _news_thread_started
    with _news_thread_lock:
        _news_thread_started = False


def _ensure_news_background_started():
    global _news_thread_started
    if _news_thread_started:
        return
    with _news_thread_lock:
        if _news_thread_started:
            return
        _t = threading.Thread(
            target=_news_updater_loop,
            daemon=True,
            name="news-updater",
        )
        _t.start()
        _news_thread_started = True
        log.info(
            f"[NEWS] On-demand news refresh triggered (TTL {_NEWS_BG_INTERVAL_SEC / 60:.1f} min)"
        )


def _filter_news_ctx_for_pairs(data: dict, pairs: list | None) -> dict:
    """Restrict pair-keyed EODHD fields to the given pair list (scanner / Engine B)."""
    if not data:
        return copy.deepcopy(_NEWS_EMPTY)
    if not pairs:
        return copy.deepcopy(data)
    displays = {
        p.get("display") or p.get("pair")
        for p in pairs
        if isinstance(p, dict)
    }
    displays.discard(None)
    if not displays:
        return data
    out = copy.deepcopy(data)
    for key in ("pairSentiment", "pairNews", "wordWeights"):
        if key in out and isinstance(out[key], dict):
            out[key] = {
                k: v for k, v in out[key].items() if k in displays
            }
    return out


def fetch_news_context(pairs: list | None = None, allow_refresh: bool = True):
    """Return cached news context immediately. Trigger refresh if missing or expired (unless allow_refresh is False)."""
    global _news_cache
    now = time.time()

    with _news_lock:
        stale = _news_cache["data"] is None or (now - _news_cache["ts"]) > _NEWS_BG_INTERVAL_SEC

    if stale and allow_refresh:
        _ensure_news_background_started()

    with _news_lock:
        data = _news_cache["data"]
    return _filter_news_ctx_for_pairs(data if data is not None else {}, pairs)


def _fetch_pair_news_on_demand(display: str, sticker: str) -> tuple[list, list]:
    """Fetch EODHD news + word-weights for one pair, called only during AI analysis.

    Returns (pair_news_list, word_weights_list). Results cached for 4 hours so
    repeated AI checks on the same pair within a session don't re-fetch.
    """
    _eodhd_key = os.environ.get("EODHD_KEY", "")
    if not _eodhd_key or not sticker:
        return [], []

    _now = time.time()
    _ttl = float(CONFIG.get("NEWS_PAIR_CACHE_TTL_SEC", 14400.0) or 14400.0)
    cached = _news_pair_cache.get(sticker, {})
    _age = _now - float(cached.get("ts", 0) or 0)
    if _age < _ttl and ("news" in cached or "weights" in cached):
        return cached.get("news", []), cached.get("weights", [])

    pair_news: list = []
    word_weights: list = []
    try:
        ndata = http_requests.get(
            f"https://eodhd.com/api/news?s={sticker}&limit=3&api_token={_eodhd_key}&fmt=json",
            timeout=10,
        ).json()
        if ndata and isinstance(ndata, list):
            pair_news = [
                {"t": a.get("title", "")[:80], "s": round(a.get("sentiment", {}).get("polarity", 0.5), 2)}
                for a in ndata[:3]
            ]
    except Exception as _e:
        log.debug(f"[NEWS-AI] {display} news fetch: {_e}")

    try:
        wdata = http_requests.get(
            f"https://eodhd.com/api/news-word-weights?s={sticker}&page[limit]=5&api_token={_eodhd_key}&fmt=json",
            timeout=10,
        ).json()
        if wdata and isinstance(wdata, dict) and wdata.get("data"):
            word_weights = list(wdata["data"].keys())[:5]
    except Exception as _e:
        log.debug(f"[NEWS-AI] {display} word-weights fetch: {_e}")

    _news_pair_cache[sticker] = {"news": pair_news, "weights": word_weights, "ts": _now}
    log.info(f"[NEWS-AI] {display}: fetched {len(pair_news)} headlines, {len(word_weights)} word-weights on AI request")
    return pair_news, word_weights


def _build_signal_message(
    signal: dict,
    news_ctx: dict | None,
    style_pref: str,
    style_labels: dict,
    portfolio_heat: float = 0.0,
    drawdown_pct: float = 0.0,
    learning_ctx: dict | None = None,
) -> str:
    """Build sectioned signal string sent to Marcus Reid for AI analysis.



    Sections: SIGNAL, TECHNICALS, LEVELS, WARNINGS, CONTEXT, PORTFOLIO.

    Removes redundant ServerIndicators (votes already contain the verdict).

    """

    def _num(x, default: float = 0.0) -> float:
        """Coerce for formatting; treats explicit None/missing/non-numeric as default."""
        try:
            if x is None:
                return float(default)
            return float(x)
        except (TypeError, ValueError):
            return float(default)

    max_score = _num(signal.get("maxScore"), 3.0)

    score = _num(signal.get("confluenceScore"), 0.0)

    score_pct = round(score / max_score * 100) if max_score else 0

    spread = _num(signal.get("spread"), 0.0)

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
    # Engine A signals store naked overlay in "engine_b"; Engine B scan signals store it in "naked_data"
    eng_b = signal.get("engine_b") or signal.get("naked_data")
    if eng_b:
        lines.append("")
        lines.append("=== ENGINE B (NAKED MARKET STRUCTURE) ===")
        lines.append(f"  Structural Verdict: {eng_b.get('structural_verdict')}")
        lines.append(f"  Current Swing Seq: {eng_b.get('current_swing_sequence')}")
        lines.append(f"  Macro Swing Seq: {eng_b.get('macro_swing_sequence')}")

        rz = eng_b.get("nearest_resistance_zone")
        if rz:
            lines.append(
                f"  Nearest Res Zone: {_num(rz.get('lower')):.4f} - {_num(rz.get('upper')):.4f}"
            )

        sz = eng_b.get("nearest_support_zone")
        if sz:
            lines.append(
                f"  Nearest Sup Zone: {_num(sz.get('lower')):.4f} - {_num(sz.get('upper')):.4f}"
            )

        lines.append(f"  Distance to Res: {_num(eng_b.get('distance_to_res')):.2f}%")
        lines.append(f"  Distance to Sup: {_num(eng_b.get('distance_to_sup')):.2f}%")
        lines.append(f"  Room to Move Bonus: {eng_b.get('room_to_move_bonus', 0)}")
        lines.append(f"  Catalyst Bonus: {eng_b.get('catalyst_bonus', 0)}")
        lines.append(f"  AI Stats Adjustment: {_num(eng_b.get('ai_adjustment')):.2f}")
        lines.append(
            f"  Engine B Final Score: {_num(eng_b.get('score')):.2f} / {_num(eng_b.get('max_possible'), 3.0):.2f} ({_num(eng_b.get('score_pct')):.1f}%)"
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

    # === RAW MARKET DATA (last 20 H4 bars) ===
    # Give Marcus Reid actual OHLCV + indicator time-series so the AI can
    # independently verify Engine A conclusions, not just read summaries.
    _h4_candles = signal.get("h4Candles", [])
    if _h4_candles:
        _raw_count = min(20, len(_h4_candles))
        _raw_slice = _h4_candles[-_raw_count:]
        _closes = [c.get("c", c.get("close")) for c in _h4_candles]
        _highs = [c.get("h", c.get("high")) for c in _h4_candles]
        _lows = [c.get("l", c.get("low")) for c in _h4_candles]
        try:
            from indicators import calc_ema, calc_rsi, calc_macd, calc_adx, calc_atr
            _ema21_s = calc_ema(_closes, 21)
            _ema50_s = calc_ema(_closes, 50)
            _ema200_s = calc_ema(_closes, 200)
            _rsi_s = calc_rsi(_closes, 14)
            _macd_s = calc_macd(_closes, 12, 26, 9)
            _adx_s = calc_adx(_highs, _lows, _closes, 14)
            _atr_s = calc_atr(_highs, _lows, _closes, 14)
            lines.append("")
            lines.append(f"=== RAW MARKET DATA (last {_raw_count} H4 bars) ===")
            lines.append(
                "Bar | Time | Open | High | Low | Close | Vol | EMA21 | EMA50 | RSI | MACD | MACD_Sig | ADX | +DI | -DI | ATR"
            )
            for idx, c in enumerate(_raw_slice):
                _g = idx + (len(_h4_candles) - _raw_count)  # global index in full array
                _fmt = lambda x: f"{x:.5f}" if isinstance(x, (int, float)) else str(x) if x is not None else "-"
                _fmt_rsi = lambda x: f"{x:.1f}" if isinstance(x, (int, float)) else "-"
                _row = (
                    f"{_g:3d} | {c.get('t','')[:16]} | {_fmt(c.get('o',c.get('open')))} | "
                    f"{_fmt(c.get('h',c.get('high')))} | {_fmt(c.get('l',c.get('low')))} | "
                    f"{_fmt(c.get('c',c.get('close')))} | {_fmt(c.get('v',c.get('volume')))} | "
                    f"{_fmt(_ema21_s[_g])} | {_fmt(_ema50_s[_g])} | {_fmt_rsi(_rsi_s[_g])} | "
                    f"{_fmt(_macd_s['macd'][_g])} | {_fmt(_macd_s['signal'][_g])} | "
                    f"{_fmt_rsi(_adx_s['adx'][_g])} | {_fmt_rsi(_adx_s['plusDI'][_g])} | "
                    f"{_fmt_rsi(_adx_s['minusDI'][_g])} | {_fmt(_atr_s[_g])}"
                )
                lines.append(_row)
            lines.append(
                "NOTE: EMA50/EMA200 may show '-' until lookback fills. "
                "MACD valid after 26 bars. ADX valid after 28 bars."
            )
        except Exception as _raw_err:
            log.debug("[BUILD_SIGNAL] raw market data section failed: %s", _raw_err)

    try:
        from ai_context import build_ai_calibration_context_string
        from conductor import conductor_orchestrate
        lines.append("")
        lines.append(build_ai_calibration_context_string(signal, engine_source="Engine A factor/confluence signal", explicit_style=style_pref))
    except Exception as _ai_ctx_err:
        log.debug("[BUILD_SIGNAL] aiContext string failed: %s", _ai_ctx_err)

    # === FACTOR DIAGNOSTICS (quantitative scoring breakdown) ===
    _fd = signal.get("factorDiagnostics", {})
    _fs = signal.get("factorScores", {})
    _fw = signal.get("factorWeights", {})
    _naked = signal.get("naked_data", {})
    _is_naked = bool(signal.get("is_naked"))
    if _fd or _fs or (_is_naked and _naked):
        lines.append("")
        lines.append("=== FACTOR DIAGNOSTICS ===")
        if _fs:
            lines.append("  Factor scores (name: score [weight]):")
            for fn, fv in _fs.items():
                w = _fw.get(fn, "?") if _fw else "?"
                lines.append(f"    {fn}: {fv if fv is not None else 'None'} [w={w}]")
        if _fd.get("directionalScore") is not None:
            lines.append(f"  Directional score (weighted): {_fd['directionalScore']:.4f}")
        if _fd.get("nondirectionalScore") is not None:
            lines.append(f"  Nondirectional score (quality): {_fd['nondirectionalScore']:.4f}")
        if _fd.get("directionalConfidenceMultiplier") is not None:
            lines.append(f"  Directional confidence multiplier: {_fd['directionalConfidenceMultiplier']:.4f}")
        if _fd.get("minDirectionalThreshold") is not None:
            lines.append(f"  Min directional threshold: {_fd['minDirectionalThreshold']}")
        if _fd.get("minDirectionalFailed"):
            lines.append("  ** MIN DIRECTIONAL FAILED - signal below directional threshold **")
        _tc = _fd.get("trendCoherence", {})
        if _tc:
            lines.append(
                f"  Trend coherence: {_tc.get('agreement_count', '?')}/{_tc.get('total_count', '?')} TFs agree "
                f"(ratio={_tc.get('coherence_ratio', '?')}, dominant={_tc.get('dominant_direction', '?')})"
            )
        if _fd.get("optionalFactorCoverage") is not None:
            lines.append(f"  Optional factor coverage: {_fd['optionalFactorCoverage']}")
        if _fd.get("activeDirectionalFactors"):
            lines.append(f"  Active directional: {', '.join(_fd['activeDirectionalFactors'])}")
        if _fd.get("activeNondirectionalFactors"):
            lines.append(f"  Active nondirectional: {', '.join(_fd['activeNondirectionalFactors'])}")
        _im = signal.get("intermarketConfirmation") or _fd.get("intermarket") or {}
        if _im:
            lines.append("  Intermarket confirmation:")
            lines.append(
                f"    verdict={_im.get('verdict', 'neutral')} "
                f"score={_im.get('score', 0)} delta={_im.get('engineADelta', 0)} "
                f"window={_im.get('activeWindow', 'n/a')}"
            )
            if _im.get("topSupporting"):
                lines.append(
                    "    supporting: "
                    + ", ".join(
                        str(x.get("driver"))
                        for x in (_im.get("topSupporting") or [])
                    )
                )
            if _im.get("topContradictory"):
                lines.append(
                    "    contradictory: "
                    + ", ".join(
                        str(x.get("driver"))
                        for x in (_im.get("topContradictory") or [])
                    )
                )
            if _im.get("unavailablePriors"):
                lines.append(
                    "    unavailable priors: "
                    + ", ".join(str(x) for x in (_im.get("unavailablePriors") or []))
                )
        if _fd.get("insufficientFactors"):
            lines.append("  ** INSUFFICIENT FACTORS - too few active to produce valid score **")

    # Engine B signals: emit naked scoring summary in place of factor diagnostics
    if _is_naked and _naked and not _fs:
        lines.append("")
        lines.append("=== ENGINE B SCORING DIAGNOSTICS ===")
        lines.append(f"  Score: {_naked.get('score', 'N/A')} / {_naked.get('max_possible', 'N/A')} ({_naked.get('score_pct', 'N/A')}%)")
        lines.append(f"  Structural Verdict: {_naked.get('structural_verdict', 'N/A')}")
        lines.append(f"  Room-to-move bonus: {_naked.get('room_to_move_bonus', 0)}")
        lines.append(f"  Catalyst bonus: {_naked.get('catalyst_bonus', 0)}")
        lines.append(f"  AI stats adjustment: {_naked.get('ai_adjustment', 0)}")
        lines.append(f"  Actionable: {'YES' if _naked.get('is_actionable') else 'NO'}")

    # === CONFIDENCE ENGINE ===
    _conf = signal.get("confidenceDetail", {})
    if _conf and _conf.get("confidence") is not None:
        lines.append("")
        lines.append("=== CONFIDENCE ENGINE ===")
        lines.append(f"  Confidence: {_conf['confidence']:.4f}")
        _comps = _conf.get("components", {})
        for cname, cval in _comps.items():
            lines.append(f"    {cname}: {cval if cval is not None else 'N/A'}")
        if _conf.get("degraded"):
            lines.append(f"  ** DEGRADED - only {_conf.get('available_count', '?')}/{len(_comps)} components available **")

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
        # Only send key fib levels - full dump is ~100 extra tokens
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
            f"3M: {_yc.get('y3m')}%, 10Y: {_yc['y10y']}%) - {_yc['riskContext']}"
        )

    _ds = fetch_div_split_context()

    _pair_sym = signal.get("symbol", "")

    if _ds and _pair_sym in _ds:
        _ev = _ds[_pair_sym]

        if _ev.get("upcomingDiv"):
            _d = _ev["upcomingDiv"][0]

            lines.append(
                f"Ex-div in {_d['daysTo']} days ({_d['exDate']}, amount: {_d.get('amount', '?')}) - gap-down risk"
            )

        if _ev.get("upcomingSplit"):
            _s = _ev["upcomingSplit"][0]

            lines.append(
                f"Split in {_s['daysTo']} days ({_s['splitDate']}, ratio: {_s.get('ratio', '?')}) - price distortion risk"
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

        # Forex events - keep only event name + date (strip full object bloat)
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
                # Title only - strip full article body
                _ctx_parts.append(
                    f"Crypto news: {', '.join(n.get('title', '')[:80] for n in relevant[:2])}"
                )

        if news_ctx.get("marketNews"):
            # Title only
            _ctx_parts.append(
                f"Market news: {', '.join(n.get('title', n.get('headline', ''))[:80] for n in news_ctx['marketNews'][:2])}"
            )

        _pair_display = signal.get("pair", "")
        _pair_sticker = _eodhd_ticker_for_pair({"display": _pair_display, "symbol": signal.get("symbol", ""), "type": signal.get("type", "")})
        _pnews, _ww = _fetch_pair_news_on_demand(_pair_display, _pair_sticker or "")

        if _pnews:
            _ctx_parts.append(
                f"Pair news: {', '.join(n.get('t', n.get('title', ''))[:80] for n in _pnews[:2])}"
            )

        if _ww:
            _ctx_parts.append(f"News drivers: {json.dumps(_ww[:5])}")

        for cp in _ctx_parts:
            lines.append(cp)

    # === CANDLE DATA FRESHNESS ===
    try:
        from ai_utils import build_freshness_ai_context as _build_freshness
        _freshness_str = _build_freshness(signal)
        if _freshness_str:
            lines.append("")
            lines.append(_freshness_str)
    except Exception:
        pass

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
                f"This pair history: {_num(pair_s.get('win_rate')) * 100:.0f}% WR over "
                f"{pair_s.get('total_trades', 0)} trades (avg {_num(pair_s.get('avg_r')):+.2f}R)"
                + (
                    f", best in {pair_s['best_regime']}"
                    if pair_s.get("best_regime")
                    else ""
                )
            )

        at_s = learning_ctx.get("asset_type_stats")

        if at_s:
            lines.append(
                f"{signal.get('type', '').upper()} class: {_num(at_s.get('win_rate')) * 100:.0f}% WR "
                f"avg {_num(at_s.get('avg_r')):+.2f}R ({at_s.get('total_trades', 0)} trades)"
            )

        grade_acc = learning_ctx.get("grade_accuracy", {})

        if grade_acc:
            grade_parts = []

            for g in ["A+", "A", "B", "C"]:
                if g in grade_acc and grade_acc[g]["trades"] >= 2:
                    s = grade_acc[g]

                    grade_parts.append(
                        f"{g}:{_num(s.get('win_rate')) * 100:.0f}%WR/{_num(s.get('avg_r')):+.1f}R"
                    )

            if grade_parts:
                lines.append(f"Grade calibration: {' | '.join(grade_parts)}")

        failures = learning_ctx.get("recent_failures", [])

        if failures:
            fail_summary = "; ".join(
                f"{f['pair']} {f['grade']} {f['regime']} R={_num(f.get('r')):.1f}"
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


@ai_call_with_safe_default("marcus", max_retries=1)
def run_ai(
    signal: dict,
    news_ctx: dict | None = None,
    style_pref: str = "auto",
    portfolio_heat: float = 0.0,
    drawdown_pct: float = 0.0,
    learning_ctx: dict | None = None,
) -> dict:
    """Send signal data to the configured AI provider for Marcus Reid analysis."""

    ensure_trace_id(signal)

    if not ai_key_configured(CONFIG):
        log.error("[AI] AI API key is not configured")
        return {"error": "AI API key not configured"}

    _provider = get_ai_provider_label(CONFIG)
    _model = get_ai_model(CONFIG, "AI_MODEL", "grok-4.3")
    _timeout_sec = None
    _sdk_max_retries = None
    _prompt_sec = None
    _prompt_chars = None
    _provider_started = None

    try:
        log.info(
            f"[AI] Analyzing {signal['pair']} via provider={_provider} "
            f"base_url={get_ai_base_url(CONFIG)} model={_model}"
        )

        _sdk_max_retries = get_ai_max_retries(
            CONFIG,
            preferred_key="MARCUS_AI_SDK_MAX_RETRIES",
            fallback=0,
        )
        c = create_ai_client(CONFIG, max_retries=_sdk_max_retries)
        _temp = float(AITemperatureConfig.get_temperature("marcus"))

        style_labels = {
            "scalp": "SCALP - focus on H1 exhaustion, tight 1.5R, quick execution",
            "intraday": "INTRADAY - H4+H1 alignment, same-session execution, 2-3R",
            "swing": "SWING - D1 trend dominance, EMA200 slope, 4-6R multi-day hold",
        }

        if style_pref == "auto":
            _sc = signal.get("confluenceScore", 0)
            _max = signal.get("maxScore", 3.0) or 3.0
            _pct = (_sc / _max * 100) if _max > 0 else 0

            style_pref = (
                "swing" if _pct >= 75 else "intraday" if _pct >= 50 else "scalp"
            )
        _prompt_started = time.monotonic()
        msg = _build_signal_message(
            signal,
            news_ctx,
            style_pref,
            style_labels,
            portfolio_heat=portfolio_heat,
            drawdown_pct=drawdown_pct,
            learning_ctx=learning_ctx,
        )
        _prompt_sec = time.monotonic() - _prompt_started
        _prompt_chars = len(msg)

        _timeout_sec = get_ai_timeout_sec(
            CONFIG,
            preferred_key="MARCUS_AI_TIMEOUT_SEC",
            fallback=90.0,
        )
        _provider_started = time.monotonic()

        def _is_retryable_ai_error(exc: Exception) -> bool:
            text = str(exc or "").strip().lower()
            if not text:
                return False
            markers = (
                "timed out",
                "timeout",
                "connection reset",
                "connection aborted",
                "connection refused",
                "temporarily unavailable",
                "server error",
                "rate limit",
                "too many requests",
                "502",
                "503",
                "504",
            )
            return any(marker in text for marker in markers)

        _max_ai_retries = int(CONFIG.get("MARCUS_AI_MAX_RETRIES", 1) or 1)
        _backoff_base = float(CONFIG.get("MARCUS_AI_RETRY_BACKOFF_SEC", 2.0) or 2.0)
        _last_exc = None
        completion = None
        for _attempt in range(_max_ai_retries + 1):
            try:
                completion = c.chat.completions.create(
                    model=_model,
                    max_tokens=1100,
                    temperature=_temp,
                    messages=[
                        {"role": "system", "content": EXPERT_PROMPT},
                        {"role": "user", "content": msg},
                    ],
                    response_format={"type": "json_object"},
                    timeout=_timeout_sec,
                )
                break
            except Exception as _e:
                _last_exc = _e
                if _attempt >= _max_ai_retries or not _is_retryable_ai_error(_e):
                    raise
                _sleep = _backoff_base * (2 ** _attempt)
                log.warning(
                    "[AI] transient failure for %s on attempt %s/%s: %s; retrying in %.1fs",
                    signal.get("pair", "?"),
                    _attempt + 1,
                    _max_ai_retries + 1,
                    _e,
                    _sleep,
                )
                time.sleep(_sleep)
        if completion is None:
            raise _last_exc or RuntimeError("AI call failed with no completion")

        _provider_sec = time.monotonic() - _provider_started
        log.info(
            "[AI] %s timing: prompt_build=%.2fs provider=%.2fs timeout=%.1fs prompt_chars=%d",
            signal.get("pair", "?"),
            _prompt_sec,
            _provider_sec,
            _timeout_sec,
            _prompt_chars,
        )
        t = (completion.choices[0].message.content or "").strip()
        result = _parse_ai_json(t, signal["pair"])

        if result is None:
            log.error(
                f"[AI] {signal['pair']}: could not parse JSON from response: {t[:200]}"
            )
            return {"error": "AI response was not valid JSON"}

        try:
            from ai_schemas import (
                EngineAResponse,
                evaluate_engine_a_ai_advisory_rules,
            )

            EngineAResponse.model_validate(result)
            _schema_valid = True
            _schema_error = None
        except Exception as _schema_exc:
            _schema_valid = False
            _schema_error = str(_schema_exc)[:500]
            log.warning(
                "[AI] %s: Marcus structured schema validation failed: %s",
                signal.get("pair", "?"),
                _schema_error,
            )

        try:
            result.update(
                evaluate_engine_a_ai_advisory_rules(
                    result,
                    signal,
                    news_ctx,
                    min_rr=1.5,
                )
            )
        except Exception as _rule_exc:
            log.debug("[AI] Marcus advisory rule evaluation failed: %s", _rule_exc)

        # Validate legacy and structured keys for audit visibility.

        _required = {"grade", "edgeProbability", "riskLevel"}
        _structured_required = {
            "symbol",
            "timeframe",
            "bias",
            "setup_type",
            "trend_score",
            "structure_score",
            "momentum_score",
            "liquidity_score",
            "risk_score",
            "confirmation_score",
            "total_score",
            "ai_action",
            "blocking_reasons",
            "reason",
        }

        _missing = _required - set(result.keys())
        _structured_missing = _structured_required - set(result.keys())

        if _missing:
            log.warning(f"[AI] {signal['pair']}: parsed JSON missing keys {_missing}")
        if _structured_missing:
            log.warning(
                "[AI] %s: parsed JSON missing structured keys %s",
                signal.get("pair", "?"),
                sorted(_structured_missing),
            )

        log.warning(
            f"[AI] {signal['pair']} => Grade:{result.get('grade', '?')} Prob:{result.get('edgeProbability', '?')}% Risk:{result.get('riskLevel', '?')} | {str(result.get('verdict', ''))[:60]}"
        )

        try:
            from ai_review_logger import (
                extract_engine_d_audit_state,
                log_ai_review,
                REVIEW_TYPE_MARCUS_REID,
                map_engine_b_grade_to_ai_state,
                AI_STATE_REVIEW_INCOMPLETE,
                AI_STATE_CONFIRM,
                AI_STATE_CAUTION,
                AI_STATE_REJECT,
            )
            _grade = (result.get("grade") or "").upper()
            if _grade in ("A+", "A"):
                _ai_state = AI_STATE_CONFIRM
            elif _grade == "B":
                _ai_state = AI_STATE_CAUTION
            elif _grade in ("C", "D", "F"):
                _ai_state = AI_STATE_REJECT
            else:
                _ai_state = AI_STATE_REVIEW_INCOMPLETE
            log_ai_review(
                symbol=signal.get("pair", "?"),
                asset_type=signal.get("type", "?"),
                review_type=REVIEW_TYPE_MARCUS_REID,
                model=_model,
                provider=get_ai_provider_label(CONFIG),
                prompt_version=get_prompt_version("marcus_expert"),
                input_packet={"pair": signal.get("pair"), "score": signal.get("confluenceScore")},
                has_chart_image=False,
                candle_freshness_status=(
                    (signal.get("dataFreshness") or {}).get("reason") or "unknown"
                ),
                engine_a_state=signal.get("confluenceScore"),
                engine_b_state=signal.get("engine_b", {}).get("score") if isinstance(signal.get("engine_b"), dict) else None,
                engine_c_state=None,
                engine_d_state=extract_engine_d_audit_state(signal),
                risk_state=None,
                ai_review_state=_ai_state,
                ai_confidence=result.get("edgeProbability"),
                contradictions_count=0,
                missing_information_count=0,
                parse_success=True,
                schema_valid=bool(_schema_valid) and not bool(_missing),
                execution_allowed_before_ai=True,
                execution_allowed_after_ai=True,
                final_action="advisory",
                trace_id=signal.get("trace_id"),
            )
        except Exception as _log_err:
            log.debug("[AI_AUDIT] Marcus Reid audit log failed: %s", _log_err)

        return result

    except Exception as e:
        _elapsed_sec = (
            time.monotonic() - _provider_started
            if _provider_started is not None
            else 0.0
        )
        log.error(
            "[AI] ERROR for %s: %s | elapsed=%.2fs timeout=%.1fs sdk_retries=%s "
            "provider=%s model=%s prompt_build=%.2fs prompt_chars=%s",
            signal.get("pair", "?"),
            e,
            _elapsed_sec,
            float(_timeout_sec or 0.0),
            _sdk_max_retries if _sdk_max_retries is not None else "?",
            _provider,
            _model,
            float(_prompt_sec or 0.0),
            _prompt_chars if _prompt_chars is not None else "?",
        )

        return {"error": str(e)}



from backtest_runner import backtest_pair, backtest_pair_naked, backtest_pair_consensus, backtest_pair_scalp, run_full_backtest  # noqa: E402


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

            engine                TEXT,

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

    # Migrate backtest_results: rename eval_threshold → bt_min for DBs created before the rename
    _bt_cols = {row[1] for row in con.execute("PRAGMA table_info(backtest_results)")}
    if "bt_min" not in _bt_cols:
        con.execute("ALTER TABLE backtest_results ADD COLUMN bt_min REAL")
        if "eval_threshold" in _bt_cols:
            con.execute("UPDATE backtest_results SET bt_min = eval_threshold")

    # Migrate: add new columns to existing tables that lack them

    existing = {row[1] for row in con.execute("PRAGMA table_info(audit_log)")}

    for col, defn in [
        ("entry_price", "REAL"),
        ("engine", "TEXT"),
        ("sl", "REAL"),
        ("tp", "REAL"),
        ("volume", "REAL"),
        ("regime", "TEXT"),
        ("risk_amount", "REAL"),
        ("risk_pct", "REAL"),
        ("ticket", "TEXT"),
        # Task 1 - outcome tracking
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
        ("error_tag", "TEXT"),  # AUTO-ERR: reason - set on failed auto-trade attempts
        ("fee_cost", "REAL"),  # Actual paid commission captured from exchange order
        (
            "factors_json",
            "TEXT",
        ),  # Factor scores + key indicators (COT, carry, microstructure, etc.)
        ("signal_price_ref", "REAL"),  # Scan/signal price at order time (vs entry_price fill)
        ("slippage_bps", "REAL"),  # Adverse slippage in basis points (sign = bad for trader)
        ("tp_partial", "REAL"),
        ("tp2", "REAL"),
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
    _ensure_vision_schema(con)
    ensure_lottery_schema(con)

    con.commit()

    con.close()


_AUDIT_DB = os.path.join(os.path.dirname(os.path.abspath(__file__)), "audit.db")
_VISION_ARTIFACTS_DIR = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    "vision_artifacts",
)
_VISION_MODEL_DIR = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    "models",
    "chart_vision_v2",
)

_init_audit_db(_AUDIT_DB)
set_lottery_db_path(_AUDIT_DB)
init_stability_store(_AUDIT_DB)


def _stability_engine_from_audit_row(
    style: str | None, max_score: float | None, engine: str | None = None
) -> str | None:
    engine_norm = str(engine or "").strip().lower()
    if engine_norm in ("engine_a", "engine_b", "engine_c", "scalp"):
        return engine_norm
    style_norm = str(style or "").strip().lower()
    if style_norm == "scalp":
        return "scalp"
    if style_norm in ("engine_c", "consensus"):
        return "engine_c"
    try:
        max_score_f = float(max_score) if max_score is not None else None
    except (TypeError, ValueError):
        max_score_f = None
    if max_score_f is not None:
        return "engine_a"
    return None


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
ensure_advisory_store(_AUDIT_DB)


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

try:
    from guardian_routes import guardian_bp

    app.register_blueprint(guardian_bp)
except ImportError:
    pass

# ── Kimi Code Integration Bridge ─────────────────────────────────────────
try:
    from tools.kimi_bridge import register_kimi_routes

    register_kimi_routes(app)
except ImportError:
    pass

# ── API authentication (shared-secret header) ────────────────────────────

_ATHENA_API_KEY = os.environ.get("ATHENA_API_KEY", "")  # set in .env to enable auth

_AUTH_EXEMPT = {"/", "/favicon.ico"}  # paths that don't require auth


# ── Lightweight rate limiter (no external dependency) ─────────────────────

_rate_limits: dict = {}  # ip -> [timestamps]

_RATE_WINDOW = 60  # seconds

_RATE_MAX_REQUESTS = 120  # max requests per window (2/sec average)

_RATE_EXECUTE_MAX = 5  # stricter limit for execution endpoints


# _json_safe imported from config.py - see that module for implementation


@app.before_request
def _auth_and_rate_limit():

    from flask import request as _req

    path = _req.path or ""

    ip = _req.remote_addr or "unknown"

    # Auth check - only enforced when ATHENA_API_KEY is set in .env

    if _ATHENA_API_KEY and path not in _AUTH_EXEMPT:
        provided = _req.headers.get("X-Sentinel-Key", "")

        if provided != _ATHENA_API_KEY:
            log.warning(
                f"[AUTH] {ip} rejected on {path} - invalid/missing X-Sentinel-Key"
            )

            return jsonify({"error": "Unauthorized - set X-Sentinel-Key header"}), 401

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


@app.route("/api/scan", methods=["POST"])
def api_scan():
    global _dxy_h4_cache
    _dxy_h4_cache = None  # fresh DXY fetch per scan batch
    d = request.get_json(force=True, silent=True) or {}
    result = api_scan_impl(
        d,
        scan_service_handle=lambda payload: handle_scan_request(
            payload, run_full_scan=run_full_scan
        ),
    )

    # Run Conductor on all signals so widget can show routing for any selected pair
    _signals = result.get("signals", [])
    _conductor_plan = None
    if _signals:
        try:
            from conductor import (
                conductor_orchestrate,
                extract_conductor_microstructure,
                reset_scan_results,
            )
            reset_scan_results("engine_a")
            for _sig in _signals[:30]:  # cap at 30 to bound DB query cost
                _vd, _sr = extract_conductor_microstructure(_sig)
                _plan = conductor_orchestrate(
                    _sig,
                    _sig.get("regime", "UNKNOWN"),
                    _AUDIT_DB,
                    volume_divergence=_vd,
                    stop_run=_sr,
                )
                if _sig is _signals[0]:
                    _conductor_plan = _plan
            # Attach top-signal routing to scan result so /api/last-scan serves it
            if _conductor_plan:
                result["conductor"] = _conductor_plan.get("routing", {})
                result["conductor_context"] = _conductor_plan.get("context", {})
        except Exception as _cerr:
            log.warning(f"[CONDUCTOR] scan-side orchestration failed: {_cerr}")

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
        return jsonify({"error": "Kill-switch active - system paused"}), 503

    try:
        _api_ai_started = time.monotonic()
        news_ctx = sig.get("newsCtx") or fetch_news_context()

        style_pref = d.get("stylePreference", "auto")

        # Fetch live portfolio context so the AI knows current risk exposure

        _p_heat = 0.0

        _dd_pct = 0.0

        try:
            from risk_engine import _calc_portfolio_heat, _current_drawdown

            _sig_type = sig.get("type", "")

            if _sig_type == "crypto":
                from bybit_executor import bybit_get_account, bybit_get_positions

                _acct = bybit_get_account()

                _pos_resp = bybit_get_positions()

                if isinstance(_pos_resp, dict) and _pos_resp.get("error"):
                    _pos = []
                else:
                    _pos = (
                        _pos_resp.get("positions", [])
                        if isinstance(_pos_resp, dict)
                        else (_pos_resp or [])
                    )

            else:
                from mt5_executor import mt5_get_account, mt5_get_positions

                _acct = mt5_get_account()

                _pos_resp = mt5_get_positions()

                if isinstance(_pos_resp, dict) and _pos_resp.get("error"):
                    _pos = []
                else:
                    _pos = (
                        _pos_resp.get("positions", [])
                        if isinstance(_pos_resp, dict)
                        else (_pos_resp or [])
                    )

            if _acct and not _acct.get("error"):
                _p_heat = _calc_portfolio_heat(_pos, _acct["balance"])

                _dd_pct = _current_drawdown(
                    _acct["equity"], _sig_type or "unknown"
                )

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

        # ── Conductor: Decide which AI functions to run ──────────────────
        _conductor_plan = None
        try:
            from conductor import conductor_orchestrate, extract_conductor_microstructure

            _vd, _sr = extract_conductor_microstructure(sig)
            _conductor_plan = conductor_orchestrate(
                sig,
                sig.get("regime", "UNKNOWN"),
                _AUDIT_DB,
                news_ctx=news_ctx,
                volume_divergence=_vd,
                stop_run=_sr,
                news_risk=news_ctx.get("risk") if news_ctx else None,
            )
            sig["conductor"] = _conductor_plan.get("routing", {})
            sig["conductor_context"] = _conductor_plan.get("context", {})
        except Exception as _cerr:
            log.debug(f"[CONDUCTOR] failed: {_cerr}")

        _routing = (_conductor_plan or {}).get("routing") or {}
        if _routing.get("skip_signal"):
            return jsonify(
                _json_safe(
                    {
                        "error": "conductor_hard_fail",
                        "message": "Signal below conductor minimum score for analysis pipeline",
                        "conductor": _routing,
                        "conductor_context": (_conductor_plan or {}).get("context"),
                    }
                )
            ), 422
        if _conductor_plan and not _routing.get("run_marcus", True):
            return jsonify(
                _json_safe(
                    {
                        "skipped": True,
                        "message": "Marcus skipped per conductor routing",
                        "conductor": _routing,
                        "conductor_context": _conductor_plan.get("context"),
                    }
                )
            ), 200

        _pre_run_sec = time.monotonic() - _api_ai_started
        result = run_ai(
            sig,
            news_ctx,
            style_pref,
            portfolio_heat=_p_heat,
            drawdown_pct=_dd_pct,
            learning_ctx=_learning_ctx,
        )
        log.info(
            "[AI] %s api_analyze timing: prep=%.2fs total=%.2fs",
            sig.get("pair", "?"),
            _pre_run_sec,
            time.monotonic() - _api_ai_started,
        )

        # N9: Audit log - persist every AI analysis to SQLite

        try:
            _max_s = sig.get("maxScore", 3.0)

            _score_pct = (
                round(sig.get("confluenceScore", 0) / _max_s * 100, 1) if _max_s else 0
            )

            # Add conductor output to result for dashboard display
            if _conductor_plan:
                result["conductor"] = _conductor_plan.get("routing", {})
                result["conductor_context"] = _conductor_plan.get("context", {})

            with sqlite3.connect(_AUDIT_DB, timeout=15.0) as _con:
                # CLAUDE.md Hard Rule 23: signal dict uses camelCase. Legacy
                # snake_case fallback kept so any upstream caller that still
                # sets the old keys continues to populate.
                _factors = {
                    "scores": sig.get("factorScores") or sig.get("factor_scores"),
                    "weights": sig.get("factorWeights") or sig.get("factor_weights"),
                    "disabled": sig.get("disabledFactors"),
                    "regime": sig.get("regimeName"),
                }
                _con.execute(
                    "INSERT INTO audit_log(ts,pair,score,engine,direction,trend,grade,edge_prob,risk,style,"
                    "asset_class,score_pct,max_score,votes_json,warnings_json,"
                    "weinstein,trend_state,adx_pct,btc_bias,session_name,regime,factors_json,"
                    "entry_price,sl,tp,tp_partial,tp2) "
                    "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        datetime.now(timezone.utc).isoformat(),
                        sig.get("pair"),
                        sig.get("confluenceScore"),
                        "engine_a",
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
                        sig.get("price"),
                        sig.get("sl"),
                        sig.get("tp1"),
                        sig.get("tp_partial"),
                        sig.get("tp2"),
                    ),
                )

                _con.commit()

        except Exception as _ae:
            log.warning(f"Audit DB write failed: {_ae}")

        return jsonify(result)

    except Exception as e:
        # S2: Sanitise exception - don't leak internal paths

        log.error(f"api_analyze error: {e}")

        return jsonify({"error": "Analysis failed"}), 500


@app.route("/api/pair-scan", methods=["POST"])
def api_pair_scan():
    """Run Engine A for a single pair so the UI can inspect pairs without a prior full scan."""

    d = request.get_json(silent=True) or {}
    symbol = str(d.get("symbol") or d.get("pair") or "").strip()
    if not symbol:
        return jsonify({"error": "Missing symbol"}), 400

    pair_obj = next(
        (
            p
            for p in ALL_PAIRS
            if p.get("symbol") == symbol or p.get("display") == symbol
        ),
        None,
    )
    if not pair_obj:
        return jsonify({"error": f"Unknown symbol: {symbol}"}), 404

    requested_style = d.get("style", "auto")
    include_intermarket = d.get("includeIntermarket")
    if include_intermarket is None:
        ic = CONFIG.get("INTERMARKET_CONFIRMATION") or {}
        include_intermarket = bool(ic.get("enabled", False))

    intermarket_snapshot = None
    if include_intermarket:
        try:
            from intermarket import build_scan_snapshot

            intermarket_snapshot = build_scan_snapshot(
                ALL_PAIRS,
                disabled_pairs=_disabled_pairs,
                etf_pairs=ETF_PAIRS,
                fetch_candles=fetch_candles,
                config=CONFIG,
            )
        except Exception as e:
            log.warning(
                f"[PAIR-SCAN] Intermarket snapshot unavailable for {pair_obj.get('display')}: {e}"
            )

    try:
        btc_bias = (
            _current_btc_bias() if pair_obj.get("type") == "crypto" else "neutral"
        )
        resolved_style = _resolve_scan_style(
            _normalize_style(requested_style), pair_obj
        )

        # Preload market state for all TFs so confirmed-only policy matches
        # the full-scan path — prevents forming-bar contamination in non-MT5
        # assets and ensures consistent candle splitting.
        _preloaded_state = {}
        _preloaded_candles = {}
        _preloaded_meta = {}
        _lim = scan_candle_limits()
        for _tf in ("D1", "H4", "H1"):
            try:
                if pair_obj.get("source") == "mt5":
                    _state = _engine_b_live_market_state(pair_obj, _tf, _lim[_tf])
                else:
                    _state = fetch_market_state(pair_obj, _tf, _lim[_tf])
                _preloaded_state[_tf] = _state
                _preloaded_candles[_tf] = _market_state_series(_state, include_forming=True)
                _preloaded_meta[_tf] = _get_candle_fetch_meta(pair_obj, _tf, _lim[_tf])
            except Exception:
                pass

        signal = analyze_pair(
            pair_obj,
            btc_bias,
            style=resolved_style,
            intermarket_snapshot=intermarket_snapshot,
            preloaded_market_state=_preloaded_state,
            preloaded_candles=_preloaded_candles,
            preloaded_fetch_meta=_preloaded_meta,
        )
        if not signal:
            return (
                jsonify({"error": "Engine A analysis unavailable for this pair"}),
                422,
            )
        return jsonify(
            _json_safe(
                {
                    "pair": {
                        "symbol": pair_obj.get("symbol"),
                        "display": pair_obj.get("display"),
                        "type": pair_obj.get("type"),
                        "enabled": bool(pair_obj.get("enabled", True)),
                        "source": pair_obj.get("source"),
                    },
                    "signal": signal,
                    "style": resolved_style,
                    "intermarketIncluded": bool(intermarket_snapshot),
                }
            )
        )
    except Exception as e:
        log.error(f"api_pair_scan error [{pair_obj.get('display')}]: {e}")
        return jsonify({"error": "Pair scan failed"}), 500


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
    style: str | None,
    score_group: str | None = None,
    asset_type: str | None = None,
) -> tuple[str, dict]:
    resolved = _normalize_style(style)
    if resolved == "auto":
        resolved = "intraday"  # Engine B walks H4 bars - intraday is the natural default
    # TFs come from market_structure.resolve_engine_b_tfs (single source of truth).
    # asset_type defaults to "forex" when caller omits it; this matches legacy behaviour
    # for tests / callers that don't supply pair context.
    from market_structure import resolve_engine_b_tfs
    _tf_asset = (asset_type or "forex").lower()
    _tf_scalp = resolve_engine_b_tfs(_tf_asset, "scalp")
    _tf_intra = resolve_engine_b_tfs(_tf_asset, "intraday")
    _tf_swing = resolve_engine_b_tfs(_tf_asset, "swing")
    profiles = {
        "scalp": {
            "min_score": 3.0,
            "min_room_atr": 0.35,
            "min_rr": 1.0,
            "fallback_rr": 1.4,
            "require_macro_align": False,
            "zone_tf": _tf_scalp["zone"],
            "entry_tf": _tf_scalp["trigger"],
            "atr_tf": _tf_scalp["atr"],
        },
        "intraday": {
            "min_score": 4.0,
            "min_room_atr": 0.7,
            "min_rr": 1.2,
            "fallback_rr": 1.8,
            "require_macro_align": False,
            "zone_tf": _tf_intra["zone"],
            "entry_tf": _tf_intra["trigger"],
            "atr_tf": _tf_intra["atr"],
        },
        "swing": {
            "min_score": 4.0,
            "min_room_atr": 1.0,
            "min_rr": 1.6,
            "fallback_rr": 2.2,
            "require_macro_align": False,
            "zone_tf": _tf_swing["zone"],
            "entry_tf": _tf_swing["trigger"],
            "atr_tf": _tf_swing["atr"],
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

    resolved_profile["style"] = resolved
    if score_group:
        resolved_profile["score_group"] = score_group
    # Propagate live structure gate toggle into the profile dict consumed by calculate_confidence().
    if not CONFIG.get("ENGINE_B_STRUCTURE_GATE_ENABLED", True):
        resolved_profile["disable_structure_gate"] = True
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
        "MOMENTUM_BURST": "TRENDING",
        "RANGE_REVERSION": "RANGING",
        "MEAN_REVERSION": "RANGING",
        "CONSOLIDATION_BREAK": "HIGH_VOLATILITY",
        "LOW_VOL_ENV": "LOW_VOLATILITY",
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
        _use_forming_structure = bool(CONFIG.get("ENGINE_B_USE_FORMING_FOR_STRUCTURE", False))
        _use_forming_trigger = bool(CONFIG.get("ENGINE_B_USE_FORMING_FOR_TRIGGER", True))
        if pair_obj.get("source") == "mt5":
            d1_state = _engine_b_live_market_state(pair_obj, "D1", _clim["D1"])
            h4_state = _engine_b_live_market_state(pair_obj, "H4", _clim["H4"])
            h1_state = _engine_b_live_market_state(pair_obj, "H1", _clim["H1"])
            d1 = _market_state_series(d1_state, include_forming=_use_forming_structure)
            h4 = _market_state_series(h4_state, include_forming=_use_forming_structure)
            h1 = _market_state_series(h1_state, include_forming=_use_forming_structure)
            h4_trigger = _market_state_series(h4_state, include_forming=_use_forming_trigger)
            h1_trigger = _market_state_series(h1_state, include_forming=_use_forming_trigger)
            is_forming_b = any(
                bool(state.get("is_live"))
                for state in (d1_state, h4_state, h1_state)
                if isinstance(state, dict)
            )
        else:
            from candle_manager import fetch_market_state as _fms

            d1_state = _fms(pair_obj, "D1", _clim["D1"])
            h4_state = _fms(pair_obj, "H4", _clim["H4"])
            h1_state = _fms(pair_obj, "H1", _clim["H1"])
            d1 = _market_state_series(d1_state, include_forming=_use_forming_structure)
            h4 = _market_state_series(h4_state, include_forming=_use_forming_structure)
            h1 = _market_state_series(h1_state, include_forming=_use_forming_structure)
            h4_trigger = _market_state_series(h4_state, include_forming=_use_forming_trigger)
            h1_trigger = _market_state_series(h1_state, include_forming=_use_forming_trigger)
            is_forming_b = any(
                bool(state.get("is_live"))
                for state in (d1_state, h4_state, h1_state)
                if isinstance(state, dict)
            )

        if not d1 or not h4 or not h1:
            return None, pair_obj, "Failed to fetch D1/H4/H1 candles"

        _pair_score_group = get_pair_score_group(pair_obj)
        _requested_style_naked = sig.get("style", "auto")
        resolved_style, style_profile = _naked_scan_style_profile(
            _requested_style_naked,
            score_group=_pair_score_group,
            asset_type=pair_obj.get("type", ""),
        )
        _zone_tf = str(style_profile.get("zone_tf", "H4")).upper()
        _entry_tf = str(style_profile.get("entry_tf", "H1")).upper()
        _atr_tf = str(style_profile.get("atr_tf", "H4")).upper()
        _tf_map = {"D1": d1, "H4": h4, "H1": h1}
        _trigger_tf_map = {"D1": d1, "H4": h4_trigger, "H1": h1_trigger}
        zone_candles = _tf_map.get(_zone_tf, h4)
        structure_entry_candles = _tf_map.get(_entry_tf, h1)
        trigger_candles = _trigger_tf_map.get(_entry_tf, structure_entry_candles)
        atr_candles = _tf_map.get(_atr_tf, zone_candles)
        if not zone_candles or not structure_entry_candles or not trigger_candles or not atr_candles:
            return None, pair_obj, (
                f"Failed to resolve Engine B candles "
                f"(zone={_zone_tf}:{len(zone_candles or [])}, "
                f"entry={_entry_tf}:{len(structure_entry_candles or [])}, "
                f"trigger={_entry_tf}:{len(trigger_candles or [])}, "
                f"atr={_atr_tf}:{len(atr_candles or [])})"
            )

        # Structure is confirmed-bar first; trigger candles may include the live bar by config.

        atr_highs = [float(c["high"]) for c in atr_candles]
        atr_lows = [float(c["low"]) for c in atr_candles]
        atr_closes = [float(c["close"]) for c in atr_candles]

        log.info(
            f"[NAKED-AI] {pair_obj.get('display')}: {_atr_tf} ATR candles={len(atr_candles)}, sample_high={atr_highs[-1] if atr_highs else 'N/A'}"
        )

        atr_series = calc_atr(atr_highs, atr_lows, atr_closes, 14)
        atr = (
            float(atr_series[-1]) if atr_series and atr_series[-1] is not None else 0.0
        )
        if (
            pair_obj.get("type") == "crypto"
            and str(CONFIG.get("ENGINE_B_CRYPTO_LEVELS_FEED", "bybit")).lower() == "bybit"
        ):
            bybit_atr = _bybit_atr_for_levels(pair_obj, resolved_style)
            if bybit_atr:
                atr = float(bybit_atr)
            elif not bool(CONFIG.get("ENGINE_B_CRYPTO_LEVELS_SIGNAL_FEED_FALLBACK", False)):
                atr = 0.0

        log.info(
            f"[NAKED-AI] {pair_obj.get('display')}: ATR tf={_atr_tf} series length={len(atr_series) if atr_series else 0}, final_ATR={atr}"
        )

        if not atr or atr <= 0:
            log.warning(
                f"[NAKED-AI] {pair_obj.get('display')}: Failed ATR calc - series={atr_series}, using fallback ATR"
            )
            current_price = float(sig.get("price") or (trigger_candles or structure_entry_candles)[-1]["close"])
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

        current_price = float(sig.get("price") or (trigger_candles or structure_entry_candles)[-1]["close"])

        from market_structure import (
            NakedEngine,
            _engine_b_regime_gate,
            engine_b_confidence_passes,
        )

        engine = NakedEngine()
        regime_label = _engine_b_regime_label(
            zone_candles,
            pair_obj.get("type", "stock"),
            engine_a_ctx.get("regime") if isinstance(engine_a_ctx, dict) else None,
        )
        _na_d1_snap = {}
        _na_h4_snap = {}
        try:
            _na_d1_snap = (
                calc_indicators_with_normalized(d1, pair_obj.get("type", "stock")) or {}
            ).get("snap") or {}
            _na_h4_snap = (
                calc_indicators_with_normalized(zone_candles, pair_obj.get("type", "stock")) or {}
            ).get("snap") or {}
        except Exception:
            pass
        res = engine.set_registry_context(
            pair_obj.get("symbol") or pair_obj.get("display")
        ).analyze_structure(
            d1,
            zone_candles,
            structure_entry_candles,
            current_price,
            direction,
            atr,
            regime_label,
            asset_type=pair_obj.get("type", ""),
            d1_snap=_na_d1_snap,
            h4_snap=_na_h4_snap,
            style=resolved_style,
            pair=pair_obj,
        )

        try:
            from ai_learning import get_ai_learning_context

            learning_ctx = get_ai_learning_context(
                pair_obj.get("display"), pair_obj.get("type", "crypto"), _AUDIT_DB
            )
        except Exception as e:
            log.warning(f"Failed to fetch AI learning context for Naked Analysis: {e}")
            learning_ctx = None

        _pair_type = pair_obj.get("type", "")
        conf = engine.calculate_confidence(
            res,
            current_price,
            direction,
            learning_ctx,
            entry_candles=trigger_candles,
            style_profile=style_profile,
        )
        _gate_ok, _min_score_scaled = engine_b_confidence_passes(
            conf, style_profile, regime_label, _pair_type
        )
        _regime_gate = _engine_b_regime_gate(regime_label, _pair_type)
        res["min_score_used"] = int(_min_score_scaled)
        res["regime_gate"] = _regime_gate
        res.update(conf)
        res["checklist_passed"] = bool(conf.get("passed"))
        res["passed"] = bool(_gate_ok)
        res["current_price"] = current_price
        res["direction"] = direction
        res["style"] = resolved_style
        res["regime"] = regime_label
        res["is_forming"] = is_forming_b
        # Expose execution-resolved levels as final levels for consumers
        res["final_stop_loss"] = conf.get("execution_sl") or res.get("recommended_stop_loss")
        res["final_take_profit"] = conf.get("execution_tp") or res.get("recommended_take_profit")
        res["rr_used_for_gate"] = conf.get("rr_used_for_gate", conf.get("rr", 0.0))

        try:
            record_signal_event(
                engine="engine_b",
                score=conf.get("score"),
                max_score=conf.get("max_possible"),
                passed=bool(_gate_ok),
                expected_prob=(conf.get("pct", 0) or 0) / 100.0,
                feature_map={
                    "structure_ok": conf.get("structure_ok"),
                    "location_ok": conf.get("location_ok"),
                    "trigger_ok": conf.get("trigger_ok"),
                    "rr_ok": conf.get("rr_ok"),
                    "room_ok": conf.get("room_ok"),
                    "macro_ok": conf.get("macro_ok"),
                    "bos_mtf_confirmed": conf.get("bos_mtf_confirmed"),
                    "ob_at_zone": conf.get("ob_at_zone"),
                    "rr": conf.get("rr"),
                },
                db_path=_AUDIT_DB,
                meta={
                    "pair": pair_obj.get("display"),
                    "style": resolved_style,
                    "regime": regime_label,
                    "min_score_used": int(_min_score_scaled),
                    "lifecycle_state": conf.get("lifecycle_state", "unknown"),
                    "lifecycle_reason": conf.get("lifecycle_reason", ""),
                },
            )
        except Exception as _ssi_err:
            log.debug(f"[SSI] Engine B sample skipped: {_ssi_err}")

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
                    xai_api_key=get_ai_api_key(CONFIG),
                    xai_model=get_ai_model(CONFIG, "AI_MODEL", "grok-4.3"),
                    engine_a_ctx=engine_a_ctx,
                    news_ctx=_news_ctx,
                    freshness_ctx=engine_a_ctx if isinstance(engine_a_ctx, dict) else None,
                    asset_type=pair_obj.get("type"),
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

        # ── Phase 1: AI Reconciliation layer ─────────────────────────────────
        # Compare Marcus Reid (text) vs any Vision result available for this pair.
        # Output is display-only - no scoring effect.
        try:
            from ai_reconciliation import compute_ai_agreement, reconcile_style_ratings
            _vision_cache_key = (
                pair_obj.get("symbol") or pair_obj.get("display") or ""
            ).replace("/", "_")
            _cached_vision = _engine_b_cache_get(_vision_cache_key + "_vision") or {}
            _marcus_result = res.get("ai_analysis")
            _reconciled = compute_ai_agreement(_marcus_result, _cached_vision or None)
            _style_reconciled = reconcile_style_ratings(_marcus_result, _cached_vision or None)
            res["ai_reconciliation"] = {
                **_reconciled,
                "style_reconciliation": _style_reconciled,
            }
        except Exception as _rec_err:
            log.debug(f"[AI_RECONCILE] {pair_obj.get('display')}: skipped ({_rec_err})")
            res["ai_reconciliation"] = {"ai_agreement_label": "UNAVAILABLE"}

        return res, pair_obj, None
    except Exception as e:
        log.error(f"naked_analysis error: {e}")
        return None, pair_obj, "Naked Structure Analysis failed"


@app.route("/api/naked-analysis", methods=["POST"])
def api_naked_analysis():
    d = request.json
    if not d or "signal" not in d:
        return jsonify({"error": "Invalid payload"}), 400

    force_ai = bool(d.get("force_ai", True))
    res, _pair_obj, err = _compute_naked_analysis(d["signal"], force_ai=force_ai)
    if err:
        return jsonify({"error": err}), 500
    # Cache for chart-analysis context lookup
    sig = d["signal"]
    _sym = sig.get("symbol") or sig.get("display") or ""
    if _sym and res:
        _sid = _sym.replace("/", "_").replace("=", "_").replace("^", "_").replace(".", "_")
        _engine_b_cache_put(_sid, res)
        _engine_b_cache_put(_sym, res)
    if isinstance(res, dict):
        active_fvgs = []
        for fvg in res.get("active_fvgs", []) or []:
            if not isinstance(fvg, dict):
                continue
            active_fvgs.append(
                {
                    **fvg,
                    "top": float(fvg.get("top", 0.0)),
                    "bottom": float(fvg.get("bottom", 0.0)),
                    "size": float(fvg.get("size", 0.0)),
                    "mitigated": bool(fvg.get("mitigated", False)),
                    "strength": float(fvg.get("strength", 0.0)),
                    "scan_count": int(fvg.get("scan_count", 1)),
                }
            )
        res = {**res, "active_fvgs": active_fvgs}
    return jsonify(_json_safe(res))


@app.route("/api/compare-engines", methods=["POST"])
def api_compare_engines():
    d = request.get_json(silent=True) or {}
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
        requested_style,
        score_group=_pair_score_group,
        asset_type=pair_obj.get("type", ""),
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

        _ctx_a, _ctx_b = None, None
        try:
            from ai_context import build_ai_calibration_context
            _ctx_a = build_ai_calibration_context(engine_a, engine_source="Engine A")
            _ctx_b = build_ai_calibration_context(engine_b, engine_source="Engine B")
        except Exception as _ctx_err:
            log.debug("[COMPARE] AI context generation failed: %s", _ctx_err)

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
            _json_safe({
                "engineA": engine_a,
                "engineB": engine_b,
                "summary": summary,
                "aiCalibrationContextA": _ctx_a,
                "aiCalibrationContextB": _ctx_b
            })
        )

    except Exception as e:
        log.error(f"compare_engines error: {e}")
        return jsonify({"error": "Engine comparison failed"}), 500


@app.route("/api/scan-naked", methods=["POST"])
def api_scan_naked():
    global _dxy_h4_cache
    _dxy_h4_cache = None  # fresh DXY fetch per scan batch
    d = request.get_json(silent=True) or {}
    asset_class = str(d.get("assetClass") or "").strip().lower()
    requested_style = d.get("style", "auto")
    debug_mode = d.get("debug", False)

    candidate_pairs = []
    for p in ALL_PAIRS:
        if not p.get("enabled", True):
            continue
        if p["display"] in _disabled_pairs:
            continue
        if asset_class and p.get("type", "").lower() != asset_class:
            continue
        candidate_pairs.append(p)

    results = []
    _best_per_pair = {}

    from market_structure import NakedEngine, engine_b_confidence_passes

    from concurrent.futures import ThreadPoolExecutor, as_completed

    def _scan_pair(pair, debug_mode=False):
        debug_row = {
            "pair": pair.get("display"),
            "symbol": pair.get("symbol"),
            "style": None,
            "zone_tf": None,
            "entry_tf": None,
            "atr_tf": None,
            "zone_count": 0,
            "entry_count": 0,
            "d1_count": 0,
            "atr_count": 0,
            "atr_value": None,
            "volatility_gate_passed": True,
            "tested_long": False,
            "tested_short": False,
            "long_structural_verdict": None,
            "short_structural_verdict": None,
            "long_confidence_score": None,
            "short_confidence_score": None,
            "long_passed": False,
            "short_passed": False,
            "failed_gate_names": [],
            "final_reject_reason": None,
            "direction_details": {}
        }

        try:
            engine = NakedEngine()

            _pair_score_group = get_pair_score_group(pair)
            resolved_style, style_profile = _naked_scan_style_profile(
                requested_style,
                score_group=_pair_score_group,
                asset_type=pair.get("type", ""),
            )
            debug_row["style"] = resolved_style

            # Timeframes resolved by market_structure.resolve_engine_b_tfs (single source of truth)
            _zone_tf = style_profile.get("zone_tf", "H4")
            _entry_tf = style_profile.get("entry_tf", "H1")
            _atr_tf = style_profile.get("atr_tf", "H4")
            # Always load H4/H1/D1 so candleFetchMeta + execution freshness match analyze_pair/risk_check.
            _needed_tfs = list({_zone_tf, _entry_tf, _atr_tf, "D1", "H4", "H1"})
            _lim_b = scan_candle_limits()

            debug_row["zone_tf"] = _zone_tf
            debug_row["entry_tf"] = _entry_tf
            debug_row["atr_tf"] = _atr_tf

            # Fetch all needed timeframes
            _tf_map = {}
            _tf_trigger_map = {}
            _tf_state_map = {}
            _tf_raw_map: dict[str, list] = {}
            _use_forming_structure = bool(CONFIG.get("ENGINE_B_USE_FORMING_FOR_STRUCTURE", False))
            _use_forming_trigger = bool(CONFIG.get("ENGINE_B_USE_FORMING_FOR_TRIGGER", True))
            for tf in _needed_tfs:
                cfg_key = f"{tf.upper()}_CANDLES"
                if cfg_key not in CONFIG:
                    scalp_cfg = CONFIG.get("SCALP_ENGINE", {}) or {}
                    if cfg_key in scalp_cfg:
                        limit = int(scalp_cfg[cfg_key])
                    else:
                        cfg_key = "H4_CANDLES"
                        limit = int(CONFIG[cfg_key])
                else:
                    limit = int(CONFIG[cfg_key])
                if pair.get("source") == "mt5":
                    state = _engine_b_live_market_state(pair, tf, limit)
                    _tf_state_map[tf] = state
                    _tf_raw_map[tf] = _market_state_series(state, include_forming=True)
                    _tf_map[tf] = _market_state_series(
                        state, include_forming=_use_forming_structure
                    )
                    _tf_trigger_map[tf] = _market_state_series(
                        state, include_forming=_use_forming_trigger
                    )
                else:
                    raw = fetch_candles(pair, tf, limit)
                    from athena_app.services.market_state import (
                        market_state_offset_hours,
                        split_market_state,
                    )

                    _state = split_market_state(
                        list(raw or []),
                        tf,
                        pair.get("display") or pair.get("symbol") or "",
                        offset_hours=market_state_offset_hours(pair, tf),
                    )
                    _tf_state_map[tf] = _state
                    _tf_raw_map[tf] = list(raw or [])
                    _tf_map[tf] = _market_state_series(
                        _state, include_forming=_use_forming_structure
                    )
                    _tf_trigger_map[tf] = _market_state_series(
                        _state, include_forming=_use_forming_trigger
                    )

            zone_candles = _tf_map.get(_zone_tf, [])
            structure_entry_candles = _tf_map.get(_entry_tf, [])
            entry_candles = _tf_trigger_map.get(_entry_tf, _tf_map.get(_entry_tf, []))
            d1_candles = _tf_map.get("D1", [])
            atr_candles = _tf_map.get(_atr_tf, zone_candles)
            _engine_b_is_forming = any(
                bool((_tf_state_map.get(tf) or {}).get("is_live"))
                for tf in (_zone_tf, _entry_tf, _atr_tf, "D1")
            )

            debug_row["zone_count"] = len(zone_candles)
            debug_row["entry_count"] = len(entry_candles)
            debug_row["d1_count"] = len(d1_candles)
            debug_row["atr_count"] = len(atr_candles)

            if len(zone_candles) < 10 or len(entry_candles) < 10:
                debug_row["final_reject_reason"] = "insufficient_candles"
                if debug_mode:
                    return {"debug": debug_row}
                return []

            # ATR from the style's designated timeframe
            _atr_highs = [float(c["high"]) for c in atr_candles]
            _atr_lows = [float(c["low"]) for c in atr_candles]
            _atr_closes = [float(c["close"]) for c in atr_candles]
            atr_series = calc_atr(_atr_highs, _atr_lows, _atr_closes, 14)
            atr = float(atr_series[-1]) if atr_series else 0.0
            if (
                pair.get("type") == "crypto"
                and str(CONFIG.get("ENGINE_B_CRYPTO_LEVELS_FEED", "bybit")).lower() == "bybit"
            ):
                bybit_atr = _bybit_atr_for_levels(pair, resolved_style)
                if bybit_atr:
                    atr = float(bybit_atr)
                    debug_row["atr_feed"] = "bybit"
                elif not bool(CONFIG.get("ENGINE_B_CRYPTO_LEVELS_SIGNAL_FEED_FALLBACK", False)):
                    debug_row["final_reject_reason"] = "bybit_atr_unavailable"
                    if debug_mode:
                        return {"debug": debug_row}
                    return []

            debug_row["atr_value"] = atr

            if not atr or atr <= 0:
                debug_row["final_reject_reason"] = "invalid_atr"
                if debug_mode:
                    return {"debug": debug_row}
                return []

            # Volatility gate
            if len(atr_series) >= 50:
                _valid_atrs = [a for a in atr_series[-50:] if a and a > 0]
                _atr_avg = sum(_valid_atrs) / len(_valid_atrs) if _valid_atrs else 0
                if _atr_avg > 0 and atr < _atr_avg * 0.6:
                    debug_row["volatility_gate_passed"] = False
                    debug_row["final_reject_reason"] = "volatility_gate"
                    log.debug(f"[NAKED-DBG] {pair['display']}: ATR={atr:.6f} < 60% avg={_atr_avg:.6f} - VOL GATE")
                    if debug_mode:
                        return {"debug": debug_row}
                    return []

            current_price = float(entry_candles[-1]["close"])

            # Test both directions
            # analyze_structure uses: arg2 (h4 slot) for zones/macro, arg3 (h1 slot) for micro/BOS/sweep
            regime_label = _engine_b_regime_label(zone_candles, pair.get("type", "stock"))
            _eb_d1_snap = {}
            _eb_zone_snap = {}
            try:
                if len(d1_candles) >= 20:
                    _eb_d1_snap = (
                        calc_indicators_with_normalized(
                            d1_candles, pair.get("type", "stock")
                        )
                        or {}
                    ).get("snap") or {}
                if len(zone_candles) >= 20:
                    _eb_zone_snap = (
                        calc_indicators_with_normalized(
                            zone_candles, pair.get("type", "stock")
                        )
                        or {}
                    ).get("snap") or {}
            except Exception:
                pass
            local_results = []
            _best_signal = None
            def _build_direction_debug(direction, verdict, res, conf_data, threshold, fail_gates):
                _conf = conf_data or {}
                _res = res if isinstance(res, dict) else {}
                _failed_gate_names = list(_conf.get("failed_gate_names") or fail_gates)
                _price_action_trigger = bool(_res.get("trigger_ok"))
                _entry_ok = bool(_conf.get("entry_ok", False))
                _bos_volume = bool(_res.get("bos_volume_confirmed"))
                _trigger_type_found = "NONE"
                if _price_action_trigger:
                    _trigger_type_found = _res.get("trigger_pattern", "PRICE_ACTION")
                elif bool(_conf.get("breakout_ok")) and _bos_volume:
                    _trigger_type_found = "BOS_BREAKOUT_WITH_VOLUME"
                elif bool(_res.get("liquidity_sweep")) and _entry_ok:
                    _trigger_type_found = "LIQUIDITY_SWEEP_AT_ZONE"
                elif bool(_res.get("choch_confirmed")) and _entry_ok:
                    _trigger_type_found = "CHOCH_AT_ZONE"
                return {
                    "symbol": pair.get("symbol"),
                    "direction": direction,
                    "score": _conf.get("score"),
                    "structural_verdict": verdict,
                    "confidence_score": _conf.get("score"),
                    "threshold": threshold,
                    "passed_all_gates": bool(_conf.get("passed", False)),
                    "passed_all_non_trigger_gates": (
                        "struct" not in _failed_gate_names
                        and "loc" not in _failed_gate_names
                        and not any(str(g).startswith("rr=") or g == "rr_gate" for g in _failed_gate_names)
                    ),
                    "final_engine_b_passed": False,
                    "trigger_failed": ("trigger" in _failed_gate_names) if conf_data else None,
                    "rr_failed": any(str(g).startswith("rr=") or g == "rr_gate" for g in _failed_gate_names) if conf_data else None,
                    "production_pass_flag": False,
                    "debug_gate_failed_names": _failed_gate_names,
                    "failed_gate_names": _failed_gate_names,
                    "final_reject_reason": None,
                    "trigger_required": True,
                    "trigger_timeframe": _conf.get("trigger_timeframe", _res.get("trigger_timeframe")),
                    "trigger_detected": _entry_ok,
                    "trigger_type_checked": "price_action_or_structural_catalyst",
                    "trigger_type_found": _conf.get("trigger_type") or _trigger_type_found,
                    "candle_pattern_state": {
                        "pattern": _res.get("trigger_pattern", "NONE"),
                        "rejection": bool(_res.get("rejection_candle")),
                        "engulfing": bool(_res.get("engulfing_candle")),
                        "inside_break": bool(_res.get("inside_break_candle")),
                        "strong_close": bool(_res.get("strong_close")),
                    },
                    "sweep_state": _res.get("sweep_data"),
                    "bos_choch_trigger_state": {
                        "bos_confirmed": bool(_res.get("bos_confirmed")),
                        "bos_volume_confirmed": _bos_volume,
                        "choch_confirmed": bool(_res.get("choch_confirmed")),
                        "liquidity_sweep": bool(_res.get("liquidity_sweep")),
                        "entry_ok": _entry_ok,
                    },
                    "displacement_state": {
                        "strong_close": bool(_res.get("strong_close")),
                        "inside_break": bool(_res.get("inside_break_candle")),
                        "engulfing": bool(_res.get("engulfing_candle")),
                    },
                    "volume_confirmation_state": {
                        "bos_volume_confirmed": _bos_volume,
                    },
                    "exact_trigger_reject_reason": (
                        "no_price_action_or_structural_catalyst" if "trigger" in _failed_gate_names else None
                    ),
                    "rr_used_for_gate": _conf.get("rr_used_for_gate"),
                    "rr_source": _conf.get("rr_source"),
                    "structural_rr": _conf.get("structural_rr"),
                    "execution_rr": _conf.get("execution_rr"),
                    "execution_sl": _conf.get("execution_sl"),
                    "execution_tp": _conf.get("execution_tp"),
                    "fallback_tp_applied": _conf.get("fallback_tp_applied"),
                    "fallback_tp_reason": _conf.get("fallback_tp_reason"),
                    "location_passed": _conf.get("location_passed", _conf.get("location_ok")),
                    "location_mode": _conf.get("location_mode"),
                    "location_distance_atr": _conf.get("location_distance_atr"),
                    "trigger_passed": _conf.get("trigger_passed", _conf.get("trigger_ok")),
                    "trigger_type": _conf.get("trigger_type"),
                    "entry": res.get("current_price") if isinstance(res, dict) else None,
                    "stop": res.get("recommended_stop_loss") if isinstance(res, dict) else None,
                }

            for direction in ["LONG", "SHORT"]:
                if direction == "LONG":
                    debug_row["tested_long"] = True
                else:
                    debug_row["tested_short"] = True

                res = engine.set_registry_context(
                    pair.get("symbol") or pair.get("display")
                ).analyze_structure(
                    d1_candles,
                    zone_candles,
                    structure_entry_candles,
                    current_price,
                    direction,
                    atr,
                    regime_label,
                    fallback_rr=style_profile.get("fallback_rr", 2.0),
                    asset_type=pair.get("type", ""),
                    d1_snap=_eb_d1_snap,
                    h4_snap=_eb_zone_snap,
                    style=resolved_style,
                    pair=pair,
                )

                verdict = res.get("structural_verdict", "NONE")
                seq = res.get("current_swing_sequence", "")

                if direction == "LONG":
                    debug_row["long_structural_verdict"] = verdict
                else:
                    debug_row["short_structural_verdict"] = verdict

                if verdict != "CLEAR":
                    log.debug(f"[NAKED-DBG] {pair['display']} {direction}: verdict={verdict} seq={seq} - SKIPPED")
                    _direction_debug = _build_direction_debug(
                        direction, verdict, res, {}, None, ["struct"]
                    )
                    _direction_debug["final_reject_reason"] = f"structural_verdict_{verdict}"
                    debug_row["direction_details"][direction] = _direction_debug
                    continue

                conf_data = engine.calculate_confidence(
                    res,
                    current_price,
                    direction,
                    entry_candles=entry_candles,
                    style_profile=style_profile,
                )

                if direction == "LONG":
                    debug_row["long_confidence_score"] = conf_data.get("score", 0)
                else:
                    debug_row["short_confidence_score"] = conf_data.get("score", 0)

                # Capture crypto target model debug fields from res
                debug_row["crypto_target_old_target"] = res.get("crypto_target_old_target")
                debug_row["crypto_target_old_rr"] = res.get("crypto_target_old_rr")
                debug_row["crypto_target_tp1"] = res.get("crypto_target_tp1")
                debug_row["crypto_target_tp2"] = res.get("crypto_target_tp2")
                debug_row["crypto_target_new_rr"] = res.get("crypto_target_new_rr")
                debug_row["crypto_target_selection_mode"] = res.get("crypto_target_selection_mode")
                debug_row["crypto_target_reject_reason"] = res.get("crypto_target_reject_reason")
                debug_row["crypto_target_path_clear_to_tp2"] = res.get("crypto_target_path_clear_to_tp2")
                debug_row["crypto_target_atr_multiple"] = res.get("crypto_target_atr_multiple")

                _gate_ok, _min_score_scaled = engine_b_confidence_passes(
                    conf_data,
                    style_profile,
                    regime_label,
                    pair.get("type", ""),
                )

                _fail_gates = []
                if not conf_data.get("structure_ok"):
                    _fail_gates.append("struct")
                if not conf_data.get("location_ok"):
                    _fail_gates.append("loc")
                if not conf_data.get("entry_ok"):
                    _fail_gates.append("trigger")
                if not conf_data.get("rr_ok"):
                    _fail_gates.append(f"rr={conf_data.get('rr', 0):.1f}")
                _conf_failed_gates = list(conf_data.get("failed_gate_names") or _fail_gates)
                _direction_debug = _build_direction_debug(
                    direction, verdict, res, conf_data, _min_score_scaled, _conf_failed_gates
                )

                if not _gate_ok:
                    debug_row["failed_gate_names"].extend(_conf_failed_gates)
                    _direction_debug["final_reject_reason"] = f"gate_failures: {','.join(_conf_failed_gates)}"
                    debug_row["direction_details"][direction] = _direction_debug

                    log.debug(
                        f"[NAKED-DBG] {pair['display']} {direction}: "
                        f"score={conf_data['score']:.1f} vs min={_min_score_scaled:.1f}, "
                        f"passed={conf_data.get('passed')}, regime={regime_label}, "
                        f"fails=[{','.join(_conf_failed_gates)}] - REJECTED"
                    )
                    continue

                _res = dict(res)
                _res.update(conf_data)
                _res["current_price"] = current_price
                _res["symbol"] = pair.get("symbol")
                _res["display"] = pair.get("display")
                _res["type"] = pair.get("type")
                _res["scoreGroup"] = _pair_score_group
                _res["direction"] = direction
                _res["style"] = resolved_style
                _res["regime"] = regime_label
                _res["zone_tf"] = _zone_tf
                _res["entry_tf"] = _entry_tf
                _res["atr_tf"] = _atr_tf
                _res["is_forming"] = _engine_b_is_forming
                _res["max_score"] = conf_data.get("max_possible")
                _res["confidence_max"] = conf_data.get("max_possible")
                _res["min_score"] = _min_score_scaled
                _res["min_rr"] = style_profile.get("min_rr")

                sl = conf_data.get("execution_sl") or _res.get("recommended_stop_loss")
                tp = conf_data.get("execution_tp") or _res.get("recommended_take_profit")
                rr = conf_data.get("rr_used_for_gate", conf_data.get("rr", 0.0))

                _crypto_requires_structural_target = (
                    pair.get("type") == "crypto"
                    and bool(CONFIG.get("ENGINE_B_CRYPTO_PROFILE_ENABLED", False))
                    and bool(
                        CONFIG.get(
                            "ENGINE_B_CRYPTO_REQUIRE_STRUCTURAL_TARGET_FOR_PASS", True
                        )
                    )
                )
                if not tp and sl and not _crypto_requires_structural_target:
                    sl_dist = abs(current_price - sl)
                    if direction == "LONG":
                        tp = current_price + (sl_dist * style_profile["fallback_rr"])
                    else:
                        tp = current_price - (sl_dist * style_profile["fallback_rr"])
                    _res["recommended_take_profit"] = tp
                    _res["fallback_tp_applied"] = True
                elif not tp and sl and _crypto_requires_structural_target:
                    _res["fallback_tp_applied"] = False
                    _res["fallback_blocked_reason"] = "crypto_requires_structural_target"

                if sl and tp and rr <= 0:
                    sl_dist = abs(current_price - sl)
                    tp_dist = abs(tp - current_price)
                    rr = (tp_dist / sl_dist) if sl_dist > 0 else 0.0
                if rr < style_profile["min_rr"]:
                    debug_row["failed_gate_names"].append(f"rr_gate")
                    if "rr_gate" not in _direction_debug["debug_gate_failed_names"]:
                        _direction_debug["debug_gate_failed_names"].append("rr_gate")
                    _direction_debug["rr_failed"] = True
                    _direction_debug["passed_all_gates"] = False
                    _direction_debug["final_reject_reason"] = (
                        f"rr={rr:.2f} < min_rr={style_profile['min_rr']}"
                    )
                    debug_row["direction_details"][direction] = _direction_debug
                    log.debug(
                        f"[NAKED-DBG] {pair['display']} {direction}: "
                        f"rr={rr:.2f} < min_rr={style_profile['min_rr']} - REJECTED"
                    )
                    continue

                # Signal passed all gates
                if direction == "LONG":
                    debug_row["long_passed"] = True
                else:
                    debug_row["short_passed"] = True
                _direction_debug["final_engine_b_passed"] = True
                _direction_debug["production_pass_flag"] = True
                _direction_debug["debug_gate_failed_names"] = []
                debug_row["direction_details"][direction] = _direction_debug

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
                    "maxScore": conf_data.get("max_possible"),
                    "threshold": _min_score_scaled,
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
                    "isForming": _engine_b_is_forming,
                    "requestedStyle": requested_style,
                    "style": resolved_style,
                    "atr": atr,
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
                    "style_levels": _build_style_levels(
                        price=float(current_price),
                        atr=float(atr),
                        direction=direction,
                        pair_type=get_pair_level_atr_class(pair),
                    ),
                    "naked_data": _res,
                }

                signal["candleFetchMeta"] = {
                    "D1": _get_candle_fetch_meta(pair, "D1", _lim_b["D1"]),
                    "H4": _get_candle_fetch_meta(pair, "H4", _lim_b["H4"]),
                    "H1": _get_candle_fetch_meta(pair, "H1", _lim_b["H1"]),
                    "pairSource": pair.get("source"),
                }
                signal.setdefault("executable", True)
                try:
                    from athena_app.services.data_freshness import (
                        check_live_candle_consistency,
                        evaluate_execution_data_freshness,
                    )

                    _time_now_b = datetime.now(timezone.utc).timestamp()
                    for _tf_u in ("H4", "H1", "D1"):
                        _state_u = _tf_state_map.get(_tf_u)
                        if not isinstance(_state_u, dict):
                            continue
                        _tf_candles_u = list(_tf_raw_map.get(_tf_u) or [])
                        if pair.get("source") == "mt5" and pair.get("type") == "forex":
                            _engine_a_input_u = list(_state_u.get("confirmed") or [])
                        else:
                            _engine_a_input_u = list(_state_u.get("confirmed") or [])
                            if _state_u.get("forming"):
                                _engine_a_input_u.append(_state_u["forming"])
                        _engine_b_input_u = list(_state_u.get("confirmed") or [])
                        _scanner_input_u = list(_engine_a_input_u)
                        _consistency_paths_b = {
                            "raw_provider": _tf_candles_u,
                            "market_state": _state_u,
                            "engine_a": _engine_a_input_u,
                            "engine_b": _engine_b_input_u,
                            "scanner": _scanner_input_u,
                            "compare": _scanner_input_u,
                        }
                        _cache_meta_b = signal["candleFetchMeta"].get(_tf_u)
                        if isinstance(_cache_meta_b, dict) and _cache_meta_b:
                            _consistency_paths_b["cache"] = _cache_meta_b
                        _consistency_b = check_live_candle_consistency(
                            pair,
                            _tf_u,
                            _consistency_paths_b,
                            time_now=_time_now_b,
                        )
                        if _consistency_b:
                            signal.setdefault("candleConsistency", {})[_tf_u] = _consistency_b

                    _fresh_b = evaluate_execution_data_freshness(signal, CONFIG)
                    signal["dataFreshness"] = _fresh_b
                    _cons_b = signal.get("candleConsistency", {})
                    _has_confirmed_only_ok_b = (
                        any(
                            (isinstance(d, dict) and d.get("status") == "CONFIRMED_ONLY_OK")
                            or d == "CONFIRMED_ONLY_OK"
                            for d in _cons_b.values()
                        )
                        if isinstance(_cons_b, dict)
                        else False
                    )
                    if _fresh_b.get("warnOnStaleScan") and _fresh_b.get("blocked"):
                        _blocked_b = _fresh_b.get("blocked", [])
                        _filtered_b = [
                            b
                            for b in _blocked_b
                            if not (
                                _has_confirmed_only_ok_b
                                and isinstance(b, dict)
                                and b.get("severity") == "stale_1_bucket"
                            )
                        ]
                        if _filtered_b:
                            _warn_b = "DATA_FRESHNESS: " + "; ".join(
                                f"{b.get('timeframe')} {b.get('severity')}"
                                for b in _filtered_b
                            )
                            signal.setdefault("warnings", [])
                            if _warn_b not in signal["warnings"]:
                                signal["warnings"].append(_warn_b)
                except Exception as _fresh_err_b:
                    log.debug(
                        "[NAKED SCAN] %s data freshness unavailable: %s",
                        pair.get("display", "?"),
                        _fresh_err_b,
                    )

                _fe_b = signal.get("dataFreshness")
                if (
                    CONFIG.get("SIGNAL_EXECUTABLE_FALSE_WHEN_FRESHNESS_BLOCKS", True)
                    and isinstance(_fe_b, dict)
                    and not _fe_b.get("allowed")
                ):
                    signal["executable"] = False

                # Calculate style-specific SL/TP for display
                try:
                    from indicators import calc_levels
                    _style_level_class = get_pair_level_atr_class(pair)
                    _lvl_scalp = calc_levels(current_price, atr, direction,
                                              _style_level_class, style="scalp")
                    _lvl_intra = calc_levels(current_price, atr, direction,
                                              _style_level_class, style="intraday")
                    signal["scalp_sl"] = _lvl_scalp["sl"]
                    signal["scalp_tp"] = _lvl_scalp["tp1"]
                    signal["intraday_sl"] = _lvl_intra["sl"]
                    signal["intraday_tp"] = _lvl_intra["tp1"]
                except Exception:
                    pass

                if _best_signal is None or signal["confluenceScore"] > _best_signal["confluenceScore"]:
                    _best_signal = signal
        except Exception as e:
            debug_row["final_reject_reason"] = f"exception: {str(e)}"
            log.warning(f"[NAKED SCAN] {pair.get('display')} error: {e}")
            if debug_mode:
                return {"debug": debug_row}
            return []

        # If no signal was generated but we tested both directions, set final reject reason
        if _best_signal is None:
            if not debug_row["long_structural_verdict"] and not debug_row["short_structural_verdict"]:
                debug_row["final_reject_reason"] = "no_clear_structural_verdict"
            elif debug_row["failed_gate_names"]:
                debug_row["final_reject_reason"] = f"gate_failures: {','.join(debug_row['failed_gate_names'])}"
            else:
                debug_row["final_reject_reason"] = "unknown"

        if _best_signal is not None:
            local_results.append(_best_signal)

        # Always return debug row in debug mode
        if debug_mode:
            local_results.append({"debug": debug_row})

        return local_results

    _max_workers = max(1, int(CONFIG.get("SCAN_MAX_WORKERS", 3) or 3))
    debug_rows = []

    # REGRESSION CHECK: Engine B scan-funnel tracking
    engine_b_funnel = {
        "total": len(candidate_pairs),
        "no_clear_structure": 0,
        "gate_fail_struct": 0,
        "gate_fail_loc": 0,
        "gate_fail_trigger": 0,
        "gate_fail_rr": 0,
        "passed": 0,
    }

    log.info(f"[DEBUG] Starting scan with {len(candidate_pairs)} candidate pairs, debug_mode={debug_mode}")
    with ThreadPoolExecutor(max_workers=_max_workers) as pool:
        futures = {pool.submit(_scan_pair, pair, debug_mode): pair for pair in candidate_pairs}
        for future in as_completed(futures):
            result = future.result()
            log.debug(f"[DEBUG] Future result type: {type(result)}, length: {len(result) if isinstance(result, list) else 'N/A'}")
            for row in (result or []):
                if debug_mode and row.get("debug"):
                    debug_rows.append(row["debug"])
                    log.debug(f"[DEBUG] Added debug row for {row['debug'].get('pair')}")

                    # REGRESSION CHECK: Track Engine B funnel
                    dbg = row["debug"]
                    if not dbg.get("long_structural_verdict") and not dbg.get("short_structural_verdict"):
                        engine_b_funnel["no_clear_structure"] += 1
                    else:
                        for gate in dbg.get("failed_gate_names", []):
                            if "struct" in gate:
                                engine_b_funnel["gate_fail_struct"] += 1
                            elif "loc" in gate:
                                engine_b_funnel["gate_fail_loc"] += 1
                            elif "trigger" in gate:
                                engine_b_funnel["gate_fail_trigger"] += 1
                            elif "rr" in gate:
                                engine_b_funnel["gate_fail_rr"] += 1
                        if dbg.get("long_passed") or dbg.get("short_passed"):
                            engine_b_funnel["passed"] += 1
                else:
                    results.append(row)
                    _pair_key = row.get("display", "")
                    _existing = _best_per_pair.get(_pair_key)
                    if _existing is None or row["confluenceScore"] > _existing["confluenceScore"]:
                        _best_per_pair[_pair_key] = row
    log.info(f"[DEBUG] Collected {len(debug_rows)} debug rows, {len(results)} signals")

    results = sorted(
        _best_per_pair.values(),
        key=lambda x: x.get("confluenceScore", 0),
        reverse=True,
    )

    # Run Conductor on Engine B signals so widget updates after an Engine B scan
    if results:
        try:
            from conductor import (
                conductor_orchestrate,
                extract_conductor_microstructure,
                reset_scan_results,
            )
            reset_scan_results("engine_b")
            for _sig in results[:30]:
                _vd, _sr = extract_conductor_microstructure(_sig)
                conductor_orchestrate(
                    _sig,
                    _sig.get("regime", "UNKNOWN"),
                    _AUDIT_DB,
                    volume_divergence=_vd,
                    stop_run=_sr,
                )
        except Exception as _cerr:
            log.warning(f"[CONDUCTOR] engine-b scan orchestration failed: {_cerr}")

    if debug_mode:
        # REGRESSION CHECK: Print Engine B scan-funnel summary
        print("\n" + "="*80)
        print("REGRESSION CHECK - ENGINE B SCAN FUNNEL")
        print("="*80)
        print(f"Total pairs scanned: {engine_b_funnel['total']}")
        print(f"No clear structure: {engine_b_funnel['no_clear_structure']}")
        print(f"Gate fail - structure: {engine_b_funnel['gate_fail_struct']}")
        print(f"Gate fail - location: {engine_b_funnel['gate_fail_loc']}")
        print(f"Gate fail - trigger: {engine_b_funnel['gate_fail_trigger']}")
        print(f"Gate fail - RR: {engine_b_funnel['gate_fail_rr']}")
        print(f"Passed: {engine_b_funnel['passed']}")
        print("="*80 + "\n")

        return jsonify(_json_safe({
            "success": True,
            "signals": results,
            "debugRows": debug_rows
        }))

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

        "score":     1.8,               # confluence score (optional, for sizing)

        "maxScore":  3.0               # max possible score (optional)

      }



    Returns: same shape as /api/execute (success, ticket, volume, entryPrice, etc.)

    """

    if not CONFIG.get("EXECUTION_ENABLED", False):
        return jsonify({"error": "Execution disabled in config.yaml"}), 403

    if _kill_switch:
        return jsonify({"error": "Kill-switch active - webhook blocked"}), 503

    d = request.get_json(force=True, silent=True) or {}

    # Optional shared-secret guard (separate from ATHENA_API_KEY header auth)

    _wh_secret = os.environ.get("WEBHOOK_SECRET", "")

    if _wh_secret and d.get("secret", "") != _wh_secret:
        log.warning(f"[WEBHOOK] {request.remote_addr} rejected - invalid secret")

        return jsonify({"error": "Unauthorized - invalid webhook secret"}), 401

    # Build minimal signal dict from webhook payload

    pair_name = d.get("pair", "")

    direction = (d.get("direction") or d.get("side") or "").upper()

    sig_type = d.get("type", "")

    price = d.get("price") or d.get("entry") or 0

    sl = d.get("sl") or d.get("stop") or 0

    tp1 = d.get("tp1") or d.get("tp") or 0

    tp2 = d.get("tp2", 0)
    tp_partial = d.get("tp_partial", 0)

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
        "tp_partial": float(tp_partial) if tp_partial else None,
        "confluenceScore": float(d.get("score", 0.5)),
        "maxScore": float(d.get("maxScore", 3.0)),
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "trendState": d.get("trendState", "DEVELOPING"),
    }

    # Duplicate guard - webhook signals use pair+direction+minute-bucket as key

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
            )

            account = bybit_get_account()

            if not account or account.get("error"):
                return jsonify({"error": "Bybit not connected"}), 503

            _pos_resp = bybit_get_positions()

            if isinstance(_pos_resp, dict) and _pos_resp.get("error"):
                return jsonify({"error": "Positions unavailable - cannot verify exposure"}), 503

            positions = (
                _pos_resp.get("positions", [])
                if isinstance(_pos_resp, dict)
                else (_pos_resp or [])
            )

            symbol_info = bybit_get_symbol_info(pair_name)

            if symbol_info and symbol_info.get("error"):
                symbol_info = None

            _exec_venue = "bybit"

        else:
            from mt5_executor import (
                mt5_get_account,
                mt5_get_positions,
                mt5_get_symbol_info,
            )

            account = mt5_get_account()

            if not account or account.get("error"):
                return jsonify({"error": "MT5 not connected"}), 503

            _pos_resp = mt5_get_positions()

            if isinstance(_pos_resp, dict) and _pos_resp.get("error"):
                return jsonify({"error": "Positions unavailable - cannot verify exposure"}), 503

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

            _exec_venue = "mt5"

        from execution_lifecycle import run_managed_execution

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

        result = run_managed_execution(_exec_venue, sig, approval)

        if result.get("success"):
            executed_signals.add(sig_id)

            try:
                with sqlite3.connect(_AUDIT_DB, timeout=15.0) as con:
                    # CLAUDE.md Hard Rule 23: signal dict uses camelCase.
                    _factors = {
                        "scores": sig.get("factorScores") or sig.get("factor_scores"),
                        "weights": sig.get("factorWeights") or sig.get("factor_weights"),
                        "disabled": sig.get("disabledFactors"),
                        "regime": sig.get("regimeName"),
                    }
                    con.execute(
                        "INSERT INTO audit_log(ts,pair,score,engine,direction,trend,grade,edge_prob,risk,style,"
                        "entry_price,sl,tp,tp_partial,tp2,volume,regime,risk_amount,risk_pct,ticket,factors_json,"
                        "signal_price_ref,slippage_bps) "
                        "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                        (
                            datetime.now(timezone.utc).isoformat(),
                            pair_name,
                            sig.get("confluenceScore"),
                            "external",
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

        return jsonify({"error": "Webhook execution failed - check logs"}), 500


@app.route("/api/close-position", methods=["POST"])
def api_close_position():
    """Manually close an open position on MT5 or Bybit."""

    data = request.get_json(force=True) or {}

    exch = str(data.get("exchange") or "").strip().lower()

    pair = str(data.get("pair") or data.get("symbol") or "").strip()

    direction = str(data.get("direction") or "").strip().upper()

    volume = float(data.get("volume", 0))

    ticket = data.get("ticket")

    # Legacy React payload sent ticket + symbol without exchange - assume MT5.
    if not exch and ticket not in (None, "", 0):
        exch = "mt5"
        log.debug("[CLOSE] Inferred exchange=mt5 from ticket-only payload")

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

        # Immediately capture PnL from MT5 deal history so the audit_log
        # is updated right away (avoids $0.00 when outcome monitor hasn't run yet)
        if result.get("success"):
            try:
                import time as _time
                _time.sleep(0.8)  # brief pause for MT5 to process the deal
                import MetaTrader5 as _mt5_lib_close
                from mt5_executor import mt5_connect as _mt5_connect
                if _mt5_connect():
                    _deals = _mt5_deals_for_audit_ticket(_mt5_lib_close, int(ticket))
                    if _deals:
                        _deals_s = sorted(_deals, key=lambda d: d.time)
                        _close_deal = _deals_s[-1]
                        _total_pnl = sum(float(d.profit) for d in _deals_s)
                        # Fetch audit row for this ticket
                        with sqlite3.connect(_AUDIT_DB, timeout=10.0) as _ac:
                            _ac.row_factory = sqlite3.Row
                            _arow = _ac.execute(
                                "SELECT entry_price, sl, tp, volume, ts, risk_amount, asset_class "
                                "FROM audit_log WHERE ticket=? AND exit_price IS NULL ORDER BY id DESC LIMIT 1",
                                (str(ticket),)
                            ).fetchone()
                        if _arow:
                            # Check for Pepperstone bug and fix if needed
                            entry_price = float(_arow["entry_price"])
                            deal_price = float(_close_deal.price)
                            exit_price_to_use = deal_price

                            if abs(deal_price - entry_price) < 1e-8 and abs(_total_pnl) < 1e-8:
                                # Pepperstone bug detected - deal shows entry price as close price
                                # Try to get live position profit before it closed
                                log.warning(
                                    f"[CLOSE] Pepperstone bug detected for ticket={ticket} "
                                    f"deal_price={deal_price} equals entry_price - attempting fix"
                                )
                                # For manual closes, we can't easily compute the correct price
                                # So we'll leave it as is for now
                                pass

                            # Fix timezone: MT5 deal.time is broker time (UTC+3), convert to UTC
                            broker_timestamp = int(_close_deal.time)
                            utc_timestamp = broker_timestamp - 3 * 3600  # Pepperstone UTC+3
                            exit_time_utc = datetime.fromtimestamp(utc_timestamp, tz=timezone.utc).isoformat()

                            _update_trade_outcome(
                                ticket=str(ticket),
                                exit_price=exit_price_to_use,
                                exit_time=exit_time_utc,
                                pnl=_total_pnl,
                                entry_price=_arow["entry_price"],
                                sl=_arow["sl"],
                                tp=_arow["tp"],
                                volume=_arow["volume"],
                                entry_ts=_arow["ts"],
                                risk_amount=float(_arow["risk_amount"]) if _arow["risk_amount"] else None,
                                asset_type=_arow["asset_class"] or "",
                            )
                            result["pnl"] = round(_total_pnl, 4)
                            log.info(f"[CLOSE] MT5 outcome captured immediately: ticket={ticket} pnl={_total_pnl:.4f}")
            except Exception as _ce:
                log.debug(f"[CLOSE] immediate MT5 outcome capture failed for ticket={ticket}: {_ce}")

    else:
        return jsonify({"success": False, "error": f"Unknown exchange: {exch}"}), 400

    status = 200 if result.get("success") else 500

    return jsonify(result), status


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

    d = request.get_json(silent=True) or {}
    errors = []

    if "executionEnabled" in d:
        CONFIG["EXECUTION_ENABLED"] = bool(d["executionEnabled"])
        log.info(
            f"[EXEC] Execution {'ENABLED' if CONFIG['EXECUTION_ENABLED'] else 'DISABLED'}"
        )

    if "autoExecute" in d:
        CONFIG["AUTO_EXECUTE"] = bool(d["autoExecute"])

    if "autoExecuteMinScore" in d:
        try:
            _v = float(d["autoExecuteMinScore"])
            if _v < 0 or _v > 10:
                raise ValueError("out_of_range")
            CONFIG["AUTO_EXECUTE_MIN_SCORE"] = round(_v, 4)
        except (TypeError, ValueError):
            errors.append("autoExecuteMinScore must be a number in range 0-10")

    if "autoExecuteMinGrade" in d:
        _g = str(d.get("autoExecuteMinGrade") or "").strip().upper()
        if not _g:
            errors.append("autoExecuteMinGrade must be a non-empty string")
        else:
            CONFIG["AUTO_EXECUTE_MIN_GRADE"] = _g

    if "maxPortfolioHeat" in d:
        try:
            _v = float(d["maxPortfolioHeat"])
            if _v <= 0 or _v > 1:
                raise ValueError("out_of_range")
            CONFIG["MAX_PORTFOLIO_HEAT"] = round(_v, 6)
        except (TypeError, ValueError):
            errors.append("maxPortfolioHeat must be a number in range (0, 1]")

    if "maxOpenPositions" in d:
        try:
            _v = int(d["maxOpenPositions"])
            if _v < 1 or _v > 100:
                raise ValueError("out_of_range")
            CONFIG["MAX_OPEN_POSITIONS"] = _v
        except (TypeError, ValueError):
            errors.append("maxOpenPositions must be an integer in range 1-100")

    if "riskPct" in d:
        try:
            _v = float(d["riskPct"])
            if _v <= 0 or _v > 0.2:
                raise ValueError("out_of_range")
            CONFIG["RISK_PCT"] = round(_v, 6)
        except (TypeError, ValueError):
            errors.append("riskPct must be a number in range (0, 0.2]")

    payload = {
        "success": len(errors) == 0,
        "executionEnabled": CONFIG.get("EXECUTION_ENABLED", False),
        "autoExecute": CONFIG.get("AUTO_EXECUTE", False),
        "autoExecuteMinScore": CONFIG.get("AUTO_EXECUTE_MIN_SCORE", 8.0),
        "autoExecuteMinGrade": CONFIG.get("AUTO_EXECUTE_MIN_GRADE", "B"),
        "maxPortfolioHeat": CONFIG.get("MAX_PORTFOLIO_HEAT", 0.06),
        "maxOpenPositions": CONFIG.get("MAX_OPEN_POSITIONS", 5),
        "riskPct": CONFIG.get("RISK_PCT", 0.01),
    }
    if errors:
        payload["errors"] = errors
        return jsonify(payload), 400
    return jsonify(payload)


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
                    "error": "No pair selected. Engine B backtest requires a specific pair - select one from the dropdown.",
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

        _vm = str(data.get("validation_mode") or "standard").strip().lower()
        try:
            _pg = int(data.get("purge_gap", 200))
        except (TypeError, ValueError):
            _pg = 200
        try:
            _fd = int(data.get("folds", 3))
        except (TypeError, ValueError):
            _fd = 3
        result = backtest_pair_naked(
            pair,
            style=requested_style,
            validation_mode=_vm,
            purge_gap=_pg,
            folds=max(1, _fd),
        )

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


@app.route("/api/backtest-scalp", methods=["POST"])
def api_backtest_scalp():
    """Engine D (Scalp - Fabio VP+OrderFlow) backtest endpoint."""
    try:
        data = request.get_json(force=True, silent=True) or {}
        pair_symbol = data.get("pair") or data.get("symbol")

        if not pair_symbol:
            return jsonify({
                "success": False,
                "error": "No pair selected. Scalp backtest requires a specific pair.",
            }), 400

        pair = next(
            (p for p in ALL_PAIRS
             if p.get("display") == pair_symbol or p.get("symbol") == pair_symbol),
            None,
        )
        if not pair:
            return jsonify({
                "success": False,
                "error": f"Pair '{pair_symbol}' not found in pair list.",
            }), 404

        _vm = str(data.get("validation_mode") or "standard").strip().lower()
        result = backtest_pair_scalp(pair, validation_mode=_vm)

        if result is None:
            return jsonify({
                "success": False,
                "error": "Insufficient candle data to run scalp backtest for this pair.",
            }), 422

        safe_result = _json_safe(result) if callable(globals().get("_json_safe")) else result
        return jsonify(safe_result)

    except Exception as exc:
        log.exception("[SCALP-BT] Unhandled error in api_backtest_scalp")
        return jsonify({
            "success": False,
            "error": f"Scalp backtest failed: {str(exc)}",
        }), 500


@app.route("/api/backtest-consensus", methods=["POST"])
def api_backtest_consensus():
    """Engine C (consensus A+B) backtest endpoint."""
    try:
        data = request.get_json(force=True, silent=True) or {}
        pair_symbol = data.get("pair") or data.get("symbol")
        if not pair_symbol:
            return jsonify({"success": False,
                "error": "No pair selected. Engine C backtest requires a specific pair."}), 400
        pair = next(
            (p for p in ALL_PAIRS if p.get("display") == pair_symbol or p.get("symbol") == pair_symbol),
            None,
        )
        if not pair:
            return jsonify({"success": False, "error": f"Pair '{pair_symbol}' not found."}), 404
        _vm = str(data.get("validation_mode") or "standard").strip().lower()
        try:
            _pg = int(data.get("purge_gap", 200))
        except (TypeError, ValueError):
            _pg = 200
        try:
            _fd = int(data.get("folds", 3))
        except (TypeError, ValueError):
            _fd = 3
        result = backtest_pair_consensus(pair, validation_mode=_vm, purge_gap=_pg, folds=max(1, _fd))
        if result is None:
            return jsonify({"success": False,
                "error": "Insufficient candle data for Engine C backtest."}), 422
        return jsonify(_json_safe(result))
    except Exception as exc:
        log.exception("[ENGINE C BT] Unhandled error in api_backtest_consensus")
        return jsonify({"success": False, "error": f"Engine C backtest failed: {str(exc)}"}), 500


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


# N4: Kill-switch API - immediately blocks new scans/analyses


@app.route("/api/killswitch", methods=["POST"])
def api_killswitch():

    global _kill_switch

    d = request.get_json(silent=True) or {}

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

    d = request.get_json(silent=True) or {}

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


def _persist_bt_min_yaml(cfg_path: str, current: dict) -> None:
    import re as _re

    with open(cfg_path, "r", encoding="utf-8") as f:
        content = f.read()

    def _bt_block_replacer(m):
        block = m.group(0)
        for cls, val in current.items():
            block = _re.sub(
                rf"^([ \t]+{_re.escape(cls)}\s*:\s*)[\d.]+",
                lambda mm, v=val: f"{mm.group(1)}{v}",
                block,
                flags=_re.MULTILINE,
            )
        return block

    content = _re.sub(
        r"^BT_MIN\s*:\s*\n(?:[ \t]+\S[^\n]*\n)+",
        _bt_block_replacer,
        content,
        flags=_re.MULTILINE,
    )
    with open(cfg_path, "w", encoding="utf-8") as f:
        f.write(content)


def _persist_live_confluence_yaml(cfg_path: str, current: dict) -> None:
    import re as _re

    with open(cfg_path, "r", encoding="utf-8") as f:
        content = f.read()

    def _live_block_replacer(m):
        block = m.group(0)
        for cls, val in current.items():
            block = _re.sub(
                rf"^([ \t]+{_re.escape(cls)}\s*:\s*)[\d.]+",
                lambda mm, v=val: f"{mm.group(1)}{v}",
                block,
                flags=_re.MULTILINE,
            )
        return block

    content = _re.sub(
        r"^MIN_CONFLUENCE_CLASS\s*:\s*\n(?:[ \t]+\S[^\n]*\n)+",
        _live_block_replacer,
        content,
        flags=_re.MULTILINE,
    )
    with open(cfg_path, "w", encoding="utf-8") as f:
        f.write(content)


def _persist_backtest_bt_chain_flag_yaml(cfg_path: str, value: bool) -> None:
    """Write BACKTEST_USE_BT_MIN_THRESHOLDS: true|false in config.yaml."""
    import re as _re

    with open(cfg_path, "r", encoding="utf-8") as f:
        content = f.read()
    repl = "true" if value else "false"
    new_content, n = _re.subn(
        r"^(BACKTEST_USE_BT_MIN_THRESHOLDS\s*:\s*)(true|false)\s*$",
        lambda m: f"{m.group(1)}{repl}",
        content,
        count=1,
        flags=_re.MULTILINE | _re.IGNORECASE,
    )
    if n:
        with open(cfg_path, "w", encoding="utf-8") as f:
            f.write(new_content)
        return
    m = _re.search(
        r"^(RESEARCH_MODE\s*:\s*(?:true|false))\s*$",
        content,
        _re.MULTILINE | _re.IGNORECASE,
    )
    if m:
        insert = f"\nBACKTEST_USE_BT_MIN_THRESHOLDS: {repl}"
        content = content[: m.end()] + insert + content[m.end() :]
        with open(cfg_path, "w", encoding="utf-8") as f:
            f.write(content)
        return
    raise ValueError("config.yaml: RESEARCH_MODE / BACKTEST_USE_BT_MIN_THRESHOLDS not found")


def _apply_bt_min_updates(new_vals: dict) -> dict:
    current = dict(CONFIG.get("BT_MIN") or {})
    current.update({k: round(float(v), 4) for k, v in (new_vals or {}).items()})
    CONFIG["BT_MIN"] = current
    cfg_path = os.path.join(os.path.dirname(__file__), "config.yaml")
    _persist_bt_min_yaml(cfg_path, current)
    log.info(f"[BT_MIN] Updated via UI: {current}")
    return current


def _apply_live_confluence_updates(new_vals: dict) -> dict:
    current = dict(CONFIG.get("MIN_CONFLUENCE_CLASS") or {})
    current.update({k: round(float(v), 4) for k, v in (new_vals or {}).items()})
    CONFIG["MIN_CONFLUENCE_CLASS"] = current
    cfg_path = os.path.join(os.path.dirname(__file__), "config.yaml")
    _persist_live_confluence_yaml(cfg_path, current)
    log.info(f"[LIVE_THRESHOLD] Updated via UI: {current}")
    return current


def _apply_naked_style_score_updates(new_vals: dict) -> dict:
    ne = CONFIG.setdefault("NAKED_ENGINE", {})
    styles = ne.setdefault("style_profiles", {})
    for style, value in (new_vals or {}).items():
        cur = dict(styles.get(style) or {})
        cur["min_score"] = round(float(value), 4)
        styles[style] = {**dict(styles.get(style) or {}), **cur}

    full_for_yaml = {}
    for style in ("scalp", "intraday", "swing"):
        profile = dict(styles.get(style) or {})
        entry = {}
        if profile.get("min_score") is not None:
            entry["min_score"] = profile["min_score"]
        if profile.get("min_rr") is not None:
            entry["min_rr"] = profile["min_rr"]
        full_for_yaml[style] = entry

    cfg_path = os.path.join(os.path.dirname(__file__), "config.yaml")
    _persist_naked_style_profiles_yaml(cfg_path, full_for_yaml)
    log.info(f"[NAKED_ENGINE] min_score updated via advisory: {new_vals}")
    return {style: dict(styles.get(style) or {}) for style in ("scalp", "intraday", "swing")}


# MIN_CONFLUENCE_CLASS is advisory/legacy in dashboard; Engine A gates use scoring.py.
_ENGINE_A_ACTIVE_GATE_HELP = (
    "Runtime Engine A threshold: scoring.get_score_threshold — "
    "PAIR_PROFILES.min_confluence, ENGINE_A_PAIR_THRESHOLDS, "
    "ENGINE_A_SCORE_GROUP_THRESHOLDS (score_group, type, default), "
    "then 3-tier fallback. MIN_CONFLUENCE_CLASS in this API payload is not read by that resolver."
)


def _engine_a_threshold_max_for_class(asset_class: str) -> float:
    """Return the Engine A score-cap-aligned max threshold for an asset class."""
    return 3.0  # Engine A v2: unified 0-3.0 scale for all asset classes (was 2.0 for forex pre-v2)


_SCAN_SETTINGS_KEYS = (
    "D1_CANDLES",
    "H4_CANDLES",
    "H1_CANDLES",
    "SCAN_MAX_WORKERS",
    "SCAN_DEBUG_CANDLE_META",
)


def _scan_settings_snapshot() -> dict:
    snap = {
        "D1_CANDLES": int(CONFIG.get("D1_CANDLES", 1001) or 1001),
        "H4_CANDLES": int(CONFIG.get("H4_CANDLES", 1001) or 1001),
        "H1_CANDLES": int(CONFIG.get("H1_CANDLES", 1001) or 1001),
        "SCAN_MAX_WORKERS": int(CONFIG.get("SCAN_MAX_WORKERS", 3) or 3),
        "SCAN_DEBUG_CANDLE_META": bool(CONFIG.get("SCAN_DEBUG_CANDLE_META", False)),
    }
    try:
        from timed_exit_monitor import _get_timed_cfg as _timed_cfg_merge

        snap["TIMED_EXIT"] = _json_safe(_timed_cfg_merge(lambda: CONFIG))
    except Exception:
        snap["TIMED_EXIT"] = {}
    _se = CONFIG.get("SCALP_ENGINE") or {}
    snap["SCALP_LIVE_MILESTONES"] = _json_safe(
        {
            "LIVE_MILESTONE_MANAGEMENT": bool(_se.get("LIVE_MILESTONE_MANAGEMENT", False)),
            "SL_AFTER_PARTIAL": str(_se.get("SL_AFTER_PARTIAL", "tp_partial")),
        }
    )
    return snap


def _persist_scan_settings_yaml(cfg_path: str, current: dict) -> None:
    import re as _re

    with open(cfg_path, "r", encoding="utf-8") as f:
        content = f.read()

    for key in _SCAN_SETTINGS_KEYS:
        if key not in current:
            continue
        value = current[key]
        if isinstance(value, bool):
            rendered = "true" if value else "false"
        else:
            rendered = str(int(value))
        content, count = _re.subn(
            rf"^({_re.escape(key)}\s*:\s*)([^#\n]+?)(\s*(?:#.*)?)$",
            lambda m, v=rendered: f"{m.group(1)}{v}{m.group(3)}",
            content,
            count=1,
            flags=_re.MULTILINE,
        )
        if count == 0:
            raise ValueError(f"config.yaml: {key} not found")

    with open(cfg_path, "w", encoding="utf-8") as f:
        f.write(content)


def _apply_scan_settings_updates(new_vals: dict) -> dict:
    current = _scan_settings_snapshot()

    for key in ("D1_CANDLES", "H4_CANDLES", "H1_CANDLES"):
        if key in new_vals:
            try:
                value = int(new_vals[key])
            except (TypeError, ValueError):
                raise ValueError(f"{key} must be an integer") from None
            if value < 10 or value > 5000:
                raise ValueError(f"{key} must be between 10 and 5000")
            current[key] = value

    if "SCAN_MAX_WORKERS" in new_vals:
        try:
            workers = int(new_vals["SCAN_MAX_WORKERS"])
        except (TypeError, ValueError):
            raise ValueError("SCAN_MAX_WORKERS must be an integer") from None
        if workers < 1 or workers > 64:
            raise ValueError("SCAN_MAX_WORKERS must be between 1 and 64")
        current["SCAN_MAX_WORKERS"] = workers

    if "SCAN_DEBUG_CANDLE_META" in new_vals:
        raw_debug = new_vals["SCAN_DEBUG_CANDLE_META"]
        if isinstance(raw_debug, str):
            current["SCAN_DEBUG_CANDLE_META"] = raw_debug.strip().lower() in (
                "1",
                "true",
                "yes",
                "on",
            )
        else:
            current["SCAN_DEBUG_CANDLE_META"] = bool(raw_debug)

    for key, value in current.items():
        CONFIG[key] = value

    cfg_path = os.path.join(os.path.dirname(__file__), "config.yaml")
    _persist_scan_settings_yaml(cfg_path, current)
    log.info("[SCAN_SETTINGS] Updated via UI: %s", current)
    return current


@app.route("/api/scan-settings", methods=["GET"])
def api_scan_settings():
    """Read-only snapshot of verified scan runtime settings exposed in the dashboard."""
    return jsonify(
        {
            "settings": _scan_settings_snapshot(),
            "unverified_scan_keys": ["FOREX_H4_RESAMPLE_OFFSET_HOURS"],
        }
    )


@app.route("/api/bt-min", methods=["GET", "POST"])
def api_bt_min():
    """GET: BT_MIN + legacy MIN_CONFLUENCE_CLASS metadata (not the Engine A runtime gate).
    POST: update BT_MIN class floors and/or backtest_use_bt_min_thresholds (backtest only).
    Body: {"crypto": 0.55, ...} and optional {"backtest_use_bt_min_thresholds": true}
    """
    asset_classes = ("crypto", "forex", "commodity", "stock", "index")
    cfg_path = os.path.join(os.path.dirname(__file__), "config.yaml")

    if request.method == "GET":
        use_bt = bool(
            CONFIG.get("BACKTEST_USE_BT_MIN_THRESHOLDS", False)
            or CONFIG.get("RESEARCH_MODE", False)
        )
        return jsonify(
            {
                "bt_min": dict(CONFIG.get("BT_MIN") or {}),
                "live_class": dict(CONFIG.get("MIN_CONFLUENCE_CLASS") or {}),
                "min_confluence_class_role": "advisory_legacy_display",
                "engine_a_active_gate": _ENGINE_A_ACTIVE_GATE_HELP,
                "backtest_use_bt_min_thresholds": bool(
                    CONFIG.get("BACKTEST_USE_BT_MIN_THRESHOLDS", False)
                ),
                "research_mode": bool(CONFIG.get("RESEARCH_MODE", False)),
                "backtest_uses_bt_min_chain": use_bt,
            }
        )

    data = request.get_json(silent=True) or {}

    if "backtest_use_bt_min_thresholds" in data:
        try:
            CONFIG["BACKTEST_USE_BT_MIN_THRESHOLDS"] = bool(
                data["backtest_use_bt_min_thresholds"]
            )
            _persist_backtest_bt_chain_flag_yaml(
                cfg_path, CONFIG["BACKTEST_USE_BT_MIN_THRESHOLDS"]
            )
            log.info(
                "[BT_MIN] BACKTEST_USE_BT_MIN_THRESHOLDS=%s (persisted)",
                CONFIG["BACKTEST_USE_BT_MIN_THRESHOLDS"],
            )
        except Exception as e:
            log.error(f"Failed to persist BACKTEST_USE_BT_MIN_THRESHOLDS: {e}")
            return jsonify(
                {
                    "saved": False,
                    "error": str(e),
                    "backtest_use_bt_min_thresholds": bool(
                        CONFIG.get("BACKTEST_USE_BT_MIN_THRESHOLDS", False)
                    ),
                }
            ), 500

    new_vals = {}
    for cls in asset_classes:
        if cls not in data:
            continue
        try:
            val = float(data[cls])
        except (TypeError, ValueError):
            return jsonify({"error": f"Invalid value for {cls}"}), 400
        _max_allowed = _engine_a_threshold_max_for_class(cls)
        if val < 0 or val > _max_allowed:
            return jsonify(
                {"error": f"Value for {cls} out of range (0-{_max_allowed:g})"}
            ), 400
        new_vals[cls] = round(val, 4)

    if not new_vals:
        if "backtest_use_bt_min_thresholds" in data:
            use_bt = bool(
                CONFIG.get("BACKTEST_USE_BT_MIN_THRESHOLDS", False)
                or CONFIG.get("RESEARCH_MODE", False)
            )
            return jsonify(
                {
                    "saved": True,
                    "bt_min": dict(CONFIG.get("BT_MIN") or {}),
                    "backtest_use_bt_min_thresholds": bool(
                        CONFIG.get("BACKTEST_USE_BT_MIN_THRESHOLDS", False)
                    ),
                    "research_mode": bool(CONFIG.get("RESEARCH_MODE", False)),
                    "backtest_uses_bt_min_chain": use_bt,
                }
            )
        return jsonify({"error": "No valid keys provided"}), 400

    try:
        current = _apply_bt_min_updates(new_vals)
    except Exception as e:
        log.error(f"Failed to persist BT_MIN to config.yaml: {e}")
        return jsonify({"saved": False, "error": str(e), "bt_min": dict(CONFIG.get('BT_MIN') or {})}), 500

    use_bt = bool(
        CONFIG.get("BACKTEST_USE_BT_MIN_THRESHOLDS", False)
        or CONFIG.get("RESEARCH_MODE", False)
    )
    return jsonify(
        {
            "saved": True,
            "bt_min": current,
            "backtest_use_bt_min_thresholds": bool(
                CONFIG.get("BACKTEST_USE_BT_MIN_THRESHOLDS", False)
            ),
            "research_mode": bool(CONFIG.get("RESEARCH_MODE", False)),
            "backtest_uses_bt_min_chain": use_bt,
        }
    )


def _persist_naked_style_profiles_yaml(cfg_path: str, full_profiles: dict) -> None:
    """Rewrite NAKED_ENGINE.style_profiles min_score/min_rr lines in config.yaml (block-preserving)."""
    import re as _re

    with open(cfg_path, "r", encoding="utf-8") as f:
        content = f.read()
    m = _re.search(
        r"(^  style_profiles:\n)(.*?)(^  zone_multipliers:\n)",
        content,
        _re.MULTILINE | _re.DOTALL,
    )
    if not m:
        raise ValueError("config.yaml: NAKED_ENGINE style_profiles block not found")
    block = m.group(2)
    for style in ("scalp", "intraday", "swing"):
        sp = full_profiles.get(style) or {}
        if "min_score" in sp:
            ms = float(sp["min_score"])
            block = _re.sub(
                rf"(^    {_re.escape(style)}:\n      min_score: )[\d.]+",
                lambda mm, v=ms: f"{mm.group(1)}{v}",
                block,
                count=1,
                flags=_re.MULTILINE,
            )
        if "min_rr" in sp:
            mr = float(sp["min_rr"])
            block = _re.sub(
                rf"(^    {_re.escape(style)}:\n      min_score: [\d.]+\n      min_rr: )[\d.]+",
                lambda mm, v=mr: f"{mm.group(1)}{v}",
                block,
                count=1,
                flags=_re.MULTILINE,
            )
    new_content = content[: m.start()] + m.group(1) + block + m.group(3) + content[m.end() :]
    with open(cfg_path, "w", encoding="utf-8") as f:
        f.write(new_content)


def _persist_score_group_overrides_yaml(cfg_path: str, group_overrides: dict) -> dict:
    """Update min_score values in NAKED_ENGINE.score_group_overrides in config.yaml.

    Returns a small status dict so callers can surface partial-write warnings.
    """
    import re as _re

    with open(cfg_path, "r", encoding="utf-8") as f:
        content = f.read()

    status = {"saved": False, "updated": [], "warnings": []}

    # Find the score_group_overrides block
    m = _re.search(
        r"(^  score_group_overrides:\n)(.*?)(^# Neutralized by default)",
        content,
        _re.MULTILINE | _re.DOTALL,
    )
    if not m:
        warning = "config.yaml: score_group_overrides block not found, skipping update"
        log.warning(warning)
        status["warnings"].append(warning)
        return status

    block = m.group(2)
    for group_name, group_styles in group_overrides.items():
        if not isinstance(group_styles, dict):
            continue
        for style in ("scalp", "intraday", "swing"):
            style_cfg = group_styles.get(style)
            if not isinstance(style_cfg, dict) or "min_score" not in style_cfg:
                continue
            ms = float(style_cfg["min_score"])
            # Pattern: "      scalp: {min_score: 3.0" or "      scalp: {min_score: 3.0, min_rr: 1.4"
            # We need to find lines within the specific group block
            group_pattern = rf"(    {_re.escape(group_name)}:\n(?:.*?\n)*?      {_re.escape(style)}: \{{min_score: )[\d.]+"
            block, replaced = _re.subn(
                group_pattern,
                lambda mm, v=ms: f"{mm.group(1)}{v}",
                block,
                count=1,
                flags=_re.DOTALL,
            )
            key = f"{group_name}.{style}"
            if replaced:
                status["updated"].append(key)
            else:
                warning = f"config.yaml: score_group_overrides entry not found for {key}, skipping update"
                log.warning(warning)
                status["warnings"].append(warning)

    new_content = content[: m.start()] + m.group(1) + block + m.group(3) + content[m.end() :]
    with open(cfg_path, "w", encoding="utf-8") as f:
        f.write(new_content)
    status["saved"] = True
    return status


def _sync_score_group_override_min_scores(
    group_overrides: dict,
    previous_base_min_scores: dict,
    new_base_min_scores: dict,
) -> tuple[list[str], list[str]]:
    """Propagate base style min_score only for entries that were inheriting the old base."""
    updated = []
    skipped_custom = []

    for group_name, group_styles in list(group_overrides.items()):
        if not isinstance(group_styles, dict):
            continue
        for style in ("scalp", "intraday", "swing"):
            style_cfg = group_styles.get(style)
            if not isinstance(style_cfg, dict) or "min_score" not in style_cfg:
                continue

            old_base = previous_base_min_scores.get(style)
            new_base = new_base_min_scores.get(style)
            key = f"{group_name}.{style}"
            if old_base is None or new_base is None:
                skipped_custom.append(key)
                continue

            try:
                current_group_min = float(style_cfg["min_score"])
                old_base_f = float(old_base)
                new_base_f = float(new_base)
            except (TypeError, ValueError):
                skipped_custom.append(key)
                continue

            if abs(current_group_min - old_base_f) > 1e-9:
                skipped_custom.append(key)
                continue

            style_cfg["min_score"] = new_base_f
            updated.append(key)

    return updated, skipped_custom


@app.route("/api/naked-style-thresholds", methods=["GET", "POST"])
def api_naked_style_thresholds():
    """GET: NAKED_ENGINE.style_profiles (Engine B live + backtest).
    POST: update min_score / min_rr per style; merge into CONFIG and config.yaml.
    Body: {"scalp": {"min_score": 1.5, "min_rr": 1.4}, "intraday": {...}, "swing": {...}}
    """
    STYLES = ("scalp", "intraday", "swing")
    if request.method == "GET":
        ne = CONFIG.get("NAKED_ENGINE") or {}
        profiles = dict(ne.get("style_profiles") or {})
        out = {}
        for s in STYLES:
            p = dict(profiles.get(s) or {})
            out[s] = {
                "min_score": p.get("min_score"),
                "min_rr": p.get("min_rr"),
            }
        group_overrides = dict(ne.get("score_group_overrides") or {})
        return jsonify({
            "style_profiles": out,
            "score_group_overrides": group_overrides,
            "note": "Used by Engine B naked scan and Engine B backtest",
        })

    data = request.get_json(silent=True) or {}
    ne = CONFIG.setdefault("NAKED_ENGINE", {})
    styles = ne.setdefault("style_profiles", {})
    previous_base_min_scores = {
        style: (dict(styles.get(style) or {})).get("min_score")
        for style in STYLES
    }
    any_change = False
    for style in STYLES:
        payload = data.get(style)
        if not isinstance(payload, dict):
            continue
        cur = dict(styles.get(style) or {})
        changed = False
        if "min_score" in payload:
            try:
                ms = float(payload["min_score"])
            except (TypeError, ValueError):
                return jsonify({"error": f"Invalid min_score for {style}"}), 400
            if ms < 0 or ms > 20:
                return jsonify({"error": f"min_score for {style} out of range (0-20)"}), 400
            cur["min_score"] = round(ms, 4)
            changed = True
        if "min_rr" in payload:
            try:
                mr = float(payload["min_rr"])
            except (TypeError, ValueError):
                return jsonify({"error": f"Invalid min_rr for {style}"}), 400
            if mr < 0.1 or mr > 10:
                return jsonify({"error": f"min_rr for {style} out of range (0.1-10)"}), 400
            cur["min_rr"] = round(mr, 4)
            changed = True
        if changed:
            styles[style] = {**dict(styles.get(style) or {}), **cur}
            any_change = True

    if not any_change:
        return jsonify({"error": "No valid style keys (scalp/intraday/swing) with min_score or min_rr"}), 400

    full_for_yaml = {}
    for s in STYLES:
        p = dict(styles.get(s) or {})
        entry = {}
        if p.get("min_score") is not None:
            entry["min_score"] = p["min_score"]
        if p.get("min_rr") is not None:
            entry["min_rr"] = p["min_rr"]
        full_for_yaml[s] = entry

    # Also update score_group_overrides min_score to match the new base thresholds
    group_overrides = ne.setdefault("score_group_overrides", {})
    new_base_min_scores = {
        style: full_for_yaml.get(style, {}).get("min_score")
        for style in STYLES
    }
    groups_updated, groups_skipped_custom = _sync_score_group_override_min_scores(
        group_overrides,
        previous_base_min_scores,
        new_base_min_scores,
    )
    persist_warnings = []

    try:
        import os as _os

        cfg_path = _os.path.join(_os.path.dirname(__file__), "config.yaml")
        _persist_naked_style_profiles_yaml(cfg_path, full_for_yaml)
        if groups_updated:
            persist_status = _persist_score_group_overrides_yaml(cfg_path, group_overrides)
            persist_warnings.extend(persist_status.get("warnings") or [])
            log.info(f"[NAKED_ENGINE] score_group_overrides also updated: {groups_updated}")
        if groups_skipped_custom:
            log.info(
                f"[NAKED_ENGINE] preserved custom score_group_overrides min_score values: {groups_skipped_custom}"
            )
        log.info(f"[NAKED_ENGINE] style_profiles updated via UI: {full_for_yaml}")
    except Exception as e:
        log.error(f"Failed to persist NAKED_ENGINE.style_profiles: {e}")
        return jsonify({"saved": False, "error": str(e), "style_profiles": full_for_yaml}), 500

    out = {}
    for s in STYLES:
        p = dict(styles.get(s) or {})
        out[s] = {"min_score": p.get("min_score"), "min_rr": p.get("min_rr")}
    response = {
        "saved": True,
        "style_profiles": out,
        "score_group_overrides_updated": groups_updated,
        "score_group_overrides_preserved_custom": groups_skipped_custom,
    }
    if persist_warnings:
        response["warnings"] = persist_warnings
    return jsonify(response)


def _persist_scalp_group_rr_yaml(cfg_path: str, group: str, style: str, min_rr: float) -> None:
    """Rewrite a single min_rr value inside NAKED_ENGINE.score_group_overrides in config.yaml."""
    import re as _re

    with open(cfg_path, "r", encoding="utf-8") as f:
        content = f.read()

    # Pattern: find the group block and update or insert min_rr for the given style line
    # e.g.   forex_majors:\n      scalp: {min_rr: 1.2}
    style_pat = _re.compile(
        rf"(  score_group_overrides:.*?{_re.escape(group)}:\n(?:.*\n)*?      {_re.escape(style)}: \{{[^}}]*)min_rr: [\d.]+",
        _re.DOTALL,
    )
    if style_pat.search(content):
        content = style_pat.sub(
            lambda m: m.group(0)[: m.start(0) - m.start(0)] + m.group(0).replace(
                _re.search(r"min_rr: [\d.]+", m.group(0)).group(0),
                f"min_rr: {min_rr}",
            ),
            content,
            count=1,
        )
    else:
        # Insert the group+style block if missing
        insert_pat = _re.compile(r"(  score_group_overrides:\n)")
        if insert_pat.search(content):
            content = insert_pat.sub(
                rf"\1    {group}:\n      {style}: {{min_rr: {min_rr}}}\n",
                content,
                count=1,
            )

    with open(cfg_path, "w", encoding="utf-8") as f:
        f.write(content)


@app.route("/api/scalp-group-rr", methods=["GET", "POST"])
def api_scalp_group_rr():
    """GET: return current min_rr values for forex score groups across scalp/intraday/swing.
    POST: update min_rr for a specific group+style combination.
    Body: {"group": "forex_majors", "scalp": 1.2, "intraday": 1.3, "swing": 1.8}
    """
    FOREX_GROUPS = ("forex_majors", "forex_crosses", "forex_exotics", "forex_other")
    STYLES = ("scalp", "intraday", "swing")
    ne = CONFIG.get("NAKED_ENGINE") or {}
    group_overrides = ne.get("score_group_overrides") or {}
    global_min_rr = float((CONFIG.get("SCALP_ENGINE") or {}).get("MIN_RR", 2.0))

    if request.method == "GET":
        out = {"global_min_rr": global_min_rr, "groups": {}}
        for grp in FOREX_GROUPS:
            grp_cfg = group_overrides.get(grp) or {}
            out["groups"][grp] = {
                s: (grp_cfg.get(s) or {}).get("min_rr") for s in STYLES
            }
        return jsonify(out)

    data = request.get_json(silent=True) or {}
    group = data.get("group")
    if group not in FOREX_GROUPS:
        return jsonify({"error": f"group must be one of {FOREX_GROUPS}"}), 400

    cfg_path = os.path.join(os.path.dirname(__file__), "config.yaml")
    ne_mut = CONFIG.setdefault("NAKED_ENGINE", {})
    sgo = ne_mut.setdefault("score_group_overrides", {})
    grp_entry = sgo.setdefault(group, {})

    saved = {}
    for style in STYLES:
        if style not in data:
            continue
        try:
            val = float(data[style])
        except (TypeError, ValueError):
            return jsonify({"error": f"Invalid value for {style}"}), 400
        if val < 0.5 or val > 5.0:
            return jsonify({"error": f"{style} min_rr out of range (0.5-5.0)"}), 400
        style_entry = grp_entry.setdefault(style, {})
        style_entry["min_rr"] = round(val, 2)
        saved[style] = round(val, 2)

    if not saved:
        return jsonify({"error": "No valid style keys provided"}), 400

    try:
        import re as _re
        with open(cfg_path, "r", encoding="utf-8") as f:
            content = f.read()
        for style, val in saved.items():
            # Try to replace existing min_rr for this group+style
            pat = _re.compile(
                rf"(  score_group_overrides:.*?{_re.escape(group)}:\n(?:(?!    \w).*\n)*?      {_re.escape(style)}: \{{[^}}]*)min_rr: [\d.]+",
                _re.DOTALL,
            )
            if pat.search(content):
                content = pat.sub(
                    lambda m, v=val: m.group(1) + f"min_rr: {v}",
                    content, count=1,
                )
            else:
                # Group+style line exists but no min_rr yet - append it
                line_pat = _re.compile(
                    rf"(      {_re.escape(style)}: \{{)([^}}]*)(\}})",
                )
                def _inject(m, v=val):
                    inner = m.group(2).rstrip()
                    sep = ", " if inner else ""
                    return m.group(1) + inner + sep + f"min_rr: {v}" + m.group(3)
                content = line_pat.sub(_inject, content, count=1)
        with open(cfg_path, "w", encoding="utf-8") as f:
            f.write(content)
        log.info("[SCALP_GROUP_RR] %s updated: %s", group, saved)
    except Exception as e:
        log.error("[SCALP_GROUP_RR] persist failed: %s", e)
        return jsonify({"saved": False, "error": str(e)}), 500

    return jsonify({"saved": True, "group": group, "updated": saved})


@app.route("/api/live-confluence-thresholds", methods=["GET", "POST"])
def api_live_confluence_thresholds():
    """GET/POST MIN_CONFLUENCE_CLASS — advisory/legacy; Engine A uses scoring.get_score_threshold."""
    asset_classes = ("crypto", "forex", "commodity", "stock", "index")
    if request.method == "GET":
        return jsonify(
            {
                "live_class": dict(CONFIG.get("MIN_CONFLUENCE_CLASS") or {}),
                "min_confluence_class_role": "advisory_legacy_display",
                "engine_a_active_gate": _ENGINE_A_ACTIVE_GATE_HELP,
            }
        )

    data = request.get_json(silent=True) or {}
    new_vals = {}
    for cls in asset_classes:
        if cls not in data:
            continue
        try:
            val = float(data[cls])
        except (TypeError, ValueError):
            return jsonify({"error": f"Invalid value for {cls}"}), 400
        _max_allowed = _engine_a_threshold_max_for_class(cls)
        if val < 0 or val > _max_allowed:
            return jsonify(
                {"error": f"Value for {cls} out of range (0-{_max_allowed:g})"}
            ), 400
        new_vals[cls] = round(val, 4)

    if not new_vals:
        return jsonify({"error": "No valid keys provided"}), 400

    try:
        current = _apply_live_confluence_updates(new_vals)
    except Exception as e:
        log.error(f"Failed to persist MIN_CONFLUENCE_CLASS to config.yaml: {e}")
        return jsonify(
            {
                "saved": False,
                "error": str(e),
                "live_class": dict(CONFIG.get("MIN_CONFLUENCE_CLASS") or {}),
            }
        ), 500

    return jsonify({"saved": True, "live_class": current})


@app.route("/api/advisory-thresholds")
def api_advisory_thresholds():
    """Return pending/approved/rejected threshold recommendations for dashboard review."""
    try:
        return jsonify(get_recommendation_snapshot(_AUDIT_DB))
    except Exception as e:
        log.error(f"api_advisory_thresholds error: {e}")
        return jsonify({"error": str(e)}), 500


@app.route("/api/advisory-thresholds/<rec_id>/approve", methods=["POST"])
def api_advisory_threshold_approve(rec_id):
    data = request.get_json(silent=True) or {}
    note = (str(data.get("note") or "").strip() or None)
    recommendation = find_pending_recommendation(rec_id, _AUDIT_DB)
    if not recommendation:
        return jsonify({"error": "Recommendation not found or no longer pending"}), 404

    try:
        scope_type = recommendation.get("scope_type")
        scope_key = recommendation.get("scope_key")
        proposed_value = float(recommendation.get("proposed_value"))
        if scope_type == "engine_a_bt_class":
            applied = {"bt_min": _apply_bt_min_updates({scope_key: proposed_value})}
        elif scope_type == "engine_a_live_class":
            applied = {"live_class": _apply_live_confluence_updates({scope_key: proposed_value})}
        elif scope_type == "engine_b_style":
            applied = {"style_profiles": _apply_naked_style_score_updates({scope_key: proposed_value})}
        else:
            return jsonify({"error": f"Unsupported recommendation type: {scope_type}"}), 400

        record_action(rec_id, "approved", recommendation, note=note, db_path=_AUDIT_DB)
        return jsonify({"saved": True, "recommendation": recommendation, "applied": applied})
    except Exception as e:
        log.error(f"api_advisory_threshold_approve error: {e}")
        return jsonify({"saved": False, "error": str(e)}), 500


@app.route("/api/advisory-thresholds/<rec_id>/reject", methods=["POST"])
def api_advisory_threshold_reject(rec_id):
    data = request.get_json(silent=True) or {}
    note = (str(data.get("note") or "").strip() or None)
    recommendation = find_pending_recommendation(rec_id, _AUDIT_DB)
    if not recommendation:
        return jsonify({"error": "Recommendation not found or no longer pending"}), 404

    try:
        record_action(rec_id, "rejected", recommendation, note=note, db_path=_AUDIT_DB)
        return jsonify({"saved": True, "recommendation": recommendation})
    except Exception as e:
        log.error(f"api_advisory_threshold_reject error: {e}")
        return jsonify({"saved": False, "error": str(e)}), 500


@app.route("/api/test-mode", methods=["POST"])
def api_test_mode():
    """Toggle test mode: drops score thresholds, enables force-execute on all signals. For demo accounts only."""
    global _test_mode
    d = request.get_json(silent=True) or {}
    action = d.get("action", "toggle")
    if action == "on":
        _test_mode = True

    elif action == "off":
        _test_mode = False

    else:
        _test_mode = not _test_mode

    log.warning(
        f"[TEST MODE] {'ACTIVATED' if _test_mode else 'DEACTIVATED'} - score thresholds {'lowered' if _test_mode else 'restored'}"
    )

    return jsonify({"testMode": _test_mode})


@app.route("/api/test-mode")
def api_test_mode_status():
    """Check current test mode status."""

    return jsonify({"testMode": _test_mode})


@app.route("/api/feature-toggles", methods=["GET", "POST"])
def api_feature_toggles():
    """GET: return current state. POST: toggle one or more features."""
    _features = {
        "intermarket": {
            "config_keys": [
                "INTERMARKET_CONFIRMATION.enabled",
                "INTERMARKET_CONFIRMATION.engine_a_enabled",
                "INTERMARKET_CONFIRMATION.engine_c_enabled",
            ],
            "label": "Intermarket Confirmation",
        },
        "news_sentiment": {
            "config_keys": ["NEWS_SENTIMENT_CONFLUENCE_ENABLED"],
            "label": "News Score Blend",
        },
        "sentiment_gate": {
            "config_keys": ["SENTIMENT_GATE_ENABLED"],
            "label": "Sentiment Gate",
        },
        "event_risk": {
            "config_keys": ["EVENT_RISK_ENABLED"],
            "label": "Macro Event Gate",
        },
    }

    def _get_state():
        ic = CONFIG.get("INTERMARKET_CONFIRMATION") or {}
        return {
            "intermarket": bool(ic.get("enabled", False)),
            "news_sentiment": bool(CONFIG.get("NEWS_SENTIMENT_CONFLUENCE_ENABLED", False)),
            "sentiment_gate": bool(CONFIG.get("SENTIMENT_GATE_ENABLED", True)),
            "event_risk": bool(CONFIG.get("EVENT_RISK_ENABLED", True)),
        }

    if request.method == "GET":
        return jsonify(_get_state())

    data = request.get_json(silent=True) or {}
    feature = str(data.get("feature") or "").strip().lower()
    action = str(data.get("action") or "toggle").strip().lower()

    if feature not in _features:
        return jsonify({"error": f"Unknown feature: {feature}. Valid: {list(_features.keys())}"}), 400

    if feature == "intermarket":
        ic = CONFIG.get("INTERMARKET_CONFIRMATION")
        if not isinstance(ic, dict):
            ic = {}
            CONFIG["INTERMARKET_CONFIRMATION"] = ic
        current = bool(ic.get("enabled", False))
        new_val = not current if action == "toggle" else (action == "enable")
        ic["enabled"] = new_val
        ic["engine_a_enabled"] = new_val
        ic["engine_c_enabled"] = new_val
        log.warning(f"[FEATURE] Intermarket Confirmation {'ENABLED' if new_val else 'DISABLED'}")

    elif feature == "news_sentiment":
        current = bool(CONFIG.get("NEWS_SENTIMENT_CONFLUENCE_ENABLED", False))
        new_val = not current if action == "toggle" else (action == "enable")
        CONFIG["NEWS_SENTIMENT_CONFLUENCE_ENABLED"] = new_val
        log.warning(f"[FEATURE] News Score Blend {'ENABLED' if new_val else 'DISABLED'}")

    elif feature == "sentiment_gate":
        current = bool(CONFIG.get("SENTIMENT_GATE_ENABLED", True))
        new_val = not current if action == "toggle" else (action == "enable")
        CONFIG["SENTIMENT_GATE_ENABLED"] = new_val
        log.warning(f"[FEATURE] Sentiment Gate {'ENABLED' if new_val else 'DISABLED'}")

    elif feature == "event_risk":
        current = bool(CONFIG.get("EVENT_RISK_ENABLED", True))
        new_val = not current if action == "toggle" else (action == "enable")
        CONFIG["EVENT_RISK_ENABLED"] = new_val
        log.warning(f"[FEATURE] Macro Event Gate {'ENABLED' if new_val else 'DISABLED'}")

    return jsonify(_get_state())


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
        "structural_tp": raw_signal.get("structural_tp"),
        "structural_rr": raw_signal.get("structural_rr"),
        "structure_target_close": raw_signal.get("structure_target_close"),
        "rr": float(raw_signal.get("rr1", 0.0) or 0.0),
        "ai_grade": ai_grade,
        "ai_score": raw_signal.get("ai_score", 0),
        "risk_level": risk_level,
        "zone_desc": zone_desc,
        "trigger_desc": trigger_desc,
        "zone_type": raw_signal.get("zone_type"),
        "zone_level": raw_signal.get("zone_level"),
        "market_state": raw_signal.get("market_state"),
        "vp_poc": raw_signal.get("vp_poc"),
        "vp_vah": raw_signal.get("vp_vah"),
        "vp_val": raw_signal.get("vp_val"),
        "vp_volume_source": raw_signal.get("vp_volume_source"),
        "vp_bucket_count": raw_signal.get("vp_bucket_count"),
        "vp_fidelity": raw_signal.get("vp_fidelity"),
        "vp_is_proxy": raw_signal.get("vp_is_proxy"),
        "vp_uses_real_trade_buckets": raw_signal.get("vp_uses_real_trade_buckets"),
        "absorption_count": raw_signal.get("absorption_count"),
        "absorption_source": raw_signal.get("absorption_source"),
        "absorption_fidelity": raw_signal.get("absorption_fidelity"),
        "absorption_is_proxy": raw_signal.get("absorption_is_proxy"),
        "cvd_direction": raw_signal.get("cvd_direction"),
        "cvd_source": raw_signal.get("cvd_source"),
        "cvd_bucket_count": raw_signal.get("cvd_bucket_count"),
        "cvd_fidelity": raw_signal.get("cvd_fidelity"),
        "cvd_is_proxy": raw_signal.get("cvd_is_proxy"),
        "cvd_uses_real_trade_buckets": raw_signal.get("cvd_uses_real_trade_buckets"),
        "aggression_source": raw_signal.get("aggression_source"),
        "aggression_source_raw": raw_signal.get("aggression_source_raw"),
        "aggression_source_is_proxy": raw_signal.get("aggression_source_is_proxy"),
        "aggression_confirmed": raw_signal.get("aggression_confirmed"),
        "aggression_uses_real_order_flow": raw_signal.get("aggression_uses_real_order_flow"),
        "data_fidelity": raw_signal.get("data_fidelity"),
        "strict_fabio_pass": raw_signal.get("strict_fabio_pass"),
        "strict_fabio_reason": raw_signal.get("strict_fabio_reason"),
        "strict_fabio_missing_pillars": raw_signal.get("strict_fabio_missing_pillars"),
        "strict_fabio_pillars": raw_signal.get("strict_fabio_pillars"),
        "current_vs_strict_status": raw_signal.get("current_vs_strict_status"),
        "aggression_components": raw_signal.get("aggression_components"),
        "profile_anchor_mode": raw_signal.get("profile_anchor_mode"),
        "profile_anchor_bars": raw_signal.get("profile_anchor_bars"),
        "profile_anchor_start": raw_signal.get("profile_anchor_start"),
        "profile_anchor_end": raw_signal.get("profile_anchor_end"),
        "profile_anchor_shadow": raw_signal.get("profile_anchor_shadow"),
        "aaa_complete": raw_signal.get("aaa_complete"),
        "size_multiplier": raw_signal.get("size_multiplier"),
        "sl_method": raw_signal.get("sl_method"),
        "rr1": raw_signal.get("rr1"),
        "tp_partial": raw_signal.get("tp_partial"),
        "ai_reasons": raw_signal.get("ai_reasons", []),
        "gate_result": raw_signal.get("gate_result", "PASS"),
        "executable": bool(raw_signal.get("executable", True)),
        "candidate_status": raw_signal.get("candidate_status"),
        "fail_reasons": raw_signal.get("fail_reasons", []),
        "soft_warnings": raw_signal.get("soft_warnings", []),
        "fee_guard": raw_signal.get("fee_guard", {}),
        "rr_ok": raw_signal.get("rr_ok"),
        "advisory": raw_signal.get("advisory"),
        "advisory_summary": raw_signal.get("advisory_summary"),
        "premarket_delta_cluster_type": raw_signal.get("premarket_delta_cluster_type"),
        "premarket_delta_proxy_levels": raw_signal.get("premarket_delta_proxy_levels"),
        "engine": raw_signal.get("engine", "SCALP"),
        "session": raw_signal.get("session"),
        "spread_pips": raw_signal.get("spread_pips"),
        "htf_bias": raw_signal.get("htf_bias"),
        "htf_bias_tf": raw_signal.get("htf_bias_tf"),
        "timestamp": raw_signal.get("timestamp"),
    }


@app.route("/api/scalp-pairs", methods=["GET"])
def api_scalp_pairs():
    """List Engine D scan universe (no candle work). Respects SCALP_SCAN_UNIVERSE / disabled pairs."""
    try:
        from scalp_engine import displays_for_scalp_scan

        pairs = displays_for_scalp_scan(ACTIVE_PAIRS, disabled_displays=_disabled_pairs)
        return jsonify({"pairs": pairs, "count": len(pairs)})
    except Exception as e:
        log.error(f"api_scalp_pairs error: {e}")
        return jsonify({"error": str(e)}), 500


@app.route("/api/scalp-scan", methods=["POST"])
def api_scalp_scan():
    """Run Engine D scalp scan and return frontend-friendly JSON."""
    try:
        from scalp_engine import displays_for_scalp_scan, run_scalp_scan

        payload = request.get_json(silent=True) or {}
        pairs = payload.get("pairs")
        if not isinstance(pairs, list) or not pairs:
            pairs = displays_for_scalp_scan(ACTIVE_PAIRS, disabled_displays=_disabled_pairs)

        diagnostic = bool(payload.get("diagnostic", False))
        result = run_scalp_scan(pairs)
        signals = [_scalp_ui_signal(s) for s in (result.get("signals", []) or [])]
        skipped = result.get("skipped", []) or []

        # In diagnostic mode, enrich skipped rows so UI can show why each failed
        if diagnostic:
            for row in skipped:
                if isinstance(row, dict):
                    row["_diagnostic"] = True
                    row["gate_result"] = row.get("reason", "BLOCKED").split(":")[0]

        # Populate live dashboard scalp cache so snapshot can read Engine D state
        _ts_now = time.time()
        with _live_dashboard_scalp_cache_lock:
            for _sig in (result.get("signals") or []):
                _key = str(_sig.get("display") or _sig.get("pair") or "").upper()
                if _key:
                    _live_dashboard_scalp_cache[_key] = dict(_sig)
                    _live_dashboard_scalp_cache[_key]["_ts"] = _ts_now
                    _live_dashboard_scalp_cache[_key]["_skipped"] = False
            for _row in (result.get("skipped") or []):
                _key = str(_row.get("display") or _row.get("pair") or "").upper()
                if _key:
                    _live_dashboard_scalp_cache[_key] = {
                        "gateResult": "BLOCKED",
                        "reason": _row.get("reason"),
                        "_ts": _ts_now,
                        "_skipped": True,
                    }

        return jsonify(
            {
                "signals": signals,
                "skipped": skipped,
                "scanned": result.get("scanned", len(pairs)),
                "pairs": pairs,
                "pair_count": len(pairs),
                "session": result.get("session"),
                "sessions_active": result.get("sessions_active", []),
                "reason": result.get("reason"),
                "diagnostic_summary": result.get("diagnostic_summary"),
                "diagnostic": diagnostic,
                "pass_count": sum(
                    1
                    for _sig in signals
                    if _sig.get("executable") and _sig.get("gate_result", "PASS") == "PASS"
                ),
                "candidate_count": len(signals),
                "skip_count": len(skipped),
            }
        )
    except Exception as e:
        log.error(f"api_scalp_scan error: {e}")
        return jsonify({"error": str(e)}), 500


@app.route("/api/scalp-execute", methods=["POST"])
def api_scalp_execute():
    """Execute a single Engine D scalp setup: MT5 (forex/commodities/stocks) or Bybit (crypto)."""
    try:
        from scalp_engine import run_scalp_scan
        from risk_engine import risk_check

        payload = request.get_json(silent=True) or {}
        symbol = (payload.get("symbol") or "").strip()
        client_signal = payload.get("signal") if isinstance(payload.get("signal"), dict) else None
        if not symbol:
            return jsonify({"error": "Missing symbol"}), 400

        scan = run_scalp_scan([symbol])
        raw_signals = scan.get("signals", []) or []
        if not raw_signals:
            reason = scan.get("reason") or "No valid scalp setup found"
            skipped = scan.get("skipped", []) or []
            if skipped:
                reason = skipped[0].get("reason", reason)
            return jsonify({
                "success": False,
                "error": reason,
                "skipped": skipped,
                "fresh_scan": {
                    "signals": [],
                    "skipped": skipped,
                    "session": scan.get("session"),
                    "reason": scan.get("reason"),
                },
            }), 200

        if client_signal:
            requested_symbol = (
                client_signal.get("symbol")
                or client_signal.get("pair")
                or client_signal.get("display")
                or ""
            ).strip()
            requested_direction = (client_signal.get("direction") or "").upper()
            if requested_symbol and requested_symbol != symbol:
                return jsonify({"success": False, "error": "Signal symbol mismatch"}), 200
            if requested_direction not in ("LONG", "SHORT"):
                return jsonify({"success": False, "error": "Signal direction missing"}), 200

            signal = next(
                (
                    s
                    for s in raw_signals
                    if (s.get("pair") or "").strip() == symbol
                    and (s.get("direction") or "").upper() == requested_direction
                ),
                None,
            )
            if signal is None:
                fresh_dirs = sorted(
                    {str((s.get("direction") or "")).upper() for s in raw_signals if s.get("direction")}
                )
                if fresh_dirs:
                    return jsonify(
                        {
                            "success": False,
                            "error": (
                                f"SIGNAL_FLIPPED: {symbol} is now {'/'.join(fresh_dirs)} "
                                f"(was {requested_direction})"
                            ),
                            "newDirection": fresh_dirs[0] if len(fresh_dirs) == 1 else fresh_dirs,
                        }
                    ), 200
                return jsonify(
                    {
                        "success": False,
                        "error": f"SETUP_INVALIDATED: {symbol} {requested_direction} is no longer a valid scalp setup",
                    }
                ), 200
        else:
            signal = raw_signals[0]

        if signal.get("gate_result", "PASS") != "PASS" or signal.get("executable") is False:
            return jsonify(
                {
                    "success": False,
                    "error": signal.get("candidate_status") or "ENGINE_D_NOT_EXECUTABLE",
                    "skipped": [
                        {
                            "pair": signal.get("pair") or symbol,
                            "reason": signal.get("candidate_status") or "ENGINE_D_NOT_EXECUTABLE",
                            "gate_result": signal.get("gate_result"),
                            "ai_grade": signal.get("ai_grade"),
                            "ai_score": signal.get("ai_score"),
                            "fail_reasons": signal.get("fail_reasons", []),
                        }
                    ],
                    "fresh_scan": scan,
                }
            ), 200

        is_crypto = (signal.get("type") == "crypto")

        if is_crypto:
            from bybit_executor import (
                bybit_get_account,
                bybit_get_positions,
                bybit_get_symbol_info,
            )

            account = bybit_get_account()
            if not account or account.get("error"):
                return jsonify({"error": "Bybit not connected"}), 503

            positions_resp = bybit_get_positions()
            if isinstance(positions_resp, dict) and positions_resp.get("error"):
                return jsonify({"error": "Positions unavailable - cannot verify exposure"}), 503
            positions = (
                positions_resp.get("positions", [])
                if isinstance(positions_resp, dict)
                else (positions_resp or [])
            )

            symbol_info = bybit_get_symbol_info(symbol)
            if not symbol_info or symbol_info.get("error"):
                return jsonify(
                    {"success": False, "error": f"Symbol '{symbol}' not available on Bybit"}
                ), 200
            _exec_venue = "bybit"
        else:
            from mt5_executor import (
                mt5_get_account,
                mt5_get_positions,
                mt5_get_symbol_info,
            )

            account = mt5_get_account()
            if not account or account.get("error"):
                return jsonify({"error": "MT5 not connected"}), 503

            positions_resp = mt5_get_positions()
            if isinstance(positions_resp, dict) and positions_resp.get("error"):
                return jsonify({"error": "Positions unavailable - cannot verify exposure"}), 503
            positions = (
                positions_resp.get("positions", [])
                if isinstance(positions_resp, dict)
                else (positions_resp or [])
            )

            symbol_info = mt5_get_symbol_info(symbol)
            if not symbol_info or symbol_info.get("error"):
                return jsonify({"error": f"Symbol '{symbol}' not available on MT5"}), 200
            _exec_venue = "mt5"

        from execution_lifecycle import run_managed_execution

        approval = risk_check(
            signal=signal,
            account_balance=account["balance"],
            account_equity=account["equity"],
            open_positions=positions,
            symbol_info=symbol_info,
            kill_switch=_kill_switch,
            sizing_override=float(signal.get("size_multiplier", 1.0) or 1.0),
        )
        if not approval.approved:
            return jsonify(
                {
                    "success": False,
                    "error": f"Risk engine rejected: {approval.reason}",
                    "approval": approval.to_dict(),
                }
            ), 200

        result = run_managed_execution(_exec_venue, signal, approval)
        if not result.get("success"):
            return jsonify(
                {"success": False, "error": result.get("error", "Execution failed")}
            ), 200

        try:
            _factors_json = json.dumps({
                "vp_poc":           signal.get("vp_poc"),
                "vp_vah":           signal.get("vp_vah"),
                "vp_val":           signal.get("vp_val"),
                "ai_score":         signal.get("ai_score"),
                "absorption_count": signal.get("absorption_count"),
                "cvd_direction":    signal.get("cvd_direction"),
                "aaa_complete":     signal.get("aaa_complete"),
                "zone_type":        signal.get("zone_type"),
                "htf_bias":         signal.get("htf_bias"),
            })
            with sqlite3.connect(_AUDIT_DB, timeout=15.0) as con:
                con.execute(
                    "INSERT INTO audit_log("
                    "ts,pair,score,max_score,engine,direction,grade,risk,style,"
                    "entry_price,sl,tp,tp_partial,tp2,volume,ticket,risk_amount,risk_pct,"
                    "asset_class,regime,factors_json"
                    ") VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        datetime.now(timezone.utc).isoformat(),
                        signal.get("pair") or symbol,
                        signal.get("confluenceScore"),
                        1.0,
                        "scalp",
                        signal.get("direction"),
                        signal.get("ai_grade"),
                        f"${approval.risk_amount}",
                        "scalp",
                        result.get("entryPrice") or result.get("entry_price"),
                        signal.get("sl"),
                        signal.get("tp1"),
                        signal.get("tp_partial"),
                        signal.get("tp2"),
                        result.get("volume"),
                        str(result.get("ticket", "")),
                        approval.risk_amount,
                        approval.risk_pct,
                        signal.get("type"),
                        signal.get("market_state"),
                        _factors_json,
                    ),
                )
                con.commit()
        except Exception as audit_exc:
            log.warning(f"[SCALP EXEC] audit_log insert failed: {audit_exc}")

        return jsonify(
            {
                "success": True,
                "ticket": result.get("ticket"),
                "volume": result.get("volume"),
                "entry_price": result.get("entry_price") or result.get("entryPrice"),
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

    d = request.get_json(silent=True) or {}

    action = d.get("action", "toggle")

    if action == "on":
        _auto_trader.enable()

    elif action == "off":
        _auto_trader.disable()

    else:
        _auto_trader.toggle()

    current_state = bool(_auto_trader.get_status().get("enabled", False))
    CONFIG["AUTO_TRADE_ENABLED"] = current_state
    _update_yaml_toggle(current_state)

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
            get_ai_api_key(CONFIG),
            get_ai_model(CONFIG, "AI_MODEL", "grok-4.3"),
        )

        return jsonify(result)

    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/sentiment/inject", methods=["POST"])
def api_inject_sentiment():
    """Inject external sentiment score (from LunarCrush, Crypto.com, etc.)"""

    data = request.get_json(silent=True) or {}

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
    """Last 30 auto-trade attempts - both successful and failed - for dashboard diagnosis."""

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


def _xai_chat_completions_retry(client, *, max_attempts: int = 4, base_delay_sec: float = 2.5, **kwargs):
    """OpenAI-compatible ``chat.completions.create`` with backoff on rate limits / overload."""
    last_exc: BaseException | None = None
    for attempt in range(max_attempts):
        try:
            return client.chat.completions.create(**kwargs)
        except Exception as e:
            last_exc = e
            code = getattr(e, "status_code", None)
            msg = str(e).lower()
            retryable = (
                code == 429
                or code == 500
                or code == 503
                or code == 529
                or "rate" in msg
                or "server error" in msg
                or "overloaded" in msg
                or "529" in msg
            )
            if not retryable or attempt >= max_attempts - 1:
                raise
            delay = base_delay_sec * (2**attempt)
            log.warning(
                "[XAI] retry %s/%s after %.1fs (%s)",
                attempt + 1,
                max_attempts,
                delay,
                e,
            )
            time.sleep(delay)
    assert last_exc is not None
    raise last_exc


def _chart_blocks_to_openai_user_content(blocks: list) -> list:
    """Anthropic-style content blocks -> OpenAI multimodal user content."""
    out: list = []
    for block in blocks or []:
        btype = block.get("type")
        if btype == "text":
            out.append({"type": "text", "text": block.get("text", "")})
        elif btype == "image":
            src = block.get("source") or {}
            b64 = src.get("data", "")
            mime = str(src.get("media_type", "image/png"))
            if b64:
                out.append(
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": f"data:{mime};base64,{b64}",
                            "detail": "high",
                        },
                    }
                )
    return out


@app.route("/api/chart-analysis", methods=["POST"])
def api_chart_analysis():
    """Send chart screenshot to configured vision model for professional TA reading."""
    data = request.get_json(silent=True) or {}
    if not data:
        return jsonify({"error": "Missing request body"}), 400

    symbol = str(data.get("symbol") or "").strip()
    tf = str(data.get("tf") or "H4").strip().upper()
    chart_source = str(data.get("chart_source") or "").strip().lower()

    def _safe_float(value):
        try:
            if value is None or value == "":
                return None
            return float(value)
        except (TypeError, ValueError):
            return None

    if not data.get("image") and bool(data.get("server_render")) and data.get("candles"):
        try:
            from chart_renderer import render_chart_image

            data["image"] = render_chart_image(
                candles=data.get("candles") or [],
                entry=_safe_float(data.get("entry")),
                sl=_safe_float(data.get("sl")),
                tp=_safe_float(data.get("tp")),
                title=f"{symbol or 'UNKNOWN'} {tf}",
                engine_b=data.get("engineB") if isinstance(data.get("engineB"), dict) else None,
                bars_window=int(data.get("bars_window") or 80),
                dpi=int(data.get("dpi") or 200),
                show_pattern_labels=bool(data.get("show_pattern_labels", True)),
                pattern_lookback=int(data.get("pattern_lookback") or 10),
            )
            if data.get("candles_d1"):
                data["image_d1"] = render_chart_image(
                    candles=data.get("candles_d1") or [],
                    title=f"{symbol or 'UNKNOWN'} D1",
                    engine_b=data.get("engineB") if isinstance(data.get("engineB"), dict) else None,
                    bars_window=int(data.get("bars_window") or 80),
                    dpi=int(data.get("dpi") or 200),
                    show_pattern_labels=bool(data.get("show_pattern_labels", True)),
                    pattern_lookback=int(data.get("pattern_lookback") or 10),
                )
            if data.get("candles_h1"):
                data["image_h1"] = render_chart_image(
                    candles=data.get("candles_h1") or [],
                    title=f"{symbol or 'UNKNOWN'} H1",
                    engine_b=data.get("engineB") if isinstance(data.get("engineB"), dict) else None,
                    bars_window=int(data.get("bars_window") or 80),
                    dpi=int(data.get("dpi") or 200),
                    show_pattern_labels=bool(data.get("show_pattern_labels", True)),
                    pattern_lookback=int(data.get("pattern_lookback") or 10),
                )
            chart_source = chart_source or "server_renderer"
        except Exception as _render_err:
            return jsonify({"error": f"Server render failed: {_render_err}"}), 400

    if not data.get("image"):
        return jsonify({"error": "Missing image data"}), 400

    # === Chart timestamp / freshness validation ===
    # Warn (non-blocking) when client chart images lack timestamp grounding.
    _chart_ts_warnings: list[str] = []
    _chart_generated_at = data.get("chart_generated_at") or data.get("chartGeneratedAt")
    _latest_candle_ts = data.get("latest_candle_ts") or data.get("latestCandleTs")
    _is_server_render = bool(data.get("server_render"))

    if not _is_server_render:
        # Client-side (browser) charts may be stale screenshots.
        if not _chart_generated_at:
            _chart_ts_warnings.append("chart_generated_at missing - client chart image age unknown")
        if not _latest_candle_ts:
            _chart_ts_warnings.append("latest_candle_ts missing - cannot verify chart data freshness")
    else:
        # Server-render always generates from the candles passed in the request.
        # Derive latest_candle_ts from the last candle in the primary TF candle array if not provided.
        if not _latest_candle_ts and data.get("candles"):
            try:
                _last_candle = data["candles"][-1]
                _latest_candle_ts = (
                    _last_candle.get("t") or _last_candle.get("time") or _last_candle.get("ts")
                )
            except Exception:
                pass

    if _chart_ts_warnings:
        log.debug("[CHART-VISION] Timestamp warnings for %s: %s", symbol, _chart_ts_warnings)

    pair = next(
        (p for p in ALL_PAIRS if p.get("symbol") == symbol or p.get("display") == symbol),
        None,
    )

    context_parts = []

    # Engine A signal context - prefer POST body, then fallback cache.
    sig = data.get("signal")
    if not sig:
        for s in _last_scan_results.get("signals", []):
            if s.get("symbol") == symbol or s.get("display") == symbol:
                sig = s
                break

    if sig:
        _regime_raw = sig.get("regime", {})
        _regime_label = (
            _regime_raw.get("label", "UNKNOWN")
            if isinstance(_regime_raw, dict)
            else str(_regime_raw or "UNKNOWN")
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
        # Full Engine A factor breakdown + diagnostics for chart cross-check (not only headline score).
        _fs = sig.get("factorScores") or sig.get("factor_scores") or {}
        _fw = sig.get("factorWeights") or sig.get("factor_weights") or {}
        if _fs:
            _flines = [f"  {fn}: {fv} [w={_fw.get(fn, '?')}]" for fn, fv in _fs.items()]
            context_parts.append("ENGINE A FACTORS:\n" + "\n".join(_flines))
        _fd = sig.get("factorDiagnostics") or {}
        if _fd:
            _tc = _fd.get("trendCoherence") or {}
            _coh = _tc.get("coherence_ratio") if isinstance(_tc, dict) else None
            context_parts.append(
                "ENGINE A DIAGNOSTICS: "
                f"directionalScore={_fd.get('directionalScore')}, "
                f"nondirectionalScore={_fd.get('nondirectionalScore')}, "
                f"directionalConfidenceMultiplier={_fd.get('directionalConfidenceMultiplier')}, "
                f"trendCoherence_ratio={_coh}"
            )
        _cd = sig.get("confidenceDetail") or {}
        if _cd and _cd.get("confidence") is not None:
            try:
                _ccf = f"{float(_cd.get('confidence')):.4f}"
            except (TypeError, ValueError):
                _ccf = str(_cd.get("confidence"))
            context_parts.append(
                f"CONFIDENCE ENGINE (Engine A): confidence={_ccf}, "
                f"components={_cd.get('components')}"
            )
        _eb_vis = sig.get("engine_b") or {}
        if _eb_vis:
            _ebc = _eb_vis.get("confidence")
            if isinstance(_ebc, dict):
                context_parts.append(
                    "ENGINE B CONFIDENCE: "
                    f"structure_ok={_ebc.get('structure_ok')}, "
                    f"zone_ok={_ebc.get('zone_ok')}, "
                    f"trigger_ok={_ebc.get('trigger_ok')}, "
                    f"location_ok={_ebc.get('location_ok')}, "
                    f"room_ok={_ebc.get('room_ok')}, "
                    f"rr_ok={_ebc.get('rr_ok')}, "
                    f"passed={_ebc.get('passed')}, "
                    f"score={_ebc.get('score')}/{_ebc.get('max_possible')}, "
                    f"structural_verdict={_eb_vis.get('structural_verdict')}"
                )
            elif _eb_vis.get("score_pct") is not None or _eb_vis.get("structural_verdict"):
                context_parts.append(
                    "ENGINE B SUMMARY: "
                    f"structural_verdict={_eb_vis.get('structural_verdict')}, "
                    f"score_pct={_eb_vis.get('score_pct')}, "
                    f"is_actionable={_eb_vis.get('is_actionable')}"
                )

    sid = symbol.replace("/", "_").replace("=", "_").replace("^", "_").replace(".", "_")
    eb = data.get("engineB") or _engine_b_cache_get(sid) or _engine_b_cache_get(symbol)
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
        active_fvgs = [f for f in (eb.get("active_fvgs") or []) if not f.get("mitigated")]
        if active_fvgs:
            fvg_str = ", ".join(
                f"{f['type']} {float(f['bottom']):.5f}-{float(f['top']):.5f}"
                for f in active_fvgs
            )
            context_parts.append(f"ACTIVE FVGs (unmitigated imbalances): {fvg_str}")
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

    def _chart_level_sets(sig_payload: dict) -> dict:
        def _to_num(value):
            try:
                if value is None or value == "":
                    return None
                return float(value)
            except (TypeError, ValueError):
                return None

        out = {}
        sig_payload = sig_payload or {}
        style_levels = sig_payload.get("style_levels")
        if isinstance(style_levels, dict):
            for _style in ("scalp", "intraday", "swing"):
                row = style_levels.get(_style) or {}
                slv = _to_num(row.get("sl"))
                tp1 = _to_num(row.get("tp1") or row.get("tp"))
                tp2 = _to_num(row.get("tp2") or row.get("tp1") or row.get("tp"))
                if slv is not None and tp1 is not None:
                    out[_style] = {
                        "sl": slv,
                        "tp1": tp1,
                        "tp2": tp2 if tp2 is not None else tp1,
                    }

        base_sl = _to_num(sig_payload.get("sl"))
        base_tp1 = _to_num(sig_payload.get("tp1") or sig_payload.get("tp"))
        base_tp2 = _to_num(sig_payload.get("tp2") or sig_payload.get("tp1") or sig_payload.get("tp"))
        if base_sl is not None and base_tp1 is not None:
            base = {
                "sl": base_sl,
                "tp1": base_tp1,
                "tp2": base_tp2 if base_tp2 is not None else base_tp1,
            }
            for _style in ("scalp", "intraday", "swing"):
                out.setdefault(_style, dict(base))
        return out

    asset_type = pair.get("type", "unknown") if pair else "unknown"
    current_levels = _chart_level_sets(sig or {})
    if current_levels:
        _level_lines = []
        for _style in ("scalp", "intraday", "swing"):
            row = current_levels.get(_style) or {}
            if row.get("sl") is None or row.get("tp1") is None:
                continue
            _level_lines.append(
                f"{_style.upper()} CURRENT LEVELS: SL={row['sl']:.6f} TP={row['tp1']:.6f}"
            )
        if _level_lines:
            context_parts.append("STYLE LEVELS:\n" + "\n".join(_level_lines))

    # Raw candle + indicator table for vision model cross-check
    _prim_candles = data.get("candles") or []
    if _prim_candles:
        _vc = min(15, len(_prim_candles))
        _vslice = _prim_candles[-_vc:]
        _vcls = [c.get("c", c.get("close")) for c in _prim_candles]
        _vhi = [c.get("h", c.get("high")) for c in _prim_candles]
        _vlo = [c.get("l", c.get("low")) for c in _prim_candles]
        try:
            from indicators import calc_ema, calc_rsi, calc_macd, calc_adx, calc_atr
            _ve21 = calc_ema(_vcls, 21)
            _ve50 = calc_ema(_vcls, 50)
            _vrsi = calc_rsi(_vcls, 14)
            _vmac = calc_macd(_vcls, 12, 26, 9)
            _vadx = calc_adx(_vhi, _vlo, _vcls, 14)
            _vatr = calc_atr(_vhi, _vlo, _vcls, 14)
            _vlines = [f"RAW DATA - last {_vc} {tf} bars (cross-check chart visually):"]
            _vlines.append("Time | Close | EMA21 | EMA50 | RSI | MACD | ADX | ATR")
            for vidx, vc in enumerate(_vslice):
                _vg = vidx + (len(_prim_candles) - _vc)
                _fv = lambda x: f"{x:.5f}" if isinstance(x, (int, float)) else "-"
                _fv1 = lambda x: f"{x:.1f}" if isinstance(x, (int, float)) else "-"
                _vlines.append(
                    f"{vc.get('t','')[:16]} | {_fv(vc.get('c',vc.get('close')))} | "
                    f"{_fv(_ve21[_vg])} | {_fv(_ve50[_vg])} | {_fv1(_vrsi[_vg])} | "
                    f"{_fv(_vmac['macd'][_vg])} | {_fv1(_vadx['adx'][_vg])} | {_fv(_vatr[_vg])}"
                )
            context_parts.append("\n".join(_vlines))
        except Exception as _vraw_err:
            log.debug("[CHART-VISION] raw data table failed: %s", _vraw_err)

    direction_str = sig.get("direction", "UNKNOWN") if sig else "UNKNOWN"

    algo_context = "\n".join(context_parts) if context_parts else "No algorithmic data available."

    try:
        from vision_candle_features import extract_candle_features
        _candle_ctx_lines = []
        _cf_prim = extract_candle_features(data.get("candles") or [], direction_str)
        if _cf_prim: _candle_ctx_lines.append(f"CANDLE FEATURES - PRIMARY RIGHT EDGE:\n- {_cf_prim['candle_summary']}")
        _cf_h1 = extract_candle_features(data.get("candles_h1") or [], direction_str)
        if _cf_h1: _candle_ctx_lines.append(f"CANDLE FEATURES - H1 RIGHT EDGE:\n- {_cf_h1['candle_summary']}")
        _cf_h4 = extract_candle_features(data.get("candles_h4") or [], direction_str)
        if _cf_h4: _candle_ctx_lines.append(f"CANDLE FEATURES - H4 RIGHT EDGE:\n- {_cf_h4['candle_summary']}")
        _cf_d1 = extract_candle_features(data.get("candles_d1") or [], direction_str)
        if _cf_d1: _candle_ctx_lines.append(f"CANDLE FEATURES - D1 RIGHT EDGE:\n- {_cf_d1['candle_summary']}")
        if _candle_ctx_lines:
            algo_context += "\n\n" + "\n\n".join(_candle_ctx_lines)
    except Exception as _cf_err:
        log.debug("[CHART-VISION] Failed to extract candle features context: %s", _cf_err)

    system_prompt = build_system_prompt()

    def _extract_vision_structured(
        analysis_text: str,
        direction_hint: str,
        entry_price: float | None = None,
        current_level_sets: dict | None = None,
    ) -> dict:
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
            "level_suggestions": {},
            "right_edge_status": "",
            "final_verdict": "",
            "ema_reclaim_flag": None,
            "countertrend_volume_flag": None,
            "reviewSource": "ai_vision",
            "selectedStyleGrade": "",
            "executionRisk": "Medium",
            "structuralRisk": "Medium",
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
            vals = set(out["style_ratings"].values())
            supportive = {"STRONG", "MODERATE"}
            negative = {"CONTRADICTS", "AVOID"}

            if (vals & supportive) and (vals & negative):
                out["rating"] = "MODERATE"
                out["confirms_direction"] = False
            else:
                for label in ("CONTRADICTS", "AVOID", "STRONG", "MODERATE", "WEAK"):
                    if label in vals:
                        out["rating"] = label
                        break

        if not out["rating"]:
            for label in ("CONTRADICTS", "AVOID", "WEAK", "MODERATE", "STRONG"):
                if label in up:
                    out["rating"] = label
                    break

        if "CONFLICTED" in up or "CONTRADICT" in up:
            if out["rating"] in ("STRONG", "MODERATE"):
                out["rating"] = "MODERATE"
            else:
                out["rating"] = out["rating"] or "CONTRADICTS"
            out["confirms_direction"] = False
        elif direction_hint and direction_hint.upper() in ("LONG", "SHORT"):
            _oppose = _re.search(r"(OPPOSES|AGAINST)\s+THE\s+ALGORITHMIC", up)
            if _oppose:
                out["confirms_direction"] = False
                out["rating"] = "CONTRADICTS"

        if _re.search(r"\bSL\b.*(TOO\s+TIGHT|TIGHT)", up):
            out["sl_flag"] = "too_tight"
        if _re.search(r"\bTP\b.*(TOO\s+FAR|UNREALISTIC)", up):
            out["tp_flag"] = "too_far"
        _right_edge = _re.search(
            r"RIGHT\s+EDGE\s*:\s*(CONFIRMS|REVIEW|POTENTIAL\s+REVERSAL)",
            up,
        )
        if _right_edge:
            out["right_edge_status"] = _right_edge.group(1).replace(" ", "_")
        _verdict = _re.search(r"FINAL\s+VERDICT\s*:\s*(HOLD|CLOSE|ADJUST)", up)
        if _verdict:
            out["final_verdict"] = _verdict.group(1)
        if _re.search(
            r"(RECLAIM(?:ED|ING)?|RETAKEN|RECOVERED).{0,40}(EMA\s*21|EMA21|EMA\s*50|EMA50|EMA\s*200|EMA200)",
            up,
        ):
            out["ema_reclaim_flag"] = True
        elif _re.search(
            r"(HAS\s+NOT|DID\s+NOT|NO).{0,24}(RECLAIM(?:ED|ING)?|RETAKEN|RECOVERED).{0,40}(EMA\s*21|EMA21|EMA\s*50|EMA50|EMA\s*200|EMA200)",
            up,
        ):
            out["ema_reclaim_flag"] = False
        if _re.search(
            r"((COUNTER[-\s]?TREND).{0,60}(RISING\s+VOLUME|VOLUME\s+RISING))|((RISING\s+VOLUME|VOLUME\s+RISING).{0,60}(COUNTER[-\s]?TREND))",
            up,
        ):
            out["countertrend_volume_flag"] = True
        elif _re.search(
            r"(NO\s+COUNTER[-\s]?TREND\s+VOLUME|RIGHT\s+EDGE\s+STILL\s+CONFIRMS)",
            up,
        ):
            out["countertrend_volume_flag"] = False

        def _parse_level_token(token: str, fallback: float | None) -> float | None:
            raw = str(token or "").strip().upper().rstrip(",.;")
            if raw in ("", "KEEP", "CURRENT", "SAME", "UNCHANGED", "-", "N/A", "NA"):
                return fallback
            m = _re.search(r"-?\d+(?:\.\d+)?", raw)
            if not m:
                return None
            try:
                return float(m.group(0))
            except (TypeError, ValueError):
                return None

        def _levels_valid(entry: float | None, direction: str, slv: float | None, tp1v: float | None) -> bool:
            if entry is None or entry <= 0 or slv is None or tp1v is None:
                return False
            if direction == "LONG":
                return slv < entry < tp1v
            if direction == "SHORT":
                return slv > entry > tp1v
            return False

        _current_sets = current_level_sets or {}
        for style in ("SCALP", "INTRADAY", "SWING"):
            m = _re.search(
                rf"{style}\s+LEVELS?\s*:\s*SL\s*=\s*([^\s,;]+)\s+TP(?:1)?\s*=\s*([^\s,;]+)",
                up,
            )
            if not m:
                continue
            _current = _current_sets.get(style.lower(), {})
            sl_val = _parse_level_token(m.group(1), _current.get("sl"))
            tp1_val = _parse_level_token(m.group(2), _current.get("tp1"))
            if not _levels_valid(entry_price, direction_hint.upper(), sl_val, tp1_val):
                continue
            risk = abs(float(entry_price) - float(sl_val)) if entry_price else 0.0
            reward = abs(float(tp1_val) - float(entry_price)) if entry_price else 0.0
            out["level_suggestions"][style.lower()] = {
                "sl": round(float(sl_val), 6),
                "tp1": round(float(tp1_val), 6),
                "tp2": round(float(tp1_val), 6),
                "entry_anchor": round(float(entry_price), 6) if entry_price else None,
                "rr": round((reward / risk), 2) if risk > 0 else None,
            }

        for style_key in ("scalp", "intraday", "swing"):
            if out["level_suggestions"].get(style_key):
                continue
            _current = _current_sets.get(style_key, {})
            sl_cur = _current.get("sl")
            tp1_cur = _current.get("tp1")
            if not _levels_valid(entry_price, direction_hint.upper(), sl_cur, tp1_cur):
                continue
            risk = abs(float(entry_price) - float(sl_cur)) if entry_price else 0.0
            reward = abs(float(tp1_cur) - float(entry_price)) if entry_price else 0.0
            out["level_suggestions"][style_key] = {
                "sl": round(float(sl_cur), 6),
                "tp1": round(float(tp1_cur), 6),
                "tp2": round(float(_current.get("tp2") if _current.get("tp2") is not None else tp1_cur), 6),
                "entry_anchor": round(float(entry_price), 6) if entry_price else None,
                "rr": round((reward / risk), 2) if risk > 0 else None,
            }

        if out["final_verdict"] == "HOLD" and out["right_edge_status"] in (
            "REVIEW",
            "POTENTIAL_REVERSAL",
        ):
            out["final_verdict"] = "ADJUST"

        if not out["rating"]:
            # Fail closed: missing footer ratings must not default to confirm-tier MODERATE
            out["rating"] = "AVOID"

        _res_style = str(data.get("resolvedStyle") or (sig or {}).get("tradeStyle") or "swing").lower()
        if _res_style in out["style_ratings"]:
            out["selectedStyleGrade"] = out["style_ratings"][_res_style]
        elif out["style_ratings"]:
            _hierarchy = ["STRONG", "MODERATE", "WEAK", "AVOID", "CONTRADICTS"]
            _best = "WEAK"
            for _h in _hierarchy:
                if _h in out["style_ratings"].values():
                    _best = _h
                    break
            out["selectedStyleGrade"] = _best
        else:
            out["selectedStyleGrade"] = out["rating"]

        return out


    user_prompt = build_single_prompt(
        symbol=symbol,
        tf=tf,
        direction_str=direction_str,
        algo_context=algo_context,
        asset_type=asset_type,
    )

    try:
        _ai_key = get_ai_api_key(CONFIG)
        if not _ai_key:
            return jsonify({"error": "AI API key not set"}), 500

        client = create_ai_client(CONFIG, api_key=_ai_key)
        log.info(
            f"[AI CHART] provider={get_ai_provider_label(CONFIG)} "
            f"base_url={get_ai_base_url(CONFIG)} model={get_ai_model(CONFIG, 'VISION_MODEL', 'grok-4.3')}"
        )

        img_h4 = str(data["image"])
        if img_h4.startswith("data:"):
            img_h4 = img_h4.split(",", 1)[1]

        img_d1 = data.get("image_d1")
        if isinstance(img_d1, str) and img_d1.startswith("data:"):
            img_d1 = img_d1.split(",", 1)[1]

        img_h1 = data.get("image_h1")
        if isinstance(img_h1, str) and img_h1.startswith("data:"):
            img_h1 = img_h1.split(",", 1)[1]

        dual_mode = bool(img_d1)
        triple_mode = bool(img_d1 and img_h1)

        if triple_mode:
            triple_prompt = build_triple_prompt(
                symbol=symbol,
                direction_str=direction_str,
                algo_context=algo_context,
                asset_type=asset_type,
            )
            content = [
                {"type": "text", "text": "IMAGE 1 - D1 DAILY BIAS CHART:"},
                {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": img_d1}},
                {"type": "text", "text": "IMAGE 2 - H4 INTERMEDIATE CHART:"},
                {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": img_h4}},
                {"type": "text", "text": "IMAGE 3 - H1 ENTRY TIMING CHART:"},
                {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": img_h1}},
                {"type": "text", "text": triple_prompt},
            ]
            log.info(f"[AI CHART] Triple-screen D1+H4+H1 analysis for {symbol}")
        elif dual_mode:
            dual_prompt = build_dual_prompt(
                symbol=symbol,
                direction_str=direction_str,
                algo_context=algo_context,
                asset_type=asset_type,
            )
            content = [
                {"type": "text", "text": "IMAGE 1 - D1 DAILY BIAS CHART:"},
                {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": img_d1}},
                {"type": "text", "text": "IMAGE 2 - H4 ENTRY CHART:"},
                {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": img_h4}},
                {"type": "text", "text": dual_prompt},
            ]
            log.info(f"[AI CHART] Dual-TF D1+H4 analysis for {symbol}")
        else:
            content = [
                {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": img_h4}},
                {"type": "text", "text": user_prompt},
            ]
            log.info(f"[AI CHART] Single-TF {tf} analysis for {symbol}")

        import base64 as _b64_vision

        _stored_vision: dict = {}
        try:
            from ai_review_logger import store_vision_image

            _raw_b64 = str(img_h4).strip()
            if _raw_b64.startswith("data:"):
                _raw_b64 = _raw_b64.split(",", 1)[1]
            _pad_v = (-len(_raw_b64)) % 4
            _png_bytes = _b64_vision.b64decode(_raw_b64 + ("=" * _pad_v))
            _stored_vision = store_vision_image(
                _png_bytes,
                {
                    "pair": symbol,
                    "chart_timeframe": tf,
                    "chart_source": chart_source,
                    "dashboard_source": request.headers.get("X-Dashboard-Source", "unknown"),
                    "chart_generated_at": data.get("chart_generated_at")
                    or data.get("chartGeneratedAt"),
                },
            )
        except Exception as _vs_err:
            log.debug("[AI CHART] vision image retention skipped: %s", _vs_err)

        # Vision body was trimmed to A-H framework + 8-line structured footer (audit fix).
        # Token budgets cut accordingly: triple 1100->450, dual/single 800->350.
        _vision_default_max = 450 if triple_mode else 350
        _max_tokens = int(CONFIG.get("VISION_MAX_TOKENS", _vision_default_max) or _vision_default_max)
        _vision_model = get_ai_model(CONFIG, "VISION_MODEL", "grok-4.3")
        _vision_temp = float(AITemperatureConfig.get_temperature("vision"))
        _user_parts = _chart_blocks_to_openai_user_content(content)
        _completion = _xai_chat_completions_retry(
            client,
            model=_vision_model,
            max_tokens=_max_tokens,
            temperature=_vision_temp,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": _user_parts},
            ],
        )

        analysis = ((_completion.choices[0].message.content or "").strip() or "No analysis returned.")
        if chart_source == "acm":
            _id_text = f"instrument/timeframe from request metadata ({symbol}/{tf})"
            analysis = analysis.replace("chart label not legible - from request", _id_text)

        try:
            _entry_hint = float(
                (sig or {}).get("price")
                or (sig or {}).get("entry")
                or (eb or {}).get("current_price")
                or 0
            )
        except (TypeError, ValueError):
            _entry_hint = None

        structured = _extract_vision_structured(
            analysis,
            direction_str,
            entry_price=_entry_hint,
            current_level_sets=current_levels,
        )

        try:
            if bool(CONFIG.get("CHART_VISION_DATASET_ENABLED", True)):
                authoritative_tf = "H1" if triple_mode else ("H4" if dual_mode else tf)
                authoritative_image = img_h1 if triple_mode and img_h1 else img_h4
                sample = _create_vision_sample(
                    _AUDIT_DB,
                    {
                        "symbol": symbol,
                        "timeframe": authoritative_tf,
                        "tf_role": authoritative_tf,
                        "asset_type": asset_type,
                        "source": "chart-analysis",
                        "chart_source": chart_source,
                        "request_id": data.get("request_id"),
                        "image": authoritative_image,
                        "engine_b": eb or {},
                        "structured": structured,
                        "model_context": {
                            "direction": direction_str,
                            "entry_price": _entry_hint,
                            "sl": (sig or {}).get("sl"),
                            "tp1": (sig or {}).get("tp1"),
                            "rr1": (sig or {}).get("rr1"),
                            "cf_primary": _cf_prim if '_cf_prim' in locals() else None,
                            "cf_h1": _cf_h1 if '_cf_h1' in locals() else None,
                            "cf_h4": _cf_h4 if '_cf_h4' in locals() else None,
                            "cf_d1": _cf_d1 if '_cf_d1' in locals() else None,
                        },

                    },
                    _VISION_ARTIFACTS_DIR,
                )
                # Vision auto-label confidence is derived from RIGHT EDGE
                # classification + TF alignment (audit fix: previously hardcoded 0.60).
                _re_status = (structured.get("right_edge_status") or "").upper().replace("_", " ")
                _re_conf_map = {
                    "CONFIRMS": 0.85,
                    "REVIEW": 0.50,
                    "POTENTIAL REVERSAL": 0.20,
                }
                _vision_conf = _re_conf_map.get(_re_status, 0.50)
                if structured.get("confirms_direction", True) is False:
                    _vision_conf = max(0.0, _vision_conf - 0.10)
                _vision_conf = round(min(1.0, max(0.0, _vision_conf)), 3)

                auto_label = _create_vision_label(
                    _AUDIT_DB,
                    {
                        "sample_id": sample.id,
                        "label_source": "auto",
                        "review_status": "auto",
                        "structured": structured,
                        "engine_b": eb or {},
                        "confidence": _vision_conf,
                    },
                )
                v1_pred = _create_vision_prediction(
                    _AUDIT_DB,
                    {
                        "sample_id": sample.id,
                        "model_version": "chart_vision_v1",
                        "right_edge_status": structured.get("right_edge_status") or auto_label.right_edge_status,
                        "tf_alignment": "ALIGNED" if structured.get("confirms_direction", True) else "CONFLICTED",
                        "scalp_rating": (structured.get("style_ratings") or {}).get("scalp") or "MODERATE",
                        "intraday_rating": (structured.get("style_ratings") or {}).get("intraday") or "MODERATE",
                        "swing_rating": (structured.get("style_ratings") or {}).get("swing") or "MODERATE",
                        "levels": structured.get("level_suggestions") or {},
                        "confidence": _vision_conf,
                        "payload": {
                            "model": _vision_model,
                            "analysis_excerpt": analysis[:500],
                        },
                    },
                )
                if bool(CONFIG.get("CHART_VISION_V2_SHADOW_ENABLED", True)):
                    v2 = _infer_chart_vision_v2(
                        sample,
                        _VISION_MODEL_DIR,
                        current_level_sets=current_levels,
                    )
                    if isinstance(v2, dict):
                        _create_vision_prediction(
                            _AUDIT_DB,
                            {
                                "sample_id": sample.id,
                                "model_version": "chart_vision_v2",
                                "right_edge_status": v2.get("right_edge_status"),
                                "tf_alignment": v2.get("tf_alignment"),
                                "scalp_rating": v2.get("scalp_rating"),
                                "intraday_rating": v2.get("intraday_rating"),
                                "swing_rating": v2.get("swing_rating"),
                                "levels": v2.get("level_suggestions") or {},
                                "confidence": v2.get("confidence", 0.5),
                                "disagreement_score": 0.0 if v2.get("right_edge_status") == v1_pred.right_edge_status else 1.0,
                                "payload": v2,
                            },
                        )
        except Exception as _vision_log_err:
            log.debug(f"[AI CHART] vision sample/prediction log skipped: {_vision_log_err}")

        _tf_label = "D1+H4+H1" if triple_mode else ("D1+H4" if dual_mode else tf)
        _vision_payload = {
            "analysis": analysis,
            "structured": structured,
            "model": _vision_model,
            "symbol": symbol,
            "tf": _tf_label,
        }
        # Cache vision result for AI reconciliation layer (keyed by symbol with _vision suffix)
        try:
            _vsym_key = str(symbol or "").replace("/", "_") + "_vision"
            _engine_b_cache_put(_vsym_key, _vision_payload)
        except Exception:
            pass
        try:
            from ai_review_logger import (
                log_ai_review,
                map_vision_rating_to_ai_state,
                REVIEW_TYPE_CHART_VISION,
            )
            if sig:
                ensure_trace_id(sig)
            _re_status = (structured or {}).get("right_edge_status", "")
            _vision_ai_state = map_vision_rating_to_ai_state(_re_status)
            _vision_before = bool((sig or {}).get("trade", False))
            _vision_after = _vision_before  # chart-analysis doesn't set trade directly here
            log_ai_review(
                symbol=symbol,
                asset_type=(pair or {}).get("type", "unknown") if pair else "unknown",
                review_type=REVIEW_TYPE_CHART_VISION,
                model=_vision_model,
                provider="xAI",
                prompt_version=get_prompt_version("chart_vision_audit"),
                input_packet={"symbol": symbol, "tf": _tf_label},
                has_chart_image=True,
                candle_freshness_status=(
                    "server_render" if _is_server_render
                    else ("stale_unknown" if _chart_ts_warnings else "client_render")
                ),
                engine_a_state=(sig or {}).get("confluenceScore"),
                engine_b_state=None,
                engine_c_state=None,
                engine_d_state=None,
                risk_state=None,
                ai_review_state=_vision_ai_state,
                ai_confidence=None,
                contradictions_count=0,
                missing_information_count=len(_chart_ts_warnings),
                parse_success=True,
                schema_valid=bool(_re_status),
                execution_allowed_before_ai=_vision_before,
                execution_allowed_after_ai=_vision_after,
                final_action="vision_advisory",
                trace_id=(sig or {}).get("trace_id") if sig else None,
                image_hash=_stored_vision.get("image_hash"),
                image_retrieval_key=_stored_vision.get("retrieval_key"),
            )
        except Exception as _log_err:
            log.debug("[AI_AUDIT] Chart Vision audit log failed: %s", _log_err)

        _response_payload: dict = {
            "analysis": analysis,
            "structured": structured,
            "model": _vision_model,
            "symbol": symbol,
            "tf": _tf_label,
            "dual_tf": dual_mode,
            "triple_tf": triple_mode,
        }
        # Return the rendered chart image so the UI can display what the AI analyzed
        if img_h4:
            _response_payload["chart_image"] = img_h4
        if img_h1:
            _response_payload["chart_image_h1"] = img_h1
        if img_d1:
            _response_payload["chart_image_d1"] = img_d1
        if _chart_ts_warnings:
            _response_payload["chart_timestamp_warnings"] = _chart_ts_warnings
        if _latest_candle_ts:
            _response_payload["latest_candle_ts"] = _latest_candle_ts
        return jsonify(_response_payload)

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


@app.route("/api/vision-sample", methods=["POST"])
def api_vision_sample():
    """Persist chart sample + artifacts for vision training."""
    try:
        payload = request.get_json(silent=True) or {}
        if not payload.get("image") and not payload.get("image_path"):
            return jsonify({"error": "Missing image or image_path"}), 400
        sample = _create_vision_sample(_AUDIT_DB, payload, _VISION_ARTIFACTS_DIR)
        return jsonify({"sample": _vision_asdict_safe(sample)})
    except Exception as e:
        log.error(f"api_vision_sample error: {e}")
        return jsonify({"error": str(e)}), 500


@app.route("/api/vision-label", methods=["POST"])
def api_vision_label():
    """Persist labels/provenance for a stored vision sample."""
    try:
        payload = request.get_json(silent=True) or {}
        if not payload.get("sample_id"):
            return jsonify({"error": "Missing sample_id"}), 400
        label = _create_vision_label(_AUDIT_DB, payload)
        return jsonify({"label": _vision_asdict_safe(label)})
    except Exception as e:
        log.error(f"api_vision_label error: {e}")
        return jsonify({"error": str(e)}), 500


@app.route("/api/vision-infer-v2", methods=["POST"])
def api_vision_infer_v2():
    """Run advisory-only hybrid chart_vision_v2 inference and log prediction."""
    try:
        payload = request.get_json(silent=True) or {}
        sample = None
        sample_id = int(payload.get("sample_id") or 0)
        if sample_id > 0:
            sample = _fetch_vision_sample(_AUDIT_DB, sample_id)
            if sample is None:
                return jsonify({"error": f"Unknown sample_id: {sample_id}"}), 404
        else:
            if not payload.get("image") and not payload.get("image_path"):
                return jsonify({"error": "Provide sample_id or image/image_path"}), 400
            sample = _create_vision_sample(_AUDIT_DB, payload, _VISION_ARTIFACTS_DIR)
            sample_id = sample.id

        current_level_sets = payload.get("current_level_sets") or {}
        inference = _infer_chart_vision_v2(
            sample,
            _VISION_MODEL_DIR,
            current_level_sets=current_level_sets,
        )
        if not isinstance(inference, dict):
            return (
                jsonify(
                    {
                        "error": "chart_vision_v2 model not available",
                        "hint": "train with: python tools/train_chart_vision_v2.py",
                    }
                ),
                404,
            )
        prediction = _create_vision_prediction(
            _AUDIT_DB,
            {
                "sample_id": sample_id,
                "model_version": inference.get("model_version", "chart_vision_v2"),
                "right_edge_status": inference.get("right_edge_status"),
                "tf_alignment": inference.get("tf_alignment"),
                "scalp_rating": inference.get("scalp_rating"),
                "intraday_rating": inference.get("intraday_rating"),
                "swing_rating": inference.get("swing_rating"),
                "levels": inference.get("level_suggestions") or {},
                "confidence": inference.get("confidence", 0.5),
                "payload": inference,
            },
        )
        return jsonify(
            {
                "sample_id": sample_id,
                "inference": inference,
                "prediction": _vision_asdict_safe(prediction),
            }
        )
    except Exception as e:
        log.error(f"api_vision_infer_v2 error: {e}")
        return jsonify({"error": str(e)}), 500


@app.route("/api/vision-metrics", methods=["GET"])
def api_vision_metrics():
    """Fetch shadow metrics for dataset volume, drift, disagreement, and accuracy."""
    try:
        try:
            lookback_days = int(request.args.get("lookback_days", 30))
        except (TypeError, ValueError):
            lookback_days = 30
        metrics = _fetch_vision_metrics(_AUDIT_DB, lookback_days=lookback_days)
        return jsonify(_json_safe(metrics))
    except Exception as e:
        log.error(f"api_vision_metrics error: {e}")
        return jsonify({"error": str(e)}), 500


@app.route("/api/vision-train-v2", methods=["POST"])
def api_vision_train_v2():
    """Train advisory hybrid model using labeled vision_samples/labels."""
    try:
        payload = request.get_json(silent=True) or {}
        min_samples = int(payload.get("min_samples", 40))
        result = _train_hybrid_model(
            db_path=_AUDIT_DB,
            model_dir=_VISION_MODEL_DIR,
            min_samples=min_samples,
        )
        return jsonify({"result": _vision_asdict_safe(result)})
    except Exception as e:
        log.error(f"api_vision_train_v2 error: {e}")
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
















def _mt5_connection_health() -> dict:
    try:
        import mt5_executor
    except Exception as exc:
        return {
            "available": False,
            "connected": False,
            "detail": f"import_failed:{exc}",
        }

    try:
        mt5 = mt5_executor._get_mt5()
    except Exception as exc:
        return {
            "available": False,
            "connected": False,
            "detail": f"load_failed:{exc}",
        }

    if mt5 is None:
        return {
            "available": False,
            "connected": False,
            "detail": "mt5_library_unavailable",
        }

    try:
        terminal = mt5.terminal_info()
    except Exception as exc:
        return {
            "available": True,
            "connected": False,
            "detail": f"terminal_info_failed:{exc}",
        }

    connected = terminal is not None
    return {
        "available": True,
        "connected": connected,
        "detail": "ok" if connected else "not_initialized",
    }


def _feed_health_snapshot() -> dict:
    rows = []
    limits = scan_candle_limits()
    for pair in ACTIVE_PAIRS:
        timeframe_meta = {}
        for tf in ("D1", "H4", "H1"):
            limit = limits.get(tf)
            if not limit:
                continue
            meta = _get_candle_fetch_meta(pair, tf, limit)
            if meta:
                timeframe_meta[tf] = meta
        rows.append(
            {
                "pair": pair.get("display", pair.get("symbol")),
                "symbol": pair.get("symbol"),
                "type": pair.get("type"),
                "source": pair.get("source"),
                "timeframes": timeframe_meta,
            }
        )
    return {
        "activePairCount": len(ACTIVE_PAIRS),
        "pairsWithCachedMeta": sum(1 for row in rows if row["timeframes"]),
        "mt5": _mt5_connection_health(),
        "pairs": rows,
    }


@app.route("/api/feed-health")
def api_feed_health():
    return jsonify(_json_safe(_feed_health_snapshot()))




@app.route("/api/open-trades-timed")
def api_open_trades_timed():
    """
    Returns open positions from MT5 + Bybit enriched with:
    - open_time_iso: when the trade was opened (from MT5 pos.time or audit_log ts)
    - style: scalp / intraday / swing (from audit_log)
    - audit_engine / is_engine_d: Engine D (Scalp Lab) vs other engines
    - tp_partial: +1R scale-out level from audit (Engine D)
    - timed_tp_mode / trail_activation_r: global TIMED_EXIT TP policy
    - be_trigger_min / close_trigger_min: hybrid timed exit windows (omitted for Engine D)
    Used by the dashboard countdown timer on each position card.
    """
    from datetime import datetime, timezone as _tz
    from timed_exit_monitor import (
        _activation_r_for,
        _get_timed_cfg,
        _load_recent_audit_rows,
        _match_audit_row_for_position,
    )

    tcfg = _get_timed_cfg(lambda: CONFIG)

    out = []
    now_ts = datetime.now(_tz.utc).timestamp()

    def _fetch_mt5_positions():
        from mt5_executor import mt5_get_positions
        _res = mt5_get_positions()
        if isinstance(_res, dict) and _res.get("error"):
            return {"error": _res["error"]}
        return _res

    def _fetch_bybit_positions():
        from bybit_executor import bybit_get_positions
        _res = bybit_get_positions()
        if isinstance(_res, dict) and _res.get("error"):
            return {"error": _res["error"]}
        return _res

    try:
        from concurrent.futures import ThreadPoolExecutor

        # Both broker reads are independent and read-only. Fetching them in
        # parallel keeps dashboard latency at max(MT5, Bybit), not MT5+Bybit.
        with ThreadPoolExecutor(max_workers=2) as pool:
            mt5_future = pool.submit(_fetch_mt5_positions)
            bybit_future = pool.submit(_fetch_bybit_positions)
            mt5_res = mt5_future.result()
            bybit_res = bybit_future.result()
    except Exception as e:
        return jsonify({"error": f"Broker position fetch failed: {e}"}), 500

    if mt5_res.get("error"):
        return jsonify(mt5_res), 500
    if bybit_res.get("error"):
        return jsonify(bybit_res), 500

    mt5_positions = mt5_res.get("positions", [])
    bybit_positions = bybit_res.get("positions", [])
    for p in bybit_positions:
        p["_bybit"] = True

    all_positions = mt5_positions + bybit_positions

    audit_rows = _load_recent_audit_rows(_AUDIT_DB)

    for p in all_positions:
        ticket = str(p.get("ticket", ""))
        audit = _match_audit_row_for_position(p, audit_rows) or {}
        audit_engine = str(audit.get("engine") or "").strip().lower()
        is_engine_d = audit_engine in ("scalp", "engine d", "scalp_vp")

        # Resolve open time
        raw_open_time = p.get("open_time", 0)  # Unix seconds from MT5
        audit_ts_iso  = audit.get("ts", "")
        try:
            raw_open_ts = float(raw_open_time or 0)
        except (TypeError, ValueError):
            raw_open_ts = 0.0
        # Some MT5 terminals/brokers can surface position open times that are ahead
        # of UTC "now" from the API consumer's perspective. Ignore implausible future
        # timestamps and fall back to audited open time instead.
        if raw_open_ts > 0 and raw_open_ts <= (now_ts + 300):
            open_ts = raw_open_ts
            open_iso = datetime.fromtimestamp(open_ts, tz=_tz.utc).isoformat()
        elif audit_ts_iso:
            try:
                open_ts = datetime.fromisoformat(
                    audit_ts_iso.replace("Z", "+00:00")
                ).timestamp()
                open_iso = audit_ts_iso
            except Exception:
                open_ts = now_ts
                open_iso = ""
        else:
            open_ts = now_ts
            open_iso = ""

        mins_open = (now_ts - open_ts) / 60.0

        # Resolve style safely: scalp must never degrade to generic intraday labeling.
        style = ""
        audit_style = str(audit.get("style") or "").strip().lower()
        pos_engine = str(p.get("engine") or "").strip().lower()
        engine_hint = audit_engine or pos_engine
        if engine_hint in ("scalp", "engine d", "scalp_vp"):
            style = "scalp"
        elif audit_style in ("scalp", "intraday", "swing"):
            style = audit_style
        elif audit_style == "trend":
            style = "swing"
        elif engine_hint in ("intraday", "swing"):
            style = engine_hint

        # Timed exit windows: merged TIMED_EXIT (scalp/intraday/swing). Engine D trades
        # are managed by milestone logic + broker TP/SL - timed_exit_monitor skips them.
        be_min = None
        close_min = None
        style_cfg = tcfg.get(style) if style in ("scalp", "intraday", "swing") else {}
        timed_global = bool(tcfg.get("enabled", True))
        timed_style_on = bool((style_cfg or {}).get("timed_close_enabled", True))

        if (
            style in ("scalp", "intraday", "swing")
            and timed_global
            and timed_style_on
            and not is_engine_d
        ):
            scfg = style_cfg or {}
            if style == "swing":
                be_min = float(scfg.get("breakeven_days", 2.5)) * 1440.0
                close_min = float(scfg.get("close_days", 5.0)) * 1440.0
            else:
                be_min = float(scfg.get("breakeven_min", 5 if style == "scalp" else 15))
                close_min = float(scfg.get("close_min", 10 if style == "scalp" else 30))

        profit = float(p.get("profit", 0))
        in_profit = profit > 0

        _tp_part = audit.get("tp_partial")
        try:
            _tp_part_f = float(_tp_part) if _tp_part not in (None, "") else None
        except (TypeError, ValueError):
            _tp_part_f = None

        _se = CONFIG.get("SCALP_ENGINE") or {}

        # Construct position data with conditional timer fields
        res_p = {
            "ticket":          ticket,
            "pair":            p.get("pair") or p.get("symbol", ""),
            "direction":       p.get("direction", "LONG"),
            "profit":          profit,
            "entry":           p.get("entry", 0),
            "sl":              p.get("sl", 0),
            "tp":              p.get("tp", 0),
            "volume":          p.get("volume", 0),
            "exchange":        "bybit" if p.get("_bybit") else "mt5",
            "open_time_iso":   open_iso,
            "mins_open":       round(mins_open, 1),
            "style":           style,
            "in_profit":       in_profit,
            "be_reached":      False,
            "close_reached":   False,
            "mins_to_be":      0,
            "mins_to_close":   0,
            "audit_engine":    audit_engine or None,
            "is_engine_d":     is_engine_d,
            "tp_partial":      _tp_part_f,
            "timed_tp_mode":   str(tcfg.get("tp_mode", "fixed")),
            "trail_activation_r": _activation_r_for(tcfg, style),
            "timed_exit_daemon_enabled": timed_global,
            "timed_close_style_enabled": timed_style_on if style in ("scalp", "intraday", "swing") else None,
            "sl_after_partial": str(_se.get("SL_AFTER_PARTIAL", "tp_partial")),
            "live_milestone_management": bool(_se.get("LIVE_MILESTONE_MANAGEMENT", False)),
        }

        # Attach timers only if both are resolved (hides UI timer for scalps/unknowns)
        if be_min is not None and close_min is not None:
            res_p.update({
                "be_trigger_min":  round(be_min, 1),
                "close_trigger_min": round(close_min, 1),
                "be_reached":      mins_open >= be_min,
                "close_reached":   mins_open >= close_min,
                "mins_to_be":      round(max(0, be_min - mins_open), 1),
                "mins_to_close":   round(max(0, close_min - mins_open), 1),
            })
        else:
            res_p.update({
                "be_trigger_min":  None,
                "close_trigger_min": None,
            })

        out.append(res_p)

    return jsonify({"positions": out, "count": len(out)})


def _check_api_keys() -> None:
    """Log startup warnings for missing API keys so the operator knows what's degraded."""

    missing = []

    if not os.environ.get("EODHD_KEY"):
        missing.append(
            "EODHD_KEY - real-time WebSocket prices, indicators, screener, and news all disabled"
        )

    _ak = get_ai_api_key(CONFIG)

    if not _ak:
        missing.append("AI API key - AI trade grading disabled")

    if not os.environ.get("CRYPTOPANIC_KEY"):
        missing.append("CRYPTOPANIC_KEY (optional) - crypto news sentiment reduced")

    if not os.environ.get("FINNHUB_KEY"):
        missing.append("FINNHUB_KEY (optional) - Polygon news fallback disabled")

    if missing:
        log.warning("[KEYS] Running in degraded mode - set missing keys in .env:")

        for m in missing:
            log.warning(f"  • {m}")

    else:
        log.info("[KEYS] All API keys configured")


# ── Injected runtime definitions (scan helpers, analyze_pair, event risk) ──


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
    pair,
    btc_bias,
    style="swing",
    use_naked_engine=False,
    regime_context=None,
    preloaded_candles=None,
    preloaded_market_state=None,
    preloaded_fetch_meta=None,
    intermarket_snapshot=None,
):

    pair_profile = get_pair_profile(pair)
    _score_group = get_pair_score_group(pair)
    _pair_ctx = dict(pair or {})
    _pair_ctx["score_group"] = _score_group

    _lim = scan_candle_limits()
    preloaded_candles = preloaded_candles or {}
    preloaded_market_state = preloaded_market_state or {}
    preloaded_fetch_meta = preloaded_fetch_meta or {}

    # Determine if the primary timeframe (H1) is currently forming.
    _h1_state = preloaded_market_state.get("H1")
    h1 = preloaded_candles.get("H1")
    if isinstance(_h1_state, dict):
        _h1_confirmed = list(_h1_state.get("confirmed") or [])
        _h1_forming = _h1_state.get("forming")
        # For MT5 forex we default to confirmed candles for scoring paths.
        if pair.get("source") == "mt5" and pair.get("type") == "forex":
            h1 = _h1_confirmed
        else:
            h1 = _h1_confirmed + ([_h1_forming] if _h1_forming else [])
        is_forming = bool(_h1_state.get("is_live"))
    elif h1:
        try:
            from athena_app.services.market_state import split_market_state

            _h1_state = split_market_state(
                h1,
                "H1",
                pair.get("display") or pair.get("symbol") or "",
            )
            is_forming = bool(_h1_state.get("is_live"))
        except Exception:
            is_forming = False
    else:
        try:
            from candle_manager import fetch_market_state as _fms
            h1_state = _fms(pair, "H1", _lim["H1"])
            if pair.get("source") == "mt5" and pair.get("type") == "forex":
                h1 = list(h1_state.get("confirmed") or [])
            else:
                h1 = h1_state["confirmed"] + ([h1_state["forming"]] if h1_state["forming"] else [])
            is_forming = h1_state["is_live"]
        except Exception:
            h1 = fetch_candles(pair, "H1", _lim["H1"])
            is_forming = False

    _d1_state = preloaded_market_state.get("D1")
    d1 = preloaded_candles.get("D1")
    if isinstance(_d1_state, dict):
        _d1_confirmed = list(_d1_state.get("confirmed") or [])
        _d1_forming = _d1_state.get("forming")
        # Combine to raw candles for trim, then re-split after trimming.
        d1_raw = _d1_confirmed + ([_d1_forming] if _d1_forming else [])
    else:
        d1_raw = d1

    if d1_raw is None:
        d1_raw = fetch_candles(pair, "D1", _lim["D1"])

    # Trim session-ahead tail BEFORE market-state split to avoid misclassification.
    try:
        from athena_app.services.market_state import trim_mt5_d1_broker_session_ahead_tail

        if d1_raw:
            d1_raw, _d1_sess_trim = trim_mt5_d1_broker_session_ahead_tail(
                pair, "D1", list(d1_raw)
            )
    except Exception as _d1_trim_err:
        log.debug(
            "[ANALYZE] %s D1 session-ahead trim skipped: %s",
            pair.get("display", "?"),
            _d1_trim_err,
        )

    # Re-split after trimming to get correct confirmed/forming classification.
    if isinstance(_d1_state, dict):
        _d1_confirmed = list(_d1_state.get("confirmed") or [])
        _d1_forming = _d1_state.get("forming")
        # Apply confirmed-only policy to all forex pairs regardless of source
        # to prevent forming-bar contamination in RSI/EMA/ATR calculations.
        if pair.get("type") == "forex":
            d1 = _d1_confirmed
        else:
            d1 = _d1_confirmed + ([_d1_forming] if _d1_forming else [])
    else:
        d1 = d1_raw

    _h4_state = preloaded_market_state.get("H4")
    h4 = preloaded_candles.get("H4")
    if isinstance(_h4_state, dict):
        _h4_confirmed = list(_h4_state.get("confirmed") or [])
        _h4_forming = _h4_state.get("forming")
        if pair.get("source") == "mt5" and pair.get("type") == "forex":
            h4 = _h4_confirmed
        else:
            h4 = _h4_confirmed + ([_h4_forming] if _h4_forming else [])
    if h4 is None:
        h4 = fetch_candles(pair, "H4", _lim["H4"])

    if not d1 or not h4 or not h1:
        log.warning(
            f"[ANALYZE] {pair.get('display', '?')} no candles - "
            f"D1={len(d1) if d1 else 0} H4={len(h4) if h4 else 0} H1={len(h1) if h1 else 0}"
        )
        return None

    # F8: As requested, we no longer drop the last (forming) bar automatically.
    # This provides live-bar scoring for maximum accuracy.
    # Indicators are now calculated on the full live series.

    if len(d1) < 220 or len(h4) < 50 or len(h1) < 50:
        log.warning(
            f"[ANALYZE] {pair.get('display', '?')} insufficient bars - "
            f"D1={len(d1)}/220 H4={len(h4)}/50 H1={len(h1)}/50"
        )
        return None

    # Pre-scoring freshness gate: skip indicator calculation if any required
    # TF is stale. This prevents wasted CPU and false signal log entries from
    # scoring on stale data that would be blocked at execution time anyway.
    if CONFIG.get("PRE_SCORING_FRESHNESS_GATE_ENABLED", True):
        try:
            from athena_app.services.market_state import candle_freshness_diagnostic

            _stale_tfs = []
            _freshness_diag = {}
            for _tf, _candles in (("D1", d1), ("H4", h4), ("H1", h1)):
                _diag = candle_freshness_diagnostic(
                    pair, _tf, _candles,
                    source=pair.get("source"),
                )
                _freshness_diag[_tf] = _diag
                _sev = _diag.get("stalenessSeverity", "")
                if _sev and _sev != "fresh":
                    _stale_tfs.append(f"{_tf}:{_sev}")

            # Crypto-specific D1 staleness threshold: enforce max hours since last bar open.
            if pair.get("type") == "crypto" and d1:
                try:
                    import time
                    _last_candle = d1[-1] if d1 else None
                    if _last_candle:
                        _last_time = _last_candle.get("time")
                        if _last_time:
                            _last_ts = _coerce_utc_datetime(_last_time)
                            if _last_ts:
                                _now = _current_utc_datetime()
                                _age_hours = (_now - _last_ts).total_seconds() / 3600
                                _max_hours = CONFIG.get("CRYPTO_D1_MAX_STALE_HOURS", 25)
                                if _age_hours > _max_hours:
                                    _stale_tfs.append(f"D1:crypto_stale_{int(_age_hours)}h")
                except Exception:
                    pass

            if _stale_tfs:
                _reason = "STALE_DATA_PRE_SCORING:" + ",".join(_stale_tfs)
                log.warning(
                    "[ANALYZE] %s pre-scoring freshness block: %s",
                    pair.get("display", "?"),
                    _reason,
                )
                return {
                    "pair": pair.get("display"),
                    "symbol": pair.get("symbol"),
                    "score": 0,
                    "confluenceScore": 0,
                    "maxScore": 3.0,
                    "direction": "neutral",
                    "trendState": "neutral",
                    "executable": False,
                    "dataFreshness": {
                        "allowed": False,
                        "reason": _reason,
                        "diagnostics": _freshness_diag,
                    },
                    "candleFetchMeta": {
                        "D1": _freshness_diag.get("D1"),
                        "H4": _freshness_diag.get("H4"),
                        "H1": _freshness_diag.get("H1"),
                        "pairSource": pair.get("source"),
                    },
                    "is_forming": is_forming,
                }
        except Exception as _prefresh_err:
            log.debug(
                "[ANALYZE] %s pre-scoring freshness check skipped: %s",
                pair.get("display", "?"),
                _prefresh_err,
            )

    d1i = calc_indicators_with_normalized(d1, pair.get("type", "stock"))

    h4i = calc_indicators_with_normalized(h4, pair.get("type", "stock"))

    # Inject fib proximity into H4 snap so structure factor in compute_factor_scores() is non-None
    try:
        _h4_fib = calc_fib(h4)
        _h4_close = h4[-1]["close"] if h4 else None
        if _h4_fib and _h4_close:
            h4i["snap"]["fib_proximity"] = calc_fib_proximity(float(_h4_close), _h4_fib)
    except Exception:
        pass  # gracefully degrade - structure factor stays None

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

    # F11: EMA200 slope computed once here (UI display only - not fed to calc_confluence)

    _e200 = calc_ema([c["close"] for c in d1], 200)

    e200s = (
        (_e200[-1] - _e200[-21]) / _e200[-21]
        if _e200 and len(_e200) >= 21 and _e200[-1] and _e200[-21]
        else 0
    )

    # Fetch funding rate + open interest for crypto pairs

    _funding_rate = None

    _oi_divergence = None

    _oi_data = None

    _oi_context = None

    if pair.get("type") == "crypto":
        _bn_sym = pair.get("symbol", "").replace("/", "")  # e.g. BTCUSDT

        # Bybit funding rate - execution is on Bybit; fall back to Binance if Bybit fails
        _fr_resp = _fetch_bybit_funding_rate(_bn_sym)
        if isinstance(_fr_resp, dict) and not _fr_resp.get("error"):
            _funding_rate = _fr_resp.get("rate")
        else:
            _fr_resp = _fetch_funding_rate(_bn_sym)
            _funding_rate = (
                _fr_resp.get("rate")
                if isinstance(_fr_resp, dict) and not _fr_resp.get("error")
                else None
            )
            if _funding_rate is not None:
                log.debug(
                    "[FUNDING] %s: using Binance fallback rate",
                    pair.get("display", _bn_sym),
                )

        if str(CONFIG.get("ENGINE_A_CRYPTO_DERIVATIVES_FEED", "bybit")).lower() == "bybit":
            _oi_data = _fetch_bybit_open_interest(_bn_sym)
            if (
                isinstance(_oi_data, dict)
                and _oi_data.get("error")
                and bool(CONFIG.get("ENGINE_A_CRYPTO_DERIVATIVES_BINANCE_FALLBACK", False))
            ):
                _oi_data = _fetch_open_interest(_bn_sym)
        else:
            _oi_data = _fetch_open_interest(_bn_sym)

        _prev_close = d1[-2]["close"] if d1 and len(d1) >= 2 else None

        _cur_close = h1i["snap"].get("close") or (d1[-1]["close"] if d1 else None)

        if _cur_close and _prev_close:
            _oi_divergence = _calc_oi_divergence(_oi_data, _cur_close, _prev_close)
            if _oi_data.get("oiChange") is not None:
                _oi_context = {
                    "oi_change_pct": float(_oi_data["oiChange"]),
                    "price_change_pct": (_cur_close - _prev_close) / _prev_close * 100.0,
                }

    # Engine A v2: all asset classes (including forex) route through calc_confluence

    _usd_relative_strength = fetch_usd_relative_strength_context(pair, _cf_h4c, tf="H4")
    _intermarket_raw_context = None
    try:
        if intermarket_snapshot:
            from intermarket import build_symbol_context

            _intermarket_raw_context = build_symbol_context(
                _pair_ctx,
                intermarket_snapshot,
                config=CONFIG,
            )
    except Exception as _im_ctx_err:
        log.debug(
            "[INTERMARKET] %s context build skipped: %s",
            pair.get("display"),
            _im_ctx_err,
        )

    # Extract close-price series for real 30-day correlation (Bug 2 fix)
    _asset_prices = None
    _benchmark_prices = None
    if pair.get("type") == "crypto" and _cf_h4c:
        _asset_prices = [float(c.get("close", 0)) for c in _cf_h4c if c.get("close") is not None]
        if _asset_prices and len(_asset_prices) >= 15:
            try:
                _btc_candles = fetch_candles(
                    {"symbol": "BTCUSDT", "source": "binance"}, "H4", len(_asset_prices)
                )
                if _btc_candles:
                    _benchmark_prices = [
                        float(c.get("close", 0)) for c in _btc_candles if c.get("close") is not None
                    ]
            except Exception as _btc_fetch_err:
                log.debug("[CORR] BTC benchmark fetch failed: %s", _btc_fetch_err)

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
        oi_data=_oi_data,
        oi_context=_oi_context,
        macro_context=_usd_relative_strength,
        intermarket_context=_intermarket_raw_context,
        asset_prices=_asset_prices,
        benchmark_prices=_benchmark_prices,
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
                    f"SCALP COUNTER-TREND: D1 EMA stack is {_d1_dir} - trading against higher-TF trend, reduce size"
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
    level_atr_feed = "signal"
    if (
        pair.get("type") == "crypto"
        and str(CONFIG.get("ENGINE_A_CRYPTO_LEVELS_FEED", "bybit")).lower() == "bybit"
    ):
        bybit_atr = _bybit_atr_for_levels(pair, _style)
        if bybit_atr:
            atr = bybit_atr
            level_atr_feed = "bybit"
        elif not bool(CONFIG.get("ENGINE_A_CRYPTO_LEVELS_SIGNAL_FEED_FALLBACK", False)):
            return None

    if price is None or not atr:
        return None

    # Engine A v2: unified 0-3.0 scale for all asset classes including forex
    if res.get("maxScoreOverride") is None:
        res["maxScoreOverride"] = 3.0
    max_score = res.get("maxScoreOverride") or _max_score_for_pair(pair)

    # Always derive explicit structural context from Engine B's H1/H4/D1 model so
    # Engine A and the forex scorer both use real zones, not just fib proxies.
    structure_data = None
    try:
        from market_structure import engine as _structure_engine
        from athena_app.services.structure_context import (
            apply_structure_context_to_score,
        )

        _structure_regime_label = _engine_b_regime_label(
            h4,
            pair.get("type", "stock"),
            res.get("regime"),
        )
        structure_data = _structure_engine.set_registry_context(
            pair.get("symbol") or pair.get("display")
        ).analyze_structure(
            d1,
            h4,
            h1,
            float(price),
            direction,
            float(atr),
            _structure_regime_label,
            asset_type=pair.get("type", ""),
            d1_snap=(d1i or {}).get("snap") or {},
            h4_snap=(h4i or {}).get("snap") or {},
            style=_style or "intraday",
            pair=pair,
        )
        _structure_adjustment = apply_structure_context_to_score(
            structure_data,
            direction=direction,
            base_score=float(res.get("score", 0.0) or 0.0),
            max_score=float(max_score or 0.0),
        )
        res["score"] = float(_structure_adjustment["adjusted_score"])
        _fd = dict(res.get("factorDiagnostics") or {})
        _fd["explicitStructureContext"] = _structure_adjustment
        res["factorDiagnostics"] = _fd

        _votes = dict(res.get("votes") or {})
        _sc = _structure_adjustment.get("components", {})
        if _sc.get("zone_proximity"):
            _votes["H4 Structural Zone"] = 1
        if _sc.get("ob_at_zone"):
            _votes["Order Block at Zone"] = 1
        if _sc.get("fvg_overlap"):
            _votes["FVG at Zone"] = 1
        _align = _sc.get("independent_direction_alignment")
        if _align == "aligned":
            _votes["Structure Alignment"] = 1
        elif _align == "opposed":
            _votes["Structure Alignment"] = -1
        if _votes:
            res["votes"] = _votes
    except Exception as _structure_err:
        log.debug(
            "[STRUCTURE-CONTEXT] %s skipped: %s",
            pair.get("display"),
            _structure_err,
        )

    fib = calc_fib(h4)

    _regime_state = res.get("regime", {}).get("state") if res.get("regime") else None
    _level_atr_class = get_pair_level_atr_class(pair)
    _fd_level = dict(res.get("factorDiagnostics") or {})
    _fd_level["levelAtrFeed"] = level_atr_feed
    _fd_level["levelAtrClass"] = _level_atr_class
    res["factorDiagnostics"] = _fd_level

    lvl = calc_levels(
        float(price), float(atr), direction, _level_atr_class,
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
    _structure_diag = (res.get("factorDiagnostics") or {}).get("explicitStructureContext") or {}
    _structure_components = _structure_diag.get("components") or {}
    if _structure_components.get("independent_direction_alignment") == "opposed":
        _sdir = _structure_components.get("independent_direction")
        _swarn = f"STRUCTURE CONTEXT OPPOSED: Engine B directional bias is {_sdir}"
        if _swarn not in warn_list:
            warn_list.append(_swarn)

    if pair_filter_enabled(pair, "divergence_warning"):
        for w in detect_div(d1, h4, h1):
            if w not in warn_list:
                warn_list.append(w)

    # OI divergence warnings are appended inside calc_confluence when oi_data is passed (no duplicate here).

    score_norm = (
        min(1.0, float(res["score"]) / float(max_score))
        if max_score and float(max_score) > 0
        else 0.0
    )

    # --- ENGINE B: NAKED MARKET STRUCTURE OVERLAY ---
    _engine_b_overlay_meta = None
    if use_naked_engine and res["score"] >= get_min_confluence_threshold(pair):
        try:
            from market_structure import (
                ENGINE_B_REASON_ADVERSE_DXY,
                ENGINE_B_REASON_RESISTANCE_TOO_CLOSE,
                ENGINE_B_REASON_STRUCTURAL_SL_HARD_CAP,
                ENGINE_B_REASON_SUPPORT_TOO_CLOSE,
                engine as naked_engine,
            )
            _overlay_style, _overlay_profile = _naked_scan_style_profile(
                _style,
                score_group=_score_group,
                asset_type=pair.get("type", ""),
            )

            if structure_data is None:
                _regime_label = _engine_b_regime_label(
                    h4,
                    pair.get("type", "stock"),
                    res.get("regime"),
                )
                structure_data = naked_engine.set_registry_context(
                    pair.get("symbol") or pair.get("display")
                ).analyze_structure(
                    d1,
                    h4,
                    h1,
                    float(price),
                    direction,
                    float(atr),
                    _regime_label,
                    asset_type=pair.get("type", ""),
                    d1_snap=(d1i or {}).get("snap") or {},
                    h4_snap=(h4i or {}).get("snap") or {},
                    style=_overlay_style,
                    pair=pair,
                )

            # 5.3 FIX: Engine B issues now add warnings instead of full block (return None).
            # This allows signals to be downgraded to watchlist instead of being filtered out.
            _engine_b_block_reasons = []
            if structure_data and structure_data.get("structural_verdict") == "CLEAR":
                _min_room = float(atr) * float(_overlay_profile.get("min_room_atr", 1.0))
                if (
                    direction == "LONG"
                    and structure_data.get("distance_to_res", float("inf")) < _min_room
                ):
                    log.warning(
                        "[ENGINE-B] pair=%s block_code=%s min_room_atr=%.4f dist_res=%s",
                        pair["display"],
                        ENGINE_B_REASON_RESISTANCE_TOO_CLOSE,
                        float(_overlay_profile.get("min_room_atr", 1.0)),
                        structure_data.get("distance_to_res"),
                    )
                    _engine_b_block_reasons.append(f"ENGINE-B: {ENGINE_B_REASON_RESISTANCE_TOO_CLOSE}")
                if (
                    direction == "SHORT"
                    and structure_data.get("distance_to_sup", float("inf")) < _min_room
                ):
                    log.warning(
                        "[ENGINE-B] pair=%s block_code=%s min_room_atr=%.4f dist_sup=%s",
                        pair["display"],
                        ENGINE_B_REASON_SUPPORT_TOO_CLOSE,
                        float(_overlay_profile.get("min_room_atr", 1.0)),
                        structure_data.get("distance_to_sup"),
                    )
                    _engine_b_block_reasons.append(f"ENGINE-B: {ENGINE_B_REASON_SUPPORT_TOO_CLOSE}")

                if pair.get("type") in ("crypto", "forex", "commodity"):
                    # Light fetch for DXY correlation (cached per scan batch)
                    _dxy_c = _get_dxy_h4_closes()
                    if _dxy_c:
                        _asset_c = [float(c["close"]) for c in h4]
                        _dxy_ok, _dxy_reason = naked_engine.check_macro_correlation_detail(
                            _asset_c, _dxy_c, direction
                        )
                        if not _dxy_ok:
                            log.warning(
                                "[ENGINE-B] pair=%s block_code=%s direction=%s",
                                pair["display"],
                                _dxy_reason or ENGINE_B_REASON_ADVERSE_DXY,
                                direction,
                            )
                            _engine_b_block_reasons.append(f"ENGINE-B: {_dxy_reason or ENGINE_B_REASON_ADVERSE_DXY}")

                # Safest Stop Loss Override (Combined Risk Management)
                if structure_data.get("recommended_stop_loss"):
                    _struct_sl = float(structure_data["recommended_stop_loss"])
                    _math_sl = float(lvl["sl"])
                    # LONG: SL below price - max() picks closer (higher) = tighter
                    # SHORT: SL above price - min() picks closer (lower) = tighter
                    lvl["sl"] = (
                        max(_math_sl, _struct_sl)
                        if direction == "LONG"
                        else min(_math_sl, _struct_sl)
                    )

                    # Hard SL distance cap - prevents runaway structural overrides
                    _max_sl_pct = CONFIG.get("MAX_SL_PCT", {}).get(pair.get("type", ""), 0.05)
                    _sl_dist_pct = abs(float(price) - float(lvl["sl"])) / float(price)
                    if _sl_dist_pct > _max_sl_pct:
                        log.warning(
                            "[ENGINE-B] pair=%s block_code=%s direction=%s sl_dist_pct=%.4f max_sl_pct=%.4f",
                            pair["display"],
                            ENGINE_B_REASON_STRUCTURAL_SL_HARD_CAP,
                            direction,
                            _sl_dist_pct,
                            _max_sl_pct,
                        )
                        _engine_b_block_reasons.append(f"ENGINE-B: {ENGINE_B_REASON_STRUCTURAL_SL_HARD_CAP}")

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
                _engine_b_overlay_meta = {
                    "applied": True,
                    "reason_codes": _engine_b_block_reasons,
                    "structural_verdict": structure_data.get("structural_verdict"),
                }
                # 5.3 FIX: Add Engine B block reasons to warnings for watchlist classification
                for _eb_reason in _engine_b_block_reasons:
                    if _eb_reason not in warn_list:
                        warn_list.append(_eb_reason)
        except Exception as e:
            log.error(f"[ENGINE-B] Error on {pair['display']}: {e}")
    # ------------------------------------------------

    _sl_distance_for_rr = abs(float(price) - float(lvl["sl"]))
    risk_pct = round((_sl_distance_for_rr / float(price) * 100) if float(price) else 0.0, 2)
    if _sl_distance_for_rr > 0:
        lvl["rr1"] = abs(float(lvl["tp1"]) - float(price)) / _sl_distance_for_rr
        lvl["rr2"] = abs(float(lvl["tp2"]) - float(price)) / _sl_distance_for_rr

    # FIX 4: News AI sentiment with guard rails
    try:
        from news_sentiment_feed import apply_news_sentiment_to_scan_result

        MAX_NEWS_IMPACT = 0.30

        # Get base score before news
        base_score = float(res.get("score", 0.0) or 0.0)
        _threshold = get_min_confluence_threshold(pair)

        # Apply raw news sentiment first to compute adjustment
        _pre_news_score = base_score
        apply_news_sentiment_to_scan_result(
            res,
            pair,
            config=CONFIG,
            eodhd_ticker_for_pair=_eodhd_ticker_for_pair,
            current_price=float(price),
        )
        _post_news_score = float(res.get("score", 0.0) or 0.0)
        news_adjustment = _post_news_score - _pre_news_score

        # GUARD 1: Don't let news rescue a weak setup to trade-tier
        if base_score < _threshold * 0.8:
            news_adjustment = min(0.0, news_adjustment)  # Only negative adjustments apply

        # GUARD 2: Cap total impact
        news_adjustment = max(-MAX_NEWS_IMPACT, min(MAX_NEWS_IMPACT, news_adjustment))

        # Apply guarded adjustment
        final_score = max(0.0, min(3.0, base_score + news_adjustment))
        res["score"] = round(final_score, 4)
        if res.get("final_score") is not None:
            res["final_score"] = res["score"]

        # LOG for audit trail
        res["news_adjustment"] = round(news_adjustment, 4)
        res["pre_news_score"] = round(_pre_news_score, 4)
    except Exception as _ns_err:
        log.debug("[NewsAI] scan blend skipped: %s", _ns_err)

    # Dynamic Confluence Scaling: Anchor the UI 67% mark to the actual pair threshold.
    # This prevents strong Crypto signals (e.g. 1.88) from looking 'WEAK' just because 3.0 is impossible.
    _threshold = get_min_confluence_threshold(pair)
    _raw_pct = (res["score"] / _threshold) * 67 if _threshold > 0 else 0
    _confluence_pct = min(100, max(0, round(_raw_pct)))

    try:
        record_signal_event(
            engine="engine_a",
            score=res.get("score"),
            max_score=max_score,
            passed=bool(res.get("score", 0) >= _threshold),
            expected_prob=_confluence_pct / 100.0,
            feature_map=res.get("factor_scores") if isinstance(res.get("factor_scores"), dict) else {},
            db_path=_AUDIT_DB,
            meta={
                "asset_type": pair.get("type"),
                "pair": pair.get("display"),
                "threshold": _threshold,
                "style": _style,
                "runtime_only_metric": False,
            },
        )
    except Exception as _ssi_err:
        log.debug(f"[SSI] Engine A sample skipped: {_ssi_err}")

    if (
        CONFIG.get("ENGINE_A_DIVERGENCE_MONITOR_ENABLED", True)
        and float(res.get("score", 0) or 0) >= float(_threshold or 0)
    ):
        try:
            from divergence_monitor import check_divergence

            check_divergence(
                _pair_ctx,
                {
                    "score": float(res.get("score", 0) or 0.0),
                    "direction": direction,
                    "regime": res.get("regime"),
                },
                _cf_d1c,
                _cf_h4c,
                _cf_h1c,
                funding_rate=_funding_rate,
                btc_bias=btc_bias,
                oi_data=_oi_data,
                oi_context=_oi_context,
                macro_context=_usd_relative_strength,
                intermarket_context=_intermarket_raw_context,
                style=_style,
                volume_threshold=pair_profile.get(
                    "volume_threshold", CONFIG["VOLUME_THRESHOLD"]
                ),
                regime_context=regime_context,
                volume_ratio=vr,
            )
        except Exception as _div_err:
            log.debug(
                "[DIVERGENCE] %s replay skipped: %s",
                pair.get("display"),
                _div_err,
            )

    signal = {
        "pair": pair["display"],
        "display": pair["display"],
        "symbol": pair["symbol"],
        "type": pair["type"],
        "scoreGroup": _score_group,
        "direction": direction,
        "confluenceScore": round(res["score"], 4),
        "confluencePct": _confluence_pct,
        "scoreNorm": round(score_norm, 4),
        "votes": res["votes"],
        "maxScore": max_score,
        "liveThreshold": _threshold,
        "rawScorePct": round((float(res["score"]) / float(max_score) * 100) if max_score else 0, 2),
        "thresholdProgressPct": _confluence_pct,
        "engine_b": structure_data if structure_data else {},
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
        # time - vendor bar timestamps can lag or parse oddly vs wall clock at scan time.
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
        "confidenceDetail": res.get("confidenceDetail", {}),
        "newsSentimentVote": res.get("newsSentimentVote"),
        "newsSentimentDelta": res.get("newsSentimentDelta"),
        "newsSentimentSummary": res.get("newsSentimentSummary"),
        "usdRelativeStrength": _usd_relative_strength,
        "intermarketConfirmation": res.get("intermarketConfirmation", {}),
        "pairProfile": pair_profile,
        "style": _style,
        "isForming": is_forming,
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
            for c in h4[-240:]
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
            pair_type=_level_atr_class,
        ),
        "engine_b_overlay": _engine_b_overlay_meta,
    }
    _candle_fetch_meta = {
        "D1": preloaded_fetch_meta.get("D1")
        or _get_candle_fetch_meta(pair, "D1", _lim["D1"]),
        "H4": preloaded_fetch_meta.get("H4")
        or _get_candle_fetch_meta(pair, "H4", _lim["H4"]),
        "H1": preloaded_fetch_meta.get("H1")
        or _get_candle_fetch_meta(pair, "H1", _lim["H1"]),
        "pairSource": pair.get("source"),
    }
    signal["candleFetchMeta"] = _candle_fetch_meta

    try:
        from ai_context import build_ai_calibration_context
        signal["aiContext"] = build_ai_calibration_context(signal, engine_source="engine_a")
    except Exception as _ai_ctx_err:
        log.debug("[ANALYZE] aiContext generation failed: %s", _ai_ctx_err)


    # Populate candleConsistency for policy-aware freshness evaluation
    try:
        from athena_app.services.data_freshness import check_live_candle_consistency
        from athena_app.services.market_state import split_market_state, market_state_offset_hours

        time_now = datetime.now(timezone.utc).timestamp()
        for tf_u in ("H4", "H1", "D1"):
            state = preloaded_market_state.get(tf_u) if preloaded_market_state else None
            if not state:
                tf_candles_for_state = {"H4": h4, "H1": h1, "D1": d1}.get(tf_u, [])
                state = split_market_state(
                    list(tf_candles_for_state or []),
                    tf_u,
                    pair.get("display") or pair.get("symbol") or "",
                    time_now=time_now,
                    offset_hours=market_state_offset_hours(pair, tf_u),
                )
            # Build engine input paths for consistency check
            if pair.get("source") == "mt5" and pair.get("type") == "forex":
                engine_a_input = list(state.get("confirmed") or [])
            else:
                engine_a_input = list(state.get("confirmed") or [])
                if state.get("forming"):
                    engine_a_input.append(state["forming"])
            engine_b_input = list(state.get("confirmed") or [])
            scanner_input = list(engine_a_input)

            # Get raw candles for this timeframe
            tf_candles = {"H4": h4, "H1": h1, "D1": d1}.get(tf_u, [])

            consistency_paths = {
                "raw_provider": tf_candles or [],
                "market_state": state,
                "engine_a": engine_a_input,
                "engine_b": engine_b_input,
                "scanner": scanner_input,
                "compare": scanner_input,
            }
            cache_meta = _candle_fetch_meta.get(tf_u)
            if isinstance(cache_meta, dict) and cache_meta:
                consistency_paths["cache"] = cache_meta

            consistency_result = check_live_candle_consistency(
                pair,
                tf_u,
                consistency_paths,
                time_now=time_now,
            )
            if consistency_result:
                signal.setdefault("candleConsistency", {})[tf_u] = consistency_result
    except Exception as _consistency_err:
        log.debug(f"[ANALYZE] {pair.get('display')} candle consistency check unavailable: {_consistency_err}")

    try:
        from athena_app.services.data_freshness import evaluate_execution_data_freshness

        _freshness_eval = evaluate_execution_data_freshness(signal, CONFIG)
        signal["dataFreshness"] = _freshness_eval

        # Check if any timeframe has CONFIRMED_ONLY_OK status (intentional policy lag)
        # If so, do not warn about stale_1_bucket as it's expected behavior
        consistency = signal.get("candleConsistency", {})
        has_confirmed_only_ok = any(
            (isinstance(d, dict) and d.get("status") == "CONFIRMED_ONLY_OK") or d == "CONFIRMED_ONLY_OK"
            for d in consistency.values()
        ) if isinstance(consistency, dict) else False

        if _freshness_eval.get("warnOnStaleScan") and _freshness_eval.get("blocked"):
            # Filter out stale_1_bucket warnings when CONFIRMED_ONLY_OK is present
            blocked_items = _freshness_eval.get("blocked", [])
            filtered_blocked = [
                b for b in blocked_items
                if not (has_confirmed_only_ok and b.get("severity") == "stale_1_bucket")
            ]
            if filtered_blocked:
                _warn = "DATA_FRESHNESS: " + "; ".join(
                    f"{b.get('timeframe')} {b.get('severity')}"
                    for b in filtered_blocked
                )
                signal.setdefault("warnings", [])
                if _warn not in signal["warnings"]:
                    signal["warnings"].append(_warn)
    except Exception as _freshness_err:
        log.debug(
            "[ANALYZE] %s data freshness evaluation unavailable: %s",
            pair.get("display", "?"),
            _freshness_err,
        )
    _fe_final = signal.get("dataFreshness")
    if (
        CONFIG.get("SIGNAL_EXECUTABLE_FALSE_WHEN_FRESHNESS_BLOCKS", True)
        and isinstance(_fe_final, dict)
        and not _fe_final.get("allowed")
    ):
        signal["executable"] = False
    if CONFIG.get("SCAN_DEBUG_CANDLE_META", False):
        signal["candleMeta"] = {
            "D1": _candle_fetch_meta.get("D1"),
            "H4": _candle_fetch_meta.get("H4"),
            "H1": _candle_fetch_meta.get("H1"),
        }
    if CONFIG.get("CANDLE_FRESHNESS_ENABLED", True):
        try:
            from athena_app.services.market_state import candle_freshness_diagnostic

            def _series_for_candle_diag(tf_key, fallback):
                state = preloaded_market_state.get(tf_key)
                if isinstance(state, dict):
                    confirmed = list(state.get("confirmed") or [])
                    forming = state.get("forming")
                    return confirmed + ([forming] if forming else [])
                return list(fallback or [])

            signal["candleFreshness"] = {
                "D1": candle_freshness_diagnostic(
                    pair,
                    "D1",
                    _series_for_candle_diag("D1", d1),
                    source=pair.get("source"),
                ),
                "H4": candle_freshness_diagnostic(
                    pair,
                    "H4",
                    _series_for_candle_diag("H4", h4),
                    source=pair.get("source"),
                ),
                "H1": candle_freshness_diagnostic(
                    pair,
                    "H1",
                    _series_for_candle_diag("H1", h1),
                    source=pair.get("source"),
                ),
            }
        except Exception as _diag_err:
            log.debug(
                "[ANALYZE] %s candle freshness diagnostic unavailable: %s",
                pair.get("display", "?"),
                _diag_err,
            )
    return signal


# _build_event_risk imported from scoring.py


# _classify_signal imported from scoring.py - see that module for implementation


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


# ── Task 1: Trade Outcome Monitor ──────────────────────────────────────────


def _resolve_exit_reason(exit_price: float, sl: float | None, tp: float | None) -> str:
    """Classify exit as SL_HIT, TP_HIT, or MANUAL_OPERATOR_CLOSE based on price proximity."""

    if exit_price is None:
        return "MANUAL_OPERATOR_CLOSE"

    tol_pct = 0.002  # 0.2% tolerance

    if sl and abs(exit_price - sl) / max(abs(sl), 1e-9) <= tol_pct:
        return "SL_HIT"

    if tp and abs(exit_price - tp) / max(abs(tp), 1e-9) <= tol_pct:
        return "TP_HIT"

    return "MANUAL_OPERATOR_CLOSE"


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
    asset_type: str = "",
) -> None:
    """Write outcome columns for a closed trade in audit_log."""

    # Check if exit_reason was already set (e.g., TIMED_CLOSE) before resolving
    existing_exit_reason = None
    _ssi_row = None
    try:
        with sqlite3.connect(_AUDIT_DB, timeout=15.0) as _ssi_con:
            _ssi_con.row_factory = sqlite3.Row
            _ssi_row = _ssi_con.execute(
                "SELECT exit_reason, style, engine, score, max_score FROM audit_log WHERE ticket=? ORDER BY id DESC LIMIT 1",
                (ticket,),
            ).fetchone()
            if _ssi_row:
                existing_exit_reason = _ssi_row["exit_reason"]
    except Exception as _ssi_lookup_err:
        log.debug(f"[SSI] audit lookup failed for {ticket}: {_ssi_lookup_err}")

    # Preserve TIMED_CLOSE even if price matches SL/TP (after BE move)
    if str(existing_exit_reason or "").upper() == "TIMED_CLOSE":
        exit_reason = "TIMED_CLOSE"
    elif (
        pnl is not None
        and abs(float(pnl or 0.0)) < 1e-8
        and entry_price is not None
        and exit_price is not None
        and abs(float(exit_price) - float(entry_price)) < 1e-8
    ):
        exit_reason = "BREAKEVEN"
    else:
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

        elif (
            entry_price and sl and volume and abs(entry_price - sl) > 0
            and str(asset_type or "").lower() in ("crypto", "stock")
        ):
            # Fallback only valid when volume is in base units (crypto USDT-notional,
            # stock shares). Forex/commodity/index use lots/contracts, so
            # risk_dist * volume is not dollar risk - skip and leave r_multiple None.

            risk_dist = abs(entry_price - sl)

            r_multiple = round(pnl / (risk_dist * volume), 2)

        # Sanity clamp: |R| > 50 indicates corrupt risk_amount units (e.g. price
        # distance stored in $-field). Legacy forex rows produced R=-44554 on a
        # $106 loss. Discard rather than pollute downstream aggregates.
        if r_multiple is not None and abs(r_multiple) > 50:
            log.warning(
                f"[MONITOR] Discarding implausible R={r_multiple} for ticket={ticket} "
                f"pnl={pnl} risk_amount={risk_amount} entry={entry_price} sl={sl} volume={volume}"
            )
            r_multiple = None

    # Holding period

    holding_hours = None

    if entry_ts:
        try:
            t_entry = datetime.fromisoformat(entry_ts.replace("Z", "+00:00"))

            # Fix timezone bug: MT5 deal timestamps are in broker time (UTC+3 for Pepperstone)
            # but datetime.fromtimestamp treats them as UTC. Subtract 3 hours to get true UTC.
            if exit_time and exit_time.isdigit():
                # exit_time is a timestamp string from MT5 deal
                broker_timestamp = int(exit_time)
                utc_timestamp = broker_timestamp - 3 * 3600  # Subtract 3 hours for UTC+3 broker
                t_exit = datetime.fromtimestamp(utc_timestamp, tz=timezone.utc)
            else:
                # ISO format (e.g., from timed exit)
                t_exit = datetime.fromisoformat(exit_time.replace("Z", "+00:00"))

            holding_hours = round((t_exit - t_entry).total_seconds() / 3600, 2)

        except Exception as _hp_err:
            log.debug(f"[AUDIT] holding period calc failed: {_hp_err}")

    # existing_exit_reason already checked at top of function

    _audit_outcome_updated = False
    try:
        with sqlite3.connect(_AUDIT_DB, timeout=15.0) as con:
            _audit_update_cur = con.execute(
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
            _audit_outcome_updated = (_audit_update_cur.rowcount or 0) > 0 or con.total_changes > 0

        log.info(
            f"[MONITOR] Outcome logged: ticket={ticket} exit={exit_price} pnl={pnl} R={r_multiple} reason={exit_reason}"
        )

        if _audit_outcome_updated and r_multiple is not None and _ssi_row:
            try:
                _row_engine = str(_ssi_row["engine"] or "").strip().lower()
                _row_style = str(_ssi_row["style"] or "").strip().lower()
                if _row_engine in ("scalp", "engine d", "scalp_vp") or _row_style == "scalp":
                    from scalp_engine import record_scalp_trade_outcome

                    record_scalp_trade_outcome(r_multiple)
            except Exception as _scalp_outcome_err:
                log.debug(f"[SCALP] session outcome record failed for ticket={ticket}: {_scalp_outcome_err}")

        # AI self-learning: extract outcome into learning_log

        if CONFIG.get("LEARNING_ENABLED", True):
            try:
                from ai_learning import extract_learning_from_trade

                extract_learning_from_trade(_AUDIT_DB, ticket, is_demo=_test_mode)

            except Exception as _le:
                log.debug(f"[LEARN] extraction failed for {ticket}: {_le}")

        # Feed realized P&L to daily loss tracker (per-account: crypto→Bybit, rest→MT5)

        if pnl is not None:
            try:
                from risk_engine import record_daily_pnl

                _bal = 0.0

                if asset_type == "crypto":
                    try:
                        import bybit_executor as _bm

                        _bex = _bm._get_exchange()

                        if _bex:
                            _bb = _bex.fetch_balance()

                            _bal = float(_bb.get("total", {}).get("USDT", 0))

                    except Exception as _bbe:
                        log.debug(f"[DAILY-PNL] Bybit balance fetch: {_bbe}")

                else:
                    try:
                        from mt5_executor import mt5_get_account

                        _acc = mt5_get_account()

                        if _acc:
                            _bal = _acc.get("balance", 0)

                    except Exception as _mt5e:
                        log.debug(f"[DAILY-PNL] MT5 balance fetch: {_mt5e}")

                if _bal > 0:
                    record_daily_pnl(pnl, _bal, asset_type or "unknown")

            except Exception as _dpnl_err:
                log.debug(f"[DAILY-PNL] record failed: {_dpnl_err}")

        try:
            _ssi_engine = _stability_engine_from_audit_row(
                _ssi_row["style"] if _ssi_row else None,
                _ssi_row["max_score"] if _ssi_row else None,
                _ssi_row["engine"] if _ssi_row else None,
            )
            if _ssi_engine:
                record_outcome_event(
                    engine=_ssi_engine,
                    won=(pnl is not None and pnl > 0),
                    expectancy_r=r_multiple,
                    score=_ssi_row["score"] if _ssi_row else None,
                    max_score=_ssi_row["max_score"] if _ssi_row else None,
                    db_path=_AUDIT_DB,
                    meta={
                        "ticket": ticket,
                        "runtime_only_metric": True,
                        "exit_reason": exit_reason,
                    },
                )
        except Exception as _ssi_err:
            log.debug(f"[SSI] outcome sample skipped for {ticket}: {_ssi_err}")

    except Exception as e:
        log.warning(f"[MONITOR] DB write failed for ticket {ticket}: {e}")


_score_decay_counter = 0
_score_decay_lock = threading.Lock()
_score_decay_last_run = 0.0
_SCORE_DECAY_MIN_INTERVAL = 240.0  # 4 min - prevent back-to-back runs


def _outcome_monitor_loop() -> None:
    """Background loop: every 60s reconcile closed MT5/CCXT trades against audit_log."""

    global _score_decay_counter, _score_decay_last_run

    while True:
        try:
            time.sleep(10)

            _check_mt5_outcomes()

            _check_ccxt_outcomes()

            # Score decay check every 5 minutes (not every 60s - too expensive)

            _score_decay_counter += 1

            if _score_decay_counter >= 5:
                _score_decay_counter = 0

                if time.time() - _score_decay_last_run >= _SCORE_DECAY_MIN_INTERVAL:
                    if _score_decay_lock.acquire(blocking=False):
                        try:
                            _check_score_decay()
                        finally:
                            _score_decay_last_run = time.time()
                            _score_decay_lock.release()

        except Exception as e:
            log.debug(f"[MONITOR] loop error: {e}")


def _mt5_deals_for_audit_ticket(_mt5_lib, ticket_int: int):
    """All history deals for a closed row. Prefer position= (stable across partials/closes).

    Stored ticket may be position id (preferred) or legacy opening *order* id - both are handled.
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
        from mt5_executor import (
            mt5_get_positions,
            mt5_connect,
            mt5_close_position,
            mt5_move_sl_to_breakeven,
        )

        import MetaTrader5 as _mt5_lib

        if not mt5_connect():
            return

        _pos_resp = mt5_get_positions()

        if isinstance(_pos_resp, dict) and _pos_resp.get("error"):
            return

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
                "SELECT id, ticket, pair, direction, entry_price, sl, tp, tp_partial, volume, ts, risk_amount, asset_class, engine, style FROM audit_log "
                "WHERE ticket IS NOT NULL AND ticket != '' AND exit_price IS NULL"
            ).fetchall()

        for row in pending:
            ticket_str = str(row["ticket"])

            if ticket_str in open_tickets:
                # Engine D milestone management is opt-in. Default Scalp Lab live
                # protection is broker TP1 + SL only.
                row_engine = str(row["engine"] or "").strip().lower()
                is_scalp_row = row_engine in ("scalp", "engine d", "scalp_vp")
                scalp_milestones_enabled = bool(
                    (CONFIG.get("SCALP_ENGINE") or {}).get(
                        "LIVE_MILESTONE_MANAGEMENT", False
                    )
                )
                if is_scalp_row and scalp_milestones_enabled:
                    try:
                        pos = next((p for p in _pos_list if str(p.get("ticket")) == ticket_str), None)
                        if pos:
                            entry = float(pos.get("entry") or row["entry_price"] or 0)
                            sl = float(row["sl"] or 0)
                            tp1 = float(row["tp"] or 0)
                            direction = str(row["direction"] or pos.get("direction") or "").upper()
                            vol = float(pos.get("volume") or row["volume"] or 0)
                            sl_dist = abs(entry - sl)
                            if entry > 0 and sl_dist > 0 and vol > 0 and direction in ("LONG", "SHORT"):
                                tick = _mt5_lib.symbol_info_tick(pos.get("symbol", ""))
                                bid = float(getattr(tick, "bid", 0) or 0)
                                ask = float(getattr(tick, "ask", 0) or 0)
                                cur_px = ((bid + ask) / 2.0) if (bid > 0 and ask > 0) else (bid or ask or 0.0)
                                if cur_px > 0:
                                    current_r = (cur_px - entry) / sl_dist if direction == "LONG" else (entry - cur_px) / sl_dist

                                    # Step 1: +1R -> close 50% partial + move SL to tp_partial
                                    if current_r >= 1.0 and ticket_str not in _scalp_partial_applied:
                                        partial_vol = max(0.0, vol * 0.5)
                                        close_res = mt5_close_position(int(ticket_str), volume=partial_vol)
                                        if close_res.get("success"):
                                            _scalp_partial_applied.add(ticket_str)
                                            log.info(
                                                f"[MONITOR] MT5 {pos.get('symbol')}: +1R reached - closed 50% ticket={ticket_str}"
                                            )
                                            _sl_after = str(
                                                (CONFIG.get("SCALP_ENGINE") or {}).get("SL_AFTER_PARTIAL", "tp_partial")
                                            ).lower()
                                            if _sl_after == "tp_partial":
                                                _tp_partial_raw = float(row.get("tp_partial") or 0) if row.get("tp_partial") else 0.0
                                                if _tp_partial_raw <= 0:
                                                    _tp_partial_raw = (entry + sl_dist) if direction == "LONG" else (entry - sl_dist)
                                                _valid = (
                                                    (_tp_partial_raw > entry and _tp_partial_raw < cur_px)
                                                    if direction == "LONG"
                                                    else (_tp_partial_raw < entry and _tp_partial_raw > cur_px)
                                                )
                                                if _valid:
                                                    be_res = mt5_move_sl_to_breakeven(int(ticket_str), _tp_partial_raw)
                                                    if be_res.get("success"):
                                                        _scalp_be_applied.add(ticket_str)
                                                        _breakeven_applied.add(ticket_str)
                                                        log.info(
                                                            f"[MONITOR] MT5 {pos.get('symbol')}: SL moved to tp_partial={_tp_partial_raw:.5f} ticket={ticket_str}"
                                                        )
                                            elif _sl_after == "breakeven":
                                                be_res = mt5_move_sl_to_breakeven(int(ticket_str), entry)
                                                if be_res.get("success"):
                                                    _scalp_be_applied.add(ticket_str)
                                                    _breakeven_applied.add(ticket_str)
                                                    log.info(
                                                        f"[MONITOR] MT5 {pos.get('symbol')}: SL moved to BE after partial ticket={ticket_str}"
                                                    )

                                    # Step 2: fallback - if tp1 hit and SL not yet moved, move to BE
                                    if tp1 > 0 and ticket_str not in _scalp_be_applied:
                                        tp1_hit = (cur_px >= tp1) if direction == "LONG" else (cur_px <= tp1)
                                        if tp1_hit:
                                            be_res = mt5_move_sl_to_breakeven(int(ticket_str), entry)
                                            if be_res.get("success"):
                                                _scalp_be_applied.add(ticket_str)
                                                _breakeven_applied.add(ticket_str)
                                                log.info(
                                                    f"[MONITOR] MT5 {pos.get('symbol')}: TP1 reached - SL moved to BE ticket={ticket_str}"
                                                )
                    except Exception as me:
                        log.debug(f"[MONITOR] MT5 scalp management check failed for {ticket_str}: {me}")
                continue  # still open

            # Position closed - look up deal history

            try:
                ticket_int = int(ticket_str)

                deals = _mt5_deals_for_audit_ticket(_mt5_lib, ticket_int)

                if deals:
                    deals_sorted = sorted(deals, key=lambda d: d.time)
                    close_deal = deals_sorted[-1]
                    total_profit = sum(float(d.profit) for d in deals_sorted)

                    # Pepperstone can report a zero-PnL SL close at the breakeven entry
                    # after Athena moves SL to BE. That is a valid close; do not leave
                    # the audit row open just because deal.price equals entry.
                    entry_price = float(row["entry_price"])
                    deal_price = float(close_deal.price)

                    if abs(deal_price - entry_price) < 1e-8 and abs(total_profit) < 1e-8:
                        log.warning(
                            f"[AUDIT] Breakeven close detected for {row['pair']} ticket={ticket_str} "
                            f"deal_price={deal_price} equals entry_price; updating audit outcome"
                        )

                    # Fix timezone: MT5 deal.time is broker time (UTC+3), convert to UTC
                    broker_timestamp = int(close_deal.time)
                    utc_timestamp = broker_timestamp - 3 * 3600  # Pepperstone UTC+3
                    exit_time_utc = datetime.fromtimestamp(utc_timestamp, tz=timezone.utc).isoformat()

                    _update_trade_outcome(
                        ticket=ticket_str,
                        exit_price=float(close_deal.price),
                        exit_time=exit_time_utc,
                        pnl=total_profit,
                        entry_price=row["entry_price"],
                        sl=row["sl"],
                        tp=row["tp"],
                        volume=row["volume"],
                        entry_ts=row["ts"],
                        risk_amount=float(row["risk_amount"])
                        if row["risk_amount"]
                        else None,
                        asset_type=row["asset_class"] or "",
                    )

            except Exception as e:
                log.debug(
                    f"[MONITOR] MT5 deal lookup failed for ticket {ticket_str}: {e}"
                )

    except Exception as e:
        log.debug(f"[MONITOR] MT5 outcome check failed: {e}")


_breakeven_applied: set = set()  # tickets already moved to breakeven
_scalp_partial_applied: set = set()  # Engine D: +1R partial already taken
_scalp_be_applied: set = set()  # Engine D: BE moved after TP1 milestone


def _check_ccxt_outcomes() -> None:
    """Check Bybit futures for crypto positions that have been fully exited.

    Engine D live management:
      1) close 50% at +1R
      2) after TP1, move SL to breakeven on remaining runner
    """

    try:
        import bybit_executor as _bybit_mod

        exchange = _bybit_mod._get_exchange()

        if not exchange:
            return

        _pos_resp = _bybit_mod.bybit_get_positions()

        if isinstance(_pos_resp, dict) and _pos_resp.get("error"):
            return

        positions = (
            _pos_resp.get("positions", [])
            if isinstance(_pos_resp, dict)
            else (_pos_resp or [])
        )

        for pos in positions:
            try:
                ccxt_sym = pos.get("symbol", "")
                entry_px = float(pos.get("entryPrice", 0) or 0)
                cur_px = float(pos.get("markPrice", 0) or pos.get("lastPrice", 0) or 0)
                side = (pos.get("side") or "").lower()
                contracts = abs(float(pos.get("contracts", 0) or 0))

                if not entry_px or not cur_px or not contracts or not side:
                    continue

                with sqlite3.connect(_AUDIT_DB, timeout=15.0) as con:
                    con.row_factory = sqlite3.Row
                    match = con.execute(
                        "SELECT ticket, sl, tp, tp_partial, entry_price, engine, style FROM audit_log "
                        "WHERE exit_price IS NULL AND ticket IS NOT NULL AND ticket != '' "
                        "AND (pair LIKE '%USDT%') ORDER BY ts DESC LIMIT 20"
                    ).fetchall()

                for row in match:
                    audit_entry = float(row["entry_price"] or 0)
                    audit_sl = float(row["sl"] or 0)
                    audit_tp1 = float(row["tp"] or 0)
                    row_engine = str(row["engine"] or "").strip().lower()
                    is_scalp_row = row_engine in ("scalp", "engine d", "scalp_vp")
                    scalp_milestones_enabled = bool(
                        (CONFIG.get("SCALP_ENGINE") or {}).get(
                            "LIVE_MILESTONE_MANAGEMENT", False
                        )
                    )
                    if (
                        not is_scalp_row
                        or not scalp_milestones_enabled
                    ):
                        continue

                    ticket = str(row["ticket"])
                    if not audit_entry or not audit_sl:
                        continue

                    sl_dist = abs(audit_entry - audit_sl)
                    if sl_dist == 0:
                        continue

                    if abs(audit_entry - entry_px) / entry_px > 0.01:
                        continue

                    if side == "long":
                        current_r = (cur_px - entry_px) / sl_dist
                    else:
                        current_r = (entry_px - cur_px) / sl_dist

                    _direction_str = "LONG" if side == "long" else "SHORT"

                    # Step 1: +1R -> close 50% partial + move SL to tp_partial
                    if current_r >= 1.0 and ticket not in _scalp_partial_applied:
                        try:
                            close_side = "sell" if side == "long" else "buy"
                            reduce_qty = float(exchange.amount_to_precision(ccxt_sym, contracts * 0.5))
                            if reduce_qty > 0:
                                exchange.create_market_order(
                                    ccxt_sym,
                                    close_side,
                                    reduce_qty,
                                    params={"reduceOnly": True, "positionIdx": 0},
                                )
                                _scalp_partial_applied.add(ticket)
                                log.info(
                                    f"[MONITOR] {ccxt_sym}: +1R reached - closed 50% ({reduce_qty}) ticket={ticket}"
                                )
                                _sl_after = str(
                                    (CONFIG.get("SCALP_ENGINE") or {}).get("SL_AFTER_PARTIAL", "tp_partial")
                                ).lower()
                                _remaining = contracts - reduce_qty
                                if _sl_after == "tp_partial" and _remaining > 0:
                                    _tp_partial_raw = float(row.get("tp_partial") or 0) if row.get("tp_partial") else 0.0
                                    if _tp_partial_raw <= 0:
                                        _tp_partial_raw = (audit_entry + sl_dist) if side == "long" else (audit_entry - sl_dist)
                                    _valid = (
                                        (_tp_partial_raw > audit_entry and _tp_partial_raw < cur_px)
                                        if side == "long"
                                        else (_tp_partial_raw < audit_entry and _tp_partial_raw > cur_px)
                                    )
                                    if _valid:
                                        be_res = _bybit_mod.bybit_move_sl_to_breakeven(
                                            ccxt_sym, _direction_str, _tp_partial_raw, _remaining,
                                        )
                                        if be_res.get("success"):
                                            _scalp_be_applied.add(ticket)
                                            _breakeven_applied.add(ticket)
                                            log.info(
                                                f"[MONITOR] {ccxt_sym}: SL moved to tp_partial={_tp_partial_raw:.5f} ticket={ticket}"
                                            )
                                elif _sl_after == "breakeven" and _remaining > 0:
                                    be_res = _bybit_mod.bybit_move_sl_to_breakeven(
                                        ccxt_sym, _direction_str, entry_px, _remaining,
                                    )
                                    if be_res.get("success"):
                                        _scalp_be_applied.add(ticket)
                                        _breakeven_applied.add(ticket)
                                        log.info(
                                            f"[MONITOR] {ccxt_sym}: SL moved to BE after partial ticket={ticket}"
                                        )
                        except Exception as pe:
                            log.warning(f"[MONITOR] {ccxt_sym}: +1R partial close failed for ticket={ticket}: {pe}")

                    # Step 2: fallback - if TP1 hit and SL not yet moved, move to BE
                    if audit_tp1 > 0 and ticket not in _scalp_be_applied:
                        tp1_hit = (cur_px >= audit_tp1) if side == "long" else (cur_px <= audit_tp1)
                        if tp1_hit:
                            result = _bybit_mod.bybit_move_sl_to_breakeven(
                                ccxt_sym, _direction_str, entry_px, contracts,
                            )
                            if result.get("success"):
                                _scalp_be_applied.add(ticket)
                                _breakeven_applied.add(ticket)
                                log.info(
                                    f"[MONITOR] {ccxt_sym}: TP1 reached - SL moved to BE ticket={ticket}"
                                )

                    break

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
                            asset_type="crypto",
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


# ── Score Decay Monitor - recalculate confluence for open positions ────────

_score_decay_results: dict = {}  # pair -> {"score": float, "entryScore": float, "ts": str}
_regime_shift_results: dict = {}  # pair -> regime classification dict from regime_shift_monitor
_decay_ai_cache: dict = {}  # pair -> {"decay_at_call": float, "ts": str, "result": dict}


def _verified_open_decay_pairs() -> set[str]:
    """Return pairs currently open at broker level for decay alert filtering."""
    open_pairs: set[str] = set()

    try:
        from mt5_executor import mt5_get_positions

        mt5_res = mt5_get_positions()
        if not (isinstance(mt5_res, dict) and mt5_res.get("error")):
            for pos in (mt5_res or {}).get("positions", []) or []:
                pair = str(pos.get("pair") or "").strip()
                if pair:
                    open_pairs.add(pair)
    except Exception as exc:
        log.debug(f"[DECAY] MT5 open-position verification failed: {exc}")

    try:
        from bybit_executor import bybit_get_positions

        bybit_res = bybit_get_positions()
        if not (isinstance(bybit_res, dict) and bybit_res.get("error")):
            for pos in (bybit_res or {}).get("positions", []) or []:
                pair = str(pos.get("pair") or "").strip()
                if pair:
                    open_pairs.add(pair)
    except Exception as exc:
        log.debug(f"[DECAY] Bybit open-position verification failed: {exc}")

    return open_pairs


def _get_decay_ai_verdict(
    pair_name: str,
    direction: str,
    entry_score: float,
    cur_score: float,
    decay: float,
    direction_flip: bool,
    signal_context: dict,
) -> dict:
    """Ask the configured AI provider whether a decaying position should be EXIT / HOLD / WATCH.

    Returns {"verdict": "EXIT"|"HOLD"|"WATCH", "urgency": "HIGH"|"MEDIUM"|"LOW", "reasoning": str}
    Skips gracefully if no AI API key is configured.
    Results are cached 30 min per pair unless decay worsens by >= 0.5.
    """
    api_key = get_ai_api_key(CONFIG)
    if not api_key:
        return {}

    # Cache check - skip re-call if recent and decay hasn't worsened significantly
    cached = _decay_ai_cache.get(pair_name)
    if cached:
        try:
            age_secs = (
                datetime.now(timezone.utc)
                - datetime.fromisoformat(cached["ts"])
            ).total_seconds()
            if age_secs < 1800 and (decay - cached["decay_at_call"]) < 0.5:
                return cached["result"]
        except Exception:
            pass

    trend = signal_context.get("trendState", "UNKNOWN")
    vol_ratio = signal_context.get("volRatio", "?")
    rr = signal_context.get("rr1", "?")
    flip_note = (
        " WARNING: Current signal direction has FLIPPED vs entry direction - original thesis may be invalidated."
        if direction_flip
        else ""
    )

    prompt = (
        f"You are a risk management advisor reviewing an OPEN TRADE showing score decay.\n\n"
        f"Trade: {pair_name} | Entry Direction: {direction} | Market Regime: {trend}\n"
        f"Entry Score: {entry_score:.2f} → Current Score: {cur_score:.2f} | Decay: Δ{decay:.2f}\n"
        f"Vol Ratio: {vol_ratio} | R:R at entry: 1:{rr}\n"
        f"{flip_note}\n\n"
        f"Assess whether the trader should EXIT now, continue HOLDING, or place on WATCH (monitor closely).\n"
        f"Consider: magnitude of score drop, direction flip risk, regime context, and whether the original trade thesis remains valid.\n\n"
        f'Return ONLY valid JSON: {{"verdict":"<EXIT|HOLD|WATCH>","urgency":"<HIGH|MEDIUM|LOW>","reasoning":"<1-2 sentences max>"}}'
    )

    try:
        from ai_utils import parse_json_object

        client = create_ai_client(CONFIG, api_key=api_key)
        _model = get_ai_model(CONFIG, "DEBATE_MODEL", "grok-4.3")
        _temp = float(AITemperatureConfig.get_temperature("decay"))

        result = None
        try:
            completion = client.chat.completions.create(
                model=_model,
                max_tokens=220,
                temperature=_temp,
                messages=[
                    {
                        "role": "system",
                        "content": (
                            "You are a concise trading risk manager. "
                            "Give direct EXIT/HOLD/WATCH verdicts based on signal decay evidence. "
                            "Return strict JSON only."
                        ),
                    },
                    {"role": "user", "content": prompt},
                ],
                response_format={"type": "json_object"},
            )
            raw = completion.choices[0].message.content or ""
            result = parse_json_object(raw)
        except Exception as _ce:
            log.debug(f"[DECAY-AI] {pair_name} chat.completions failed: {_ce}")

        if result is None:
            try:
                from ai_review_logger import (
                    AI_STATE_REVIEW_INCOMPLETE,
                    REVIEW_TYPE_DECAY_AI,
                    log_ai_review,
                )

                log_ai_review(
                    symbol=pair_name,
                    asset_type=str(signal_context.get("type") or "?"),
                    review_type=REVIEW_TYPE_DECAY_AI,
                    model=_model,
                    provider=get_ai_provider_label(CONFIG),
                    prompt_version="score_decay_ai_v1",
                    input_packet=prompt,
                    has_chart_image=False,
                    candle_freshness_status=(
                        (signal_context.get("dataFreshness") or {}).get("reason")
                        or "unknown"
                    ),
                    engine_a_state=cur_score,
                    engine_b_state=None,
                    engine_c_state=None,
                    engine_d_state=None,
                    risk_state={
                        "entry_score": entry_score,
                        "current_score": cur_score,
                        "decay": decay,
                        "direction_flip": direction_flip,
                    },
                    ai_review_state=AI_STATE_REVIEW_INCOMPLETE,
                    ai_confidence=None,
                    contradictions_count=0,
                    missing_information_count=1,
                    parse_success=False,
                    schema_valid=False,
                    execution_allowed_before_ai=True,
                    execution_allowed_after_ai=True,
                    final_action="advisory",
                )
            except Exception as _log_exc:
                log.debug("[DECAY-AI] audit log failed for %s: %s", pair_name, _log_exc)
            return {}

        verdict = str(result.get("verdict", "WATCH")).upper()
        if verdict not in ("EXIT", "HOLD", "WATCH"):
            verdict = "WATCH"
        result["verdict"] = verdict
        result["urgency"] = str(result.get("urgency", "MEDIUM")).upper()

        try:
            from ai_review_logger import (
                AI_STATE_CAUTION,
                AI_STATE_CONFIRM,
                AI_STATE_REJECT,
                REVIEW_TYPE_DECAY_AI,
                log_ai_review,
            )

            _state = (
                AI_STATE_REJECT
                if verdict == "EXIT"
                else AI_STATE_CONFIRM
                if verdict == "HOLD"
                else AI_STATE_CAUTION
            )
            _required = {"verdict", "urgency", "reasoning"}
            log_ai_review(
                symbol=pair_name,
                asset_type=str(signal_context.get("type") or "?"),
                review_type=REVIEW_TYPE_DECAY_AI,
                model=_model,
                provider=get_ai_provider_label(CONFIG),
                prompt_version="score_decay_ai_v1",
                input_packet=prompt,
                has_chart_image=False,
                candle_freshness_status=(
                    (signal_context.get("dataFreshness") or {}).get("reason")
                    or "unknown"
                ),
                engine_a_state=cur_score,
                engine_b_state=None,
                engine_c_state=None,
                engine_d_state=None,
                risk_state={
                    "entry_score": entry_score,
                    "current_score": cur_score,
                    "decay": decay,
                    "direction_flip": direction_flip,
                },
                ai_review_state=_state,
                ai_confidence=None,
                contradictions_count=0,
                missing_information_count=0,
                parse_success=True,
                schema_valid=_required.issubset(result.keys()),
                execution_allowed_before_ai=True,
                execution_allowed_after_ai=True,
                final_action="advisory",
            )
        except Exception as _log_exc:
            log.debug("[DECAY-AI] audit log failed for %s: %s", pair_name, _log_exc)

        _decay_ai_cache[pair_name] = {
            "decay_at_call": decay,
            "ts": datetime.now(timezone.utc).isoformat(),
            "result": result,
        }

        log.info(
            f"[DECAY-AI] {pair_name}: verdict={verdict} urgency={result.get('urgency')} | "
            f"{result.get('reasoning', '')[:100]}"
        )
        return result

    except Exception as e:
        log.debug(f"[DECAY-AI] {pair_name} failed: {e}")
        return {}


def _refresh_trade_score(pair_obj, engine, style, direction):
    """
    Refresh score context for an open trade based on its originating engine.
    Returns (score, max_possible, pct, result_dict)
    """
    _engine = str(engine or "engine_a").lower()
    _style = str(style or "swing").lower()
    _direction = str(direction or "LONG").upper()

    # Engine B (Naked Market Structure) re-evaluation path
    if _engine == "engine_b":
        try:
            # Construct minimal signal context for Engine B
            sig = {
                "symbol": pair_obj.get("symbol"),
                "display": pair_obj.get("display"),
                "direction": _direction,
                "style": _style,
                "price": None # Let engine fetch latest
            }
            # force_ai=False to avoid expensive LLM calls during monitor loop
            res, _, err = _compute_naked_analysis(sig, force_ai=False)
            if res and not err:
                return (
                    res.get("score", 0),
                    res.get("max_possible", 1.0),
                    res.get("pct", 0.0),
                    res
                )
        except Exception as e:
            log.debug(f"[DECAY-REFRESH] {pair_obj.get('display')} engine_b refresh failed: {e}")

    # Default/Engine A (Confluence) re-evaluation path
    try:
        btc_bias = "neutral"
        if pair_obj.get("type") == "crypto":
            btc_bias = _current_btc_bias()

        res = analyze_pair(pair_obj, btc_bias, style=_style)
        if res:
            cur_score = res.get("confluenceScore", 0)
            max_score = res.get("maxScore", 3.0)
            pct = (cur_score / max_score * 100) if max_score > 0 else 0
            return (cur_score, max_score, pct, res)
    except Exception as e:
        log.debug(f"[DECAY-REFRESH] {pair_obj.get('display')} engine_a refresh failed: {e}")

    return 0.0, 1.0, 0.0, None


def _check_score_decay() -> None:
    """Re-evaluate confluence for open positions; log warnings if score has decayed."""

    try:
        live_open_pairs = _verified_open_decay_pairs()
        for k in [p for p in _score_decay_results if p not in live_open_pairs]:
            del _score_decay_results[k]

        if not live_open_pairs:
            return

        with sqlite3.connect(_AUDIT_DB, timeout=15.0) as con:
            con.row_factory = sqlite3.Row
            # engine_b trades context: engine, max_score, score_pct
            open_trades = con.execute(
                "SELECT pair, score, direction, ticket, engine, max_score, score_pct, style FROM audit_log "
                "WHERE exit_price IS NULL AND ticket IS NOT NULL AND ticket != '' "
                "AND (error_tag IS NULL OR error_tag = '')"
            ).fetchall()

        # Remove closed trades from decay results before early-exit check
        open_pairs = {row["pair"] for row in open_trades if row["pair"] in live_open_pairs}
        stale_keys = [k for k in _score_decay_results if k not in open_pairs]
        for k in stale_keys:
            del _score_decay_results[k]

        if not open_trades:
            return

        all_pairs = {p["display"]: p for p in ALL_PAIRS}

        for row in open_trades:
            pair_name = row["pair"]
            if pair_name not in live_open_pairs:
                continue

            entry_score = row["score"] or 0
            entry_max = row["max_score"]
            entry_pct = row["score_pct"]
            engine = row["engine"] or "engine_a"
            style = row["style"] or "swing"

            pair = all_pairs.get(pair_name)
            if not pair:
                continue

            try:
                cur_score, cur_max, cur_pct, result = _refresh_trade_score(
                    pair, engine, style, row["direction"]
                )

                if not result:
                    continue

                # Decay calculation is engine-specific
                if engine == "engine_b" and entry_pct is not None:
                    # Engine B uses percentage-of-max (e.g. 85% -> 40%)
                    decay = entry_pct - cur_pct
                    decay_pct = (decay / entry_pct * 100) if entry_pct > 0 else 0
                    score_note = f"pct {entry_pct:.0f}% → {cur_pct:.0f}%"
                else:
                    # Engine A / Legacy uses raw score scale (e.g. 2.15 -> 0.80)
                    decay = entry_score - cur_score
                    decay_pct = (decay / entry_score * 100) if entry_score > 0 else 0
                    score_note = f"score {entry_score:.2f} → {cur_score:.2f}"

                direction_flip = bool(
                    row["direction"] and result.get("direction")
                    and row["direction"] != result.get("direction")
                )

                _score_decay_results[pair_name] = {
                    "engine": engine,
                    "currentScore": cur_score,
                    "entryScore": entry_score,
                    "currentPct": cur_pct,
                    "entryPct": entry_pct,
                    "decay": round(decay, 4),
                    "decayPct": round(decay_pct, 1),
                    "direction": row["direction"],
                    "currentDirection": result.get("direction"),
                    "directionFlip": direction_flip,
                    "scoreNote": score_note,
                    "ts": datetime.now(timezone.utc).isoformat(),
                }

                # Regime shift classifier (advisory only - never closes trades)
                try:
                    def _fetch_for_classifier(_tf, _lim, _pair=pair):
                        try:
                            return fetch_candles(_pair, _tf, _lim)
                        except Exception as _fe:
                            log.debug(f"[REGIME-SHIFT] fetch wrapper error: {_fe}")
                            return None

                    _regime_result = _regime_classify(
                        pair_display=pair_name,
                        direction=row["direction"] or "LONG",
                        engine=engine,
                        style=style,
                        fetch_candles_fn=_fetch_for_classifier,
                        config=CONFIG,
                    )
                    _regime_result["ts"] = datetime.now(timezone.utc).isoformat()
                    _regime_shift_results[pair_name] = _regime_result
                except Exception as _rse:
                    log.debug(f"[REGIME-SHIFT] {pair_name} classify failed: {_rse}")

                # Warn at 40%+ drop from entry score (works for both forex 0-2 and factor 0-3 scales)
                if decay_pct >= 40 or direction_flip:
                    _msg = f"[DECAY] {pair_name} ({engine}): {score_note} ({decay_pct:.0f}% drop) - consider exit"
                    if direction_flip:
                        _msg += f" | DIRECTION FLIP: {row['direction']} → {result.get('direction')}"

                    # Diagnostic: Check for major component shifts (Engine A)
                    if engine == "engine_a" and result and "components" in result:
                        comps = result["components"]
                        _dr = []
                        if comps.get("trend_gate") is False:
                            _dr.append(f"trend_gate_blocked (adx={comps.get('trend_gate_adx')})")
                        if comps.get("regime_blocked") is True:
                            _dr.append(f"regime_blocked ({comps.get('regime_label')})")
                        if _dr:
                            _msg += f" | VETO: {', '.join(_dr)}"

                    log.warning(_msg)
                    try:
                        pass  # decay telegram notifications disabled
                    except Exception:
                        pass
                elif decay_pct >= 25:
                    log.info(
                        f"[DECAY] {pair_name} ({engine}): {score_note} ({decay_pct:.0f}% drop)"
                    )

                # AI assessment - only for meaningful decay, runs in background to avoid blocking
                if decay_pct >= 25:
                    def _run_ai_verdict(_pn=pair_name, _dir=row["direction"] or "",
                                        _es=entry_score, _cs=cur_score, _d=decay,
                                        _dpct=decay_pct, _flip=direction_flip,
                                        _engine=engine, _epct=entry_pct, _cpct=cur_pct, _ctx=result):
                        ai_v = _get_decay_ai_verdict(_pn, _dir, _es, _cs, _d, _flip, _ctx)
                        if ai_v:
                            _score_decay_results.get(_pn, {}).update({
                                "aiVerdict": ai_v.get("verdict"),
                                "aiUrgency": ai_v.get("urgency"),
                                "aiReasoning": ai_v.get("reasoning"),
                                "aiTs": datetime.now(timezone.utc).isoformat(),
                            })
                            if _dpct >= 40:
                                try:
                                    pass  # decay telegram notifications disabled
                                except Exception:
                                    pass
                    threading.Thread(target=_run_ai_verdict, daemon=True).start()

            except Exception as e:
                log.debug(f"[DECAY] {pair_name} re-eval failed: {e}")

    except Exception as e:
        log.debug(f"[DECAY] score decay check failed: {e}")


@app.route("/api/score-decay")
def api_score_decay():
    """Return score decay status for open positions, merged with regime shift classifier."""

    live_open_pairs = _verified_open_decay_pairs()
    for k in [p for p in _score_decay_results if p not in live_open_pairs]:
        del _score_decay_results[k]
    for k in [p for p in _regime_shift_results if p not in live_open_pairs]:
        del _regime_shift_results[k]

    merged = {}
    for p, d in _score_decay_results.items():
        if p not in live_open_pairs:
            continue
        row = dict(d)
        if p in _regime_shift_results:
            row["regimeShift"] = _regime_shift_results[p]
        merged[p] = row
    return jsonify(merged)


@app.route("/api/regime-shift")
def api_regime_shift():
    """Standalone endpoint for regime shift classifier results (open positions only)."""

    live_open_pairs = _verified_open_decay_pairs()
    for k in [p for p in _regime_shift_results if p not in live_open_pairs]:
        del _regime_shift_results[k]
    return jsonify({p: d for p, d in _regime_shift_results.items() if p in live_open_pairs})


# ── Task 2: Performance Dashboard Endpoint ─────────────────────────────────────────


@app.route("/api/performance")
def api_performance():
    """Return performance statistics for all completed trades."""

    try:
        def _safe_float(val):
            try:
                if val is None:
                    return None
                return float(val)
            except (TypeError, ValueError):
                return None

        def _load_json_dict(raw):
            if isinstance(raw, dict):
                return raw
            if raw in (None, ""):
                return {}
            try:
                parsed = json.loads(str(raw))
            except (TypeError, ValueError, json.JSONDecodeError):
                return {}
            return parsed if isinstance(parsed, dict) else {}

        def _infer_perf_engine(row: dict) -> str:
            engine = str(row.get("engine") or "").strip().lower()
            style = str(row.get("style") or "").strip().lower()
            grade = str(row.get("grade") or "").strip().upper()
            if engine in ("engine_a", "engine_b", "engine_c", "scalp", "external"):
                return engine
            if not engine and (style == "scalp" or grade == "SCALP"):
                return "scalp"
            if grade == "WEBHOOK":
                return "external"
            factors = _load_json_dict(row.get("factors_json"))
            scores = factors.get("scores") if isinstance(factors.get("scores"), dict) else {}
            if any(str(key).startswith("Naked_") for key in scores.keys()):
                return "engine_b"
            if scores:
                return "engine_a"
            if row.get("max_score") is not None:
                return "engine_a"
            return "unknown"

        def _sqn_from_r_values(values):
            clean = [float(v) for v in values if v is not None]
            if len(clean) < 2:
                return None
            mean = sum(clean) / len(clean)
            variance = sum((v - mean) ** 2 for v in clean) / (len(clean) - 1)
            if variance <= 0 or mean == 0:
                return 0.0
            raw = mean / (variance**0.5) * (len(clean) ** 0.5)
            return round(max(-10.0, min(10.0, raw)), 2)

        with sqlite3.connect(_AUDIT_DB, timeout=15.0) as con:
            con.row_factory = sqlite3.Row

            rows = con.execute(
                "SELECT * FROM audit_log WHERE exit_price IS NOT NULL ORDER BY id ASC"
            ).fetchall()
            # Fetch last 20 directly from DB ordered by id DESC (most recently closed first).
            # This is more reliable than Python-sorting when exit_time is absent from the schema.
            last_20_rows = con.execute(
                "SELECT * FROM audit_log WHERE exit_price IS NOT NULL ORDER BY id DESC LIMIT 20"
            ).fetchall()
            last_20 = [dict(r) for r in last_20_rows]

        all_trades = [dict(r) for r in rows]

        if not all_trades:
            _empty = jsonify(
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
            _empty.headers["Cache-Control"] = "no-store, max-age=0, must-revalidate"
            _empty.headers["Pragma"] = "no-cache"
            return _empty

        # Split: aggregate stats only use engine-tagged trades so legacy
        # "unknown" rows (no engine/style tag, often from pre-tagging era)
        # don't corrupt win rate, total R, equity curve, etc.
        # Unknown rows still appear in the per-engine breakdown table.
        trades = [t for t in all_trades if _infer_perf_engine(t) != "unknown"]
        if not trades:
            trades = all_trades  # fallback: if everything is unknown, show all

        from collections import defaultdict

        from research_metrics import (
            annualized_sharpe_from_r_multiples,
            annualized_sortino_engine_a_from_r_multiples,
            trades_per_year_from_parallel_trade_days,
        )

        _epoch = datetime(1970, 1, 1, tzinfo=timezone.utc)

        def _parse_close_dt(trade: dict):
            for key in ("exit_time", "closed_at", "close_time", "ts"):
                raw = trade.get(key)
                if raw is None or raw == "":
                    continue
                try:
                    return datetime.fromisoformat(str(raw).strip().replace("Z", "+00:00"))
                except Exception:
                    continue
            return _epoch

        def _is_breakeven_trade(t: dict) -> bool:
            reason = str(t.get("exit_reason") or "").upper()
            pnl = _safe_float(t.get("pnl"))
            r_mult = _safe_float(t.get("r_multiple"))
            return reason == "BREAKEVEN" or (
                pnl is not None
                and abs(pnl) < 1e-8
                and (r_mult is None or abs(r_mult) < 1e-8)
            )

        wins = [t for t in trades if (_safe_float(t.get("pnl")) or 0) > 0]

        breakevens = [t for t in trades if _is_breakeven_trade(t)]

        losses = [
            t
            for t in trades
            if (_safe_float(t.get("pnl")) or 0) < 0 and not _is_breakeven_trade(t)
        ]

        total = len(trades)

        win_count = len(wins)

        breakeven_count = len(breakevens)

        loss_count = len(losses)

        decisive_total = win_count + loss_count

        win_rate = round(win_count / decisive_total * 100, 1) if decisive_total else 0

        def _trade_closed_sort_key(trade: dict):
            return (_parse_close_dt(trade), int(trade.get("id") or 0))

        # Same chronological order for R series, cumulative equity, Sharpe annualiser hints, and max DD path.
        trades_series = sorted(trades, key=_trade_closed_sort_key)

        r_vals: list[float] = []
        _r_day_hints: list[str | None] = []
        for _t in trades_series:
            _rm = _safe_float(_t.get("r_multiple"))
            r_vals.append(float(_rm) if _rm is not None else 0.0)
            _cd = _parse_close_dt(_t)
            _r_day_hints.append(
                _cd.date().isoformat() if _cd > _epoch else None
            )

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

        # Sharpe / Sortino aligned with backtests: annualised on R using calendar span hints.
        _trades_per_live = trades_per_year_from_parallel_trade_days(r_vals, _r_day_hints)
        perf_sharpe = annualized_sharpe_from_r_multiples(
            r_vals, _trades_per_live, decimals=2
        )
        perf_sortino = annualized_sortino_engine_a_from_r_multiples(
            r_vals, _trades_per_live, decimals=2
        )

        perf_sqn = _sqn_from_r_values(r_vals)

        _sq_warn_floor = max(3, int(CONFIG.get("BT_LOW_SAMPLE_SQ_WARN_TRADES", 30) or 30))

        metric_interpretation_notes = [
            "Sharpe/Sortino use closed-trade R-multiples with the same annualiser as backtests "
            "(calendar span × 365 / trade count).",
            "This panel is live/paper audit aggregates only. PSR, DSR, PBO, and bootstrap assumptions appear on "
            "backtest API responses (Performance Lab / Backtest panel) after you run a pair backtest.",
            "Cumulative R chart is ordered by close time (+ id tie-break); headline Sharpe/Sortino/SQN use that "
            "same R sequence.",
        ]
        if len(r_vals) > 0 and len(r_vals) < _sq_warn_floor:
            metric_interpretation_notes.append(
                f"Fewer than {_sq_warn_floor} decisive R-multiples — treat SQN/Sharpe as indicative only "
                "(same low-sample caveat as Engine A wfSplit)."
            )

        daily_buckets: defaultdict[str, float] = defaultdict(float)
        for _t in trades:
            _pnlx = _safe_float(_t.get("pnl"))
            if _pnlx is None:
                continue
            _cd_p = _parse_close_dt(_t)
            if _cd_p <= _epoch:
                continue
            daily_buckets[_cd_p.date().isoformat()] += float(_pnlx)
        daily_pnl = [{"date": d, "pnl": round(v, 2)} for d, v in sorted(daily_buckets.items())]

        # Win rate by regime (trendState)
        regime_stats: dict = defaultdict(lambda: {"w": 0, "l": 0})

        for t in trades:
            if _is_breakeven_trade(t):
                continue
            k = t.get("trend") or t.get("regime") or "UNKNOWN"

            if (_safe_float(t.get("pnl")) or 0) > 0:
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
            bt = [
                t
                for t in trades
                if lo <= (t.get("score") or 0) < hi and not _is_breakeven_trade(t)
            ]

            if bt:
                bw = sum(1 for t in bt if (_safe_float(t.get("pnl")) or 0) > 0)

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
            if _is_breakeven_trade(t):
                continue
            k = _pair_type(t.get("pair", ""))

            if (_safe_float(t.get("pnl")) or 0) > 0:
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

        # last_20 is already fetched from DB with ORDER BY id DESC LIMIT 20 above.

        # Equity curve: cumulative R list for charting (same order as Sharpe/max-DD R series)

        equity_curve = []

        cum = 0

        for t in trades_series:
            cum += t.get("r_multiple") or 0

            equity_curve.append(round(cum, 2))

        # Execution quality - adverse slippage magnitude at entry (Bybit/MT5 when captured)
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
            if _is_breakeven_trade(t):
                bucket = "BREAKEVEN"
                att_raw[bucket]["n"] += 1
                continue
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
            if (_safe_float(t.get("pnl")) or 0) > 0:
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

        engine_raw: dict = defaultdict(list)
        for t in all_trades:
            engine_raw[_infer_perf_engine(t)].append(t)

        performance_by_engine = {}
        for engine_name, subset in engine_raw.items():
            if not subset:
                continue
            engine_r_vals = [_safe_float(t.get("r_multiple")) for t in subset]
            engine_r_vals = [v for v in engine_r_vals if v is not None]
            engine_decisive = [t for t in subset if not _is_breakeven_trade(t)]
            engine_wins = sum(1 for t in engine_decisive if (_safe_float(t.get("pnl")) or 0) > 0)
            engine_slip = [abs(_safe_float(t.get("slippage_bps")) or 0.0) for t in subset if t.get("slippage_bps") is not None]
            performance_by_engine[engine_name] = {
                "trades": len(subset),
                "breakevens": sum(1 for t in subset if _is_breakeven_trade(t)),
                "win_rate_pct": round(engine_wins / len(engine_decisive) * 100, 1) if engine_decisive else 0.0,
                "avg_r": round(sum(engine_r_vals) / len(engine_r_vals), 2) if engine_r_vals else None,
                "total_r": round(sum(engine_r_vals), 2) if engine_r_vals else 0.0,
                "sqn": _sqn_from_r_values(engine_r_vals),
                "avg_slippage_bps": round(sum(engine_slip) / len(engine_slip), 2) if engine_slip else None,
            }

        _resp = jsonify(
            {
                "total_trades": total,
                "win_count": win_count,
                "loss_count": loss_count,
                "breakeven_count": breakeven_count,
                "win_rate": win_rate,
                "average_r_multiple": avg_r,
                "total_r": total_r,
                "profit_factor": profit_factor,
                "sharpe": perf_sharpe,
                "sortino": perf_sortino,
                "sqn": perf_sqn,
                "max_drawdown_pct": max_dd_pct,
                "win_rate_by_regime": win_rate_by_regime,
                "win_rate_by_score_band": win_rate_by_score_band,
                "win_rate_by_asset_type": win_rate_by_asset_type,
                "best_pair": best_pair,
                "worst_pair": worst_pair,
                "average_holding_period_hours": avg_holding,
                "equity_curve": equity_curve,
                "daily_pnl": daily_pnl,
                "metric_interpretation_notes": metric_interpretation_notes,
                "last_20_trades": last_20,
                "execution_quality": execution_quality,
                "attribution_by_source": attribution_by_source,
                "performance_by_engine": performance_by_engine,
            }
        )
        _resp.headers["Cache-Control"] = "no-store, max-age=0, must-revalidate"
        _resp.headers["Pragma"] = "no-cache"
        return _resp

    except Exception as e:
        log.error(f"api_performance error: {e}")

        return jsonify({"error": str(e)}), 500


# Microstructure live cache - populated by WS feed callbacks, keyed by symbol (e.g. "BTCUSDT")
_micro_cache: dict = {}
_ws_clients: list = []  # WS client instances for graceful shutdown

# ── Live Dashboard v1 - scalp results cache ──────────────────────────────────
# Populated by api_scalp_scan after each scan run. Keyed by display.upper().
# Used by the live dashboard snapshot endpoint (read-only).
_live_dashboard_scalp_cache: dict = {}
_live_dashboard_scalp_cache_lock = threading.Lock()
_LIVE_DASHBOARD_SCALP_TTL = 300.0  # 5 min - longer TTL so snapshot returns something even between scans




# ── Live Dashboard v1 - snapshot helpers ─────────────────────────────────────









































from types import SimpleNamespace  # noqa: E402

from athena_runtime import set_runtime  # noqa: E402
from athena_app.api.routes_audit import register_audit_routes  # noqa: E402
from athena_app.api.routes_backtest import register_backtest_history_routes  # noqa: E402
from athena_app.api.routes_broker_status import register_broker_status_routes  # noqa: E402
from athena_app.api.routes_live_dashboard import register_live_dashboard_routes  # noqa: E402
from athena_app.api.routes_lottery import register_lottery_routes  # noqa: E402
from athena_app.api.routes_market_data import register_market_data_routes  # noqa: E402
from athena_app.api.routes_status import register_status_routes  # noqa: E402
from execution import register_execution_routes  # noqa: E402

set_runtime(
    SimpleNamespace(
        CONFIG=CONFIG,
        AUDIT_DB=_AUDIT_DB,
        log=log,
        ALL_PAIRS=ALL_PAIRS,
        ACTIVE_PAIRS=ACTIVE_PAIRS,
        disabled_pairs=_disabled_pairs,
        scan_lock=_scan_lock,
        kill_switch=lambda: _kill_switch,
        test_mode=lambda: _test_mode,
        analyze_pair=analyze_pair,
        fetch_candles=fetch_candles,
        fetch_eodhd=fetch_eodhd,
        fetch_polygon=fetch_polygon,
        fetch_yfinance=fetch_yfinance,
        fetch_mt5=fetch_mt5,
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
        bybit_atr_for_levels=_bybit_atr_for_levels,
        get_pair_level_atr_class=get_pair_level_atr_class,
        calc_levels=calc_levels,
        fetch_news_context=fetch_news_context,
        fetch_yield_curve=fetch_yield_curve,
        fetch_div_split_context=fetch_div_split_context,
        fetch_upcoming_earnings_context=fetch_upcoming_earnings_context,
        fetch_eodhd_indicators=fetch_eodhd_indicators,
        JSE_PAIRS=JSE_PAIRS,
        ETF_PAIRS=ETF_PAIRS,
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
        fetch_eodhd_volume_only=_fetch_eodhd_volume_only,
    )
)
register_execution_routes(app)
register_broker_status_routes(
    app,
    SimpleNamespace(CONFIG=CONFIG),
)
register_audit_routes(
    app,
    SimpleNamespace(AUDIT_DB=_AUDIT_DB),
)
register_backtest_history_routes(
    app,
    SimpleNamespace(AUDIT_DB=_AUDIT_DB),
)
register_lottery_routes(
    app,
    SimpleNamespace(CONFIG=CONFIG, AUDIT_DB=_AUDIT_DB, log=log),
)
register_market_data_routes(
    app,
    SimpleNamespace(
        CONFIG=CONFIG,
        ALL_PAIRS=ALL_PAIRS,
        ACTIVE_PAIRS=ACTIVE_PAIRS,
        ETF_PAIRS=ETF_PAIRS,
        JSE_PAIRS=JSE_PAIRS,
        disabled_pairs=_disabled_pairs,
        live_prices=_live_prices,
        live_prices_lock=_live_prices_lock,
        fetch_yield_curve=fetch_yield_curve,
        http_requests=http_requests,
        fetch_candles=fetch_candles,
        fetch_eodhd=fetch_eodhd,
        extract_candles=_extract_candles,
        merge_forex_forming_ws=_merge_forex_forming_ws,
        resample_from_h1=_resample_from_h1,
        forex_h4_resample_offset_hours=_forex_h4_resample_offset_hours,
        eodhd_ticker_for_pair=_eodhd_ticker_for_pair,
        json_safe=_json_safe,
        log=log,
    ),
)
register_live_dashboard_routes(
    app,
    SimpleNamespace(
        CONFIG=CONFIG,
        AUDIT_DB=_AUDIT_DB,
        ALL_PAIRS=ALL_PAIRS,
        ACTIVE_PAIRS=ACTIVE_PAIRS,
        live_prices=_live_prices,
        live_prices_lock=_live_prices_lock,
        live_dashboard_scalp_cache=_live_dashboard_scalp_cache,
        live_dashboard_scalp_cache_lock=_live_dashboard_scalp_cache_lock,
        fetch_candles=fetch_candles,
        scan_candle_limits=scan_candle_limits,
        get_candle_fetch_meta=_get_candle_fetch_meta,
        get_candle_builder=get_candle_builder,
        json_safe=_json_safe,
        mt5_connection_health=_mt5_connection_health,
        engine_b_cache_get=_engine_b_cache_get,
        resolve_pair_from_signal=_resolve_pair_from_signal,
        get_min_confluence_threshold=get_min_confluence_threshold,
        last_scan_results=lambda: _last_scan_results,
        binance_ws=lambda: _binance_ws,
        kill_switch=lambda: _kill_switch,
        log=log,
    ),
)
register_status_routes(
    app,
    SimpleNamespace(
        CONFIG=CONFIG,
        AUDIT_DB=_AUDIT_DB,
        all_pairs=lambda: ALL_PAIRS,
        active_pairs=lambda: ACTIVE_PAIRS,
        kill_switch=lambda: _kill_switch,
        last_scan_results=lambda: _last_scan_results,
        mt5_connection_health=lambda: _mt5_connection_health(),
        micro_cache=lambda: _micro_cache,
        json_safe=_json_safe,
        signal_stability_index=get_signal_stability_index,
        log=log,
    ),
)

# ── Research Lab routes (optional - does not affect production logic) ─────────
try:
    from athena_research.research_lab_routes import register_research_lab_routes
    register_research_lab_routes(app)
    log.info("[startup] Research Lab routes registered")
except Exception as _rl_err:
    log.warning("[startup] Research Lab routes not loaded: %s", _rl_err)

_runtime_services_lock = threading.Lock()
_runtime_services_started = False
_binance_ws = None
_binance_candle_ws = None


def _select_live_crypto_symbols_for_ws() -> list[str]:
    """Demand-driven crypto symbol scope for runtime WS feeds (uppercase, e.g. BTCUSDT)."""
    enabled_crypto = [
        p for p in CRYPTO_PAIRS
        if p.get("enabled", True) and str(p.get("symbol") or "").strip()
    ]
    if not enabled_crypto:
        return []

    display_to_symbol = {
        str(p.get("display") or "").strip().upper(): str(p.get("symbol") or "").replace("/", "").upper()
        for p in enabled_crypto
    }
    symbol_set = set()

    # 1) Active scalp symbols from explicit SCALP_PAIRS config and resolved scalp universe.
    scalp_cfg = (CONFIG.get("SCALP_ENGINE") or {}).get("SCALP_PAIRS") or []
    for disp in scalp_cfg:
        sym = display_to_symbol.get(str(disp or "").strip().upper())
        if sym:
            symbol_set.add(sym)
    try:
        from scalp_engine import get_scalp_pairs
        for disp in get_scalp_pairs(ACTIVE_PAIRS):
            sym = display_to_symbol.get(str(disp or "").strip().upper())
            if sym:
                symbol_set.add(sym)
    except Exception as exc:
        log.debug("[WS-SCOPE] get_scalp_pairs unavailable for scope bootstrap: %s", exc)

    # If the resolved live/scalp scope already covers every enabled crypto pair,
    # avoid a signed Bybit REST call during feed bootstrap. That call is only
    # needed to add open positions outside the selected scan universe.
    if symbol_set and symbol_set >= set(display_to_symbol.values()):
        return sorted(symbol_set)

    # 2) Open crypto positions (Bybit).
    try:
        from bybit_executor import bybit_get_positions
        _pos = bybit_get_positions() or {}
        if isinstance(_pos, dict) and _pos.get("error"):
            log.warning(
                "[WS-SCOPE] Bybit positions unavailable during scope bootstrap; subscribing to all enabled crypto symbols"
            )
            return sorted(set(display_to_symbol.values()))
        for row in (_pos.get("positions") or []):
            pair = str(row.get("pair") or row.get("symbol") or "").strip().upper()
            if not pair:
                continue
            if "/" not in pair and pair.endswith("USDT"):
                symbol_set.add(pair)
            else:
                sym = display_to_symbol.get(pair)
                if sym:
                    symbol_set.add(sym)
    except Exception as exc:
        log.debug("[WS-SCOPE] bybit_get_positions unavailable for scope bootstrap: %s", exc)

    # 3) Recently executed crypto signals in-process (best-effort).
    try:
        for sig_id in list(executed_signals):
            sid = str(sig_id or "").upper()
            for disp_up, sym in display_to_symbol.items():
                if disp_up in sid or sym in sid:
                    symbol_set.add(sym)
    except Exception:
        pass

    selected = sorted(symbol_set)
    return selected


def _select_eodhd_volume_warm_pairs() -> list[dict]:
    """Select active non-crypto scalp pairs for M1/M5/M15 volume warming (Engine D)."""
    try:
        from scalp_engine import get_scalp_pairs

        scalp_names = set(get_scalp_pairs(ACTIVE_PAIRS))
    except Exception:
        scalp_names = {str(p.get("display") or p.get("symbol") or "") for p in ACTIVE_PAIRS}
    selected = []
    for pair in ACTIVE_PAIRS:
        if pair.get("type") == "crypto":
            continue
        display = str(pair.get("display") or pair.get("symbol") or "")
        if display not in scalp_names:
            continue
        if _supports_eodhd_volume_overlay(pair):
            selected.append(pair)
    return selected


def _select_engine_a_warm_pairs() -> list[dict]:
    """Select ALL active non-crypto pairs for H1/H4/D1 volume warming (Engine A + chart rendering)."""
    return [
        p for p in ACTIVE_PAIRS
        if p.get("type") != "crypto" and _supports_eodhd_volume_overlay(p)
    ]


def _start_eodhd_volume_warmer() -> None:
    """Warm EODHD volume cache off the live scan path.

    Two loops:
    - Fast loop (M1/M5/M15, Engine D scalp pairs, 60s interval)
    - Slow loop (H1/H4/D1, all Engine A non-crypto pairs, configurable slow interval)
      Uses scan_candle_limits() for H1/H4/D1 so cache keys match analyze_pair().
    """
    cfg = CONFIG.get("SCALP_ENGINE", {})
    if not cfg.get("EODHD_VOLUME_WARMER_ENABLED", True):
        log.info("[EODHD-VOL] Warmer disabled")
        return
    if not os.environ.get("EODHD_KEY", "").strip():
        log.warning("[EODHD-VOL] Warmer disabled - EODHD_KEY not set")
        return

    # ── Fast loop: scalp TFs for Engine D ────────────────────────────────
    scalp_tfs = [str(tf).upper() for tf in (cfg.get("EODHD_VOLUME_WARMER_TIMEFRAMES") or ["M1", "M5", "M15"])]
    scalp_interval = max(30, int(cfg.get("EODHD_VOLUME_WARMER_INTERVAL_SEC", 60) or 60))
    scalp_limits = {
        "M1": int(cfg.get("M1_CANDLES", 300) or 300),
        "M5": int(cfg.get("M5_CANDLES", 1000) or 1000),
        "M15": int(cfg.get("M15_CANDLES", 500) or 500),
        "H1": int(cfg.get("H1_CANDLES", 300) or 300),
    }

    def _scalp_loop():
        while True:
            pairs = _select_eodhd_volume_warm_pairs()
            warmed = 0
            skipped = 0
            for pair in pairs:
                for tf in scalp_tfs:
                    if not _is_eodhd_volume_whitelisted(pair, tf):
                        skipped += 1
                        continue
                    try:
                        resp = _fetch_eodhd_volume_only(pair, tf, scalp_limits.get(tf, 300))
                        if isinstance(resp, dict) and not resp.get("error"):
                            warmed += 1
                    except Exception as exc:
                        log.debug("[EODHD-VOL] Scalp warmer fetch failed %s %s: %s", pair.get("display"), tf, exc)
                    time.sleep(0.15)
            log.info("[EODHD-VOL] Scalp warmer cycle warmed=%s skipped=%s pairs=%s", warmed, skipped, len(pairs))
            time.sleep(scalp_interval)

    threading.Thread(target=_scalp_loop, daemon=True, name="EODHDVolumeWarmer").start()

    # ── Slow loop: Engine A TFs (H1/H4/D1) for all non-crypto pairs ──────
    if not cfg.get("EODHD_VOLUME_WARMER_ENGINE_A_ENABLED", True):
        log.info("[EODHD-VOL] Engine A slow warmer disabled")
        return

    slow_interval = max(60, int(cfg.get("EODHD_VOLUME_WARMER_SLOW_INTERVAL_SEC", 900) or 900))
    # Use the same limits as analyze_pair() so _eodhd_volume_cache keys match exactly.
    _scan_lim = scan_candle_limits()
    engine_a_limits = {
        "H1": int(_scan_lim.get("H1", 1001)),
        "H4": int(_scan_lim.get("H4", 1001)),
        "D1": int(_scan_lim.get("D1", 1001)),
    }
    engine_a_tfs = ["H1", "H4", "D1"]

    def _engine_a_loop():
        # Brief startup delay so scalp warmer and MT5 can initialise first.
        time.sleep(30)
        while True:
            pairs = _select_engine_a_warm_pairs()
            warmed = 0
            skipped = 0
            for pair in pairs:
                for tf in engine_a_tfs:
                    if not _is_eodhd_volume_whitelisted(pair, tf):
                        skipped += 1
                        continue
                    try:
                        resp = _fetch_eodhd_volume_only(pair, tf, engine_a_limits[tf])
                        if isinstance(resp, dict) and not resp.get("error"):
                            warmed += 1
                    except Exception as exc:
                        log.debug("[EODHD-VOL] Engine A warmer fetch failed %s %s: %s", pair.get("display"), tf, exc)
                    # Modest request rate - H1/H4/D1 TTLs are long so this loop runs infrequently.
                    time.sleep(0.2)
            log.info(
                "[EODHD-VOL] Engine A warmer cycle warmed=%s skipped=%s pairs=%s tfs=%s",
                warmed, skipped, len(pairs), engine_a_tfs,
            )
            time.sleep(slow_interval)

    threading.Thread(target=_engine_a_loop, daemon=True, name="EODHDVolumeWarmerEngineA").start()

    # Start the Live v2 batch poller for ws:False stocks.
    # One HTTP request per poll interval covers all non-WS tickers combined,
    # replacing ~33 per-pair REST calls per minute with 1 batch call.
    # Injects delta-volume ticks into CandleBuilder via on_tick() so M1/M5/M15/H1/H4
    # bars accumulate real exchange volume identically to ws:True stocks.
    # D1 is already covered by bulk_update_d1() - the batcher does not touch D1.
    try:
        from eodhd_volume_batch import start_live_v2_batcher_for_non_ws_stocks
        _livev2_interval = max(30, int(cfg.get("LIVEV2_POLL_INTERVAL_SEC", 60) or 60))
        start_live_v2_batcher_for_non_ws_stocks(
            ACTIVE_PAIRS,
            api_key=os.environ.get("EODHD_KEY", ""),
            poll_interval=_livev2_interval,
        )
    except Exception as _lv2_err:
        log.warning("[LIVEV2] Failed to start Live v2 batcher: %s", _lv2_err)


def ensure_runtime_services_started() -> None:
    """Start background feed/runtime services once for both script and app-factory boot."""
    global _runtime_services_started, _binance_ws, _binance_candle_ws

    with _runtime_services_lock:
        if _runtime_services_started:
            return
        _runtime_services_started = True

    from athena.crypto_ws_scope import (
        binance_micro_symbol_strings,
        enabled_binance_micro_crypto_pairs,
    )

    # Start EODHD WebSocket real-time price streaming + candle builder
    _ws_key = os.environ.get("EODHD_KEY", "")
    if _ws_key:
        _ws_mgr = EODHDWebSocketManager(_ws_key)
        _eodhd_pairs = [p for p in ACTIVE_PAIRS if p.get("source") == "eodhd"]
        _ws_mgr.start(_eodhd_pairs)

        if get_candle_builder() is None:
            set_candle_builder(CandleBuilder())

        def _cb_startup():
            # Background news work removed from startup to prevent heavy load.
            # It will now start on-demand when fetch_news_context is called.
            cb = get_candle_builder()
            if cb is None:
                return

            # Re-seed only the non-WS US stock/ETF set so CandleBuilder has enough
            # H1/H4 history to avoid per-pair intraday REST fallback on cold start.
            non_ws_stock_pairs = build_non_ws_stock_pairs(ALL_PAIRS)
            if non_ws_stock_pairs:
                cb.seed(non_ws_stock_pairs)

            cb.bulk_update_d1()  # fresh D1 from Bulk API
            cb.start_refresh_loop()  # bulk D1 every 4h
            start_live_v2_batcher_for_non_ws_stocks(ALL_PAIRS, _ws_key, poll_interval=60)

        threading.Thread(target=_cb_startup, daemon=True, name="candle-seed").start()
        log.info(
            "[CB] Candle builder started (WS ticks + 6mo seed + Bulk D1 for EODHD sources)"
        )
    else:
        log.warning("[WS] No EODHD_KEY - WebSocket prices disabled")

    _start_eodhd_volume_warmer()

    # MT5 tick poller - keeps forex/commodity/index/stock prices fresh in
    # _live_prices independent of /api/candles traffic.
    threading.Thread(
        target=_run_mt5_tick_poller, daemon=True, name="mt5-price-poll"
    ).start()

    # Start Binance Futures WebSocket for crypto live prices + kline candles
    crypto_enabled = [p for p in CRYPTO_PAIRS if p.get("enabled", True)]
    if crypto_enabled:
        demand_symbols = _select_live_crypto_symbols_for_ws()
        micro_symbols = set(binance_micro_symbol_strings(CRYPTO_PAIRS))
        selected_symbols = sorted(set(demand_symbols) | micro_symbols)
        selected_intervals = resolve_binance_kline_ws_intervals(CONFIG)
        _binance_ws = BinanceLivePriceWS()
        _binance_ws.start()
        _binance_candle_ws = BinanceCandleWS(
            symbols=selected_symbols,
            intervals=selected_intervals,
        )
        _binance_candle_ws.start()
        # REST fallback poller - runs in parallel with the !ticker@arr WS so crypto
        # prices keep flowing even if the WS is in a silent reconnect loop.
        threading.Thread(
            target=_run_binance_price_poller, daemon=True, name="binance-price-poll"
        ).start()
        log.info(
            "[BINANCE-WS] Price feed active; kline scope symbols=%s tfs=%s (enabled_crypto=%s demand_scoped=%s all_binance_micro=%s)",
            len(selected_symbols),
            selected_intervals,
            len(crypto_enabled),
            len(demand_symbols),
            len(micro_symbols),
        )
        if selected_symbols:
            log.info(
                "[BINANCE-WS] Kline symbol scope: %s",
                ", ".join(selected_symbols),
            )
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
            micro_crypto_pairs = enabled_binance_micro_crypto_pairs(CRYPTO_PAIRS)
            expected_n = sum(
                1
                for p in CRYPTO_PAIRS
                if p.get("enabled", True)
                and str(p.get("type") or "").lower() == "crypto"
                and str(p.get("source") or "").lower() == "binance"
                and str(p.get("symbol") or "").strip()
            )
            malformed = False
            for p in CRYPTO_PAIRS:
                if not p.get("enabled", True):
                    continue
                if str(p.get("type") or "").lower() != "crypto":
                    continue
                if str(p.get("source") or "").lower() != "binance":
                    continue
                if not str(p.get("symbol") or "").strip():
                    malformed = True
                    log.warning(
                        "[MICRO] enabled binance crypto pair missing symbol: display=%s",
                        p.get("display") or "?",
                    )

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

            bybit_micro_enabled = bool(CONFIG.get("MICROSTRUCTURE_BYBIT_FEEDS_ENABLED", False))

            for pair in micro_crypto_pairs:
                sym = pair["symbol"]
                cb = _make_cb(sym)
                b = BinanceWS(sym.lower())
                _ws_clients.append(b)
                threading.Thread(
                    target=_run, args=(b, cb), daemon=True, name=f"BinWS-{sym}"
                ).start()
                if bybit_micro_enabled:
                    y = BybitWS(sym.upper())
                    _ws_clients.append(y)
                    threading.Thread(
                        target=_run, args=(y, cb), daemon=True, name=f"BbtWS-{sym}"
                    ).start()
            scope_note = ""
            if len(micro_crypto_pairs) != expected_n or malformed:
                scope_note = f" enabled_binance_crypto_expected={expected_n}"
            log.info(
                "[MICRO] Started feeds (all enabled Binance crypto): binance=%s bybit=%s pairs_total=%s%s symbols=%s",
                len(micro_crypto_pairs),
                len(micro_crypto_pairs) if bybit_micro_enabled else 0,
                expected_n,
                scope_note,
                ", ".join(str(p["symbol"]).replace("/", "").upper() for p in micro_crypto_pairs)
                if micro_crypto_pairs
                else "none",
            )

        threading.Thread(
            target=_start_micro_feeds, daemon=True, name="MicroFeeds"
        ).start()
    else:
        log.info(
            "[MICRO] Microstructure feeds disabled (set MICROSTRUCTURE_FEEDS_ENABLED: true to enable)"
        )


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

    ensure_runtime_services_started()

    # Startup position reconciliation - check for open positions on restart

    def _startup_reconcile():
        """On startup, check MT5/Bybit for open positions and log them."""

        time.sleep(5)  # wait for connections to establish

        mt5_pos: list = []
        bpos: list = []

        try:
            from mt5_executor import mt5_get_positions, mt5_connect

            if mt5_connect():
                _pos_resp = mt5_get_positions()

                if isinstance(_pos_resp, dict) and _pos_resp.get("error"):
                    mt5_pos = []
                else:
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

            if isinstance(_pos_resp, dict) and _pos_resp.get("error"):
                bpos = []
            else:
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

        mt5_ticket_open = {
            str(p.get("ticket")).strip()
            for p in mt5_pos
            if p.get("ticket") is not None and str(p.get("ticket")).strip()
        }

        # Check audit_log for rows without exit_price (not the same as broker "open",
        # but flags missing outcome rows for the outcome monitor / manual cleanup).
        try:
            with sqlite3.connect(_AUDIT_DB, timeout=15.0) as con:
                con.row_factory = sqlite3.Row

                unresolved = con.execute(
                    "SELECT pair, direction, entry_price, ticket FROM audit_log "
                    "WHERE exit_price IS NULL AND ticket IS NOT NULL AND ticket != ''"
                ).fetchall()

            if unresolved:
                by_ticket: dict[str, sqlite3.Row] = {}
                for r in unresolved:
                    tix = str(r["ticket"]).strip()
                    if tix:
                        by_ticket[tix] = r
                unique_rows = list(by_ticket.values())
                orphans_mt5: list[sqlite3.Row] = []
                for r in unique_rows:
                    tix = str(r["ticket"]).strip()
                    if tix.isdigit() and tix not in mt5_ticket_open:
                        orphans_mt5.append(r)
                log.warning(
                    f"[STARTUP] {len(unique_rows)} audit row(s) without exit_price ({len(unresolved)} incl. duplicate tickets); "
                    f"MT5 ticket not in broker open set: {len(orphans_mt5)}."
                )
                for r in sorted(unique_rows, key=lambda x: str(x["pair"] or "")):
                    tix = str(r["ticket"]).strip()
                    tag = ""
                    if tix.isdigit() and tix not in mt5_ticket_open:
                        tag = " [audit-only: MT5 ticket not in open positions]"
                    elif "-" in tix and len(tix) >= 32:
                        tag = " [Bybit/paper id — verify vs outcome monitor if already flat]"
                    log.warning(
                        "  - %s %s entry=%s ticket=%s%s",
                        r["pair"],
                        r["direction"],
                        r["entry_price"],
                        tix,
                        tag,
                    )
                log.warning(
                    "[STARTUP] Dashboard open cards use /api/open-trades-timed (live brokers). "
                    "These audit rows are bookkeeping; CCXT/MT5 monitors backfill exit_price when they match."
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

    # threading.Thread(target=_duka_seed, daemon=True, name="DukaSeed").start()

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

    # Graceful shutdown handler - clean up connections on SIGINT/SIGTERM

    def _graceful_shutdown(signum, frame):

        sig_name = "SIGINT" if signum == _signal.SIGINT else "SIGTERM"

        log.warning(f"[SHUTDOWN] {sig_name} received - shutting down gracefully...")

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
        # Start the scheduler thread in standby - it does nothing until enabled
        _auto_trader._start_thread()

        log.info("[AUTO] Auto-trader standby (toggle via UI)")

    _host = os.environ.get(
        "ATHENA_HOST", "127.0.0.1"
    )  # default localhost; set to 0.0.0.0 in .env for LAN

    # Backup databases at startup - protects against data loss during updates
    try:
        from backup_db import backup_now

        backup_now(reason="startup")
        log.info("[BACKUP] Database backup completed at startup")
    except Exception as _bak_e:
        log.warning(f"[BACKUP] Startup backup failed: {_bak_e}")

    # Start Telegram bot (two-way command centre)
    # Skip if ATHENA_DISABLE_TELEGRAM environment variable is set
    import os as _os
    if not _os.environ.get("ATHENA_DISABLE_TELEGRAM"):
        try:
            from telegram_bot import start_telegram_bot
            start_telegram_bot()
        except Exception as e:
            log.warning(f"[TELEGRAM] Bot startup failed: {e}")
    else:
        log.info("[TELEGRAM] Bot disabled via ATHENA_DISABLE_TELEGRAM environment variable")

    # Clean Ctrl-C shutdown on Windows - daemon threads stop automatically
    import signal as _signal
    def _shutdown_handler(sig, frame):
        log.info("[SHUTDOWN] Ctrl-C received - stopping Sentinel Pro...")
        import os as _os
        _os._exit(0)
    _signal.signal(_signal.SIGINT, _shutdown_handler)
    _signal.signal(_signal.SIGTERM, _shutdown_handler)

    app.run(host=_host, port=5000, debug=False, use_reloader=False)
