"""
Athena Research Lab — Data Loader
----------------------------------
Fetches OHLCV data from Athena's existing data infrastructure.

Data source priority per asset class:
  crypto    → Binance Futures REST (public endpoint, no auth required)
  forex     → MT5 (if running) → EODHD REST (if EODHD_KEY is set)
  commodity → MT5 (if running) → EODHD REST (if EODHD_KEY is set)
  index     → MT5 (if running) → EODHD REST (if EODHD_KEY is set)
  stock     → EODHD REST (if EODHD_KEY is set) → MT5 (if running)

yfinance:  NEVER used unless ALLOW_YFINANCE_FALLBACK: true is explicitly set
           in configs/vectorbt_research_lab.yaml.

If a symbol cannot be fetched from any configured source, it is marked
DATA_UNAVAILABLE — it is never silently dropped or substituted.

Every loaded dataset carries full DataProvenance metadata:
  data_source, symbol, timeframe, start_time, end_time, candle_count,
  missing_candle_count, timezone, cached (bool).

Do NOT mix data sources within one comparison without recording it.
No live execution imports. No production config writes.
"""

from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd

log = logging.getLogger(__name__)

_DEFAULT_CACHE_DIR = Path("logs/research_lab/data_cache")

# ── Asset class detection ─────────────────────────────────────────────────────

_CRYPTO_SYMBOLS = {
    "BTC/USDT", "ETH/USDT", "SOL/USDT", "BNB/USDT", "ADA/USDT",
    "XRP/USDT", "DOGE/USDT", "AVAX/USDT", "LINK/USDT", "DOT/USDT",
    "POL/USDT", "MATIC/USDT", "NEAR/USDT", "APT/USDT", "ARB/USDT",
    "SUI/USDT", "INJ/USDT", "RENDER/USDT", "LTC/USDT",
    "AAVE/USDT", "ALGO/USDT", "ATOM/USDT", "BCH/USDT", "ETC/USDT",
    "TRX/USDT", "XLM/USDT", "UNI/USDT", "FIL/USDT", "ICP/USDT",
    "HBAR/USDT", "OP/USDT", "SEI/USDT",
    "PEPE/USDT", "WIF/USDT", "FET/USDT",
}
_COMMODITY_SYMBOLS = {
    "XAU/USD", "XAG/USD", "WTI Oil", "Brent Oil", "WTI/USD",
    "USOIL", "UKOIL", "Nat Gas", "Copper", "Aluminium", "Lead",
    "Nickel", "Zinc", "XPT/USD", "XPD/USD", "Gasoline",
    "Cattle", "Cocoa", "Coffee", "Corn", "Cotton", "Soybeans", "Sugar", "Wheat",
}
_INDEX_SYMBOLS = {
    "US500", "SPX", "NAS100", "NASDAQ-100", "US30", "GER40",
    "UK100", "JP225", "AUS200", "S&P 500", "HK50",
    "EURX", "JPYX", "USDX", "Dow Jones", "DAX 40", "ASX 200",
    "Nikkei 225", "Hang Seng",
}
_FOREX_PAIRS = {
    "EUR/USD", "GBP/USD", "USD/JPY", "AUD/USD", "AUD/CHF", "AUD/NZD",
    "NZD/USD", "EUR/GBP", "USD/CAD", "USD/CHF", "EUR/JPY", "GBP/JPY",
    "AUD/JPY", "EUR/AUD", "GBP/AUD", "USD/ZAR", "EUR/CHF",
    "USD/MXN", "USD/SGD", "USD/BRL", "USD/INR", "USD/TRY",
}
_STOCK_SYMBOLS = {
    "AAPL", "TSLA", "NVDA", "MSFT", "AMZN", "META", "GOOG", "JPM",
    "V", "XOM", "NFLX", "AMD", "CRM", "DIS", "BA", "COIN", "PYPL",
    "INTC", "UBER", "PLTR",
}
_ETF_SYMBOLS = {
    "SPY", "QQQ", "GLD", "TLT", "IWM", "EEM", "DIA", "GDX",
    "SOXX", "XLE", "SLV", "USO",
}


