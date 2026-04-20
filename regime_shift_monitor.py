"""
Regime Shift Monitor — read-only pullback classifier for open positions.

Runs alongside _check_score_decay() in the outcome monitor loop.
Classifies the current pullback on each open position as:
  - HEALTHY: clean retracement, trend likely to resume
  - FAKE:    reversal forming, decay alert is real
  - NEUTRAL: insufficient signal, use own judgement

Publishes advisory only. Never closes trades, never modifies signals,
never affects scoring. If classifier errors on a position, that
position is marked NEUTRAL and the existing decay display is unaffected.

Plugs into: athena.py _outcome_monitor_loop (same 50s cadence as decay).
Consumed by: /api/score-decay (merged field) and dashboard badge.
"""
from __future__ import annotations

import logging
from typing import Callable

from indicators import calc_cvd, calc_vwap, detect_absorption

log = logging.getLogger(__name__)

_DEFAULT_CFG = {
    "CHOCH_LOOKBACK_BARS": 20,
    "IMPULSE_LOOKBACK_BARS": 30,
    "CVD_FLIP_MIN_BARS": 3,
    "ABSORPTION_RECENT_BARS": 3,
    "MIN_CANDLES_REQUIRED": 30,
    "FAKE_WEIGHT_THRESHOLD": 3,
    "HEALTHY_FIB_LOW": 0.382,
    "HEALTHY_FIB_HIGH": 0.786,
}

_STYLE_TF = {
    "scalp": "M5",
    "intraday": "H1",
    "swing": "H4",
}


def _cfg(key: str, config: dict | None = None):
    if config and "REGIME_SHIFT_MONITOR" in config:
        ov = config["REGIME_SHIFT_MONITOR"]
        if isinstance(ov, dict) and key in ov:
            return ov[key]
    return _DEFAULT_CFG[key]


def _exec_tf_for(engine: str, style: str) -> str:
    """Map (engine, style) -> execution TF string used by fetch_candles."""
    engine_l = (engine or "").lower()
    style_l = (style or "").lower()
    if engine_l == "scalp" or style_l == "scalp":
        return "M5"
    if style_l == "swing":
        return "H4"
    return _STYLE_TF.get(style_l, "H1")


def _detect_choch_simple(candles: list, direction: str, lookback: int) -> bool:
    """Standalone CHoCH detector. Returns True if CHoCH against `direction`.

    LONG position: CHoCH bearish = recent close breaks the last swing low.
    SHORT position: CHoCH bullish = recent close breaks the last swing high.

    Uses simple 3-bar swing detection over the last `lookback` bars.
    Self-contained to avoid depending on private NakedEngine methods.
    """
    if not candles or len(candles) < 10:
        return False
    recent = candles[-lookback:]
    highs = [float(c.get("high", 0)) for c in recent]
    lows = [float(c.get("low", 0)) for c in recent]

    swing_highs = []
    swing_lows = []
    for i in range(1, len(recent) - 1):
        if highs[i] > highs[i - 1] and highs[i] > highs[i + 1]:
            swing_highs.append((i, highs[i]))
        if lows[i] < lows[i - 1] and lows[i] < lows[i + 1]:
            swing_lows.append((i, lows[i]))

    last_close = float(recent[-1].get("close", 0))
    direction_u = (direction or "").upper()

    if direction_u == "LONG" and len(swing_lows) >= 1:
        last_swing_low = swing_lows[-1][1]
        return last_close < last_swing_low
    if direction_u == "SHORT" and len(swing_highs) >= 1:
        last_swing_high = swing_highs[-1][1]
        return last_close > last_swing_high
    return False


def _detect_impulse_leg(candles: list, direction: str, lookback: int) -> tuple[float, float] | None:
    """Find the most recent impulse leg in trade direction.

    LONG: returns (leg_low, leg_high) — impulse up.
    SHORT: returns (leg_high, leg_low) — impulse down.
    None if no valid leg can be isolated.
    """
    if not candles or len(candles) < 10:
        return None
    window = candles[-lookback:]
    direction_u = (direction or "").upper()
    if direction_u == "LONG":
        lo = min(float(c.get("low", 0)) for c in window)
        lo_idx = next((i for i, c in enumerate(window) if float(c.get("low", 0)) == lo), 0)
        after = window[lo_idx:]
        if not after:
            return None
        hi = max(float(c.get("high", 0)) for c in after)
        if hi <= lo:
            return None
        return (lo, hi)
    else:  # SHORT
        hi = max(float(c.get("high", 0)) for c in window)
        hi_idx = next((i for i, c in enumerate(window) if float(c.get("high", 0)) == hi), 0)
        after = window[hi_idx:]
        if not after:
            return None
        lo = min(float(c.get("low", 0)) for c in after)
        if lo >= hi:
            return None
        return (hi, lo)


