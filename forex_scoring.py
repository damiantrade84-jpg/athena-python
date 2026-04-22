"""
forex_scoring.py — Legacy forex scoring engine kept for audit and regression work.

Live Engine A no longer routes forex here. The active live path is:
athena.py -> scoring.calc_confluence() -> factor_scoring.compute_factor_scores().

This module remains on disk so older diagnostics, parity probes, and historical
research branches can still be inspected without guessing what the retired
rules-based forex engine used to do.
"""

from __future__ import annotations
import logging
import math
from dataclasses import dataclass, field
from datetime import datetime, time, timedelta, timezone
from typing import Optional
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

log = logging.getLogger("athena")


@dataclass
class ForexScoreResult:
    final_score: float = 0.0
    direction: str = "LONG"
    signal_type: str = "NONE"
    trend_gate: bool = False
    session_active: bool = False
    entry_quality: float = 0.0
    momentum_confirm: float = 0.0
    adx_filter: float = 0.0
    cot_boost: float = 0.0
    carry_tilt: float = 0.0
    breakout_score: float = 0.0
    components: dict = field(default_factory=dict)


# ─── Session windows (UTC hours, inclusive) ─────────────────────────────────

_ASIAN_OPEN = (0, 8)  # 00:00–08:00 UTC  (Tokyo/Sydney)
_LONDON_OPEN = (7, 17)  # 07:00–17:00 UTC  (full London session, BST)
_NY_OPEN = (12, 21)  # 12:00–21:00 UTC  (full NY session: 08:00–17:00 ET)


_ASIAN_SESSION_PAIRS = {
    "USD/JPY",
    "EUR/JPY",
    "GBP/JPY",
    "AUD/JPY",
    "NZD/JPY",
    "AUD/USD",
    "NZD/USD",
    "AUD/NZD",
}


def _session_state(utc_hour: int, pair_display: str = "") -> dict:
    """Evaluate session state using soft/strict modes."""
    try:
        from config import CONFIG
        
        if not CONFIG.get("FOREX_SESSION_FILTER", True):
            return {"is_active": True, "multiplier": 1.0}
            
        fx_cfg = CONFIG.get("FOREX_ENGINE", {}) or {}
        mode = fx_cfg.get("session_mode", "strict")
        if mode == "off":
            return {"is_active": True, "multiplier": 1.0}
        
        soft_mult = float(fx_cfg.get("session_soft_multiplier", 0.75))
        shoulder_enabled = bool(fx_cfg.get("session_shoulder_enabled", True))
        shoulder_mult = float(fx_cfg.get("session_shoulder_multiplier", 0.90))
        shoulder_hours = int(fx_cfg.get("session_shoulder_hours", 1))
    except Exception:
        mode = "strict"
        soft_mult = 0.75
        shoulder_enabled = True
        shoulder_mult = 0.90
        shoulder_hours = 1

    in_core = False
    in_shoulder = False

    # Check London + NY
    if _LONDON_OPEN[0] <= utc_hour <= _LONDON_OPEN[1] or _NY_OPEN[0] <= utc_hour <= _NY_OPEN[1]:
        in_core = True
    elif shoulder_enabled:
        if _LONDON_OPEN[0] - shoulder_hours <= utc_hour < _LONDON_OPEN[0] or \
           _LONDON_OPEN[1] < utc_hour <= _LONDON_OPEN[1] + shoulder_hours or \
           _NY_OPEN[0] - shoulder_hours <= utc_hour < _NY_OPEN[0] or \
           _NY_OPEN[1] < utc_hour <= _NY_OPEN[1] + shoulder_hours:
            in_shoulder = True

    # Asian check
    if not in_core and not in_shoulder and pair_display in _ASIAN_SESSION_PAIRS:
        if _ASIAN_OPEN[0] <= utc_hour <= _ASIAN_OPEN[1]:
            in_core = True
        elif shoulder_enabled and (_ASIAN_OPEN[0] - shoulder_hours <= utc_hour < _ASIAN_OPEN[0] or \
                                   _ASIAN_OPEN[1] < utc_hour <= _ASIAN_OPEN[1] + shoulder_hours):
            in_shoulder = True

    if in_core:
        return {"is_active": True, "multiplier": 1.0}
    if in_shoulder:
        return {"is_active": True, "multiplier": shoulder_mult}
        
    if mode == "soft":
        return {"is_active": True, "multiplier": soft_mult}
        
    return {"is_active": False, "multiplier": 0.0}


def _hurst_exponent(prices: list, max_lag: int = 20) -> float:
    """
    Hurst Exponent via variance of lagged differences.
    H < 0.45 = mean-reverting  H > 0.55 = trending  H = 0.5 = random walk
    """
    if len(prices) < max_lag * 2:
        return 0.5
    try:
        arr = np.array(prices, dtype=float)
        lags = range(2, max_lag)
        tau = [np.std(np.subtract(arr[lag:], arr[:-lag])) for lag in lags]
        tau = [t for t in tau if t > 0]
        if len(tau) < 2:
            return 0.5
        valid_lags = list(lags)[: len(tau)]
        poly = np.polyfit(np.log(valid_lags), np.log(tau), 1)
        return float(np.clip(poly[0], 0.0, 1.0))
    except Exception:
        return 0.5


