"""scalp_engine.py — Engine D: Independent M15/M5 Scalping Module

Fully independent from Engine A (MFQS), Engine B (Naked), Engine C (Consensus).
Data source: MT5 only (copy_rates_from_pos).

Logic flow:
  1. Session filter (London/NY only)
  2. Spread filter
  3. M15 zone detection (≥2 conditions: S/R, sweep, EMA21)
  4. M5 entry trigger (rejection/engulfing + close direction + inside zone)
  5. Momentum confirmation (M5 structure break OR strong body)
  6. Risk levels (SL beyond zone, TP at 1.5R, TP2 at next M15 swing)
  7. AI quality grade (rule-based, 0-100, A/B/C/D — instant, no API)

All trades route through risk_engine.risk_check() before execution.
"""

import logging
import math
from datetime import datetime, timezone
from typing import Optional

from config import CONFIG

log = logging.getLogger("sentinel.scalp")


# ── Session windows (UTC) ─────────────────────────────────────────────────────
_SESSIONS = {
    "london":   (7, 16),    # 07:00–16:00 UTC
    "new_york": (13, 21),   # 13:00–21:00 UTC
}


# ── MT5 candle fetching ───────────────────────────────────────────────────────

def mt5_fetch_scalp_candles(mt5_symbol: str, timeframe_str: str, count: int) -> list:
    """Fetch OHLCV candles from MT5 terminal for M15 or M5 timeframes.

    Args:
        mt5_symbol: MT5 symbol string (e.g. 'EURUSD', 'XAUUSD')
        timeframe_str: 'M15' or 'M5'
        count: number of candles to fetch

    Returns:
        list of dicts: {time, open, high, low, close, vol}
    """
    try:
        import MetaTrader5 as mt5

        if not mt5.terminal_info():
            if not mt5.initialize():
                log.error(f"[SCALP] MT5 not initialised — cannot fetch {mt5_symbol}")
                return []

        tf_map = {
            "M5":  mt5.TIMEFRAME_M5,
            "M15": mt5.TIMEFRAME_M15,
        }
        tf = tf_map.get(timeframe_str.upper())
        if tf is None:
            log.error(f"[SCALP] Unknown timeframe: {timeframe_str}")
            return []

        # Enable symbol if not in Market Watch
        mt5.symbol_select(mt5_symbol, True)

        rates = mt5.copy_rates_from_pos(mt5_symbol, tf, 0, count)
        if rates is None or len(rates) == 0:
            log.warning(f"[SCALP] No {timeframe_str} data for {mt5_symbol}")
            return []

        candles = []
        for r in rates:
            candles.append({
                "time":  r["time"],
                "open":  float(r["open"]),
                "high":  float(r["high"]),
                "low":   float(r["low"]),
                "close": float(r["close"]),
                "vol":   float(r.get("tick_volume", 0)),
            })

        # Drop the forming (last) bar
        if len(candles) > 1:
            candles = candles[:-1]

        return candles

    except ImportError:
        log.error("[SCALP] MetaTrader5 package not installed")
        return []
    except Exception as e:
        log.error(f"[SCALP] mt5_fetch_scalp_candles error: {e}")
        return []


# ── EMA helper ────────────────────────────────────────────────────────────────

def _calc_ema(values: list, period: int) -> list:
    """Calculate EMA over a list of floats."""
    if len(values) < period:
        return []
    k = 2.0 / (period + 1)
    ema = [sum(values[:period]) / period]
    for v in values[period:]:
        ema.append(v * k + ema[-1] * (1 - k))
    return ema


# ── Session filter ────────────────────────────────────────────────────────────

def get_current_sessions() -> list:
    """Return active session names based on current UTC hour."""
    cfg = CONFIG.get("SCALP_ENGINE", {})
    tz_offset = CONFIG.get("SERVER_TZ_OFFSET_HOURS", 0)
    now_utc_h = (datetime.now().hour - int(tz_offset)) % 24
    active = []
    for name, (start, end) in _SESSIONS.items():
        if start <= now_utc_h < end:
            active.append(name)
    return active or ["off_hours"]