def _fib_zone_status(
    impulse: tuple[float, float],
    current_price: float,
    direction: str,
    cfg: dict | None,
) -> str:
    """Return 'inside' (healthy retrace zone), 'past_ceiling' (reversal
    territory), or 'before_zone' (too shallow)."""
    lo_ret = _cfg("HEALTHY_FIB_LOW", cfg)
    hi_ret = _cfg("HEALTHY_FIB_HIGH", cfg)
    direction_u = (direction or "").upper()

    if direction_u == "LONG":
        leg_low, leg_high = impulse
        leg = leg_high - leg_low
        if leg <= 0:
            return "before_zone"
        retrace_pct = (leg_high - current_price) / leg
    else:
        leg_high, leg_low = impulse
        leg = leg_high - leg_low
        if leg <= 0:
            return "before_zone"
        retrace_pct = (current_price - leg_low) / leg

    if retrace_pct < lo_ret:
        return "before_zone"
    if retrace_pct > hi_ret:
        return "past_ceiling"
    return "inside"


def _cvd_flipped(candles: list, direction: str, min_bars: int) -> bool:
    """True if smoothed CVD delta is against position direction for last min_bars."""
    try:
        cvd_result = calc_cvd(candles, smooth_period=5)
        smoothed = cvd_result.get("smoothed_delta", [])
        if len(smoothed) < min_bars:
            return False
        last_n = [float(x) for x in smoothed[-min_bars:]]
        direction_u = (direction or "").upper()
        if direction_u == "LONG":
            return all(x < 0 for x in last_n)
        if direction_u == "SHORT":
            return all(x > 0 for x in last_n)
    except Exception as _err:
        log.debug(f"[REGIME-SHIFT] cvd_flipped error: {_err}")
    return False


def _absorption_at_pullback(candles: list, direction: str, recent_bars: int) -> bool:
    """True if absorption detected in the last `recent_bars` AGAINST position direction.

    LONG: bearish absorption (sellers defending) in the recent window.
    SHORT: bullish absorption (buyers defending) in the recent window.

    ``indicators.detect_absorption`` returns a per-candle list of
    ``{absorbed, vol_ratio, move_ratio, direction}`` aligned 1:1 with ``candles``,
    so we slice the tail directly.
    """
    try:
        results = detect_absorption(candles)
        if not results:
            return False
        tail = results[-recent_bars:] if recent_bars > 0 else []
        direction_u = (direction or "").upper()
        for ev in tail:
            if not isinstance(ev, dict) or not ev.get("absorbed"):
                continue
            ev_dir = str(ev.get("direction", "")).lower()
            if direction_u == "LONG" and "bear" in ev_dir:
                return True
            if direction_u == "SHORT" and "bull" in ev_dir:
                return True
    except Exception as _err:
        log.debug(f"[REGIME-SHIFT] absorption check error: {_err}")
    return False


def _vwap_flipped(candles: list, direction: str) -> bool:
    """True if price closed on the wrong side of VWAP for the current bar."""
    try:
        vwap_data = calc_vwap(candles)
        vwap_vals = vwap_data.get("vwap", [])
        if not vwap_vals:
            return False
        last_vwap = None
        for v in reversed(vwap_vals):
            if v is not None:
                last_vwap = float(v)
                break
        if last_vwap is None:
            return False
        last_close = float(candles[-1].get("close", 0))
        direction_u = (direction or "").upper()
        if direction_u == "LONG":
            return last_close < last_vwap
        if direction_u == "SHORT":
            return last_close > last_vwap
    except Exception as _err:
        log.debug(f"[REGIME-SHIFT] vwap check error: {_err}")
    return False


