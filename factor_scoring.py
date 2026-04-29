"""factor_scoring.py — Engine A v2: 3-Factor Quantitative Scoring Engine.

Architecture (research-validated, 2026-04):
  Factor 1 — TREND       : Multi-TF EMA alignment (D1/H4/H1). Determines direction ONLY.
  Factor 2 — MOMENTUM    : RSI + MACD confirmation quality (0-1 scale). Sizes conviction.
  Factor 3 — ADX GATE    : Trend-strength filter. Hard abort < 15; soft penalty 15-25; full >= 25.
  Addon    — ASSET ADDON : One optional secondary per class (forex=carry, crypto=funding,
                           commodity=COT). Graceful 0.0 when data unavailable — no phantom signal.

Design principles:
  - Direction from TREND factor only — no other factor can flip direction.
  - 3 factors max — validated by Harvey et al. (2016), Asness et al. (2012), AQR (2017).
  - No variance restorer (sqrt(n)) — that rewarded factor quantity, not quality.
  - No stacked regime/adaptive weight layers — single clean pass.
  - Forex routes here (NOT forex_scoring.py) — unified engine with forex-appropriate params.
  - Missing addon data = 0.0 (not phantom signal, not blocking).
  - Score scale 0-3.0 preserved for backward compat with Engine C / confluence gates.
"""

import math
import logging
import threading
from typing import Dict, List, Optional

from config import CONFIG
from regime import detect_regime

log = logging.getLogger("athena")

# ── Constants ─────────────────────────────────────────────────────────────────

_CRYPTO_COT_PAIRS = {"BTC/USDT", "ETH/USDT"}

# ADX gate thresholds (Wilder 1978 standard) — tunable via config.yaml FACTOR_ADX_*
# NOTE: Read lazily inside _adx_gate() so config reloads take effect without restart.
_ADX_HARD_FAIL_DEFAULT = 15.0    # below this → dead market, abort
_ADX_SOFT_MULT_DEFAULT = 0.65    # multiplier in soft zone

# Session multiplier defaults (forex only — read lazily in _session_multiplier)
_SESSION_CORE_MULT_DEFAULT = 1.00
_SESSION_SHOULDER_MULT_DEFAULT = 0.90
_SESSION_OFF_MULT_DEFAULT = 0.75

# Addon contribution bounds
_ADDON_CONFIRM = 0.30    # confirming the trend direction
_ADDON_NEUTRAL = 0.00    # data missing or neutral
_ADDON_AGAINST = -0.15   # actively opposing direction

# Final score formula weight defaults — read lazily inside compute_factor_scores
_MOMENTUM_WEIGHT_DEFAULT = 0.50
_ADDON_WEIGHT_DEFAULT = 0.30
_BASE_WEIGHT_DEFAULT = 0.20
_CONVICTION_FLOOR_DEFAULT = 0.60
_RESEARCH_BONUS_DEFAULT = 0.15
_RESEARCH_PENALTY_DEFAULT = -0.10


# ── Regime smoothing (kept for scan-level stability) ──────────────────────────

def make_regime_smoothing_context() -> dict:
    return {"history": {}, "committed": {}, "lock": threading.Lock()}


def _get_smoothed_regime(
    regime_context: Optional[dict], pair_id: str, raw_regime: str
) -> str:
    """Return smoothed regime: only switch if new regime holds for REGIME_SMOOTHING_BARS bars."""
    if regime_context is None:
        return raw_regime
    min_bars = CONFIG.get("REGIME_SMOOTHING_BARS", 3)
    history = regime_context.setdefault("history", {})
    committed_map = regime_context.setdefault("committed", {})
    lock = regime_context.setdefault("lock", threading.Lock())
    with lock:
        hist = history.setdefault(pair_id, [])
        committed = committed_map.get(pair_id)
        hist.append(raw_regime)
        if len(hist) > min_bars:
            hist[:] = hist[-min_bars:]
        if committed is None:
            committed_map[pair_id] = raw_regime
            return raw_regime
        if len(hist) >= min_bars and len(set(hist[-min_bars:])) == 1:
            committed_map[pair_id] = hist[-1]
        return committed_map[pair_id]


# ── Backward-compat stub (used by backtest_runner + scoring.py) ───────────────

def build_oi_context_for_factor_scoring(
    oi_data: Optional[dict],
    d1_candles: List,
    h1_snap: Optional[dict],
) -> Optional[dict]:
    """Build oi_context for crypto derivatives addon (parity with old engine)."""
    if not oi_data or oi_data.get("oiChange") is None:
        return None
    d1c = d1_candles or []
    if len(d1c) < 2:
        return None
    try:
        prev = float(d1c[-2]["close"])
        cur = (h1_snap or {}).get("close")
        if cur is None:
            cur = float(d1c[-1]["close"])
        else:
            cur = float(cur)
    except (TypeError, ValueError):
        return None
    if not prev:
        return None
    return {
        "oi_change_pct": float(oi_data["oiChange"]),
        "price_change_pct": (cur - prev) / prev * 100.0,
    }


# ── Factor 1: Trend (multi-TF EMA alignment) ─────────────────────────────────

