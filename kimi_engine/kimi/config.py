"""KIMI Engine configuration. All knobs in one place; env overrides allowed.

Default mode is PAPER. Live/demo broker execution is gated behind env keys and
KIMI_LIVE_ENABLED=1 — without those, every order routes to the paper broker.
"""
from __future__ import annotations

import os


def _env(name: str, default: str) -> str:
    v = os.environ.get(name)
    return v if v not in (None, "") else default


def _env_float(name: str, default: float) -> float:
    try:
        return float(_env(name, str(default)))
    except ValueError:
        return default


def _env_int(name: str, default: int) -> int:
    try:
        return int(float(_env(name, str(default))))
    except ValueError:
        return default


class Config:
    # ---- universe -----------------------------------------------------
    # USDT pairs resolve via Binance/Bybit; FX/metals/indices via the
    # attached MT5 terminal (broker suffixes like ".s" are auto-resolved),
    # Yahoo as last resort. Mixing classes in one universe is supported.
    SYMBOLS = [s.strip().upper() for s in _env(
        "KIMI_SYMBOLS",
        "BTCUSDT,ETHUSDT,SOLUSDT,BNBUSDT,XRPUSDT,DOGEUSDT,ADAUSDT,LINKUSDT,"
        "EURUSD,GBPUSD,USDJPY,XAUUSD",
    ).split(",") if s.strip()]

    TF_ENTRY = "5m"        # execution / trigger timeframe
    TF_CONTEXT = "15m"     # setup timeframe
    TF_BIAS = "1h"         # bias timeframe
    TF_MINUTES = {"1m": 1, "5m": 5, "15m": 15, "1h": 60, "4h": 240, "1d": 1440}

    KLINE_LIMIT = 240

    # Upper bound on the scan universe. The old limit was 30, which forced the
    # picker to be an exercise in choosing favourites; the live Athena active
    # book is ~189 instruments, so the cap is sized to hold it with room to
    # spare. It is still a cap, not a suggestion: an unbounded universe
    # would blow the per-cycle request budget at both venues.
    MAX_SYMBOLS = _env_int("KIMI_MAX_SYMBOLS", 200)
    # Per-symbol evaluation is three kline fetches deep. Serially that is fine
    # for a dozen symbols and hopeless for a hundred, so it runs in a pool.
    SCAN_WORKERS = _env_int("KIMI_SCAN_WORKERS", 8)

    # ---- price freshness ----------------------------------------------
    # A quote older than this is shown as stale rather than as a live price.
    # Sized off TICK_POLL_SEC: several missed poll cycles, not one slow call.
    TICK_STALE_SEC = _env_float("KIMI_TICK_STALE_SEC", 30.0)
    # And older than this is dropped from the cache entirely.
    TICK_MAX_AGE_SEC = _env_float("KIMI_TICK_MAX_AGE_SEC", 900.0)

    # ---- data ---------------------------------------------------------
    BINANCE_BASE = _env("KIMI_BINANCE_BASE", "https://api.binance.com")
    BYBIT_PUBLIC_BASE = "https://api.bybit.com"
    TICK_POLL_SEC = _env_float("KIMI_TICK_POLL_SEC", 5.0)
    KLINE_POLL_SEC = _env_float("KIMI_KLINE_POLL_SEC", 20.0)
    HTTP_TIMEOUT_SEC = _env_float("KIMI_HTTP_TIMEOUT_SEC", 8.0)
    # MT5 bars/ticks share the broker server clock (ATFX = UTC+3). Used as
    # fallback when the tick clock can't prove the offset.
    MT5_BROKER_UTC_OFFSET = _env_int("KIMI_MT5_BROKER_UTC_OFFSET", 3)

    # ---- scoring ------------------------------------------------------
    SCORE_A_PLUS = 85.0
    SCORE_A = 75.0
    SCORE_B = 65.0          # watchlist only, no signal
    SIGNAL_MIN_SCORE = _env_float("KIMI_SIGNAL_MIN_SCORE", SCORE_A)
    SIGNAL_COOLDOWN_SEC = _env_float("KIMI_SIGNAL_COOLDOWN_SEC", 20 * 60)
    # Entry-TF validity window. 6×5m was a 30-minute chip that barely moved
    # and outlived the setup; two bars is a 5m trigger's usable window.
    SIGNAL_TTL_BARS = _env_int("KIMI_SIGNAL_TTL_BARS", 2)
    # A sweep/displacement older than this many context bars still contributes
    # decayed structure points, but no longer counts as the REQUIRED hard
    # trigger for A+ grades and signal gating.
    STRUCT_HARD_MAX_AGE_BARS = _env_int("KIMI_STRUCT_HARD_MAX_AGE_BARS", 8)

    # ---- risk ----------------------------------------------------------
    START_EQUITY = _env_float("KIMI_START_EQUITY", 100_000.0)
    RISK_PER_TRADE_PCT = _env_float("KIMI_RISK_PER_TRADE_PCT", 1.0)
    MAX_POSITIONS = _env_int("KIMI_MAX_POSITIONS", 4)
    MAX_DAILY_LOSS_PCT = _env_float("KIMI_MAX_DAILY_LOSS_PCT", 3.0)
    TAKER_FEE_BPS = _env_float("KIMI_TAKER_FEE_BPS", 5.0)       # 0.05%
    SLIPPAGE_BPS = _env_float("KIMI_SLIPPAGE_BPS", 2.0)
    SL_ATR_BUFFER = 0.35
    TP1_R = 1.5
    TP2_R = 3.0
    TP1_CLOSE_FRAC = 0.5

    # ---- broker ---------------------------------------------------------
    # paper | bybit   (bybit requires keys + KIMI_LIVE_ENABLED=1)
    BROKER = _env("KIMI_BROKER", "paper").lower()
    LIVE_ENABLED = _env("KIMI_LIVE_ENABLED", "0") == "1"
    BYBIT_KEY = _env("KIMI_BYBIT_KEY", "")
    BYBIT_SECRET = _env("KIMI_BYBIT_SECRET", "")
    BYBIT_ENV = _env("KIMI_BYBIT_ENV", "demo").lower()  # demo | testnet | live

    STATE_FILE = _env("KIMI_STATE_FILE", "state/kimi_state.json")

    # ---- host execution (Athena pipeline: risk_engine → guardian → executors)
    # When the host modules import cleanly, signals can execute for real (demo
    # account first) exactly like the other engines. venue="host" forces it;
    # venue="auto" prefers it. Real (non-demo) accounts additionally require
    # KIMI_HOST_REAL_ORDERS=1.
    HOST_EXEC = _env("KIMI_HOST_EXEC", "1") == "1"
    HOST_REAL_ORDERS = _env("KIMI_HOST_REAL_ORDERS", "0") == "1"

    # ---- server ---------------------------------------------------------
    HOST = _env("KIMI_HOST", "127.0.0.1")
    PORT = _env_int("KIMI_PORT", 7100)