def _mad_zscore(value: float, history: list, window: int = 20) -> float:
    """
    Modified Z-score using Median Absolute Deviation.
    Resistant to fat tails and volatility compression — correct for forex.
    Formula: M = 0.6745 * (x - median) / MAD
    """
    if len(history) < window:
        return 0.0
    try:
        series = pd.Series(history[-window:], dtype=float)
        med = series.median()
        mad = float(np.median(np.abs(series - med)))
        if mad < 1e-8:
            return 0.0
        return float(np.clip(0.6745 * (value - med) / mad, -3.0, 3.0))
    except Exception:
        return 0.0


class DynamicForexWeights:
    """
    Shifts scoring weights based on Hurst Exponent regime.
    Mean-reverting (H<0.45): RSI pullback quality dominates.
    Trending (H>0.55): EMA trend alignment and COT dominate.
    Neutral (0.45-0.55): balanced weights.
    """

    def __init__(self):
        self.rsi_w = 0.40
        self.cot_w = 0.20
        self.base = 0.40
        self.regime = "NEUTRAL"

    def update(self, hurst: float, backtest_mode: bool = False) -> None:
        if hurst < 0.45:
            self.base = 0.20
            self.rsi_w = 0.60
            self.cot_w = 0.20
            self.regime = "MEAN_REVERTING"
        elif hurst > 0.55:
            self.base = 0.55
            self.rsi_w = 0.20
            self.cot_w = 0.25
            self.regime = "TRENDING"
        else:
            self.base = 0.40
            self.rsi_w = 0.40
            self.cot_w = 0.20
            self.regime = "NEUTRAL"

    def score(self, eq: float, cot: float) -> float:
        return self.base + eq * self.rsi_w + cot * self.cot_w


# ─── Trend gate ──────────────────────────────────────────────────────────────


def _trend_gate_detail(d1_snap: dict, h4_snap: dict) -> tuple[bool, str, dict]:
    """Return trend-gate pass/fail plus reason codes for scan diagnostics.

    ADX for the gate is read from D1 or H4 per ``FOREX_ENGINE.trend_gate_adx_source``.
    Daily ADX reacts slowly; using H4 aligns the gate with the H4 momentum screen and
    avoids mass ``adx_below_min`` skips when D1 is still compressing.
    """
    _adx_src = "d1"
    try:
        from config import CONFIG

        fx_cfg = CONFIG.get("FOREX_ENGINE", {}) or {}
        _adx_src = str(fx_cfg.get("trend_gate_adx_source", "d1")).lower().strip()
        if _adx_src not in ("d1", "h4"):
            _adx_src = "d1"
        trend_gate_adx_min = float(fx_cfg.get("trend_gate_adx_min", 20.0))
        adx_hard_fail = float(fx_cfg.get("trend_gate_adx_hard_fail_min", trend_gate_adx_min))
        adx_soft_full = float(fx_cfg.get("trend_gate_adx_soft_full_at", trend_gate_adx_min))
        adx_soft_enabled = bool(fx_cfg.get("trend_gate_adx_soft_enabled", False))
        trend_margin_min = float(fx_cfg.get("trend_margin_min", 0.003))
    except Exception:
        trend_gate_adx_min = 20.0
        adx_hard_fail = 20.0
        adx_soft_full = 20.0
        adx_soft_enabled = False
        trend_margin_min = 0.003

    _adx_snap = h4_snap if _adx_src == "h4" else d1_snap
    adx = _adx_snap.get("adx")
    if adx is None:
        adx = 0.0
    try:
        adx = float(adx)
    except (ValueError, TypeError):
        adx = 0.0
        
    adx_gate_multiplier = 1.0

    detail = {
        "reason": "unknown",
        "direction_hint": "LONG",
        "adx": adx,
        "adx_timeframe": _adx_src.upper(),
        "adx_min": trend_gate_adx_min,
        "adx_gate_multiplier": 1.0,
        "margin": None,
        "margin_min": trend_margin_min,
        "d1_bull": None,
        "h4_bull": None,
        "d1_slope": 0.0,
    }
    
    if adx_soft_enabled:
        if adx < adx_hard_fail:
            detail["reason"] = "adx_below_min"
            return False, "LONG", detail
        elif adx < adx_soft_full:
            span = adx_soft_full - adx_hard_fail
            if span > 0:
                adx_gate_multiplier = 0.40 + 0.60 * ((adx - adx_hard_fail) / span)
    else:
        if adx < trend_gate_adx_min:
            detail["reason"] = "adx_below_min"
            return False, "LONG", detail
            
    detail["adx_gate_multiplier"] = adx_gate_multiplier

    d1_close = d1_snap.get("close")
    d1_ema200 = d1_snap.get("ema200")
    h4_ema50 = h4_snap.get("ema50")
    h4_ema200 = h4_snap.get("ema200")
    d1_slope = d1_snap.get("ema200Slope10", 0) or 0
    try:
        d1_slope = float(d1_slope)
    except (TypeError, ValueError):
        d1_slope = 0.0
    detail["d1_slope"] = d1_slope

    if None in (d1_close, d1_ema200, h4_ema50, h4_ema200):
        detail["reason"] = "missing_ema_inputs"
        return False, "LONG", detail

    d1_bull = d1_close > d1_ema200
    h4_bull = h4_ema50 > h4_ema200
    detail["d1_bull"] = d1_bull
    detail["h4_bull"] = h4_bull

    if d1_bull and h4_bull:
        detail["direction_hint"] = "LONG"
        margin = (d1_close - d1_ema200) / d1_ema200 if d1_ema200 != 0 else 0.0
        detail["margin"] = margin
        if margin > trend_margin_min or d1_slope > 0:
            detail["reason"] = "passed_long"
            return True, "LONG", detail
        detail["reason"] = "long_margin_or_slope_failed"
        return False, "LONG", detail

    if not d1_bull and not h4_bull:
        detail["direction_hint"] = "SHORT"
        margin = (d1_ema200 - d1_close) / d1_ema200 if d1_ema200 != 0 else 0.0
        detail["margin"] = margin
        if margin > trend_margin_min or d1_slope < 0:
            detail["reason"] = "passed_short"
            return True, "SHORT", detail
        detail["reason"] = "short_margin_or_slope_failed"
        return False, "SHORT", detail

    detail["reason"] = "mixed_ema_alignment"
    return False, "LONG", detail