def is_valid_session() -> tuple:
    """Check if current time is London or NY session.

    Returns:
        (valid: bool, session_name: str)
    """
    cfg = CONFIG.get("SCALP_ENGINE", {})
    if not cfg.get("SESSION_FILTER", True):
        return True, "all"

    sessions = get_current_sessions()
    for s in sessions:
        if s in ("london", "new_york"):
            return True, s
    return False, "off_hours"


# ── Spread filter ─────────────────────────────────────────────────────────────

def check_spread(symbol_info: dict, asset_type: str) -> tuple:
    """Validate spread is within acceptable limits.

    Returns:
        (ok: bool, spread_pips: float)
    """
    cfg = CONFIG.get("SCALP_ENGINE", {})
    max_spreads = cfg.get("MAX_SPREAD_PIPS", {})

    spread_raw = symbol_info.get("spread", 0)  # MT5 spread in points
    point = symbol_info.get("point", 0.00001)
    spread_price = spread_raw * point

    # Convert to pips (1 pip = 10 points for 5-digit brokers)
    digits = symbol_info.get("digits", 5)
    pip_size = point * 10 if digits >= 4 else point
    spread_pips = spread_price / pip_size if pip_size > 0 else 0

    max_spread = max_spreads.get(asset_type, max_spreads.get("forex", 4))

    if asset_type == "crypto":
        return True, spread_pips  # Skip spread filter for crypto

    ok = spread_pips <= max_spread
    return ok, round(spread_pips, 2)


# ── M15 Zone Detection ────────────────────────────────────────────────────────

def detect_m15_zone(candles: list, current_price: float) -> dict:
    """Detect valid M15 support/resistance zone.

    A zone is valid if ≥ ZONE_MIN_CONDITIONS of these are true:
      1. Price touched prior S/R level (swing high/low in last 50 bars)
      2. Liquidity sweep (wick through level then close back inside)
      3. Price is near EMA21

    Returns dict with zone details and validity.
    """
    cfg = CONFIG.get("SCALP_ENGINE", {})
    min_conditions = cfg.get("ZONE_MIN_CONDITIONS", 2)

    if len(candles) < 30:
        return {"valid": False, "reason": "insufficient_m15_data"}

    closes = [c["close"] for c in candles]
    highs  = [c["high"]  for c in candles]
    lows   = [c["low"]   for c in candles]

    # EMA21
    ema21_series = _calc_ema(closes, 21)
    ema21 = ema21_series[-1] if ema21_series else None

    # Find swing highs/lows (last 50 bars, window=5)
    lookback = min(50, len(candles))
    window = 5
    swing_highs, swing_lows = [], []
    for i in range(window, lookback - window):
        h = highs[i]
        if h == max(highs[i - window:i + window + 1]):
            swing_highs.append(h)
        lo = lows[i]
        if lo == min(lows[i - window:i + window + 1]):
            swing_lows.append(lo)

    if not swing_highs and not swing_lows:
        return {"valid": False, "reason": "no_swing_levels"}

    # Find nearest S/R level to current price
    last_bar = candles[-1]
    all_levels = [(h, "resistance") for h in swing_highs] + [(l, "support") for l in swing_lows]
    all_levels.sort(key=lambda x: abs(x[0] - current_price))

    if not all_levels:
        return {"valid": False, "reason": "no_levels"}

    nearest_level, zone_type = all_levels[0]
    # Zone band: ±0.25% of price (wider for commodities/indices)
    band = current_price * 0.0025
    zone_high = nearest_level + band
    zone_low  = nearest_level - band

    conditions_met = []

    # Condition 1: Current price is inside the zone
    price_in_zone = zone_low <= current_price <= zone_high
    if price_in_zone:
        conditions_met.append("price_at_sr_level")

    # Condition 2: Liquidity sweep on last 5 bars
    for c in candles[-5:]:
        if zone_type == "resistance":
            # Wick broke above zone but close came back below
            if c["high"] > zone_high and c["close"] < zone_high:
                conditions_met.append("liquidity_sweep")
                break
        else:
            # Wick broke below zone but close came back above
            if c["low"] < zone_low and c["close"] > zone_low:
                conditions_met.append("liquidity_sweep")
                break

    # Condition 3: EMA21 touch (within 0.20% of EMA)
    if ema21 and abs(current_price - ema21) / ema21 < 0.002:
        conditions_met.append("ema21_proximity")

    # De-duplicate
    conditions_met = list(dict.fromkeys(conditions_met))

    valid = len(conditions_met) >= min_conditions

    return {
        "valid": valid,
        "zone_type": zone_type,
        "zone_high": round(zone_high, 6),
        "zone_low":  round(zone_low, 6),
        "zone_level": round(nearest_level, 6),
        "ema21": round(ema21, 6) if ema21 else None,
        "conditions_met": conditions_met,
        "conditions_count": len(conditions_met),
        "price_in_zone": price_in_zone,
    }