def _coherent_trend_score(
    d1_snap: dict, h4_snap: dict, h1_snap: dict, asset_type: str
) -> tuple:
    """Multi-TF EMA alignment trend score.

    Returns (trend_score, direction, trend_detail).
    trend_score  : float in [-3.0, +3.0]; 0.0 means indeterminate.
    direction    : "LONG" | "SHORT" | None
    trend_detail : dict with per-TF votes and coherence ratio.

    D1 weight 0.50 (tide), H4 weight 0.30 (momentum), H1 weight 0.20 (entry).
    All 3 aligned → ±3.0; 2-of-3 dominant → ±(0.35+0.65*coherence)*3.0.
    All split (1.5 LONG / 1.5 SHORT with equal weights) → 0.0, direction=None.
    """
    tf_weights = CONFIG.get("INDICATOR_WEIGHTS", {}).get("trend", {})
    if isinstance(tf_weights, dict):
        raw = tf_weights.get(asset_type, tf_weights.get("default", {}))
        if isinstance(raw, dict):
            tf_weights = raw
        else:
            tf_weights = {}
    else:
        tf_weights = {}

    fallback = {"d1_ema_trend": 0.50, "h4_ema_trend": 0.30, "ema_trend": 0.20}

    def _w(key):
        try:
            return float(tf_weights.get(key, fallback.get(key, 0.0)))
        except (TypeError, ValueError):
            return fallback.get(key, 0.0)

    votes = []
    detail = {}

    # D1 — primary trend (EMA21 vs EMA200 for cleaner signal)
    d1_e21 = d1_snap.get("ema21")
    d1_e200 = d1_snap.get("ema200") or d1_snap.get("ema50")
    if d1_e21 is not None and d1_e200 is not None and d1_e200 != 0:
        sign = 1.0 if d1_e21 > d1_e200 else -1.0
        votes.append(("d1_ema_trend", sign, _w("d1_ema_trend")))
        detail["d1"] = "LONG" if sign > 0 else "SHORT"

    # H4 — momentum confirmation (EMA21 vs EMA50)
    h4_e21 = h4_snap.get("ema21")
    h4_e50 = h4_snap.get("ema50")
    if h4_e21 is not None and h4_e50 is not None and h4_e50 != 0:
        sign = 1.0 if h4_e21 > h4_e50 else -1.0
        votes.append(("h4_ema_trend", sign, _w("h4_ema_trend")))
        detail["h4"] = "LONG" if sign > 0 else "SHORT"

    # H1 — entry quality (EMA21 vs EMA50)
    h1_e21 = h1_snap.get("ema21")
    h1_e50 = h1_snap.get("ema50")
    if h1_e21 is not None and h1_e50 is not None and h1_e50 != 0:
        sign = 1.0 if h1_e21 > h1_e50 else -1.0
        votes.append(("ema_trend", sign, _w("ema_trend")))
        detail["h1"] = "LONG" if sign > 0 else "SHORT"

    if not votes:
        return 0.0, None, {"error": "no_ema_data", **detail}

    long_w = sum(w for _, d, w in votes if d > 0)
    short_w = sum(w for _, d, w in votes if d < 0)
    total_w = long_w + short_w
    if total_w <= 0:
        return 0.0, None, {"error": "zero_weight", **detail}

    if abs(long_w - short_w) < 1e-9:
        # Perfectly tied — use D1 as tiebreaker; refuse to guess if D1 is absent.
        d1_vote = next((d for name, d, _ in votes if name == "d1_ema_trend"), None)
        if d1_vote is None:
            detail["tie_no_d1"] = True
            return 0.0, None, {"error": "tied_no_d1_tiebreaker", **detail}
        dominant_sign = d1_vote
    else:
        dominant_sign = 1.0 if long_w > short_w else -1.0

    dominant_w = long_w if dominant_sign > 0 else short_w
    coherence_ratio = max(0.5, min(1.0, dominant_w / total_w))
    agreement_count = sum(1 for _, d, _ in votes if d == dominant_sign)
    # Scale by TF coverage so a single available TF cannot produce a full 3.0 score.
    # D1 only → max 1.0; D1+H4 → max 2.0; all three → max 3.0.
    _tf_coverage = len(votes) / 3.0
    magnitude = (0.35 + 0.65 * coherence_ratio) * 3.0 * _tf_coverage
    trend_score = dominant_sign * magnitude
    direction = "LONG" if dominant_sign > 0 else "SHORT"

    detail.update({
        "agreement_count": agreement_count,
        "total_count": len(votes),
        "coherence_ratio": round(coherence_ratio, 4),
        "tf_coverage": round(_tf_coverage, 4),
        "dominant_direction": direction,
        "weighted_balance": round((long_w - short_w) / total_w, 4),
    })
    return trend_score, direction, detail


# ── Factor 2: Momentum quality (RSI + MACD confirmation) ─────────────────────

def _momentum_quality(
    h4_snap: dict, direction: str, asset_type: str
) -> float:
    """RSI + MACD confirmation quality score in [0.0, 1.0].

    Checks whether momentum indicators confirm the trend direction.
    Does NOT change direction — only sizes conviction.

    RSI contribution (0.6 weight):
      - Confirming zone (not extreme): +0.5
      - Extreme overbought/oversold (overextended): -0.25 (late entry)
      - Neutral: 0.0

    MACD contribution (0.4 weight):
      - Histogram aligned with direction: +0.5
      - Histogram opposing direction: 0.0 (neutral — MACD lags, don't penalise hard)

    Final: clamp(weighted_sum, 0.0, 1.0)
    """
    is_long = direction == "LONG"
    rsi_bounds = CONFIG.get("RSI_BOUNDS", {}).get(asset_type, {"ob": 70, "os": 30})
    ob = float(rsi_bounds.get("ob", 70))
    os_ = float(rsi_bounds.get("os", 30))

    # RSI score — graded by 50-midline so only RSI above 50 (LONG) or below 50 (SHORT)
    # counts as confirming.  RSI on the wrong side of 50 is weak/non-confirming.
    # The original os_-to-ob flat +0.50 treated RSI=35 the same as RSI=65 on a LONG.
    rsi_score = 0.0
    rsi = h4_snap.get("rsi")
    if rsi is not None:
        try:
            rsi = float(rsi)
            if is_long:
                if rsi >= ob:
                    rsi_score = -0.25  # overbought — late entry risk
                elif rsi >= 50:
                    rsi_score = 0.50   # above midline — momentum confirming LONG
                elif rsi >= os_:
                    rsi_score = 0.10   # below midline — trend not yet in momentum
                # rsi < os_: oversold on LONG is a reversal zone — Engine B handles structure;
                # leave rsi_score = 0.0 (neutral) here rather than double-counting
            else:
                if rsi <= os_:
                    rsi_score = -0.25  # oversold — late short entry risk
                elif rsi <= 50:
                    rsi_score = 0.50   # below midline — momentum confirming SHORT
                elif rsi <= ob:
                    rsi_score = 0.10   # above midline — weak short confirmation
                # rsi > ob: overbought on SHORT is reversal zone — leave neutral
        except (TypeError, ValueError):
            pass

    # MACD histogram score — aligned confirms, opposing penalises (not neutral).
    macd_score = 0.0
    macd_hist = h4_snap.get("macdHist") or h4_snap.get("macd_hist")
    if macd_hist is None:
        macd_hist = h4_snap.get("macdLine_z")
    if macd_hist is not None:
        try:
            hist = float(macd_hist)
            if is_long and hist > 0:
                macd_score = 0.50
            elif not is_long and hist < 0:
                macd_score = 0.50
            elif (is_long and hist < 0) or (not is_long and hist > 0):
                macd_score = -0.15
        except (TypeError, ValueError):
            pass

    # Per-indicator weights from config
    ind_weights = CONFIG.get("INDICATOR_WEIGHTS", {}).get("momentum", {})
    if isinstance(ind_weights, dict) and any(isinstance(v, dict) for v in ind_weights.values()):
        ind_weights = ind_weights.get(asset_type, ind_weights.get("default", {}))
    rsi_w = float(ind_weights.get("rsi_z", 0.6)) if isinstance(ind_weights, dict) else 0.6
    macd_w = float(ind_weights.get("macdLine_z", 0.4)) if isinstance(ind_weights, dict) else 0.4
    total_w = rsi_w + macd_w
    if total_w <= 0:
        total_w = 1.0

    raw = (rsi_score * rsi_w + macd_score * macd_w) / total_w
    return max(0.0, min(1.0, raw + 0.0))  # floor at 0


