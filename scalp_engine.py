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
import pandas as pd
from datetime import datetime, timezone
from typing import Any, Optional
from zoneinfo import ZoneInfo

from config import CONFIG
from stability_monitor import record_signal_event

log = logging.getLogger("sentinel.scalp")


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


def _reset_session_state_if_new_day():
    """Reset daily counters at UTC midnight."""
    today = _current_utc_datetime().date()
    if _session_state["date"] != today:
        _session_state["date"] = today
        _session_state["consecutive_losses"] = 0
        _session_state["total_losses_today"] = 0
        _session_state["net_r_today"] = 0.0
        _session_state["size_cut_active"] = False


def record_scalp_trade_outcome(r_multiple: float):
    """Called after a scalp trade closes to update session risk state."""
    _reset_session_state_if_new_day()
    _session_state["net_r_today"] += r_multiple
    if r_multiple <= 0:
        _session_state["consecutive_losses"] += 1
        _session_state["total_losses_today"] += 1
    else:
        _session_state["consecutive_losses"] = 0
    # Check +2R size cut
    cfg = CONFIG.get("SCALP_ENGINE", {})
    if cfg.get("SIZE_CUT_AFTER_2R", True) and _session_state["net_r_today"] >= 2.0:
        _session_state["size_cut_active"] = True


def get_scalp_session_risk_state() -> dict:
    """Return current session risk state for UI/logging."""
    _reset_session_state_if_new_day()
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
    last_close = closes[-1]

    if ema21 > ema50 > ema200 and last_close >= ema21:
        return "LONG"
    if ema21 < ema50 < ema200 and last_close <= ema21:
        return "SHORT"
    return None


# ═══════════════════════════════════════════════════════════════════════════════
# SESSION FILTER
# ═══════════════════════════════════════════════════════════════════════════════

def _current_utc_datetime() -> datetime:
    return datetime.now(timezone.utc)


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


def get_current_sessions() -> list:
    """Return active session names using DST-aware local market clocks."""
    now_utc = _current_utc_datetime()
    now_london_h = now_utc.astimezone(_TZ_LONDON).hour
    now_ny_h = now_utc.astimezone(_TZ_NEW_YORK).hour
    active = []
    lon_start, lon_end = _FOREX_LOCAL_SESSIONS["london"]
    ny_start, ny_end = _FOREX_LOCAL_SESSIONS["new_york"]
    if lon_start <= now_london_h < lon_end:
        active.append("london")
    if ny_start <= now_ny_h < ny_end:
        active.append("new_york")
    return active or ["off_hours"]


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
    if not cfg.get("SESSION_FILTER", True):
        return True, "all"

    mode_key = "BT_SESSION_MODE" if backtest else "SESSION_MODE"
    raw_mode = str(cfg.get(mode_key, cfg.get("SESSION_MODE", "new_york")) or "new_york").strip().lower()
    mode = raw_mode if raw_mode not in {"inherit", "default"} else str(cfg.get("SESSION_MODE", "new_york")).strip().lower()
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
            london_open_utc_minute = 7 * 60  # 07:00 UTC
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
                london_open_utc_minute = 7 * 60  # 07:00 UTC
                now_utc_minute = current_utc.hour * 60 + current_utc.minute
                if london_open_utc_minute <= now_utc_minute < london_open_utc_minute + skip_lon:
                    return False, "LONDON_OPEN_COOLDOWN"
            return True, "london_ny"
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

def check_spread(symbol_info: dict, asset_type: str) -> tuple:
    """Validate spread within limits.  Returns (ok: bool, spread_pips: float)."""
    cfg = CONFIG.get("SCALP_ENGINE", {})
    max_spreads = cfg.get("MAX_SPREAD_PIPS", {})

    spread_raw = symbol_info.get("spread", 0)
    point = symbol_info.get("point", 0.00001)
    spread_price = spread_raw * point

    digits = symbol_info.get("digits", 5)
    pip_size = point * 10 if digits >= 4 else point
    spread_pips = spread_price / pip_size if pip_size > 0 else 0

    max_spread = max_spreads.get(asset_type, max_spreads.get("forex", 4))

    if asset_type == "crypto":
        return True, spread_pips

    ok = spread_pips <= max_spread
    return ok, round(spread_pips, 2)


# ═══════════════════════════════════════════════════════════════════════════════
# PILLAR 1 — VOLUME PROFILE: Market State + Location
# ═══════════════════════════════════════════════════════════════════════════════

