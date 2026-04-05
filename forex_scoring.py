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
Threshold: MIN_FOREX_CONFLUENCE / MIN_CONFLUENCE_CLASS.forex (see config.yaml)
"""

from __future__ import annotations
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
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


def _in_session(utc_hour: int, pair_display: str = "") -> bool:
    """True if current hour is inside an active session for this pair.

    Asian session (00:00-08:00 UTC) only valid for JPY and AUD/NZD crosses
    where Tokyo/Sydney volume is meaningful. All other pairs: London + NY only.
    """
    try:
        from config import CONFIG

        if not CONFIG.get("FOREX_SESSION_FILTER", True):
            return True  # session filter disabled — always trade
    except Exception:
        pass
    # London and NY are valid for all forex pairs
    if (
        _LONDON_OPEN[0] <= utc_hour <= _LONDON_OPEN[1]
        or _NY_OPEN[0] <= utc_hour <= _NY_OPEN[1]
    ):
        return True
    # Asian session only for JPY/AUD/NZD crosses
    if (
        _ASIAN_OPEN[0] <= utc_hour <= _ASIAN_OPEN[1]
        and pair_display in _ASIAN_SESSION_PAIRS
    ):
        return True
    return False


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
    try:
        from config import CONFIG

        fx_cfg = CONFIG.get("FOREX_ENGINE", {}) or {}
        trend_gate_adx_min = float(fx_cfg.get("trend_gate_adx_min", 20.0))
        trend_margin_min = float(fx_cfg.get("trend_margin_min", 0.003))
    except Exception:
        trend_gate_adx_min = 20.0
        trend_margin_min = 0.003
    if adx < trend_gate_adx_min:
        return False, "LONG"  # not trending — skip regardless of EMA alignment

    d1_close = d1_snap.get("close")
    d1_ema200 = d1_snap.get("ema200")
    h4_ema50 = h4_snap.get("ema50")
    h4_ema200 = h4_snap.get("ema200")
    d1_slope = d1_snap.get("ema200Slope10", 0) or 0

    if None in (d1_close, d1_ema200, h4_ema50, h4_ema200):
        return False, "LONG"

    d1_bull = d1_close > d1_ema200
    h4_bull = h4_ema50 > h4_ema200

    if d1_bull and h4_bull:
        margin = (d1_close - d1_ema200) / d1_ema200 if d1_ema200 != 0 else 0.0
        if margin > trend_margin_min or d1_slope > 0:
            return True, "LONG"
        return False, "LONG"

    if not d1_bull and not h4_bull:
        margin = (d1_ema200 - d1_close) / d1_ema200 if d1_ema200 != 0 else 0.0
        if margin > trend_margin_min or d1_slope < 0:
            return True, "SHORT"
        return False, "SHORT"

    return False, "LONG"  # mixed


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
    ADX >= 25 = confirmed trend (1.0)
    ADX < 25 = no trend / choppy (0.0 — should not trade)
    Binary filter: no partial credit for developing trends.
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
    if adx >= adx_confirm_min:
        return 1.0
    return 0.0


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
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            _bar_hour = dt.utctimetuple().tm_hour if dt.tzinfo else dt.hour
            if 0 <= _bar_hour < 7:
                asian_candles.append(c)
        except Exception:
            continue

    if len(asian_candles) < 3:
        return 0.0, "LONG"

    asian_high = max(c["high"] for c in asian_candles)
    asian_low = min(c["low"] for c in asian_candles)
    asian_range = asian_high - asian_low

    if asian_range <= 0:
        return 0.0, "LONG"

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

    if backtest_mode and bar_time:
        # In backtest, use the historical bar's timestamp (UTC) for session check
        try:
            utc_hour = datetime.fromisoformat(bar_time).hour
        except Exception:
            utc_hour = _local_to_utc_hour()
    else:
        # In live mode, D1 bar_time is always midnight UTC — use real clock instead
        utc_hour = _local_to_utc_hour()

    # Hurst Exponent from H1 close prices — determines regime weighting
    _closes = [c.get("close", 0) for c in (h1_candles or [])[-60:] if c.get("close")]
    _hurst = _hurst_exponent(_closes) if len(_closes) >= 20 else 0.5

    _dfw = DynamicForexWeights()
    _dfw.update(_hurst, backtest_mode)

    # Hurst regime gate — block trend-following entries in mean-reverting markets.
    # Only the London breakout signal (regime-neutral) bypasses this gate.
    _hurst_gate_enabled = True
    _hurst_threshold = 0.52
    try:
        from config import CONFIG

        _fe = CONFIG.get("FOREX_ENGINE", {}) or {}
        _hurst_gate_enabled = bool(_fe.get("hurst_gate_enabled", True))
        _hurst_threshold = float(_fe.get("hurst_gate_threshold", 0.52))
    except Exception:
        pass

    _hurst_trending = _hurst > _hurst_threshold

    trend_ok, trend_dir = _check_trend_gate(d1_snap, h4_snap)
    _hurst_veto_trend = False
    if _hurst_gate_enabled and not _hurst_trending:
        # Market is mean-reverting or random walk — skip trend-following signals.
        # London breakout (below) is still allowed because breakouts are regime-neutral.
        if trend_ok:
            _hurst_veto_trend = True
        trend_ok = False

    # Pre-compute COT boost for both direction cases to avoid double DB queries
    _cot_trend = _cot_boost(pair, trend_dir, bar_time)
    # For breakout: direction may differ — only fetch if different from trend_dir
    # (avoids a second DB hit in the common case where directions agree)
    # BUG 3 fix: Restore parity between forex backtest and live scoring for session validation.
    # Default to parity (respect sessions). Opt-in to bypass via BACKTEST_IGNORE_SESSIONS.
    from config import CONFIG
    _ignore_sessions = CONFIG.get("BACKTEST_IGNORE_SESSIONS", False)
    if backtest_mode and _ignore_sessions:
        session_ok = True
    else:
        session_ok = _in_session(utc_hour, pair.get("display", ""))
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
        trend_score = min(
            1.0,
            _dfw.score(eq, cot)
            + momentum_score * momentum_w
            + adx_score * adx_w
            + carry_score * carry_w,
        )

        result.entry_quality = eq
        result.cot_boost = cot

    # ── Signal 2: London breakout ─────────────────────────────────────────
    bo_score, bo_dir = _london_breakout_score(h1_candles, utc_hour)
    if bo_score > 0:
        cot_bo = _cot_boost(pair, bo_dir, bar_time) if bo_dir != trend_dir else _cot_trend
        bo_final = bo_score * (1.0 + cot_bo * 0.3)
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
            fvg_bonus = 0.20  # multiplicative: 1.20x

        # 2. Liquidity sweep
        if detect_liquidity_sweep(h1_candles or [], atr):
            liquidity_bonus = 0.15  # multiplicative: 1.15x

        # 3. Volume strength at Asian range or Fib level
        fib = calc_fib(h4_candles or [])
        key_level = (
            fib.get("fib618", current_price)
            if direction == "LONG"
            else fib.get("fib382", current_price)
        )
        volume_bonus = volume_strength_at_level(h1_candles or [], key_level) * 0.10  # up to 1.10x
    except Exception as e:
        log.error(f"[FOREX SMC ERROR] {e}")
        fvg_bonus, liquidity_bonus, volume_bonus, fvg_overlap = 0.0, 0.0, 0.0, False

    # ── Final score with multiplicative bonuses (avoids 1.0 saturation) ─────
    if trend_score >= bo_final:
        base_score = trend_score
        result.direction = trend_dir
        result.signal_type = "TREND_PULLBACK" if trend_score > 0 else "NONE"
    else:
        base_score = bo_final
        result.direction = bo_dir
        result.signal_type = "LONDON_BREAKOUT"
    final_score = round(base_score * (1.0 + fvg_bonus) * (1.0 + liquidity_bonus) * (1.0 + volume_bonus), 4)

    result.final_score = min(1.0, final_score)  # keep 0–1 scale
    try:
        result.final_score = max(
            0.0, min(1.0, float(result.final_score) * float(_sg_cfg.get("score_mult", 1.0)))
        )
    except Exception:
        pass

    result.components = {
        "trend_gate": trend_ok,
        "session_active": session_ok,
        "utc_hour": utc_hour,
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
    }

    return result