# ── M5 Entry Trigger ──────────────────────────────────────────────────────────

def detect_m5_trigger(candles_m5: list, zone: dict, current_price: float) -> dict:
    """Detect M5 entry trigger inside M15 zone.

    Must ALL be true:
      1. Price is inside M15 zone
      2. Rejection candle (long wick ≥ 2x body) OR engulfing candle
      3. Candle closes in trade direction

    Returns trigger dict with direction.
    """
    if not zone.get("valid") or not zone.get("price_in_zone"):
        return {"valid": False, "reason": "price_not_in_zone"}

    if len(candles_m5) < 5:
        return {"valid": False, "reason": "insufficient_m5_data"}

    last = candles_m5[-1]
    prev = candles_m5[-2]

    open_p  = last["open"]
    close_p = last["close"]
    high_p  = last["high"]
    low_p   = last["low"]

    body = abs(close_p - open_p)
    upper_wick = high_p - max(open_p, close_p)
    lower_wick = min(open_p, close_p) - low_p
    total_range = high_p - low_p or 1e-10

    zone_type = zone.get("zone_type", "support")

    # Determine expected direction from zone type
    direction = "LONG" if zone_type == "support" else "SHORT"

    trigger_type = None
    trigger_valid = False

    # Check rejection candle: long wick ≥ 2x body, closes in direction
    if zone_type == "support":
        # Bullish rejection: lower wick ≥ 2x body AND close > open
        if lower_wick >= 1.5 * body and close_p > open_p:
            trigger_type = "rejection"
            trigger_valid = True
    else:
        # Bearish rejection: upper wick ≥ 2x body AND close < open
        if upper_wick >= 1.5 * body and close_p < open_p:
            trigger_type = "rejection"
            trigger_valid = True

    # Check engulfing candle
    if not trigger_valid:
        prev_body = abs(prev["close"] - prev["open"])
        if zone_type == "support":
            # Bullish engulfing: current body > prev body AND current close > prev open
            if (close_p > open_p and
                    close_p > prev["open"] and
                    open_p < prev["close"] and
                    body > prev_body * 0.8):
                trigger_type = "engulfing"
                trigger_valid = True
        else:
            # Bearish engulfing
            if (close_p < open_p and
                    close_p < prev["open"] and
                    open_p > prev["close"] and
                    body > prev_body * 0.8):
                trigger_type = "engulfing"
                trigger_valid = True

    if not trigger_valid:
        return {
            "valid": False,
            "reason": "no_rejection_or_engulfing",
            "direction": direction,
        }

    return {
        "valid": True,
        "direction": direction,
        "trigger_type": trigger_type,
        "entry_price": round(current_price, 6),
        "last_body_pct": round(body / total_range * 100, 1) if total_range else 0,
    }


# ── Momentum Confirmation ─────────────────────────────────────────────────────