def asset_class_for(symbol: str) -> str:
    if symbol in _CRYPTO_SYMBOLS or (
        "/" in symbol and symbol.endswith("/USDT")
    ):
        return "crypto"
    if symbol in _COMMODITY_SYMBOLS:
        return "commodity"
    if symbol in _INDEX_SYMBOLS:
        return "index"
    if symbol in _FOREX_PAIRS:
        return "forex"
    if symbol in _ETF_SYMBOLS:
        return "etf"
    if symbol in _STOCK_SYMBOLS:
        return "stock"
    # Heuristic fallbacks
    if "/" in symbol and not symbol.endswith("/USDT"):
        return "forex"   # most X/Y pairs are forex or commodity
    return "stock"


# ── DataProvenance ────────────────────────────────────────────────────────────

@dataclass
class DataProvenance:
    symbol: str
    timeframe: str
    data_source: str          # "binance_rest" | "mt5" | "eodhd" | "yfinance_fallback" | "synthetic_test" | "DATA_UNAVAILABLE"
    candle_count: int
    missing_candle_count: int
    start_time: Optional[str]
    end_time: Optional[str]
    timezone: str
    cached: bool
    fetch_timestamp: str = ""
    notes: str = ""

    def to_dict(self) -> dict:
        return asdict(self)

    @staticmethod
    def unavailable(symbol: str, timeframe: str, reason: str) -> "DataProvenance":
        return DataProvenance(
            symbol=symbol, timeframe=timeframe, data_source="DATA_UNAVAILABLE",
            candle_count=0, missing_candle_count=-1, start_time=None, end_time=None,
            timezone="UTC", cached=False,
            fetch_timestamp=datetime.now(timezone.utc).isoformat(),
            notes=reason,
        )


def _compute_missing(df: pd.DataFrame, timeframe: str) -> int:
    """Estimate missing candles by comparing actual count to expected."""
    if df.empty or len(df) < 2:
        return 0
    freq_minutes = {"M1": 1, "M5": 5, "M15": 15, "H1": 60, "H4": 240, "D1": 1440, "W1": 10080}
    mins = freq_minutes.get(timeframe, 60)
    total_mins = (df.index[-1] - df.index[0]).total_seconds() / 60
    expected = int(total_mins / mins) + 1
    return max(0, expected - len(df))


def _provenance(df: pd.DataFrame, symbol: str, timeframe: str,
                source: str, cached: bool, notes: str = "") -> DataProvenance:
    return DataProvenance(
        symbol=symbol,
        timeframe=timeframe,
        data_source=source,
        candle_count=len(df),
        missing_candle_count=_compute_missing(df, timeframe),
        start_time=df.index[0].isoformat() if not df.empty else None,
        end_time=df.index[-1].isoformat() if not df.empty else None,
        timezone="UTC",
        cached=cached,
        fetch_timestamp=datetime.now(timezone.utc).isoformat(),
        notes=notes,
    )


# ── Timeframe helpers ─────────────────────────────────────────────────────────

_BINANCE_TF: dict[str, str] = {
    "M1": "1m", "M5": "5m", "M15": "15m",
    "H1": "1h", "H4": "4h", "D1": "1d", "W1": "1w",
}

_MT5_TF_STR: dict[str, int] = {
    "M1": 1, "M5": 5, "M15": 15, "H1": 60, "H4": 240, "D1": 1440,
}

_EODHD_INTRA_TF: dict[str, str] = {
    "M1": "1m", "M5": "5m", "M15": "15m", "H1": "1h",
}

_TF_TO_FREQ: dict[str, str] = {
    "M1": "1min", "M5": "5min", "M15": "15min",
    "H1": "1h", "H4": "4h", "D1": "1D", "W1": "1W",
}


def vbt_freq(timeframe: str) -> str:
    return _TF_TO_FREQ.get(timeframe, "1h")


# ── MT5 symbol mapping ────────────────────────────────────────────────────────

