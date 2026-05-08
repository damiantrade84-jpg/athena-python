"""scalp_engine.py — Engine D: Fabio Valentini Pro Scalper

Replaces legacy M15/M5 zone-trigger system with Volume Profile + Order Flow
methodology based on Fabio Valentini's audited approach (Robbins World Cup).

Three-pillar decision gate (ALL must align):
  1. Market State — balance vs imbalance (Volume Profile distribution shape)
  2. Location — price at VP level (VAL, VAH, POC, LVN)
  3. Aggression — absorption, CVD confirmation, or AAA completion

Two setup types:
  - Mean Reversion: price at value area extreme → target POC
  - Trend Continuation: price pulls back to LVN inside impulse leg → target POC/opposite VA

Grading: A (full size) / B (half) / C (quarter) / D (skip)

Data sources:
  - MT5 (copy_rates_from_pos) — forex, commodities, indices, stocks
  - Crypto: fetch_candles → Binance futures via athena_runtime
  - Volume: tick_volume (MT5), real volume (Binance/EODHD/Polygon) — all stored as 'vol'

All trades route through risk_engine.risk_check() before execution.
"""

import logging
import math
import threading
import time as _time
from collections import Counter
import pandas as pd
from datetime import datetime, time, timezone
from typing import Any, Optional
from zoneinfo import ZoneInfo

from config import CONFIG
from stability_monitor import record_signal_event

log = logging.getLogger("sentinel.scalp")

# Engine D funnel diagnostics (report-only)
try:
    from scalp_audit import build_funnel_row, log_engine_d_funnel, shadow_proximity_simulations
except Exception as _sa_err:
    build_funnel_row = None  # type: ignore
    log_engine_d_funnel = None  # type: ignore
    shadow_proximity_simulations = None  # type: ignore
    log.debug("[SCALP-AUDIT] scalp_audit import failed: %s", _sa_err)


def _london_cash_open_utc_minute_of_day(when_utc: datetime | None = None) -> int:
    """UTC minute-of-day (0..1439) for 08:00 Europe/London on London's calendar day of ``when_utc``."""
    ref = (when_utc or datetime.now(timezone.utc)).astimezone(ZoneInfo("Europe/London"))
    day = ref.date()
    open_local = datetime.combine(day, time(8, 0), tzinfo=ZoneInfo("Europe/London"))
    open_utc = open_local.astimezone(timezone.utc)
    return open_utc.hour * 60 + open_utc.minute


# =========================================================================
# SESSION RISK STATE (resets on new UTC day)
# =========================================================================
_session_state = {
    "date": None,
    "consecutive_losses": 0,
    "total_losses_today": 0,
    "net_r_today": 0.0,
    "size_cut_active": False,
}
_session_state_lock = threading.Lock()


def _reset_session_state_if_new_day_locked():
    """Caller must hold _session_state_lock."""
    today = _current_utc_datetime().date()
    if _session_state["date"] != today:
        _session_state["date"] = today
        _session_state["consecutive_losses"] = 0
        _session_state["total_losses_today"] = 0
        _session_state["net_r_today"] = 0.0
        _session_state["size_cut_active"] = False


def _reset_session_state_if_new_day():
    """Reset daily counters at UTC midnight (thread-safe)."""
    with _session_state_lock:
        _reset_session_state_if_new_day_locked()


def record_scalp_trade_outcome(r_multiple: float):
    """Called after a scalp trade closes to update session risk state (thread-safe)."""
    try:
        r_multiple = float(r_multiple)
    except (TypeError, ValueError):
        return
    cfg = CONFIG.get("SCALP_ENGINE", {})
    with _session_state_lock:
        _reset_session_state_if_new_day_locked()
        _session_state["net_r_today"] += r_multiple
        if r_multiple <= 0:
            _session_state["consecutive_losses"] += 1
            _session_state["total_losses_today"] += 1
        else:
            _session_state["consecutive_losses"] = 0
        # Recompute size cut from current net_r (not a one-way latch).
        # Deactivates if subsequent losses bring net_r back below the 2R threshold.
        if cfg.get("SIZE_CUT_AFTER_2R", True):
            _session_state["size_cut_active"] = _session_state["net_r_today"] >= 2.0
        else:
            _session_state["size_cut_active"] = False


def get_scalp_session_risk_state() -> dict:
    """Return current session risk state for UI/logging (thread-safe snapshot)."""
    with _session_state_lock:
        _reset_session_state_if_new_day_locked()
        return dict(_session_state)


# ═══════════════════════════════════════════════════════════════════════════════
# DATA FETCHING — unchanged interface, MT5 + Binance/Athena runtime
# ═══════════════════════════════════════════════════════════════════════════════

def _scalp_fetch_candles(pair: dict, tf: str, limit: int):
    """Route through monolith fetch_candles for crypto M1/M5/M15/H1.

    Engine D crypto M1 prefers verified Binance WS candles where available,
    then falls back to the routed cache/REST path.
    """
    tf = str(tf or "").upper()
    if (
        tf == "M1"
        and str(pair.get("type", "")).lower() == "crypto"
        and str(pair.get("source", "")).lower() == "binance"
    ):
        display = str(pair.get("display") or "")
        try:
            from candle_feeds import fetch_candles_live
            ws_resp = fetch_candles_live(display, "M1", limit)
            ws_candles = (ws_resp or {}).get("candles") if isinstance(ws_resp, dict) else None
            if ws_candles:
                last_ts = _coerce_utc_datetime(ws_candles[-1].get("time"))
                now_ts = _current_utc_datetime()
                age_s = (now_ts - last_ts).total_seconds() if last_ts else 1e9
                # "Verified WS" means bars are present and latest update is fresh enough.
                if len(ws_candles) >= 5 and age_s <= 180:
                    log.info(
                        "[SCALP-DATA] %s M1 source=binance_ws bars=%s age_s=%.0f",
                        display,
                        len(ws_candles),
                        age_s,
                    )
                    return ws_candles[-limit:] if len(ws_candles) > limit else ws_candles
                log.info(
                    "[SCALP-DATA] %s M1 ws_unverified bars=%s age_s=%.0f -> fallback=routed",
                    display,
                    len(ws_candles),
                    age_s,
                )
            else:
                log.info("[SCALP-DATA] %s M1 ws_unavailable -> fallback=routed", display)
        except Exception as e:
            log.warning("[SCALP-DATA] %s M1 ws_check_error=%s -> fallback=routed", display, e)

    try:
        from athena_runtime import rt
        candles = rt().fetch_candles(pair, tf, limit)
        if tf == "M1" and str(pair.get("type", "")).lower() == "crypto":
            n = len(candles) if candles else 0
            log.info("[SCALP-DATA] %s M1 source=routed bars=%s", pair.get("display", ""), n)
        return candles
    except RuntimeError:
        log.error("[SCALP] fetch_candles unavailable — athena runtime not initialized")
    except Exception as e:
        log.error("[SCALP] fetch_candles error: %s", e)
    return None


def _rate_value(rate, field: str, default=0.0):
    if isinstance(rate, dict):
        return rate.get(field, default)
    try:
        return rate[field]
    except Exception:
        return default


def _tick_value(tick, field: str, default=0.0):
    if tick is None:
        return default
    if isinstance(tick, dict):
        return tick.get(field, default)
    return getattr(tick, field, default)


# ═══════════════════════════════════════════════════════════════════════════════
# SESSION WINDOWS (LOCAL MARKET TIME)
# ═══════════════════════════════════════════════════════════════════════════════

_SESSIONS = {
    "london":    (7, 16),
    "new_york":  (13, 21),
    "london_ny": (7, 21),
}

_CRYPTO_SESSIONS = {
    "asia":        (1, 9),
    "london":      (7, 16),
    "new_york":    (13, 22),
    "london_ny":   (7, 22),
    "asia_london_ny": (1, 22),
}

_TZ_LONDON = ZoneInfo("Europe/London")
_TZ_NEW_YORK = ZoneInfo("America/New_York")

# Keep the existing session concept, but anchor clocks to local market time.
# Derived from existing UTC windows on a winter reference date so behaviour
# remains conceptually unchanged while becoming DST-safe.
_FOREX_LOCAL_SESSIONS = {
    "london": (7, 16),      # Europe/London local time
    "new_york": (8, 16),    # America/New_York local time
}
_CRYPTO_LOCAL_SESSIONS = {
    "asia": (1, 9),         # UTC (24/7 crypto Asia activity window)
    "london": (7, 16),      # Europe/London local time
    "new_york": (8, 17),    # America/New_York local time
}

_CRYPTO_SCALP_PAIRS = [
    "BTC/USDT", "ETH/USDT", "SOL/USDT", "XRP/USDT",
    "ADA/USDT", "DOGE/USDT", "AVAX/USDT", "LINK/USDT",
    "BNB/USDT", "SUI/USDT", "LTC/USDT",
]

_SCALP_PAIR_META_BY_DISPLAY: dict[str, dict[str, Any]] = {}
_EODHD_STOCK_SUFFIX_UNMAPPED_SOURCE = "eodhd_suffix_unmapped_for_stock"


def _crypto_sym_info(current_price: float) -> dict:
    """Return point/digits/spread for a crypto symbol based on current price.

    Replaces the former hardcoded {point=0.01, digits=2} which produced
    catastrophic SL geometry on low-price altcoins (DOGE, XRP, ADA, SUI).
    """
    if current_price >= 100.0:
        return {"spread": 0, "point": 0.01,    "digits": 2}
    if current_price >= 5.0:
        return {"spread": 0, "point": 0.001,   "digits": 3}
    if current_price >= 0.50:
        return {"spread": 0, "point": 0.0001,  "digits": 4}
    return {"spread": 0, "point": 0.00001, "digits": 5}


def _resolve_eodhd_stock_symbol_for_scalp(display: str, cfg: dict) -> tuple[Optional[str], Optional[str]]:
    raw = str(display or "").strip()
    if not raw:
        return None, _EODHD_STOCK_SUFFIX_UNMAPPED_SOURCE
    if "." in raw:
        return raw, None

    meta = _SCALP_PAIR_META_BY_DISPLAY.get(raw)
    meta_symbol = str((meta or {}).get("symbol") or "").strip() if isinstance(meta, dict) else ""
    if "." in meta_symbol:
        return meta_symbol, None

    suffix_map = cfg.get("EODHD_STOCK_EXCHANGE_SUFFIX_MAP", {}) or {}
    suffix = None
    if isinstance(suffix_map, dict):
        suffix = suffix_map.get(raw) or suffix_map.get(raw.upper())
    if suffix:
        return f"{raw}.{str(suffix).strip().upper()}", None
    return None, _EODHD_STOCK_SUFFIX_UNMAPPED_SOURCE


# ═══════════════════════════════════════════════════════════════════════════════
# MT5 CANDLE FETCHING
# ═══════════════════════════════════════════════════════════════════════════════

def mt5_fetch_scalp_candles(
    mt5_symbol: str, timeframe_str: str, count: int, *, include_forming: bool = False
) -> list:
    """Fetch OHLCV candles from MT5 terminal.

    Returns list of dicts: {time, open, high, low, close, vol}
    """
    try:
        from mt5_executor import mt5_connect
        import MetaTrader5 as mt5

        if not mt5_connect():
            log.error(f"[SCALP] MT5 not connected - cannot fetch {mt5_symbol}")
            return []

        tf_map = {
            "M1":  mt5.TIMEFRAME_M1,
            "M5":  mt5.TIMEFRAME_M5,
            "M15": mt5.TIMEFRAME_M15,
            "H1":  mt5.TIMEFRAME_H1,
        }
        tf = tf_map.get(timeframe_str.upper())
        if tf is None:
            log.error(f"[SCALP] Unknown timeframe: {timeframe_str}")
            return []

        mt5.symbol_select(mt5_symbol, True)
        rates = mt5.copy_rates_from_pos(mt5_symbol, tf, 0, count)
        if rates is None or len(rates) == 0:
            log.warning(f"[SCALP] No {timeframe_str} data for {mt5_symbol}")
            return []

        candles = []
        for r in rates:
            candles.append({
                "time":  _rate_value(r, "time"),
                "open":  float(_rate_value(r, "open")),
                "high":  float(_rate_value(r, "high")),
                "low":   float(_rate_value(r, "low")),
                "close": float(_rate_value(r, "close")),
                "vol":   float(_rate_value(r, "tick_volume", 0)),
            })

        if not include_forming and len(candles) > 1:
            candles = candles[:-1]

        return candles

    except ImportError:
        log.error("[SCALP] MetaTrader5 package not installed")
        return []
    except Exception as e:
        log.error(f"[SCALP] mt5_fetch_scalp_candles error: {e}")
        return []


def _overlay_eodhd_volume_for_scalp(
    display: str,
    asset_type: str,
    tf: str,
    candles: list,
    *,
    live: bool = True,
) -> tuple[list, str]:
    """Overlay audited EODHD volume on MT5 scalp candles when available.

    Returns (candles, volume_source) where volume_source is one of:
      'ws_tick'      — live WS-accumulated US stock volume (near-real-time)
      'eodhd_1m'     — EODHD intraday hist 1m resampled (~1-2 min lag, forex/metals)
      'eodhd_1h'     — EODHD intraday hist 1h (2-3h stale for stocks during session)
      'eodhd_hist'   — EODHD intraday hist other interval
      'mt5_tick'     — fell back to raw MT5 tick-volume (overlay unavailable)
      'cache_miss'   — cache empty, bg re-warm triggered for next scan
    """
    if not candles or str(asset_type or "").lower() not in {"forex", "commodity", "index", "stock"}:
        return candles, "mt5_tick"
    cfg = CONFIG.get("SCALP_ENGINE", {})
    if live and not cfg.get("EODHD_VOLUME_OVERLAY_LIVE_ENABLED", False):
        return candles, "mt5_tick"
    if not live and not cfg.get("EODHD_VOLUME_OVERLAY_BACKTEST_ENABLED", True):
        return candles, "mt5_tick"
    if str(asset_type).lower() == "stock" and "." not in str(display):
        symbol, suffix_error = _resolve_eodhd_stock_symbol_for_scalp(display, cfg)
        if suffix_error:
            log.warning(
                "[SCALP-DATA] %s %s EODHD stock suffix unmapped; skipping volume overlay",
                display,
                tf,
            )
            return candles, suffix_error
    else:
        symbol = display
    try:
        from athena_runtime import rt
        from eodhd_volume_overlay import overlay_candle_volumes

        pair = {"display": display, "symbol": symbol, "type": asset_type, "source": "mt5"}
        volume_resp = rt().fetch_eodhd_volume_only(
            pair,
            tf,
            len(candles),
            cache_only=live,
        )
        vol_source = (volume_resp or {}).get("volume_source", "mt5_tick") if isinstance(volume_resp, dict) else "mt5_tick"
        volume_candles = (volume_resp or {}).get("candles") if isinstance(volume_resp, dict) else None
        if not volume_candles:
            return candles, vol_source
        merged, matched = overlay_candle_volumes(candles, volume_candles, tf)
        if matched > 0:
            log.debug("[SCALP-DATA] %s %s volume overlay matched=%s/%s source=%s", display, tf, matched, len(candles), vol_source)
            return merged, vol_source
    except Exception as exc:
        log.debug("[SCALP-DATA] %s %s EODHD volume overlay skipped: %s", display, tf, exc)
    return candles, "mt5_tick"


def _merge_vp_volume_source_tag(vp: dict, dominant: str) -> dict:
    """Tag VP with the resolved structural volume lineage (overlay + candle module)."""
    if not isinstance(vp, dict) or not vp.get("valid"):
        return vp
    dom = str(dominant or "").strip() or "unknown"
    raw = str(vp.get("volume_source") or "").strip().lower()
    if raw in ("", "candles", "candle_volume"):
        vp["volume_source"] = dom
    elif raw.startswith("range_proxy"):
        vp["volume_source"] = f"range_proxy({dom})"
    elif dom and dom not in raw:
        vp["volume_source"] = f"{raw}|{dom}"
    return vp


def _attach_engine_d_data_freshness_to_signal(
    signal: dict,
    *,
    pair_dict: dict,
    candles_by_tf: dict[str, list],
    time_now: float | None = None,
) -> None:
    """Populate candleFetchMeta + dataFreshness for parity with analyze_pair / risk_engine."""
    try:
        from athena_app.services.data_freshness import (
            build_live_feed_diagnostic,
            evaluate_execution_data_freshness,
        )

        ts = (
            float(time_now)
            if time_now is not None
            else datetime.now(timezone.utc).timestamp()
        )
        meta: dict[str, Any] = {"pairSource": pair_dict.get("source")}
        for tf, series in (candles_by_tf or {}).items():
            if not series or not tf:
                continue
            tf_key = str(tf).upper()
            meta[tf_key] = build_live_feed_diagnostic(
                pair_dict,
                tf_key,
                list(series),
                time_now=ts,
                source=pair_dict.get("source"),
            )
        signal["candleFetchMeta"] = meta
        fe = evaluate_execution_data_freshness(signal, CONFIG)
        signal["dataFreshness"] = fe
        if (
            CONFIG.get("SIGNAL_EXECUTABLE_FALSE_WHEN_FRESHNESS_BLOCKS", True)
            and isinstance(fe, dict)
            and not fe.get("allowed")
        ):
            signal["executable"] = False
            signal.setdefault("fail_reasons", []).append(
                str(fe.get("reason") or "STALE_DATA_BLOCK")
            )
    except Exception:
        log.debug("[SCALP] Engine D data freshness attachment failed", exc_info=True)


def mt5_get_live_price(mt5_symbol: str) -> float | None:
    """Return a live MT5 price (bid/ask midpoint preferred)."""
    try:
        from mt5_executor import mt5_connect
        import MetaTrader5 as mt5

        if not mt5_connect():
            return None

        mt5.symbol_select(mt5_symbol, True)
        tick = mt5.symbol_info_tick(mt5_symbol)
        if not tick:
            return None

        bid = float(_tick_value(tick, "bid", 0) or 0)
        ask = float(_tick_value(tick, "ask", 0) or 0)
        last = float(_tick_value(tick, "last", 0) or 0)

        if bid > 0 and ask > 0:
            return (bid + ask) / 2.0
        if last > 0:
            return last
        if bid > 0:
            return bid
        if ask > 0:
            return ask
        return None
    except ImportError:
        return None
    except Exception as e:
        log.error(f"[SCALP] mt5_get_live_price error: {e}")
        return None


def mt5_market_open_state(mt5_symbol: str) -> dict:
    """Return whether MT5 has fresh tradable market data for a symbol."""
    cfg = CONFIG.get("SCALP_ENGINE", {})
    if not cfg.get("MARKET_OPEN_CHECK_ENABLED", True):
        return {"open": True, "reason": "market_open_check_disabled"}

    max_age = max(1, int(cfg.get("MARKET_TICK_MAX_AGE_SEC", 900)))
    try:
        from mt5_executor import mt5_connect
        import MetaTrader5 as mt5

        if not mt5_connect():
            return {"open": False, "reason": "MT5_NOT_CONNECTED"}

        mt5.symbol_select(mt5_symbol, True)
        info = mt5.symbol_info(mt5_symbol)
        disabled_mode = getattr(mt5, "SYMBOL_TRADE_MODE_DISABLED", None)
        trade_mode = getattr(info, "trade_mode", None) if info is not None else None
        if disabled_mode is not None and trade_mode == disabled_mode:
            return {"open": False, "reason": "MARKET_CLOSED_TRADE_DISABLED"}

        tick = mt5.symbol_info_tick(mt5_symbol)
        if not tick:
            return {"open": False, "reason": "MARKET_CLOSED_NO_TICK"}

        bid = float(_tick_value(tick, "bid", 0) or 0)
        ask = float(_tick_value(tick, "ask", 0) or 0)
        last = float(_tick_value(tick, "last", 0) or 0)
        if bid <= 0 and ask <= 0 and last <= 0:
            return {"open": False, "reason": "MARKET_CLOSED_NO_PRICE"}

        tick_time = _tick_value(tick, "time_msc", None)
        if tick_time is not None:
            # time_msc is milliseconds since epoch; convert to seconds for datetime
            tick_time = float(tick_time) / 1000.0
        else:
            tick_time = _tick_value(tick, "time", None)
        tick_dt = _coerce_utc_datetime(tick_time)
        if tick_dt is not None:
            age_s = (_current_utc_datetime() - tick_dt).total_seconds()
            if age_s > max_age:
                return {
                    "open": False,
                    "reason": "MARKET_CLOSED_STALE_TICK",
                    "age_sec": round(age_s, 1),
                }

        return {"open": True, "reason": "market_open"}
    except ImportError:
        return {"open": False, "reason": "MT5_PACKAGE_UNAVAILABLE"}
    except Exception as e:
        log.error(f"[SCALP] mt5_market_open_state error: {e}")
        return {"open": False, "reason": "MARKET_OPEN_CHECK_ERROR"}


def _latest_candle_age_seconds(candles: list) -> float | None:
    if not candles:
        return None
    ts = _coerce_utc_datetime((candles[-1] or {}).get("time"))
    if ts is None:
        return None
    return (_current_utc_datetime() - ts).total_seconds()


def _scalp_candles_fresh(candles: list, timeframe: str, role: str = "execution") -> tuple[bool, str]:
    cfg = CONFIG.get("SCALP_ENGINE", {})
    max_age = max(1, int(cfg.get("MARKET_CANDLE_MAX_AGE_SEC", 900)))
    age_s = _latest_candle_age_seconds(candles)
    if age_s is None:
        if bool(cfg.get("ALLOW_TIMELESS_SCALP_CANDLES", False)):
            return True, "candle_time_unavailable"
        role_key = str(role or "data").upper()
        return False, f"MARKET_DATA_TIME_UNAVAILABLE_{role_key}"
    # Candle timestamps are bar-open times. Allow one extra bar length before
    # declaring live data stale.
    tf_sec = {"M1": 60, "M5": 300, "M15": 900, "H1": 3600}.get(str(timeframe).upper(), 60)
    if age_s > max_age + tf_sec:
        role_key = str(role or "data").upper()
        return False, f"MARKET_DATA_STALE_{role_key}_{round(age_s)}s"
    return True, "fresh"


def _execution_candles_fresh(candles: list, timeframe: str) -> tuple[bool, str]:
    return _scalp_candles_fresh(candles, timeframe, "execution")


# ═══════════════════════════════════════════════════════════════════════════════
# EMA HELPER + BIAS INFERENCE
# ═══════════════════════════════════════════════════════════════════════════════

def _calc_ema(values: list, period: int) -> list:
    """Calculate EMA over a list of floats."""
    if len(values) < period:
        return []
    return pd.Series(values).ewm(span=period, adjust=False).mean().tolist()


def infer_bias_from_ema_stack(candles: list) -> Optional[str]:
    """Infer directional bias from EMA 21/50/200 stack on higher timeframe."""
    if len(candles) < 200:
        return None

    closes = [float(c.get("close", 0.0)) for c in candles if c.get("close") is not None]
    if len(closes) < 200:
        return None

    ema21_series = _calc_ema(closes, 21)
    ema50_series = _calc_ema(closes, 50)
    ema200_series = _calc_ema(closes, 200)
    if not ema21_series or not ema50_series or not ema200_series:
        return None

    ema21  = ema21_series[-1]
    ema50  = ema50_series[-1]
    ema200 = ema200_series[-1]

    # Bias is determined by EMA stack order alone. Price relative to EMA21
    # is intentionally NOT required here: pullbacks below EMA21 in an uptrend
    # are precisely the entries Engine D targets, so blocking them defeats the purpose.
    if ema21 > ema50 > ema200:
        return "LONG"
    if ema21 < ema50 < ema200:
        return "SHORT"
    return None


# ═══════════════════════════════════════════════════════════════════════════════
# SESSION FILTER
# ═══════════════════════════════════════════════════════════════════════════════

def _current_utc_datetime() -> datetime:
    return datetime.now(timezone.utc)


