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
from datetime import datetime, timezone
from typing import Optional

log = logging.getLogger("athena")


@dataclass
class ForexScoreResult:
    final_score: float = 0.0
    direction: str = "LONG"          # "LONG" or "SHORT"
    signal_type: str = "NONE"        # "TREND_PULLBACK", "LONDON_BREAKOUT", "NONE"
    trend_gate: bool = False         # D1/H4 EMA alignment confirmed
    session_active: bool = False     # In London or NY session
    entry_quality: float = 0.0       # RSI pullback quality 0-1
    cot_boost: float = 0.0           # COT confirmation 0-1
    breakout_score: float = 0.0      # London breakout quality 0-1
    components: dict = field(default_factory=dict)


# ─── Session windows (UTC hours, inclusive) ─────────────────────────────────

_LONDON_OPEN  = (7, 11)   # 07:00–11:00 UTC
_NY_OPEN      = (12, 16)  # 12:00–16:00 UTC


def _in_session(utc_hour: int) -> bool:
    """True if current hour is inside London open or NY open window."""
    return (_LONDON_OPEN[0] <= utc_hour <= _LONDON_OPEN[1] or
            _NY_OPEN[0]     <= utc_hour <= _NY_OPEN[1])


# ─── Trend gate ──────────────────────────────────────────────────────────────

def _check_trend_gate(d1_snap: dict, h4_snap: dict) -> tuple[bool, str]:
    """
    D1 and H4 EMA alignment check.
    LONG: D1 close > D1 ema200 AND H4 ema50 > H4 ema200
    SHORT: D1 close < D1 ema200 AND H4 ema50 < H4 ema200
    Mixed = no trade.
    Also checks ema200Slope10 for trend health — flat ema200 reduces conviction.
    """
    d1_close  = d1_snap.get("close")
    d1_ema200 = d1_snap.get("ema200")
    h4_ema50  = h4_snap.get("ema50")
    h4_ema200 = h4_snap.get("ema200")

    d1_slope = d1_snap.get("ema200Slope10", 0)

    if None in (d1_close, d1_ema200, h4_ema50, h4_ema200):
        return False, "LONG"

    d1_bull = d1_close > d1_ema200
    h4_bull = h4_ema50 > h4_ema200

    if d1_bull and h4_bull:
        margin = (d1_close - d1_ema200) / d1_ema200
        if margin > 0.001 or d1_slope > 0:
            return True, "LONG"
        return False, "LONG"

    if not d1_bull and not h4_bull:
        margin = (d1_ema200 - d1_close) / d1_ema200
        if margin > 0.001 or d1_slope < 0:
            return True, "SHORT"
        return False, "SHORT"

    return False, "LONG"  # mixed


# ─── Entry quality (RSI pullback) ────────────────────────────────────────────

def _entry_quality(h1_snap: dict, direction: str) -> float:
    """
    Score the H1 RSI pullback depth.
    LONG: RSI between 35-55 = good pullback (1.0), 55-65 = ok (0.5)
    SHORT: RSI between 45-65 = good pullback (1.0), 35-45 = ok (0.5)
    Outside these zones = 0.0 (too extended or too deep)
    """
    rsi = h1_snap.get("rsi") or h1_snap.get("rsi14")
    if rsi is None:
        return 0.3  # neutral if no RSI data

    if direction == "LONG":
        if 35 <= rsi <= 55:
            return 1.0
        elif 55 < rsi <= 65:
            return 0.5
        elif rsi < 35:
            return 0.2  # too oversold
        else:
            return 0.0  # overbought
    else:  # SHORT
        if 45 <= rsi <= 65:
            return 1.0
        elif 35 <= rsi < 45:
            return 0.5
        elif rsi > 65:
            return 0.2
        else:
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

    if current_close > asian_high:
        breakout_pct = (current_close - asian_high) / asian_range
        return min(1.0, breakout_pct * 3), "LONG"

    if current_close < asian_low:
        breakout_pct = (asian_low - current_close) / asian_range
        return min(1.0, breakout_pct * 3), "SHORT"

    return 0.0, "LONG"


# ─── Main scoring function ───────────────────────────────────────────────────

def compute_forex_score(
    d1_snap:       dict,
    h4_snap:       dict,
    h1_snap:       dict,
    h1_candles:    list,
    pair:          dict,
    bar_time:      Optional[str] = None,
    backtest_mode: bool = False,
) -> ForexScoreResult:
    """
    Compute forex-specific confluence score.

    Scoring formula:
      TREND_PULLBACK signal:
        live:     trend_score = 0.4 + entry_quality * 0.4 + cot_boost * 0.2
        backtest: trend_score = 0.3 + entry_quality * 0.4 + cot_boost * 0.3
        (trend_gate is binary — if False, score = 0)

      LONDON_BREAKOUT signal (independent of trend gate):
        final_score = breakout_score * (1.0 + cot_boost * 0.3)

      Returns the higher of the two signal scores.
    """
    result = ForexScoreResult()

    utc_hour = datetime.now(timezone.utc).hour
    if bar_time:
        try:
            utc_hour = datetime.fromisoformat(bar_time).hour
        except Exception:
            pass

    # ── Signal 1: Trend pullback ──────────────────────────────────────────
    trend_ok, trend_dir = _check_trend_gate(d1_snap, h4_snap)
    # backtest_mode bypasses session filter — D1 bars are timestamped midnight UTC
    session_ok = True if backtest_mode else _in_session(utc_hour)

    trend_score = 0.0
    if trend_ok and session_ok:
        eq  = _entry_quality(h1_snap, trend_dir)
        cot = _cot_boost(pair, trend_dir, bar_time)

        if backtest_mode:
            # Plan spec: 0.3 + eq*0.4 + cot*0.3
            trend_score = 0.3 + eq * 0.4 + cot * 0.3
        else:
            # Plan spec: 0.4 + eq*0.4 + cot*0.2
            trend_score = 0.4 + eq * 0.4 + cot * 0.2

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

    # ── Pick best signal ──────────────────────────────────────────────────
    if trend_score >= bo_final:
        result.final_score = round(trend_score, 4)
        result.direction   = trend_dir
        result.signal_type = "TREND_PULLBACK" if trend_score > 0 else "NONE"
    else:
        result.final_score = round(bo_final, 4)
        result.direction   = bo_dir
        result.signal_type = "LONDON_BREAKOUT"

    result.components = {
        "trend_gate":     trend_ok,
        "session_active": session_ok,
        "utc_hour":       utc_hour,
        "entry_quality":  result.entry_quality,
        "cot_boost":      result.cot_boost,
        "breakout_score": bo_score,
        "trend_score":    round(trend_score, 4),
        "breakout_final": round(bo_final, 4),
    }

    return result