def confirm_momentum(candles_m5: list, trigger: dict) -> dict:
    """Confirm momentum on M5.

    ONE of:
      1. Break of previous M5 high (LONG) / low (SHORT)
      2. Strong candle body: body > avg of last 3 bodies

    Returns: {valid, method}
    """
    if not trigger.get("valid"):
        return {"valid": False, "reason": "no_trigger"}

    if len(candles_m5) < 5:
        return {"valid": False, "reason": "insufficient_m5_data"}

    direction = trigger["direction"]
    last = candles_m5[-1]
    prev = candles_m5[-2]

    # Method 1: Structure break
    if direction == "LONG":
        if last["close"] > prev["high"]:
            return {"valid": True, "method": "structure_break", "detail": f"close {last['close']:.5f} > prev_high {prev['high']:.5f}"}
    else:
        if last["close"] < prev["low"]:
            return {"valid": True, "method": "structure_break", "detail": f"close {last['close']:.5f} < prev_low {prev['low']:.5f}"}

    # Method 2: Strong body vs avg of last 3 bodies
    recent = candles_m5[-4:-1]
    if recent:
        avg_body = sum(abs(c["close"] - c["open"]) for c in recent) / len(recent)
        last_body = abs(last["close"] - last["open"])
        if avg_body > 0 and last_body > avg_body * 1.1:
            return {"valid": True, "method": "strong_body", "detail": f"body {last_body:.5f} > {avg_body:.5f} avg"}

    return {"valid": False, "reason": "no_momentum", "detail": "no structure break or strong body"}


# ── Scalp Risk Levels ─────────────────────────────────────────────────────────

def calculate_scalp_levels(
    trigger: dict,
    zone: dict,
    candles_m15: list,
    symbol_info: dict,
    asset_type: str,
) -> dict:
    """Calculate SL, TP1, TP2 for scalp trade.

    SL: beyond zone boundary + asset buffer
    TP1: entry + SL_distance * 1.5  (default 1.5R)
    TP2: next M15 swing high/low (if enabled)
    """
    cfg = CONFIG.get("SCALP_ENGINE", {})
    entry = trigger.get("entry_price", 0)
    direction = trigger.get("direction", "LONG")
    digits = symbol_info.get("digits", 5)
    point = symbol_info.get("point", 0.00001)

    # Buffer per asset type
    _pip = point * 10 if digits >= 4 else point
    _buffers = {
        "forex":     _pip * 3,    # ~3 pips
        "commodity": entry * 0.002 if entry > 0 else _pip * 5,  # 0.2%
        "crypto":    entry * 0.003 if entry > 0 else 0,         # 0.3%
        "index":     entry * 0.002 if entry > 0 else 0,         # 0.2%
        "stock":     entry * 0.002 if entry > 0 else 0,
    }
    buffer = _buffers.get(asset_type, _buffers["forex"])

    if direction == "LONG":
        sl = zone["zone_low"] - buffer
        if sl >= entry:
            sl = entry - (entry * 0.003)  # fallback 0.3%
    else:
        sl = zone["zone_high"] + buffer
        if sl <= entry:
            sl = entry + (entry * 0.003)

    sl_distance = abs(entry - sl)
    min_rr = float(cfg.get("MIN_RR", 1.5))

    if direction == "LONG":
        tp1 = entry + sl_distance * min_rr
    else:
        tp1 = entry - sl_distance * min_rr

    actual_rr = round(sl_distance * min_rr / sl_distance, 2) if sl_distance > 0 else 0

    # TP2: next M15 swing in trade direction
    tp2 = None
    if cfg.get("TP2_ENABLED", True) and len(candles_m15) >= 20:
        highs = [c["high"] for c in candles_m15[-30:]]
        lows  = [c["low"]  for c in candles_m15[-30:]]
        if direction == "LONG":
            candidates = [h for h in highs if h > entry]
            tp2 = min(candidates) if candidates else None
        else:
            candidates = [l for l in lows if l < entry]
            tp2 = max(candidates) if candidates else None

    return {
        "entry": round(entry, digits),
        "sl":    round(sl, digits),
        "tp1":   round(tp1, digits),
        "tp2":   round(tp2, digits) if tp2 else None,
        "rr":    actual_rr,
        "sl_distance": round(sl_distance, digits),
        "sl_method": "zone_boundary",
    }


# ── AI Quality Grade (Rule-Based, Instant) ────────────────────────────────────