def _check_trend_gate(d1_snap: dict, h4_snap: dict) -> tuple[bool, str]:
    """
    D1 and H4 EMA alignment check with ADX trend filter.
    ADX filter — market must be trending, not ranging
    ADX < 20 = choppy/ranging = EMA signals are noise in forex
    LONG: D1 close > D1 ema200 AND H4 ema50 > H4 ema200
    SHORT: D1 close < D1 ema200 AND H4 ema50 < H4 ema200
    Mixed = no trade.
    Also checks ema200Slope10 for trend health — flat ema200 reduces conviction.
    """
    trend_ok, trend_dir, _detail = _trend_gate_detail(d1_snap, h4_snap)
    return trend_ok, trend_dir


def _unique_reason_codes(values: list[str]) -> list[str]:
    out = []
    seen = set()
    for value in values:
        key = str(value or "").strip()
        if not key or key in seen:
            continue
        seen.add(key)
        out.append(key)
    return out


# ─── Entry quality (RSI pullback + H1 EMA21 anchor) ─────────────────────────


def _apply_h1_ema_entry_quality_modifier(
    score: float, h1_snap: dict, direction: str
) -> float:
    """Elder-style H1 screen: penalise if price has not reclaimed ema21; boost if stacked."""
    try:
        from config import CONFIG

        fx_cfg = CONFIG.get("FOREX_ENGINE", {}) or {}
        if not fx_cfg.get("h1_ema_entry_filter", True):
            return score
    except Exception:
        pass

    ema21 = h1_snap.get("ema21")
    ema50 = h1_snap.get("ema50")
    close = h1_snap.get("close")
    if ema21 is None or ema50 is None or close is None:
        return score
    try:
        ema21_f = float(ema21)
        ema50_f = float(ema50)
        close_f = float(close)
    except (TypeError, ValueError):
        return score
    if ema50_f == 0:
        return score

    if direction == "LONG":
        h1_ema_aligned = close_f > ema21_f > ema50_f
        h1_ema_opposing = close_f < ema21_f
    else:
        h1_ema_aligned = close_f < ema21_f < ema50_f
        h1_ema_opposing = close_f > ema21_f

    if h1_ema_aligned:
        return min(1.0, score * 1.15)
    if h1_ema_opposing:
        return score * 0.50
    return score


def _entry_quality(h1_snap: dict, direction: str, rsi_history: list = None) -> float:
    rsi = h1_snap.get("rsi") or h1_snap.get("rsi14")
    if rsi is None:
        return 0.3

    if rsi_history and len(rsi_history) >= 10:
        rsi_z = _mad_zscore(rsi, rsi_history)
        if direction == "LONG":
            if rsi_z <= -0.5:
                score = 1.0
            elif rsi_z <= 0.0:
                score = 0.6
            elif rsi_z <= 0.5:
                score = 0.3
            else:
                score = 0.0
        else:
            if rsi_z >= 0.5:
                score = 1.0
            elif rsi_z >= 0.0:
                score = 0.6
            elif rsi_z >= -0.5:
                score = 0.3
            else:
                score = 0.0
        return _apply_h1_ema_entry_quality_modifier(score, h1_snap, direction)

    if direction == "LONG":
        if 30 <= rsi <= 45:
            score = 1.0  # genuine pullback zone
        elif 45 < rsi <= 52:
            score = 0.5  # mild pullback — partial credit
        elif rsi < 30:
            score = 0.3  # deep oversold — possible reversal, not trend entry
        else:
            score = 0.0  # RSI > 52 is not a pullback
    else:
        if 55 <= rsi <= 70:
            score = 1.0  # genuine pullback zone (for shorts)
        elif 48 <= rsi < 55:
            score = 0.5  # mild pullback
        elif rsi > 70:
            score = 0.3  # deep overbought — possible reversal
        else:
            score = 0.0  # RSI < 48 is not a short pullback
    return _apply_h1_ema_entry_quality_modifier(score, h1_snap, direction)


# ─── Momentum confirmation ───────────────────────────────────────────────────────


def _momentum_confirm(h4_snap: dict, direction: str) -> float:
    """
    H4 MACD histogram direction as momentum confirmation.
    LONG: MACD histogram > 0 and rising = 1.0, > 0 but falling = 0.5
    SHORT: MACD histogram < 0 and falling = 1.0, < 0 but rising = 0.5
    Returns 0.0-1.0
    """
    hist = h4_snap.get("macdHist")
    hist_prev = h4_snap.get("macdHistPrev")
    if hist is None:
        return 0.3  # neutral if no data

    if direction == "LONG":
        if hist > 0:
            if hist_prev is not None and hist > hist_prev:
                return 1.0  # positive and rising
            return 0.5  # positive but falling
        return 0.0  # negative histogram
    else:  # SHORT
        if hist < 0:
            if hist_prev is not None and hist < hist_prev:
                return 1.0  # negative and falling
            return 0.5  # negative but rising
        return 0.0  # positive histogram