def _normalize_session_mode(raw: Any) -> str:
    """Map common SESSION_MODE typos/aliases to canonical keys used by scalp_session_window."""
    mode = str(raw or "new_york").strip().lower()
    key = mode.replace("-", "_").replace(" ", "_").replace("/", "_")
    while "__" in key:
        key = key.replace("__", "_")
    aliases = {
        "ny": "new_york",
        "newyork": "new_york",
        "new_york": "new_york",
        "us": "new_york",
        "london_new_york": "london_ny",
        "londonny": "london_ny",
        "london_ny": "london_ny",
        "overlap": "london_ny",
        "lny": "london_ny",
        "asia_london_new_york": "asia_london_ny",
        "asia_london_ny": "asia_london_ny",
        "asia_lny": "asia_london_ny",
        "crypto_major": "asia_london_ny",
        "all_major": "asia_london_ny",
        "24_7": "all",
        "247": "all",
        "24x7": "all",
    }
    return aliases.get(key, key)


def _resolved_normalized_session_mode(
    cfg: dict,
    *,
    backtest: bool = False,
    asset_type: Optional[str] = None,
) -> str:
    mode_key = "BT_SESSION_MODE" if backtest else "SESSION_MODE"
    raw_mode = cfg.get(mode_key, cfg.get("SESSION_MODE", "new_york"))
    if str(raw_mode).strip().lower() in {"inherit", "default"}:
        raw_mode = cfg.get("SESSION_MODE", "new_york")
    if asset_type:
        asset_key = str(asset_type).strip().lower()
        by_asset_key = "BT_SESSION_MODE_BY_ASSET" if backtest else "SESSION_MODE_BY_ASSET"
        by_asset = cfg.get(by_asset_key, {}) or {}
        if isinstance(by_asset, dict):
            asset_mode = by_asset.get(asset_key)
            if asset_mode is not None and str(asset_mode).strip().lower() not in {"inherit", "default"}:
                raw_mode = asset_mode
        specific_key = f"{asset_key.upper()}_SESSION_MODE"
        specific_bt_key = f"BT_{asset_key.upper()}_SESSION_MODE"
        specific_mode = cfg.get(specific_bt_key if backtest else specific_key)
        if specific_mode is not None and str(specific_mode).strip().lower() not in {"inherit", "default"}:
            raw_mode = specific_mode
    return _normalize_session_mode(raw_mode)


def _scalp_cfg_lookup(
    cfg: dict,
    key: str,
    default: Any = None,
    *,
    asset_type: Optional[str] = None,
    score_group: Optional[str] = None,
) -> Any:
    group = str(score_group or "").strip()
    asset = str(asset_type or "").strip().lower()
    for map_key in (f"{key}_GROUP", f"{key}_BY_SCORE_GROUP"):
        values = cfg.get(map_key, {}) or {}
        if group and isinstance(values, dict) and group in values:
            return values[group]
    for map_key in (f"{key}_CLASS", f"{key}_BY_ASSET"):
        values = cfg.get(map_key, {}) or {}
        if asset and isinstance(values, dict) and asset in values:
            return values[asset]
    return cfg.get(key, default)


def _as_fraction(
    value: Any,
    default: float,
    *,
    clamp_minmax: tuple[float, float] = (0.01, 0.99),
) -> float:
    """Interpret YAML fractions: accept 0.70 or accidental 70 (→ 0.70). Clamp to range."""
    try:
        v = float(value)
    except (TypeError, ValueError):
        return default
    if v > 1.0:
        v = v / 100.0
    lo, hi = clamp_minmax
    return max(lo, min(hi, v))


def _as_value_area_pct(value: Any, default: float = 0.70) -> float:
    """Match volume_profile.compute_fixed_range_volume_profile VA clamp [0.1, 0.95]."""
    return _as_fraction(value, default, clamp_minmax=(0.1, 0.95))


def _merge_vp_aliases(vp: dict) -> dict:
    """Ensure poc/vah/val exist when optional alternate keys appear on upstream dicts."""
    def _first_num(keys: tuple[str, ...]) -> Optional[float]:
        for k in keys:
            rv = vp.get(k)
            if rv is None:
                continue
            try:
                return float(rv)
            except (TypeError, ValueError):
                continue
        return None

    out = dict(vp)
    if out.get("poc") is None:
        p = _first_num(("poc", "POC", "point_of_control"))
        if p is not None:
            out["poc"] = p
    if out.get("vah") is None:
        p = _first_num(("vah", "VAH", "va_high", "value_area_high", "value_high"))
        if p is not None:
            out["vah"] = p
    if out.get("val") is None:
        p = _first_num(("val", "VAL", "va_low", "value_area_low", "value_low"))
        if p is not None:
            out["val"] = p
    return out


def summarize_engine_d_scan(result: dict) -> dict:
    """Aggregate skip reasons vs signal funnel (gate_result, executable, fail_reasons).

    Rows in ``skipped`` are hard early exits (no setup, stale data, spread, umbrella session).
    Grade / RR / fee guard failures appear on ``signals`` with gate_result executable flags.
    """
    skipped = result.get("skipped") or []
    signals = result.get("signals") or []
    skipped_reason_counts = dict(Counter(s.get("reason", "unknown") for s in skipped).most_common())
    skipped_diag = Counter()
    for s in skipped:
        raw_diag = s.get("diagnostic_reasons") if isinstance(s, dict) else None
        if raw_diag is None and isinstance(s, dict):
            raw_diag = s.get("diagnostic_reason")
        if raw_diag is None:
            continue
        if isinstance(raw_diag, (list, tuple, set)):
            values = raw_diag
        else:
            values = [raw_diag]
        for item in values:
            if item:
                skipped_diag[str(item)] += 1
    gate_counts = dict(Counter(str(s.get("gate_result") or "UNKNOWN") for s in signals).most_common())
    executable_counts = {
        "executable_true": sum(1 for s in signals if s.get("executable")),
        "executable_false": sum(1 for s in signals if not s.get("executable")),
    }
    flat_fails = Counter()
    for s in signals:
        for fr in s.get("fail_reasons") or []:
            flat_fails[str(fr)] += 1
        for sw in s.get("soft_warnings") or []:
            flat_fails[f"warning:{sw}"] += 1

    soft_counts = Counter()
    for s in signals:
        for sw in s.get("soft_warnings") or []:
            soft_counts[str(sw)] += 1

    return {
        "counts": {
            "skipped_rows": len(skipped),
            "signal_candidates": len(signals),
            "sessions_active_len": len(result.get("sessions_active") or []),
        },
        "skipped_reason_counts": skipped_reason_counts,
        "skipped_diagnostic_reason_counts": dict(skipped_diag.most_common(40)),
        "signals_gate_result_counts": gate_counts,
        "signals_executable_breakdown": executable_counts,
        "signals_fail_and_warning_flat_counts": dict(flat_fails.most_common(40)),
        "signals_soft_warnings_counts": dict(soft_counts.most_common(20)),
        "top_level_reason": result.get("reason"),
        "session_label": result.get("session"),
    }


def _finalize_run_scalp_scan_result(
    *,
    signals: list,
    skipped: list,
    scanned: int,
    session_name: str,
    sessions_active: list | None = None,
    reason: Any = None,
) -> dict:
    """Attach ``diagnostic_summary`` to scan responses (including early-exit payloads)."""
    out: dict[str, Any] = {
        "signals": signals,
        "skipped": skipped,
        "scanned": scanned,
        "session": session_name,
        "sessions_active": list(sessions_active or []),
    }
    if reason is not None:
        out["reason"] = reason
    out["diagnostic_summary"] = summarize_engine_d_scan(out)
    return out


def _coerce_utc_datetime(value: Any) -> Optional[datetime]:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc) if value.tzinfo else value.replace(tzinfo=timezone.utc)
    # Try numeric conversion first — covers Python int/float AND numpy int64/float64
    try:
        ts = float(value)
        return datetime.fromtimestamp(ts, tz=timezone.utc)
    except (TypeError, ValueError, OverflowError, OSError):
        pass
    if isinstance(value, str):
        raw = value.strip()
        if raw.endswith("Z"):
            raw = raw[:-1] + "+00:00"
        try:
            parsed = datetime.fromisoformat(raw)
        except ValueError:
            return None
        return parsed.astimezone(timezone.utc) if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    return None


def get_sessions_for_time(asset_type: str = "forex", when: Any = None) -> list:
    """Return active session names using DST-aware local market clocks."""
    now_utc = (_coerce_utc_datetime(when) or _current_utc_datetime()).astimezone(timezone.utc)
    now_london_h = now_utc.astimezone(_TZ_LONDON).hour
    now_ny_h = now_utc.astimezone(_TZ_NEW_YORK).hour
    active = []

    local_defs = _CRYPTO_LOCAL_SESSIONS if asset_type == "crypto" else _FOREX_LOCAL_SESSIONS

    if asset_type == "crypto":
        asia_start, asia_end = _CRYPTO_LOCAL_SESSIONS["asia"]
        if asia_start <= now_utc.hour < asia_end:
            active.append("asia")

    lon_start, lon_end = local_defs["london"]
    ny_start, ny_end = local_defs["new_york"]
    if lon_start <= now_london_h < lon_end:
        active.append("london")
    if ny_start <= now_ny_h < ny_end:
        active.append("new_york")
    if active:
        return active
    return ["off_hours_crypto"] if asset_type == "crypto" else ["off_hours"]


def get_current_sessions() -> list:
    """Return active forex session names for the current time."""
    return get_sessions_for_time("forex")


def _grade_session_names(cfg: dict, asset_type: Optional[str] = None) -> list[str]:
    raw = cfg.get("GRADE_SESSIONS", ["london", "new_york"])
    by_asset = cfg.get("GRADE_SESSIONS_BY_ASSET", {}) or {}
    asset_key = str(asset_type or "").strip().lower()
    if asset_key and isinstance(by_asset, dict) and asset_key in by_asset:
        raw = by_asset.get(asset_key)
    if isinstance(raw, str):
        raw = [raw]
    return [str(s).strip().lower() for s in raw if str(s).strip()]


def get_grade_sessions_for_mode(asset_type: str = "forex", when: Any = None, *, backtest: bool = False) -> list:
    """Return session labels used by quality grading under the configured mode."""
    cfg = CONFIG.get("SCALP_ENGINE", {})
    mode = _resolved_normalized_session_mode(cfg, backtest=backtest, asset_type=asset_type)
    active = get_sessions_for_time(asset_type, when=when)
    grade_sessions = _grade_session_names(cfg, asset_type)
    if mode in {"all", "any", "disabled", "off"}:
        return []
    if mode in {"london_ny", "asia_london_ny"}:
        return [s for s in active if s in grade_sessions]
    if mode in grade_sessions:
        return [mode] if mode in active else []
    return [s for s in active if s in grade_sessions]


def is_valid_session(asset_type: str = "forex") -> tuple:
    """Check if current time is a valid session for the given asset type.

    Returns (valid: bool, session_name: str)
    """
    cfg = CONFIG.get("SCALP_ENGINE", {})
    if not cfg.get("SESSION_FILTER", True):
        return True, "all"

    now_utc = _current_utc_datetime()
    now_london_h = now_utc.astimezone(_TZ_LONDON).hour
    now_ny_h = now_utc.astimezone(_TZ_NEW_YORK).hour

    if asset_type == "crypto":
        asia_start, asia_end = _CRYPTO_LOCAL_SESSIONS["asia"]
        if asia_start <= now_utc.hour < asia_end:
            return True, "asia"
        lon_start, lon_end = _CRYPTO_LOCAL_SESSIONS["london"]
        if lon_start <= now_london_h < lon_end:
            return True, "london"
        ny_start, ny_end = _CRYPTO_LOCAL_SESSIONS["new_york"]
        if ny_start <= now_ny_h < ny_end:
            return True, "new_york"
        return True, "off_hours_crypto"

    sessions = get_current_sessions()
    for s in sessions:
        if s in ("london", "new_york"):
            return True, s
    return False, "off_hours"


def scalp_session_window(
    asset_type: str = "forex",
    when: Any = None,
    *,
    backtest: bool = False,
) -> tuple[bool, str]:
    """Config-driven scalp session gate with optional NY open cooldown."""
    cfg = CONFIG.get("SCALP_ENGINE", {})
    session_filter_key = "BT_SESSION_FILTER" if backtest else "SESSION_FILTER"
    if not cfg.get(session_filter_key, True):
        return True, "all"

    mode = _resolved_normalized_session_mode(cfg, backtest=backtest, asset_type=asset_type)
    if mode in {"all", "any", "disabled", "off"}:
        return True, "all"

    if mode not in (_CRYPTO_SESSIONS if asset_type == "crypto" else _SESSIONS):
        return False, "off_hours"

    current_dt = _coerce_utc_datetime(when) or _current_utc_datetime()
    current_utc = current_dt.astimezone(timezone.utc)
    current_london = current_utc.astimezone(_TZ_LONDON)
    current_ny = current_utc.astimezone(_TZ_NEW_YORK)

    if asset_type == "crypto":
        local_defs = _CRYPTO_LOCAL_SESSIONS
    else:
        local_defs = _FOREX_LOCAL_SESSIONS

    def _hour_in_range(dt_local: datetime, start_h: int, end_h: int) -> bool:
        m = dt_local.hour * 60 + dt_local.minute
        return (start_h * 60) <= m < (end_h * 60)

    if mode == "london":
        start_h, end_h = local_defs["london"]
        if not _hour_in_range(current_london, start_h, end_h):
            return False, "off_hours"
        skip_key_lon = "BT_LONDON_OPEN_SKIP_MINUTES" if backtest else "LONDON_OPEN_SKIP_MINUTES"
        skip_lon = max(0, int(cfg.get(skip_key_lon, cfg.get("LONDON_OPEN_SKIP_MINUTES", 20))))
        if skip_lon > 0:
            london_open_utc_minute = _london_cash_open_utc_minute_of_day(current_utc)
            now_utc_minute = current_utc.hour * 60 + current_utc.minute
            if london_open_utc_minute <= now_utc_minute < london_open_utc_minute + skip_lon:
                return False, "LONDON_OPEN_COOLDOWN"
        return True, "london"

    if mode == "new_york":
        start_h, end_h = local_defs["new_york"]
        in_window = _hour_in_range(current_ny, start_h, end_h)
        if not in_window:
            return False, "off_hours"
        skip_key = "BT_NY_OPEN_SKIP_MINUTES" if backtest else "NY_OPEN_SKIP_MINUTES"
        skip_minutes = max(0, int(cfg.get(skip_key, cfg.get("NY_OPEN_SKIP_MINUTES", 0))))
        ny_open_minute = 9 * 60 + 30  # NY cash open 09:30 local, DST-safe via timezone conversion above.
        now_ny_minute = current_ny.hour * 60 + current_ny.minute
        if ny_open_minute <= now_ny_minute < ny_open_minute + skip_minutes:
            return False, "NY_OPEN_COOLDOWN"
        return True, "new_york"

    if mode == "london_ny":
        lon_start, lon_end = local_defs["london"]
        ny_start, ny_end = local_defs["new_york"]
        in_london = _hour_in_range(current_london, lon_start, lon_end)
        in_ny = _hour_in_range(current_ny, ny_start, ny_end)
        if in_london or in_ny:
            # Block fresh entries during the NY cash-open caution window.
            skip_key = "BT_NY_OPEN_SKIP_MINUTES" if backtest else "NY_OPEN_SKIP_MINUTES"
            skip_minutes = max(0, int(cfg.get(skip_key, cfg.get("NY_OPEN_SKIP_MINUTES", 0))))
            if skip_minutes > 0:
                ny_open_minute = 9 * 60 + 30
                now_ny_minute = current_ny.hour * 60 + current_ny.minute
                if ny_open_minute <= now_ny_minute < ny_open_minute + skip_minutes:
                    return False, "NY_OPEN_COOLDOWN"
            # Block fresh entries during the London open caution window.
            skip_key_lon = "BT_LONDON_OPEN_SKIP_MINUTES" if backtest else "LONDON_OPEN_SKIP_MINUTES"
            skip_lon = max(0, int(cfg.get(skip_key_lon, cfg.get("LONDON_OPEN_SKIP_MINUTES", 20))))
            if skip_lon > 0:
                london_open_utc_minute = _london_cash_open_utc_minute_of_day(current_utc)
                now_utc_minute = current_utc.hour * 60 + current_utc.minute
                if london_open_utc_minute <= now_utc_minute < london_open_utc_minute + skip_lon:
                    return False, "LONDON_OPEN_COOLDOWN"
            return True, "london_ny"
        return False, "off_hours"

    if mode == "asia_london_ny" and asset_type == "crypto":
        asia_start, asia_end = local_defs["asia"]
        lon_start, lon_end = local_defs["london"]
        ny_start, ny_end = local_defs["new_york"]
        in_asia = _hour_in_range(current_utc, asia_start, asia_end)
        in_london = _hour_in_range(current_london, lon_start, lon_end)
        in_ny = _hour_in_range(current_ny, ny_start, ny_end)
        if in_asia or in_london or in_ny:
            skip_key = "BT_NY_OPEN_SKIP_MINUTES" if backtest else "NY_OPEN_SKIP_MINUTES"
            skip_minutes = max(0, int(cfg.get(skip_key, cfg.get("NY_OPEN_SKIP_MINUTES", 0))))
            if in_ny and skip_minutes > 0:
                ny_open_minute = 9 * 60 + 30
                now_ny_minute = current_ny.hour * 60 + current_ny.minute
                if ny_open_minute <= now_ny_minute < ny_open_minute + skip_minutes:
                    return False, "NY_OPEN_COOLDOWN"
            skip_key_lon = "BT_LONDON_OPEN_SKIP_MINUTES" if backtest else "LONDON_OPEN_SKIP_MINUTES"
            skip_lon = max(0, int(cfg.get(skip_key_lon, cfg.get("LONDON_OPEN_SKIP_MINUTES", 20))))
            if in_london and skip_lon > 0:
                london_open_utc_minute = _london_cash_open_utc_minute_of_day(current_utc)
                now_utc_minute = current_utc.hour * 60 + current_utc.minute
                if london_open_utc_minute <= now_utc_minute < london_open_utc_minute + skip_lon:
                    return False, "LONDON_OPEN_COOLDOWN"
            return True, "asia_london_ny"
        return False, "off_hours"

    if mode == "asia" and asset_type == "crypto":
        start_h, end_h = local_defs["asia"]
        if _hour_in_range(current_utc, start_h, end_h):
            return True, "asia"
        return False, "off_hours"

    return False, "off_hours"


# ═══════════════════════════════════════════════════════════════════════════════
# SPREAD FILTER
# ═══════════════════════════════════════════════════════════════════════════════

def check_spread(symbol_info: dict, asset_type: str, display: str = "") -> tuple:
    """Validate spread within limits.  Returns (ok: bool, spread_pips: float).

    Forex: spread in pips via point/pip_size conversion (standard).
    Index/Stock/Commodity: spread in raw MT5 integer points — the pip formula
      produces nonsense values (e.g. 47200 pips) for these instruments because
      their point size is tiny but has no relationship to the forex pip concept.
    Crypto: always passes (spread not applicable to perpetual futures).
    """
    cfg = CONFIG.get("SCALP_ENGINE", {})
    spread_raw = symbol_info.get("spread", 0)
    point = symbol_info.get("point", 0.00001)

    if asset_type == "crypto":
        return True, 0.0

    if asset_type in ("index", "stock", "commodity"):
        # Use raw point count from MT5 — meaningful unit for these instruments.
        # MAX_SPREAD_POINTS: index=100, stock=50, commodity=30 (configurable).
        # Per-symbol overrides via MAX_SPREAD_POINTS_OVERRIDES (e.g. XAU/USD=60).
        max_points_cfg = cfg.get("MAX_SPREAD_POINTS", {})
        defaults = {"index": 100, "stock": 50, "commodity": 30}
        per_symbol_overrides = cfg.get("MAX_SPREAD_POINTS_OVERRIDES", {})
        max_pts = per_symbol_overrides.get(display) if display else None
        if max_pts is None:
            max_pts = max_points_cfg.get(asset_type, defaults.get(asset_type, 100))
        max_pts = int(max_pts)
        ok = spread_raw <= max_pts
        return ok, float(spread_raw)

    # Forex: convert to pips via standard formula
    max_spreads = cfg.get("MAX_SPREAD_PIPS", {})
    spread_price = spread_raw * point
    digits = symbol_info.get("digits", 5)
    if digits == 3:
        # JPY pairs: pip = 0.01 (point is typically 0.001)
        pip_size = 0.01
    elif digits >= 4:
        pip_size = point * 10
    else:
        pip_size = point
    spread_pips = spread_price / pip_size if pip_size > 0 else 0
    max_spread = max_spreads.get(asset_type, max_spreads.get("forex", 4))
    ok = spread_pips <= max_spread
    return ok, round(spread_pips, 2)


# ═══════════════════════════════════════════════════════════════════════════════
# PILLAR 1 — VOLUME PROFILE: Market State + Location
# ═══════════════════════════════════════════════════════════════════════════════

def _build_volume_profile(
    candles: list,
    asset_type: Optional[str] = None,
    score_group: Optional[str] = None,
) -> dict:
    """Compute Volume Profile over the given candles.

    Returns {poc, vah, val, lvn_levels[], distribution[], balance_ratio}.
    Uses volume_profile.compute_fixed_range_volume_profile when available,
    otherwise falls back to a lightweight internal histogram.
    """
    cfg = CONFIG.get("SCALP_ENGINE", {})
    num_bins = int(_scalp_cfg_lookup(cfg, "VP_BINS", 100, asset_type=asset_type, score_group=score_group))
    va_pct = _as_value_area_pct(
        _scalp_cfg_lookup(
            cfg,
            "VP_VALUE_AREA_PCT",
            cfg.get("VP_VA_PCT", 0.70),
            asset_type=asset_type,
            score_group=score_group,
        ),
        0.70,
    )
    lvn_factor = _as_fraction(
        _scalp_cfg_lookup(
            cfg,
            "VP_LVN_THRESHOLD",
            cfg.get("VP_LVN_FACTOR", 0.30),
            asset_type=asset_type,
            score_group=score_group,
        ),
        0.30,
    )

    if len(candles) < 20:
        return {"valid": False, "reason": "insufficient_candles_for_vp"}

    def _internal_profile() -> dict:
        highs = [c["high"] for c in candles]
        lows = [c["low"] for c in candles]
        price_min = min(lows)
        price_max = max(highs)
        price_range = price_max - price_min
        if price_range <= 0:
            return {"valid": False, "reason": "zero_price_range"}

        bin_size = price_range / num_bins
        bins = [0.0] * num_bins

        for c in candles:
            vol = float(c.get("vol", 0) or 0)
            if vol <= 0:
                vol = max(float(c["high"]) - float(c["low"]), 1e-10)
            lo = c["low"]
            hi = c["high"]
            lo_bin = max(0, int((lo - price_min) / bin_size))
            hi_bin = min(num_bins - 1, int((hi - price_min) / bin_size))
            bars_touched = hi_bin - lo_bin + 1
            vol_per_bin = vol / bars_touched if bars_touched > 0 else vol
            for b in range(lo_bin, hi_bin + 1):
                bins[b] += vol_per_bin

        total_vol = sum(bins)
        if total_vol <= 0:
            return {"valid": False, "reason": "no_volume"}

        poc_bin = max(range(num_bins), key=lambda i: bins[i])
        poc = price_min + (poc_bin + 0.5) * bin_size

        target_vol = total_vol * va_pct
        cum = bins[poc_bin]
        lo_idx = poc_bin
        hi_idx = poc_bin
        while cum < target_vol and (lo_idx > 0 or hi_idx < num_bins - 1):
            add_lo = bins[lo_idx - 1] if lo_idx > 0 else 0
            add_hi = bins[hi_idx + 1] if hi_idx < num_bins - 1 else 0
            if add_lo >= add_hi and lo_idx > 0:
                lo_idx -= 1
                cum += bins[lo_idx]
            elif hi_idx < num_bins - 1:
                hi_idx += 1
                cum += bins[hi_idx]
            else:
                lo_idx -= 1
                cum += bins[lo_idx]

        val = price_min + lo_idx * bin_size
        vah = price_min + (hi_idx + 1) * bin_size

        lvn_threshold = bins[poc_bin] * lvn_factor
        lvn_levels = []
        for i in range(num_bins):
            if bins[i] < lvn_threshold and lo_idx <= i <= hi_idx:
                lvn_levels.append(round(price_min + (i + 0.5) * bin_size, 6))

        distribution = [round(b, 2) for b in bins]
        result = {
            "valid": True,
            "poc": round(poc, 6),
            "vah": round(vah, 6),
            "val": round(val, 6),
            "lvn_levels": lvn_levels,
            "distribution": distribution,
            "total_volume": round(total_vol, 2),
            "session_high": round(price_max, 6),
            "session_low": round(price_min, 6),
        }
        result["balance_ratio"] = _calc_balance_ratio(result)
        return result

    try:
        from volume_profile import compute_fixed_range_volume_profile

        vp = compute_fixed_range_volume_profile(candles, bins=num_bins, value_area_pct=va_pct)
        vp = _merge_vp_aliases(dict(vp if vp else {}))
        if vp.get("profile_valid") and vp.get("poc") is not None:
            result = dict(vp)
            result["valid"] = True
            if not result.get("lvn_levels") or not result.get("distribution"):
                supplemental = _internal_profile()
                if supplemental.get("valid"):
                    result.setdefault("lvn_levels", supplemental.get("lvn_levels", []))
                    result.setdefault("distribution", supplemental.get("distribution", []))
                    result.setdefault("session_high", supplemental.get("session_high"))
                    result.setdefault("session_low", supplemental.get("session_low"))
            result.setdefault("lvn_levels", [])
            result["balance_ratio"] = _calc_balance_ratio(result)
            return result
    except Exception as exc:
        log.debug("[SCALP] volume_profile module error, using internal VP: %s", exc)

    return _internal_profile()


