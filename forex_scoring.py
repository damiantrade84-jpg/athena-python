"""
forex_scoring.py — Dedicated forex scoring engine for Athena.

Replaces Z-score factor engine for forex pairs with a rules-based system
calibrated to how forex actually moves:
  - Trend gate: D1/H4 EMA alignment (binary — on or off)
  - Session filter: London (07:00-11:00 UTC) and NY (12:00-16:00 UTC) only
  - Entry quality: RSI(14) H1 pullback depth
  - COT confirmation: commercial positioning as signal booster
  - London breakout: Asian range breakout at session open

Output: ForexScoreResult with final_score, direction, signal_type, components
Threshold: MIN_FOREX_CONFLUENCE (default 0.60) in config.yaml
"""

from __future__ import annotations
import os
import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

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

_ASIAN_OPEN   = (0,  8)   # 00:00–08:00 UTC  (Tokyo/Sydney)
_LONDON_OPEN  = (7, 11)   # 07:00–11:00 UTC
_NY_OPEN      = (12, 16)  # 12:00–16:00 UTC


def _in_session(utc_hour: int) -> bool:
    """True if current hour is inside Asian, London, or NY open window."""
    try:
        from config import CONFIG
        if not CONFIG.get("FOREX_SESSION_FILTER", True):
            return True  # session filter disabled — always trade
    except Exception:
        pass
    return (_ASIAN_OPEN[0]  <= utc_hour <= _ASIAN_OPEN[1]  or
            _LONDON_OPEN[0] <= utc_hour <= _LONDON_OPEN[1] or
            _NY_OPEN[0]     <= utc_hour <= _NY_OPEN[1])


def _hurst_exponent(prices: list, max_lag: int = 20) -> float:
    """
    Hurst Exponent via variance of lagged differences.
    H < 0.45 = mean-reverting  H > 0.55 = trending  H = 0.5 = random walk
    """
    if len(prices) < max_lag * 2:
        return 0.5
    try:
        arr  = np.array(prices, dtype=float)
        lags = range(2, max_lag)
        tau  = [np.sqrt(np.std(np.subtract(arr[lag:], arr[:-lag])))
                for lag in lags]
        tau  = [t for t in tau if t > 0]
        if len(tau) < 2:
            return 0.5
        valid_lags = list(lags)[:len(tau)]
        poly = np.polyfit(np.log(valid_lags), np.log(tau), 1)
        return float(np.clip(poly[0] * 2.0, 0.0, 1.0))
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
        self.rsi_w  = 0.40
        self.cot_w  = 0.20
        self.base   = 0.30
        self.regime = "NEUTRAL"

    def update(self, hurst: float, backtest_mode: bool = False) -> None:
        b = 0.25 if backtest_mode else 0.35
        if hurst < 0.45:
            self.base   = b - 0.05
            self.rsi_w  = 0.60
            self.cot_w  = 0.20
            self.regime = "MEAN_REVERTING"
        elif hurst > 0.55:
            self.base   = b + 0.05
            self.rsi_w  = 0.20
            self.cot_w  = 0.25
            self.regime = "TRENDING"
        else:
            self.base   = b
            self.rsi_w  = 0.40
            self.cot_w  = 0.20
            self.regime = "NEUTRAL"

    def score(self, eq: float, cot: float) -> float:
        return self.base + eq * self.rsi_w + cot * self.cot_w