_MT5_SYMBOL_MAP: dict[str, str] = {
    "EUR/USD": "EURUSD", "GBP/USD": "GBPUSD", "USD/JPY": "USDJPY",
    "AUD/USD": "AUDUSD", "USD/CAD": "USDCAD", "USD/CHF": "USDCHF",
    "NZD/USD": "NZDUSD", "EUR/GBP": "EURGBP", "GBP/JPY": "GBPJPY",
    "EUR/JPY": "EURJPY", "USD/TRY": "USDTRY", "USD/ZAR": "USDZAR",
    "XAU/USD": "XAUUSD", "XAG/USD": "XAGUSD", "WTI Oil": "USOIL",
    "Brent Oil": "UKOIL", "US500": "US500", "NAS100": "NAS100",
    "S&P 500": "US500", "NASDAQ-100": "NAS100", "US30": "US30",
    "GER40": "GER40", "UK100": "UK100", "JP225": "JP225",
    # US stocks — all use plain ticker on Pepperstone (same convention as AAPL/NVDA/MSFT)
    "AAPL": "AAPL", "NVDA": "NVDA", "MSFT": "MSFT",
    "TSLA": "TSLA", "AMZN": "AMZN",
    "META": "META", "GOOG": "GOOG", "JPM": "JPM",
    "V": "V", "XOM": "XOM", "NFLX": "NFLX", "AMD": "AMD",
    "CRM": "CRM", "DIS": "DIS", "BA": "BA", "COIN": "COIN",
    "PYPL": "PYPL", "INTC": "INTC", "UBER": "UBER", "PLTR": "PLTR",
}

# ── EODHD symbol mapping ──────────────────────────────────────────────────────

_EODHD_SYMBOL_MAP: dict[str, str] = {
    "EUR/USD": "EURUSD.FOREX", "GBP/USD": "GBPUSD.FOREX",
    "USD/JPY": "USDJPY.FOREX", "AUD/USD": "AUDUSD.FOREX",
    "USD/CAD": "USDCAD.FOREX", "USD/CHF": "USDCHF.FOREX",
    "NZD/USD": "NZDUSD.FOREX", "XAU/USD": "GC.US",
    "XAG/USD": "SI.US", "WTI Oil": "CL.US", "Brent Oil": "BZ.US",
    "US500": "GSPC.INDX", "NAS100": "NDX.INDX", "US30": "DJI.INDX",
    "GER40": "GDAXI.INDX", "UK100": "FTSE.INDX",
    # US stocks — EODHD uses TICKER.US format
    "AAPL": "AAPL.US", "NVDA": "NVDA.US", "MSFT": "MSFT.US",
    "TSLA": "TSLA.US", "AMZN": "AMZN.US",
    "META": "META.US", "GOOG": "GOOG.US", "JPM": "JPM.US",
    "V": "V.US", "XOM": "XOM.US", "NFLX": "NFLX.US", "AMD": "AMD.US",
    "CRM": "CRM.US", "DIS": "DIS.US", "BA": "BA.US", "COIN": "COIN.US",
    "PYPL": "PYPL.US", "INTC": "INTC.US", "UBER": "UBER.US", "PLTR": "PLTR.US",
}

# ── Binance crypto symbol mapping ─────────────────────────────────────────────

# Binance Futures uses non-obvious denominations for some tokens.
# PEPE perpetual contract is 1000PEPEUSDT (1 contract = 1000 PEPE).
_BINANCE_SYMBOL_OVERRIDES: dict[str, str] = {
    "PEPE/USDT": "1000PEPEUSDT",
    "PEPEUSDT":  "1000PEPEUSDT",
}


def _binance_symbol(symbol: str) -> str:
    """Convert Athena crypto symbol to Binance futures symbol (BTCUSDT)."""
    raw = symbol.replace("/", "").replace("-", "").upper()
    return _BINANCE_SYMBOL_OVERRIDES.get(symbol, _BINANCE_SYMBOL_OVERRIDES.get(raw, raw))


# ══════════════════════════════════════════════════════════════════════════════
# Source-specific fetchers
# ══════════════════════════════════════════════════════════════════════════════