def _research_lab_candidate_addon(
    pair: dict,
    direction: str,
    h4_candles: list,
    d1_candles: list,
    asset_type: str,
) -> tuple[float, dict]:
    """Research Lab candidate factor, config-gated and bounded.

    This is intentionally small and diagnostic-rich so it can be reverted by one
    config switch if paper validation does not improve Engine A.
    """
    cfg = CONFIG.get("ENGINE_A_RESEARCH_LAB_FACTORS", {}) or {}
    if not cfg.get("ENABLED", False):
        return 0.0, {"enabled": False}

    display = pair.get("display", pair.get("symbol", ""))
    score_group = pair.get("score_group") or _infer_research_score_group(display, asset_type)
    group_cfg = cfg.get("GROUPS", {}) or {}
    allowed = group_cfg.get(score_group, group_cfg.get(asset_type, []))
    if not allowed:
        return 0.0, {
            "enabled": True,
            "score_group": score_group,
            "applied": False,
            "reason": "no_group_factors",
        }

    candles = d1_candles if len(d1_candles or []) >= 60 else h4_candles
    if not candles or len(candles) < 60:
        return 0.0, {
            "enabled": True,
            "score_group": score_group,
            "applied": False,
            "reason": "insufficient_candles",
        }

    bonus = float(cfg.get("BONUS", _RESEARCH_BONUS_DEFAULT))
    penalty = float(cfg.get("PENALTY", _RESEARCH_PENALTY_DEFAULT))
    max_abs = abs(float(cfg.get("MAX_ABS", 0.20)))
    components: dict[str, dict] = {}
    total = 0.0

    for name in allowed:
        val, detail = _research_factor_value(str(name), direction, candles, bonus, penalty)
        components[str(name)] = detail
        total += val

    total = max(-max_abs, min(max_abs, total))
    return total, {
        "enabled": True,
        "score_group": score_group,
        "allowed": list(allowed),
        "components": components,
        "raw_total": round(sum(d.get("value", 0.0) for d in components.values()), 4),
        "value": round(total, 4),
    }


def _infer_research_score_group(display: str, asset_type: str) -> str:
    if asset_type == "forex":
        majors = {"EUR/USD", "GBP/USD", "USD/JPY", "AUD/USD", "NZD/USD", "USD/CAD", "USD/CHF"}
        exotics = {"USD/ZAR", "USD/MXN", "USD/SGD", "USD/BRL", "USD/INR", "USD/TRY"}
        if display in majors:
            return "forex_majors"
        if display in exotics:
            return "forex_exotics"
        return "forex_crosses"
    if asset_type == "crypto":
        if display in {"BTC/USDT", "ETH/USDT"}:
            return "crypto_majors"
        if display in {"DOGE/USDT", "PEPE/USDT", "WIF/USDT"}:
            return "crypto_meme"
        return "crypto_alts"
    if asset_type == "commodity":
        if any(token in display for token in ("XAU", "XAG", "XPT", "XPD")):
            return "metals"
        return "commodity_other"
    return asset_type


def _research_factor_value(name: str, direction: str, candles: list, bonus: float, penalty: float) -> tuple[float, dict]:
    try:
        if name == "obv_divergence":
            return _research_obv_value(direction, candles, bonus, penalty)
        if name == "stochastic_cross":
            return _research_stochastic_value(direction, candles, bonus, penalty)
        if name == "chandelier_trend":
            return _research_chandelier_value(direction, candles, bonus, penalty)
        if name == "bollinger_touch":
            return _research_bollinger_value(direction, candles, bonus, penalty)
        if name == "aroon_trend":
            return _research_aroon_value(direction, candles, bonus, penalty)
    except Exception as e:
        return 0.0, {"signal": "error", "value": 0.0, "error": str(e)}
    return 0.0, {"signal": "unsupported", "value": 0.0}