# ─── Trend gate ──────────────────────────────────────────────────────────────

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
    # ADX filter — market must be trending, not ranging
    # ADX < 20 = choppy/ranging = EMA signals are noise in forex
    adx = d1_snap.get("adx")
    if adx is None:
        adx = 0.0
    try:
        adx = float(adx)
    except (ValueError, TypeError):
        adx = 0.0
    if adx < 20.0:
        return False, "LONG"  # not trending — skip regardless of EMA alignment

    d1_close  = d1_snap.get("close")
    d1_ema200 = d1_snap.get("ema200")
    h4_ema50  = h4_snap.get("ema50")
    h4_ema200 = h4_snap.get("ema200")
    d1_slope  = d1_snap.get("ema200Slope10", 0) or 0

    if None in (d1_close, d1_ema200, h4_ema50, h4_ema200):
        return False, "LONG"

    d1_bull = d1_close > d1_ema200
    h4_bull = h4_ema50 > h4_ema200

    if d1_bull and h4_bull:
        margin = (d1_close - d1_ema200) / d1_ema200
        if margin > 0.003 or d1_slope > 0:
            return True, "LONG"
        return False, "LONG"

    if not d1_bull and not h4_bull:
        margin = (d1_ema200 - d1_close) / d1_ema200
        if margin > 0.003 or d1_slope < 0:
            return True, "SHORT"
        return False, "SHORT"

    return False, "LONG"  # mixed


# ─── Entry quality (RSI pullback) ────────────────────────────────────────────