# ─── ADX filter ───────────────────────────────────────────────────────────────


def _adx_filter(h4_snap: dict) -> float:
    """
    ADX-based trend strength filter.
    Graduated scoring: 0/0.4/0.7/1.0 based on trend strength.
    """
    adx = h4_snap.get("adx")
    if adx is None:
        return 0.3  # neutral if no data
    try:
        from config import CONFIG

        fx_cfg = CONFIG.get("FOREX_ENGINE", {}) or {}
        adx_confirm_min = float(fx_cfg.get("adx_confirm_min", 22.0))
    except Exception:
        adx_confirm_min = 22.0
    if adx < adx_confirm_min:
        return 0.0
    elif adx < 25:
        return 0.4  # weak trend — low conviction
    elif adx < 30:
        return 0.7  # moderate trend
    else:
        return 1.0  # strong trend


# ─── Carry tilt ───────────────────────────────────────────────────────────────


def _carry_tilt(pair: dict, direction: str, bar_time: Optional[str] = None) -> float:
    """
    Carry direction as a mild tilt (booster, never blocker).
    Returns 0.0-1.0. Carry aligned with direction = boost.
    Carry opposing = 0.0 (neutral, not negative).
    """
    try:
        from carry_feed import get_carry_z

        _as_of = bar_time[:10] if bar_time else None
        carry_z = get_carry_z(pair.get("display", ""), as_of_date=_as_of)
        if carry_z is None or carry_z == 0.0:
            return 0.0
        # Carry aligned with direction = boost
        if (direction == "LONG" and carry_z > 0) or (
            direction == "SHORT" and carry_z < 0
        ):
            return min(1.0, abs(carry_z) / 2.0)
        return 0.0  # opposing carry = no boost, not penalty
    except Exception:
        return 0.0


# ─── COT boost ───────────────────────────────────────────────────────────────


def _cot_boost(pair: dict, direction: str, bar_time: Optional[str] = None) -> float:
    """
    COT commercial positioning as signal booster.
    Returns 0.0-1.0. Never blocks a signal — only amplifies.
    Returns 0.0 if COT data unavailable (degrades gracefully).
    """
    try:
        from cot_feed import get_cot_z

        cot_z = get_cot_z(pair.get("display", ""), as_of_date=bar_time)
        if cot_z is None:
            return 0.0
        if direction == "LONG" and cot_z > 0.3:
            return min(1.0, cot_z / 2.5)
        elif direction == "SHORT" and cot_z < -0.3:
            return min(1.0, abs(cot_z) / 2.5)
        return 0.0
    except Exception:
        return 0.0


# ─── London breakout ─────────────────────────────────────────────────────────