def _research_obv_value(direction: str, candles: list, bonus: float, penalty: float) -> tuple[float, dict]:
    from indicators import calc_obv_trend

    signal = calc_obv_trend(candles, lookback=20)
    if signal == "confirming":
        return bonus, {"signal": signal, "value": bonus}
    if direction == "LONG" and signal == "diverging_bullish":
        return bonus, {"signal": signal, "value": bonus}
    if direction == "SHORT" and signal == "diverging_bearish":
        return bonus, {"signal": signal, "value": bonus}
    if signal in {"diverging_bullish", "diverging_bearish"}:
        return penalty, {"signal": signal, "value": penalty}
    return 0.0, {"signal": signal, "value": 0.0}


def _research_stochastic_value(direction: str, candles: list, bonus: float, penalty: float) -> tuple[float, dict]:
    from indicators import calc_stochastic

    stoch = calc_stochastic(candles, 14, 3, 3)
    k = stoch.get("k", [])
    d = stoch.get("d", [])
    if len(k) < 2 or len(d) < 2 or k[-1] is None or d[-1] is None or k[-2] is None or d[-2] is None:
        return 0.0, {"signal": "missing", "value": 0.0}
    crossed_up = k[-1] > d[-1] and k[-2] <= d[-2]
    crossed_down = k[-1] < d[-1] and k[-2] >= d[-2]
    if direction == "LONG" and crossed_up:
        return bonus, {"signal": "bull_cross", "k": round(k[-1], 2), "d": round(d[-1], 2), "value": bonus}
    if direction == "SHORT" and crossed_down:
        return bonus, {"signal": "bear_cross", "k": round(k[-1], 2), "d": round(d[-1], 2), "value": bonus}
    if (direction == "LONG" and crossed_down) or (direction == "SHORT" and crossed_up):
        return penalty, {"signal": "opposing_cross", "k": round(k[-1], 2), "d": round(d[-1], 2), "value": penalty}
    return 0.0, {"signal": "no_cross", "k": round(k[-1], 2), "d": round(d[-1], 2), "value": 0.0}


def _research_chandelier_value(direction: str, candles: list, bonus: float, penalty: float) -> tuple[float, dict]:
    from indicators import chandelier_exit

    highs = [float(c["high"]) for c in candles if c.get("high") is not None]
    lows = [float(c["low"]) for c in candles if c.get("low") is not None]
    closes = [float(c["close"]) for c in candles if c.get("close") is not None]
    if len(closes) < 30 or not (len(highs) == len(lows) == len(closes)):
        return 0.0, {"signal": "missing", "value": 0.0}
    ce = chandelier_exit(highs, lows, closes, atr_period=14, lookback=22, mult=3.0)
    ce_dir = (ce.get("direction") or [None])[-1]
    if ce_dir == 1 and direction == "LONG":
        return bonus, {"signal": "bull_trail", "value": bonus}
    if ce_dir == -1 and direction == "SHORT":
        return bonus, {"signal": "bear_trail", "value": bonus}
    if ce_dir in (1, -1):
        return penalty, {"signal": "opposing_trail", "value": penalty}
    return 0.0, {"signal": "missing", "value": 0.0}


def _research_bollinger_value(direction: str, candles: list, bonus: float, penalty: float) -> tuple[float, dict]:
    closes = [float(c["close"]) for c in candles if c.get("close") is not None]
    if len(closes) < 25:
        return 0.0, {"signal": "missing", "value": 0.0}
    window = closes[-20:]
    mean = sum(window) / len(window)
    variance = sum((x - mean) ** 2 for x in window) / len(window)
    std = math.sqrt(max(0.0, variance))
    upper = mean + 2.0 * std
    lower = mean - 2.0 * std
    close = closes[-1]
    if direction == "LONG" and close <= lower:
        return bonus, {"signal": "lower_band_touch", "value": bonus}
    if direction == "SHORT" and close >= upper:
        return bonus, {"signal": "upper_band_touch", "value": bonus}
    if direction == "LONG" and close >= upper:
        return penalty, {"signal": "long_chasing_upper_band", "value": penalty}
    if direction == "SHORT" and close <= lower:
        return penalty, {"signal": "short_chasing_lower_band", "value": penalty}
    return 0.0, {"signal": "inside_bands", "value": 0.0}


def _research_aroon_value(direction: str, candles: list, bonus: float, penalty: float) -> tuple[float, dict]:
    from indicators import calc_aroon

    aroon = calc_aroon(candles, period=14)
    up = aroon.get("aroonUp")
    down = aroon.get("aroonDown")
    osc = aroon.get("aroonOsc")
    if up is None or down is None or osc is None:
        return 0.0, {"signal": "missing", "value": 0.0}
    if direction == "LONG" and up > down and osc > 0:
        return bonus, {"signal": "bull_trend", "aroonUp": round(up, 2), "aroonDown": round(down, 2), "value": bonus}
    if direction == "SHORT" and down > up and osc < 0:
        return bonus, {"signal": "bear_trend", "aroonUp": round(up, 2), "aroonDown": round(down, 2), "value": bonus}
    if (direction == "LONG" and osc < 0) or (direction == "SHORT" and osc > 0):
        return penalty, {"signal": "opposing_trend", "aroonUp": round(up, 2), "aroonDown": round(down, 2), "value": penalty}
    return 0.0, {"signal": "neutral", "aroonUp": round(up, 2), "aroonDown": round(down, 2), "value": 0.0}


# ── Factor 3: ADX gate ────────────────────────────────────────────────────────