def _trade_bucket_session_id(reference_ts=None) -> str | None:
    if reference_ts is None:
        return None
    dt = _coerce_utc_datetime(reference_ts)
    return dt.strftime("%Y-%m-%d") if dt else None


def _trade_bucket_max_last_ts(reference_ts=None, require_fresh: bool = True) -> float | None:
    if reference_ts is None or require_fresh:
        return None
    dt = _coerce_utc_datetime(reference_ts)
    return dt.timestamp() if dt else None


def _build_trade_bucket_volume_profile(display: str, reference_ts=None, require_fresh: bool = True) -> dict:
    """Build crypto VP from live Binance aggregate-trade price buckets."""
    cfg = CONFIG.get("SCALP_ENGINE", {})
    min_buckets = int(cfg.get("TRADE_BUCKET_MIN_LEVELS", 8))
    min_volume = float(cfg.get("TRADE_BUCKET_MIN_VOLUME", 0.0))
    max_age_sec = int(cfg.get("TRADE_BUCKET_MAX_AGE_SEC", 300))
    va_pct = _as_value_area_pct(
        _scalp_cfg_lookup(cfg, "VP_VALUE_AREA_PCT", cfg.get("VP_VA_PCT", 0.70), asset_type="crypto"),
        0.70,
    )
    lvn_factor = _as_fraction(
        _scalp_cfg_lookup(cfg, "VP_LVN_THRESHOLD", cfg.get("VP_LVN_FACTOR", 0.30), asset_type="crypto"),
        0.30,
    )
    symbol = str(display or "").replace("/", "").upper()
    try:
        from athena.microstructure.trade_bucket_store import query_session_buckets
        from volume_profile import compute_bucketed_volume_profile

        rows = query_session_buckets(
            symbol,
            exchange="binance",
            session_id=_trade_bucket_session_id(reference_ts),
            min_last_ts=(_time.time() - max_age_sec) if require_fresh else None,
            max_last_ts=_trade_bucket_max_last_ts(reference_ts, require_fresh=require_fresh),
        )
        if len(rows) < min_buckets:
            return {"valid": False, "reason": "insufficient_trade_buckets", "bucket_count": len(rows)}
        vp = compute_bucketed_volume_profile(rows, value_area_pct=va_pct, lvn_threshold=lvn_factor)
        if not vp.get("profile_valid"):
            return {"valid": False, "reason": "trade_bucket_profile_invalid", "bucket_count": len(rows)}
        if float(vp.get("total_volume") or 0.0) < min_volume:
            return {"valid": False, "reason": "trade_bucket_volume_too_low", "bucket_count": len(rows)}
        out = dict(vp)
        out["valid"] = True
        out["volume_source"] = "binance_aggtrade"
        out["bucket_count"] = len(rows)
        out["balance_ratio"] = _calc_balance_ratio(out)
        return out
    except Exception as exc:
        log.debug("[SCALP] trade bucket VP unavailable for %s: %s", display, exc)
        return {"valid": False, "reason": "trade_bucket_error"}


def _calc_balance_ratio(vp: dict) -> float | None:
    """Ratio of value area width to total range.  High -> balanced, low -> trending.

    Returns ``None`` when neither session bounds nor a usable vah/val range
    are available, so the caller can classify the market using an alternative
    signal (e.g. H1 ADX) instead of silently defaulting to 'balance'.
    """
    vah = vp.get("vah", 0)
    val = vp.get("val", 0)
    session_high = vp.get("session_high")
    session_low = vp.get("session_low")
    if session_high is not None and session_low is not None:
        total_range = float(session_high) - float(session_low)
        if total_range > 0:
            va_width = float(vah) - float(val) if vah and val else 0.0
            return round(max(0.0, min(1.0, va_width / total_range)), 3)
    # Fallback: use LVN count as a heuristic — many LVNs suggest imbalance
    # (price traversed multiple thin zones).  Two or more LVNs -> ratio 0.30.
    lvn_count = len(vp.get("lvn_levels", []))
    if vah and val and float(vah) > float(val):
        va_width = float(vah) - float(val)
        # With no session range we cannot compute the true ratio.
        # Use LVN count: >=2 LVNs in a short profile → likely imbalance.
        if lvn_count >= 2:
            return 0.30
        # Single/no LVN but we have vah/val — estimate conservatively at 0.55.
        # This is above BALANCE_THRESHOLD (0.40) so it leans toward balance,
        # but is no longer the automatic 0.80 that blocked all imbalance paths.
        return 0.55
    return None


def _classify_market_state(vp: dict) -> str:
    """Classify market as 'balance' or 'imbalance' from VP shape.

    When the balance ratio is ``None`` (session bounds unavailable), default
    to 'balance' — the safer assumption — but log a warning so the gap is
    visible in diagnostics.
    """
    br = vp.get("balance_ratio")
    cfg = CONFIG.get("SCALP_ENGINE", {})
    threshold = _as_fraction(cfg.get("BALANCE_THRESHOLD", 0.40), 0.40)
    if br is None:
        log.debug("[SCALP] balance_ratio unavailable — defaulting to 'balance'")
        return "balance"
    return "balance" if br >= threshold else "imbalance"


def _locate_price_vs_vp(
    price: float,
    vp: dict,
    atr_m15: float = 0.0,
    asset_type: Optional[str] = None,
    score_group: Optional[str] = None,
) -> dict:
    """Determine price location relative to VP levels.

    Returns {location: str, nearest_level: float, distance_pct: float}
    Locations: 'at_vah', 'at_val', 'at_poc', 'at_lvn', 'inside_va', 'outside_va'

    When *atr_m15* > 0 and VP_PROXIMITY_USE_ATR is true, proximity is measured
    as ``ATR_M15 * VP_PROXIMITY_ATR_K`` instead of a fixed percentage of price.
    This prevents forex pairs (e.g. EUR/USD) from having a 32-pip proximity band
    that matches almost any random close to a VP level.
    """
    cfg = CONFIG.get("SCALP_ENGINE", {})
    proximity_pct = float(cfg.get("VP_PROXIMITY_PCT", 0.15)) / 100.0

    use_atr_proximity = cfg.get("VP_PROXIMITY_USE_ATR", True) and atr_m15 > 0
    if use_atr_proximity:
        atr_k = float(
            _scalp_cfg_lookup(
                cfg,
                "VP_PROXIMITY_ATR_K",
                0.20,
                asset_type=asset_type,
                score_group=score_group,
            )
        )
        atr_proximity = atr_m15 * atr_k

    poc = vp.get("poc", 0)
    vah = vp.get("vah", 0)
    val = vp.get("val", 0)
    lvn_levels = vp.get("lvn_levels", [])

    def _near(level):
        if not level:
            return False
        if use_atr_proximity:
            return abs(price - level) < atr_proximity
        return abs(price - level) / level < proximity_pct

    def _dist(level):
        return abs(price - level) if level else float("inf")

    # Collect all nearby named levels and pick the closest one to avoid
    # check-order tiebreak bias (e.g. VAH winning over a nearer POC).
    candidates = []
    if _near(vah):
        candidates.append(("at_vah", vah, _dist(vah)))
    if _near(val):
        candidates.append(("at_val", val, _dist(val)))
    if _near(poc):
        candidates.append(("at_poc", poc, _dist(poc)))
    for lvn in lvn_levels:
        if _near(lvn):
            candidates.append(("at_lvn", lvn, _dist(lvn)))

    def _pct(level):
        return round(abs(price - level) / level * 100, 3) if level else 0.0

    if candidates:
        label, level, dist = min(candidates, key=lambda t: t[2])
        return {"location": label, "nearest_level": level, "distance_pct": _pct(level)}

    if val <= price <= vah:
        return {"location": "inside_va", "nearest_level": poc, "distance_pct": _pct(poc)}

    above_va = price > vah
    return {
        "location": "outside_va",
        "above_va": above_va,
        "nearest_level": vah if above_va else val,
        "distance_pct": _pct(vah if above_va else val),
    }


# ═══════════════════════════════════════════════════════════════════════════════
# PILLAR 2 — AGGRESSION: Absorption + CVD + AAA
# ═══════════════════════════════════════════════════════════════════════════════

def _check_absorption(
    candles: list,
    asset_type: Optional[str] = None,
    score_group: Optional[str] = None,
) -> dict:
    """Detect absorption candles (high volume, small price move).

    Uses indicators.detect_absorption() when available, otherwise internal logic.
    """
    cfg = CONFIG.get("SCALP_ENGINE", {})
    vol_mult = float(
        _scalp_cfg_lookup(
            cfg,
            "ABSORPTION_VOL_MULT",
            cfg.get("ABS_VOL_MULT", 2.0),
            asset_type=asset_type,
            score_group=score_group,
        )
    )
    max_move = float(
        _scalp_cfg_lookup(
            cfg,
            "ABSORPTION_MAX_MOVE_ATR",
            cfg.get("ABS_MAX_MOVE_ATR", 0.30),
            asset_type=asset_type,
            score_group=score_group,
        )
    )
    sma_period = int(cfg.get("ABSORPTION_SMA_PERIOD", cfg.get("ABS_SMA_PERIOD", 20)))
    recent_window = max(1, int(cfg.get("ABSORPTION_RECENT_BARS", 5)))

    try:
        from indicators import detect_absorption
        rows = detect_absorption(candles, vol_mult=vol_mult, max_move_atr=max_move, sma_period=sma_period)
        hits = []
        start_idx = max(0, len(rows) - recent_window)
        for idx, row in enumerate(rows[start_idx:], start=start_idx):
            if row.get("absorbed"):
                hits.append({"index": idx, **row})
        return {"detected": len(hits) > 0, "count": len(hits), "bars": hits}
    except Exception as exc:
        log.debug("[SCALP] indicators.detect_absorption error: %s", exc)

    # Internal fallback
    if len(candles) < sma_period + 1:
        return {"detected": False, "count": 0, "bars": []}

    vols = [float(c.get("vol", 0) or 0) for c in candles]
    sma_vol = sum(vols[-sma_period:]) / sma_period if sma_period > 0 else 1

    atr_vals = []
    for c in candles[-sma_period:]:
        atr_vals.append(c["high"] - c["low"])
    avg_atr = sum(atr_vals) / len(atr_vals) if atr_vals else 1

    hits = []
    for i in range(-min(recent_window, len(candles)), 0):
        c = candles[i]
        vol = float(c.get("vol", 0) or 0)
        move = abs(c["close"] - c["open"])
        if vol >= sma_vol * vol_mult and move <= avg_atr * max_move:
            hits.append({"index": len(candles) + i, "vol": vol, "move": move})

    return {"detected": len(hits) > 0, "count": len(hits), "bars": hits}


def _check_cvd(
    candles: list,
    asset_type: Optional[str] = None,
    score_group: Optional[str] = None,
) -> dict:
    """Compute CVD direction from recent candles.

    Uses indicators.calc_cvd() when available, otherwise internal approximation.
    Returns {direction: 'LONG'|'SHORT'|None, cvd_value, cvd_slope}
    """
    cfg = CONFIG.get("SCALP_ENGINE", {})
    smooth_period = int(cfg.get("CVD_SMOOTH_PERIOD", 5))
    min_slope = float(
        _scalp_cfg_lookup(
            cfg,
            "CVD_MIN_SLOPE",
            0.0,
            asset_type=asset_type,
            score_group=score_group,
        )
    )

    try:
        from indicators import calc_cvd
        result = calc_cvd(candles, smooth_period=smooth_period)
        cvd_raw = (result.get("cvd") or []) if result else []
        slope_series = cvd_raw or ((result.get("smoothed_delta") or []) if result else [])
        if slope_series and len(slope_series) >= 6:
            slope = slope_series[-1] - slope_series[-6]
            direction = (
                "LONG" if slope > min_slope
                else "SHORT" if slope < -min_slope
                else None
            )
            cvd_val = round(cvd_raw[-1], 2) if cvd_raw else 0
            return {"direction": direction, "cvd_value": cvd_val, "cvd_slope": round(slope, 4)}
    except Exception as exc:
        log.debug("[SCALP] indicators.calc_cvd error: %s", exc)

    # Internal fallback — candle-based buy/sell approximation
    if len(candles) < 10:
        return {"direction": None, "cvd_value": 0, "cvd_slope": 0}

    cvd = 0.0
    cvd_series = []
    for c in candles[-20:]:
        vol = float(c.get("vol", 0) or 0)
        rng = c["high"] - c["low"]
        if rng > 0:
            buy_pct = (c["close"] - c["low"]) / rng
        else:
            buy_pct = 0.5
        cvd += vol * (2 * buy_pct - 1)
        cvd_series.append(cvd)

    slope = cvd_series[-1] - cvd_series[-6] if len(cvd_series) >= 6 else 0
    direction = (
        "LONG" if slope > min_slope
        else "SHORT" if slope < -min_slope
        else None
    )
    return {"direction": direction, "cvd_value": round(cvd, 2), "cvd_slope": round(slope, 2)}


def _check_trade_bucket_cvd(display: str, reference_ts=None, require_fresh: bool = True) -> dict:
    """Compute live crypto CVD from Binance aggregate-trade buckets."""
    cfg = CONFIG.get("SCALP_ENGINE", {})
    max_age_sec = int(cfg.get("TRADE_BUCKET_MAX_AGE_SEC", 300))
    min_buckets = int(cfg.get("TRADE_BUCKET_MIN_LEVELS", 8))
    symbol = str(display or "").replace("/", "").upper()
    try:
        from athena.microstructure.trade_bucket_store import query_session_buckets

        rows = query_session_buckets(
            symbol,
            exchange="binance",
            session_id=_trade_bucket_session_id(reference_ts),
            min_last_ts=(_time.time() - max_age_sec) if require_fresh else None,
            max_last_ts=_trade_bucket_max_last_ts(reference_ts, require_fresh=require_fresh),
        )
        if len(rows) < min_buckets:
            return {"direction": None, "cvd_value": 0, "cvd_slope": 0, "source": "unavailable"}
        # Sort by last_ts (time-ordered) so slope = recent flow - early flow.
        # Price-bucket sorting produces a spatial bias: high-price bins always
        # accumulate more buy delta in a rising market, giving a false positive.
        rows = sorted(rows, key=lambda r: float(r.get("last_ts") or r.get("price_bucket") or 0.0))
        deltas = [float(r.get("delta") or 0.0) for r in rows]
        cvd = sum(deltas)
        slope = (sum(deltas[-3:]) - sum(deltas[:3])) if len(deltas) >= 6 else cvd
        direction = "LONG" if slope > 0 else "SHORT" if slope < 0 else None
        return {
            "direction": direction,
            "cvd_value": round(cvd, 2),
            "cvd_slope": round(slope, 4),
            "source": "binance_aggtrade",
            "bucket_count": len(rows),
        }
    except Exception as exc:
        log.debug("[SCALP] trade bucket CVD unavailable for %s: %s", display, exc)
        return {"direction": None, "cvd_value": 0, "cvd_slope": 0, "source": "error"}


def _engine_d_aggression_fidelity(
    absorption: dict,
    cvd: dict,
    aaa: dict,
    vwap: dict,
    setup_direction: Optional[str] = None,
) -> dict:
    """Expose whether Engine D aggression evidence is true flow or proxy data."""
    cvd_source_raw = str((cvd or {}).get("source") or "candles").strip().lower()
    source_aliases = {
        "binance_aggtrade": "binance_aggtrade",
        "candles": "candle_proxy",
        "candle": "candle_proxy",
        "disabled": "disabled",
        "error": "error",
        "unavailable": "unavailable",
        "mt5_tick": "mt5_tick_proxy",
        "range_proxy": "range_proxy",
    }
    aggression_source = source_aliases.get(cvd_source_raw, f"{cvd_source_raw}_proxy")
    source_is_proxy = aggression_source != "binance_aggtrade"

    setup_dir = str(setup_direction or "").upper()
    cvd_dir = str((cvd or {}).get("direction") or "").upper()
    aaa_dir = str((aaa or {}).get("direction") or "").upper()
    vwap_dir = str((vwap or {}).get("lean") or "").upper()

    cvd_aligned = bool(cvd_dir) and (not setup_dir or cvd_dir == setup_dir)
    aaa_complete = bool((aaa or {}).get("complete"))
    aaa_aligned = aaa_complete and (not setup_dir or not aaa_dir or aaa_dir == setup_dir)
    absorption_confirmed = bool((absorption or {}).get("detected"))
    vwap_aligned = bool(vwap_dir) and (not setup_dir or vwap_dir == setup_dir)

    aggression_confirmed = bool(absorption_confirmed or cvd_aligned or aaa_aligned)
    strict_fabio_pass = bool(
        aggression_source == "binance_aggtrade"
        and not source_is_proxy
        and (cvd_aligned or aaa_aligned)
    )

    return {
        "aggression_source": aggression_source,
        "aggression_source_raw": cvd_source_raw,
        "aggression_source_is_proxy": source_is_proxy,
        "aggression_confirmed": aggression_confirmed,
        "strict_fabio_pass": strict_fabio_pass,
        "aggression_components": {
            "absorption_confirmed": absorption_confirmed,
            "cvd_aligned": cvd_aligned,
            "aaa_aligned": aaa_aligned,
            "vwap_aligned": vwap_aligned,
        },
    }


def _engine_d_strict_fabio_shadow(
    *,
    market_state: str,
    price_loc: dict,
    setup: dict,
    aggression_fidelity: dict,
    current_gate_result: Optional[str] = None,
) -> dict:
    """Strict three-pillar Fabio check used for diagnostics and optional gating."""
    setup_type = str((setup or {}).get("setup_type") or "").lower()
    location = str((price_loc or {}).get("location") or "").lower()
    market = str(market_state or "").lower()

    if setup_type == "mean_reversion":
        market_ok = market == "balance"
        location_ok = location in {"at_vah", "at_val", "outside_va"}
    elif setup_type == "trend_continuation":
        market_ok = market == "imbalance"
        location_ok = location == "at_lvn"
    elif setup_type == "trend_extension":
        market_ok = market == "imbalance"
        location_ok = location == "outside_va"
    else:
        market_ok = False
        location_ok = False

    aggression_ok = bool((aggression_fidelity or {}).get("aggression_confirmed"))
    pillars = {
        "market_state": market_ok,
        "location": location_ok,
        "aggression": aggression_ok,
    }
    missing = [name for name, ok in pillars.items() if not ok]
    strict_pass = not missing
    reason = "strict_pass" if strict_pass else "missing_" + "_".join(missing)

    current = str(current_gate_result or "UNKNOWN").upper()
    if strict_pass:
        if current == "NO_SETUP":
            status = "strict_pass_current_no_setup"
        elif current == "BLOCKED":
            status = "strict_pass_current_blocked"
        else:
            status = f"current_{current.lower()}_strict_pass"
    else:
        status = f"current_{current.lower()}_strict_fail"

    return {
        "strict_fabio_pass": strict_pass,
        "strict_fabio_reason": reason,
        "strict_fabio_missing_pillars": missing,
        "strict_fabio_pillars": pillars,
        "current_vs_strict_status": status,
    }


def _engine_d_source_fidelity(source: Any, *, domain: str) -> dict:
    raw = str(source or "unknown").strip().lower()
    aliases = {
        "binance_aggtrade": "binance_aggtrade",
        "trade_buckets": "binance_aggtrade",
        "candle": "candles",
        "candle_volume": "candle_volume",
        "candles": "candles",
        "range_proxy": "range_proxy",
        "mt5_tick": "mt5_tick",
        "mt5_tick_proxy": "mt5_tick",
        "binance_ws": "binance_candle",
        "binance_candle": "binance_candle",
        "eodhd_1m": "eodhd_candle_volume",
        "eodhd_5m": "eodhd_candle_volume",
        "eodhd_1h": "eodhd_candle_volume",
        "eodhd_hist": "eodhd_candle_volume",
        "ws_tick": "ws_tick_volume",
        "disabled": "disabled",
        "error": "error",
        "unavailable": "unavailable",
        "unknown": "unknown",
    }
    normalized = aliases.get(raw, raw)
    real_trade_flow = normalized == "binance_aggtrade"
    real_volume_proxy = normalized in {
        "candle_volume",
        "binance_candle",
        "eodhd_candle_volume",
        "ws_tick_volume",
    }
    range_or_tick_proxy = normalized in {"range_proxy", "mt5_tick", "candles"}
    unavailable = normalized in {"disabled", "error", "unavailable", "unknown"}

    if real_trade_flow:
        fidelity = "real_trade_bucket"
    elif real_volume_proxy:
        fidelity = f"{domain}_candle_volume_proxy"
    elif range_or_tick_proxy:
        fidelity = f"{domain}_{normalized}_proxy"
    else:
        fidelity = f"{domain}_proxy"

    return {
        "raw_source": raw,
        "source": normalized,
        "fidelity": fidelity,
        "uses_real_trade_buckets": real_trade_flow,
        "uses_real_order_flow": real_trade_flow,
        "is_proxy": not real_trade_flow,
        "is_unavailable": unavailable,
    }


def _engine_d_data_fidelity(
    *,
    vp: dict,
    cvd: dict,
    absorption: dict,
    asset_type: str,
    structure_volume_source: Any,
    execution_volume_source: Any,
    active_profile_anchor: str,
) -> dict:
    """Report-only source truth for VP, CVD, and aggression evidence."""
    vp_source = (vp or {}).get("volume_source") or structure_volume_source or "unknown"
    cvd_source = (cvd or {}).get("source") or "candles"
    absorption_source = execution_volume_source or "unknown"

    vp_fidelity = _engine_d_source_fidelity(vp_source, domain="vp")
    cvd_fidelity = _engine_d_source_fidelity(cvd_source, domain="cvd")
    absorption_fidelity = _engine_d_source_fidelity(absorption_source, domain="absorption")

    notes: list[str] = []
    if vp_fidelity["is_proxy"]:
        notes.append(f"vp:{vp_fidelity['fidelity']}")
    if cvd_fidelity["is_proxy"]:
        notes.append(f"cvd:{cvd_fidelity['fidelity']}")
    if absorption_fidelity["is_proxy"]:
        notes.append(f"absorption:{absorption_fidelity['fidelity']}")

    aggression_real_order_flow = bool(cvd_fidelity["uses_real_order_flow"])
    return {
        "report_only": True,
        "asset_type": str(asset_type or "unknown"),
        "active_profile_anchor": active_profile_anchor,
        "vp_source": vp_fidelity["source"],
        "vp_source_raw": vp_fidelity["raw_source"],
        "vp_fidelity": vp_fidelity["fidelity"],
        "vp_is_proxy": vp_fidelity["is_proxy"],
        "vp_uses_real_trade_buckets": vp_fidelity["uses_real_trade_buckets"],
        "vp_bucket_count": (vp or {}).get("bucket_count"),
        "structure_volume_source": structure_volume_source,
        "cvd_source": cvd_fidelity["source"],
        "cvd_source_raw": cvd_fidelity["raw_source"],
        "cvd_fidelity": cvd_fidelity["fidelity"],
        "cvd_is_proxy": cvd_fidelity["is_proxy"],
        "cvd_uses_real_trade_buckets": cvd_fidelity["uses_real_trade_buckets"],
        "cvd_bucket_count": (cvd or {}).get("bucket_count"),
        "absorption_source": absorption_fidelity["source"],
        "absorption_source_raw": absorption_fidelity["raw_source"],
        "absorption_fidelity": absorption_fidelity["fidelity"],
        "absorption_is_proxy": absorption_fidelity["is_proxy"],
        "absorption_detected": bool((absorption or {}).get("detected")),
        "execution_volume_source": execution_volume_source,
        "aggression_uses_real_order_flow": aggression_real_order_flow,
        "aggression_proxy_components": notes,
        "notes": notes,
    }