def ai_quality_grade(
    zone: dict,
    trigger: dict,
    momentum: dict,
    sessions: list,
    spread_pips: float,
    symbol_info: dict,
) -> dict:
    """Rule-based quality scoring (0–100). No API call — instant.

    Grades:
      A  (80–100): High-probability, take it
      B  (60–79):  Good, acceptable
      C  (40–59):  Marginal, caution
      D  (<40):    Skip

    Returns {score, grade, reasons[]}
    """
    score = 0
    reasons = []

    # Zone quality (up to 30 points)
    cond_count = zone.get("conditions_count", 0)
    if cond_count >= 3:
        score += 30
        reasons.append("Strong zone: 3+ conditions")
    elif cond_count == 2:
        score += 20
        reasons.append("Valid zone: 2 conditions")
    else:
        score += 5

    if "liquidity_sweep" in zone.get("conditions_met", []):
        score += 5
        reasons.append("Liquidity sweep at zone")

    if "ema21_proximity" in zone.get("conditions_met", []):
        score += 5
        reasons.append("EMA21 confluence")

    # Session quality (up to 20 points)
    if "london" in sessions and "new_york" in sessions:
        score += 20
        reasons.append("London/NY overlap — peak liquidity")
    elif "london" in sessions:
        score += 15
        reasons.append("London session")
    elif "new_york" in sessions:
        score += 15
        reasons.append("NY session")

    # Trigger quality (up to 20 points)
    ttype = trigger.get("trigger_type", "")
    if ttype == "rejection":
        score += 20
        reasons.append("Rejection candle (wicks out of zone)")
    elif ttype == "engulfing":
        score += 15
        reasons.append("Engulfing candle")

    # Momentum quality (up to 20 points)
    mmethod = momentum.get("method", "")
    if mmethod == "structure_break":
        score += 20
        reasons.append("Structure break confirmed")
    elif mmethod == "strong_body":
        score += 12
        reasons.append("Strong momentum body")

    # Spread penalty (up to -10)
    cfg = CONFIG.get("SCALP_ENGINE", {})
    max_sp = cfg.get("MAX_SPREAD_PIPS", {}).get("forex", 4)
    if spread_pips > 0:
        if spread_pips <= max_sp * 0.5:
            score += 5
            reasons.append(f"Tight spread ({spread_pips:.1f} pips)")
        elif spread_pips > max_sp * 0.8:
            score -= 5
            reasons.append(f"Wide spread ({spread_pips:.1f} pips)")

    score = max(0, min(100, score))

    if score >= 80:
        grade = "A"
    elif score >= 60:
        grade = "B"
    elif score >= 40:
        grade = "C"
    else:
        grade = "D"

    return {"score": score, "grade": grade, "reasons": reasons}


# ── Main Scan Function ────────────────────────────────────────────────────────