def _adx_gate(d1_snap: dict, h4_snap: dict, asset_type: str) -> tuple:
    """ADX trend-strength gate.

    Returns (multiplier, adx_value, adx_source).
    multiplier = 0.0  → hard abort (ADX < 15, dead market)
    multiplier = 0.65 → soft penalty (15 ≤ ADX < adx_min)
    multiplier = 1.0  → full credit (ADX ≥ adx_min)

    Source preference: D1 ADX (structural) first, H4 ADX fallback.
    Per-class ADX minimum from ADX_TREND_MIN_CLASS config.
    """
    adx_min = float(
        (CONFIG.get("ADX_TREND_MIN_CLASS") or {}).get(asset_type, 25)
    )
    # Per-class hard-fail so crypto (class_min=15) gets a soft zone (10-15)
    # instead of the 15/15 cliff where hard_fail == class_min.
    _hf_class = (CONFIG.get("FACTOR_ADX_HARD_FAIL_CLASS") or {})
    if isinstance(_hf_class, dict) and asset_type in _hf_class:
        hard_fail = float(_hf_class[asset_type])
    else:
        hard_fail = float(CONFIG.get("FACTOR_ADX_HARD_FAIL", _ADX_HARD_FAIL_DEFAULT))

    # Use the stronger of D1 and H4 ADX so a rising intraday trend isn't
    # masked by a lagging D1 value.  D1 is still the primary label for source.
    _soft = float(CONFIG.get("FACTOR_ADX_SOFT_MULT", _ADX_SOFT_MULT_DEFAULT))

    d1_adx = d1_snap.get("adx")
    h4_adx = h4_snap.get("adx")
    adx = None
    source = "missing"
    try:
        _d1 = float(d1_adx) if d1_adx is not None else None
    except (TypeError, ValueError):
        _d1 = None
    try:
        _h4 = float(h4_adx) if h4_adx is not None else None
    except (TypeError, ValueError):
        _h4 = None

    if _d1 is not None and _h4 is not None:
        adx = max(_d1, _h4)
        source = "d1" if _d1 >= _h4 else "h4"
    elif _d1 is not None:
        adx, source = _d1, "d1"
    elif _h4 is not None:
        adx, source = _h4, "h4"

    if adx is None:
        # Both D1 and H4 ADX unavailable — likely a feed issue.
        # ADX_MISSING_BOTH_ABORT (default False): when True, treat as hard abort
        # instead of soft-pass, since no ADX data means no trend confirmation.
        if CONFIG.get("ADX_MISSING_BOTH_ABORT", False):
            return 0.0, None, "missing_both_abort"
        return _soft, None, "missing"

    if adx < hard_fail:
        return 0.0, adx, source
    if adx < adx_min:
        return _soft, adx, source
    return 1.0, adx, source


# ── Addon: asset-specific secondary factor ────────────────────────────────────

def _carry_addon(pair_display: str, direction: str, bar_time: Optional[str]) -> float:
    """Carry z-score addon for forex pairs. Returns _ADDON_* constant."""
    try:
        from carry_feed import get_carry_z as _get_carry_z
        from carry_feed import _PAIR_CARRY_FORMULA as _CARRY_FORMULA
        if pair_display not in _CARRY_FORMULA:
            return _ADDON_NEUTRAL
        _as_of = bar_time[:10] if bar_time else None
        carry = _get_carry_z(pair_display, as_of_date=_as_of)
        if carry is None or carry == 0.0:
            return _ADDON_NEUTRAL
        carry = float(carry)
        is_long = direction == "LONG"
        if (is_long and carry > 0.5) or (not is_long and carry < -0.5):
            return _ADDON_CONFIRM
        if (is_long and carry < -0.5) or (not is_long and carry > 0.5):
            return _ADDON_AGAINST
        return _ADDON_NEUTRAL
    except Exception:
        return _ADDON_NEUTRAL


def _cot_addon(pair_display: str, asset_type: str, direction: str, bar_time: Optional[str]) -> float:
    """COT net speculator positioning addon for commodity/forex. Returns _ADDON_* constant."""
    try:
        from cot_feed import get_cot_z as _get_cot_z
        from cot_feed import _PAIR_FORMULA as _COT_FORMULA
        if pair_display not in _COT_FORMULA:
            return _ADDON_NEUTRAL
        _as_of = bar_time[:10] if bar_time else None
        cot = _get_cot_z(pair_display, as_of_date=_as_of)
        if cot is None or cot == 0.0:
            return _ADDON_NEUTRAL
        cot = float(cot)
        # Fade the herd at extremes — linear taper avoids the sharp discontinuity
        # that previously flipped the sign at exactly |z|=2.0.
        # z < 1.5  → confirm direction
        # 1.5..2.5 → linear blend from confirm toward fade
        # z > 2.5  → full fade (opposing direction, 1.5× magnitude)
        if asset_type in ("forex", "commodity") and abs(cot) > 1.5:
            _fade_lo, _fade_hi = 1.5, 2.5
            _abs_cot = abs(cot)
            if _abs_cot >= _fade_hi:
                cot = -cot * 1.5
            else:
                _blend = (_abs_cot - _fade_lo) / (_fade_hi - _fade_lo)
                _confirm = cot
                _fade = -cot * 1.5
                cot = _confirm * (1.0 - _blend) + _fade * _blend
        if abs(cot) < 1.0:
            return _ADDON_NEUTRAL  # insignificant signal
        is_long = direction == "LONG"
        if (is_long and cot > 0) or (not is_long and cot < 0):
            return _ADDON_CONFIRM
        if (is_long and cot < 0) or (not is_long and cot > 0):
            return _ADDON_AGAINST
        return _ADDON_NEUTRAL
    except Exception:
        return _ADDON_NEUTRAL