def _stock_real_volume_fail_reasons(data_fidelity: dict, *volume_sources: Any) -> list[str]:
    reasons: list[str] = []
    normalized_sources = {str(src or "").strip().lower() for src in volume_sources}
    if _EODHD_STOCK_SUFFIX_UNMAPPED_SOURCE in normalized_sources:
        reasons.append(_EODHD_STOCK_SUFFIX_UNMAPPED_SOURCE)
    if (data_fidelity or {}).get("vp_is_proxy") or (data_fidelity or {}).get("absorption_is_proxy"):
        reasons.append("real_volume_required_for_stock")
    return reasons


def _candle_float(candle: dict, key: str) -> Optional[float]:
    try:
        return float((candle or {}).get(key))
    except (TypeError, ValueError):
        return None


def _profile_anchor_window_summary(
    candles: list,
    *,
    label: str,
    reason: str | None = None,
) -> dict:
    if not candles:
        return {"label": label, "valid": False, "reason": reason or "no_candles", "bars": 0}
    highs = [_candle_float(c, "high") for c in candles]
    lows = [_candle_float(c, "low") for c in candles]
    closes = [_candle_float(c, "close") for c in candles]
    vols = [_candle_float(c, "vol") for c in candles]
    highs = [v for v in highs if v is not None]
    lows = [v for v in lows if v is not None]
    closes = [v for v in closes if v is not None]
    vols = [v for v in vols if v is not None]
    start_ts = _coerce_utc_datetime((candles[0] or {}).get("time"))
    end_ts = _coerce_utc_datetime((candles[-1] or {}).get("time"))
    out = {
        "label": label,
        "valid": True,
        "bars": len(candles),
        "start_time": start_ts.isoformat() if start_ts else None,
        "end_time": end_ts.isoformat() if end_ts else None,
        "high": round(max(highs), 6) if highs else None,
        "low": round(min(lows), 6) if lows else None,
        "first_close": round(closes[0], 6) if closes else None,
        "last_close": round(closes[-1], 6) if closes else None,
        "total_volume": round(sum(vols), 2) if vols else None,
    }
    if reason:
        out["reason"] = reason
    return out


def _prior_session_anchor_candidate(candles: list, min_bars: int = 20) -> dict:
    parsed = []
    for candle in candles or []:
        ts = _coerce_utc_datetime((candle or {}).get("time"))
        if ts is not None:
            parsed.append((ts, candle))
    if len(parsed) < min_bars:
        return {
            "label": "prior_session",
            "valid": False,
            "reason": "timestamped_candles_below_min",
            "bars": len(parsed),
            "min_bars": min_bars,
        }
    current_date = parsed[-1][0].date()
    prior_dates = sorted({ts.date() for ts, _ in parsed if ts.date() < current_date})
    if not prior_dates:
        return {
            "label": "prior_session",
            "valid": False,
            "reason": "no_prior_utc_session_in_window",
            "bars": 0,
            "session_basis": "utc_calendar_day",
        }
    prior_date = prior_dates[-1]
    window = [c for ts, c in parsed if ts.date() == prior_date]
    if len(window) < min_bars:
        return {
            "label": "prior_session",
            "valid": False,
            "reason": "prior_utc_session_below_min_bars",
            "bars": len(window),
            "min_bars": min_bars,
            "session_date": str(prior_date),
            "session_basis": "utc_calendar_day",
        }
    out = _profile_anchor_window_summary(
        window,
        label="prior_session",
        reason="utc_calendar_day_candidate",
    )
    out["session_date"] = str(prior_date)
    out["session_basis"] = "utc_calendar_day"
    return out


def _impulse_anchor_candidate(candles: list, min_bars: int = 5) -> dict:
    closes = [_candle_float(c, "close") for c in candles or []]
    if len([v for v in closes if v is not None]) < min_bars:
        return {
            "label": "impulse_leg",
            "valid": False,
            "reason": "insufficient_closes",
            "bars": len(candles or []),
            "min_bars": min_bars,
        }
    best: tuple[float, int, int] | None = None
    for i, start_close in enumerate(closes):
        if start_close is None or start_close <= 0:
            continue
        for j in range(i + min_bars - 1, len(closes)):
            end_close = closes[j]
            if end_close is None:
                continue
            move_pct = abs(end_close - start_close) / start_close
            if best is None or move_pct > best[0]:
                best = (move_pct, i, j)
    if best is None or best[0] <= 0:
        return {"label": "impulse_leg", "valid": False, "reason": "no_directional_close_move"}
    _, start_idx, end_idx = best
    window = candles[start_idx:end_idx + 1]
    start_close = _candle_float(window[0], "close") or 0.0
    end_close = _candle_float(window[-1], "close") or 0.0
    out = _profile_anchor_window_summary(
        window,
        label="impulse_leg",
        reason="largest_close_to_close_swing_candidate",
    )
    out["direction"] = "LONG" if end_close > start_close else "SHORT"
    out["close_change_pct"] = round(best[0] * 100.0, 4)
    out["heuristic"] = "largest_close_to_close_swing"
    return out


def _reclaim_anchor_candidate(candles: list, vp: dict, min_bars: int = 3) -> dict:
    try:
        vah = float((vp or {}).get("vah"))
        val = float((vp or {}).get("val"))
    except (TypeError, ValueError):
        return {"label": "reclaim_leg", "valid": False, "reason": "vp_value_area_unavailable"}
    if vah <= val:
        return {"label": "reclaim_leg", "valid": False, "reason": "invalid_value_area"}

    last_outside_idx: int | None = None
    outside_side: str | None = None
    for idx, candle in enumerate(candles or []):
        close = _candle_float(candle, "close")
        if close is None:
            continue
        if close > vah:
            last_outside_idx = idx
            outside_side = "above_vah"
            continue
        if close < val:
            last_outside_idx = idx
            outside_side = "below_val"
            continue
        if last_outside_idx is not None and idx - last_outside_idx + 1 >= min_bars:
            window = candles[last_outside_idx:idx + 1]
            out = _profile_anchor_window_summary(
                window,
                label="reclaim_leg",
                reason="outside_value_reclaimed_inside_candidate",
            )
            out["outside_side"] = outside_side
            out["direction"] = "SHORT_RECLAIM" if outside_side == "above_vah" else "LONG_RECLAIM"
            out["heuristic"] = "close_outside_value_then_close_inside_value"
            return out
    return {"label": "reclaim_leg", "valid": False, "reason": "no_reclaim_sequence_found"}


def _engine_d_profile_anchor_shadow(
    *,
    candles_m15: list,
    vp_lookback: int,
    vp: dict,
    active_anchor_mode: str,
    volume_source: Any,
) -> dict:
    """Report-only current/future profile anchor context; never selects levels."""
    lookback = max(1, int(vp_lookback or 1))
    fixed_window = (candles_m15 or [])[-lookback:]
    candidate_window = candles_m15 or []
    fixed_anchor = _profile_anchor_window_summary(
        fixed_window,
        label="fixed_lookback",
        reason="current_profile_window" if active_anchor_mode == "fixed_lookback" else "fallback_fixed_lookback_context",
    )
    fixed_anchor["mode"] = "fixed_lookback"
    fixed_anchor["lookback_bars"] = lookback
    fixed_anchor["volume_source"] = volume_source
    if active_anchor_mode == "fixed_lookback":
        active = dict(fixed_anchor)
    else:
        active = {
            "label": active_anchor_mode,
            "valid": True,
            "mode": active_anchor_mode,
            "reason": "active_profile_not_fixed_lookback",
            "volume_source": volume_source,
        }
    return {
        "report_only": True,
        "active_anchor": active,
        "fixed_lookback_anchor": fixed_anchor,
        "candidates": {
            "prior_session": _prior_session_anchor_candidate(candidate_window),
            "impulse_leg": _impulse_anchor_candidate(candidate_window[-lookback:]),
            "reclaim_leg": _reclaim_anchor_candidate(candidate_window[-lookback:], vp),
        },
    }


def _detect_volume_divergence(candles: list, lookback: int = 5) -> dict:
    """Detect volume divergence: price new high/low but volume doesn't confirm.

    Bearish divergence: price makes new high, volume flat or lower
        -> smart money distributing into retail buying
    Bullish divergence: price makes new low, volume flat or higher
        -> smart money accumulating into retail selling

    Uses tick volume only — works with MT5 tick volume proxy.
    """
    if len(candles) < lookback + 1:
        return {"divergence": False, "type": None, "strength": 0.0}

    cfg = CONFIG.get("SCALP_ENGINE", {})
    if not cfg.get("VOLUME_DIVERGENCE_ENABLED", True):
        return {"divergence": False, "type": None, "strength": 0.0, "disabled": True}

    window = candles[-lookback:]
    highs = [float(c["high"]) for c in window]
    lows = [float(c["low"]) for c in window]
    vols = [float(c.get("vol", 0) or 0) for c in window]

    if len(highs) < 3 or len(vols) < 3:
        return {"divergence": False, "type": None, "strength": 0.0}

    # Need at least some volume variation to detect divergence
    avg_vol = sum(vols) / len(vols)
    if avg_vol <= 0:
        return {"divergence": False, "type": None, "strength": 0.0}

    # Normalize: coefficient of variation
    vol_std = (sum((v - avg_vol) ** 2 for v in vols) / len(vols)) ** 0.5
    cv = vol_std / avg_vol if avg_vol > 0 else 0
    if cv < 0.05:  # Flat volume — no divergence possible
        return {"divergence": False, "type": None, "strength": 0.0, "reason": "flat_volume"}

    # Price extremes
    latest_high = highs[-1]
    latest_low = lows[-1]
    prior_high = max(highs[:-1]) if len(highs) > 1 else latest_high
    prior_low = min(lows[:-1]) if len(lows) > 1 else latest_low

    # Volume extremes
    latest_vol = vols[-1]
    prior_max_vol = max(vols[:-1]) if len(vols) > 1 else latest_vol
    prior_avg_vol = sum(vols[:-1]) / max(1, len(vols) - 1)

    # Bearish divergence: new price high + volume not confirming
    price_new_high = latest_high >= prior_high * 0.999  # allow tiny tolerance
    volume_declining = latest_vol < prior_avg_vol * 0.85  # 15% below recent avg

    # Bullish divergence: new price low + volume holding or rising
    price_new_low = latest_low <= prior_low * 1.001
    volume_holding = latest_vol >= prior_avg_vol * 0.70  # not collapsed

    # Scoring thresholds
    strong_threshold = float(cfg.get("VOLUME_DIVERGENCE_STRONG_PCT", 0.25))
    moderate_threshold = float(cfg.get("VOLUME_DIVERGENCE_MODERATE_PCT", 0.10))

    result = {"divergence": False, "type": None, "strength": 0.0}

    if price_new_high and volume_declining:
        vol_drop = (prior_avg_vol - latest_vol) / prior_avg_vol if prior_avg_vol > 0 else 0
        if vol_drop >= strong_threshold:
            strength = min(1.0, vol_drop * 2.0)
            result = {"divergence": True, "type": "bearish", "strength": round(strength, 2)}
        elif vol_drop >= moderate_threshold:
            result = {"divergence": True, "type": "bearish_weak", "strength": 0.5}

    elif price_new_low and volume_holding:
        vol_rise = (latest_vol - prior_avg_vol) / prior_avg_vol if prior_avg_vol > 0 else 0
        if vol_rise >= strong_threshold:
            strength = min(1.0, vol_rise * 2.0)
            result = {"divergence": True, "type": "bullish", "strength": round(strength, 2)}
        elif vol_rise >= moderate_threshold:
            result = {"divergence": True, "type": "bullish_weak", "strength": 0.5}

    result["latest_high"] = round(latest_high, 6)
    result["latest_low"] = round(latest_low, 6)
    result["latest_vol"] = round(latest_vol, 2)
    result["prior_avg_vol"] = round(prior_avg_vol, 2)
    result["lookback"] = lookback
    return result


def _detect_stop_run(candles: list, direction: str, atr: float) -> dict:
    """Detect stop-run / liquidity sweep patterns.

    A stop-run occurs when price rapidly moves beyond a key level (VAH/VAL/POC)
    but immediately reverses with a long wick and close back inside the level.
    This traps breakout traders and often reverses.

    Scoring:
      -2.0  Confirmed stop-run (long wick > 60% of range, close back inside, high vol)
      -1.0  Suspected stop-run (wick > 40%, close near level, moderate vol)
       0.0  No stop-run detected

    Returns dict with score, confidence, and diagnostic details.
    """
    if not candles or len(candles) < 3:
        return {"stop_run": False, "score": 0.0, "confidence": "insufficient_data"}

    cfg = CONFIG.get("SCALP_ENGINE", {})
    if not cfg.get("STOP_RUN_DETECTION_ENABLED", True):
        return {"stop_run": False, "score": 0.0, "confidence": "disabled"}

    # Analyze the last 2 bars for stop-run pattern
    latest = candles[-1]
    prev = candles[-2] if len(candles) >= 2 else latest

    _open = float(latest.get("open", 0.0))
    _high = float(latest.get("high", 0.0))
    _low = float(latest.get("low", 0.0))
    _close = float(latest.get("close", 0.0))
    _range = max(_high - _low, 1e-12)
    _body = abs(_close - _open)

    # Volume context
    vols = [float(c.get("vol", 0) or 0) for c in candles[-5:]]
    avg_vol = sum(vols) / len(vols) if vols else 0
    latest_vol = float(latest.get("vol", 0) or 0)
    vol_spike = latest_vol > avg_vol * 1.5 if avg_vol > 0 else False

    is_long = direction == "LONG"

    # Wick analysis
    upper_wick = _high - max(_open, _close)
    lower_wick = min(_open, _close) - _low
    upper_wick_pct = upper_wick / _range if _range > 0 else 0
    lower_wick_pct = lower_wick / _range if _range > 0 else 0

    # ATR-scaled thresholds
    wick_threshold = float(cfg.get("STOP_RUN_WICK_PCT", 0.60))
    wick_suspect = float(cfg.get("STOP_RUN_WICK_SUSPECT_PCT", 0.40))

    result = {"stop_run": False, "score": 0.0, "confidence": "none"}

    if is_long:
        # Stop-run SHORT: price spikes above level (long upper wick) then falls back
        if upper_wick_pct >= wick_threshold and _close < _high * 0.999:
            # Close back down, long upper wick
            score = -2.0 if vol_spike else -1.5
            result = {
                "stop_run": True,
                "score": round(score, 1),
                "confidence": "confirmed" if vol_spike else "suspected",
                "wick_pct": round(upper_wick_pct, 4),
                "vol_spike": vol_spike,
                "direction": "SHORT",
                "pattern": "upper_wick_rejection",
            }
        elif upper_wick_pct >= wick_suspect and _close < _open:
            # Moderate upper wick, close below open
            result = {
                "stop_run": True,
                "score": -1.0,
                "confidence": "suspected",
                "wick_pct": round(upper_wick_pct, 4),
                "vol_spike": vol_spike,
                "direction": "SHORT",
                "pattern": "moderate_rejection",
            }
    else:
        # Stop-run LONG: price spikes below level (long lower wick) then bounces back
        if lower_wick_pct >= wick_threshold and _close > _low * 1.001:
            score = -2.0 if vol_spike else -1.5
            result = {
                "stop_run": True,
                "score": round(score, 1),
                "confidence": "confirmed" if vol_spike else "suspected",
                "wick_pct": round(lower_wick_pct, 4),
                "vol_spike": vol_spike,
                "direction": "LONG",
                "pattern": "lower_wick_rejection",
            }
        elif lower_wick_pct >= wick_suspect and _close > _open:
            result = {
                "stop_run": True,
                "score": -1.0,
                "confidence": "suspected",
                "wick_pct": round(lower_wick_pct, 4),
                "vol_spike": vol_spike,
                "direction": "LONG",
                "pattern": "moderate_rejection",
            }

    result["latest_close"] = round(_close, 6)
    result["latest_high"] = round(_high, 6)
    result["latest_low"] = round(_low, 6)
    result["latest_vol"] = round(latest_vol, 2)
    result["avg_vol"] = round(avg_vol, 2)
    return result


def _time_of_day_adjustment(sessions: list, pair: str, cfg: dict) -> tuple[float, str]:
    """Return grading adjustment based on time-of-day quality.

    Different hours have different liquidity and signal quality.
    Reduces grade during known low-quality periods, boosts during high-quality.

    Returns (adjustment_pct, reason).
    """
    if not cfg.get("TIME_OF_DAY_ADJUSTMENT_ENABLED", True):
        return 0.0, ""

    try:
        now_utc = datetime.now(timezone.utc)
        hour_utc = now_utc.hour
    except Exception:
        return 0.0, ""

    # Quality mapping by UTC hour (forex-centric)
    # 07:00-08:00 = London pre-open (low liquidity, false breaks common)
    # 08:00-09:00 = London open (high volatility, more noise)
    # 10:00-12:00 = London mid (cleanest trends)
    # 12:00-13:00 = London lunch (thin, avoid)
    # 13:00-16:00 = London-NY overlap (best liquidity)
    # 16:00-17:00 = London close (choppy)
    # 17:00-21:00 = NY only (moderate)
    # 22:00-07:00 = Asia (thin for most pairs, OK for JPY/AUD)

    hour_quality = {
        7: -5,     # London pre-open
        8: -3,     # London open (volatile)
        9: 0,      # Early London
        10: 3,     # Mid London (good)
        11: 3,     # Mid London (good)
        12: -5,    # London lunch (thin)
        13: 5,     # Overlap start (excellent)
        14: 5,     # Overlap (excellent)
        15: 5,     # Overlap (excellent)
        16: 3,     # Late overlap (good)
        17: -2,    # London closed, NY only
        18: -2,    # NY afternoon
        19: -3,    # NY late (thinning)
        20: -3,    # NY late
        21: -5,    # NY close (choppy)
        22: -3,    # Asia start
        23: -3,    # Asia
        0: -3,     # Asia
        1: -3,     # Asia
        2: -5,     # Asia mid (thin)
        3: -5,     # Asia mid (thin)
        4: -3,     # Asia late
        5: -3,     # Asia late
        6: -3,     # Pre-London
    }

    adjustment = hour_quality.get(hour_utc, 0)

    # JPY pairs get Asia boost
    if "JPY" in pair and (hour_utc >= 22 or hour_utc <= 6):
        adjustment += 10

    # Crypto is 24h but still has quality hours
    if "USDT" in pair or "BTC" in pair or "ETH" in pair:
        # Crypto: overlap hours still matter (more institutional)
        if 13 <= hour_utc <= 16:
            adjustment += 5
        # Asia hours OK for crypto
        if hour_utc >= 22 or hour_utc < 9:
            adjustment = max(adjustment, 0)  # Don't penalize crypto Asia

    return float(adjustment), f"time_of_day_UTC{hour_utc}"


def _check_aaa_sequence(
    candles: list,
    absorption: dict,
    cvd: dict,
    asset_type: Optional[str] = None,
    score_group: Optional[str] = None,
) -> dict:
    """Detect Absorption -> Accumulation -> Aggression sequence.

    - Absorption: already detected (pillar)
    - Accumulation: range contraction after absorption (narrow bars)
    - Aggression: breakout candle with volume spike
    """
    cfg = CONFIG.get("SCALP_ENGINE", {})
    lookback = int(cfg.get("AAA_ACCUMULATION_LOOKBACK", 10))
    _default_ct = 0.5
    # Per-asset contraction threshold: MT5 tick-volume pairs have noisier
    # candle ranges, so use a more lenient threshold (0.65) to avoid
    # accumulation detection being unreachable on M1/M5 forex.
    contraction_threshold = float(
        _scalp_cfg_lookup(
            cfg,
            "AAA_CONTRACTION_THRESHOLD",
            _default_ct,
            asset_type=asset_type,
            score_group=score_group,
        )
    )
    breakout_vol_mult = float(
        _scalp_cfg_lookup(
            cfg,
            "AAA_BREAKOUT_VOL_MULT",
            1.5,
            asset_type=asset_type,
            score_group=score_group,
        )
    )
    absorption_window = max(1, int(cfg.get("AAA_ABSORPTION_RECENT_BARS", lookback * 2)))

    if not absorption.get("detected"):
        return {"complete": False, "phase": "no_absorption"}
    absorption_hits = absorption.get("bars") or []
    latest_abs_idx = None
    for hit in absorption_hits:
        try:
            latest_abs_idx = max(latest_abs_idx if latest_abs_idx is not None else -1, int(hit.get("index")))
        except (TypeError, ValueError):
            continue
    if latest_abs_idx is not None and len(candles) - 1 - latest_abs_idx > absorption_window:
        return {"complete": False, "phase": "stale_absorption"}

    # Accumulation: check range contraction
    try:
        from indicators import detect_range_contraction
        rc = detect_range_contraction(candles, lookback=lookback, threshold=contraction_threshold)
        accumulating = bool(rc.get("contracting", rc.get("contracted", False)))
    except Exception:
        # Internal fallback
        if len(candles) < lookback * 2:
            return {"complete": False, "phase": "insufficient_data"}
        recent_ranges = [c["high"] - c["low"] for c in candles[-lookback:]]
        prior_ranges = [c["high"] - c["low"] for c in candles[-(lookback * 2):-lookback]]
        avg_recent = sum(recent_ranges) / len(recent_ranges) if recent_ranges else 1
        avg_prior = sum(prior_ranges) / len(prior_ranges) if prior_ranges else 1
        accumulating = avg_recent < avg_prior * contraction_threshold

    if not accumulating:
        return {"complete": False, "phase": "absorption_only"}

    # Aggression: last candle breaks out with above-average volume
    last = candles[-1]
    prev = candles[-2]
    vol = float(last.get("vol", 0) or 0)
    vol_window = candles[-20:] if len(candles) >= 20 else candles
    avg_vol = (
        sum(float(c.get("vol", 0) or 0) for c in vol_window) / len(vol_window)
        if vol_window else vol
    )

    body = abs(last["close"] - last["open"])
    prev_range = prev["high"] - prev["low"]
    # Protect against doji prev bar collapsing the body threshold to near-zero
    min_meaningful_range = max(prev["high"] * 1e-6, 1e-10)
    prev_range = max(prev_range, min_meaningful_range)

    aggression = vol > avg_vol * breakout_vol_mult and body > prev_range * 0.8

    if aggression:
        direction = "LONG" if last["close"] > last["open"] else "SHORT"
        return {"complete": True, "phase": "aggression", "direction": direction}

    return {"complete": False, "phase": "accumulation"}


# ═══════════════════════════════════════════════════════════════════════════════
# PILLAR 3 — VWAP DIRECTIONAL LEAN
# ═══════════════════════════════════════════════════════════════════════════════