def _fetch_binance_rest(
    symbol: str,
    timeframe: str,
    limit: int = 1000,
) -> Optional[pd.DataFrame]:
    """
    Fetch from Binance Futures public klines endpoint.
    No authentication required.  Paginates automatically for large requests.
    """
    import requests

    interval = _BINANCE_TF.get(timeframe)
    if interval is None:
        log.debug("[data_loader] Binance: unsupported TF %s", timeframe)
        return None

    bs = _binance_symbol(symbol)
    bases = ["https://fapi.binance.com", "https://fapi1.binance.com"]
    per_page = 1500
    all_rows: list = []

    # Paginate backwards in time
    end_time = None
    retries = 0
    while len(all_rows) < limit:
        params: dict = {
            "symbol": bs,
            "interval": interval,
            "limit": min(per_page, limit - len(all_rows)),
        }
        if end_time is not None:
            params["endTime"] = end_time

        try:
            last_error = None
            batch = None
            for base in bases:
                try:
                    resp = requests.get(f"{base}/fapi/v1/klines", params=params, timeout=15)
                    if resp.status_code == 200:
                        batch = resp.json()
                        break
                    last_error = f"HTTP {resp.status_code}: {resp.text[:120]}"
                except Exception as e:
                    last_error = str(e)
            if batch is None:
                raise RuntimeError(last_error or "all Binance endpoints failed")
        except Exception as e:
            if retries < 2:
                retries += 1
                time.sleep(1.5)
                continue
            log.warning("[data_loader] Binance REST failed for %s: %s", symbol, e)
            return None

        if not batch:
            break

        all_rows = batch + all_rows          # prepend (going backwards)
        end_time = int(batch[0][0]) - 1      # move end_time earlier
        retries = 0

        if len(batch) < per_page:
            break  # reached earliest available data

    if not all_rows:
        return None

    # Keep last `limit` rows
    all_rows = all_rows[-limit:]

    df = pd.DataFrame(all_rows, columns=[
        "open_time", "open", "high", "low", "close", "volume",
        "close_time", "quote_volume", "count",
        "taker_buy_volume", "taker_buy_quote_volume", "ignore",
    ])
    df.index = pd.to_datetime(df["open_time"], unit="ms", utc=True)
    df.index.name = "time"
    for col in ["open", "high", "low", "close", "volume"]:
        df[col] = pd.to_numeric(df[col], errors="coerce")

    return df[["open", "high", "low", "close", "volume"]]


def _fetch_mt5(symbol: str, timeframe: str, limit: int = 1000) -> Optional[pd.DataFrame]:
    """
    Fetch OHLCV from MetaTrader 5.
    Returns None if MT5 is not installed or not connected.
    """
    try:
        import MetaTrader5 as mt5
    except ImportError:
        return None

    if not mt5.initialize():
        log.debug("[data_loader] MT5 initialize() failed — not connected")
        mt5.shutdown()
        return None

    tf_constants = {
        "M1": mt5.TIMEFRAME_M1, "M5": mt5.TIMEFRAME_M5,
        "M15": mt5.TIMEFRAME_M15, "H1": mt5.TIMEFRAME_H1,
        "H4": mt5.TIMEFRAME_H4, "D1": mt5.TIMEFRAME_D1,
    }
    mt5_tf = tf_constants.get(timeframe)
    if mt5_tf is None:
        mt5.shutdown()
        return None

    mt5_sym = _MT5_SYMBOL_MAP.get(symbol, symbol.replace("/", "").replace(" ", ""))

    # Request limit+100 to have headroom (MT5 pattern)
    rates = mt5.copy_rates_from_pos(mt5_sym, mt5_tf, 0, limit + 100)
    mt5.shutdown()

    if rates is None or len(rates) == 0:
        log.debug("[data_loader] MT5 returned no data for %s %s", symbol, timeframe)
        return None

    df = pd.DataFrame(rates)
    df.index = pd.to_datetime(df["time"], unit="s", utc=True)
    df.index.name = "time"
    df = df.rename(columns={"tick_volume": "volume"})
    cols = [c for c in ["open", "high", "low", "close", "volume"] if c in df.columns]
    df = df[cols].tail(limit)

    return df