def _entry_quality(h1_snap: dict, direction: str,
                   rsi_history: list = None) -> float:
    rsi = h1_snap.get("rsi") or h1_snap.get("rsi14")
    if rsi is None:
        return 0.3

    if rsi_history and len(rsi_history) >= 10:
        rsi_z = _mad_zscore(rsi, rsi_history)
        if direction == "LONG":
            if rsi_z <= -0.5:   return 1.0
            elif rsi_z <= 0.0:  return 0.6
            elif rsi_z <= 0.5:  return 0.3
            else:               return 0.0
        else:
            if rsi_z >= 0.5:    return 1.0
            elif rsi_z >= 0.0:  return 0.6
            elif rsi_z >= -0.5: return 0.3
            else:               return 0.0

    if direction == "LONG":
        if 35 <= rsi <= 55:   return 1.0
        elif 55 < rsi <= 65:  return 0.5
        elif rsi < 35:        return 0.2
        else:                 return 0.0
    else:
        if 45 <= rsi <= 65:   return 1.0
        elif 35 <= rsi < 45:  return 0.5
        elif rsi > 65:        return 0.2
        else:                 return 0.0


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
    ADX >= 25 = confirmed trend (1.0)
    ADX < 25 = no trend / choppy (0.0 — should not trade)
    Binary filter: no partial credit for developing trends.
    """
    adx = h4_snap.get("adx")
    if adx is None:
        return 0.3  # neutral if no data
    if adx >= 25:
        return 1.0
    return 0.0


# ─── Carry tilt ───────────────────────────────────────────────────────────────

def _carry_tilt(pair: dict, direction: str,
                bar_time: Optional[str] = None) -> float:
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
        if (direction == "LONG" and carry_z > 0) or (direction == "SHORT" and carry_z < 0):
            return min(1.0, abs(carry_z) / 2.0)
        return 0.0  # opposing carry = no boost, not penalty
    except Exception:
        return 0.0


# ─── COT boost ───────────────────────────────────────────────────────────────

def _cot_boost(pair: dict, direction: str,
               bar_time: Optional[str] = None) -> float:
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
        if direction == "LONG" and cot_z >= 1.0:
            return min(1.0, cot_z / 3.0)
        elif direction == "SHORT" and cot_z <= -1.0:
            return min(1.0, abs(cot_z) / 3.0)
        return 0.0
    except Exception:
        return 0.0


# ─── London breakout ─────────────────────────────────────────────────────────

def _london_breakout_score(h1_candles: list, utc_hour: int) -> tuple[float, str]:
    """
    London breakout signal.
    Measures Asian session range (00:00-07:00 UTC) from recent H1 candles.
    At London open, checks if current bar breaks above/below that range.
    Returns (score 0-1, direction).
    """
    if not h1_candles or len(h1_candles) < 10:
        return 0.0, "LONG"

    if not (_LONDON_OPEN[0] <= utc_hour <= _LONDON_OPEN[0] + 2):
        return 0.0, "LONG"

    asian_candles = []
    for c in h1_candles[-20:]:
        try:
            dt = datetime.fromisoformat(c.get("time", ""))  # candles use "time" key
            if 0 <= dt.hour < 7:
                asian_candles.append(c)
        except Exception:
            continue

    if len(asian_candles) < 3:
        return 0.0, "LONG"

    asian_high  = max(c["high"] for c in asian_candles)
    asian_low   = min(c["low"]  for c in asian_candles)
    asian_range = asian_high - asian_low

    if asian_range <= 0:
        return 0.0, "LONG"

    current       = h1_candles[-1]
    current_close = current.get("close", 0)
    current_open  = current.get("open", current_close)
    body_size     = abs(current_close - current_open)
    candle_range  = current.get("high", current_close) - current.get("low", current_close)
    body_ratio    = body_size / candle_range if candle_range > 0 else 0

    if current_close > asian_high:
        if body_ratio < 0.3:
            return 0.0, "LONG"
        breakout_pct = (current_close - asian_high) / asian_range
        return min(1.0, breakout_pct * 3), "LONG"

    if current_close < asian_low:
        if body_ratio < 0.3:
            return 0.0, "SHORT"
        breakout_pct = (asian_low - current_close) / asian_range
        return min(1.0, breakout_pct * 3), "SHORT"

    return 0.0, "LONG"


# ─── UTC hour helper ─────────────────────────────────────────────────────────

def _local_to_utc_hour() -> int:
    """
    Derive UTC hour from local system clock + SERVER_TZ_OFFSET_HOURS config.
    Uses datetime.now() (local time) to avoid relying on the OS timezone
    database, which can be misconfigured on Windows machines.
    Set SERVER_TZ_OFFSET_HOURS = 2 for SAST (GMT+2).
    """
    try:
        from config import CONFIG
        offset = int(CONFIG.get("SERVER_TZ_OFFSET_HOURS", 2))
    except Exception:
        offset = 2
    local_hour = datetime.now().hour
    utc_h = (local_hour - offset) % 24
    log.debug(f"[FOREX-TZ] local_hour={local_hour} offset={offset} utc_hour={utc_h}")
    log.debug(f"[FOREX-TZ] local_hour={local_hour} offset={offset} utc_hour={utc_h}")
    return utc_h


# ─── Main scoring function ───────────────────────────────────────────────────

def compute_forex_score(
    d1_snap:       dict,
    h4_snap:       dict,
    h1_snap:       dict,
    h1_candles:    list,
    pair:          dict,
    bar_time:      Optional[str] = None,
    backtest_mode: bool = False,
    h4_candles:    Optional[list] = None,
) -> ForexScoreResult:
    """
    Updated with 3 new 2026 SMC edges:
    1. FVG confirmation
    2. Liquidity sweep detection
    3. Volume strength at Asian range / Fib level
    All new logic is isolated and only affects forex scoring.
    """
    result = ForexScoreResult()

    if bar_time:
        try:
            utc_hour = datetime.fromisoformat(bar_time).hour
        except Exception:
            utc_hour = _local_to_utc_hour()
    else:
        utc_hour = _local_to_utc_hour()

    # Hurst Exponent from H1 close prices — determines regime weighting
    _closes = [c.get("close", 0) for c in (h1_candles or [])[-60:]
               if c.get("close")]
    _hurst  = _hurst_exponent(_closes) if len(_closes) >= 20 else 0.5

    _dfw = DynamicForexWeights()
    _dfw.update(_hurst, backtest_mode)

    trend_ok, trend_dir = _check_trend_gate(d1_snap, h4_snap)
    session_ok = True if backtest_mode else _in_session(utc_hour)

    if not session_ok:
        log.info(f"[FOREX] {pair.get('display','?')} session closed (utc_hour={utc_hour})")
    if not trend_ok:
        _adx_val = d1_snap.get("adx")
        _d1c     = d1_snap.get("close")
        _d1e200  = d1_snap.get("ema200")
        _h4e50   = h4_snap.get("ema50")
        _h4e200  = h4_snap.get("ema200")
        log.info(
            f"[FOREX] {pair.get('display','?')} trend_gate=False "
            f"adx={_adx_val} d1_close={_d1c} d1_ema200={_d1e200} "
            f"h4_ema50={_h4e50} h4_ema200={_h4e200}"
        )

    trend_score = 0.0
    if trend_ok and session_ok:
        rsi_history = [c.get("rsi", 50) for c in (h1_candles or [])[-40:]
                       if c.get("rsi") is not None]
        eq  = _entry_quality(h1_snap, trend_dir, rsi_history)
        cot = _cot_boost(pair, trend_dir, bar_time)
        trend_score = _dfw.score(eq, cot)

        result.trend_gate     = trend_ok
        result.session_active = session_ok
        result.entry_quality  = eq
        result.cot_boost      = cot

    # ── Signal 2: London breakout ─────────────────────────────────────────
    bo_score, bo_dir = _london_breakout_score(h1_candles, utc_hour)
    if bo_score > 0:
        cot_bo   = _cot_boost(pair, bo_dir, bar_time)
        bo_final = bo_score * (1.0 + cot_bo * 0.3)
        result.breakout_score = bo_score
    else:
        bo_final = 0.0
        bo_dir   = trend_dir

    # ── NEW: 3 SMC Upgrades (2026 edge) ─────────────────────────────────────
    try:
        from indicators import detect_fvg, detect_liquidity_sweep, volume_strength_at_level, calc_fib
        atr = h4_snap.get("atr", 0.0)
        fvg_bonus = 0.0
        liquidity_bonus = 0.0
        volume_bonus = 0.0
        
        direction = trend_dir if trend_score >= bo_final else bo_dir

        # 1. FVG confirmation
        fvgs = detect_fvg(h4_candles or [])
        current_price = h4_snap.get("close", 0)
        # Check if current price falls within any FVG zone (proper SMC overlap)
        fvg_overlap = any(
            fvg["bottom"] <= current_price <= fvg["top"]
            for fvg in fvgs
        ) if fvgs else False
        if fvg_overlap:
            fvg_bonus = 0.35

        # 2. Liquidity sweep
        if detect_liquidity_sweep(h1_candles or [], atr):
            liquidity_bonus = 0.30

        # 3. Volume strength at Asian range or Fib level
        fib = calc_fib(h4_candles or [])
        key_level = fib.get("fib618", current_price) if direction == "LONG" else fib.get("fib382", current_price)
        volume_bonus = volume_strength_at_level(h1_candles or [], key_level) * 0.25
    except Exception as e:
        log.error(f"[FOREX SMC ERROR] {e}")
        fvg_bonus, liquidity_bonus, volume_bonus, fvg_overlap = 0.0, 0.0, 0.0, False

    # ── Final score with bonuses ────────────────────────────────────────────
    if trend_score >= bo_final:
        final_score = round(trend_score + fvg_bonus + liquidity_bonus + volume_bonus, 4)
        result.direction = trend_dir
        result.signal_type = "TREND_PULLBACK" if trend_score > 0 else "NONE"
    else:
        final_score = round(bo_final + fvg_bonus + liquidity_bonus + volume_bonus, 4)
        result.direction = bo_dir
        result.signal_type = "LONDON_BREAKOUT"

    result.final_score = min(1.0, final_score)  # keep 0–1 scale

    result.components = {
        "trend_gate":        trend_ok,
        "session_active":    session_ok,
        "utc_hour":          utc_hour,
        "entry_quality":     result.entry_quality,
        "momentum_confirm":  result.momentum_confirm,
        "adx_filter":        result.adx_filter,
        "cot_boost":         result.cot_boost,
        "carry_tilt":        result.carry_tilt,
        "breakout_score":    bo_score,
        "trend_score":       round(trend_score, 4),
        "breakout_final":    round(bo_final, 4),
        "hurst":             round(_hurst, 3),
        "regime":            _dfw.regime,
        # newly added visibility metrics
        "fvg_bonus":         round(fvg_bonus, 3),
        "liquidity_sweep":   liquidity_bonus > 0,
        "volume_strength":   round(volume_bonus, 3),
        "fvg_overlap":       fvg_overlap,
    }

    return result