def _neutral_result(tf: str, reason: str) -> dict:
    return {
        "classification": "NEUTRAL",
        "confidence": 0.0,
        "signals_triggered": [],
        "advisory": f"Classifier unavailable: {reason}",
        "tf_used": tf,
        "weight": 0,
        "fib_status": None,
    }


def classify_position(
    pair_display: str,
    direction: str,
    engine: str,
    style: str,
    fetch_candles_fn: Callable[[str, int], list | None],
    config: dict | None = None,
) -> dict:
    """Main entry point. Classifier is fully read-only.

    Args:
        pair_display: pair display name (e.g. "EUR/USD") — logging only
        direction: "LONG" or "SHORT"
        engine: "engine_a" | "engine_b" | "scalp"
        style: "scalp" | "intraday" | "swing"
        fetch_candles_fn: callable(tf, limit) -> list of candle dicts or None
        config: optional CONFIG dict for overrides

    Returns:
        {
          "classification": "HEALTHY" | "FAKE" | "NEUTRAL",
          "confidence": float 0.0-1.0,
          "signals_triggered": list[str],
          "advisory": str,
          "tf_used": str,
          "weight": int,
          "fib_status": str | None,
        }
    """
    exec_tf = _exec_tf_for(engine, style)
    min_bars = _cfg("MIN_CANDLES_REQUIRED", config)
    fetch_limit = max(min_bars + 20, 100)

    try:
        candles = fetch_candles_fn(exec_tf, fetch_limit)
    except Exception as _fe:
        log.debug(f"[REGIME-SHIFT] {pair_display} fetch failed: {_fe}")
        return _neutral_result(exec_tf, "fetch_failed")

    if not candles or len(candles) < min_bars:
        return _neutral_result(exec_tf, "insufficient_bars")

    direction_u = (direction or "").upper()
    if direction_u not in ("LONG", "SHORT"):
        return _neutral_result(exec_tf, "invalid_direction")

    current_price = float(candles[-1].get("close", 0))
    if current_price <= 0:
        return _neutral_result(exec_tf, "invalid_price")

    signals = []
    weight = 0

    # Signal 1 — CHoCH against position (weight 2)
    try:
        choch = _detect_choch_simple(candles, direction_u, _cfg("CHOCH_LOOKBACK_BARS", config))
        if choch:
            signals.append("choch_against")
            weight += 2
    except Exception as _e:
        log.debug(f"[REGIME-SHIFT] {pair_display} choch error: {_e}")

    # Signal 2 — CVD flipped (weight 2)
    if _cvd_flipped(candles, direction_u, _cfg("CVD_FLIP_MIN_BARS", config)):
        signals.append("cvd_flipped")
        weight += 2

    # Signal 3 — past 78.6% Fib ceiling (weight 2)
    impulse = _detect_impulse_leg(candles, direction_u, _cfg("IMPULSE_LOOKBACK_BARS", config))
    fib_status = None
    if impulse:
        fib_status = _fib_zone_status(impulse, current_price, direction_u, config)
        if fib_status == "past_ceiling":
            signals.append("past_fib_ceiling")
            weight += 2

    # Signal 4 — absorption at pullback extreme (weight 1)
    if _absorption_at_pullback(candles, direction_u, _cfg("ABSORPTION_RECENT_BARS", config)):
        signals.append("absorption_against")
        weight += 1

    # Signal 5 — VWAP flipped (weight 1)
    if _vwap_flipped(candles, direction_u):
        signals.append("vwap_flipped")
        weight += 1

    fake_threshold = _cfg("FAKE_WEIGHT_THRESHOLD", config)

    if weight >= fake_threshold or ("choch_against" in signals and len(signals) >= 2):
        classification = "FAKE"
        advisory = "Reversal forming — decay alert is real"
        confidence = min(1.0, weight / 5.0)
    elif weight == 0 and fib_status == "inside":
        classification = "HEALTHY"
        advisory = "Pullback is a healthy retracement — consider holding"
        confidence = 0.8
    else:
        classification = "NEUTRAL"
        advisory = "Mixed signals — use own judgement"
        confidence = 0.3 + (weight * 0.1)

    return {
        "classification": classification,
        "confidence": round(confidence, 2),
        "signals_triggered": signals,
        "advisory": advisory,
        "tf_used": exec_tf,
        "weight": weight,
        "fib_status": fib_status,
    }