def _check_vwap_lean(candles: list, current_price: float) -> dict:
    """Determine VWAP directional lean.

    Uses indicators.calc_vwap() when available.
    Returns {lean: 'LONG'|'SHORT'|None, vwap_value}
    """
    cfg = CONFIG.get("SCALP_ENGINE", {})
    band_mult = float(cfg.get("VWAP_BAND_MULT", 0.5))

    try:
        from indicators import calc_vwap
        result = calc_vwap(candles, band_mult=band_mult)
        if result and result.get("vwap") is not None:
            series = result["vwap"]
            if isinstance(series, list):
                vwap = next((float(v) for v in reversed(series) if v is not None), None)
            else:
                vwap = float(series)
            if vwap is not None:
                lean = "LONG" if current_price > vwap else "SHORT" if current_price < vwap else None
                return {"lean": lean, "vwap_value": round(vwap, 6)}
    except Exception as exc:
        log.debug("[SCALP] indicators.calc_vwap error: %s", exc)

    # Internal VWAP fallback
    if len(candles) < 5:
        return {"lean": None, "vwap_value": 0}

    cum_tp_vol = 0.0
    cum_vol = 0.0
    for c in candles:
        tp = (c["high"] + c["low"] + c["close"]) / 3.0
        vol = float(c.get("vol", 0) or 0)
        if vol <= 0:
            vol = 1.0
        cum_tp_vol += tp * vol
        cum_vol += vol

    vwap = cum_tp_vol / cum_vol if cum_vol > 0 else current_price
    lean = "LONG" if current_price > vwap else "SHORT" if current_price < vwap else None
    return {"lean": lean, "vwap_value": round(vwap, 6)}


# ═══════════════════════════════════════════════════════════════════════════════
# SETUP CLASSIFICATION — Mean Reversion vs Trend Continuation
# ═══════════════════════════════════════════════════════════════════════════════

def _has_meaningful_absorption(absorption: dict, asset_type: Optional[str]) -> bool:
    """Return True when absorption is reliable enough to count as confirmation.

    MT5 tick-volume absorption on a single bar is noisy — require
    ``MT5_ABSORPTION_MIN_COUNT`` (default 2) bars for non-crypto pairs.
    Crypto (Binance real bid/ask volume) has no extra requirement.
    """
    if not absorption.get("detected"):
        return False
    if asset_type and asset_type != "crypto":
        cfg = CONFIG.get("SCALP_ENGINE", {})
        mt5_min = int(cfg.get("MT5_ABSORPTION_MIN_COUNT", 2))
        if int(absorption.get("count", 0)) < mt5_min:
            return False
    return True


def _setup_aggression_confirmed(
    absorption: dict,
    cvd: dict,
    aaa: dict,
    direction: Optional[str],
    asset_type: Optional[str],
) -> bool:
    setup_dir = str(direction or "").upper()
    cvd_dir = str((cvd or {}).get("direction") or "").upper()
    aaa_dir = str((aaa or {}).get("direction") or "").upper()
    cvd_aligned = bool(cvd_dir) and (not setup_dir or cvd_dir == setup_dir)
    aaa_aligned = bool((aaa or {}).get("complete")) and (not setup_dir or not aaa_dir or aaa_dir == setup_dir)
    return bool(_has_meaningful_absorption(absorption or {}, asset_type) or cvd_aligned or aaa_aligned)


def _classify_setup(
    market_state: str,
    price_loc: dict,
    absorption: dict,
    cvd: dict,
    aaa: dict,
    vwap: dict,
    htf_bias: Optional[str],
    asset_type: Optional[str] = None,
    candles: Optional[list] = None,
) -> dict:
    """Decide setup type and direction.

    Mean Reversion (balance market):
      - Price at VAH → SHORT toward POC
      - Price at VAL → LONG toward POC
      - Needs at least one confirmation: absorption OR CVD agrees OR VWAP lean agrees
      - CVD actively opposing the reversion direction is a hard veto

    Trend Continuation (imbalance market):
      - Price pulls back to LVN inside impulse → continue in trend direction
      - AAA sequence completion preferred
      - HTF EMA bias alignment required

    Returns {valid, setup_type, direction, target, reasons[]}
    """
    cfg = CONFIG.get("SCALP_ENGINE", {})
    location = price_loc.get("location", "")
    reasons = []
    strict_fabio_gate = bool(cfg.get("STRICT_FABIO_GATE_ENABLED", True))

    # ── Mean Reversion — price extended beyond VA boundary (strongest signal) ─
    # Price pushed outside the value area in a ranging market → expect reversion to POC.
    # This is a stronger signal than being exactly at VAH/VAL (more extended = more elastic).
    if cfg.get("SETUP_MEAN_REVERSION", True) and market_state == "balance" and location == "outside_va":
        above_va = price_loc.get("above_va", True)
        direction = "SHORT" if above_va else "LONG"
        side_label = "above VAH" if above_va else "below VAL"

        # Need at least one confirmation: absorption OR CVD agrees OR VWAP lean agrees
        cvd_dir = cvd.get("direction")
        has_absorption = _has_meaningful_absorption(absorption, asset_type)
        cvd_confirms = (cvd_dir == direction) or (not strict_fabio_gate and cvd_dir is None)
        vwap_confirms = (vwap.get("lean") == direction)
        if strict_fabio_gate:
            if not _setup_aggression_confirmed(absorption, cvd, aaa, direction, asset_type):
                return {"valid": False, "reason": "no_aggression_outside_va"}
        elif not has_absorption and not cvd_confirms and not vwap_confirms:
            return {"valid": False, "reason": "no_absorption_outside_va"}

        # CVD must not actively oppose the reversion direction.
        # For non-crypto pairs using candle-proxy CVD, the veto is downgraded to a
        # grade penalty (advisory) since the proxy is noisy.  Config-gated.
        _cvd_source = cvd.get("source", "candles")
        _cvd_proxy = _cvd_source in ("candles", "error", "disabled")
        _cvd_veto_proxy = cfg.get("CVD_PROXY_HARD_VETO", False)
        if cvd_dir and cvd_dir != direction:
            if _cvd_proxy and not _cvd_veto_proxy:
                reasons.append(f"CVD proxy conflict ({cvd_dir}) — advisory only (grade reduced)")
            else:
                return {"valid": False, "reason": f"cvd_against_reversion:{cvd_dir}_vs_{direction}"}

        # Volume divergence check — strengthens mean reversion
        vol_div = _detect_volume_divergence(candles or [])
        if vol_div.get("divergence"):
            div_type = vol_div.get("type", "")
            if direction == "SHORT" and "bearish" in div_type:
                reasons.append(f"Volume divergence confirms distribution at high ({vol_div['strength']})")
            elif direction == "LONG" and "bullish" in div_type:
                reasons.append(f"Volume divergence confirms accumulation at low ({vol_div['strength']})")

        reasons.append(f"Mean reversion: price extended {side_label} — revert to POC")
        if cvd_dir == direction:
            reasons.append(f"CVD confirms {direction} from extended level")
        if vwap.get("lean") == direction:
            reasons.append(f"VWAP confirms {direction}")

        return {
            "valid": True,
            "setup_type": "mean_reversion",
            "direction": direction,
            "target": "POC",
            "reasons": reasons,
            "volume_divergence": vol_div,
        }

    # ── Mean Reversion ───────────────────────────────────────────────────
    if cfg.get("SETUP_MEAN_REVERSION", True) and market_state == "balance" and location in ("at_vah", "at_val"):
        direction = "SHORT" if location == "at_vah" else "LONG"

        # Need at least one confirmation: absorption OR CVD agrees OR VWAP lean agrees.
        # Neutral CVD (None) matches outside_va mean-reversion behaviour when enabled.
        _cvd_pre = cvd.get("direction")
        _has_abs = _has_meaningful_absorption(absorption, asset_type)
        _vwap_pre = (vwap.get("lean") == direction)
        allow_neutral_cvd = bool(cfg.get("ALLOW_NEUTRAL_CVD_AT_VA_EXTREME", True))
        cvd_pre_ok = (_cvd_pre == direction) or (not strict_fabio_gate and allow_neutral_cvd and _cvd_pre is None)
        if strict_fabio_gate:
            if not _setup_aggression_confirmed(absorption, cvd, aaa, direction, asset_type):
                return {"valid": False, "reason": "no_aggression_at_va_extreme"}
        elif not _has_abs and not cvd_pre_ok and not _vwap_pre:
            return {"valid": False, "reason": "no_absorption_at_va_extreme"}

        # CVD divergence: price at high but CVD falling (SHORT), or vice versa
        cvd_dir = cvd.get("direction")
        cvd_confirms = False
        if direction == "SHORT" and cvd_dir == "SHORT":
            cvd_confirms = True
            reasons.append("CVD divergence: sellers absorbing at VAH")
        elif direction == "LONG" and cvd_dir == "LONG":
            cvd_confirms = True
            reasons.append("CVD divergence: buyers absorbing at VAL")
        elif cvd_dir is None:
            cvd_confirms = True  # neutral CVD — allow with reduced grade
            reasons.append("CVD neutral — proceed with caution")

        if not cvd_confirms:
            _cvd_source_va = cvd.get("source", "candles")
            _cvd_proxy_va = _cvd_source_va in ("candles", "error", "disabled")
            _cvd_veto_proxy_va = cfg.get("CVD_PROXY_HARD_VETO", False)
            if _cvd_proxy_va and not _cvd_veto_proxy_va:
                reasons.append(f"CVD proxy conflict ({cvd_dir}) — advisory only (grade reduced)")
            else:
                return {"valid": False, "reason": f"cvd_against_reversion:{cvd_dir}_vs_{direction}"}

        # VWAP lean as bonus (not required for mean reversion)
        if vwap.get("lean") == direction:
            reasons.append(f"VWAP confirms {direction}")

        # Volume divergence check — strengthens mean reversion at VA extremes
        vol_div_va = _detect_volume_divergence(candles or [])
        if vol_div_va.get("divergence"):
            div_type_va = vol_div_va.get("type", "")
            if direction == "SHORT" and "bearish" in div_type_va:
                reasons.append(f"Volume divergence confirms distribution at VAH ({vol_div_va['strength']})")
            elif direction == "LONG" and "bullish" in div_type_va:
                reasons.append(f"Volume divergence confirms accumulation at VAL ({vol_div_va['strength']})")

        reasons.append(f"Mean reversion from {location.upper()} toward POC")
        # target = POC, not the touched VA extreme. nearest_level for at_vah/at_val
        # returns the VA extreme itself, but mean reversion targets POC.
        # POC is available when location is inside_va/outside_va but not at VA extremes.
        # Use a sentinel: caller (run_scalp_scan) can resolve from vp dict.
        return {
            "valid": True,
            "setup_type": "mean_reversion",
            "direction": direction,
            "target": "POC",
            "reasons": reasons,
            "volume_divergence": vol_div_va,
        }

    # ── Trend Continuation ───────────────────────────────────────────────
    _trend_locations = ("at_lvn", "at_poc", "inside_va", "at_val", "at_vah")
    if (
        strict_fabio_gate
        and bool(cfg.get("STRICT_TREND_LOCATION_LVN_ONLY", True))
        and market_state == "imbalance"
        and location in _trend_locations
        and location != "at_lvn"
    ):
        return {"valid": False, "reason": "trend_continuation_requires_lvn"}
    if cfg.get("SETUP_TREND", True) and market_state == "imbalance" and location in _trend_locations:
        # AAA completion is the strongest signal
        if aaa.get("complete"):
            direction = aaa["direction"]
            reasons.append("AAA sequence complete — aggression breakout")
        elif htf_bias:
            direction = htf_bias
            reasons.append(f"Trend continuation aligned with HTF bias ({htf_bias})")
        elif vwap.get("lean"):
            direction = vwap["lean"]
            reasons.append(f"Trend continuation via VWAP lean ({direction})")
        else:
            return {"valid": False, "reason": "no_direction_for_trend_continuation"}

        # Absorption at pullback level is a plus
        has_absorption_tc = absorption.get("detected", False)
        if has_absorption_tc:
            reasons.append("Absorption detected at pullback level")

        # Volume divergence check — weakens trend continuation (exhaustion signal)
        vol_div_tc = _detect_volume_divergence(candles or [])
        if vol_div_tc.get("divergence"):
            div_type_tc = vol_div_tc.get("type", "")
            # Bearish divergence + LONG trend = exhaustion
            # Bullish divergence + SHORT trend = exhaustion
            if direction == "LONG" and "bearish" in div_type_tc:
                reasons.append(f"Volume divergence warns exhaustion at high ({vol_div_tc['strength']})")
            elif direction == "SHORT" and "bullish" in div_type_tc:
                reasons.append(f"Volume divergence warns exhaustion at low ({vol_div_tc['strength']})")

        # Stop-run detection for trend continuation
        stop_run_tc = _detect_stop_run(candles or [], direction, 0.0)
        if stop_run_tc.get("stop_run"):
            sr_score_tc = stop_run_tc.get("score", 0)
            sr_conf_tc = stop_run_tc.get("confidence", "")
            reasons.append(f"STOP-RUN detected ({sr_conf_tc}): wick {stop_run_tc.get('wick_pct', 0):.1%} — score adjustment {sr_score_tc}")

        # CVD: hard veto only when no other confirmation exists.
        # If absorption or VWAP already confirms, CVD conflict lowers grade instead.
        cvd_dir = cvd.get("direction")
        vwap_confirms_tc = (vwap.get("lean") == direction)
        if cvd_dir and cvd_dir != direction:
            if not has_absorption_tc and not vwap_confirms_tc:
                return {"valid": False, "reason": f"cvd_against_trend:{cvd_dir}_vs_{direction}"}
            override_src = "absorption" if has_absorption_tc else "VWAP"
            reasons.append(f"CVD conflict ({cvd_dir}) — {override_src} override (grade reduced)")
        elif cvd_dir == direction:
            reasons.append(f"CVD confirms {direction} trend")

        reasons.append(f"Trend continuation from {location}")
        return {
            "valid": True,
            "setup_type": "trend_continuation",
            "direction": direction,
            "target": "POC_OR_OPPOSITE_VA",
            "reasons": reasons,
            "volume_divergence": vol_div_tc,
        }

    # ── Trend Extension — price broke through the value area in a trending market ─
    # Imbalance market + price already outside VA = confirmed breakout.
    # Direction: above VAH → LONG continuation, below VAL → SHORT continuation.
    # SL sits just below the broken VA boundary (now structural support/resistance).
    # TP1 is a fixed MIN_RR projection from entry; TP2 = one VA width extended.
    if cfg.get("SETUP_TREND", True) and market_state == "imbalance" and location == "outside_va":
        above_va = price_loc.get("above_va", True)
        direction = "LONG" if above_va else "SHORT"
        side_label = "above VAH" if above_va else "below VAL"

        # HTF bias must align if available — don't enter breakout against macro trend
        if htf_bias and htf_bias != direction:
            return {"valid": False, "reason": f"htf_bias_against_breakout:{htf_bias}_vs_{direction}"}

        # Need at least one aggression confirmation: absorption OR CVD aligned OR VWAP lean
        cvd_dir = cvd.get("direction")
        has_absorption = _has_meaningful_absorption(absorption, asset_type)
        cvd_aligned = (cvd_dir == direction)
        vwap_aligned_ext = (vwap.get("lean") == direction)
        if strict_fabio_gate:
            if not _setup_aggression_confirmed(absorption, cvd, aaa, direction, asset_type):
                return {"valid": False, "reason": "no_aggression_on_va_breakout"}
        elif not has_absorption and not cvd_aligned and not vwap_aligned_ext:
            return {"valid": False, "reason": "no_momentum_on_va_breakout"}
        if vwap_aligned_ext:
            reasons.append(f"VWAP lean confirms {direction} breakout direction")

        # CVD conflict after passing the momentum check is a grade penalty, not a veto.
        # At least one of absorption/CVD/VWAP already confirmed above.
        if cvd_dir and cvd_dir != direction:
            override_src = "absorption" if has_absorption else "VWAP"
            reasons.append(f"CVD conflict ({cvd_dir}) — {override_src} override (grade reduced)")

        # Volume divergence check on breakout — warns of false breakout
        vol_div_ext = _detect_volume_divergence(candles or [])
        if vol_div_ext.get("divergence"):
            div_type_ext = vol_div_ext.get("type", "")
            if direction == "LONG" and "bearish" in div_type_ext:
                reasons.append(f"Volume divergence warns breakout exhaustion ({vol_div_ext['strength']})")
            elif direction == "SHORT" and "bullish" in div_type_ext:
                reasons.append(f"Volume divergence warns breakout exhaustion ({vol_div_ext['strength']})")

        # Stop-run detection — catches liquidity sweeps
        stop_run = _detect_stop_run(candles or [], direction, 0.0)
        if stop_run.get("stop_run"):
            sr_score = stop_run.get("score", 0)
            sr_conf = stop_run.get("confidence", "")
            reasons.append(f"STOP-RUN detected ({sr_conf}): wick {stop_run.get('wick_pct', 0):.1%} — score adjustment {sr_score}")
            # Stop-run is a strong warning for trend extension — reduce confidence
            # The score penalty will be applied in ai_quality_grade

        reasons.append(f"Trend extension: price broke {side_label} in imbalance market")
        if has_absorption:
            reasons.append("Absorption confirms breakout momentum (not exhaustion)")
        if cvd_aligned:
            reasons.append(f"CVD confirms {direction} breakout")
        if vwap.get("lean") == direction:
            reasons.append(f"VWAP lean confirms {direction}")

        return {
            "valid": True,
            "setup_type": "trend_extension",
            "direction": direction,
            "target": "VA_WIDTH_PROJECTION",
            "reasons": reasons,
            "volume_divergence": vol_div_ext,
        }

    # ── No valid setup ───────────────────────────────────────────────────
    # Return the bare classification; caller (run_scalp_scan) prepends
    # "no_setup:" — keeping the prefix here would produce "no_setup:no_setup:..."
    # which was observed 88 times in baseline audit logs (audit 8577d0 §7.1).
    return {"valid": False, "reason": f"{market_state}_{location}"}


# ═══════════════════════════════════════════════════════════════════════════════
# RISK LEVELS — SL / TP1 / TP2
# ═══════════════════════════════════════════════════════════════════════════════

def _scalp_min_rr_for_group(asset_type: str, score_group: str | None = None) -> float:
    """Return the effective Engine D MIN_RR with Engine D-owned group overrides."""
    cfg = CONFIG.get("SCALP_ENGINE", {})
    base = float(cfg.get("MIN_RR", 2.0))
    if not score_group:
        return base
    group_cfg = (cfg.get("score_group_overrides", {}) or {}).get(score_group, {})
    scalp_override = (group_cfg.get("scalp") or {}).get("min_rr")
    if scalp_override is not None:
        return float(scalp_override)

    legacy_group_cfg = (
        (CONFIG.get("NAKED_ENGINE", {}) or {})
        .get("score_group_overrides", {})
        or {}
    ).get(score_group, {})
    legacy_override = (legacy_group_cfg.get("scalp") or {}).get("min_rr")
    if legacy_override is not None:
        log.warning(
            "[SCALP] Using legacy NAKED_ENGINE.score_group_overrides.%s.scalp.min_rr "
            "for Engine D; move this override under SCALP_ENGINE.score_group_overrides",
            score_group,
        )
        return float(legacy_override)
    return base


def _scalp_cost_assumptions(cfg: dict, asset_type: str) -> tuple[float, float]:
    cost_defaults = {
        "crypto": (0.0006, 0.0002),
        "forex": (0.00005, 0.00005),
        "commodity": (0.00010, 0.00010),
        "stock": (0.00010, 0.00005),
        "index": (0.00010, 0.00010),
    }
    fee_default, slip_default = cost_defaults.get(asset_type, cost_defaults["crypto"])
    fee_by_asset = cfg.get("ESTIMATED_FEE_PCT_BY_ASSET", {}) or {}
    slip_by_asset = cfg.get("ESTIMATED_SLIPPAGE_PCT_BY_ASSET", {}) or {}
    fee_pct = float(fee_by_asset.get(asset_type, cfg.get("ESTIMATED_FEE_PCT", fee_default)))
    slip_pct = float(slip_by_asset.get(asset_type, cfg.get("ESTIMATED_SLIPPAGE_PCT", slip_default)))
    return fee_pct, slip_pct


def _calc_m15_atr(candles: list, period: int = 14) -> float:
    """Return the latest true ATR value for Engine D M15 buffers/proximity."""
    highs: list[float] = []
    lows: list[float] = []
    closes: list[float] = []
    for candle in candles or []:
        try:
            highs.append(float(candle["high"]))
            lows.append(float(candle["low"]))
            closes.append(float(candle["close"]))
        except (KeyError, TypeError, ValueError):
            continue
    if len(closes) < 2:
        return 0.0
    p = max(1, int(period))
    try:
        from indicators import calc_atr
        atr_series = calc_atr(highs, lows, closes, p)
        for value in reversed(atr_series or []):
            if value is not None and float(value) > 0:
                return float(value)
    except Exception as exc:
        log.debug("[SCALP] indicators.calc_atr error, using internal true-range ATR: %s", exc)

    true_ranges = []
    for idx in range(1, len(closes)):
        true_ranges.append(
            max(
                highs[idx] - lows[idx],
                abs(highs[idx] - closes[idx - 1]),
                abs(lows[idx] - closes[idx - 1]),
            )
        )
    if not true_ranges:
        return 0.0
    window = true_ranges[-p:]
    return sum(window) / len(window) if window else 0.0


def _scalp_execution_min_grade(cfg: dict) -> str:
    if cfg.get("EXECUTION_MIN_GRADE") is not None:
        return str(cfg.get("EXECUTION_MIN_GRADE")).upper()
    if cfg.get("MIN_GRADE_AUTO_EXECUTE") is not None:
        return str(cfg.get("MIN_GRADE_AUTO_EXECUTE")).upper()
    if cfg.get("MIN_GRADE") is not None:
        log.warning(
            "[SCALP] MIN_GRADE is deprecated for execution gating; use EXECUTION_MIN_GRADE"
        )
        return str(cfg.get("MIN_GRADE")).upper()
    return "B"