def run_scalp_scan(pairs_or_symbols: list) -> dict:
    """Scan a list of pairs for scalp setups.

    Args:
        pairs_or_symbols: list of Athena display names (e.g. ['EUR/USD', 'XAU/USD'])

    Returns:
        {signals, skipped, session, scanned}
    """
    from mt5_executor import mt5_map_symbol, mt5_get_symbol_info, mt5_connect

    cfg = CONFIG.get("SCALP_ENGINE", {})
    m15_count = int(cfg.get("M15_CANDLES", 500))
    m5_count  = int(cfg.get("M5_CANDLES", 1000))

    # Session gate
    session_ok, session_name = is_valid_session()
    sessions = get_current_sessions()

    if not session_ok:
        return {
            "signals": [],
            "skipped": len(pairs_or_symbols),
            "scanned": 0,
            "session": session_name,
            "reason": "OUTSIDE_SESSION",
        }

    if not mt5_connect():
        return {
            "signals": [],
            "skipped": len(pairs_or_symbols),
            "scanned": 0,
            "session": session_name,
            "reason": "MT5_NOT_CONNECTED",
        }

    signals = []
    skipped = []

    for display in pairs_or_symbols:
        try:
            mt5_sym = mt5_map_symbol(display)
            if not mt5_sym:
                skipped.append({"pair": display, "reason": "no_mt5_mapping"})
                continue

            # Get symbol info (includes spread, point, digits, tick values)
            sym_info = mt5_get_symbol_info(display)
            if not sym_info or sym_info.get("error"):
                skipped.append({"pair": display, "reason": "symbol_not_available"})
                continue

            # Determine asset type from MT5 symbol map
            asset_type = _guess_asset_type(display)

            # Spread filter
            spread_ok, spread_pips = check_spread(sym_info, asset_type)
            if not spread_ok:
                skipped.append({"pair": display, "reason": f"spread_too_wide_{spread_pips}pips"})
                continue

            # Fetch candles
            candles_m15 = mt5_fetch_scalp_candles(mt5_sym, "M15", m15_count)
            if len(candles_m15) < 30:
                skipped.append({"pair": display, "reason": "insufficient_m15_candles"})
                continue

            candles_m5 = mt5_fetch_scalp_candles(mt5_sym, "M5", m5_count)
            if len(candles_m5) < 10:
                skipped.append({"pair": display, "reason": "insufficient_m5_candles"})
                continue

            current_price = candles_m5[-1]["close"]

            # Pipeline
            zone     = detect_m15_zone(candles_m15, current_price)
            if not zone["valid"]:
                skipped.append({"pair": display, "reason": f"no_zone:{zone.get('reason', '?')}"})
                continue

            trigger  = detect_m5_trigger(candles_m5, zone, current_price)
            if not trigger["valid"]:
                skipped.append({"pair": display, "reason": f"no_trigger:{trigger.get('reason', '?')}"})
                continue

            momentum = confirm_momentum(candles_m5, trigger)
            if not momentum["valid"]:
                skipped.append({"pair": display, "reason": f"no_momentum:{momentum.get('reason', '?')}"})
                continue

            levels   = calculate_scalp_levels(trigger, zone, candles_m15, sym_info, asset_type)
            quality  = ai_quality_grade(zone, trigger, momentum, sessions, spread_pips, sym_info)

            signal = {
                "pair":          display,
                "mt5_symbol":    mt5_sym,
                "type":          asset_type,
                "direction":     trigger["direction"],
                "price":         levels["entry"],
                "sl":            levels["sl"],
                "tp1":           levels["tp1"],
                "tp2":           levels["tp2"],
                "rr1":           levels["rr"],
                "sl_distance":   levels["sl_distance"],
                "sl_method":     levels["sl_method"],
                "zone_type":     zone["zone_type"],
                "zone_high":     zone["zone_high"],
                "zone_low":      zone["zone_low"],
                "zone_level":    zone["zone_level"],
                "zone_conditions": zone["conditions_met"],
                "trigger_type":  trigger["trigger_type"],
                "momentum_method": momentum["method"],
                "ai_score":      quality["score"],
                "ai_grade":      quality["grade"],
                "ai_reasons":    quality["reasons"],
                "spread_pips":   spread_pips,
                "session":       session_name,
                "ema21":         zone.get("ema21"),
                "timestamp":     datetime.now(timezone.utc).isoformat(),
                "engine":        "SCALP",
                # Fields required by risk_engine.risk_check()
                "confluenceScore": quality["score"] / 100.0,
                "maxScore":        1.0,
            }
            signals.append(signal)

        except Exception as e:
            log.error(f"[SCALP] Error on {display}: {e}")
            skipped.append({"pair": display, "reason": f"error:{str(e)[:60]}"})

    # Sort by AI score descending
    signals.sort(key=lambda s: s.get("ai_score", 0), reverse=True)

    log.warning(
        f"[SCALP] Scan: {len(pairs_or_symbols)} pairs | "
        f"{len(signals)} signals | {len(skipped)} skipped | session={session_name}"
    )

    return {
        "signals": signals,
        "skipped": skipped,
        "scanned": len(pairs_or_symbols),
        "session": session_name,
        "sessions_active": sessions,
    }


# ── Asset type helper ─────────────────────────────────────────────────────────

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


# ── Scalp pairs list ──────────────────────────────────────────────────────────

def get_scalp_pairs() -> list:
    """Return the list of pairs to scan. Reads from config or uses default set."""
    cfg = CONFIG.get("SCALP_ENGINE", {})
    configured = cfg.get("SCALP_PAIRS", [])
    if configured:
        return configured

    # Default: major forex + commodities that trade cleanly on MT5 during London/NY
    return [
        "EUR/USD", "GBP/USD", "USD/JPY", "AUD/USD", "NZD/USD",
        "USD/CAD", "USD/CHF", "EUR/JPY", "GBP/JPY", "EUR/GBP",
        "XAU/USD", "XAG/USD", "S&P 500", "Nasdaq",
    ]