def _funding_addon(funding_rate: Optional[float], direction: str,
                   funding_stats: Optional[dict] = None) -> float:
    """Funding rate addon for crypto. Returns _ADDON_* constant.

    When *funding_stats* is provided (keys: mean, std) and
    ``FACTOR_FUNDING_USE_ZSCORE`` is True, classify using a rolling z-score
    instead of a fixed noise band so the threshold auto-adapts per pair.
    """
    if funding_rate is None:
        return _ADDON_NEUTRAL
    try:
        fr = float(funding_rate)
        # Z-score mode: (fr - mean) / std.  |z| < 1.0 → neutral.
        if (CONFIG.get("FACTOR_FUNDING_USE_ZSCORE", False)
                and funding_stats
                and isinstance(funding_stats, dict)):
            _mean = float(funding_stats.get("mean", 0))
            _std = float(funding_stats.get("std", 0))
            _z_thresh = float(CONFIG.get("FACTOR_FUNDING_Z_THRESHOLD", 1.0))
            if _std > 0:
                z = (fr - _mean) / _std
                if abs(z) < _z_thresh:
                    return _ADDON_NEUTRAL
                is_long = direction == "LONG"
                # z < -threshold → funding cheaper for longs → bullish
                funding_bullish = z < -_z_thresh
                if (is_long and funding_bullish) or (not is_long and not funding_bullish):
                    return _ADDON_CONFIRM
                return _ADDON_AGAINST

        # Absolute threshold mode (default)
        _baseline = float(CONFIG.get("FACTOR_FUNDING_BASELINE", 0.0001))
        _noise_band = float(CONFIG.get("FACTOR_FUNDING_NOISE_BAND", 0.0001))
        adjusted = fr - _baseline
        if abs(adjusted) < _noise_band:
            return _ADDON_NEUTRAL
        is_long = direction == "LONG"
        funding_bullish = adjusted < 0
        if (is_long and funding_bullish) or (not is_long and not funding_bullish):
            return _ADDON_CONFIRM
        if (is_long and not funding_bullish) or (not is_long and funding_bullish):
            return _ADDON_AGAINST
        return _ADDON_NEUTRAL
    except (TypeError, ValueError):
        return _ADDON_NEUTRAL


def _oi_addon(oi_context: dict, direction: str) -> float:
    """Open-interest divergence addon for crypto. Returns _ADDON_* constant.

    OI rising + price rising  → momentum confirmed (bullish)
    OI rising + price falling → shorts adding (bearish confirmed)
    OI falling + price rising → short covering only, not smart money (less conviction)
    OI falling + price falling → longs capitulating (bearish confirmed)
    """
    try:
        oi_chg = float(oi_context["oi_change_pct"])
        px_chg = float(oi_context["price_change_pct"])
        if abs(oi_chg) < 0.5:
            return _ADDON_NEUTRAL  # OI change too small to be meaningful
        is_long = direction == "LONG"
        if is_long and oi_chg > 0 and px_chg > 0:
            return _ADDON_CONFIRM    # smart money adding longs
        if is_long and oi_chg < 0 and px_chg < 0:
            return _ADDON_AGAINST    # longs capitulating
        if not is_long and oi_chg > 0 and px_chg < 0:
            return _ADDON_CONFIRM    # shorts adding into falling price
        if not is_long and oi_chg < 0 and px_chg > 0:
            return _ADDON_AGAINST    # short covering only — not confirmed trend
        return _ADDON_NEUTRAL
    except (TypeError, ValueError, KeyError):
        return _ADDON_NEUTRAL


def _asset_addon(
    pair: dict,
    direction: str,
    funding_rate: Optional[float],
    bar_time: Optional[str],
    oi_context: Optional[dict] = None,
) -> tuple:
    """Resolve the single asset-class-specific addon factor.

    Returns (addon_value, addon_type, feed_status).
    """
    asset_type = pair.get("type", "stock")
    display = pair.get("display", "")

    if asset_type == "forex":
        val = _carry_addon(display, direction, bar_time)
        return val, "carry", "ok" if val != _ADDON_NEUTRAL else "neutral"

    if asset_type == "crypto":
        funding_val = _funding_addon(funding_rate, direction)
        if oi_context is not None:
            oi_val = _oi_addon(oi_context, direction)
            # Funding is more real-time; OI is supporting signal (lower weight).
            val = round(funding_val * 0.6 + oi_val * 0.4, 6)
            val = max(_ADDON_AGAINST, min(_ADDON_CONFIRM, val))
            status = "ok" if funding_rate is not None else "oi_only"
        else:
            val = funding_val
            status = "ok" if funding_rate is not None else "missing"
        return val, "funding+oi", status

    if asset_type == "commodity":
        val = _cot_addon(display, asset_type, direction, bar_time)
        return val, "cot", "ok" if val != _ADDON_NEUTRAL else "neutral"

    # stock / index — no addon
    return _ADDON_NEUTRAL, "none", "unsupported"


# ── Session multiplier (forex only) ──────────────────────────────────────────

def _session_multiplier(bar_time: Optional[str], asset_type: str) -> float:
    """Return session liquidity multiplier. Only applied to forex pairs."""
    if asset_type != "forex":
        return 1.0
    from datetime import datetime, timezone
    _core_mult = float(CONFIG.get("FACTOR_SESSION_CORE_MULT", _SESSION_CORE_MULT_DEFAULT))
    _shoulder_mult_default = float(CONFIG.get("FACTOR_SESSION_SHOULDER_MULT", _SESSION_SHOULDER_MULT_DEFAULT))
    _off_mult_default = float(CONFIG.get("FACTOR_SESSION_OFF_MULT", _SESSION_OFF_MULT_DEFAULT))
    try:
        if bar_time:
            dt = datetime.fromisoformat(bar_time.replace("Z", "+00:00"))
            h = dt.hour
        else:
            h = datetime.now(timezone.utc).hour
    except Exception:
        return _core_mult

    cfg = CONFIG.get("FOREX_ENGINE", {}) or {}
    soft_mult = float(cfg.get("session_soft_multiplier", _off_mult_default))
    shoulder_mult = float(cfg.get("session_shoulder_multiplier", _shoulder_mult_default))
    shoulder_h = int(cfg.get("session_shoulder_hours", 1))

    # Core sessions: London 07-16 UTC, NY 13-21 UTC
    if (7 <= h < 16) or (13 <= h < 21):
        return _core_mult
    # Shoulder zones: ±shoulder_h of core
    # Hour 16 is already inside the NY core window (13 <= h < 21) so including
    # range(16, 16+shoulder_h) here is dead code — core check fires first.
    shoulder_zones = list(range(7 - shoulder_h, 7)) + list(range(21, 21 + shoulder_h))
    if h in shoulder_zones:
        return shoulder_mult
    return soft_mult


# ── Main scoring function ─────────────────────────────────────────────────────