def _build_volume_profile(candles: list) -> dict:
    """Compute Volume Profile over the given candles.

    Returns {poc, vah, val, lvn_levels[], distribution[], balance_ratio}.
    Uses volume_profile.compute_fixed_range_volume_profile when available,
    otherwise falls back to a lightweight internal histogram.
    """
    cfg = CONFIG.get("SCALP_ENGINE", {})
    num_bins = int(cfg.get("VP_BINS", 100))
    va_pct = float(cfg.get("VP_VALUE_AREA_PCT", cfg.get("VP_VA_PCT", 0.70)))
    lvn_factor = float(cfg.get("VP_LVN_THRESHOLD", cfg.get("VP_LVN_FACTOR", 0.30)))

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
                vol = 1.0
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
        if vp and vp.get("profile_valid") and vp.get("poc") is not None:
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


def _calc_balance_ratio(vp: dict) -> float:
    """Ratio of value area width to total range.  High → balanced, low → trending."""
    vah = vp.get("vah", 0)
    val = vp.get("val", 0)
    session_high = vp.get("session_high")
    session_low = vp.get("session_low")
    if session_high is None or session_low is None:
        return 0.5
    total_range = float(session_high) - float(session_low)
    va_width = float(vah) - float(val) if vah and val else 0.0
    ratio = va_width / total_range if total_range > 0 else 0.5
    return round(max(0.0, min(1.0, ratio)), 3)


def _classify_market_state(vp: dict) -> str:
    """Classify market as 'balance' or 'imbalance' from VP shape."""
    br = vp.get("balance_ratio", 0.5)
    cfg = CONFIG.get("SCALP_ENGINE", {})
    threshold = float(cfg.get("BALANCE_THRESHOLD", 0.40))
    return "balance" if br >= threshold else "imbalance"


def _locate_price_vs_vp(price: float, vp: dict) -> dict:
    """Determine price location relative to VP levels.

    Returns {location: str, nearest_level: float, distance_pct: float}
    Locations: 'at_vah', 'at_val', 'at_poc', 'at_lvn', 'inside_va', 'outside_va'
    """
    cfg = CONFIG.get("SCALP_ENGINE", {})
    proximity_pct = float(cfg.get("VP_PROXIMITY_PCT", 0.15)) / 100.0

    poc = vp.get("poc", 0)
    vah = vp.get("vah", 0)
    val = vp.get("val", 0)
    lvn_levels = vp.get("lvn_levels", [])

    def _near(level):
        return abs(price - level) / level < proximity_pct if level else False

    if _near(vah):
        return {"location": "at_vah", "nearest_level": vah, "distance_pct": round(abs(price - vah) / vah * 100, 3)}
    if _near(val):
        return {"location": "at_val", "nearest_level": val, "distance_pct": round(abs(price - val) / val * 100, 3)}
    if _near(poc):
        return {"location": "at_poc", "nearest_level": poc, "distance_pct": round(abs(price - poc) / poc * 100, 3)}

    for lvn in lvn_levels:
        if _near(lvn):
            return {"location": "at_lvn", "nearest_level": lvn, "distance_pct": round(abs(price - lvn) / lvn * 100, 3)}

    if val <= price <= vah:
        return {"location": "inside_va", "nearest_level": poc, "distance_pct": round(abs(price - poc) / poc * 100, 3)}

    return {"location": "outside_va", "nearest_level": poc, "distance_pct": round(abs(price - poc) / poc * 100, 3)}


# ═══════════════════════════════════════════════════════════════════════════════
# PILLAR 2 — AGGRESSION: Absorption + CVD + AAA
# ═══════════════════════════════════════════════════════════════════════════════

def _check_absorption(candles: list) -> dict:
    """Detect absorption candles (high volume, small price move).

    Uses indicators.detect_absorption() when available, otherwise internal logic.
    """
    cfg = CONFIG.get("SCALP_ENGINE", {})
    vol_mult = float(cfg.get("ABSORPTION_VOL_MULT", cfg.get("ABS_VOL_MULT", 2.0)))
    max_move = float(cfg.get("ABSORPTION_MAX_MOVE_ATR", cfg.get("ABS_MAX_MOVE_ATR", 0.30)))
    sma_period = int(cfg.get("ABSORPTION_SMA_PERIOD", cfg.get("ABS_SMA_PERIOD", 20)))

    try:
        from indicators import detect_absorption
        rows = detect_absorption(candles, vol_mult=vol_mult, max_move_atr=max_move, sma_period=sma_period)
        hits = []
        for idx, row in enumerate(rows):
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
    for i in range(-5, 0):
        c = candles[i]
        vol = float(c.get("vol", 0) or 0)
        move = abs(c["close"] - c["open"])
        if vol >= sma_vol * vol_mult and move <= avg_atr * max_move:
            hits.append({"index": len(candles) + i, "vol": vol, "move": move})

    return {"detected": len(hits) > 0, "count": len(hits), "bars": hits}