def calculate_scalp_levels(
    direction: str,
    entry: float,
    vp: dict,
    setup_type: str,
    symbol_info: dict,
    asset_type: str,
    min_rr_override: float | None = None,
    atr_m15: float = 0.0,
    score_group: Optional[str] = None,
) -> dict:
    """Calculate Engine D execution levels.

    Signal quality still comes from VP/order-flow context, but execution is
    mechanical for scalp trades: use an ATR stop when available and set TP1 to
    the configured 1R self-pay target. VP levels are retained as structural
    context/runner targets instead of hard RR blockers.
    """
    cfg = CONFIG.get("SCALP_ENGINE", {})
    digits = symbol_info.get("digits", 5)
    point = symbol_info.get("point", 0.00001)

    _pip = point * 10 if digits >= 4 else point
    # Static asset-class minimums (preserved as floors).
    if entry <= 0:
        log.warning(
            "[SCALP] invalid entry for buffer calculation: entry=%s asset_type=%s",
            entry,
            asset_type,
        )
    _crypto_floor = _pip * float(cfg.get("CRYPTO_MIN_BUFFER_PIPS", 10))
    _equity_floor = _pip * float(cfg.get("EQUITY_MIN_BUFFER_PIPS", 10))
    _forex_pct_floor = (
        entry * float(cfg.get("FOREX_BUFFER_PCT", 0.0)) if entry > 0 else 0.0
    )
    _min_buffers = {
        "forex":     max(_pip * 3, _forex_pct_floor),
        "commodity": max((entry * 0.002) if entry > 0 else 0.0, _pip * 5),
        "crypto":    max((entry * 0.003) if entry > 0 else 0.0, _crypto_floor),
        "index":     max((entry * 0.002) if entry > 0 else 0.0, _equity_floor),
        "stock":     max((entry * 0.002) if entry > 0 else 0.0, _equity_floor),
    }
    _min_buf = _min_buffers.get(asset_type, _min_buffers["forex"])

    # ATR-scaled buffer: ATR_M15 * BUFFER_ATR_K (default 0.25).
    # Takes the larger of the ATR buffer and the asset-class minimum so
    # volatile pairs get wider buffers while quiet pairs keep a sensible floor.
    if atr_m15 > 0 and cfg.get("BUFFER_USE_ATR", True):
        _buf_k = float(
            _scalp_cfg_lookup(
                cfg,
                "BUFFER_ATR_K",
                0.25,
                asset_type=asset_type,
                score_group=score_group,
            )
        )
        buffer = max(_min_buf, atr_m15 * _buf_k)
    else:
        buffer = _min_buf

    # Forex fills at ASK (LONG) or BID (SHORT), not midpoint. Add half-spread to the
    # SL buffer so that sl_distance is measured from the actual fill price, not mid.
    if asset_type == "forex":
        spread_half = (float(symbol_info.get("spread", 0)) * float(point)) / 2.0
        buffer = buffer + spread_half

    poc = vp.get("poc", entry) or entry
    vah = vp.get("vah", entry) or entry
    val = vp.get("val", entry) or entry
    min_rr_cfg = float(min_rr_override) if min_rr_override is not None else float(cfg.get("MIN_RR", 2.0))
    va_width = abs(vah - val)
    sl_method = "vp_boundary"
    structural_tp = poc
    runner_tp = None

    if setup_type == "mean_reversion":
        if direction == "LONG":
            sl = val - buffer
            structural_tp = poc
            runner_tp = vah
        else:
            sl = vah + buffer
            structural_tp = poc
            runner_tp = val

    elif setup_type == "trend_extension":
        # Price has broken through the value area boundary — SL behind the broken
        # level (now structural S/R). TP1 = MIN_RR projection. TP2 = one VA width.
        if direction == "LONG":
            sl = vah - buffer          # VAH is now support
            structural_tp = entry + va_width
            runner_tp = structural_tp
        else:
            sl = val + buffer          # VAL is now resistance
            structural_tp = entry - va_width
            runner_tp = structural_tp

    else:  # trend_continuation
        # When POC sits on the wrong side of entry, use ATR-based SL (1.5x ATR_M15)
        # instead of the old fixed 0.3% fallback which was too tight on BTC and
        # too wide on quiet forex pairs.
        _tc_fallback_pct = float(cfg.get("TREND_CONT_SL_FALLBACK_PCT", 0.003))
        _tc_atr_mult = float(
            _scalp_cfg_lookup(
                cfg,
                "TREND_CONT_SL_ATR_MULT",
                1.5,
                asset_type=asset_type,
                score_group=score_group,
            )
        )
        if direction == "LONG":
            if poc < entry:
                sl = poc - buffer
            elif atr_m15 > 0:
                sl = entry - atr_m15 * _tc_atr_mult
            else:
                sl = entry - (entry * _tc_fallback_pct)
            structural_tp = vah
            runner_tp = vah + (vah - poc)
        else:
            if poc > entry:
                sl = poc + buffer
            elif atr_m15 > 0:
                sl = entry + atr_m15 * _tc_atr_mult
            else:
                sl = entry + (entry * _tc_fallback_pct)
            structural_tp = val
            runner_tp = val - (poc - val)

    if atr_m15 > 0 and cfg.get("ATR_SL_ENABLED", True):
        atr_stop_distance = atr_m15 * float(
            _scalp_cfg_lookup(
                cfg,
                "ATR_SL_MULT",
                1.5,
                asset_type=asset_type,
                score_group=score_group,
            )
        )
        if atr_stop_distance > 0:
            atr_sl = entry - atr_stop_distance if direction == "LONG" else entry + atr_stop_distance
            # Use the wider stop: the one further from entry (more conservative).
            # The structural SL (e.g. VAH/breakout-level, VAL/mean-reversion level)
            # represents the invalidation level for the setup.  We must not place
            # the stop inside that level — doing so stops the trade on normal
            # pullbacks before the setup is actually invalidated.
            if direction == "LONG" and atr_sl < sl:
                sl = atr_sl
                sl_method = "atr"
            elif direction == "SHORT" and atr_sl > sl:
                sl = atr_sl
                sl_method = "atr"

    # Defensive: if VP levels place SL on the wrong side of entry (e.g. price has
    # moved outside the value area since the VP was built), clamp to entry ± buffer.
    if direction == "LONG" and sl >= entry:
        log.warning(f"[SCALP] SL clamp: LONG sl={sl:.5f} >= entry={entry:.5f} — forcing sl = entry - buffer")
        sl = entry - buffer
        sl_method = "fallback_buffer"
    elif direction == "SHORT" and sl <= entry:
        log.warning(f"[SCALP] SL clamp: SHORT sl={sl:.5f} <= entry={entry:.5f} — forcing sl = entry + buffer")
        sl = entry + buffer
        sl_method = "fallback_buffer"

    sl_distance = abs(entry - sl)

    tp1_r_mult = max(float(cfg.get("TP1_R_MULT", 1.0)), min_rr_cfg)
    tp1 = entry + (sl_distance * tp1_r_mult) if direction == "LONG" else entry - (sl_distance * tp1_r_mult)
    tp_partial = tp1
    actual_rr = round(abs(tp1 - entry) / sl_distance, 2) if sl_distance > 0 else 0

    # Structural/profile targets are context and optional runners. They should
    # not block an otherwise valid scalp when the mechanical pay target exists.
    structural_tp_direction_ok = (
        (direction == "LONG" and structural_tp > entry)
        or (direction == "SHORT" and structural_tp < entry)
    )
    structural_rr = (
        round(abs(structural_tp - entry) / sl_distance, 2)
        if sl_distance > 0 and structural_tp_direction_ok
        else 0
    )
    structure_target_close = structural_tp_direction_ok and structural_rr < tp1_r_mult

    tp2 = None
    if cfg.get("TP2_ENABLED", True):
        runner_candidates = [structural_tp, runner_tp]
        if direction == "LONG":
            valid_runners = [t for t in runner_candidates if t is not None and t > tp1]
            tp2 = min(valid_runners) if valid_runners else None
        else:
            valid_runners = [t for t in runner_candidates if t is not None and t < tp1]
            tp2 = max(valid_runners) if valid_runners else None

    tp_direction_ok = (direction == "LONG" and tp1 > entry) or (direction == "SHORT" and tp1 < entry)
    if not tp_direction_ok:
        log.warning(
            f"[SCALP] TP direction invalid: {direction} tp1={tp1:.5f} vs entry={entry:.5f} "
            f"(mechanical {tp1_r_mult}R target could not be built)"
        )

    rr_below_min = not tp_direction_ok or actual_rr < min_rr_cfg

    # --- Defensive Rounding Safeguard ---
    # Protect against level collapse if symbol_info.digits are too coarse (e.g. 2 digits for a 0.09 crypto pair).
    # This prevents risk_engine from rejecting valid signals with INVALID_LEVELS.
    # The primary fix is in bybit_get_symbol_info, but this serves as a localized safety backup.
    if round(entry, digits) == round(sl, digits) or round(entry, digits) == round(tp1, digits):
        if asset_type == "crypto":
            # Fallback to safe precision for crypto (min 4 decimals; min 6 if price < $1)
            digits = max(digits, 6 if entry < 1.0 else 4)
    if asset_type == "crypto" and entry < 1.0:
        digits = max(digits, 6)

    return {
        "entry":        round(entry, digits),
        "sl":           round(sl, digits),
        "tp_partial":   round(tp_partial, digits),
        "tp1":          round(tp1, digits),
        "tp2":          round(tp2, digits) if tp2 else None,
        "structural_tp": round(structural_tp, digits),
        "structural_rr": structural_rr,
        "structure_target_close": structure_target_close,
        "structural_tp_direction_ok": structural_tp_direction_ok,
        "rr":           actual_rr,
        "rr_below_min": rr_below_min,
        "rr_synthetic": True,
        "sl_distance":  round(sl_distance, digits),
        "sl_method":    sl_method,
    }


# ═══════════════════════════════════════════════════════════════════════════════
# GRADING — A/B/C/D with position sizing multiplier
# ═══════════════════════════════════════════════════════════════════════════════

def ai_quality_grade(
    vp: dict,
    price_loc: dict,
    absorption: dict,
    cvd: dict,
    aaa: dict,
    vwap: dict,
    setup: dict,
    sessions: list,
    spread_pips: float,
    htf_bias: Optional[str],
    asset_type: Optional[str] = None,
    pair: str = "",
) -> dict:
    """Rule-based quality scoring (0–100). No API call — instant.

    Grades & sizing:
      A  (80–100): Full size   (1.0x)
      B  (60–79):  Half size   (0.5x)
      C  (40–59):  Quarter     (0.25x)
      D  (<40):    Skip        (0x)
    """
    cfg = CONFIG.get("SCALP_ENGINE", {})
    score = 0
    reasons = []
    components: dict[str, int] = {
        "location": 0,
        "absorption": 0,
        "cvd": 0,
        "aaa": 0,
        "vwap": 0,
        "session": 0,
        "htf_bias": 0,
        "spread": 0,
        "volume_divergence": 0,
        "stop_run": 0,
        "time_of_day": 0,
    }

    # ── Location quality (0–25) ──────────────────────────────────────────
    loc = price_loc.get("location", "")
    if loc in ("at_vah", "at_val"):
        components["location"] = 25
        reasons.append(f"Price at {loc.upper()} — prime location")
    elif loc == "outside_va":
        components["location"] = 25
        reasons.append("Price extended outside VA - prime mean-reversion / extension")
    elif loc == "at_lvn":
        components["location"] = 20
        reasons.append("Price at LVN — trend continuation zone")
    elif loc == "at_poc":
        components["location"] = 10
        reasons.append("Price at POC — neutral location")
    elif loc == "inside_va":
        components["location"] = 5
    score += components["location"]

    # ── Absorption (0–20) ────────────────────────────────────────────────
    if absorption.get("detected"):
        cnt = absorption.get("count", 0)
        strong_abs_min = max(1, int(cfg.get("GRADE_STRONG_ABSORPTION_MIN_COUNT", 2)))
        if cnt >= strong_abs_min:
            components["absorption"] = 20
            reasons.append(f"Strong absorption ({cnt} bars)")
        elif cnt >= 1:
            components["absorption"] = 12
            reasons.append(f"Absorption detected ({cnt} bar(s))")
    score += components["absorption"]

    # ── CVD confirmation (0–15) ──────────────────────────────────────────
    setup_dir = setup.get("direction")
    cvd_dir = cvd.get("direction")
    if cvd_dir and cvd_dir == setup_dir:
        components["cvd"] = 15
        reasons.append(f"CVD confirms {setup_dir}")
    elif cvd_dir is None:
        components["cvd"] = 5
        reasons.append("CVD neutral")
    score += components["cvd"]

    # ── AAA sequence (0–15) ──────────────────────────────────────────────
    if aaa.get("complete"):
        components["aaa"] = 15
        reasons.append("Full AAA sequence complete")
    elif aaa.get("phase") == "accumulation":
        components["aaa"] = 7
        reasons.append("AAA: absorption + accumulation (no aggression yet)")
    score += components["aaa"]

    # ── VWAP alignment (0–5) ─────────────────────────────────────────────
    if vwap.get("lean") == setup_dir:
        components["vwap"] = 5
        reasons.append(f"VWAP lean confirms {setup_dir}")
    score += components["vwap"]

    # ── Session (0–10) ───────────────────────────────────────────────────
    grade_sessions = _grade_session_names(cfg, asset_type)
    active_grade_sessions = [s for s in grade_sessions if s in set(sessions or [])]
    if len(active_grade_sessions) >= 2:
        components["session"] = 10
        reasons.append(f"Grade session overlap active ({'/'.join(active_grade_sessions)})")
    elif len(active_grade_sessions) == 1:
        components["session"] = 7
        reasons.append(f"Major grade session active ({active_grade_sessions[0]})")
    score += components["session"]

    # ── HTF bias alignment (0–5) ─────────────────────────────────────────
    if htf_bias and htf_bias == setup_dir:
        components["htf_bias"] = 5
        reasons.append(f"HTF EMA bias aligned ({htf_bias})")
    score += components["htf_bias"]

    # ── Spread penalty (−5 to +5) ────────────────────────────────────────
    # Keep units aligned with check_spread(): non-forex uses raw MT5 points,
    # forex uses pip-converted spread.
    if asset_type in ("index", "stock", "commodity"):
        max_points_cfg = cfg.get("MAX_SPREAD_POINTS", {})
        defaults = {"index": 100, "stock": 50, "commodity": 30}
        per_sym = cfg.get("MAX_SPREAD_POINTS_OVERRIDES", {})
        _sym_override = per_sym.get(pair) if pair else None
        max_sp = float(_sym_override if _sym_override is not None else max_points_cfg.get(asset_type, defaults.get(asset_type, 100)))
        spread_unit = "points"
    else:
        max_spreads = cfg.get("MAX_SPREAD_PIPS", {})
        max_sp = float(max_spreads.get(asset_type, max_spreads.get("forex", 4)))
        spread_unit = "pips"
    if spread_pips > 0:
        if spread_pips <= max_sp * 0.5:
            components["spread"] = 5
            reasons.append(f"Tight spread ({spread_pips:.1f} {spread_unit})")
        elif spread_pips > max_sp * 0.8:
            components["spread"] = -5
            reasons.append(f"Wide spread ({spread_pips:.1f} {spread_unit})")
    score += components["spread"]

    # ── Volume divergence bonus/penalty (−10 to +10) ───────────────────────
    vol_div_grade = setup.get("volume_divergence", {})
    if vol_div_grade.get("divergence"):
        div_type_grade = vol_div_grade.get("type", "")
        div_strength = vol_div_grade.get("strength", 0.0)
        setup_type_grade = setup.get("setup_type", "")
        # Mean reversion: divergence CONFIRMS (add points)
        if setup_type_grade in ("mean_reversion",):
            if setup_dir == "LONG" and "bullish" in div_type_grade:
                bonus = int(10 * div_strength)
                components["volume_divergence"] = bonus
                reasons.append(f"Volume divergence confirms accumulation (+{bonus})")
            elif setup_dir == "SHORT" and "bearish" in div_type_grade:
                bonus = int(10 * div_strength)
                components["volume_divergence"] = bonus
                reasons.append(f"Volume divergence confirms distribution (+{bonus})")
        # Trend setups: divergence WARNS (subtract points)
        elif setup_type_grade in ("trend_continuation", "trend_extension"):
            if setup_dir == "LONG" and "bearish" in div_type_grade:
                penalty = int(10 * div_strength)
                components["volume_divergence"] = -penalty
                reasons.append(f"Volume divergence warns exhaustion (−{penalty})")
            elif setup_dir == "SHORT" and "bullish" in div_type_grade:
                penalty = int(10 * div_strength)
                components["volume_divergence"] = -penalty
                reasons.append(f"Volume divergence warns exhaustion (−{penalty})")
    score += components["volume_divergence"]

    # ── Stop-run penalty (−20 to 0) ──────────────────────────────────────
    stop_run_grade = setup.get("stop_run", {})
    if stop_run_grade.get("stop_run"):
        sr_score = stop_run_grade.get("score", 0)
        if sr_score < 0:
            penalty = abs(int(sr_score * 10))  # −2.0 → −20 points
            components["stop_run"] = -penalty
            reasons.append(f"Stop-run / liquidity sweep penalty (−{penalty})")
    score += components["stop_run"]

    # ── Time-of-day adjustment (−5 to +5) ───────────────────────────────
    tod_adj, tod_reason = 0.0, ""
    if pair:  # Only apply during live trading, not in unit tests
        tod_adj, tod_reason = _time_of_day_adjustment(sessions, pair, cfg)
    if tod_adj != 0:
        components["time_of_day"] = int(tod_adj)
        if tod_adj > 0:
            reasons.append(f"Time-of-day boost ({tod_reason}: +{int(tod_adj)})")
        else:
            reasons.append(f"Time-of-day penalty ({tod_reason}: {int(tod_adj)})")
    score += components["time_of_day"]

    score = max(0, min(100, score))

    grade_map = cfg.get("GRADE_THRESHOLDS", {"A": 80, "B": 60, "C": 40})
    a_thresh = int(grade_map.get("A", 80))
    b_thresh = int(grade_map.get("B", 60))
    c_thresh = int(grade_map.get("C", 40))

    if score >= a_thresh:
        grade = "A"
    elif score >= b_thresh:
        grade = "B"
    elif score >= c_thresh:
        grade = "C"
    else:
        grade = "D"

    if cfg.get("GRADE_SIZING_ENABLED", True):
        size_map = cfg.get(
            "GRADE_SIZE_MAP",
            {
                "A": float(cfg.get("GRADE_A_SIZE_MULT", 1.0)),
                "B": float(cfg.get("GRADE_B_SIZE_MULT", 0.5)),
                "C": float(cfg.get("GRADE_C_SIZE_MULT", 0.25)),
                "D": float(cfg.get("GRADE_D_SIZE_MULT", 0.0)),
            },
        )
        size_mult = float(size_map.get(grade, 0.0))
    else:
        size_mult = 1.0  # full size regardless of grade

    out = {
        "score": score,
        "grade": grade,
        "reasons": reasons,
        "size_multiplier": size_mult,
    }
    if cfg.get("GRADE_PERSIST_COMPONENTS", True):
        out["score_components"] = components
    return out


def _build_engine_d_advisory(
    *,
    market_state: str,
    price_loc: dict,
    setup: dict,
    vwap: dict,
    direction: str,
    levels: dict,
    absorption: dict,
    cvd: dict,
    aaa: dict,
    htf_bias: Optional[str],
) -> dict:
    """Build an informational, non-blocking trade narrative from existing Engine D fields."""
    location = str(price_loc.get("location") or "unknown")
    setup_type = str(setup.get("setup_type") or "unknown")
    trigger = "absorption" if absorption.get("detected") else "cvd_shift" if cvd.get("direction") else "rejection"
    directional_lean = (
        "aligned"
        if vwap.get("lean") == direction
        else "counter"
        if vwap.get("lean")
        else "neutral"
    )

    target_logic = (
        "POC -> opposite VA"
        if levels.get("tp2") is not None
        else "POC only"
    )
    trigger_notes: list[str] = []
    if absorption.get("detected"):
        trigger_notes.append(f"absorption:{int(absorption.get('count', 0))}")
    if cvd.get("direction"):
        trigger_notes.append(f"cvd:{cvd.get('direction')}")
    if aaa.get("complete"):
        trigger_notes.append("aaa:complete")
    elif aaa.get("phase"):
        trigger_notes.append(f"aaa:{aaa.get('phase')}")

    summary = (
        f"{str(market_state).upper()} | loc={location} | setup={setup_type} | "
        f"trigger={trigger} ({', '.join(trigger_notes) or 'none'}) | "
        f"lean={directional_lean}:{vwap.get('lean') or 'NONE'} | "
        f"invalidation={levels.get('sl')} | targets={target_logic}"
    )

    return {
        "market_state": market_state,
        "location": location,
        "aggression_trigger": trigger,
        "trigger_notes": trigger_notes,
        "directional_lean": directional_lean,
        "vwap_lean": vwap.get("lean"),
        "setup_type": setup_type,
        "direction": direction,
        "invalidation": levels.get("sl"),
        "tp1": levels.get("tp1"),
        "tp2": levels.get("tp2"),
        "target_logic": target_logic,
        "htf_bias": htf_bias,
        "summary": summary,
    }


def _build_premarket_delta_proxy_levels(
    candles: list,
    *,
    top_n: int = 3,
    min_candles: int = 10,
    bucket_size: Optional[float] = None,
) -> dict:
    """Build pre-market proxy levels from existing candle/CVD approximation.

    This is intentionally labeled proxy data (not true footprint cluster data).
    Window: America/New_York 04:00-09:30 local time.
    """
    if not candles:
        return {
            "available": False,
            "method": "proxy",
            "label": "premarket_delta_proxy_levels",
            "is_true_delta_cluster": False,
            "reason": "no_candles",
            "levels": [],
        }

    pre = []
    for c in candles:
        ts = _coerce_utc_datetime(c.get("time"))
        if ts is None:
            continue
        ny = ts.astimezone(_TZ_NEW_YORK)
        if ny.weekday() >= 5:
            continue
        minute = ny.hour * 60 + ny.minute
        if 4 * 60 <= minute < 9 * 60 + 30:
            pre.append(c)

    if len(pre) < min_candles:
        return {
            "available": False,
            "method": "proxy",
            "label": "premarket_delta_proxy_levels",
            "is_true_delta_cluster": False,
            "reason": "insufficient_premarket_candles",
            "levels": [],
        }

    try:
        from indicators import calc_cvd
        cvd = calc_cvd(pre, smooth_period=5)
        deltas = cvd.get("delta", []) if isinstance(cvd, dict) else []
    except Exception:
        deltas = []

    if not deltas or len(deltas) != len(pre):
        return {
            "available": False,
            "method": "proxy",
            "label": "premarket_delta_proxy_levels",
            "is_true_delta_cluster": False,
            "reason": "delta_proxy_unavailable",
            "levels": [],
        }

    if bucket_size is None or bucket_size <= 0:
        closes = [float(c.get("close", 0.0)) for c in pre if c.get("close") is not None]
        if len(closes) >= 2:
            span = max(closes) - min(closes)
            bucket_size = span / 80.0 if span > 0 else max(abs(closes[-1]) * 0.0001, 1e-6)
        else:
            bucket_size = 1e-4

    bins: dict[float, dict[str, float]] = {}
    for c, d in zip(pre, deltas):
        px = float(c.get("close", 0.0))
        if bucket_size > 0:
            key = round(round(px / bucket_size) * bucket_size, 6)
        else:
            key = round(px, 6)
        row = bins.setdefault(key, {"signed": 0.0, "abs_sum": 0.0, "touches": 0.0})
        row["signed"] += float(d)
        row["abs_sum"] += abs(float(d))
        row["touches"] += 1.0

    ranked = sorted(bins.items(), key=lambda kv: kv[1]["abs_sum"], reverse=True)
    levels = []
    for price, agg in ranked[: max(1, int(top_n))]:
        signed = float(agg["signed"])
        levels.append(
            {
                "price": round(float(price), 6),
                "signed_delta_proxy": round(signed, 2),
                "intensity": round(float(agg["abs_sum"]), 2),
                "touches": int(agg["touches"]),
                "direction": "LONG" if signed > 0 else "SHORT" if signed < 0 else "NEUTRAL",
            }
        )

    return {
        "available": bool(levels),
        "method": "proxy",
        "label": "premarket_delta_proxy_levels",
        "is_true_delta_cluster": False,
        "window_local_tz": "America/New_York",
        "window_local_time": "04:00-09:30",
        "formula": "bucket(close) -> sum(delta_proxy), where delta_proxy comes from indicators.calc_cvd on premarket candles",
        "levels": levels,
    }


# ═══════════════════════════════════════════════════════════════════════════════
# MAIN SCAN — run_scalp_scan()
# ═══════════════════════════════════════════════════════════════════════════════