def compute_factor_scores(
    d1_snap: Dict,
    h4_snap: Dict,
    h1_snap: Dict,
    pair: Dict,
    d1_candles: List,
    h4_candles: List,
    h1_candles: List,
    volume_ratio: float,
    funding_rate: Optional[float] = None,
    bar_time: Optional[str] = None,
    regime_context: Optional[dict] = None,
    oi_context: Optional[dict] = None,
    macro_context: Optional[dict] = None,
    intermarket_context: Optional[dict] = None,
) -> Dict:
    """Compute Engine A v2 factor scores and aggregate to final conviction score.

    Returns dict with final_score (0-3.0), direction, factor_scores, regime, and diagnostics.
    Backward-compatible keys preserved for Engine C / scoring.py / Marcus Reid AI.
    """
    asset_type = pair.get("type", "stock")
    display = pair.get("display", pair.get("symbol", "?"))
    pair_id = display
    feed_status: Dict[str, str] = {}

    # ── Data quality guard ────────────────────────────────────────────────────
    _close = h4_snap.get("close")
    _atr = h4_snap.get("atr")
    if _close and _atr == 0:
        log.warning("[EA2] %s ATR=0 — frozen candle data suspected", display)

    # ── FACTOR 1: Trend ───────────────────────────────────────────────────────
    trend_score, direction, trend_detail = _coherent_trend_score(
        d1_snap, h4_snap, h1_snap, asset_type
    )

    # Hard abort: no direction determinable
    if direction is None or abs(trend_score) < 1e-9:
        log.debug("[EA2] %s trend indeterminate — score=0", display)
        regime_raw = detect_regime(h4_snap, asset_type).get("regime", "UNKNOWN")
        regime = _get_smoothed_regime(regime_context, pair_id, regime_raw)
        return _zero_result(pair, regime, trend_detail, feed_status, reason="indeterminate_trend")

    # ── FACTOR 3: ADX gate ────────────────────────────────────────────────────
    adx_mult, adx_val, adx_source = _adx_gate(d1_snap, h4_snap, asset_type)
    feed_status["adx"] = adx_source

    # Hard abort: dead market
    if adx_mult == 0.0:
        log.debug("[EA2] %s ADX=%.1f hard abort — dead market", display, adx_val or 0)
        regime_raw = detect_regime(h4_snap, asset_type).get("regime", "UNKNOWN")
        regime = _get_smoothed_regime(regime_context, pair_id, regime_raw)
        return _zero_result(pair, regime, trend_detail, feed_status, reason="adx_hard_abort",
                            adx_val=adx_val, direction=direction)

    # ── FACTOR 2: Momentum quality ────────────────────────────────────────────
    mom_quality = _momentum_quality(h4_snap, direction, asset_type)

    # ── ADDON: Asset-specific secondary factor ────────────────────────────────
    addon_val, addon_type, addon_status = _asset_addon(
        pair, direction, funding_rate, bar_time, oi_context=oi_context
    )
    feed_status["addon"] = f"{addon_type}:{addon_status}"

    research_val, research_detail = _research_lab_candidate_addon(
        pair, direction, h4_candles, d1_candles, asset_type
    )
    if research_detail.get("enabled"):
        addon_val += research_val
        feed_status["research_lab"] = f"{research_val:.2f}"

    # ── Session multiplier (forex only) ───────────────────────────────────────
    session_mult = _session_multiplier(bar_time, asset_type)
    feed_status["session"] = f"{session_mult:.2f}"

    # ── Conviction score: weighted combination ────────────────────────────────
    # Read weights lazily so config reloads take effect without restart.
    _momentum_w = float(CONFIG.get("FACTOR_MOMENTUM_WEIGHT", _MOMENTUM_WEIGHT_DEFAULT))
    _addon_w = float(CONFIG.get("FACTOR_ADDON_WEIGHT", _ADDON_WEIGHT_DEFAULT))
    _base_w = float(CONFIG.get("FACTOR_BASE_WEIGHT", _BASE_WEIGHT_DEFAULT))
    _conviction_floor = float(CONFIG.get("FACTOR_CONVICTION_FLOOR", _CONVICTION_FLOOR_DEFAULT))
    # conviction ∈ [0, 1] — how strongly we believe in this setup
    # Base floor + momentum quality + addon (addon_val can be negative → reduces conviction).
    # Normalise addon: +0.30 → +1.0, 0.00 → 0.0, -0.15 → -0.5 (penalty preserved, not floored).
    addon_norm = (addon_val / _ADDON_CONFIRM) if _ADDON_CONFIRM > 0 else 0.0

    # When addon is unsupported (stock/index), redistribute addon weight.
    # ADDON_UNSUPPORTED_SPLIT (default 0.5): fraction of addon weight that goes
    # to base (raises floor) vs momentum (amplifies single-factor).  At 0.5 the
    # split is 50/50 so momentum carries 0.65 instead of 0.80.
    _eff_mom_w = _momentum_w
    _eff_addon_w = _addon_w
    _eff_base_w = _base_w
    if addon_status == "unsupported":
        _split_to_base = float(CONFIG.get("ADDON_UNSUPPORTED_SPLIT_TO_BASE", 0.0))
        _split_to_base = max(0.0, min(1.0, _split_to_base))
        _eff_base_w = _base_w + _addon_w * _split_to_base
        _eff_mom_w = _momentum_w + _addon_w * (1.0 - _split_to_base)
        _eff_addon_w = 0.0

    conviction = (
        _eff_base_w
        + _eff_mom_w * mom_quality
        + _eff_addon_w * addon_norm
    )
    conviction = max(0.0, min(1.0, conviction))

    # ── Regime detection (informational — not used for weight modification) ───
    _bbw_pct = h4_snap.get("bbWidth_pct")
    if _bbw_pct is None:
        _bbw_pct = h4_snap.get("bb_width_pct")
    regime_raw = detect_regime(h4_snap, asset_type, bb_width_pct=_bbw_pct).get("regime", "UNKNOWN")
    regime = _get_smoothed_regime(regime_context, pair_id, regime_raw)

    # ── Final score ───────────────────────────────────────────────────────────
    # base_score: driven by trend coherence (0-3.0 scale from _coherent_trend_score)
    # applied: adx_mult * session_mult * conviction blend
    # Formula: abs(trend_score) * adx_mult * session_mult * (floor + (1-floor)*conviction)
    #
    # The conviction floor is regime-conditional when CONVICTION_FLOOR_BY_REGIME is
    # present. RANGING / HIGH_VOLATILITY use a lower floor (default 0.40) because
    # momentum noise is higher — weak momentum should count for less.
    _floor_by_regime = CONFIG.get("CONVICTION_FLOOR_BY_REGIME") or {}
    if isinstance(_floor_by_regime, dict) and regime in _floor_by_regime:
        _eff_floor = float(_floor_by_regime[regime])
    else:
        _eff_floor = _conviction_floor
    base_score = abs(trend_score) * adx_mult * session_mult
    final_score = base_score * (_eff_floor + (1.0 - _eff_floor) * conviction)
    final_score = max(0.0, min(3.0, final_score))

    # ── Factor scores dict (for UI / Marcus Reid diagnostics) ─────────────────
    factor_scores = {
        "trend": round(trend_score, 4),
        "momentum": round(mom_quality, 4),
        "addon": round(addon_val, 4),
        "research_lab": round(research_val, 4),
    }

    log.debug(
        "[EA2] %s dir=%s score=%.3f trend=%.3f adx=%.1f(%s) mom=%.3f addon=%.3f(%s) sess=%.2f regime=%s",
        display, direction, final_score, trend_score, adx_val or 0, adx_source,
        mom_quality, addon_val, addon_type, session_mult, regime,
    )

    return {
        # ── Core outputs ──────────────────────────────────────────────────────
        "final_score": round(final_score, 4),
        "direction": direction,
        "regime": regime,
        # ── Factor breakdown (UI + AI) ────────────────────────────────────────
        "factor_scores": factor_scores,
        "weights": {"trend": 1.0, "momentum": _momentum_w, "addon": _addon_w},
        "trend_coherence": trend_detail,
        # ── Diagnostic fields (backward compat with Engine C / scoring.py) ───
        "directional_score": round(trend_score, 4),
        "nondirectional_score": round(mom_quality, 4),
        "unweighted_directional_sum": round(trend_score, 4),
        "adx_value": adx_val,
        "adx_source": adx_source,
        "adx_multiplier": adx_mult,
        "momentum_quality": round(mom_quality, 4),
        "addon_type": addon_type,
        "addon_value": round(addon_val, 4),
        "research_lab_value": round(research_val, 4),
        "research_lab_detail": research_detail,
        "addon_unsupported": addon_status == "unsupported",
        "session_multiplier": round(session_mult, 4),
        "conviction": round(conviction, 4),
        # ── Compatibility flags ───────────────────────────────────────────────
        "insufficient_factors": False,
        "indeterminate_direction": False,
        "min_directional_failed": False,
        "active_directional_factors": ["trend"],
        "active_nondirectional_factors": ["momentum", "addon"] + (["research_lab"] if research_detail.get("enabled") else []),
        "disabled_factors": [],
        "directional_confidence_multiplier": round(conviction, 4),
        "effective_min_directional": 0.0,
        "min_directional_threshold": 0.0,
        "optional_factor_coverage": 1.0,
        "missing_directional_optional_count": 0,
        "correlation_adjustments": {},
        "crypto_engine_a_diagnostics": None,
        "intermarket_confirmation": None,
        "intermarket_engine_a_delta": 0.0,
        "feed_status": feed_status,
        "btc_bias_applied": None,
    }