def _fetch_eodhd(symbol: str, timeframe: str, limit: int = 1000) -> Optional[pd.DataFrame]:
    """
    Fetch from EODHD REST API using EODHD_KEY env var.
    Returns None if EODHD_KEY is not set or the symbol is not supported.
    Handles intraday (H1 and below) and daily (D1) timeframes.
    """
    import requests

    api_key = os.environ.get("EODHD_KEY")
    if not api_key:
        log.debug("[data_loader] EODHD: no EODHD_KEY env var")
        return None

    eodhd_sym = _EODHD_SYMBOL_MAP.get(symbol)
    if not eodhd_sym:
        log.debug("[data_loader] EODHD: no mapping for %s", symbol)
        return None

    try:
        if timeframe == "D1":
            url = f"https://eodhd.com/api/eod/{eodhd_sym}"
            params = {"api_token": api_key, "fmt": "json", "order": "d"}
            resp = requests.get(url, params=params, timeout=15)
            resp.raise_for_status()
            data = resp.json()
            if not data:
                return None
            df = pd.DataFrame(data[:limit])
            df["date"] = pd.to_datetime(df["date"], utc=True)
            # Drop duplicate dates before setting as index (EODHD can emit
            # two rows for the same date after split/adjustment; pandas 3.x
            # raises on sort_index/reindex when duplicates are present).
            df = df.drop_duplicates(subset=["date"], keep="last")
            df = df.set_index("date")
            df.index.name = "time"
            df = df.rename(columns={"adjusted_close": "close"})
            df = df[["open", "high", "low", "close", "volume"]].astype(float)
            return df.sort_index()

        elif timeframe in _EODHD_INTRA_TF:
            interval = _EODHD_INTRA_TF[timeframe]
            url = f"https://eodhd.com/api/intraday/{eodhd_sym}"
            params = {"api_token": api_key, "interval": interval,
                      "fmt": "json", "order": "d"}
            resp = requests.get(url, params=params, timeout=15)
            resp.raise_for_status()
            data = resp.json()
            if not data:
                return None
            df = pd.DataFrame(data[:limit])
            df["timestamp"] = pd.to_datetime(df["timestamp"], unit="s", utc=True)
            # Drop duplicate timestamps before setting as index so downstream
            # resample() (H4 via H1) does not crash under pandas 3.x.
            df = df.drop_duplicates(subset=["timestamp"], keep="last")
            df = df.set_index("timestamp")
            df.index.name = "time"
            cols = {c: c for c in ["open", "high", "low", "close", "volume"] if c in df.columns}
            df = df[list(cols.values())].astype(float)
            return df.sort_index()

        elif timeframe == "H4":
            # EODHD has no native H4; resample from H1
            h1 = _fetch_eodhd(symbol, "H1", limit=limit * 4)
            if h1 is None:
                return None
            agg = {"open": "first", "high": "max", "low": "min",
                   "close": "last", "volume": "sum"}
            return h1.resample("4h").agg(agg).dropna(subset=["close"])

    except Exception as e:
        log.warning("[data_loader] EODHD failed for %s %s: %s", symbol, timeframe, e)
        return None

    return None