def _check_cvd(candles: list) -> dict:
    """Compute CVD direction from recent candles.

    Uses indicators.calc_cvd() when available, otherwise internal approximation.
    Returns {direction: 'LONG'|'SHORT'|None, cvd_value, cvd_slope}
    """
    cfg = CONFIG.get("SCALP_ENGINE", {})
    smooth_period = int(cfg.get("CVD_SMOOTH_PERIOD", 5))

    try:
        from indicators import calc_cvd
        result = calc_cvd(candles, smooth_period=smooth_period)
        smoothed = (result.get("smoothed_delta") or []) if result else []
        cvd_raw = (result.get("cvd") or []) if result else []
        if smoothed and len(smoothed) >= 6:
            slope = smoothed[-1] - smoothed[-6]
            direction = "LONG" if slope > 0 else "SHORT" if slope < 0 else None
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
    direction = "LONG" if slope > 0 else "SHORT" if slope < 0 else None
    return {"direction": direction, "cvd_value": round(cvd, 2), "cvd_slope": round(slope, 2)}


def _check_aaa_sequence(candles: list, absorption: dict, cvd: dict) -> dict:
    """Detect Absorption → Accumulation → Aggression sequence.

    - Absorption: already detected (pillar)
    - Accumulation: range contraction after absorption (narrow bars)
    - Aggression: breakout candle with volume spike
    """
    cfg = CONFIG.get("SCALP_ENGINE", {})
    lookback = int(cfg.get("AAA_ACCUMULATION_LOOKBACK", 10))
    contraction_threshold = float(cfg.get("AAA_CONTRACTION_THRESHOLD", 0.5))
    breakout_vol_mult = float(cfg.get("AAA_BREAKOUT_VOL_MULT", 1.5))

    if not absorption.get("detected"):
        return {"complete": False, "phase": "no_absorption"}

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
    prev_range = prev["high"] - prev["low"] if prev["high"] > prev["low"] else 1e-10

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

def _classify_setup(
    market_state: str,
    price_loc: dict,
    absorption: dict,
    cvd: dict,
    aaa: dict,
    vwap: dict,
    htf_bias: Optional[str],
) -> dict:
    """Decide setup type and direction.

    Mean Reversion (balance market):
      - Price at VAH → SHORT toward POC
      - Price at VAL → LONG toward POC
      - Requires absorption + CVD divergence (price at high, CVD falling → reversal)

    Trend Continuation (imbalance market):
      - Price pulls back to LVN inside impulse → continue in trend direction
      - AAA sequence completion preferred
      - HTF EMA bias alignment required

    Returns {valid, setup_type, direction, target, reasons[]}
    """
    cfg = CONFIG.get("SCALP_ENGINE", {})
    location = price_loc.get("location", "")
    reasons = []

    # ── Mean Reversion ───────────────────────────────────────────────────
    if cfg.get("SETUP_MEAN_REVERSION", True) and market_state == "balance" and location in ("at_vah", "at_val"):
        direction = "SHORT" if location == "at_vah" else "LONG"

        # Check absorption at the level
        if not absorption.get("detected"):
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
            return {"valid": False, "reason": f"cvd_against_reversion:{cvd_dir}_vs_{direction}"}

        # VWAP lean as bonus (not required for mean reversion)
        if vwap.get("lean") == direction:
            reasons.append(f"VWAP confirms {direction}")

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
        }

    # ── Trend Continuation ───────────────────────────────────────────────
    if cfg.get("SETUP_TREND", True) and market_state == "imbalance" and location in ("at_lvn", "at_poc", "inside_va"):
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
        if absorption.get("detected"):
            reasons.append("Absorption detected at pullback level")

        # CVD should agree with direction
        cvd_dir = cvd.get("direction")
        if cvd_dir and cvd_dir != direction:
            return {"valid": False, "reason": f"cvd_against_trend:{cvd_dir}_vs_{direction}"}
        if cvd_dir == direction:
            reasons.append(f"CVD confirms {direction} trend")

        reasons.append(f"Trend continuation from {location}")
        return {
            "valid": True,
            "setup_type": "trend_continuation",
            "direction": direction,
            "target": "POC_OR_OPPOSITE_VA",
            "reasons": reasons,
        }

    # ── No valid setup ───────────────────────────────────────────────────
    return {"valid": False, "reason": f"no_setup:{market_state}_{location}"}