def _zero_result(
    pair: dict,
    regime: str,
    trend_detail: dict,
    feed_status: dict,
    reason: str = "unknown",
    adx_val=None,
    direction=None,
) -> dict:
    """Return a clean zero-score result with diagnostics."""
    return {
        "final_score": 0.0,
        "direction": direction,
        "regime": regime,
        "factor_scores": {"trend": 0.0, "momentum": 0.0, "addon": 0.0, "research_lab": 0.0},
        "weights": {
            "trend": 1.0,
            "momentum": float(CONFIG.get("FACTOR_MOMENTUM_WEIGHT", _MOMENTUM_WEIGHT_DEFAULT)),
            "addon": float(CONFIG.get("FACTOR_ADDON_WEIGHT", _ADDON_WEIGHT_DEFAULT)),
        },
        "trend_coherence": trend_detail,
        "directional_score": 0.0,
        "nondirectional_score": 0.0,
        "unweighted_directional_sum": 0.0,
        "adx_value": adx_val,
        "adx_source": feed_status.get("adx"),
        "adx_multiplier": 0.0,
        "momentum_quality": 0.0,
        "addon_type": "none",
        "addon_value": 0.0,
        "research_lab_value": 0.0,
        "research_lab_detail": {"enabled": bool(CONFIG.get("ENGINE_A_RESEARCH_LAB_FACTORS", {}).get("ENABLED", False))},
        "session_multiplier": 1.0,
        "conviction": 0.0,
        "insufficient_factors": True,
        "indeterminate_direction": reason == "indeterminate_trend",
        "min_directional_failed": False,
        "active_directional_factors": [],
        "active_nondirectional_factors": [],
        "disabled_factors": [],
        "directional_confidence_multiplier": 0.0,
        "effective_min_directional": 0.0,
        "min_directional_threshold": 0.0,
        "optional_factor_coverage": 0.0,
        "missing_directional_optional_count": 0,
        "correlation_adjustments": {},
        "crypto_engine_a_diagnostics": None,
        "intermarket_confirmation": None,
        "intermarket_engine_a_delta": 0.0,
        "feed_status": feed_status,
        "btc_bias_applied": None,
        "abort_reason": reason,
    }