def _fetch_yfinance(symbol: str, timeframe: str, limit: int = 1000) -> Optional[pd.DataFrame]:
    """
    yfinance fallback — only called when ALLOW_YFINANCE_FALLBACK is explicitly true.
    Returns (df, source_label) or (None, 'yfinance_unavailable').
    """
    _YF_SYMBOL_MAP: dict[str, str] = {
        "BTC/USDT": "BTC-USD", "ETH/USDT": "ETH-USD",
        "SOL/USDT": "SOL-USD", "EUR/USD": "EURUSD=X",
        "GBP/USD": "GBPUSD=X", "XAU/USD": "GC=F",
        "XAG/USD": "SI=F", "WTI Oil": "CL=F",
        "US500": "^GSPC", "NAS100": "^NDX",
        "AAPL": "AAPL", "NVDA": "NVDA", "MSFT": "MSFT",
    }
    _YF_TF: dict[str, str] = {
        "M5": "5m", "M15": "15m", "H1": "1h", "H4": "1h",
        "D1": "1d",
    }
    _YF_PERIOD: dict[str, str] = {
        "5m": "60d", "15m": "60d", "1h": "730d", "1d": "max",
    }

    try:
        import yfinance as yf
    except ImportError:
        log.warning("[data_loader] yfinance not installed — cannot use fallback")
        return None

    ticker = _YF_SYMBOL_MAP.get(symbol)
    if not ticker:
        return None

    yf_interval = _YF_TF.get(timeframe)
    if not yf_interval:
        return None

    period = _YF_PERIOD.get(yf_interval, "max")

    try:
        import inspect
        dl_kwargs: dict = dict(period=period, interval=yf_interval,
                               auto_adjust=True, progress=False)
        if "show_errors" in inspect.signature(yf.download).parameters:
            dl_kwargs["show_errors"] = False
        raw = yf.download(ticker, **dl_kwargs)
    except Exception as e:
        log.warning("[data_loader] yfinance download failed %s: %s", symbol, e)
        return None

    if raw is None or raw.empty:
        return None

    if isinstance(raw.columns, pd.MultiIndex):
        raw.columns = raw.columns.get_level_values(0)

    raw = raw.rename(columns={"Open": "open", "High": "high", "Low": "low",
                               "Close": "close", "Volume": "volume"})
    raw = raw[[c for c in ["open", "high", "low", "close", "volume"] if c in raw.columns]]

    if raw.index.tz is None:
        raw.index = raw.index.tz_localize("UTC")
    else:
        raw.index = raw.index.tz_convert("UTC")
    raw.index.name = "time"

    # Resample H1 → H4 if needed
    if timeframe == "H4":
        agg = {"open": "first", "high": "max", "low": "min",
               "close": "last", "volume": "sum"}
        raw = raw.resample("4h").agg(agg).dropna(subset=["close"])

    return raw.tail(limit) if len(raw) > limit else raw


# ══════════════════════════════════════════════════════════════════════════════
# Source dispatch
# ══════════════════════════════════════════════════════════════════════════════

def _dispatch(
    symbol: str,
    timeframe: str,
    limit: int,
    allow_yfinance: bool,
) -> tuple[Optional[pd.DataFrame], str, str]:
    """
    Try data sources in priority order.
    Returns (df, source_label, notes).
    source_label is "DATA_UNAVAILABLE" if all fail.
    """
    ac = asset_class_for(symbol)
    tried: list[str] = []

    # ── Crypto: Binance REST first ────────────────────────────────────────────
    if ac == "crypto":
        tried.append("binance_rest")
        df = _fetch_binance_rest(symbol, timeframe, limit)
        if df is not None and len(df) >= 10:
            return df, "binance_rest", ""

    # ── Forex / Commodity / Index: MT5 first ─────────────────────────────────
    if ac in ("forex", "commodity", "index"):
        tried.append("mt5")
        df = _fetch_mt5(symbol, timeframe, limit)
        if df is not None and len(df) >= 10:
            return df, "mt5", ""
        tried.append("eodhd")
        df = _fetch_eodhd(symbol, timeframe, limit)
        if df is not None and len(df) >= 10:
            return df, "eodhd", ""

    # ── Stocks: EODHD first ───────────────────────────────────────────────────
    if ac == "stock":
        tried.append("eodhd")
        df = _fetch_eodhd(symbol, timeframe, limit)
        if df is not None and len(df) >= 10:
            return df, "eodhd", ""
        tried.append("mt5")
        df = _fetch_mt5(symbol, timeframe, limit)
        if df is not None and len(df) >= 10:
            return df, "mt5", ""

    # ── yfinance opt-in fallback ──────────────────────────────────────────────
    if allow_yfinance:
        tried.append("yfinance_fallback")
        log.warning("[data_loader] %s %s: using yfinance_fallback (ALLOW_YFINANCE_FALLBACK=true)", symbol, timeframe)
        df = _fetch_yfinance(symbol, timeframe, limit)
        if df is not None and len(df) >= 10:
            return df, "yfinance_fallback", "yfinance used — results may differ from live Athena data"

    notes = f"tried: {tried}"
    log.warning("[data_loader] DATA_UNAVAILABLE: %s %s — %s", symbol, timeframe, notes)
    return None, "DATA_UNAVAILABLE", notes


# ══════════════════════════════════════════════════════════════════════════════
# Cache layer
# ══════════════════════════════════════════════════════════════════════════════