def run_scalp_scan(pairs_or_symbols: list) -> dict:
    """Scan a list of pairs for Fabio Valentini scalp setups.

    Args:
        pairs_or_symbols: list of Athena display names (e.g. ['EUR/USD', 'XAU/USD'])

    Returns:
        {signals, skipped, session, scanned, sessions_active}
    """
    from mt5_executor import mt5_map_symbol, mt5_get_symbol_info, mt5_connect

    cfg = CONFIG.get("SCALP_ENGINE", {})
    m1_count = int(cfg.get("M1_CANDLES", 300))
    m15_count = int(cfg.get("M15_CANDLES", 500))
    m5_count  = int(cfg.get("M5_CANDLES", 1000))
    h1_count  = int(cfg.get("H1_CANDLES", 300))
    execution_tf = str(cfg.get("EXECUTION_TIMEFRAME", "M1")).upper()
    if execution_tf not in {"M1", "M5"}:
        execution_tf = "M1"
    bias_tf   = str(cfg.get("BIAS_TIMEFRAME", "H1")).upper()
    use_bias  = bool(cfg.get("WITH_TREND_ONLY", True))
    min_grade = _scalp_execution_min_grade(cfg)

    def _record_stability_sample(
        display: str,
        asset_type: str,
        passed: bool,
        score_norm: float | None = None,
        feature_map: dict | None = None,
        reason: str | None = None,
    ) -> None:
        try:
            meta = {"pair": display}
            if reason:
                meta["reason"] = reason
            record_signal_event(
                engine="scalp",
                score=score_norm,
                max_score=1.0 if score_norm is not None else None,
                passed=passed,
                expected_prob=score_norm,
                feature_map=feature_map,
                meta=meta,
            )
        except Exception as exc:
            log.debug(f"[SSI] Scalp sample skipped for {display}: {exc}")

    # Daily risk rules check
    _session_snapshot = get_scalp_session_risk_state()
    max_daily = int(cfg.get("MAX_DAILY_LOSSES", 3))
    if cfg.get("MAX_DAILY_LOSSES") and _session_snapshot["total_losses_today"] >= max_daily:
        log.warning(f"[SCALP] Daily loss limit reached: {_session_snapshot['total_losses_today']} losses")
        return _finalize_run_scalp_scan_result(
            signals=[],
            skipped=[
                {"pair": display, "reason": f"MAX_DAILY_LOSSES ({max_daily}) reached"}
                for display in pairs_or_symbols
            ],
            scanned=0,
            session_name="DAILY_LOSS_LIMIT",
            sessions_active=[],
            reason=f"MAX_DAILY_LOSSES ({max_daily}) reached",
        )

    sessions = get_current_sessions()
    mt5_session_ok, session_name = scalp_session_window("forex")
    crypto_session_ok, _ = scalp_session_window("crypto")

    if not mt5_session_ok and not crypto_session_ok:
        for display in pairs_or_symbols:
            _record_stability_sample(display, _guess_asset_type(display), False, reason="OUTSIDE_SESSION")
        return _finalize_run_scalp_scan_result(
            signals=[],
            skipped=[{"pair": display, "reason": "OUTSIDE_SESSION"} for display in pairs_or_symbols],
            scanned=0,
            session_name=session_name,
            sessions_active=sessions,
            reason="OUTSIDE_SESSION",
        )

    signals = []
    skipped = []

    _already_skipped = set()
    mt5_pairs = [p for p in pairs_or_symbols if _guess_asset_type(p) != "crypto"]
    if not mt5_connect() and mt5_pairs:
        for display in mt5_pairs:
            _record_stability_sample(display, _guess_asset_type(display), False, reason="MT5_NOT_CONNECTED")
            skipped.append({"pair": display, "reason": "MT5_NOT_CONNECTED"})
            _already_skipped.add(display)
        mt5_pairs = []

    for display in pairs_or_symbols:
        if display in _already_skipped:
            continue
        mt5_sym = None
        _skip_start = len(skipped)
        _funnel: dict[str, Any] = {
            "symbol": display,
            "asset_type": "",
            "source": "",
            "scalp_enabled": bool(cfg.get("enabled", True)),
            "called": True,
            "data_available": False,
            "candle_timeframes_available": [],
            "lower_tf_candle_count": None,
            "latest_lower_tf_candle": None,
            "freshness_status": "",
            "spread": None,
            "spread_ok": None,
            "atr": None,
            "atr_ok": None,
            "volume_available": None,
            "volume_profile_available": None,
            "poc": None,
            "vah": None,
            "val": None,
            "lvn_count": None,
            "price_near_poc": None,
            "price_near_vah": None,
            "price_near_val": None,
            "cvd_available": None,
            "cvd_bias": None,
            "absorption_detected": None,
            "vwap_available": None,
            "vwap_bias": None,
            "setup_type": None,
            "setup_direction": None,
            "setup_score": None,
            "setup_grade": None,
            "min_grade_required": min_grade,
            "min_score_required": None,
            "rr": None,
            "rr_ok": None,
            "entry": None,
            "sl": None,
            "tp": None,
            "gate_result": "NOT_CALLED",
            "fail_reasons": [],
            "soft_warnings": [],
            "diagnostic_notes": {},
        }
        try:
            asset_type = _guess_asset_type(display)
            _funnel["asset_type"] = asset_type
            try:
                from scoring import get_pair_score_group as _gpsg
                _scalp_score_group = _gpsg({"display": display, "type": asset_type})
            except Exception:
                _scalp_score_group = None
            session_ok, active_session = scalp_session_window(asset_type)
            if not session_ok:
                reason = active_session if active_session == "NY_OPEN_COOLDOWN" else "OUTSIDE_SESSION"
                _record_stability_sample(display, asset_type, False, reason=reason)
                skipped.append({"pair": display, "reason": reason})
                continue

            # ── Fetch candles (crypto vs MT5) ────────────────────────────────
            _vol_src_dominant = "binance_ws" if asset_type == "crypto" else "mt5_tick"
            _vol_src_structure = "binance_candle" if asset_type == "crypto" else "mt5_tick"
            _vol_src_exec = _vol_src_structure
            candles_bias = None
            if asset_type == "crypto":
                pair_dict = {
                    "display": display,
                    "symbol": display.replace("/", ""),
                    "type": "crypto",
                    "source": "binance",
                }
                candles_m15 = _scalp_fetch_candles(pair_dict, "M15", m15_count)
                if not candles_m15 or len(candles_m15) < 30:
                    _record_stability_sample(display, asset_type, False, reason="insufficient_m15_candles")
                    skipped.append({"pair": display, "reason": "insufficient_m15_candles"})
                    continue
                fresh, stale_reason = _scalp_candles_fresh(candles_m15, "M15", "structure")
                if not fresh:
                    _record_stability_sample(display, asset_type, False, reason=stale_reason)
                    skipped.append({"pair": display, "reason": stale_reason})
                    continue

                candles_m5 = _scalp_fetch_candles(pair_dict, "M5", m5_count)
                if not candles_m5 or len(candles_m5) < 10:
                    _record_stability_sample(display, asset_type, False, reason="insufficient_m5_candles")
                    skipped.append({"pair": display, "reason": "insufficient_m5_candles"})
                    continue
                fresh, stale_reason = _scalp_candles_fresh(candles_m5, "M5", "context")
                if not fresh:
                    _record_stability_sample(display, asset_type, False, reason=stale_reason)
                    skipped.append({"pair": display, "reason": stale_reason})
                    continue

                if execution_tf == "M1":
                    candles_exec = _scalp_fetch_candles(pair_dict, "M1", m1_count)
                    if not candles_exec or len(candles_exec) < 30:
                        _record_stability_sample(display, asset_type, False, reason="insufficient_m1_candles")
                        skipped.append({"pair": display, "reason": "insufficient_m1_candles"})
                        continue
                else:
                    candles_exec = candles_m5

                fresh, stale_reason = _execution_candles_fresh(candles_exec, execution_tf)
                if not fresh:
                    _record_stability_sample(display, asset_type, False, reason=stale_reason)
                    skipped.append({"pair": display, "reason": stale_reason})
                    continue

                current_price = candles_exec[-1]["close"]
                sym_info = _crypto_sym_info(current_price)
                spread_pips = 0.0
                htf_bias = None

                if use_bias:
                    candles_bias = _scalp_fetch_candles(pair_dict, bias_tf, h1_count)
                    if not candles_bias or len(candles_bias) < 200:
                        bias_require = bool(cfg.get("BIAS_REQUIRE_CONFIRMATION", True))
                        if bias_require:
                            _record_stability_sample(display, asset_type, False,
                                                     reason="htf_bias_unavailable: insufficient bias bars for EMA stack")
                            skipped.append({"pair": display, "reason": "htf_bias_unavailable: insufficient bias bars for EMA stack"})
                            continue
                        # else: allow trade without bias confirmation
                    else:
                        fresh, stale_reason = _scalp_candles_fresh(candles_bias, bias_tf, "bias")
                        if not fresh:
                            _record_stability_sample(display, asset_type, False, reason=stale_reason)
                            skipped.append({"pair": display, "reason": stale_reason})
                            continue
                        htf_bias = infer_bias_from_ema_stack(candles_bias)

                _funnel["data_available"] = True
                _funnel["candle_timeframes_available"] = ["M15", "M5", execution_tf]
                _funnel["lower_tf_candle_count"] = len(candles_exec)
                _funnel["latest_lower_tf_candle"] = str((candles_exec[-1] or {}).get("time"))
                _funnel["freshness_status"] = "fresh"

            else:
                mt5_sym = mt5_map_symbol(display)
                if not mt5_sym:
                    _record_stability_sample(display, asset_type, False, reason="no_mt5_mapping")
                    skipped.append({"pair": display, "reason": "no_mt5_mapping"})
                    continue

                market_open = mt5_market_open_state(mt5_sym)
                if not market_open.get("open"):
                    reason = str(market_open.get("reason") or "MARKET_CLOSED")
                    _record_stability_sample(display, asset_type, False, reason=reason)
                    skipped.append({"pair": display, "reason": reason})
                    continue

                sym_info = mt5_get_symbol_info(display)
                if not sym_info or sym_info.get("error"):
                    _record_stability_sample(display, asset_type, False, reason="symbol_not_available")
                    skipped.append({"pair": display, "reason": "symbol_not_available"})
                    continue

                spread_ok, spread_pips = check_spread(sym_info, asset_type, display)
                if not spread_ok:
                    spread_unit = "pips" if asset_type == "forex" else "pts"
                    spread_label = f"{spread_pips}{spread_unit}"
                    _record_stability_sample(display, asset_type, False,
                                             feature_map={"spread_pips": spread_pips},
                                             reason=f"spread_too_wide_{spread_label}")
                    skipped.append({"pair": display, "reason": f"spread_too_wide_{spread_label}"})
                    continue

                _vol_src_dominant = "mt5_tick"  # updated after overlay calls below
                structure_include_forming = bool(cfg.get("USE_FORMING_FOR_STRUCTURE", False))
                trigger_include_forming = bool(cfg.get("USE_FORMING_FOR_TRIGGER", True))
                bias_include_forming = bool(cfg.get("USE_FORMING_FOR_BIAS", structure_include_forming))

                candles_m15 = mt5_fetch_scalp_candles(
                    mt5_sym, "M15", m15_count, include_forming=structure_include_forming
                )
                candles_m15, _vol_src_m15 = _overlay_eodhd_volume_for_scalp(display, asset_type, "M15", candles_m15)
                _vol_src_structure = _vol_src_m15
                if len(candles_m15) < 30:
                    _record_stability_sample(display, asset_type, False, reason="insufficient_m15_candles")
                    skipped.append({"pair": display, "reason": "insufficient_m15_candles"})
                    continue
                fresh, stale_reason = _scalp_candles_fresh(candles_m15, "M15", "structure")
                if not fresh:
                    _record_stability_sample(display, asset_type, False, reason=stale_reason)
                    skipped.append({"pair": display, "reason": stale_reason})
                    continue

                candles_m5 = mt5_fetch_scalp_candles(
                    mt5_sym, "M5", m5_count, include_forming=trigger_include_forming
                )
                candles_m5, _vol_src_m5 = _overlay_eodhd_volume_for_scalp(display, asset_type, "M5", candles_m5)
                if len(candles_m5) < 10:
                    _record_stability_sample(display, asset_type, False, reason="insufficient_m5_candles")
                    skipped.append({"pair": display, "reason": "insufficient_m5_candles"})
                    continue
                fresh, stale_reason = _scalp_candles_fresh(candles_m5, "M5", "context")
                if not fresh:
                    _record_stability_sample(display, asset_type, False, reason=stale_reason)
                    skipped.append({"pair": display, "reason": stale_reason})
                    continue

                # dominant volume source = M15 (used for VP), fallback to M5
                _vol_src_dominant = _vol_src_m15 if _vol_src_m15 != "mt5_tick" else _vol_src_m5
                pair_dict = {
                    "display": display,
                    "symbol": mt5_sym,
                    "type": asset_type,
                    "source": "mt5",
                }
                if execution_tf == "M1":
                    candles_exec = mt5_fetch_scalp_candles(
                        mt5_sym, "M1", m1_count, include_forming=trigger_include_forming
                    )
                    candles_exec, _vol_src_m1 = _overlay_eodhd_volume_for_scalp(display, asset_type, "M1", candles_exec)
                    _vol_src_exec = _vol_src_m1
                    if len(candles_exec) < 30:
                        _record_stability_sample(display, asset_type, False, reason="insufficient_m1_candles")
                        skipped.append({"pair": display, "reason": "insufficient_m1_candles"})
                        continue
                else:
                    candles_exec = candles_m5
                    _vol_src_exec = _vol_src_m5

                fresh, stale_reason = _execution_candles_fresh(candles_exec, execution_tf)
                if not fresh:
                    _record_stability_sample(display, asset_type, False, reason=stale_reason)
                    skipped.append({"pair": display, "reason": stale_reason})
                    continue

                live_price = mt5_get_live_price(mt5_sym)
                if not live_price or live_price <= 0:
                    _record_stability_sample(display, asset_type, False, reason="MARKET_CLOSED_NO_LIVE_PRICE")
                    skipped.append({"pair": display, "reason": "MARKET_CLOSED_NO_LIVE_PRICE"})
                    continue
                current_price = live_price
                htf_bias = None

                if use_bias:
                    candles_bias = mt5_fetch_scalp_candles(
                        mt5_sym, bias_tf, h1_count, include_forming=bias_include_forming
                    )
                    if len(candles_bias) < 200:
                        bias_require = bool(cfg.get("BIAS_REQUIRE_CONFIRMATION", True))
                        if bias_require:
                            _record_stability_sample(display, asset_type, False,
                                                     reason="htf_bias_unavailable: insufficient H1 bars for EMA stack")
                            skipped.append({"pair": display, "reason": "htf_bias_unavailable: insufficient H1 bars for EMA stack"})
                            continue
                        # else: allow trade without bias confirmation
                    else:
                        fresh, stale_reason = _scalp_candles_fresh(candles_bias, bias_tf, "bias")
                        if not fresh:
                            _record_stability_sample(display, asset_type, False, reason=stale_reason)
                            skipped.append({"pair": display, "reason": stale_reason})
                            continue
                        htf_bias = infer_bias_from_ema_stack(candles_bias)

                _funnel["data_available"] = True
                _funnel["candle_timeframes_available"] = ["M15", "M5", execution_tf]
                _funnel["lower_tf_candle_count"] = len(candles_exec)
                _funnel["latest_lower_tf_candle"] = str((candles_exec[-1] or {}).get("time"))
                _funnel["freshness_status"] = "fresh"

            # ══════════════════════════════════════════════════════════════
            # FABIO VALENTINI PIPELINE
            # ══════════════════════════════════════════════════════════════

            # Pillar 1: Volume Profile — market state + location
            if not cfg.get("VP_ENABLED", True):
                _record_stability_sample(display, asset_type, False, reason="vp_disabled")
                skipped.append({"pair": display, "reason": "vp_disabled"})
                continue
            vp_lookback = max(20, int(cfg.get("VP_LOOKBACK_BARS", 50)))
            vp_anchor_mode = "not_built"
            trade_bucket_vp_fallback_reason = None
            vp = (
                _build_trade_bucket_volume_profile(display)
                if asset_type == "crypto" and cfg.get("TRADE_BUCKET_VP_ENABLED", True)
                else {"valid": False}
            )
            if vp.get("valid"):
                vp_anchor_mode = "trade_bucket_session"
            if not vp.get("valid"):
                if asset_type == "crypto" and cfg.get("TRADE_BUCKET_VP_ENABLED", True):
                    _tb_reason = str(vp.get("reason") or "unknown")
                    trade_bucket_vp_fallback_reason = f"vp_fallback:candle_profile_after_{_tb_reason}"
                    log.info(
                        "[SCALP-VP] trade_bucket fallback: %s reason=%s — using candle VP",
                        display, _tb_reason,
                    )
                # Session-aware VP: for MT5/EODHD assets, prefer prior-session candles
                # so overnight/pre-market low-volume bars don't dilute the profile.
                _vp_candles = candles_m15[-vp_lookback:]
                if asset_type in ("forex", "index", "stock", "commodity") and cfg.get("VP_SESSION_AWARE", True):
                    try:
                        from volume_profile import split_completed_sessions
                        _sessions = split_completed_sessions(
                            candles_m15,
                            asset_type,
                            session_mode=_resolved_normalized_session_mode(cfg, asset_type=asset_type),
                        )
                        _prev = _sessions.get("prev_session_candles", [])
                        if len(_prev) >= 20:
                            _vp_candles = _prev
                            vp_anchor_mode = "prior_session"
                            log.debug("[SCALP-VP] %s using prior-session VP (%d bars)", display, len(_prev))
                    except Exception:
                        pass  # fall through to fixed lookback
                try:
                    vp = _build_volume_profile(_vp_candles, asset_type=asset_type, score_group=_scalp_score_group)
                except TypeError:
                    vp = _build_volume_profile(_vp_candles)
                if vp.get("valid"):
                    vp.setdefault("volume_source", "candles")
                    vp = _merge_vp_volume_source_tag(dict(vp), _vol_src_dominant)
                    if (
                        asset_type == "stock"
                        and cfg.get("VP_INVALIDATE_RANGE_PROXY_FOR_STOCKS", True)
                        and "range_proxy" in str(vp.get("volume_source") or "").lower()
                    ):
                        vp = {"valid": False, "reason": "range_proxy_volume_stock"}
                    if (
                        vp.get("valid")
                        and asset_type == "stock"
                        and cfg.get("BLOCK_STOCK_VP_ON_EODHD_1H_VOLUME", False)
                        and "eodhd_1h" in str(_vol_src_structure or "").lower()
                    ):
                        vp = {"valid": False, "reason": "eodhd_1h_volume_overlay_stale_risk"}
                    if vp.get("valid") and vp_anchor_mode != "prior_session":
                        vp_anchor_mode = "fixed_lookback"
            if not vp.get("valid"):
                _vp_reason = f"vp_invalid:{vp.get('reason', '?')}"
                _record_stability_sample(display, asset_type, False, reason=_vp_reason)
                skipped.append({"pair": display, "reason": _vp_reason})
                _funnel["gate_result"] = "NO_SETUP"
                _funnel["fail_reasons"].append(_vp_reason)
                continue

            market_state = _classify_market_state(vp)
            # Compute M15 ATR for proximity + buffer scaling.
            _atr_m15 = _calc_m15_atr(candles_m15, period=int(cfg.get("ATR_PERIOD", 14)))
            try:
                price_loc = _locate_price_vs_vp(
                    current_price,
                    vp,
                    atr_m15=_atr_m15,
                    asset_type=asset_type,
                    score_group=_scalp_score_group,
                )
            except TypeError:
                price_loc = _locate_price_vs_vp(current_price, vp, atr_m15=_atr_m15)
            _funnel["volume_profile_available"] = True
            _funnel["poc"] = vp.get("poc")
            _funnel["vah"] = vp.get("vah")
            _funnel["val"] = vp.get("val")
            _funnel["lvn_count"] = len(vp.get("lvn_levels", []))
            _funnel["price_near_poc"] = price_loc.get("location") == "at_poc"
            _funnel["price_near_vah"] = price_loc.get("location") == "at_vah"
            _funnel["price_near_val"] = price_loc.get("location") == "at_val"
            profile_anchor_shadow = _engine_d_profile_anchor_shadow(
                candles_m15=candles_m15,
                vp_lookback=vp_lookback,
                vp=vp,
                active_anchor_mode=vp_anchor_mode,
                volume_source=vp.get("volume_source", _vol_src_structure),
            )
            _funnel["diagnostic_notes"]["profile_anchor_shadow"] = profile_anchor_shadow
            if trade_bucket_vp_fallback_reason:
                _funnel["diagnostic_notes"]["vp_fallback_reason"] = trade_bucket_vp_fallback_reason

            # Shadow proximity simulation (report-only)
            if shadow_proximity_simulations is not None:
                try:
                    _atr_shadow = 0.0
                    if candles_m15 and len(candles_m15) >= 20:
                        _hh = max(float(c["high"]) for c in candles_m15[-20:])
                        _ll = min(float(c["low"]) for c in candles_m15[-20:])
                        _atr_shadow = (_hh - _ll) / 20.0
                    _tick = float(sym_info.get("point", 1e-6)) if sym_info else 1e-6
                    _funnel["diagnostic_notes"]["shadow_proximity"] = shadow_proximity_simulations(
                        current_price, vp, _atr_shadow, tick_size=_tick
                    )
                except Exception as _sp_err:
                    _funnel["diagnostic_notes"]["shadow_proximity_error"] = str(_sp_err)

            # Pillar 2: Aggression — absorption, CVD, AAA
            try:
                absorption = _check_absorption(candles_exec, asset_type=asset_type, score_group=_scalp_score_group)
            except TypeError:
                absorption = _check_absorption(candles_exec)
            cvd = (
                _check_trade_bucket_cvd(display)
                if asset_type == "crypto" and cfg.get("TRADE_BUCKET_CVD_ENABLED", True)
                else {"source": "disabled"}
            )
            if not cvd.get("direction"):
                try:
                    cvd = _check_cvd(candles_exec, asset_type=asset_type, score_group=_scalp_score_group)
                except TypeError:
                    cvd = _check_cvd(candles_exec)
                cvd["source"] = "candles"
            if cfg.get("AAA_ENABLED", True):
                try:
                    aaa = _check_aaa_sequence(
                        candles_exec,
                        absorption,
                        cvd,
                        asset_type=asset_type,
                        score_group=_scalp_score_group,
                    )
                except TypeError:
                    aaa = _check_aaa_sequence(candles_exec, absorption, cvd, asset_type=asset_type)
            else:
                aaa = {"complete": False, "phase": "disabled"}

            # Pillar 3: VWAP directional lean
            vwap = _check_vwap_lean(candles_m15, current_price) if cfg.get("VWAP_ENABLED", True) else {"lean": None, "vwap_value": 0}
            _funnel["cvd_available"] = bool(cvd.get("direction"))
            _funnel["cvd_bias"] = cvd.get("direction")
            _funnel["absorption_detected"] = bool(absorption.get("detected"))
            _funnel["vwap_available"] = bool(vwap.get("vwap_value"))
            _funnel["vwap_bias"] = vwap.get("lean")
            data_fidelity = _engine_d_data_fidelity(
                vp=vp,
                cvd=cvd,
                absorption=absorption,
                asset_type=asset_type,
                structure_volume_source=_vol_src_structure,
                execution_volume_source=_vol_src_exec,
                active_profile_anchor=vp_anchor_mode,
            )
            _funnel["diagnostic_notes"]["data_fidelity"] = data_fidelity
            _data_fail_reasons: list[str] = []
            if asset_type == "stock" and cfg.get("REQUIRE_REAL_VOLUME_FOR_STOCKS", True):
                _data_fail_reasons.extend(
                    _stock_real_volume_fail_reasons(
                        data_fidelity,
                        _vol_src_structure,
                        _vol_src_exec,
                        _vol_src_dominant,
                    )
                )
            if (
                asset_type == "crypto"
                and cfg.get("STRICT_FABIO_GATE_ENABLED", True)
                and cfg.get("REQUIRE_AGGTRADE_FOR_CRYPTO_STRICT", True)
            ):
                if not data_fidelity.get("vp_uses_real_trade_buckets") or not data_fidelity.get("cvd_uses_real_trade_buckets"):
                    _data_fail_reasons.append("aggtrade_required_for_crypto_strict")

            # Setup classification
            setup = _classify_setup(market_state, price_loc, absorption, cvd, aaa, vwap, htf_bias, asset_type=asset_type, candles=candles_exec)
            if not setup.get("valid"):
                _setup_reason = f"no_setup:{setup.get('reason', '?')}"
                _record_stability_sample(display, asset_type, False, reason=_setup_reason)
                _skip_row = {"pair": display, "reason": _setup_reason}
                if trade_bucket_vp_fallback_reason:
                    _skip_row["diagnostic_reason"] = trade_bucket_vp_fallback_reason
                skipped.append(_skip_row)
                _funnel["gate_result"] = "NO_SETUP"
                _funnel["fail_reasons"].append(_setup_reason)
                continue

            direction = setup["direction"]
            _funnel["setup_type"] = setup.get("setup_type")
            _funnel["setup_direction"] = direction

            candidate_fail_reasons: list[str] = []
            candidate_soft_warnings: list[str] = []
            if trade_bucket_vp_fallback_reason:
                candidate_soft_warnings.append(trade_bucket_vp_fallback_reason)
            candidate_fail_reasons.extend(_data_fail_reasons)
            if use_bias and htf_bias and direction != htf_bias:
                _ct_reason = f"counter_trend:{direction}_vs_{bias_tf}_{htf_bias}"
                candidate_soft_warnings.append(_ct_reason)

            # Risk levels — use per-group min_rr if available (e.g. forex_majors/crosses)
            _min_rr = _scalp_min_rr_for_group(asset_type, _scalp_score_group)
            try:
                levels = calculate_scalp_levels(
                    direction,
                    current_price,
                    vp,
                    setup["setup_type"],
                    sym_info,
                    asset_type,
                    min_rr_override=_min_rr,
                    atr_m15=_atr_m15,
                    score_group=_scalp_score_group,
                )
            except TypeError:
                levels = calculate_scalp_levels(
                    direction,
                    current_price,
                    vp,
                    setup["setup_type"],
                    sym_info,
                    asset_type,
                    min_rr_override=_min_rr,
                    atr_m15=_atr_m15,
                )
            if levels.get("rr_below_min"):
                log.warning(
                    f"[SCALP] {display}: {setup['setup_type']} RR {levels['rr']:.2f} < MIN_RR "
                    f"- surfacing as watchlist candidate (mechanical 1R TP invalid)"
                )
                candidate_fail_reasons.append("rr_below_min")
            if levels.get("structure_target_close"):
                candidate_soft_warnings.append("structure_target_close")

            # --- ENGINE D FEE GUARD ---
            fee_guard_metrics = {}
            if cfg.get("ENGINE_D_FEE_GUARD_ENABLED", True):
                risk_distance_abs = abs(levels["entry"] - levels["sl"])
                risk_distance_pct = risk_distance_abs / levels["entry"] if levels["entry"] > 0 else 0
                
                estimated_fee_pct, estimated_slippage_pct = _scalp_cost_assumptions(cfg, asset_type)
                estimated_total_cost_pct = estimated_fee_pct + estimated_slippage_pct
                
                cost_as_R = estimated_total_cost_pct / risk_distance_pct if risk_distance_pct > 0 else float('inf')
                
                max_cost_R = float(cfg.get("ENGINE_D_MAX_COST_R", 0.20))
                min_stop_pct = float(cfg.get("ENGINE_D_MIN_STOP_PCT", 0.0005))
                fee_guard_metrics = {
                    "engine_d_reject_reason": None,
                    "risk_distance_pct": risk_distance_pct,
                    "estimated_total_cost_pct": estimated_total_cost_pct,
                    "cost_as_R": cost_as_R,
                    "min_required_stop_pct": min_stop_pct,
                    "max_allowed_cost_R": max_cost_R,
                }
                
                _fee_reason = None
                if risk_distance_abs <= 0:
                    _fee_reason = "fee_guard_zero_stop"
                elif risk_distance_pct < min_stop_pct:
                    _fee_reason = "fee_guard_micro_stop"
                elif cost_as_R > max_cost_R:
                    _fee_reason = "fee_guard_high_cost"
                if _fee_reason:
                    log.warning(
                        "[SCALP] %s on %s: risk_abs=%.8f stop_pct=%.5f min_stop_pct=%.5f "
                        "cost_as_R=%.2f max_cost_R=%.2f",
                        _fee_reason,
                        display,
                        risk_distance_abs,
                        risk_distance_pct,
                        min_stop_pct,
                        cost_as_R,
                        max_cost_R,
                    )
                    candidate_fail_reasons.append(_fee_reason)
                    fee_guard_metrics["engine_d_reject_reason"] = _fee_reason
                _funnel["diagnostic_notes"].update(fee_guard_metrics)

            advisory = _build_engine_d_advisory(
                market_state=market_state,
                price_loc=price_loc,
                setup=setup,
                vwap=vwap,
                direction=direction,
                levels=levels,
                absorption=absorption,
                cvd=cvd,
                aaa=aaa,
                htf_bias=htf_bias,
            )
            aggression_fidelity = _engine_d_aggression_fidelity(absorption, cvd, aaa, vwap, setup_direction=direction)
            _funnel["diagnostic_notes"].update(aggression_fidelity)
            strict_fabio_shadow = _engine_d_strict_fabio_shadow(
                market_state=market_state,
                price_loc=price_loc,
                setup=setup,
                aggression_fidelity=aggression_fidelity,
                current_gate_result="CANDIDATE",
            )
            if cfg.get("STRICT_FABIO_GATE_ENABLED", True) and not strict_fabio_shadow.get("strict_fabio_pass"):
                candidate_fail_reasons.append(
                    f"strict_fabio:{strict_fabio_shadow.get('strict_fabio_reason', 'failed')}"
                )
            proxy_cfg = CONFIG.get("SCALP_ENGINE", {})
            if asset_type == "stock":
                premarket_delta_proxy_levels = _build_premarket_delta_proxy_levels(
                    candles_exec,
                    top_n=int(proxy_cfg.get("PREMARKET_DELTA_PROXY_TOP_LEVELS", 3)),
                    min_candles=int(proxy_cfg.get("PREMARKET_DELTA_PROXY_MIN_CANDLES", 10)),
                    bucket_size=float(sym_info.get("point") or 0.0) * 10.0 if sym_info else None,
                )
            else:
                premarket_delta_proxy_levels = {
                    "label": "premarket_delta_proxy_levels",
                    "valid": False,
                    "reason": "stock_only",
                    "levels": [],
                }

            # Quality grade
            if cfg.get("AI_GRADING", True):
                grade_sessions = get_grade_sessions_for_mode(asset_type)
                quality = ai_quality_grade(vp, price_loc, absorption, cvd, aaa, vwap, setup, grade_sessions, spread_pips, htf_bias, asset_type, display)
            else:
                quality = {"score": 50, "grade": "C", "reasons": ["grading_disabled"], "size_multiplier": 1.0}

            # Grade gate
            grade = quality["grade"]
            _GRADE_RANK = {"A": 4, "B": 3, "C": 2, "D": 1}
            _funnel["setup_score"] = quality.get("score")
            _funnel["setup_grade"] = grade
            _funnel["rr"] = levels.get("rr")
            _funnel["rr_ok"] = not levels.get("rr_below_min", False)
            _funnel["entry"] = levels.get("entry")
            _funnel["sl"] = levels.get("sl")
            _funnel["tp"] = levels.get("tp1")

            execution_min_grade = _scalp_execution_min_grade(cfg)
            grade_rank = _GRADE_RANK.get(grade, 0)
            execution_rank = _GRADE_RANK.get(execution_min_grade, 3)
            gate_result = "PASS"
            executable = True
            candidate_status_reason = "method_valid"
            if grade == "D":
                gate_result = "BLOCKED"
                executable = False
                candidate_status_reason = "grade_D_context_only"
                candidate_fail_reasons.append("grade_D_context_only")
            elif candidate_fail_reasons or grade_rank < execution_rank:
                gate_result = "WATCHLIST"
                executable = False
                candidate_status_reason = ",".join(candidate_fail_reasons) if candidate_fail_reasons else f"grade_{grade}_watchlist"
                if grade_rank < execution_rank:
                    candidate_soft_warnings.append(f"grade_{grade}_below_execution_min_{execution_min_grade}")
            _funnel["gate_result"] = gate_result
            _funnel["fail_reasons"] = list(candidate_fail_reasons)
            _funnel["soft_warnings"] = list(candidate_soft_warnings)
            strict_fabio_shadow = _engine_d_strict_fabio_shadow(
                market_state=market_state,
                price_loc=price_loc,
                setup=setup,
                aggression_fidelity=aggression_fidelity,
                current_gate_result=gate_result,
            )
            _funnel["diagnostic_notes"].update(strict_fabio_shadow)

            # ── Build signal dict (preserves keys required by athena.py) ─
            signal = {
                "pair":            display,
                "display":         display,
                "symbol":          display.replace("/", "") if asset_type == "crypto" else None,
                "mt5_symbol":      mt5_sym,
                "type":            asset_type,
                "direction":       direction,
                "price":           levels["entry"],
                "sl":              levels["sl"],
                "tp_partial":      levels["tp_partial"],    # Fabio: first scale-out at +1R ("pay yourself first")
                "rr_partial":      1.0,                     # always 1.0 by construction of tp_partial
                "tp1":             levels["tp1"],            # Engine D self-pay target at configured R
                "tp2":             levels["tp2"],            # optional VP/structure runner
                "structural_tp":    levels.get("structural_tp"),
                "structural_rr":    levels.get("structural_rr"),
                "structure_target_close": levels.get("structure_target_close"),
                "rr1":             levels["rr"],
                "sl_distance":     levels["sl_distance"],
                "sl_method":       levels["sl_method"],
                # VP fields
                "zone_type":       setup["setup_type"],
                "zone_high":       vp.get("vah"),
                "zone_low":        vp.get("val"),
                "zone_level":      vp.get("poc"),
                "zone_conditions": setup.get("reasons", []),
                # Trigger/momentum fields mapped from new pipeline
                "trigger_type":    setup["setup_type"],
                "momentum_method": "absorption" if absorption.get("detected") else "cvd",
                # Quality
                "ai_score":        quality["score"],
                "ai_grade":        quality["grade"],
                "ai_reasons":      quality["reasons"],
                "original_size_multiplier": quality["size_multiplier"],
                "size_multiplier":          quality["size_multiplier"],
                "gate_result":      gate_result,
                "executable":       executable,
                "candidate_status": candidate_status_reason,
                "fail_reasons":     list(candidate_fail_reasons),
                "soft_warnings":    list(candidate_soft_warnings),
                "rr_ok":            not levels.get("rr_below_min", False),
                "fee_guard":        fee_guard_metrics,
                # Context
                "spread_pips":     spread_pips,
                "session_risk_state": get_scalp_session_risk_state(),
                "session":         active_session,
                "execution_tf":    execution_tf,
                "context_tf":      "M5",
                "structure_tf":    "M15",
                "ema21":           None,  # replaced by VWAP
                "vwap":            vwap.get("vwap_value"),
                "market_state":    market_state,
                "vp_poc":          vp.get("poc"),
                "vp_vah":          vp.get("vah"),
                "vp_val":          vp.get("val"),
                "vp_lvn_count":    len(vp.get("lvn_levels", [])),
                "vp_volume_source": vp.get("volume_source", _vol_src_dominant),
                "vp_bucket_count":  vp.get("bucket_count"),
                "vp_fidelity":      data_fidelity.get("vp_fidelity"),
                "vp_is_proxy":      data_fidelity.get("vp_is_proxy"),
                "vp_uses_real_trade_buckets": data_fidelity.get("vp_uses_real_trade_buckets"),
                "absorption_count": absorption.get("count", 0),
                "absorption_source": data_fidelity.get("absorption_source"),
                "absorption_fidelity": data_fidelity.get("absorption_fidelity"),
                "absorption_is_proxy": data_fidelity.get("absorption_is_proxy"),
                "cvd_direction":   cvd.get("direction"),
                "cvd_slope":       cvd.get("cvd_slope"),
                "cvd_source":      cvd.get("source", "candles"),
                "cvd_bucket_count": cvd.get("bucket_count"),
                "cvd_fidelity":     data_fidelity.get("cvd_fidelity"),
                "cvd_is_proxy":     data_fidelity.get("cvd_is_proxy"),
                "cvd_uses_real_trade_buckets": data_fidelity.get("cvd_uses_real_trade_buckets"),
                "aaa_complete":    aaa.get("complete", False),
                "aggression_source": aggression_fidelity.get("aggression_source"),
                "aggression_source_raw": aggression_fidelity.get("aggression_source_raw"),
                "aggression_source_is_proxy": aggression_fidelity.get("aggression_source_is_proxy"),
                "aggression_confirmed": aggression_fidelity.get("aggression_confirmed"),
                "aggression_components": aggression_fidelity.get("aggression_components"),
                "aggression_uses_real_order_flow": data_fidelity.get("aggression_uses_real_order_flow"),
                "data_fidelity": data_fidelity,
                "strict_fabio_pass": strict_fabio_shadow.get("strict_fabio_pass"),
                "strict_fabio_reason": strict_fabio_shadow.get("strict_fabio_reason"),
                "strict_fabio_missing_pillars": strict_fabio_shadow.get("strict_fabio_missing_pillars"),
                "strict_fabio_pillars": strict_fabio_shadow.get("strict_fabio_pillars"),
                "current_vs_strict_status": strict_fabio_shadow.get("current_vs_strict_status"),
                "profile_anchor_mode": profile_anchor_shadow.get("active_anchor", {}).get("mode"),
                "profile_anchor_bars": profile_anchor_shadow.get("active_anchor", {}).get("bars"),
                "profile_anchor_start": profile_anchor_shadow.get("active_anchor", {}).get("start_time"),
                "profile_anchor_end": profile_anchor_shadow.get("active_anchor", {}).get("end_time"),
                "profile_anchor_shadow": profile_anchor_shadow,
                "htf_bias":        htf_bias,
                "htf_bias_tf":     bias_tf if use_bias else None,
                "advisory":        advisory,
                "advisory_summary": advisory.get("summary"),
                "premarket_delta_cluster_type": "proxy",
                "premarket_delta_proxy_levels": premarket_delta_proxy_levels,
                "timestamp":       datetime.now(timezone.utc).isoformat(),
                "engine":          "SCALP",
                "engine_type":     "engine_d",
                # Fields required by risk_engine.risk_check().
                # Audit 8577d0 §1.9 / §4.4 fix: emit raw 0-100 score with
                # explicit maxScore=100 so signal_debate / Engine B AI prompts
                # do not silently mix Engine A's 0-3 scale with Engine D's
                # 0-100 quality rubric (same field name, two rubrics).
                "qualityScore":    float(quality["score"]),
                "maxQualityScore": 100.0,
                "confluenceScore": float(quality["score"]),
                "maxScore":        100.0,
            }

            _scalp_cf_by_tf: dict[str, list] = {
                "M15": candles_m15,
                "M5": candles_m5,
                str(execution_tf).upper(): candles_exec,
            }
            if use_bias and candles_bias:
                _scalp_cf_by_tf[str(bias_tf).upper()] = candles_bias
            _attach_engine_d_data_freshness_to_signal(signal, pair_dict=pair_dict, candles_by_tf=_scalp_cf_by_tf)

            # Apply consecutive-loss halving and +2R size cap. The +2R rule caps
            # size at 0.5x; it does not increase smaller grade-based sizes.
            _risk_snapshot = get_scalp_session_risk_state()
            _original_size_multiplier = float(
                signal.get("original_size_multiplier", signal.get("size_multiplier", 1.0)) or 1.0
            )
            if cfg.get("CONSECUTIVE_LOSS_HALVE", True) and _risk_snapshot["consecutive_losses"] >= 2:
                signal["size_multiplier"] = signal.get("size_multiplier", 1.0) * 0.5
                signal["ai_reasons"] = signal.get("ai_reasons", []) + ["size_halved:consecutive_losses"]
            if _risk_snapshot.get("size_cut_active"):
                signal["size_multiplier"] = min(signal.get("size_multiplier", 1.0), 0.5)
                signal["ai_reasons"] = signal.get("ai_reasons", []) + ["size_cut:+2R_reached"]
            _final_size_multiplier = float(signal.get("size_multiplier", 0.0) or 0.0)
            if _original_size_multiplier > 0 and _final_size_multiplier < (_original_size_multiplier * 0.5):
                log.info(
                    "[SCALP] size multiplier reduced >50%% on %s: original=%.3f final=%.3f reasons=%s",
                    display,
                    _original_size_multiplier,
                    _final_size_multiplier,
                    signal.get("ai_reasons", []),
                )
            
            signals.append(signal)
            _record_stability_sample(
                display, asset_type, executable,
                score_norm=quality["score"] / 100.0,
                feature_map={
                    "market_state": 1.0 if market_state == "balance" else 0.0,
                    "location_at_value_edge": 1.0 if price_loc.get("location") in ("at_vah", "at_val") else 0.0,
                    "location_at_lvn": 1.0 if price_loc.get("location") == "at_lvn" else 0.0,
                    "location_outside_va": 1.0 if price_loc.get("location") == "outside_va" else 0.0,
                    "absorption": absorption.get("detected", False),
                    "cvd_aligned": cvd.get("direction") == direction,
                    "aaa_complete": aaa.get("complete", False),
                    "vwap_aligned": vwap.get("lean") == direction,
                    "spread_pips": spread_pips,
                    "bias_aligned": htf_bias == direction if htf_bias else True,
                    "gate_result": gate_result,
                },
                reason=None if executable else candidate_status_reason,
            )

        except Exception as e:
            log.error(f"[SCALP] Error on {display}: {e}")
            _record_stability_sample(display, _guess_asset_type(display), False, reason=f"error:{str(e)[:60]}")
            skipped.append({"pair": display, "reason": f"error:{str(e)[:60]}"})
        finally:
            if _funnel.get("gate_result") == "NOT_CALLED" and len(skipped) > _skip_start:
                _reason = str((skipped[-1] or {}).get("reason") or "BLOCKED")
                _funnel["gate_result"] = "NO_SETUP" if _reason.startswith("no_setup:") else "BLOCKED"
                _funnel["fail_reasons"].append(_reason)
            if log_engine_d_funnel is not None and build_funnel_row is not None:
                try:
                    log_engine_d_funnel(build_funnel_row(**_funnel))
                except Exception as _f_err:
                    log.debug("[SCALP-AUDIT] funnel log error: %s", _f_err)

    signals.sort(key=lambda s: s.get("ai_score", 0), reverse=True)

    log.warning(
        f"[SCALP] Scan: {len(pairs_or_symbols)} pairs | "
        f"{len(signals)} signals | {len(skipped)} skipped | session={session_name}"
    )

    if skipped:
        reason_counts = Counter(s.get("reason", "unknown") for s in skipped)
        for reason, count in reason_counts.most_common():
            log.warning(f"[SCALP] Skip reason: {reason} × {count}")
        noisy_reasons = ("MARKET_DATA_STALE_", "MARKET_CLOSED_NO_TICK", "MARKET_CLOSED_STALE_",
                          "spread_too_wide_", "OUTSIDE_SESSION", "off_hours")
        for s in skipped:
            reason = str(s.get("reason") or "")
            msg = f"[SCALP] Skipped {s.get('pair')} - {reason}"
            if reason.startswith(noisy_reasons):
                log.debug(msg)
            else:
                log.warning(msg)

    return _finalize_run_scalp_scan_result(
        signals=signals,
        skipped=skipped,
        scanned=len(pairs_or_symbols),
        session_name=session_name,
        sessions_active=sessions,
        reason=None,
    )