# ═══════════════════════════════════════════════════════════════════════════════
# RISK LEVELS — SL / TP1 / TP2
# ═══════════════════════════════════════════════════════════════════════════════

def calculate_scalp_levels(
    direction: str,
    entry: float,
    vp: dict,
    setup_type: str,
    symbol_info: dict,
    asset_type: str,
) -> dict:
    """Calculate SL, TP1, TP2 using VP levels.

    Mean Reversion:
      SL: beyond VAH/VAL + buffer
      TP1: POC
      TP2: opposite VA boundary

    Trend Continuation:
      SL: beyond LVN/POC + buffer
      TP1: POC or opposite VA
      TP2: outside VA extension
    """
    cfg = CONFIG.get("SCALP_ENGINE", {})
    digits = symbol_info.get("digits", 5)
    point = symbol_info.get("point", 0.00001)

    _pip = point * 10 if digits >= 4 else point
    _buffers = {
        "forex":     _pip * 3,
        "commodity": entry * 0.002 if entry > 0 else _pip * 5,
        "crypto":    entry * 0.003 if entry > 0 else 0,
        "index":     entry * 0.002 if entry > 0 else 0,
        "stock":     entry * 0.002 if entry > 0 else 0,
    }
    buffer = _buffers.get(asset_type, _buffers["forex"])

    poc = vp.get("poc", entry)
    vah = vp.get("vah", entry)
    val = vp.get("val", entry)
    if setup_type == "mean_reversion":
        if direction == "LONG":
            sl = val - buffer
            tp1 = poc
            tp2 = vah if cfg.get("TP2_ENABLED", True) else None
        else:
            sl = vah + buffer
            tp1 = poc
            tp2 = val if cfg.get("TP2_ENABLED", True) else None
    else:  # trend_continuation
        if direction == "LONG":
            sl = poc - buffer if poc < entry else entry - (entry * 0.003)
            tp1 = vah
            tp2 = (vah + (vah - poc)) if cfg.get("TP2_ENABLED", True) else None
        else:
            sl = poc + buffer if poc > entry else entry + (entry * 0.003)
            tp1 = val
            tp2 = (val - (poc - val)) if cfg.get("TP2_ENABLED", True) else None

    sl_distance = abs(entry - sl)

    # TP1 is intentionally the natural structural/profile target from setup logic.
    # Do not expand TP1 outward here to satisfy synthetic MIN_RR floors.
    actual_rr = round(abs(tp1 - entry) / sl_distance, 2) if sl_distance > 0 else 0

    # For mean_reversion setups, if the natural structural TP does not meet MIN_RR,
    # flag it so the caller can skip rather than distorting the level.
    min_rr_cfg = float(cfg.get("MIN_RR", 2.0))
    rr_below_min = (setup_type == "mean_reversion" and actual_rr < min_rr_cfg)

    # --- Defensive Rounding Safeguard ---
    # Protect against level collapse if symbol_info.digits are too coarse (e.g. 2 digits for a 0.09 crypto pair).
    # This prevents risk_engine from rejecting valid signals with INVALID_LEVELS.
    # The primary fix is in bybit_get_symbol_info, but this serves as a localized safety backup.
    if round(entry, digits) == round(sl, digits) or round(entry, digits) == round(tp1, digits):
        if asset_type == "crypto":
            # Fallback to safe precision for crypto (min 4 decimals; min 6 if price < $1)
            digits = max(digits, 6 if entry < 1.0 else 4)

    tp_partial = entry + sl_distance if direction == "LONG" else entry - sl_distance

    return {
        "entry":        round(entry, digits),
        "sl":           round(sl, digits),
        "tp_partial":   round(tp_partial, digits),
        "tp1":          round(tp1, digits),
        "tp2":          round(tp2, digits) if tp2 else None,
        "rr":           actual_rr,
        "rr_below_min": rr_below_min,
        "sl_distance":  round(sl_distance, digits),
        "sl_method":    "vp_boundary",
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

    # ── Location quality (0–25) ──────────────────────────────────────────
    loc = price_loc.get("location", "")
    if loc in ("at_vah", "at_val"):
        score += 25
        reasons.append(f"Price at {loc.upper()} — prime location")
    elif loc == "at_lvn":
        score += 20
        reasons.append("Price at LVN — trend continuation zone")
    elif loc == "at_poc":
        score += 10
        reasons.append("Price at POC — neutral location")
    elif loc == "inside_va":
        score += 5

    # ── Absorption (0–20) ────────────────────────────────────────────────
    if absorption.get("detected"):
        cnt = absorption.get("count", 0)
        if cnt >= 3:
            score += 20
            reasons.append(f"Strong absorption ({cnt} bars)")
        elif cnt >= 1:
            score += 12
            reasons.append(f"Absorption detected ({cnt} bar(s))")

    # ── CVD confirmation (0–15) ──────────────────────────────────────────
    setup_dir = setup.get("direction")
    cvd_dir = cvd.get("direction")
    if cvd_dir and cvd_dir == setup_dir:
        score += 15
        reasons.append(f"CVD confirms {setup_dir}")
    elif cvd_dir is None:
        score += 5
        reasons.append("CVD neutral")

    # ── AAA sequence (0–15) ──────────────────────────────────────────────
    if aaa.get("complete"):
        score += 15
        reasons.append("Full AAA sequence complete")
    elif aaa.get("phase") == "accumulation":
        score += 7
        reasons.append("AAA: absorption + accumulation (no aggression yet)")

    # ── VWAP alignment (0–5) ─────────────────────────────────────────────
    if vwap.get("lean") == setup_dir:
        score += 5
        reasons.append(f"VWAP lean confirms {setup_dir}")

    # ── Session (0–10) ───────────────────────────────────────────────────
    if "london" in sessions and "new_york" in sessions:
        score += 10
        reasons.append("London/NY overlap — peak liquidity")
    elif "london" in sessions or "new_york" in sessions:
        score += 7
        reasons.append("Major session active")

    # ── HTF bias alignment (0–5) ─────────────────────────────────────────
    if htf_bias and htf_bias == setup_dir:
        score += 5
        reasons.append(f"HTF EMA bias aligned ({htf_bias})")

    # ── Spread penalty (−5 to +5) ────────────────────────────────────────
    max_sp = cfg.get("MAX_SPREAD_PIPS", {}).get("forex", 4)
    if spread_pips > 0:
        if spread_pips <= max_sp * 0.5:
            score += 5
            reasons.append(f"Tight spread ({spread_pips:.1f} pips)")
        elif spread_pips > max_sp * 0.8:
            score -= 5
            reasons.append(f"Wide spread ({spread_pips:.1f} pips)")

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

    return {
        "score": score,
        "grade": grade,
        "reasons": reasons,
        "size_multiplier": size_mult,
    }


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
    min_grade = str(cfg.get("MIN_GRADE_AUTO_EXECUTE", cfg.get("MIN_GRADE", "C"))).upper()

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
    _reset_session_state_if_new_day()
    max_daily = int(cfg.get("MAX_DAILY_LOSSES", 3))
    if cfg.get("MAX_DAILY_LOSSES") and _session_state["total_losses_today"] >= max_daily:
        log.warning(f"[SCALP] Daily loss limit reached: {_session_state['total_losses_today']} losses")
        return {
            "signals": [], "skipped": len(pairs_or_symbols), "scanned": 0,
            "session": "DAILY_LOSS_LIMIT", "reason": f"MAX_DAILY_LOSSES ({max_daily}) reached",
        }

    sessions = get_current_sessions()
    mt5_session_ok, session_name = scalp_session_window("forex")
    crypto_session_ok, _ = scalp_session_window("crypto")

    if not mt5_session_ok and not crypto_session_ok:
        for display in pairs_or_symbols:
            _record_stability_sample(display, _guess_asset_type(display), False, reason="OUTSIDE_SESSION")
        return {
            "signals": [],
            "skipped": [{"pair": display, "reason": "OUTSIDE_SESSION"} for display in pairs_or_symbols],
            "scanned": 0,
            "session": session_name,
            "reason": "OUTSIDE_SESSION",
        }

    signals = []
    skipped = []

    mt5_pairs = [p for p in pairs_or_symbols if _guess_asset_type(p) != "crypto"]
    if not mt5_connect() and mt5_pairs:
        for display in mt5_pairs:
            _record_stability_sample(display, _guess_asset_type(display), False, reason="MT5_NOT_CONNECTED")
            skipped.append({"pair": display, "reason": "MT5_NOT_CONNECTED"})
        mt5_pairs = []

    for display in pairs_or_symbols:
        mt5_sym = None
        try:
            asset_type = _guess_asset_type(display)
            session_ok, active_session = scalp_session_window(asset_type)
            if not session_ok:
                reason = active_session if active_session == "NY_OPEN_COOLDOWN" else "OUTSIDE_SESSION"
                _record_stability_sample(display, asset_type, False, reason=reason)
                skipped.append({"pair": display, "reason": reason})
                continue

            # ── Fetch candles (crypto vs MT5) ────────────────────────────────
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

                candles_m5 = _scalp_fetch_candles(pair_dict, "M5", m5_count)
                if not candles_m5 or len(candles_m5) < 10:
                    _record_stability_sample(display, asset_type, False, reason="insufficient_m5_candles")
                    skipped.append({"pair": display, "reason": "insufficient_m5_candles"})
                    continue

                if execution_tf == "M1":
                    candles_exec = _scalp_fetch_candles(pair_dict, "M1", m1_count)
                    if not candles_exec or len(candles_exec) < 30:
                        _record_stability_sample(display, asset_type, False, reason="insufficient_m1_candles")
                        skipped.append({"pair": display, "reason": "insufficient_m1_candles"})
                        continue
                else:
                    candles_exec = candles_m5

                current_price = candles_exec[-1]["close"]
                sym_info = {"spread": 0, "point": 0.01, "digits": 2}
                spread_pips = 0.0
                htf_bias = None

                if use_bias:
                    candles_bias = _scalp_fetch_candles(pair_dict, bias_tf, h1_count)
                    if candles_bias and len(candles_bias) >= 200:
                        htf_bias = infer_bias_from_ema_stack(candles_bias)

            else:
                mt5_sym = mt5_map_symbol(display)
                if not mt5_sym:
                    _record_stability_sample(display, asset_type, False, reason="no_mt5_mapping")
                    skipped.append({"pair": display, "reason": "no_mt5_mapping"})
                    continue

                sym_info = mt5_get_symbol_info(display)
                if not sym_info or sym_info.get("error"):
                    _record_stability_sample(display, asset_type, False, reason="symbol_not_available")
                    skipped.append({"pair": display, "reason": "symbol_not_available"})
                    continue

                spread_ok, spread_pips = check_spread(sym_info, asset_type)
                if not spread_ok:
                    _record_stability_sample(display, asset_type, False,
                                             feature_map={"spread_pips": spread_pips},
                                             reason=f"spread_too_wide_{spread_pips}pips")
                    skipped.append({"pair": display, "reason": f"spread_too_wide_{spread_pips}pips"})
                    continue

                candles_m15 = mt5_fetch_scalp_candles(mt5_sym, "M15", m15_count, include_forming=True)
                if len(candles_m15) < 30:
                    _record_stability_sample(display, asset_type, False, reason="insufficient_m15_candles")
                    skipped.append({"pair": display, "reason": "insufficient_m15_candles"})
                    continue

                candles_m5 = mt5_fetch_scalp_candles(mt5_sym, "M5", m5_count, include_forming=True)
                if len(candles_m5) < 10:
                    _record_stability_sample(display, asset_type, False, reason="insufficient_m5_candles")
                    skipped.append({"pair": display, "reason": "insufficient_m5_candles"})
                    continue

                if execution_tf == "M1":
                    candles_exec = mt5_fetch_scalp_candles(mt5_sym, "M1", m1_count, include_forming=True)
                    if len(candles_exec) < 30:
                        _record_stability_sample(display, asset_type, False, reason="insufficient_m1_candles")
                        skipped.append({"pair": display, "reason": "insufficient_m1_candles"})
                        continue
                else:
                    candles_exec = candles_m5

                live_price = mt5_get_live_price(mt5_sym)
                current_price = live_price if live_price and live_price > 0 else candles_exec[-1]["close"]
                htf_bias = None

                if use_bias:
                    candles_bias = mt5_fetch_scalp_candles(mt5_sym, bias_tf, h1_count, include_forming=True)
                    if len(candles_bias) < 200:
                        bias_require = bool(cfg.get("BIAS_REQUIRE_CONFIRMATION", True))
                        if bias_require:
                            _record_stability_sample(display, asset_type, False,
                                                     reason="htf_bias_unavailable: insufficient H1 bars for EMA stack")
                            skipped.append({"pair": display, "reason": "htf_bias_unavailable: insufficient H1 bars for EMA stack"})
                            continue
                        # else: allow trade without bias confirmation
                    else:
                        htf_bias = infer_bias_from_ema_stack(candles_bias)

            # ══════════════════════════════════════════════════════════════
            # FABIO VALENTINI PIPELINE
            # ══════════════════════════════════════════════════════════════

            # Pillar 1: Volume Profile — market state + location
            if not cfg.get("VP_ENABLED", True):
                _record_stability_sample(display, asset_type, False, reason="vp_disabled")
                skipped.append({"pair": display, "reason": "vp_disabled"})
                continue
            vp = _build_volume_profile(candles_m15)
            if not vp.get("valid"):
                _record_stability_sample(display, asset_type, False, reason=f"vp_invalid:{vp.get('reason', '?')}")
                skipped.append({"pair": display, "reason": f"vp_invalid:{vp.get('reason', '?')}"})
                continue

            market_state = _classify_market_state(vp)
            price_loc = _locate_price_vs_vp(current_price, vp)

            # Pillar 2: Aggression — absorption, CVD, AAA
            absorption = _check_absorption(candles_exec)
            cvd = _check_cvd(candles_exec)
            aaa = _check_aaa_sequence(candles_exec, absorption, cvd) if cfg.get("AAA_ENABLED", True) else {"complete": False, "phase": "disabled"}

            # Pillar 3: VWAP directional lean
            vwap = _check_vwap_lean(candles_m15, current_price) if cfg.get("VWAP_ENABLED", True) else {"lean": None, "vwap_value": 0}

            # Setup classification
            setup = _classify_setup(market_state, price_loc, absorption, cvd, aaa, vwap, htf_bias)
            if not setup.get("valid"):
                _record_stability_sample(display, asset_type, False,
                                         reason=f"no_setup:{setup.get('reason', '?')}")
                skipped.append({"pair": display, "reason": f"no_setup:{setup.get('reason', '?')}"})
                continue

            direction = setup["direction"]

            # HTF bias filter
            if use_bias and htf_bias and direction != htf_bias:
                _record_stability_sample(display, asset_type, False,
                                         feature_map={"bias_aligned": False},
                                         reason=f"counter_trend:{direction}_vs_{bias_tf}_{htf_bias}")
                skipped.append({"pair": display, "reason": f"counter_trend:{direction}_vs_{bias_tf}_{htf_bias}"})
                continue

            # Risk levels
            levels = calculate_scalp_levels(direction, current_price, vp, setup["setup_type"], sym_info, asset_type)
            if levels.get("rr_below_min"):
                log.warning(f"[SCALP] {display}: mean_reversion RR {levels['rr']:.2f} < MIN_RR — skipping (natural TP too close)")
                _record_stability_sample(display, asset_type, False, reason="rr_below_min")
                skipped.append({"pair": display, "reason": "rr_below_min"})
                continue
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
            proxy_cfg = CONFIG.get("SCALP_ENGINE", {})
            premarket_delta_proxy_levels = _build_premarket_delta_proxy_levels(
                candles_exec,
                top_n=int(proxy_cfg.get("PREMARKET_DELTA_PROXY_TOP_LEVELS", 3)),
                min_candles=int(proxy_cfg.get("PREMARKET_DELTA_PROXY_MIN_CANDLES", 10)),
                bucket_size=float(sym_info.get("point") or 0.0) * 10.0 if sym_info else None,
            )

            # Quality grade
            if cfg.get("AI_GRADING", True):
                quality = ai_quality_grade(vp, price_loc, absorption, cvd, aaa, vwap, setup, sessions, spread_pips, htf_bias)
            else:
                quality = {"score": 50, "grade": "C", "reasons": ["grading_disabled"], "size_multiplier": 1.0}

            # Grade gate
            grade = quality["grade"]
            if min_grade == "C" and grade == "D":
                _record_stability_sample(display, asset_type, False, reason=f"grade_D_skip")
                skipped.append({"pair": display, "reason": "grade_D_skip"})
                continue
            if min_grade == "B" and grade in ("C", "D"):
                _record_stability_sample(display, asset_type, False, reason=f"grade_{grade}_below_min")
                skipped.append({"pair": display, "reason": f"grade_{grade}_below_min"})
                continue

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
                "tp1":             levels["tp1"],
                "tp2":             levels["tp2"],
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
                "size_multiplier": quality["size_multiplier"],
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
                "absorption_count": absorption.get("count", 0),
                "cvd_direction":   cvd.get("direction"),
                "cvd_slope":       cvd.get("cvd_slope"),
                "aaa_complete":    aaa.get("complete", False),
                "htf_bias":        htf_bias,
                "htf_bias_tf":     bias_tf if use_bias else None,
                "advisory":        advisory,
                "advisory_summary": advisory.get("summary"),
                "premarket_delta_cluster_type": "proxy",
                "premarket_delta_proxy_levels": premarket_delta_proxy_levels,
                "timestamp":       datetime.now(timezone.utc).isoformat(),
                "engine":          "SCALP",
                # Fields required by risk_engine.risk_check()
                "confluenceScore": quality["score"] / 100.0,
                "maxScore":        1.0,
            }
            
            # Apply consecutive-loss halving and +2R size cut
            if cfg.get("CONSECUTIVE_LOSS_HALVE", True) and _session_state["consecutive_losses"] >= 2:
                signal["size_multiplier"] = signal.get("size_multiplier", 1.0) * 0.5
                signal["ai_reasons"] = signal.get("ai_reasons", []) + ["size_halved:consecutive_losses"]
            if _session_state.get("size_cut_active"):
                signal["size_multiplier"] = min(signal.get("size_multiplier", 1.0), 0.5)
                signal["ai_reasons"] = signal.get("ai_reasons", []) + ["size_cut:+2R_reached"]
            
            signals.append(signal)
            _record_stability_sample(
                display, asset_type, True,
                score_norm=quality["score"] / 100.0,
                feature_map={
                    "market_state": 1.0 if market_state == "balance" else 0.0,
                    "location": price_loc.get("location", "unknown"),
                    "absorption": absorption.get("detected", False),
                    "cvd_aligned": cvd.get("direction") == direction,
                    "aaa_complete": aaa.get("complete", False),
                    "vwap_aligned": vwap.get("lean") == direction,
                    "spread_pips": spread_pips,
                    "bias_aligned": htf_bias == direction if htf_bias else True,
                },
            )

        except Exception as e:
            log.error(f"[SCALP] Error on {display}: {e}")
            _record_stability_sample(display, _guess_asset_type(display), False, reason=f"error:{str(e)[:60]}")
            skipped.append({"pair": display, "reason": f"error:{str(e)[:60]}"})

    signals.sort(key=lambda s: s.get("ai_score", 0), reverse=True)

    log.warning(
        f"[SCALP] Scan: {len(pairs_or_symbols)} pairs | "
        f"{len(signals)} signals | {len(skipped)} skipped | session={session_name}"
    )

    if skipped:
        from collections import Counter
        reason_counts = Counter(s.get("reason", "unknown") for s in skipped)
        for reason, count in reason_counts.most_common():
            log.warning(f"[SCALP] Skip reason: {reason} × {count}")
        # Per-pair detail at debug level so it's available when needed
        for s in skipped:
            log.debug(f"[SCALP] Skipped {s.get('pair')} — {s.get('reason')}")

    return {
        "signals": signals,
        "skipped": skipped,
        "scanned": len(pairs_or_symbols),
        "session": session_name,
        "sessions_active": sessions,
    }