def _reference_utc_from_h1(h1_candles: list) -> datetime:
    if not h1_candles:
        return datetime.now(timezone.utc)
    try:
        last = h1_candles[-1].get("time", "")
        if not last:
            return datetime.now(timezone.utc)
        dt = datetime.fromisoformat(str(last).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except Exception:
        return datetime.now(timezone.utc)


def _london_breakout_utc_bounds(h1_candles: list, fx_cfg: dict) -> tuple[int, int]:
    """UTC hour bounds (inclusive) for London breakout evaluation.

    - ``london_breakout_window_mode: london_local`` (default): first hours after
      08:00 Europe/London, converted to UTC (DST-safe).
    - ``explicit_utc``: use ``london_breakout_window_lo`` / ``_hi`` as fixed UTC hours.
    """
    mode = str(fx_cfg.get("london_breakout_window_mode", "london_local")).strip().lower()
    if mode in ("explicit_utc", "utc", "fixed_utc"):
        lo = int(fx_cfg.get("london_breakout_window_lo", _LONDON_OPEN[0]))
        hi = int(fx_cfg.get("london_breakout_window_hi", _LONDON_OPEN[0] + 2))
        return lo, hi

    ref_utc = _reference_utc_from_h1(h1_candles)
    z = ZoneInfo("Europe/London")
    local = ref_utc.astimezone(z)
    day = local.date()
    start_h = int(fx_cfg.get("london_breakout_local_start_h", 8))
    end_h_exc = int(fx_cfg.get("london_breakout_local_end_h", 10))
    t0 = datetime.combine(day, time(start_h, 0, tzinfo=z))
    t1 = datetime.combine(day, time(end_h_exc, 0, tzinfo=z))
    if t1 <= t0:
        t1 = t0 + timedelta(hours=2)
    utc_hours: list[int] = []
    cur = t0
    while cur < t1:
        utc_hours.append(cur.astimezone(timezone.utc).hour)
        cur += timedelta(hours=1)
    if not utc_hours:
        return 8, 9
    return min(utc_hours), max(utc_hours)


def _breakout_eval_hour_from_candles(h1_candles: list) -> int:
    """Derive the UTC hour for London breakout evaluation from the last H1 candle.

    This is the shared candle-state resolver: live, backtest (all loops), and
    Engine-C scan all call this instead of relying on bar_time (which is forced
    to 13:00 UTC for D1 sessions and would always suppress the breakout check).
    """
    if not h1_candles:
        return _local_to_utc_hour()
    try:
        last_candle_time = h1_candles[-1].get("time", "")
        if not last_candle_time:
            return _local_to_utc_hour()
        dt = datetime.fromisoformat(str(last_candle_time).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.utctimetuple().tm_hour
    except Exception:
        return _local_to_utc_hour()


def _london_breakout_score(h1_candles: list, utc_hour: int) -> tuple[float, str | None]:
    """
    London breakout signal.
    Measures Asian session range (00:00-07:00 UTC) from recent H1 candles.
    At London open, checks if current bar breaks above/below that range.
    Returns (score 0-1, direction LONG/SHORT or None when no breakout).

    NOTE: utc_hour should be derived from _breakout_eval_hour_from_candles(h1_candles)
    NOT from bar_time (which is forced to 13:00 UTC in D1 swing BT).
    """
    if not h1_candles or len(h1_candles) < 10:
        return 0.0, None

    try:
        from config import CONFIG

        _fx = CONFIG.get("FOREX_ENGINE", {}) or {}
        _bw_lo, _bw_hi = _london_breakout_utc_bounds(h1_candles, _fx)
    except Exception:
        _bw_lo, _bw_hi = _LONDON_OPEN[0], _LONDON_OPEN[0] + 2

    if not (_bw_lo <= utc_hour <= _bw_hi):
        return 0.0, None

    asian_candles = []
    for c in h1_candles[-20:]:
        try:
            dt = datetime.fromisoformat(c.get("time", ""))  # candles use "time" key
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            _bar_hour = dt.utctimetuple().tm_hour if dt.tzinfo else dt.hour
            if 0 <= _bar_hour < 7:
                asian_candles.append(c)
        except Exception:
            continue

    if len(asian_candles) < 3:
        return 0.0, None

    asian_high = max(c["high"] for c in asian_candles)
    asian_low = min(c["low"] for c in asian_candles)
    asian_range = asian_high - asian_low

    if asian_range <= 0:
        return 0.0, None

    current = h1_candles[-1]
    current_close = current.get("close", 0)
    current_open = current.get("open", current_close)
    body_size = abs(current_close - current_open)
    candle_range = current.get("high", current_close) - current.get(
        "low", current_close
    )
    body_ratio = body_size / candle_range if candle_range > 0 else 0

    if current_close > asian_high:
        if body_ratio < 0.3:
            return 0.0, None
        breakout_pct = (current_close - asian_high) / asian_range
        return min(1.0, breakout_pct * 3), "LONG"

    if current_close < asian_low:
        if body_ratio < 0.3:
            return 0.0, None
        breakout_pct = (asian_low - current_close) / asian_range
        return min(1.0, breakout_pct * 3), "SHORT"

    return 0.0, None


# ─── UTC hour helper ─────────────────────────────────────────────────────────


def _local_to_utc_hour() -> int:
    """Return current UTC hour directly from the system clock.

    Uses datetime.now(timezone.utc) so the result is always correct regardless
    of the OS timezone setting or SERVER_TZ_OFFSET_HOURS. The offset config is
    no longer needed for session detection and is kept only for backward compat.
    """
    from datetime import datetime, timezone
    utc_h = datetime.now(timezone.utc).hour
    log.debug(f"[FOREX-TZ] utc_hour={utc_h} (direct UTC clock)")
    return utc_h


# ─── Main scoring function ───────────────────────────────────────────────────


def compute_forex_score(
    d1_snap: dict,
    h4_snap: dict,
    h1_snap: dict,
    h1_candles: list,
    pair: dict,
    bar_time: Optional[str] = None,
    backtest_mode: bool = False,
    h4_candles: Optional[list] = None,
    rsi_history_override: Optional[list] = None,
    score_group: Optional[str] = None,
) -> ForexScoreResult:
    """
    Updated with 3 new 2026 SMC edges:
    1. FVG confirmation
    2. Liquidity sweep detection
    3. Volume strength at Asian range / Fib level
    All new logic is isolated and only affects forex scoring.
    """
    result = ForexScoreResult()

    # ── Session eval hour: used for the session gate (London/NY/Asian window) ──
    # In D1 swing backtest, bar_time is forced to 13:00 UTC so the session filter
    # passes (peak London/NY overlap). In H4/H1 BT and live, bar_time is the
    # actual candle timestamp.
    if backtest_mode and bar_time:
        try:
            utc_hour = datetime.fromisoformat(
                str(bar_time).replace("Z", "+00:00")
            ).utctimetuple().tm_hour
        except Exception:
            utc_hour = _local_to_utc_hour()
    else:
        # In live mode, D1 bar_time is always midnight UTC — use real clock instead
        utc_hour = _local_to_utc_hour()

    # ── Breakout eval hour: derived from last H1 candle, NOT from bar_time ────
    # CRIT-01 fix: D1 swing BT passes bar_time=13:00 UTC but the London breakout
    # window (07-09 UTC) must be evaluated against the *actual* H1 candle time.
    # Using the same shared resolver across live, BT, and Engine-C scan.
    breakout_eval_hour = _breakout_eval_hour_from_candles(h1_candles)

    # Hurst Exponent from CONFIRMED H1 close prices only — do not let the live
    # forming bar distort regime classification.
    _h1_for_hurst = h1_candles or []
    try:
        from athena_app.services.market_state import split_market_state

        _ms = split_market_state(
            _h1_for_hurst,
            "H1",
            pair.get("display", pair.get("symbol", "")),
        )
        if _ms.get("confirmed"):
            _h1_for_hurst = list(_ms["confirmed"])
    except Exception:
        pass

    # Use a slightly wider window for stability; 60 bars is too twitchy.
    _closes = [
        float(c["close"])
        for c in (_h1_for_hurst or [])[-100:]
        if c.get("close") is not None
    ]
    _hurst = _hurst_exponent(_closes) if len(_closes) >= 40 else 0.5

    _dfw = DynamicForexWeights()
    _dfw.update(_hurst, backtest_mode)

    # Hurst regime gate — block trend-following entries in mean-reverting markets.
    # Only the London breakout signal (regime-neutral) bypasses this gate.
    # F2 FIX: Now configurable per score_group to allow different behavior for majors vs exotics
    _hurst_gate_enabled = True
    _hurst_threshold = 0.52
    try:
        from config import CONFIG

        _fe = CONFIG.get("FOREX_ENGINE", {}) or {}
        _hurst_gate_enabled = bool(_fe.get("hurst_gate_enabled", True))
        _hurst_threshold = float(_fe.get("hurst_gate_threshold", 0.52))
        
        # F2 FIX: Per-score_group Hurst gate overrides
        _sg = score_group or pair.get("score_group")
        if _sg:
            _sg_hurst_cfg = ((_fe.get("hurst_gate_by_group", {}) or {}).get(_sg, {}))
            if isinstance(_sg_hurst_cfg, dict):
                if "enabled" in _sg_hurst_cfg:
                    _hurst_gate_enabled = bool(_sg_hurst_cfg.get("enabled", _hurst_gate_enabled))
                if "threshold" in _sg_hurst_cfg:
                    _hurst_threshold = float(_sg_hurst_cfg.get("threshold", _hurst_threshold))

        # Backtest-only opt-in bypass — measurement flag, reversible via config.
        # When BACKTEST_DISABLE_HURST_GATE is true, backtests ignore the Hurst
        # veto so we can compare WR/PF with and without the gate. Live is
        # never affected by this flag.
        if backtest_mode and bool(CONFIG.get("BACKTEST_DISABLE_HURST_GATE", False)):
            _hurst_gate_enabled = False
    except Exception:
        pass

    log.debug(
        "[FOREX HURST] %s hurst=%.3f threshold=%.3f bars_full=%d bars_used=%d",
        pair.get("display", "?"),
        _hurst,
        _hurst_threshold,
        len(h1_candles or []),
        len(_closes),
    )

    _hurst_trending = _hurst > _hurst_threshold

    trend_ok_raw, trend_dir, trend_detail = _trend_gate_detail(d1_snap, h4_snap)
    trend_ok = trend_ok_raw
    _hurst_veto_trend = False
    if _hurst_gate_enabled and not _hurst_trending:
        # Market is mean-reverting or random walk — skip trend-following signals.
        # London breakout (below) is still allowed because breakouts are regime-neutral.
        if trend_ok:
            _hurst_veto_trend = True
        trend_ok = False
    trend_gate_reason = (
        "hurst_veto_trend" if _hurst_veto_trend else str(trend_detail.get("reason") or "unknown")
    )

    # Pre-compute COT boost for both direction cases to avoid double DB queries
    _cot_trend = _cot_boost(pair, trend_dir, bar_time)
    # For breakout: direction may differ — only fetch if different from trend_dir
    # (avoids a second DB hit in the common case where directions agree)
    # BUG 3 fix: Restore parity between forex backtest and live scoring for session validation.
    # Default to parity (respect sessions). Opt-in to bypass via BACKTEST_IGNORE_SESSIONS.
    _ss = _session_state(utc_hour, pair.get("display", ""))
    session_ok = _ss["is_active"]
    session_mult = _ss["multiplier"]
    
    from config import CONFIG
    _ignore_sessions = CONFIG.get("BACKTEST_IGNORE_SESSIONS", False)
    if backtest_mode and _ignore_sessions:
        session_ok = True
        session_mult = 1.0
    momentum_score = _momentum_confirm(h4_snap, trend_dir)
    adx_score = _adx_filter(h4_snap)
    carry_score = _carry_tilt(pair, trend_dir, bar_time)
    result.trend_gate = trend_ok
    result.session_active = session_ok
    result.momentum_confirm = momentum_score
    result.adx_filter = adx_score
    result.carry_tilt = carry_score

    try:
        from config import CONFIG

        fx_cfg = CONFIG.get("FOREX_ENGINE", {}) or {}
        trend_support_weights = fx_cfg.get("trend_support_weights", {}) or {}
    except Exception:
        fx_cfg = {}
        trend_support_weights = {}
    momentum_w = float(trend_support_weights.get("momentum", 0.15))
    adx_w = float(trend_support_weights.get("adx", 0.10))
    carry_w = float(trend_support_weights.get("carry", 0.05))

    # Optional subgroup-level scoring adjustments.
    _sg = score_group or pair.get("score_group")
    _sg_cfg = (
        ((fx_cfg or {}).get("score_group_adjustments", {}) or {}).get(_sg, {})
        if isinstance(_sg, str) and _sg
        else {}
    )
    try:
        momentum_w *= float(_sg_cfg.get("momentum_mult", 1.0))
        adx_w *= float(_sg_cfg.get("adx_mult", 1.0))
        carry_w *= float(_sg_cfg.get("carry_mult", 1.0))
    except Exception:
        pass

    if not session_ok:
        log.debug(
            f"[FOREX] {pair.get('display', '?')} session closed (utc_hour={utc_hour})"
        )
    if not trend_ok:
        _adx_val = d1_snap.get("adx")
        _d1c = d1_snap.get("close")
        _d1e200 = d1_snap.get("ema200")
        _h4e50 = h4_snap.get("ema50")
        _h4e200 = h4_snap.get("ema200")
        _trend_msg = (
            f"[FOREX] {pair.get('display', '?')} trend_gate=False "
            f"adx={_adx_val} d1_close={_d1c} d1_ema200={_d1e200} "
            f"h4_ema50={_h4e50} h4_ema200={_h4e200}"
        )
        log.debug(_trend_msg)

    trend_score = 0.0
    if trend_ok and session_ok:
        rsi_history = rsi_history_override if rsi_history_override else None
        eq = _entry_quality(h1_snap, trend_dir, rsi_history)
        cot = _cot_trend
        # Removed 1.0 cap — true max is ~1.30 before SMC bonuses
        trend_score = (
            _dfw.score(eq, cot)
            + momentum_score * momentum_w
            + adx_score * adx_w
            + carry_score * carry_w
        )
        
        # Apply Session and Soft ADX multipliers
        trend_score *= session_mult
        trend_score *= trend_detail.get("adx_gate_multiplier", 1.0)

        result.entry_quality = eq
        result.cot_boost = cot

    # ── Signal 2: London breakout ─────────────────────────────────────────
    # CRIT-01: Use breakout_eval_hour (last H1 candle time) NOT utc_hour (bar_time).
    # This ensures parity: live, D1 swing BT, intraday BT, scalp BT, Engine-C.
    bo_score, bo_dir = _london_breakout_score(h1_candles, breakout_eval_hour)
    if bo_score > 0 and session_ok:
        cot_bo = _cot_boost(pair, bo_dir, bar_time) if bo_dir != trend_dir else _cot_trend
        bo_final = bo_score * (1.0 + cot_bo * 0.3) * session_mult
        result.breakout_score = bo_score
    else:
        bo_final = 0.0
        bo_dir = trend_dir

    # ── NEW: 3 SMC Upgrades (2026 edge) ─────────────────────────────────────
    try:
        from indicators import (
            detect_fvg,
            detect_liquidity_sweep,
            volume_strength_at_level,
            calc_fib,
        )

        atr = h4_snap.get("atr", 0.0)
        fvg_bonus = 0.0
        liquidity_bonus = 0.0
        volume_bonus = 0.0

        direction = trend_dir if trend_score >= bo_final else bo_dir

        # 1. FVG confirmation
        fvgs = detect_fvg(h4_candles or [])
        current_price = h4_snap.get("close", 0)
        # Check if current price falls within any FVG zone (proper SMC overlap)
        fvg_overlap = (
            any(fvg["bottom"] <= current_price <= fvg["top"] for fvg in fvgs)
            if fvgs
            else False
        )
        if fvg_overlap:
            fvg_bonus = 0.20

        # 2. Liquidity sweep
        h1_highs = [c.get("high", 0) for c in (h1_candles or []) if c.get("high")]
        h1_lows = [c.get("low", 0) for c in (h1_candles or []) if c.get("low")]
        if h1_highs and h1_lows and detect_liquidity_sweep(h1_candles or [], atr):
            liquidity_bonus = 0.15

        # 3. Volume strength at Asian range or Fib level
        fib = calc_fib(h4_candles or [])
        key_level = (
            fib.get("fib618", current_price)
            if direction == "LONG"
            else fib.get("fib382", current_price)
        )
        volume_bonus = volume_strength_at_level(h1_candles or [], key_level) * 0.10  # 0.0–0.10
    except Exception as e:
        log.error(f"[FOREX SMC ERROR] {e}", exc_info=True)
        fvg_bonus, liquidity_bonus, volume_bonus, fvg_overlap = 0.0, 0.0, 0.0, False

    # ── Counter-trend SHORT penalty ───────────────────────────────────────────
    # Data support (2026-04-21 backtest, n=200): hurst and trend_gate_raw are
    # SUSPECT in forex/SHORT even after direction normalization — both carry
    # negative correlation with R in SHORT trades.
    #
    # Two mechanisms:
    #   A. London breakout SHORT when EMA stack is persistently LONG and Hurst
    #      shows trend persistence → counter-trend breakout; fail most of these.
    #   B. Trend-path SHORT with very high Hurst (>0.65) → mature downtrend,
    #      higher reversal risk; soft penalty so structurally strong SHORTs pass.
    _ct_short_applied = False
    _ct_case = ""
    try:
        _ct_cfg = (fx_cfg.get("counter_trend_short_gate", {}) or {})
        if bool(_ct_cfg.get("enabled", True)):
            # Case A: breakout SHORT while EMA stack is LONG and Hurst trending
            if (
                bo_final > 0
                and bo_dir == "SHORT"
                and trend_dir == "LONG"
                and trend_ok_raw
                and _hurst_trending
            ):
                _ct_bo_mult = float(_ct_cfg.get("bo_counter_trend_mult", 0.70))
                bo_final = bo_final * _ct_bo_mult
                _ct_short_applied = True
                _ct_case = f"case_A_mult={_ct_bo_mult:.2f}"
                log.debug(
                    "[FOREX-CT] %s counter-trend SHORT bo x%.2f hurst=%.3f",
                    pair.get("display", "?"), _ct_bo_mult, _hurst,
                )
            # Case B: trend-path SHORT with very high Hurst (mature downtrend)
            if (
                trend_score > 0
                and trend_dir == "SHORT"
                and _hurst > float(_ct_cfg.get("high_hurst_threshold", 0.65))
            ):
                _ct_trend_mult = float(_ct_cfg.get("trend_high_hurst_short_mult", 0.80))
                trend_score = trend_score * _ct_trend_mult
                _ct_short_applied = True
                _ct_case = (_ct_case + "+case_B") if _ct_case else f"case_B_mult={_ct_trend_mult:.2f}"
                log.debug(
                    "[FOREX-CT] %s mature-downtrend SHORT penalty x%.2f hurst=%.3f",
                    pair.get("display", "?"), _ct_trend_mult, _hurst,
                )
    except Exception as _ct_err:
        log.debug("[FOREX-CT] penalty skipped: %s", _ct_err)

    # ── Final score with additive SMC bonus (capped) ──────────────────────────
    # Bonuses are additive and capped so they can only nudge near-threshold signals,
    # not rescue weak base scores. Multiplicative stacking (old: up to 1.518×) allowed
    # a base of 0.70 to pass the 1.0 gate purely on pattern presence — now prevented.
    # F1 FIX: When trend_score == bo_final, use momentum confirmation as tie-breaker
    # instead of always favoring trend. This allows valid breakout reversals to win.
    if trend_score > bo_final:
        base_score = trend_score
        result.direction = trend_dir
        result.signal_type = "TREND_PULLBACK" if trend_score > 0 else "NONE"
    elif bo_final > trend_score:
        base_score = bo_final
        result.direction = bo_dir
        result.signal_type = "LONDON_BREAKOUT"
    else:
        # Tie-breaker: use momentum_score to decide (positive = trend, negative = breakout)
        # If momentum is neutral, prefer breakout as it's more time-sensitive
        if momentum_score > 0 and trend_score > 0:
            base_score = trend_score
            result.direction = trend_dir
            result.signal_type = "TREND_PULLBACK"
        elif bo_final > 0:
            base_score = bo_final
            result.direction = bo_dir
            result.signal_type = "LONDON_BREAKOUT"
        else:
            base_score = trend_score
            result.direction = trend_dir
            result.signal_type = "TREND_PULLBACK" if trend_score > 0 else "NONE"
    smc_bonus = min(0.20, fvg_bonus + liquidity_bonus + volume_bonus)
    final_score = round(base_score + smc_bonus, 4)

    result.final_score = min(2.0, final_score)  # safety cap at 2.0
    try:
        result.final_score = max(
            0.0, min(2.0, float(result.final_score) * float(_sg_cfg.get("score_mult", 1.0)))
        )
    except Exception:
        pass

    zero_score_reasons = []
    if result.final_score <= 0:
        if not session_ok:
            zero_score_reasons.append("session_inactive")
        if _hurst_veto_trend:
            zero_score_reasons.append("hurst_veto_trend")
        elif not trend_ok_raw:
            _raw_reason = str(trend_detail.get("reason") or "").strip()
            if _raw_reason and not _raw_reason.startswith("passed_"):
                zero_score_reasons.append(f"trend_gate_{_raw_reason}")
            else:
                zero_score_reasons.append("trend_gate_blocked")
        if trend_score <= 0:
            zero_score_reasons.append("trend_path_inactive")
        if bo_score <= 0:
            zero_score_reasons.append("breakout_inactive")
        if not zero_score_reasons:
            zero_score_reasons.append("zero_base_score")
    zero_score_reasons = _unique_reason_codes(zero_score_reasons)

    result.components = {
        "trend_gate": trend_ok,
        "trend_gate_raw": trend_ok_raw,
        "trend_gate_reason": trend_gate_reason,
        "trend_gate_reason_raw": trend_detail.get("reason"),
        "trend_gate_direction": trend_dir,
        "trend_gate_adx": round(float(trend_detail.get("adx", 0.0) or 0.0), 4),
        "trend_gate_adx_timeframe": trend_detail.get("adx_timeframe"),
        "trend_gate_adx_min": round(float(trend_detail.get("adx_min", 0.0) or 0.0), 4),
        "trend_gate_margin": (
            round(float(trend_detail["margin"]), 6)
            if trend_detail.get("margin") is not None
            else None
        ),
        "trend_gate_margin_min": round(
            float(trend_detail.get("margin_min", 0.0) or 0.0), 6
        ),
        "trend_gate_d1_bull": trend_detail.get("d1_bull"),
        "trend_gate_h4_bull": trend_detail.get("h4_bull"),
        "trend_gate_d1_slope": round(
            float(trend_detail.get("d1_slope", 0.0) or 0.0), 6
        ),
        "session_active": session_ok,
        "utc_hour": utc_hour,
        "breakout_eval_hour": breakout_eval_hour,
        "entry_quality": result.entry_quality,
        "momentum_confirm": round(result.momentum_confirm, 3),
        "adx_filter": round(result.adx_filter, 3),
        "cot_boost": result.cot_boost,
        "carry_tilt": round(result.carry_tilt, 3),
        "breakout_score": bo_score,
        "trend_score": round(trend_score, 4),
        "breakout_final": round(bo_final, 4),
        "hurst": round(_hurst, 3),
        "hurst_trending": _hurst_trending,
        "hurst_gate_enabled": _hurst_gate_enabled,
        "hurst_gate_threshold": round(_hurst_threshold, 4),
        "hurst_veto_trend": _hurst_veto_trend,
        "regime": _dfw.regime,
        # newly added visibility metrics
        "fvg_bonus": round(fvg_bonus, 3),
        "liquidity_sweep": liquidity_bonus > 0,
        "volume_strength": round(volume_bonus, 3),
        "fvg_overlap": fvg_overlap,
        "zero_score_reasons": zero_score_reasons,
        "counter_trend_short_applied": _ct_short_applied,
        "counter_trend_case": _ct_case,
    }

    return result