# ═══════════════════════════════════════════════════════════════════════════════
# ASSET TYPE HELPER
# ═══════════════════════════════════════════════════════════════════════════════

def _guess_asset_type(display: str) -> str:
    """Infer asset type from display name for buffers and spread limits."""
    meta = _SCALP_PAIR_META_BY_DISPLAY.get(display)
    if isinstance(meta, dict) and meta.get("type"):
        return str(meta.get("type")).lower()

    forex_currencies = {
        "EUR", "GBP", "USD", "JPY", "AUD", "NZD", "CAD", "CHF",
        "BRL", "INR", "MXN", "SGD", "ZAR",
    }
    parts = display.replace("/", " ").split()
    if len(parts) == 2 and all(p in forex_currencies for p in parts):
        return "forex"
    compact = display.replace("/", "").replace(" ", "").upper()
    if len(compact) == 6 and compact[:3] in forex_currencies and compact[3:] in forex_currencies:
        return "forex"
    commodity_names = {
        "Aluminium", "Lead", "Nickel", "Zinc", "Gasoline", "Cattle", "Cocoa",
        "Coffee", "Corn", "Cotton", "Soybeans", "Sugar", "Wheat",
    }
    if (
        "XAU" in display or "XAG" in display or "Oil" in display
        or "Nat Gas" in display or "Copper" in display or "XPT" in display
        or "XPD" in display or display in commodity_names
    ):
        return "commodity"
    if "USDT" in display or "BTC" in display or "ETH" in display:
        return "crypto"
    if any(x in display for x in ["S&P", "Nasdaq", "NASDAQ", "Dow", "DAX", "UK100", "ASX", "Nikkei", "Hang", "USTEC", "NAS100", "EURX", "JPYX", "USDX"]):
        return "index"
    return "stock"


# ═══════════════════════════════════════════════════════════════════════════════
# SCALP PAIRS LIST
# ═══════════════════════════════════════════════════════════════════════════════

def get_scalp_pairs(active_pair_dicts: Optional[list[dict[str, Any]]] = None) -> list:
    """Return display names to scan for Engine D.

    Priority:
    1. SCALP_ENGINE.SCALP_PAIRS in config — explicit override.
    2. If active_pair_dicts is provided: enabled pairs with source mt5 or binance.
    3. Legacy built-in list (~54) when no runtime list is passed.
    """
    cfg = CONFIG.get("SCALP_ENGINE", {})
    configured = cfg.get("SCALP_PAIRS", [])
    if configured:
        return list(configured)

    if active_pair_dicts is not None:
        out: list[str] = []
        _SCALP_PAIR_META_BY_DISPLAY.clear()
        for p in active_pair_dicts:
            if not isinstance(p, dict) or not p.get("enabled", True):
                continue
            src = str(p.get("source") or "").lower()
            typ = str(p.get("type") or "").lower()
            disp = (p.get("display") or p.get("symbol") or "").strip()
            if not disp:
                continue
            if src == "mt5" and typ in ("forex", "commodity", "index", "stock"):
                _SCALP_PAIR_META_BY_DISPLAY[disp] = dict(p)
                out.append(disp)
            elif src == "binance" and typ == "crypto":
                _SCALP_PAIR_META_BY_DISPLAY[disp] = dict(p)
                out.append(disp)
        return sorted(set(out), key=str.casefold)

    mt5_forex = [
        "EUR/USD", "GBP/USD", "USD/JPY", "AUD/USD", "NZD/USD",
        "USD/CAD", "USD/CHF", "EUR/JPY", "GBP/JPY", "EUR/AUD",
        "EUR/GBP", "AUD/JPY", "GBP/AUD", "EUR/CHF",
    ]
    mt5_exotic = [
        "USD/ZAR", "USD/MXN", "USD/SGD",
    ]
    mt5_commodities = [
        "XAU/USD", "XAG/USD", "WTI Oil", "Brent Oil", "Nat Gas",
        "Copper", "XPT/USD", "XPD/USD",
    ]
    mt5_indices = [
        "S&P 500", "Nasdaq", "Dow Jones", "DAX 40", "UK100",
        "ASX 200", "Nikkei 225", "Euro Stoxx 50",
    ]
    mt5_stocks = [
        "AAPL", "TSLA", "NVDA", "MSFT", "AMZN", "META",
        "GOOG", "JPM", "NFLX", "AMD", "COIN", "PLTR",
    ]

    return (
        mt5_forex + mt5_exotic + mt5_commodities + mt5_indices + mt5_stocks
        + _CRYPTO_SCALP_PAIRS
    )


def displays_for_scalp_scan(
    active_pair_dicts: Optional[list[dict[str, Any]]],
    *,
    disabled_displays: set[str] | list[str] | tuple[str, ...] | None = None,
) -> list[str]:
    """Default Engine D scan roster when the API client omits ``pairs``.

    Reads ``SCALP_ENGINE.SCALP_SCAN_UNIVERSE``:
    - ``all_active`` (default): every enabled pair in ``active_pair_dicts`` whose
      ``display`` is not in ``disabled_displays`` (mirrors full-scan spirit).
    - ``scalp``: legacy narrow universe via :func:`get_scalp_pairs`.
    """
    cfg = CONFIG.get("SCALP_ENGINE") or {}
    mode = str(cfg.get("SCALP_SCAN_UNIVERSE", "all_active") or "all_active").strip().lower()
    if mode == "scalp":
        return list(get_scalp_pairs(active_pair_dicts))

    if mode != "all_active":
        log.warning(
            "[SCALP] Unknown SCALP_SCAN_UNIVERSE %r — using all_active",
            cfg.get("SCALP_SCAN_UNIVERSE"),
        )

    raw = disabled_displays or set()
    if isinstance(raw, (list, tuple)):
        banned = {str(d).strip() for d in raw}
    else:
        banned = {str(d).strip() for d in raw}

    out: list[str] = []
    for p in active_pair_dicts or []:
        if not isinstance(p, dict) or not p.get("enabled", True):
            continue
        disp = str((p.get("display") or p.get("symbol") or "")).strip()
        if not disp or disp in banned:
            continue
        out.append(disp)
    return sorted(set(out), key=str.casefold)