# ═══════════════════════════════════════════════════════════════════════════════
# ASSET TYPE HELPER
# ═══════════════════════════════════════════════════════════════════════════════

def _guess_asset_type(display: str) -> str:
    """Infer asset type from display name for buffers and spread limits."""
    forex_currencies = {"EUR", "GBP", "USD", "JPY", "AUD", "NZD", "CAD", "CHF"}
    parts = display.replace("/", " ").split()
    if len(parts) == 2 and all(p in forex_currencies for p in parts):
        return "forex"
    if "XAU" in display or "XAG" in display or "Oil" in display or "Nat Gas" in display or "Copper" in display or "XPT" in display or "XPD" in display:
        return "commodity"
    if "USDT" in display or "BTC" in display or "ETH" in display:
        return "crypto"
    if any(x in display for x in ["S&P", "Nasdaq", "Dow", "DAX", "UK100", "ASX", "Nikkei", "Hang", "USTEC", "NAS100"]):
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
        for p in active_pair_dicts:
            if not isinstance(p, dict) or not p.get("enabled", True):
                continue
            src = str(p.get("source") or "").lower()
            typ = str(p.get("type") or "").lower()
            disp = (p.get("display") or p.get("symbol") or "").strip()
            if not disp:
                continue
            if src == "mt5" and typ in ("forex", "commodity", "index", "stock"):
                out.append(disp)
            elif src == "binance" and typ == "crypto":
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