def _cache_key(symbol: str, timeframe: str) -> str:
    return symbol.replace("/", "_").replace(" ", "_") + f"_{timeframe}"


def _cache_ttl_hours(timeframe: str) -> float:
    return {"M1": 0.25, "M5": 1.0, "M15": 2.0, "H1": 4.0,
            "H4": 12.0, "D1": 24.0, "W1": 168.0}.get(timeframe, 4.0)


def _load_cache(cache_dir: Path, key: str, timeframe: str) -> Optional[tuple[pd.DataFrame, DataProvenance]]:
    parquet = cache_dir / f"{key}.parquet"
    prov_path = cache_dir / f"{key}.provenance.json"
    if not parquet.exists():
        return None
    age_h = (datetime.now() - datetime.fromtimestamp(parquet.stat().st_mtime)).total_seconds() / 3600
    if age_h >= _cache_ttl_hours(timeframe):
        return None
    try:
        df = pd.read_parquet(parquet)
        prov = DataProvenance(**json.loads(prov_path.read_text())) if prov_path.exists() else None
        if prov:
            prov.cached = True
        return df, prov
    except Exception:
        return None


def _save_cache(cache_dir: Path, key: str, df: pd.DataFrame, prov: DataProvenance) -> None:
    try:
        df.to_parquet(cache_dir / f"{key}.parquet")
        (cache_dir / f"{key}.provenance.json").write_text(json.dumps(prov.to_dict(), default=str))
    except Exception as e:
        log.debug("[data_loader] Cache write failed: %s", e)


# ══════════════════════════════════════════════════════════════════════════════
# Public API
# ══════════════════════════════════════════════════════════════════════════════

def load_ohlcv(
    symbol: str,
    timeframe: str,
    cache_dir: Optional[Path] = None,
    limit: int = 1000,
    force_refresh: bool = False,
    allow_yfinance: bool = False,
) -> tuple[Optional[pd.DataFrame], DataProvenance]:
    """
    Load OHLCV data for *symbol* at *timeframe* from Athena's native sources.

    Returns (DataFrame | None, DataProvenance).
    DataFrame has columns [open, high, low, close, volume] with UTC DatetimeIndex.
    When data is unavailable, DataFrame is None and DataProvenance.data_source = "DATA_UNAVAILABLE".

    Parameters
    ----------
    allow_yfinance : bool
        Must be explicitly True to allow yfinance fallback.
        Default False — never fall through to yfinance silently.
    """
    cache_dir = Path(cache_dir) if cache_dir else _DEFAULT_CACHE_DIR
    cache_dir.mkdir(parents=True, exist_ok=True)
    key = _cache_key(symbol, timeframe)

    # Cache check
    if not force_refresh:
        cached = _load_cache(cache_dir, key, timeframe)
        if cached is not None:
            df, prov = cached
            log.debug("[data_loader] Cache hit %s %s (%d bars, source=%s)",
                      symbol, timeframe, len(df), prov.data_source if prov else "?")
            cleaned = _clean(df).tail(limit)
            prov = _provenance(
                cleaned,
                symbol,
                timeframe,
                prov.data_source if prov else "cache",
                cached=True,
                notes=prov.notes if prov else "loaded from cache",
            )
            return cleaned, prov

    # Fetch from source
    df, source, notes = _dispatch(symbol, timeframe, limit, allow_yfinance)

    if df is None or len(df) == 0:
        prov = DataProvenance.unavailable(symbol, timeframe, notes)
        return None, prov

    df = _clean(df)
    prov = _provenance(df, symbol, timeframe, source, cached=False, notes=notes)
    _save_cache(cache_dir, key, df, prov)
    return df, prov


def load_ohlcv_multi(
    symbols: list[str],
    timeframe: str,
    cache_dir: Optional[Path] = None,
    limit: int = 1000,
    force_refresh: bool = False,
    allow_yfinance: bool = False,
    min_bars: int = 50,
) -> dict[str, tuple[pd.DataFrame, DataProvenance]]:
    """
    Load OHLCV for multiple symbols.

    Returns dict[symbol → (DataFrame, DataProvenance)].
    Symbols with DATA_UNAVAILABLE are included with df=None so callers can report them.
    Symbols with df != None but len(df) < min_bars are excluded with a warning.
    """
    out: dict[str, tuple[Optional[pd.DataFrame], DataProvenance]] = {}
    for sym in symbols:
        df, prov = load_ohlcv(sym, timeframe, cache_dir=cache_dir, limit=limit,
                               force_refresh=force_refresh, allow_yfinance=allow_yfinance)
        if prov.data_source == "DATA_UNAVAILABLE":
            log.warning("[data_loader] DATA_UNAVAILABLE: %s %s — %s", sym, timeframe, prov.notes)
            out[sym] = (None, prov)
        elif df is not None and len(df) < min_bars:
            log.warning("[data_loader] %s %s: only %d bars (< min %d) — marking unavailable",
                        sym, timeframe, len(df), min_bars)
            short_prov = DataProvenance.unavailable(
                sym, timeframe,
                f"too_few_bars: got {len(df)}, need {min_bars}; source was {prov.data_source}"
            )
            out[sym] = (None, short_prov)
        else:
            out[sym] = (df, prov)
    return out


def ohlcv_from_dict_list(candles: list[dict], symbol: str = "", timeframe: str = "") -> tuple[pd.DataFrame, DataProvenance]:
    """
    Convert Athena-style list-of-dicts (keys: time/open/high/low/close/vol)
    to a research-lab DataFrame.  Provenance is marked 'athena_cache'.
    """
    if not candles:
        return pd.DataFrame(), DataProvenance.unavailable(symbol, timeframe, "empty_list")

    df = pd.DataFrame(candles)
    df = df.rename(columns={"vol": "volume", "t": "time", "o": "open",
                             "h": "high", "l": "low", "c": "close"})
    if "time" in df.columns:
        df["time"] = pd.to_datetime(df["time"], utc=True, errors="coerce")
        df = df.set_index("time").sort_index()

    df = _clean(df)
    prov = _provenance(df, symbol, timeframe, "athena_cache", cached=False)
    return df, prov


def synthetic_ohlcv(n: int = 500, seed: int = 42, trend: float = 0.0001,
                    symbol: str = "TEST", timeframe: str = "H1") -> tuple[pd.DataFrame, DataProvenance]:
    """
    Generate synthetic OHLCV for tests.  Provenance is marked 'synthetic_test'.
    NEVER used in production research runs.
    """
    rng = np.random.default_rng(seed)
    close = 100.0 * np.cumprod(1 + rng.normal(trend, 0.01, n))
    high = close * (1 + rng.uniform(0, 0.005, n))
    low = close * (1 - rng.uniform(0, 0.005, n))
    open_ = close * (1 + rng.normal(0, 0.002, n))
    volume = rng.uniform(1000, 10000, n)

    idx = pd.date_range("2023-01-01", periods=n, freq="1h", tz="UTC")
    df = pd.DataFrame(
        {"open": open_, "high": high, "low": low, "close": close, "volume": volume},
        index=idx,
    )
    df.index.name = "time"

    prov = DataProvenance(
        symbol=symbol, timeframe=timeframe, data_source="synthetic_test",
        candle_count=n, missing_candle_count=0,
        start_time=str(idx[0]), end_time=str(idx[-1]),
        timezone="UTC", cached=False,
        fetch_timestamp=datetime.now(timezone.utc).isoformat(),
        notes="synthetic test data — not real market data",
    )
    return df, prov


# ── Data cleaning ─────────────────────────────────────────────────────────────

def _clean(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return df
    df = df.copy()
    required = [c for c in ["open", "high", "low", "close"] if c in df.columns]
    df = df.dropna(subset=required)
    if "close" in df.columns:
        df = df[df["close"] > 0]
    if "volume" not in df.columns:
        df["volume"] = 0.0
    df["volume"] = df["volume"].fillna(0.0).clip(lower=0)
    # Deduplicate BEFORE sort_index: pandas 3.x raises
    # "cannot reindex on an axis with duplicate labels" inside sort_index
    # when duplicates are present (e.g. EODHD EOD returns two rows for the
    # same date after a split adjustment).
    if df.index.has_duplicates:
        df = df[~df.index.duplicated(keep="last")]
    df = df.sort_index()
    return df
